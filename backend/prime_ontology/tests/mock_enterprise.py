"""A local server that speaks the wire protocols of SAP OData (v2 + v4), Salesforce (OAuth password grant, SOQL, nextRecordsUrl),
Odoo (JSON-RPC common/object, search_read) and a generic paginated REST API. It lets the adapter code run end-to-end in tests.

It is NOT SAP/Salesforce/Odoo: it implements the documented request/response shapes closely enough to exercise our clients
(auth, paging, incremental filters, error handling). Real-system behaviour still needs real-system verification.
"""
import base64
import json
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote, urlparse

SAP_USER, SAP_PASS = "SAPUSER", "sap-secret-pw"
SF_USER, SF_PASS, SF_TOKEN_SUFFIX, SF_ACCESS = "sf@acme.com", "sf-secret-pw", "SECTOK", "00D-ACCESS-TOKEN"
ODOO_DB, ODOO_USER, ODOO_PASS = "acme", "admin", "odoo-secret-pw"
REST_TOKEN = "rest-secret-token"


def ms(iso):
    return int(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


class State:
    def __init__(self):
        self.sap_bp = [
            {"BusinessPartner": "0000100123", "BusinessPartnerFullName": "Ann Lee", "EmailAddress": "ann@acme.com", "LastChangeDate": "2026-01-10T10:00:00Z", "City": "Pune"},
            {"BusinessPartner": "0000100124", "BusinessPartnerFullName": "Bob Ray", "EmailAddress": "bob@acme.com", "LastChangeDate": "2026-02-10T10:00:00Z", "City": "Mumbai"},
            {"BusinessPartner": "0000100125", "BusinessPartnerFullName": "Cy Dee", "EmailAddress": "cy@acme.com", "LastChangeDate": "2026-03-10T10:00:00Z", "City": "Delhi"}]
        self.sf_accounts = [
            {"Id": "001A", "Name": "Ann Lee", "Email__c": "ann@acme.com", "SystemModstamp": "2026-01-15T00:00:00.000+0000", "Phone": "555-0100"},
            {"Id": "001B", "Name": "Dan Poe", "Email__c": "dan@acme.com", "SystemModstamp": "2026-02-15T00:00:00.000+0000", "Phone": "555-0101"},
            {"Id": "001C", "Name": "Eve Ng", "Email__c": "eve@acme.com", "SystemModstamp": "2026-03-15T00:00:00.000+0000", "Phone": "555-0102"}]
        self.odoo_partners = [
            {"id": 55, "name": "Ann M. Lee", "email": "ann@acme.com", "phone": "+1 555 0100", "write_date": "2026-01-20 08:00:00"},
            {"id": 56, "name": "Fay Roe", "email": "fay@acme.com", "phone": False, "write_date": "2026-02-20 08:00:00"},
            {"id": 57, "name": "Gus Lin", "email": "gus@acme.com", "phone": False, "write_date": "2026-03-20 08:00:00"}]
        self.rest_items = [{"id": i, "name": f"Item {i}", "updated": f"2026-01-{i:02d}T00:00:00Z", "owner": {"id": i % 2}} for i in range(1, 8)]
        self.calls = []
        self.redirect_to = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    @property
    def st(self):
        return self.server.state

    def send(self, code, obj, ctype="application/json"):
        body = json.dumps(obj).encode() if not isinstance(obj, bytes) else obj
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def record(self, method):
        u = urlparse(self.path)
        self.st.calls.append({"method": method, "path": u.path, "query": parse_qs(u.query), "headers": {k.lower(): v for k, v in self.headers.items()}})
        return u, {k: v[0] for k, v in parse_qs(u.query).items()}

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        u, q = self.record("GET")
        p = u.path
        if p == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data")
            self.end_headers()
            return
        if p.startswith("/sap/"):
            return self.sap(p, q)
        if p.startswith("/odata4/"):
            return self.sap4(p, q)
        if p.startswith("/services/data/"):
            return self.sf_get(p, q)
        if p.startswith("/api/"):
            return self.rest(p, q)
        self.send(404, {"error": "not found"})

    def sap_auth(self):
        h = self.headers.get("Authorization", "")
        ok = h == "Basic " + base64.b64encode(f"{SAP_USER}:{SAP_PASS}".encode()).decode()
        if not ok:
            self.send(401, {"error": {"message": f"Unauthorized (sent {h})"}})
        return ok

    def sap(self, p, q):
        if not self.sap_auth():
            return
        if p.rstrip("/").endswith("API_BUSINESS_PARTNER"):
            return self.send(200, {"d": {"EntitySets": ["A_BusinessPartner"]}})
        rows = list(self.st.sap_bp)
        flt = q.get("$filter", "")
        m = re.search(r"LastChangeDate gt datetime'([^']+)'", flt)
        if m:
            rows = [r for r in rows if r["LastChangeDate"].rstrip("Z") > m.group(1)]
        top, skip = int(q.get("$top", 100)), int(q.get("$skip", 0))
        page = rows[skip:skip + top]
        out = [{"__metadata": {"uri": "x"}, **{k: (f"/Date({ms(v)})/" if k == "LastChangeDate" else v) for k, v in r.items()}} for r in page]
        d = {"results": out}
        if skip + top < len(rows):
            d["__next"] = f"A_BusinessPartner?$skip={skip + top}&$top={top}&$format=json" + (f"&$filter={flt}" if flt else "")
        self.send(200, {"d": d})

    def sap4(self, p, q):
        h = self.headers.get("Authorization", "")
        if h != "Bearer sap4-token":
            return self.send(401, {"error": {"message": "bad token"}})
        rows = list(self.st.sap_bp)
        m = re.search(r"LastChangeDate gt (\S+)", q.get("$filter", ""))
        if m:
            rows = [r for r in rows if r["LastChangeDate"] > m.group(1)]
        top, skip = int(q.get("$top", 100)), int(q.get("$skip", 0))
        body = {"value": rows[skip:skip + top]}
        if skip + top < len(rows):
            body["@odata.nextLink"] = f"BusinessPartners?$skip={skip + top}&$top={top}" + (f"&$filter={q['$filter']}" if q.get("$filter") else "")
        self.send(200, body)

    def sf_get(self, p, q):
        if self.headers.get("Authorization") != f"Bearer {SF_ACCESS}":
            return self.send(401, [{"message": "Session expired or invalid", "errorCode": "INVALID_SESSION_ID"}])
        if p.endswith("/limits"):
            return self.send(200, {"DailyApiRequests": {"Max": 100000, "Remaining": 99999}})
        if p.endswith("/sobjects"):
            return self.send(200, {"sobjects": [{"name": "Account", "queryable": True}, {"name": "Contact", "queryable": True}, {"name": "Hidden", "queryable": False}]})
        if p.endswith("/query") or "/query/" in p:
            soql = q.get("q", "")
            if soql:
                self.server.last_soql = soql
                m = re.search(r"SystemModstamp > (\S+)", soql)
                rows = list(self.st.sf_accounts)
                if m:
                    since = m.group(1).rstrip("Z")
                    rows = [r for r in rows if r["SystemModstamp"][:19] > since[:19]]
                self.server.sf_rows = rows
                start = 0
            else:
                start = int(p.rsplit("-", 1)[-1])
                rows = self.server.sf_rows
            page = rows[start:start + 2]
            body = {"totalSize": len(rows), "done": start + 2 >= len(rows), "records": [{"attributes": {"type": "Account"}, **r} for r in page]}
            if start + 2 < len(rows):
                body["nextRecordsUrl"] = f"/services/data/v59.0/query/01gXX-{start + 2}"
            return self.send(200, body)
        self.send(404, {})

    def rest(self, p, q):
        if p == "/api/open" or self.headers.get("Authorization") == f"Bearer {REST_TOKEN}":
            pass
        else:
            return self.send(401, {"error": "unauthorized"})
        items = self.st.rest_items
        if p == "/api/page":
            since = q.get("updated_since")
            rows = [r for r in items if not since or r["updated"] > since]
            page, size = int(q.get("page", 1)), int(q.get("per_page", 3))
            return self.send(200, {"data": {"items": rows[(page - 1) * size: page * size]}})
        if p == "/api/cursor":
            cur = int(q.get("cursor", 0))
            nxt = {"links": {"next": f"/api/cursor?cursor={cur + 3}"}} if cur + 3 < len(items) else {"links": {"next": None}}
            return self.send(200, {"results": items[cur:cur + 3], **nxt})
        if p == "/api/notjson":
            return self.send(200, b"<html>hi</html>", "text/html")
        if p == "/api/open":
            return self.send(200, {"ok": True})
        self.send(404, {})

    # ----------------------------------------------------------------- POST
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        u = urlparse(self.path)
        self.st.calls.append({"method": "POST", "path": u.path, "query": {}, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": raw.decode("utf-8", "replace")})
        if u.path == "/services/oauth2/token":
            f = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            if f.get("username") == SF_USER and f.get("password") == SF_PASS + SF_TOKEN_SUFFIX and f.get("grant_type") == "password":
                host = self.headers.get("Host")
                return self.send(200, {"access_token": SF_ACCESS, "instance_url": f"http://{host}"})
            return self.send(400, {"error": "invalid_grant", "error_description": f"authentication failure for {f.get('password')}"})
        if u.path == "/jsonrpc":
            return self.odoo(json.loads(raw))
        self.send(404, {})

    def odoo(self, req):
        pr = req["params"]
        svc, method, a = pr["service"], pr["method"], pr["args"]

        def ok(r):
            self.send(200, {"jsonrpc": "2.0", "id": req.get("id"), "result": r})

        def err(msg):
            self.send(200, {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": 200, "message": "Odoo Server Error", "data": {"message": msg}}})

        if svc == "common" and method == "version":
            return ok({"server_version": "17.0"})
        if svc == "common" and method == "login":
            return ok(2 if a == [ODOO_DB, ODOO_USER, ODOO_PASS] else False)
        if svc == "object" and method == "execute_kw":
            db, uid, pw, model, m, args, kw = a
            if (db, uid, pw) != (ODOO_DB, 2, ODOO_PASS):
                return err(f"Access denied for password {pw}")
            if model == "ir.model":
                return ok([{"id": 1, "model": "res.partner", "name": "Contact"}])
            if model == "res.partner" and m == "fields_get":
                return ok({"id": {"type": "integer"}, "name": {"type": "char"}, "email": {"type": "char"}, "phone": {"type": "char"},
                           "write_date": {"type": "datetime"}, "active": {"type": "boolean"}})
            if model == "res.partner" and m == "search_read":
                rows = list(self.st.odoo_partners)
                for dom in args[0]:
                    if dom[0] == "write_date" and dom[1] == ">":
                        rows = [r for r in rows if r["write_date"] > dom[2]]
                rows = rows[kw.get("offset", 0): kw.get("offset", 0) + kw.get("limit", 100)]
                return ok([{k: v for k, v in r.items() if not kw.get("fields") or k in kw["fields"] or k == "id"} for r in rows])
            return err(f"Unknown model {model}")
        err("bad request")


class MockEnterprise:
    def __init__(self):
        self.srv = HTTPServer(("127.0.0.1", 0), Handler)
        self.srv.state = State()
        self.srv.sf_rows = []
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def state(self):
        return self.srv.state

    @property
    def base(self):
        return f"http://127.0.0.1:{self.srv.server_port}"

    def stop(self):
        self.srv.shutdown()
