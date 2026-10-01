import json

from django.test import TestCase, override_settings

from prime_ontology.fabric import crypto
from prime_ontology.fabric.models import FabricSource

from . import mock_enterprise as M
from .fabric_helpers import make_ontology

ALLOW = {"PRIME_ONTOLOGY_CONNECTOR_HOSTS": ["127.0.0.1"], "PRIME_ONTOLOGY_ALLOW_HTTP_URLS": True}
CSV = "id,name,email,city\n1,Ann Lee,ann@x.com,Pune\n2,Bob,bob@x.com,Delhi\n"
MAP = {"entities": [{"class": "Customer", "table": "t", "id": "id", "identity": [{"keys": ["email"], "normalize": "email"}],
                     "fields": {"name": "customerName", "email": "email", "city": "city"}}]}


class FabricApiTests(TestCase):
    def setUp(self):
        self.o = make_ontology()
        self.base = f"/api/v1/ontology/{self.o.id}/fabric"

    def call(self, method, url, data=None, role="admin", tenant=None):
        h = {"HTTP_X_PRIME_USER": "u", "HTTP_X_PRIME_ROLE": role}
        if tenant:
            h["HTTP_X_PRIME_TENANT"] = tenant
        return getattr(self.client, method)(url, data=json.dumps(data) if data is not None else None, content_type="application/json", **h)

    def create(self, **kw):
        r = self.call("post", f"{self.base}/sources/", {"name": "CRM", "kind": "file", "config": {"format": "csv"}, "mapping": MAP,
                                                        "credentials": {"api": "SUPERSECRET"}, **kw})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def set_csv(self, sid, text):
        FabricSource.objects.filter(pk=sid).update(config={"inline": {"format": "csv", "text": text}})

    def test_create_never_returns_credentials_and_validates(self):
        s = self.create()
        self.assertTrue(s["hasCredentials"])
        self.assertNotIn("SUPERSECRET", json.dumps(s))
        self.assertNotIn("SUPERSECRET", self.call("get", f"{self.base}/").content.decode())
        self.assertNotIn("SUPERSECRET", FabricSource.objects.get().secret_enc)
        self.assertEqual(self.call("post", f"{self.base}/sources/", {"name": "CRM", "kind": "file"}).status_code, 400)
        self.assertEqual(self.call("post", f"{self.base}/sources/", {"name": "Z", "kind": "nope"}).status_code, 400)
        bad = self.call("post", f"{self.base}/sources/", {"name": "Z", "kind": "file", "mapping": {"entities": [{"class": "Nope"}]}})
        self.assertEqual(bad.status_code, 400)

    def test_roles(self):
        self.assertEqual(self.call("post", f"{self.base}/sources/", {"name": "A", "kind": "file"}, role="editor").status_code, 403)
        sid = self.create()["id"]
        self.assertEqual(self.call("get", f"{self.base}/", role="viewer").status_code, 200)
        self.assertEqual(self.call("post", f"{self.base}/sources/{sid}/sync/", {}, role="viewer").status_code, 403)
        self.assertEqual(self.call("delete", f"{self.base}/sources/{sid}/", role="editor").status_code, 403)

    def test_tenant_isolation_and_global_alias(self):
        self.create()
        self.assertEqual(self.call("get", f"{self.base}/", tenant="other").status_code, 404)
        self.assertEqual(self.call("get", "/api/v1/fabric/sources/", tenant="other").json()["sources"], [])
        self.assertEqual(len(self.call("get", "/api/v1/fabric/sources/").json()["sources"]), 1)
        r = self.call("post", "/api/v1/fabric/sources/", {"ontologyId": self.o.id, "name": "B", "kind": "file"})
        self.assertEqual(r.status_code, 201)

    def test_sync_entities_lineage_sparql_and_export(self):
        sid = self.create()["id"]
        self.set_csv(sid, CSV)
        run = self.call("post", f"{self.base}/sources/{sid}/sync/", {"full": True}).json()
        self.assertEqual(run["status"], "succeeded", run)
        snap = self.call("get", f"{self.base}/").json()
        self.assertEqual(snap["entities"]["total"], 2)
        self.assertEqual(snap["freshness"], "FRESH")
        ents = self.call("get", f"{self.base}/entities/?class=Customer&q=ann").json()
        self.assertEqual(ents["total"], 1)
        e = self.call("get", f"{self.base}/entities/{ents['entities'][0]['id']}/").json()
        self.assertEqual(e["canonical"]["email"], "ann@x.com")
        self.assertEqual(e["provenance"]["email"]["source"], "CRM")
        self.assertTrue(any(n["type"] == "source" for n in e["provenanceGraph"]["nodes"]))
        self.assertTrue(self.call("get", f"{self.base}/entities/{e['id']}/lineage/").json()["events"])
        q = self.call("post", f"{self.base}/sparql/", {"query": "SELECT ?c WHERE { ?c a onto:Customer }"}).json()
        self.assertEqual(q["count"], 2)
        prov = ("SELECT ?s WHERE { ?c <http://www.w3.org/ns/prov#wasDerivedFrom> ?n . ?n <https://primeontology.io/fabric#source> ?s }")
        pq = self.call("post", f"{self.base}/sparql/", {"query": prov}).json()
        self.assertIn("CRM", [r[0] for r in pq["rows"]])
        self.assertEqual(self.call("post", f"{self.base}/sparql/", {"query": "INSERT DATA { <a:b> <a:c> <a:d> }"}).status_code, 400)
        self.assertIn("Ann Lee", self.call("get", f"{self.base}/export/").content.decode())
        self.set_csv(sid, CSV + "3,Cy,cy@x.com,Goa\n")  # a later sync invalidates the cached graph
        self.call("post", f"{self.base}/sync/", {"full": True})
        again = self.call("post", f"{self.base}/sparql/", {"query": "SELECT ?c WHERE { ?c a onto:Customer }"}).json()
        self.assertEqual(again["count"], 3)

    def test_drift_apply_creates_a_draft_ontology_change(self):
        sid = self.create()["id"]
        self.set_csv(sid, "id,name,email,city,loyalty_tier\n1,Ann,ann@x.com,Pune,gold\n2,Bob,bob@x.com,Delhi,silver\n")
        self.call("post", f"{self.base}/sources/{sid}/sync/", {"full": True})
        d = self.call("get", f"{self.base}/drift/").json()["drift"]
        self.assertEqual([x["field"] for x in d], ["loyalty_tier"])
        self.assertEqual(self.call("post", f"{self.base}/drift/{d[0]['id']}/", {"action": "apply"}, role="viewer").status_code, 403)
        r = self.call("post", f"{self.base}/drift/{d[0]['id']}/", {"action": "apply"}).json()
        self.assertEqual(r["property"], "loyaltyTier")
        self.o.refresh_from_db()
        self.assertTrue(any(p["name"] == "loyaltyTier" and p["domain"] == "Customer" for p in self.o.model["dataProperties"]))
        self.assertEqual(self.call("get", f"{self.base}/drift/").json()["drift"], [])

    @override_settings(**ALLOW)
    def test_connection_test_reports_failure_without_secrets(self):
        mock = M.MockEnterprise()
        self.addCleanup(mock.stop)
        cfg = {"base_url": mock.base + "/sap/opu/odata/sap/API_BUSINESS_PARTNER"}
        r = self.call("post", f"{self.base}/sources/", {"name": "SAP", "kind": "sap_odata", "config": cfg,
                                                        "credentials": {"user": M.SAP_USER, "password": "badbadbad"}})
        sid = r.json()["id"]
        t = self.call("post", f"{self.base}/sources/{sid}/test/", {}).json()
        self.assertFalse(t["ok"])
        self.assertNotIn("badbadbad", json.dumps(t))
        self.call("put", f"{self.base}/sources/{sid}/", {"credentials": {"user": M.SAP_USER, "password": M.SAP_PASS}})
        self.assertTrue(self.call("post", f"{self.base}/sources/{sid}/test/", {}).json()["ok"])
        self.assertEqual(crypto.decrypt_json(FabricSource.objects.get(pk=sid).secret_enc)["password"], M.SAP_PASS)
