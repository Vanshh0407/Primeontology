import io
import json
import os
import sqlite3
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from prime_ontology import ingest, query, reasoning, validation
from prime_ontology.ingest.common import IngestError

from .helpers import jpost, make_xlsx


def sqlite_bytes(statements):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        con = sqlite3.connect(path)
        for s in statements:
            con.execute(s)
        con.commit()
        con.close()
        return open(path, "rb").read()
    finally:
        os.unlink(path)


SHOP = [
    "CREATE TABLE supplier (supplier_id INTEGER PRIMARY KEY, supplier_name TEXT NOT NULL, state TEXT)",
    "CREATE TABLE customer (customer_id INTEGER PRIMARY KEY, customer_name TEXT NOT NULL, password_hash TEXT, api_key TEXT)",
    "CREATE TABLE product (product_id INTEGER PRIMARY KEY, product_name TEXT, supplier_id INTEGER REFERENCES supplier(supplier_id))",
    "CREATE TABLE sales_order (order_id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customer(customer_id), total REAL)",
    "CREATE TABLE sales_order_item (item_id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES sales_order(order_id), product_id INTEGER REFERENCES product(product_id), qty INTEGER)",
    "INSERT INTO supplier VALUES (1,'Acme Metals','Maharashtra'),(2,'Globex Parts','Karnataka')",
    "INSERT INTO customer VALUES (1,'Initech','$2b$secret','sk-live-123'),(2,'Hooli','$2b$other','sk-live-456')",
    "INSERT INTO product VALUES (10,'Bolt',1),(11,'Gear',2)",
    "INSERT INTO sales_order VALUES (100,1,125.5),(101,2,80.0)",
    "INSERT INTO sales_order_item VALUES (1000,100,10,5),(1001,101,11,2)",
]


def names(res):
    return {c["name"] for c in res["model"]["classes"]}


class SqliteTests(TestCase):
    def test_structure_only_by_default_no_data_copied(self):
        res = ingest.analyze("shop.db", sqlite_bytes(SHOP))
        self.assertEqual(names(res), {"Supplier", "Customer", "Product", "SalesOrder", "SalesOrderItem"})
        self.assertEqual(res["model"].get("individuals", []), [])
        rels = {(p["domain"], p["name"], p["range"]) for p in res["model"]["objectProperties"]}
        self.assertIn(("SalesOrderItem", "hasProduct", "Product"), rels)
        self.assertIn(("Product", "hasSupplier", "Supplier"), rels)
        self.assertNotIn("sample", json.dumps(res["schema"]))

    def test_records_become_linked_individuals_and_sensitive_columns_are_never_copied(self):
        res = ingest.analyze("shop.db", sqlite_bytes(SHOP), records_per_table=50)
        inds = {i["name"]: i for i in res["model"]["individuals"]}
        self.assertEqual(len(inds), 10)
        o = inds["salesorder_100"]
        self.assertEqual(o["links"]["hasCustomer"], ["customer_1"])
        self.assertEqual(o["data"]["total"], 125.5)
        item = inds["salesorderitem_1000"]
        self.assertEqual((item["links"]["hasOrder"], item["links"]["hasProduct"]), (["salesorder_100"], ["product_10"]))
        flat = json.dumps(res["model"]["individuals"])
        for secret in ("$2b$secret", "sk-live-123", "password_hash", "passwordHash", "apiKey"):
            self.assertNotIn(secret, flat)
        self.assertEqual(set(res["recordsReport"]["skippedSensitiveColumns"]), {"customer.password_hash", "customer.api_key"})

    def test_row_cap_and_hard_limit(self):
        stmts = ["CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)"] + [f"INSERT INTO t VALUES ({i}, 'n{i}')" for i in range(1, 121)]
        res = ingest.analyze("t.db", sqlite_bytes(stmts), records_per_table=10)
        self.assertEqual(len(res["model"]["individuals"]), 10)
        self.assertEqual(res["recordsReport"]["truncatedTables"], ["t"])
        res = ingest.analyze("t.db", sqlite_bytes(stmts), records_per_table=99999)
        self.assertEqual(res["recordsReport"]["maxRowsPerTable"], 500)  # server-side hard cap
        self.assertEqual(len(res["model"]["individuals"]), 120)

    def test_links_outside_the_sample_window_are_counted_not_invented(self):
        stmts = ["CREATE TABLE p (id INTEGER PRIMARY KEY)", "CREATE TABLE c (id INTEGER PRIMARY KEY, p_id INTEGER REFERENCES p(id))",
                 "INSERT INTO p VALUES (1),(2),(3)", "INSERT INTO c VALUES (1,3),(2,1)"]
        res = ingest.analyze("x.db", sqlite_bytes(stmts), records_per_table=2)  # parent 3 is outside the 2-row window
        c1 = next(i for i in res["model"]["individuals"] if i["name"] == "c_1")
        self.assertEqual(c1["links"], {})
        self.assertEqual(res["recordsReport"]["linksOutsideSample"], 1)

    def test_bad_files(self):
        with self.assertRaisesRegex(IngestError, "Not a SQLite"):
            ingest.analyze("x.db", b"definitely not sqlite")
        with self.assertRaisesRegex(IngestError, "no tables"):
            ingest.analyze("e.db", sqlite_bytes(["CREATE VIEW v AS SELECT 1"]))


