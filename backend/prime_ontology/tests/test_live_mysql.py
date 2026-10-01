"""Live MySQL integration. Skipped unless PRIME_TEST_MYSQL_PASSWORD is set.

  PRIME_TEST_MYSQL_PASSWORD=... [PRIME_TEST_MYSQL_HOST/PORT/USER] [PRIME_TEST_MYSQL_DB=primeontology_demo] python manage.py test
Requires a schema loaded from demo/demo_schema.sql. Read-only: only information_schema is queried.
"""
import json
import os
import unittest

from django.test import TestCase

from prime_ontology import generator
from prime_ontology.introspect import IntrospectionError, introspect

PW = os.environ.get("PRIME_TEST_MYSQL_PASSWORD")
CFG = {"host": os.environ.get("PRIME_TEST_MYSQL_HOST", "localhost"), "port": int(os.environ.get("PRIME_TEST_MYSQL_PORT", 3306)),
       "user": os.environ.get("PRIME_TEST_MYSQL_USER", "root"), "password": PW,
       "database": os.environ.get("PRIME_TEST_MYSQL_DB", "primeontology_demo")}


@unittest.skipUnless(PW, "set PRIME_TEST_MYSQL_PASSWORD to run live MySQL tests")
class LiveMySQLTests(TestCase):
    def test_introspection_and_generation(self):
        s = introspect("mysql", CFG)
        self.assertEqual({t["name"] for t in s["tables"]}, {"customer", "product", "sales_order", "sales_order_item", "supplier"})
        item = next(t for t in s["tables"] if t["name"] == "sales_order_item")
        self.assertEqual(sorted(fk["ref_table"] for fk in item["foreign_keys"]), ["product", "sales_order"])
        self.assertEqual(item["primary_key"], ["item_id"])
        m = generator.generate_model(s)
        rels = {(p["domain"], p["name"], p["range"]) for p in m["objectProperties"]}
        self.assertIn(("SalesOrder", "hasCustomer", "Customer"), rels)
        dp = {(p["domain"], p["name"]): p["datatype"] for p in m["dataProperties"]}
        self.assertEqual(dp[("Product", "unitPrice")], "decimal")
        self.assertEqual(dp[("SalesOrder", "orderDate")], "date")
        self.assertEqual(dp[("SalesOrder", "isPaid")], "boolean")  # tinyint(1)
        self.assertFalse(any(x["source"].get("inferred") for x in m["objectProperties"]))  # declared FKs, not inferred

    def test_api_never_echoes_password_and_bad_credentials_fail_cleanly(self):
        r = self.client.post("/api/v1/ontology/generate/", json.dumps({"name": "Live", "type": "mysql", "config": CFG}), content_type="application/json")
        self.assertEqual(r.status_code, 201)
        self.assertNotIn(PW, r.content.decode())
        bad = self.client.post("/api/v1/ontology/inspect/", json.dumps({"type": "mysql", "config": {**CFG, "password": "definitely-wrong"}}),
                               content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        self.assertNotIn(PW, bad.content.decode())
        with self.assertRaises(IntrospectionError):
            introspect("mysql", {**CFG, "database": "no_such_database_xyz"})

    def test_sample_records_are_read_linked_and_capped(self):
        s = introspect("mysql", {**CFG, "sampleRows": 2})
        sample = {t["name"]: t.get("sample") for t in s["tables"]}
        self.assertTrue(all(v is None or len(v["rows"]) <= 3 for v in sample.values()))  # n+1 rows fetched to detect truncation
        m = generator.generate_model(s)
        from prime_ontology import records

        m2, rep = records.attach_individuals(m, s, 2)
        self.assertEqual(rep["maxRowsPerTable"], 2)
        self.assertTrue(all(len([i for i in m2["individuals"] if i["class"] == c["name"]]) <= 2 for c in m["classes"]))
        none = introspect("mysql", CFG)
        self.assertTrue(all("sample" not in t for t in none["tables"]))  # off by default: no row data is read
