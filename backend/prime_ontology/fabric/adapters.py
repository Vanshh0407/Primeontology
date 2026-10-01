"""Source adapters. Each adapter is a small, protocol-faithful client:

    adapter = ADAPTERS[kind](config, secrets)
    adapter.test()                      -> {"ok": True, "detail": ...}
    adapter.discover()                  -> [{"name": "...", "fields": [{"name", "type"}]}]
    adapter.fetch(spec, since=None)     -> iterator of raw dict rows (incremental when `since` is given)

Verification status (be honest about it): MySQL is tested against real servers. REST, Odoo (JSON-RPC), Salesforce (REST/SOQL, OAuth)
and SAP (OData v2/v4) are tested against local servers that implement the real protocol shapes — NOT against live SAP/Salesforce/Odoo
systems, which were not available.
"""
import csv
import io
import json
import re
from datetime import datetime, timezone

from django.conf import settings

from ..records import json_value
from .crypto import redact
from .http import ConnectorError, json_request

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$.]*$")
BATCH = 500


def iso(dt) -> str:
    if isinstance(dt, str):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dig(obj, path: str):
    """a.b.c lookup in nested dicts."""
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


class SourceAdapter:
    kind = ""
    label = ""
    description = ""
    config_fields: list = []  # [{"key","label","required","placeholder"}]
    secret_fields: list = []  # [{"key","label"}]
    spec_hint = ""  # which key in a mapping entity names the object (table/model/...)

    def __init__(self, config: dict, secrets: dict):
        self.config, self.secrets = config or {}, secrets or {}

    def _safe(self, msg) -> str:
        return redact(str(msg), self.secrets)

    def test(self) -> dict:
        raise NotImplementedError

    def discover(self) -> list[dict]:
        return []

    def fetch(self, spec: dict, since=None):
        raise NotImplementedError


# ------------------------------------------------------------------------ MySQL
class MySqlAdapter(SourceAdapter):
    kind, label = "mysql", "MySQL database"
    description = "Reads tables directly (read-only queries). Incremental when the mapping names an updated-at column."
    config_fields = [{"key": "host", "label": "Host", "required": True}, {"key": "port", "label": "Port", "placeholder": "3306"},
                     {"key": "database", "label": "Database", "required": True}]
    secret_fields = [{"key": "user", "label": "User"}, {"key": "password", "label": "Password"}]
    spec_hint = "table"

    def _conn(self, streaming=False):
        import pymysql
        import pymysql.cursors

        host = self.config.get("host", "localhost")
        allowed = getattr(settings, "PRIME_ONTOLOGY_ALLOWED_DB_HOSTS", None)
        if allowed and host not in allowed:
            raise ConnectorError("This database host is not in the server's allow-list.")
        try:
            return pymysql.connect(host=host, port=int(self.config.get("port") or 3306), user=self.secrets.get("user"),
                                   password=self.secrets.get("password") or "", database=self.config.get("database"), charset="utf8mb4",
                                   connect_timeout=10, read_timeout=120,
                                   cursorclass=pymysql.cursors.SSDictCursor if streaming else pymysql.cursors.DictCursor)
        except Exception as e:
            raise ConnectorError(self._safe(f"MySQL connection failed: {e}")) from None

    def test(self):
        c = self._conn()
        try:
            with c.cursor() as cur:
                cur.execute("SELECT VERSION() AS v")
                return {"ok": True, "detail": f"Connected to MySQL {cur.fetchone()['v']}"}
        finally:
            c.close()

    def discover(self):
        c = self._conn()
        try:
            with c.cursor() as cur:
                cur.execute("SELECT table_name AS t, column_name AS c, column_type AS ty FROM information_schema.columns "
                            "WHERE table_schema=%s ORDER BY table_name, ordinal_position", (self.config.get("database"),))
                out = {}
                for r in cur.fetchall():
                    out.setdefault(r["t"], []).append({"name": r["c"], "type": r["ty"]})
                return [{"name": t, "fields": f} for t, f in out.items()]
        finally:
            c.close()

    def fetch(self, spec, since=None):
        table, idc = spec.get("table"), spec.get("id")
        upd = spec.get("updated_at_field")
        if not table:
            raise ConnectorError("The mapping needs a 'table' for a MySQL source.")
        c = self._conn(streaming=True)
        try:
            meta = self._conn()
            try:
                with meta.cursor() as cur:
                    cur.execute("SELECT column_name AS c FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
                                (self.config.get("database"), table))
                    cols = {r["c"] for r in cur.fetchall()}
            finally:
                meta.close()
            if not cols:
                raise ConnectorError(f"Table '{table}' does not exist (or is not readable).")
            ids = [idc] if isinstance(idc, str) else list(idc or [])
            want = [x for x in (spec.get("columns") or sorted(cols))]
            for name in want + ids + ([upd] if upd else []):
                if name not in cols:
                    raise ConnectorError(f"Column '{name}' does not exist in table '{table}'.")
            q = lambda s: "`" + s.replace("`", "``") + "`"  # identifiers are validated against information_schema AND quoted
            sql = f"SELECT {', '.join(q(x) for x in dict.fromkeys(want + ids + ([upd] if upd else [])))} FROM {q(table)}"
            args = []
            if upd and since is not None:
                sql += f" WHERE {q(upd)} > %s"
                args.append(since if not isinstance(since, str) else since.replace("T", " ").replace("Z", ""))
            sql += " ORDER BY " + ", ".join(q(x) for x in (ids or [sorted(cols)[0]]))
            with c.cursor() as cur:
                cur.execute(sql, args)
                while True:
                    rows = cur.fetchmany(1000)
                    if not rows:
                        break
                    for r in rows:
                        yield {k: json_value(v) for k, v in r.items()}
        finally:
            c.close()


