from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from prime_ontology import ingest
from prime_ontology.ingest.common import IngestError

from .helpers import make_docx, make_pdf, make_pptx, make_xlsx, sample


def names(res):
    return {c["name"] for c in res["model"]["classes"]}


def rels(res):
    return {(p["domain"], p["name"], p["range"]) for p in res["model"]["objectProperties"]}


class StructuredIngestTests(TestCase):
    def test_csv_types_and_pk(self):
        res = ingest.analyze("customers.csv", sample("customers.csv"))
        self.assertEqual(names(res), {"Customer"})
        dp = {p["name"]: p for p in res["model"]["dataProperties"]}
        self.assertTrue(dp["customerId"]["identifier"])
        self.assertEqual(dp["createdAt"]["datatype"], "date")
        self.assertEqual(res["preview"]["rowCount"], 3)
        self.assertTrue(any(m["target"] == "Customer.customerName" for m in res["mapping"]))

    def test_tsv_and_semicolon(self):
        res = ingest.analyze("a.tsv", b"id\tname\n1\tx\n2\ty\n")
        self.assertEqual(names(res), {"A"})
        res = ingest.analyze("b.csv", b"id;name\n1;x\n2;y\n")
        self.assertEqual(len(res["model"]["dataProperties"]), 2)

    def test_excel_multi_sheet_infers_fk(self):
        data = make_xlsx({"customers": [["customer_id", "name"], [1, "A"], [2, "B"]],
                          "orders": [["order_id", "customer_id", "total"], [10, 1, 5.5], [11, 2, 6.0]]})
        res = ingest.analyze("shop.xlsx", data)
        self.assertEqual(names(res), {"Customer", "Order"})
        self.assertIn(("Order", "hasCustomer", "Customer"), rels(res))
        dp = {(p["domain"], p["name"]): p["datatype"] for p in res["model"]["dataProperties"]}
        self.assertEqual(dp[("Order", "total")], "decimal")

    def test_json_nested(self):
        res = ingest.analyze("enterprise.json", sample("enterprise.json"))
        self.assertIn("Customer", names(res))
        self.assertIn("Supplier", names(res))
        self.assertIn("Contract", names(res))
        self.assertIn(("Contract", "hasCustomer", "Customer"), rels(res))

    def test_xml_yaml(self):
        res = ingest.analyze("catalog.xml", sample("catalog.xml"))
        self.assertIn("Product", names(res))
        self.assertIn("Vendor", names(res))
        res = ingest.analyze("x.yaml", b"people:\n  - id: 1\n    name: A\n  - id: 2\n    name: B\n")
        self.assertEqual(names(res), {"Person"})

    def test_sql(self):
        res = ingest.analyze("s.sql", b"CREATE TABLE a (id int primary key, n text); CREATE TABLE b (id int primary key, a_id int references a(id));")
        self.assertIn(("B", "hasA", "A"), rels(res))

    def test_rdf_declared_and_instance_data(self):
        res = ingest.analyze("people.ttl", sample("people.ttl"))
        self.assertEqual(names(res), {"Person", "Employee", "Department"})
        emp = next(c for c in res["model"]["classes"] if c["name"] == "Employee")
        self.assertEqual(emp["parents"], ["Person"])
        self.assertIn(("Employee", "worksIn", "Department"), rels(res))
        inst = b"""@prefix ex: <http://e.org/> . ex:a a ex:Cat ; ex:name "Tom" ; ex:owner ex:b . ex:b a ex:Human ."""
        res = ingest.analyze("d.ttl", inst)
        self.assertEqual(names(res), {"Cat", "Human"})
        self.assertIn(("Cat", "owner", "Human"), rels(res))
        for fn, fmt in (("x.nt", b'<http://e.org/A> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://www.w3.org/2002/07/owl#Class> .\n'),
                        ("x.jsonld", b'{"@id":"http://e.org/A","@type":"http://www.w3.org/2002/07/owl#Class"}')):
            self.assertEqual(names(ingest.analyze(fn, fmt)), {"A"})

    def test_errors(self):
        for fn, data in (("x.csv", b""), ("x.json", b"{bad"), ("x.xml", b"<a>"), ("x.exe", b"MZ"), ("x.ttl", b"not rdf @@@"),
                         ("x.xlsx", b"nope"), ("x.pdf", b"nope"), ("x.sql", b"select 1")):
            with self.assertRaises(IngestError, msg=fn):
                ingest.analyze(fn, data)

    def test_xxe_not_resolved(self):
        evil = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a><b>&x;</b><b>y</b></a>'
        try:
            res = ingest.analyze("e.xml", evil)
            self.assertNotIn("root:", str(res))
        except IngestError:
            pass


