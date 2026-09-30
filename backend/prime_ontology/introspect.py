"""Schema introspection -> neutral schema dict.

Schema shape:
{"source": {"type", "database", "schema"},
 "tables": [{"name", "columns": [{"name","type","nullable","pk"}],
             "primary_key": [..],
             "foreign_keys": [{"columns": [..], "ref_table": str, "ref_columns": [..]}]}]}
"""
import re
from collections import defaultdict


class IntrospectionError(Exception):
    pass


def _finish(source, tables_by_name, pks, fks):
    tables = []
    for name, cols in tables_by_name.items():
        pk = pks.get(name, [])
        for c in cols:
            c["pk"] = c["name"] in pk
        tables.append({"name": name, "columns": cols, "primary_key": pk,
                       "foreign_keys": fks.get(name, [])})
    return {"source": source, "tables": tables}


def introspect_mysql(cfg: dict, schema: str | None = None) -> dict:
    try:
        import pymysql
    except ImportError as e:  # pragma: no cover
        raise IntrospectionError("PyMySQL is not installed") from e
    schema = schema or cfg["database"]
    try:
        conn = pymysql.connect(
            host=cfg.get("host", "localhost"), port=int(cfg.get("port") or 3306),
            user=cfg.get("user"), password=cfg.get("password") or "",
            database=cfg["database"], connect_timeout=8,
        )
    except Exception as e:
        raise IntrospectionError(f"MySQL connection failed: {e}") from e
    with conn, conn.cursor() as cur:
        cur.execute(
            """SELECT c.table_name, c.column_name, c.column_type, c.is_nullable, c.column_key
               FROM information_schema.columns c
               JOIN information_schema.tables t
                 ON t.table_schema=c.table_schema AND t.table_name=c.table_name
               WHERE c.table_schema=%s AND t.table_type='BASE TABLE'
               ORDER BY c.table_name, c.ordinal_position""", (schema,))
        tables, pks = defaultdict(list), defaultdict(list)
        for t, c, ct, nul, key in cur.fetchall():
            tables[t].append({"name": c, "type": ct, "nullable": nul == "YES"})
            if key == "PRI":
                pks[t].append(c)
        cur.execute(
            """SELECT table_name, constraint_name, column_name, referenced_table_name, referenced_column_name
               FROM information_schema.key_column_usage
               WHERE table_schema=%s AND referenced_table_name IS NOT NULL
               ORDER BY table_name, constraint_name, ordinal_position""", (schema,))
        grouped = {}
        for t, cn, c, rt, rc in cur.fetchall():
            g = grouped.setdefault((t, cn), {"table": t, "columns": [], "ref_table": rt, "ref_columns": []})
            g["columns"].append(c)
            g["ref_columns"].append(rc)
        fks = defaultdict(list)
        for g in grouped.values():
            fks[g["table"]].append({k: g[k] for k in ("columns", "ref_table", "ref_columns")})
    return _finish({"type": "mysql", "database": cfg["database"], "schema": schema}, tables, pks, fks)


# ---------------------------------------------------------------- SQL DDL --

_IDENT = r'(?:`[^`]+`|"[^"]+"|\[[^\]]+\]|[A-Za-z_][\w$]*)'
_QNAME = rf"(?:{_IDENT}\s*\.\s*)*{_IDENT}"


def _unq(s: str) -> str:
    s = s.strip()
    if s and s[0] in '`"[':
        s = s[1:-1]
    return s.split(".")[-1].strip('`"[] ') if "." in s else s


def _split_top(body: str) -> list[str]:
    parts, depth, cur, quote = [], 0, [], None
    for ch in body:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"`":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


def _idents(s: str) -> list[str]:
    return [_unq(x) for x in re.findall(_IDENT, s)]