# ------------------------------------------------------------------------- REST
class RestAdapter(SourceAdapter):
    kind, label = "rest", "REST / JSON API"
    description = "Any JSON API: list path, pagination (page / offset / cursor) and an optional 'updated since' parameter."
    config_fields = [{"key": "base_url", "label": "Base URL (https)", "required": True}]
    secret_fields = [{"key": "bearer", "label": "Bearer token"}, {"key": "header_name", "label": "Custom header name"},
                     {"key": "header_value", "label": "Custom header value"}]
    spec_hint = "path"

    def _headers(self):
        h = {}
        if self.secrets.get("bearer"):
            h["Authorization"] = "Bearer " + self.secrets["bearer"]
        if self.secrets.get("header_name") and self.secrets.get("header_value"):
            h[self.secrets["header_name"]] = self.secrets["header_value"]
        return h

    def _get(self, path, params=None):
        url = self.config["base_url"].rstrip("/") + "/" + path.lstrip("/") if not path.startswith("http") else path
        return json_request("GET", url, headers=self._headers(), params=params, secrets=self.secrets)

    def test(self):
        self._get(self.config.get("test_path") or "/")
        return {"ok": True, "detail": "The API answered."}

    def fetch(self, spec, since=None):
        path = spec.get("path")
        if not path:
            raise ConnectorError("The mapping needs a 'path' for a REST source.")
        params = dict(spec.get("params") or {})
        if since is not None and spec.get("since_param"):
            params[spec["since_param"]] = iso(since)
        pg = spec.get("pagination") or {}
        ptype, size = pg.get("type", "none"), int(pg.get("size", 100))
        page, offset, nxt, pages = int(pg.get("start", 1)), 0, None, 0
        while pages < int(spec.get("max_pages", 1000)):
            p = dict(params)
            if ptype == "page":
                p[pg.get("param", "page")], p[pg.get("size_param", "per_page")] = page, size
            elif ptype == "offset":
                p[pg.get("param", "offset")], p[pg.get("size_param", "limit")] = offset, size
            data = self._get(nxt or path, None if nxt else p)
            items = dig(data, spec["list_path"]) if spec.get("list_path") else data
            if isinstance(items, dict):
                items = [items]
            if not isinstance(items, list):
                raise ConnectorError("The response does not contain a list at 'list_path'.")
            for it in items:
                if isinstance(it, dict):
                    yield it
            pages += 1
            if ptype == "cursor":
                nxt = dig(data, pg.get("next_path", "next"))
                if not nxt:
                    break
            elif ptype in ("page", "offset"):
                if len(items) < size:
                    break
                page, offset = page + 1, offset + size
            else:
                break