class DocumentIngestTests(TestCase):
    def test_text_contract(self):
        res = ingest.analyze("services_agreement.txt", sample("services_agreement.txt"))
        n = names(res)
        for expected in ("Contract", "Party", "Customer", "Supplier", "Payment", "Obligation", "Confidentiality", "Liability",
                         "Termination", "GoverningLaw", "Penalty", "Jurisdiction"):
            self.assertIn(expected, n)
        self.assertIn(("Contract", "hasParty", "Party"), rels(res))
        cust = next(c for c in res["model"]["classes"] if c["name"] == "Customer")
        self.assertEqual(cust["parents"], ["Party"])
        ob = next(c for c in res["model"]["classes"] if c["name"] == "Obligation")
        self.assertTrue(ob["evidence"] and ob["evidence"][0]["section"])
        self.assertIn("effectiveDate", {p["name"] for p in res["model"]["dataProperties"]})

    def test_pdf_provenance_page_and_section(self):
        pdf = make_pdf(["SERVICES AGREEMENT\nThis Agreement is between Acme and Globex.",
                        "8. PAYMENT TERMS\nThe Customer shall pay each invoice within 30 days.\nThe Supplier must deliver goods."])
        res = ingest.analyze("c.pdf", pdf)
        ob = next(c for c in res["model"]["classes"] if c["name"] == "Obligation")
        self.assertTrue(any(e["page"] == 2 and "PAYMENT" in (e["section"] or "") for e in ob["evidence"]), ob["evidence"])
        self.assertEqual(res["preview"]["pages"], 2)

    def test_pdf_without_text_reports_ocr(self):
        with self.assertRaisesRegex(IngestError, "OCR|extract"):
            ingest.analyze("scan.pdf", make_pdf(["", ""]))

    def test_docx_and_pptx_html_md(self):
        d = make_docx([("Heading 1", "Payment Terms"), ("Normal", "The customer shall pay all fees within 30 days."),
                       ("Heading 1", "Confidentiality"), ("Normal", "Each party agrees to protect confidential information.")])
        res = ingest.analyze("a.docx", d)
        self.assertIn("Payment", names(res))
        self.assertTrue(any(s["heading"] == "Payment Terms" for s in res["preview"]["sections"]))
        p = make_pptx([("Supplier obligations", "The supplier shall deliver services and deliverables.")])
        self.assertIn("Supplier", names(ingest.analyze("a.pptx", p)))
        h = b"<html><body><h1>Liability</h1><p>The supplier is liable for damages and must indemnify.</p></body></html>"
        self.assertIn("Liability", names(ingest.analyze("a.html", h)))
        self.assertIn("Payment", names(ingest.analyze("a.md", b"# Fees\nThe customer shall pay the invoice.")))


class IngestApiTests(TestCase):
    def test_upload_review_commit_merge(self):
        r = self.client.post("/api/v1/ontology/ingest/", {"file": SimpleUploadedFile("customers.csv", sample("customers.csv"))})
        self.assertEqual(r.status_code, 200, r.content)
        cand = r.json()
        self.assertEqual(cand["stats"]["classes"], 1)
        self.assertEqual(self.client.get("/api/v1/ontology/").json()["results"], [])  # not persisted
        from .helpers import jpost
        c = jpost(self.client, "/api/v1/ontology/", {"name": "Enterprise", "model": cand["model"]})
        oid = c.json()["id"]
        r2 = self.client.post("/api/v1/ontology/ingest/", {"file": SimpleUploadedFile("orders.csv", sample("orders.csv"))})
        m = jpost(self.client, "/api/v1/ontology/", {"model": r2.json()["model"], "mergeInto": oid})
        self.assertEqual(m.status_code, 200)
        self.assertEqual({c["name"] for c in m.json()["model"]["classes"]}, {"Customer", "Order"})
        self.assertEqual(m.json()["mergeReport"]["classesAdded"], 1)

    def test_bad_upload(self):
        self.assertEqual(self.client.post("/api/v1/ontology/ingest/", {}).status_code, 400)
        r = self.client.post("/api/v1/ontology/ingest/", {"file": SimpleUploadedFile("x.exe", b"MZ")})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Unsupported", r.json()["error"])