class ParquetAndTabularTests(TestCase):
    def test_parquet_schema_and_records(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        t = pa.table({"customer_id": [1, 2, 3], "name": ["a", "b", "c"], "balance": [1.5, 2.5, 3.5], "active": [True, False, True],
                      "joined": pa.array([None, None, None], type=pa.date32())})
        buf = io.BytesIO()
        pq.write_table(t, buf)
        res = ingest.analyze("customers.parquet", buf.getvalue(), records_per_table=2)
        dp = {p["name"]: p["datatype"] for p in res["model"]["dataProperties"]}
        self.assertEqual((dp["balance"], dp["active"], dp["joined"]), ("double", "boolean", "date"))
        self.assertEqual(len(res["model"]["individuals"]), 2)
        self.assertEqual(res["preview"]["rowCount"], 3)
        with self.assertRaises(IngestError):
            ingest.analyze("bad.parquet", b"nope")

    def test_excel_and_csv_records_link_across_sheets(self):
        data = make_xlsx({"customers": [["customer_id", "name"], [1, "A"], [2, "B"]],
                          "orders": [["order_id", "customer_id", "total"], [10, 1, 5.5], [11, 2, 6.0]]})
        res = ingest.analyze("shop.xlsx", data, records_per_table=10)
        o10 = next(i for i in res["model"]["individuals"] if i["name"] == "order_10")
        self.assertEqual(o10["links"]["hasCustomer"], ["customer_1"])
        csv = ingest.analyze("c.csv", b"id,name,password\n1,x,hunter2\n2,y,swordfish\n", records_per_table=5)
        self.assertNotIn("hunter2", json.dumps(csv["model"]))
        self.assertEqual(len(csv["model"]["individuals"]), 2)

    def test_records_on_unsupported_format_warns_instead_of_silently_ignoring(self):
        res = ingest.analyze("d.json", b'{"customers":[{"id":1,"name":"x"}]}', records_per_table=10)
        self.assertTrue(any("not supported" in w for w in res["warnings"]))


class RecordsEndToEndTests(TestCase):
    """The payoff: with records, a plain-word question returns real rows."""

    def setUp(self):
        r = self.client.post("/api/v1/ontology/ingest/", {"file": SimpleUploadedFile("shop.db", sqlite_bytes(SHOP)), "records": "50"})
        self.assertEqual(r.status_code, 200, r.content)
        self.cand = r.json()
        o = jpost(self.client, "/api/v1/ontology/", {"name": "Shop", "model": self.cand["model"]}).json()
        self.oid, self.model = o["id"], o["model"]

    def test_natural_language_returns_rows(self):
        r = jpost(self.client, f"/api/v1/ontology/{self.oid}/query/",
                  {"kind": "natural", "text": "Find customers who placed sales orders for products supplied by suppliers"})
        self.assertEqual(r.status_code, 200, r.content)
        res = r.json()
        self.assertEqual(res["result"]["count"], 2)
        self.assertTrue(res["hasIndividuals"])
        first = dict(zip(res["result"]["columns"], res["result"]["rows"][0]))
        self.assertTrue(first["v0"].startswith("onto:customer_"))

    def test_the_maharashtra_style_filter_works_with_sparql(self):
        sp = ("SELECT ?customerName WHERE { ?c a onto:Customer ; onto:customerName ?customerName . ?o onto:hasCustomer ?c . "
              "?i onto:hasOrder ?o ; onto:hasProduct ?p . ?p onto:hasSupplier ?s . ?s onto:state \"Maharashtra\" }")
        res = query.run_sparql(self.model, sp)
        self.assertEqual(res["rows"], [["Initech"]])  # the plan's "customers ... suppliers in Maharashtra" example

    def test_individuals_validate_and_reason_cleanly(self):
        v = validation.validate(self.model)
        self.assertEqual(v["errors"], 0, [i for i in v["issues"] if i["severity"] == "error"])
        self.assertTrue(reasoning.reason(self.model)["consistent"])
        self.assertTrue(reasoning.shacl_validate(self.model, None)["conforms"])


class UrlSourceTests(TestCase):
    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.server.seen.append(dict(self.headers))
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://169.254.169.254/latest/meta-data")
                    self.end_headers()
                    return
                body, ctype = (b"<html>nope</html>", "text/html") if self.path == "/html" else (
                    json.dumps({"customers": [{"id": 1, "name": "Initech", "city": "Pune"}, {"id": 2, "name": "Hooli", "city": "Pune"}]}).encode(), "application/json")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        super().setUpClass()
        cls.srv = HTTPServer(("127.0.0.1", 0), H)
        cls.srv.seen = []
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        super().tearDownClass()

    def post(self, url, **extra):
        return jpost(self.client, "/api/v1/ontology/ingest-url/", {"url": url, **extra})

    def test_disabled_by_default(self):
        r = self.post(f"{self.base}/api/customers")
        self.assertEqual(r.status_code, 403)
        self.assertIn("disabled", r.json()["error"])
        self.assertEqual(self.client.get("/api/v1/ontology/supported/").json()["urlSources"], False)

    def test_allow_listed_host_works_and_auth_header_is_forwarded_once(self):
        from django.test import override_settings

        with override_settings(PRIME_ONTOLOGY_ALLOWED_URL_HOSTS=["127.0.0.1"], PRIME_ONTOLOGY_ALLOW_HTTP_URLS=True):
            r = self.post(f"{self.base}/api/customers", authorization="Bearer abc123")
            self.assertEqual(r.status_code, 200, r.content)
            self.assertIn("Customer", {c["name"] for c in r.json()["model"]["classes"]})
            self.assertTrue(any(h.get("Authorization") == "Bearer abc123" for h in self.srv.seen))
            self.assertNotIn("abc123", r.content.decode())  # never echoed back

    def test_blocks_other_hosts_schemes_redirects_credentials_and_non_json(self):
        from django.test import override_settings

        with override_settings(PRIME_ONTOLOGY_ALLOWED_URL_HOSTS=["127.0.0.1"], PRIME_ONTOLOGY_ALLOW_HTTP_URLS=True):
            self.assertEqual(self.post("http://169.254.169.254/latest/meta-data").status_code, 403)  # cloud-metadata host: not allow-listed
            self.assertEqual(self.post("file:///etc/passwd").status_code, 403)
            self.assertEqual(self.post(f"http://user:pw@127.0.0.1:{self.srv.server_port}/x").status_code, 403)
            red = self.post(f"{self.base}/redirect")
            self.assertEqual(red.status_code, 400)
            self.assertIn("redirect", red.json()["error"])  # never followed to the internal address
            self.assertEqual(self.post(f"{self.base}/html").status_code, 400)
        with override_settings(PRIME_ONTOLOGY_ALLOWED_URL_HOSTS=["127.0.0.1"], PRIME_ONTOLOGY_ALLOW_HTTP_URLS=False):
            self.assertEqual(self.post(f"{self.base}/api/customers").status_code, 403)  # plain http refused by default