# -------------------------------------------------------------------------- Odoo
class OdooAdapter(SourceAdapter):
    kind, label = "odoo", "Odoo (JSON-RPC)"
    description = "Odoo external API: partners, invoices, orders... via /jsonrpc (search_read), incremental on write_date."
    config_fields = [{"key": "url", "label": "Odoo URL", "required": True}, {"key": "db", "label": "Database", "required": True}]
    secret_fields = [{"key": "user", "label": "Login"}, {"key": "password", "label": "Password / API key"}]
    spec_hint = "model"

    def _rpc(self, service, method, args):
        r = json_request("POST", self.config["url"].rstrip("/") + "/jsonrpc", secrets=self.secrets,
                         body={"jsonrpc": "2.0", "method": "call", "params": {"service": service, "method": method, "args": args}, "id": 1})
        if isinstance(r, dict) and r.get("error"):
            err = r["error"]
            raise ConnectorError(self._safe("Odoo error: " + str((err.get("data") or {}).get("message") or err.get("message") or err)[:300]))
        return r.get("result") if isinstance(r, dict) else None

    def _uid(self):
        if not hasattr(self, "_cached_uid"):
            uid = self._rpc("common", "login", [self.config["db"], self.secrets.get("user"), self.secrets.get("password")])
            if not uid:
                raise ConnectorError("Odoo rejected the login.")
            self._cached_uid = uid
        return self._cached_uid

    def _exec(self, model, method, args, kwargs=None):
        return self._rpc("object", "execute_kw", [self.config["db"], self._uid(), self.secrets.get("password"), model, method, args, kwargs or {}])

    def test(self):
        v = self._rpc("common", "version", [])
        self._uid()
        return {"ok": True, "detail": f"Authenticated to Odoo {(v or {}).get('server_version', '')}".strip()}

    def discover(self):
        models = self._exec("ir.model", "search_read", [[["transient", "=", False]]], {"fields": ["model", "name"], "limit": 200, "order": "model"})
        return [{"name": m["model"], "fields": []} for m in (models or [])]

    def fetch(self, spec, since=None):
        model = spec.get("model")
        if not model:
            raise ConnectorError("The mapping needs a 'model' for an Odoo source.")
        domain = list(spec.get("domain") or [])
        upd = spec.get("updated_at_field", "write_date")
        if since is not None:
            domain.append([upd, ">", iso(since).replace("T", " ").replace("Z", "")])
        # Odoo reports an EMPTY field as False (not null). Convert that for every non-boolean field, or "no phone" becomes data.
        try:
            types = {k: v.get("type") for k, v in (self._exec(model, "fields_get", [], {"attributes": ["type"]}) or {}).items()}
        except ConnectorError:
            types = {}
        offset = 0
        while True:
            rows = self._exec(model, "search_read", [domain], {"fields": spec.get("columns") or [], "limit": BATCH, "offset": offset, "order": "id"})
            if not rows:
                break
            for r in rows:
                yield {k: (None if v is False and types.get(k) != "boolean" else v) for k, v in r.items()}
            if len(rows) < BATCH:
                break
            offset += BATCH