def parse_sql_ddl(script: str) -> dict:
    """Parse CREATE TABLE / ALTER TABLE ... FOREIGN KEY statements."""
    script = re.sub(r"--[^\n]*", "", script)
    script = re.sub(r"/\*.*?\*/", "", script, flags=re.S)
    tables_by_name, pks, fks = {}, {}, defaultdict(list)

    for m in re.finditer(
        rf"CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?({_QNAME})\s*\(", script, re.I
    ):
        name = _unq(re.findall(_IDENT, m.group(1))[-1])
        depth, i = 1, m.end()
        while i < len(script) and depth:
            depth += {"(": 1, ")": -1}.get(script[i], 0)
            i += 1
        cols, pk = [], []
        for item in _split_top(script[m.end():i - 1]):
            head = item.lstrip()
            up = head.upper()
            if re.match(r"(CONSTRAINT\s+\S+\s+)?PRIMARY\s+KEY", up):
                pk = _idents(re.search(r"\((.*)\)", head, re.S).group(1))
            elif re.match(r"(CONSTRAINT\s+\S+\s+)?FOREIGN\s+KEY", up):
                mm = re.search(rf"FOREIGN\s+KEY\s*\((.*?)\)\s*REFERENCES\s+({_QNAME})\s*(?:\((.*?)\))?", head, re.I | re.S)
                if mm:
                    ref_cols = _idents(mm.group(3)) if mm.group(3) else []
                    fks[name].append({"columns": _idents(mm.group(1)),
                                      "ref_table": _unq(re.findall(_IDENT, mm.group(2))[-1]),
                                      "ref_columns": ref_cols})
            elif re.match(r"(CONSTRAINT|UNIQUE|KEY|INDEX|CHECK|FULLTEXT|EXCLUDE)\b", up):
                continue
            else:
                cm = re.match(rf"({_IDENT})\s+([A-Za-z_][\w ]*?(?:\([^)]*\))?)(?=\s|$)(.*)", head, re.S)
                if not cm:
                    continue
                cname, ctype, rest = _unq(cm.group(1)), cm.group(2).strip(), cm.group(3)
                cols.append({"name": cname, "type": ctype,
                             "nullable": not re.search(r"NOT\s+NULL|PRIMARY\s+KEY", rest, re.I)})
                if re.search(r"PRIMARY\s+KEY", rest, re.I):
                    pk = [cname]
                ref = re.search(rf"REFERENCES\s+({_QNAME})\s*(?:\((.*?)\))?", rest, re.I)
                if ref:
                    fks[name].append({"columns": [cname],
                                      "ref_table": _unq(re.findall(_IDENT, ref.group(1))[-1]),
                                      "ref_columns": _idents(ref.group(2)) if ref.group(2) else []})
        tables_by_name[name] = cols
        pks[name] = pk

    for m in re.finditer(
        rf"ALTER\s+TABLE\s+(?:ONLY\s+)?({_QNAME})\s+ADD\s+(?:CONSTRAINT\s+\S+\s+)?FOREIGN\s+KEY\s*\((.*?)\)\s*REFERENCES\s+({_QNAME})\s*(?:\((.*?)\))?",
        script, re.I | re.S,
    ):
        t = _unq(re.findall(_IDENT, m.group(1))[-1])
        fks[t].append({"columns": _idents(m.group(2)),
                       "ref_table": _unq(re.findall(_IDENT, m.group(3))[-1]),
                       "ref_columns": _idents(m.group(4)) if m.group(4) else []})

    if not tables_by_name:
        raise IntrospectionError("No CREATE TABLE statements found in the SQL script.")
    for t, lst in fks.items():  # resolve implicit refs to the target primary key
        for fk in lst:
            if not fk["ref_columns"]:
                fk["ref_columns"] = pks.get(fk["ref_table"], [])[: len(fk["columns"])]
    return _finish({"type": "sql", "database": "script", "schema": ""}, tables_by_name, pks, fks)


def infer_missing_relationships(schema: dict) -> int:
    """Many schemas (e.g. Django/ORM-managed) omit DB-level FKs. Infer them from
    naming (order.customer_id -> customer) for columns that are not already FKs.
    Inferred FKs are flagged so provenance shows they were not declared."""
    from .ingest.common import infer_foreign_keys

    before = {id(t): len(t["foreign_keys"]) for t in schema["tables"]}
    infer_foreign_keys(schema["tables"])
    n = 0
    for t in schema["tables"]:
        for fk in t["foreign_keys"][before[id(t)]:]:
            fk["inferred"] = True
            n += 1
    return n


def introspect(source_type: str, cfg: dict) -> dict:
    if source_type == "mysql":
        schema = introspect_mysql(cfg, cfg.get("schema") or None)
    elif source_type == "sql":
        return parse_sql_ddl(cfg.get("script", ""))
    else:
        raise IntrospectionError(f"Unsupported source type: {source_type}")
    if cfg.get("inferRelationships", True):
        schema["source"]["inferredRelationships"] = infer_missing_relationships(schema)
    return schema
