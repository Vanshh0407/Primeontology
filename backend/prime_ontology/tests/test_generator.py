import json
from pathlib import Path

from django.test import TestCase

from prime_ontology import generator
from prime_ontology.introspect import parse_sql_ddl
from prime_ontology.naming import to_class_name, to_property_name

DEMO = Path(__file__).resolve().parents[3] / "demo" / "demo_schema.sql"


class NamingTests(TestCase):
    def test_names(self):
        self.assertEqual(to_class_name("SALES_ORDER"), "SalesOrder")
        self.assertEqual(to_class_name("customers"), "Customer")
        self.assertEqual(to_property_name("CUSTOMER_ID"), "customerId")


class GeneratorTests(TestCase):
    def setUp(self):
        self.schema = parse_sql_ddl(DEMO.read_text())
        self.model = generator.generate_model(self.schema)

    def test_classes_and_relationships(self):
        self.assertEqual({c["name"] for c in self.model["classes"]},
                         {"Supplier", "Customer", "Product", "SalesOrder", "SalesOrderItem"})
        rels = {(p["domain"], p["name"], p["range"]) for p in self.model["objectProperties"]}
        self.assertIn(("SalesOrder", "hasCustomer", "Customer"), rels)
        self.assertIn(("Product", "hasSupplier", "Supplier"), rels)
        self.assertIn(("SalesOrderItem", "hasProduct", "Product"), rels)

    def test_datatypes_and_fk_columns_excluded(self):
        dp = {(p["domain"], p["name"]): p for p in self.model["dataProperties"]}
        self.assertEqual(dp[("Product", "unitPrice")]["datatype"], "decimal")
        self.assertEqual(dp[("SalesOrder", "orderDate")]["datatype"], "date")
        self.assertEqual(dp[("SalesOrder", "isPaid")]["datatype"], "boolean")
        self.assertNotIn(("SalesOrder", "customerId"), dp)
        self.assertTrue(dp[("Customer", "customerId")]["identifier"])

    def test_junction_table_becomes_many_to_many(self):
        s = parse_sql_ddl("""CREATE TABLE a (id int primary key);
            CREATE TABLE b (id int primary key);
            CREATE TABLE a_b (a_id int references a(id), b_id int references b(id), primary key (a_id, b_id));""")
        m = generator.generate_model(s)
        self.assertEqual({c["name"] for c in m["classes"]}, {"A", "B"})
        self.assertEqual(m["objectProperties"][0]["cardinality"], "many-to-many")

    def test_serialization(self):
        ttl, _, _ = generator.serialize(self.model, "turtle", "demo")
        self.assertIn("owl:ObjectProperty", ttl)
        self.assertIn("xsd:decimal", ttl)
        for fmt in ("xml", "json-ld", "nt"):
            data, _, _ = generator.serialize(self.model, fmt, "demo")
            self.assertTrue(data)


class ApiTests(TestCase):
    def test_generate_export_roundtrip(self):
        r = self.client.post("/api/v1/ontology/generate/", json.dumps(
            {"name": "Demo", "type": "sql", "config": {"script": DEMO.read_text()}}), content_type="application/json")
        self.assertEqual(r.status_code, 201)
        oid = r.json()["ontology"]["id"]
        self.assertEqual(r.json()["stats"]["classes"], 5)
        exp = self.client.get(f"/api/v1/ontology/{oid}/export/?format=turtle")
        self.assertEqual(exp.status_code, 200)
        self.assertIn("SalesOrder", exp.content.decode())

    def test_bad_input(self):
        r = self.client.post("/api/v1/ontology/inspect/", json.dumps({"type": "sql", "config": {"script": "nope"}}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)