# ---------------------------------------------------------------------- Salesforce
class SalesforceAdapter(SourceAdapter):
    kind, label = "salesforce", "Salesforce (REST / SOQL)"
    description = "Accounts, Contacts, Opportunities... via SOQL, incremental on SystemModstamp. OAuth token or username-password flow."
    config_fields = [{"key": "instance_url", "label": "Instance URL (https://…my.salesforce.com)", "required": True},
                     {"key": "api_version", "label": "API version", "placeholder": "v59.0"}, {"key": "login_url", "label": "Login URL (OAuth)"}]
    secret_fields = [{"key": "access_token", "label": "Access token"}, {"key": "client_id", "label": "Client id"},
                     {"key": "client_secret", "label": "Client secret"}, {"key": "username", "label": "Username"},
                     {"key": "password", "label": "Password"}, {"key": "security_token", "label": "Security token"}]
    spec_hint = "sobject"

    def _token(self):
        if self.secrets.get("access_token"):
            return self.secrets["access_token"]
        if not hasattr(self, "_oauth"):
            s = self.secrets
            r = json_request("POST", (self.config.get("login_url") or self.config["instance_url"]).rstrip("/") + "/services/oauth2/token",
                             form={"grant_type": "password", "client_id": s.get("client_id", ""), "client_secret": s.get("client_secret", ""),
                                   "username": s.get("username", ""), "password": (s.get("password", "") + s.get("security_token", ""))},
                             secrets=s)
            if not isinstance(r, dict) or not r.get("access_token"):
                raise ConnectorError("Salesforce did not return an access token.")
            self._oauth = r["access_token"]
            if r.get("instance_url"):
                self.config = {**self.config, "instance_url": r["instance_url"]}
        return self._oauth

    def _get(self, path, params=None):
        url = path if path.startswith("http") else self.config["instance_url"].rstrip("/") + path
        return json_request("GET", url, headers={"Authorization": "Bearer " + self._token()}, params=params, secrets=self.secrets)

    def _base(self):
        return f"/services/data/{self.config.get('api_version') or 'v59.0'}"

    def test(self):
        v = self._get(self._base() + "/limits")
        return {"ok": True, "detail": "Salesforce answered" + (f" ({len(v)} limit groups)" if isinstance(v, dict) else "")}

    def discover(self):
        d = self._get(self._base() + "/sobjects")
        return [{"name": s["name"], "fields": []} for s in (d or {}).get("sobjects", []) if s.get("queryable")]

    def fetch(self, spec, since=None):
        obj, fields = spec.get("sobject"), spec.get("columns") or ["Id"]
        upd = spec.get("updated_at_field", "SystemModstamp")
        for name in [obj, upd, *fields]:
            if not name or not IDENT.match(name):
                raise ConnectorError(f"'{name}' is not a valid Salesforce identifier.")
        soql = f"SELECT {', '.join(dict.fromkeys(fields))} FROM {obj}"
        if since is not None:
            soql += f" WHERE {upd} > {iso(since)}"
        soql += f" ORDER BY {upd} ASC"
        data = self._get(self._base() + "/query", {"q": soql})
        while True:
            for r in (data or {}).get("records", []):
                r = {k: v for k, v in r.items() if k != "attributes"}
                yield r
            nxt = (data or {}).get("nextRecordsUrl")
            if not nxt or (data or {}).get("done", True):
                break
            data = self._get(nxt)


# ---------------------------------------------------------------------------- SAP
class SapODataAdapter(SourceAdapter):
    kind, label = "sap_odata", "SAP (OData v2 / v4)"
    description = "SAP S/4HANA, ECC (Gateway) and SuccessFactors-style OData services: business partners, invoices, orders…"
    config_fields = [{"key": "base_url", "label": "Service root URL", "required": True}, {"key": "version", "label": "OData version (v2 | v4)", "placeholder": "v2"}]
    secret_fields = [{"key": "user", "label": "User (basic)"}, {"key": "password", "label": "Password (basic)"}, {"key": "bearer", "label": "Bearer token"}]
    spec_hint = "entity_set"

    def _headers(self):
        if self.secrets.get("bearer"):
            return {"Authorization": "Bearer " + self.secrets["bearer"]}
        if self.secrets.get("user"):
            import base64

            return {"Authorization": "Basic " + base64.b64encode(f"{self.secrets['user']}:{self.secrets.get('password', '')}".encode()).decode()}
        return {}

    def _get(self, url, params=None):
        full = url if url.startswith("http") else self.config["base_url"].rstrip("/") + "/" + url.lstrip("/")
        return json_request("GET", full, headers=self._headers(), params=params, secrets=self.secrets)

    def _v4(self):
        return str(self.config.get("version", "v2")).lower() == "v4"

    def test(self):
        self._get("", {"$format": "json"} if not self._v4() else None)
        return {"ok": True, "detail": "The OData service answered."}

    @staticmethod
    def _sap_date(v):
        m = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d+)?\)/", v) if isinstance(v, str) else None
        return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if m else v

    def fetch(self, spec, since=None):
        es = spec.get("entity_set")
        if not es or not IDENT.match(es):
            raise ConnectorError("The mapping needs a valid 'entity_set' for an SAP source.")
        upd = spec.get("updated_at_field")
        params = {"$top": int(spec.get("page_size", BATCH))}
        if not self._v4():
            params["$format"] = "json"
        if spec.get("columns"):
            params["$select"] = ",".join(spec["columns"])
        if since is not None and upd:
            if not IDENT.match(upd):
                raise ConnectorError("Invalid updated_at_field.")
            ts = iso(since)
            params["$filter"] = f"{upd} gt {ts}" if self._v4() else f"{upd} gt datetime'{ts[:-1]}'"
        data = self._get(es, params)
        guard = 0
        while guard < 10000:
            guard += 1
            if isinstance(data, dict) and "d" in data:  # v2
                d = data["d"]
                rows, nxt = (d.get("results") if isinstance(d, dict) else d) or [], (d.get("__next") if isinstance(d, dict) else None)
            else:  # v4
                rows, nxt = (data or {}).get("value", []), (data or {}).get("@odata.nextLink")
            for r in rows:
                yield {k: self._sap_date(v) for k, v in r.items() if k != "__metadata"}
            if not nxt:
                break
            data = self._get(nxt)


# --------------------------------------------------------------------------- File
class FileAdapter(SourceAdapter):
    kind, label = "file", "Uploaded file (CSV / JSON)"
    description = "A CSV or JSON file stored with the source. Re-upload to sync changes."
    spec_hint = ""

    def test(self):
        n = sum(1 for _ in self.fetch({}))
        return {"ok": True, "detail": f"{n} rows in the uploaded file."}

    def fetch(self, spec, since=None):
        inline = self.config.get("inline") or {}
        text = inline.get("text", "")
        if not text:
            raise ConnectorError("No file has been uploaded to this source yet.")
        if inline.get("format") == "json":
            data = json.loads(text)
            rows = dig(data, spec["list_path"]) if spec.get("list_path") else data
            for r in rows if isinstance(rows, list) else []:
                if isinstance(r, dict):
                    yield r
        else:
            for r in csv.DictReader(io.StringIO(text)):
                yield {k: (v if v != "" else None) for k, v in r.items()}


# ---------------------------------------------------------------------- Data lake
class DataLakeAdapter(SourceAdapter):
    """Files in a data-lake / object-store folder mounted on the server (CSV, JSON, JSON-lines, Parquet).

    Only roots the administrator lists in settings.PRIME_ONTOLOGY_DATALAKE_ROOTS = {"lake": "/mnt/lake"} can be read; the mapping's
    `path` is a glob RELATIVE to that root (no '..', no absolute paths, no symlink escapes). Incremental: with a watermark, files whose
    modification time is not newer are skipped. Cloud object stores (S3/ADLS/GCS) are reached by mounting them (s3fs, blobfuse, gcsfuse)
    - there is no native cloud SDK client here.
    """
    kind, label = "datalake", "Data lake folder (Parquet / CSV / JSON)"
    description = "Files under an administrator-approved data-lake root. Mount cloud buckets to use them."
    spec_hint = "path"
    config_fields = [{"key": "root", "label": "Lake root name (configured by the administrator)", "required": True, "placeholder": "lake"}]
    EXT = (".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".parquet")
    MAX_FILES = 5000

    def _root(self):
        import os

        roots = getattr(settings, "PRIME_ONTOLOGY_DATALAKE_ROOTS", None) or {}
        name = self.config.get("root")
        if name not in roots:
            raise ConnectorError("Data-lake access is disabled or this root is not configured (PRIME_ONTOLOGY_DATALAKE_ROOTS).")
        r = os.path.realpath(roots[name])
        if not os.path.isdir(r):
            raise ConnectorError("The configured data-lake root does not exist on the server.")
        return r

    def _files(self, pattern: str):
        import glob
        import os

        root = self._root()
        if not pattern or os.path.isabs(pattern) or pattern[0] in "/\\" or ":" in pattern or ".." in pattern.replace("\\", "/").split("/"):
            raise ConnectorError("path must be a relative glob such as 'sales/*.parquet' (no '..').")
        out = []
        for f in sorted(glob.glob(os.path.join(root, pattern), recursive=True)):
            real = os.path.realpath(f)
            if os.path.commonpath([root, real]) != root or not os.path.isfile(real) or not real.lower().endswith(self.EXT):
                continue  # symlinks that leave the root, directories and unsupported types are ignored
            out.append(real)
            if len(out) >= self.MAX_FILES:
                break
        return out

    def test(self):
        self._root()
        return {"ok": True, "detail": "Data-lake root is reachable."}

    def discover(self):
        import os

        root = self._root()
        found = {}
        for dp, _dn, fn in os.walk(root):
            for f in fn:
                if f.lower().endswith(self.EXT):
                    rel = os.path.relpath(os.path.join(dp, f), root).replace("\\", "/")
                    found.setdefault(os.path.dirname(rel) or ".", []).append(rel)
            if len(found) > 200:
                break
        return [{"name": f"{d}/*" if d != "." else "*", "fields": [], "files": len(v)} for d, v in sorted(found.items())]

    def fetch(self, spec, since=None):
        import os

        cutoff = None
        if since is not None:
            if hasattr(since, "timestamp"):
                cutoff = since.timestamp()
            else:
                from .mapping_spec import parse_time

                t = parse_time(since)
                cutoff = t.timestamp() if t else None
        for f in self._files(spec["path"]):
            if cutoff is not None and os.path.getmtime(f) <= cutoff:
                continue
            yield from self._read(f)

    def _read(self, f):
        low = f.lower()
        try:
            if low.endswith(".parquet"):
                import pyarrow.parquet as pq

                for batch in pq.ParquetFile(f).iter_batches(batch_size=BATCH):
                    for row in batch.to_pylist():
                        yield {k: json_value(v) for k, v in row.items()}
            elif low.endswith((".jsonl", ".ndjson")):
                with open(f, encoding="utf-8") as fh:
                    for line in fh:
                        if line.strip():
                            r = json.loads(line)
                            if isinstance(r, dict):
                                yield r
            elif low.endswith(".json"):
                with open(f, encoding="utf-8") as fh:
                    data = json.load(fh)
                for r in data if isinstance(data, list) else []:
                    if isinstance(r, dict):
                        yield r
            else:
                with open(f, encoding="utf-8", newline="") as fh:
                    for r in csv.DictReader(fh, delimiter="\t" if low.endswith(".tsv") else ","):
                        yield {k: (v if v != "" else None) for k, v in r.items()}
        except (OSError, ValueError) as e:  # json.JSONDecodeError is a ValueError
            raise ConnectorError(f"Cannot read {re.split(r'[\\/]', f)[-1]}: {str(e)[:150]}") from None


# ------------------------------------------------------------------------ Push (SaaS webhooks)
class PushAdapter(SourceAdapter):
    """For SaaS systems that PUSH changes (webhooks, iPaaS, n8n): records are POSTed to .../fabric/sources/{id}/push/ and picked up
    by the next sync. Append-only: a push source never infers deletions from absence."""
    kind, label = "push", "Push / webhook (SaaS, iPaaS)"
    description = "Systems that send changes to PrimeOntology instead of being polled."
    spec_hint = ""
    append_only = True

    def __init__(self, config, secrets, source=None):
        super().__init__(config, secrets)
        self.source = source

    def test(self):
        n = self.source.inbox.filter(processed=False).count() if self.source else 0
        return {"ok": True, "detail": f"{n} record(s) waiting."}

    def fetch(self, spec, since=None):
        ids = []
        for row in self.source.inbox.filter(processed=False, class_name=spec["class"]).order_by("id").iterator(chunk_size=BATCH):
            ids.append(row.id)
            yield row.payload
        if ids:  # only reached when the whole stream was consumed without error
            self.source.inbox.filter(id__in=ids).update(processed=True)


ADAPTERS = {c.kind: c for c in (MySqlAdapter, RestAdapter, OdooAdapter, SalesforceAdapter, SapODataAdapter, FileAdapter, DataLakeAdapter, PushAdapter)}


def build_adapter(source) -> SourceAdapter:
    from .crypto import decrypt_json

    cls = ADAPTERS.get(source.kind)
    if not cls:
        raise ConnectorError(f"Unknown source kind '{source.kind}'.")
    if cls is PushAdapter:
        return PushAdapter(source.config, decrypt_json(source.secret_enc), source)
    return cls(source.config, decrypt_json(source.secret_enc))


def describe_kinds() -> list[dict]:
    return [{"kind": c.kind, "label": c.label, "description": c.description, "configFields": c.config_fields, "secretFields": c.secret_fields,
             "specHint": c.spec_hint} for c in ADAPTERS.values()]
