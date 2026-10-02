import hashlib
import json
import os
import tempfile
import time
from unittest import mock
from xml.etree import ElementTree as ET

from django.test import TestCase, override_settings

from prime_ontology.autonomy import engine, workflow
from prime_ontology.autonomy.models import Agent
from prime_ontology.fabric import graph as fgraph
from prime_ontology.fabric.adapters import DataLakeAdapter
from prime_ontology.fabric.http import ConnectorError
from prime_ontology.fabric.models import FabricRecord, FabricSource
from prime_ontology.fabric.sync import sync_source
from prime_ontology.osplane.models import HostApp, Policy
from prime_ontology.twin import graphs as tgraphs

from .fabric_helpers import make_ontology
from .test_agents import agents, build


class DataLakeTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "sales"))
        with open(os.path.join(self.root, "sales", "a.csv"), "w") as f:
            f.write("id,name,email\n1,Ann Lee,ann@x.com\n2,Bob,bob@x.com\n")
        with open(os.path.join(self.root, "sales", "b.jsonl"), "w") as f:
            f.write('{"id": 3, "name": "Cy", "email": "cy@x.com"}\n\n{"id": 4, "name": "Di", "email": "di@x.com"}\n')
        import pyarrow as pa
        import pyarrow.parquet as pq

        pq.write_table(pa.table({"id": [5], "name": ["Eve"], "email": ["eve@x.com"]}), os.path.join(self.root, "sales", "c.parquet"))
        self.outside = tempfile.TemporaryDirectory()
        self.addCleanup(self.outside.cleanup)
        with open(os.path.join(self.outside.name, "secret.csv"), "w") as f:
            f.write("id,name,email\n99,Leak,leak@x.com\n")
        self.ov = override_settings(PRIME_ONTOLOGY_DATALAKE_ROOTS={"lake": self.root})
        self.ov.enable()
        self.addCleanup(self.ov.disable)

    def ad(self):
        return DataLakeAdapter({"root": "lake"}, {})

    def test_reads_csv_jsonl_and_parquet(self):
        rows = list(self.ad().fetch({"path": "sales/*"}))
        self.assertEqual(sorted(str(r["id"]) for r in rows), ["1", "2", "3", "4", "5"])
        self.assertEqual(self.ad().discover(), [{"name": "sales/*", "fields": [], "files": 3}])

    def test_it_cannot_leave_the_root_or_run_when_disabled(self):
        for bad in ("../x/*.csv", "/etc/passwd", "sales/../../x.csv"):
            with self.assertRaises(ConnectorError, msg=bad):
                list(self.ad().fetch({"path": bad}))
        try:
            os.symlink(self.outside.name, os.path.join(self.root, "link"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks need privileges on this OS")
        self.assertEqual(list(self.ad().fetch({"path": "link/*.csv"})), [])  # symlink escape is ignored
        with override_settings(PRIME_ONTOLOGY_DATALAKE_ROOTS={}):
            with self.assertRaisesRegex(ConnectorError, "disabled"):
                self.ad().test()
        with self.assertRaises(ConnectorError):
            DataLakeAdapter({"root": "unknown"}, {}).test()

    def test_incremental_skips_unchanged_files_and_sync_end_to_end(self):
        o = make_ontology()
        spec = {"class": "Customer", "path": "sales/*", "id": "id", "fields": {"name": "customerName", "email": "email"}, "updated_at_field": "id",
                "identity": [{"keys": ["email"], "normalize": "email"}]}
        s = FabricSource.objects.create(ontology=o, name="Lake", kind="datalake", config={"root": "lake"}, mapping={"entities": [spec]})
        r = sync_source(s, full=True)
        self.assertEqual((r.status, r.stats["new"]), ("succeeded", 5), r.error)
        self.assertEqual(FabricRecord.objects.filter(source=s).count(), 5)
        time.sleep(1.1)
        with open(os.path.join(self.root, "sales", "a.csv"), "a") as f:
            f.write("6,Fay,fay@x.com\n")
        s.refresh_from_db()
        s.sync_count = 1  # not a scheduled full reconcile
        s.save()
        r2 = sync_source(s)
        self.assertEqual(r2.status, "succeeded", r2.error)
        self.assertEqual(FabricRecord.objects.filter(source=s, deleted=False).count(), 6)


class PushTests(TestCase):
    def setUp(self):
        self.o = make_ontology()
        self.src = FabricSource.objects.create(ontology=self.o, name="Hooks", kind="push", mapping={"entities": [
            {"class": "Customer", "id": "id", "fields": {"name": "customerName", "email": "email"}, "identity": [{"keys": ["email"], "normalize": "email"}]}]})
        self.url = f"/api/v1/ontology/{self.o.id}/fabric/sources/{self.src.id}/push/"

    def push(self, data, role="editor"):
        return self.client.post(self.url, data=json.dumps(data), content_type="application/json", HTTP_X_PRIME_USER="u", HTTP_X_PRIME_ROLE=role)

    def test_push_ingests_and_is_append_only(self):
        r = self.push({"class": "Customer", "records": [{"id": "1", "name": "Ann", "email": "ann@x.com"}, {"id": "2", "name": "Bob", "email": "bob@x.com"}]})
        self.assertEqual((r.status_code, r.json()["queued"], r.json()["run"]["status"]), (202, 2, "succeeded"))
        r = self.push({"class": "Customer", "records": [{"id": "3", "name": "Cy", "email": "cy@x.com"}]})  # a second batch must NOT retire the first two
        self.assertEqual(FabricRecord.objects.filter(source=self.src, deleted=False).count(), 3)
        self.assertEqual(self.src.inbox.filter(processed=False).count(), 0)

    def test_deletes_validation_and_roles(self):
        self.push({"class": "Customer", "records": [{"id": "1", "name": "Ann", "email": "ann@x.com"}]})
        r = self.push({"class": "Customer", "records": [{"id": "1", "_deleted": True}], "sync": False})
        self.assertEqual(r.json()["deleted"], 1)
        self.assertTrue(FabricRecord.objects.get(source=self.src, external_id="1").deleted)
        for bad in ({"class": "Nope", "records": [{}]}, {"class": "Customer", "records": []}, {"class": "Customer", "records": ["x"]}, {"class": "Customer"}):
            self.assertEqual(self.push(bad).status_code, 400)
        self.assertEqual(self.push({"class": "Customer", "records": [{"id": "9"}]}, role="viewer").status_code, 403)
        file_src = FabricSource.objects.create(ontology=self.o, name="F", kind="file", mapping={})
        r = self.client.post(f"/api/v1/ontology/{self.o.id}/fabric/sources/{file_src.id}/push/", data=json.dumps({"class": "Customer", "records": [{}]}),
                             content_type="application/json", HTTP_X_PRIME_USER="u", HTTP_X_PRIME_ROLE="editor")
        self.assertEqual(r.status_code, 400)


class GraphViewAndExportTests(TestCase):
    def setUp(self):
        self.o = build()

    def get(self, path, role="admin"):
        return self.client.get(f"/api/v1/ontology/{self.o.id}{path}", HTTP_X_PRIME_USER="u", HTTP_X_PRIME_ROLE=role)

    def test_domain_graph_views(self):
        av = self.get("/twin/graph/").json()["views"]
        self.assertEqual((av["customer"], av["supplier"], av["financial"], av["contract"]), (2, 0, 3, 0))  # model has no Supplier/Contract entities here
        c = self.get("/twin/graph/?view=customer").json()
        self.assertEqual(c["classes"], ["Customer"])
        self.assertEqual(sum(1 for n in c["nodes"] if n["focus"]), 2)
        self.assertTrue(any(n["class"] == "Invoice" and not n["focus"] for n in c["nodes"]))  # 1-hop context
        self.assertTrue(c["edges"])
        self.assertEqual(self.get("/twin/graph/?view=nonsense").status_code, 400)
        self.assertEqual(self.get("/twin/graph/?view=process").json()["counts"]["nodes"], 0)
        from prime_ontology.twin.models import TwinAsset

        a = TwinAsset.objects.create(ontology=self.o, name="Plant", asset_type="plant")
        TwinAsset.objects.create(ontology=self.o, name="Line 1", asset_type="line", parent=a)
        self.assertEqual(self.get("/twin/graph/?view=asset").json()["counts"], {"nodes": 2, "edges": 1})

    def test_cypher_export_is_escaped_and_complete(self):
        from prime_ontology.fabric.models import FabricEntity

        e = FabricEntity.objects.filter(ontology=self.o, class_name="Customer").first()
        e.canonical = {**e.canonical, "customerName": 'Evil "quote" \\ \n}); DROP'}
        e.save()
        txt = self.get("/fabric/export/?format=cypher").content.decode()
        self.assertIn("MERGE (n:Customer {primeId:", txt)
        self.assertIn("MERGE (a)-[:HAS_CUSTOMER]->(b);", txt)
        self.assertIn('Evil \\"quote\\" \\\\ \\n}); DROP', txt)  # one literal; the injection attempt stays inside the string
        self.assertEqual(txt.count("\n"), txt.rstrip("\n").count("\n") + 1)
        self.assertEqual(self.get("/fabric/export/?format=xml").status_code, 400)

    def test_vector_export_respects_roles(self):
        from prime_ontology import embeddings
        from prime_ontology.rag.ingest import ingest_document

        with override_settings(PRIME_ONTOLOGY_EMBEDDINGS="hash"):
            embeddings.reset()
            ingest_document(self.o, title="Public", text="# A\nhello world about invoices", classification="public")
            ingest_document(self.o, title="Secret", text="# B\nboard hiring plans", classification="restricted")
            r = self.get("/rag/export/", role="viewer")
            rows = [json.loads(x) for x in r.content.decode().splitlines()]
            self.assertEqual([x["metadata"]["document"] for x in rows], ["Public"])
            self.assertTrue(len(rows[0]["vector"]) > 8)
            self.assertEqual(r["X-Embedding-Provider"], "hash")
            self.assertEqual(len(self.get("/rag/export/", role="admin").content.decode().splitlines()), 2)


class WorkflowAndAliasTests(TestCase):
    def setUp(self):
        self.o = build()
        self.a = agents(self.o)
        Policy.objects.create(ontology=self.o, name="Review", effect="require_approval", operations=["update"])

    def post(self, path, data):
        return self.client.post(f"/api/v1/ontology/{self.o.id}{path}", data=json.dumps(data), content_type="application/json", HTTP_X_PRIME_USER="alice", HTTP_X_PRIME_ROLE="editor")

    def test_n8n_and_bpmn_exports_are_well_formed(self):
        run = engine.create_run(self.o, "What if Acme credit limit goes to 300, then apply it", user="alice", role="editor", supervisor=self.a["chief"])
        run = engine.execute(run, backoff=0)
        n8n = self.post("/agents/workflow/", {"runId": run.id, "format": "n8n"}).json()
        names = [n["name"] for n in n8n["nodes"]]
        self.assertEqual(names, ["Start", "simulate", "apply"])
        self.assertEqual(n8n["connections"]["Start"]["main"][0][0]["node"], "simulate")
        self.assertEqual(n8n["connections"]["simulate"]["main"][0][0]["node"], "apply")
        self.assertIn("needs human approval", n8n["nodes"][2]["notes"])
        body = json.loads(n8n["nodes"][1]["parameters"]["jsonBody"])
        self.assertEqual(body["steps"][0]["tool"], "twin.simulate")
        x = self.post("/agents/workflow/", {"runId": run.id, "format": "bpmn"})
        root = ET.fromstring(x.content.decode())  # well-formed XML
        ns = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
        self.assertEqual({e.get("id") for e in root.iter(ns + "userTask")}, {"approve_apply"})  # the approval gate is a human task
        self.assertEqual({e.get("id") for e in root.iter(ns + "serviceTask")}, {"simulate", "apply"})
        flows = {(e.get("sourceRef"), e.get("targetRef")) for e in root.iter(ns + "sequenceFlow")}
        self.assertEqual(flows, {("start", "simulate"), ("simulate", "approve_apply"), ("approve_apply", "apply"), ("apply", "end")})
        planned = self.post("/agents/workflow/", {"goal": "What do we know about Acme?", "format": "n8n"}).json()
        self.assertEqual([n["name"] for n in planned["nodes"]], ["Start", "lookup", "research"])
        self.assertEqual(self.post("/agents/workflow/", {"goal": "x y", "format": "zip"}).status_code, 400)

    def test_per_ontology_autonomous_routes(self):
        base = f"/api/v1/ontology/{self.o.id}/autonomous"
        h = dict(HTTP_X_PRIME_USER="alice", HTTP_X_PRIME_ROLE="editor")
        man = self.client.get(f"{base}/manifest/", **h).json()
        self.assertEqual(len(man["agents"]), 5)
        self.assertEqual(len(self.client.get(f"{base}/agents/", **h).json()["agents"]), 5)
        plan = self.client.post(f"{base}/plan/", data=json.dumps({"goal": "What do we know about Acme?"}), content_type="application/json", **h).json()
        self.assertEqual(len(plan["steps"]), 2)
        run = self.client.post(f"{base}/run/", data=json.dumps({"goal": "What if Acme credit limit goes to 200?"}), content_type="application/json", **h).json()
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(self.client.post(f"{base}/recover/", data=json.dumps({"runId": run["id"]}), content_type="application/json", **h).status_code, 400)


class FakeN8n:
    """In-memory stand-in for n8n's public REST API (workflows + credentials)."""

    def __init__(self):
        self.workflows, self.credentials = [], []

    def __call__(self, method, path, payload=None):
        if method == "GET" and path.startswith("/workflows"):
            return {"data": self.workflows, "nextCursor": None}
        if method == "POST" and path == "/credentials":
            c = {"id": f"c{len(self.credentials) + 1}", **payload}
            self.credentials.append(c)
            return c
        if method == "POST" and path == "/workflows":
            w = {"id": f"w{len(self.workflows) + 1}", "active": False, "updatedAt": "2026-10-01T10:00:00Z", **payload}
            self.workflows.append(w)
            return w
        raise AssertionError(f"unexpected n8n call {method} {path}")


@override_settings(PRIME_N8N_API_KEY="k", PRIME_N8N_URL="http://n8n:5678", PRIME_WORKFLOW_BASE_URL="http://host.docker.internal:8008")
class N8nLinkTests(TestCase):
    def setUp(self):
        self.o = build()
        self.a = agents(self.o)
        self.fake = FakeN8n()
        patcher = mock.patch("prime_ontology.autonomy.n8n._call", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, method, data=None, role="admin"):
        url = f"/api/v1/ontology/{self.o.id}/agents/n8n/"
        h = dict(HTTP_X_PRIME_USER="alice", HTTP_X_PRIME_ROLE=role)
        return self.client.post(url, data=json.dumps(data), content_type="application/json", **h) if method == "POST" else self.client.get(url, **h)

    def test_push_creates_credential_once_and_lists_workflows(self):
        r = self.call("POST", {"goal": "What if Acme credit limit goes to 200?"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["url"], "http://n8n:5678/workflow/w1")
        node = self.fake.workflows[0]["nodes"][1]
        self.assertEqual(node["parameters"]["url"], f"http://host.docker.internal:8008/api/v1/ontology/{self.o.id}/agents/execute/")
        self.assertEqual(node["credentials"]["httpHeaderAuth"]["id"], "c1")
        headers = {h["name"]: h["value"] for h in node["parameters"]["headerParameters"]["parameters"]}
        self.assertEqual(headers, {"X-Prime-Client": "n8n"})  # the token lives only in the n8n credential
        token = self.fake.credentials[0]["data"]["value"]
        self.assertTrue(HostApp.objects.filter(ontology=self.o, key="n8n-link", token_hash=hashlib.sha256(token.encode()).hexdigest()).exists())
        self.assertEqual(self.call("POST", {"goal": "What do we know about Acme?"}).status_code, 201)
        self.assertEqual(len(self.fake.credentials), 1)  # reused, not re-issued
        listed = self.call("GET").json()
        self.assertTrue(listed["configured"])
        self.assertEqual([w["id"] for w in listed["workflows"]], ["w1", "w2"])
        self.assertEqual(listed["workflows"][0]["steps"], ["simulate"])
        # the issued token really authenticates as the host app
        run = self.client.post(node["parameters"]["url"].replace("http://host.docker.internal:8008", ""), data=node["parameters"]["jsonBody"],
                               content_type="application/json", HTTP_X_PRIME_CLIENT="n8n", HTTP_X_PRIME_SERVICE_TOKEN=token).json()
        self.assertEqual((run["status"], run["requestedBy"]), ("succeeded", "host:n8n-link"))

    def test_only_admins_push_and_unconfigured_is_reported(self):
        self.assertEqual(self.call("POST", {"goal": "What if Acme credit limit goes to 200?"}, role="editor").status_code, 403)
        self.assertEqual(self.call("GET", role="viewer").status_code, 200)
        with override_settings(PRIME_N8N_API_KEY=""):
            self.assertFalse(self.call("GET").json()["configured"])


class ConflictingEntityRegressionTests(TestCase):
    """Entities whose systems disagree carry a '__conflicts' list inside provenance: every consumer must cope (found in a real browser run)."""

    def test_conflicts_do_not_break_detail_sparql_export_or_rag(self):
        from .fabric_helpers import make_source

        o = make_ontology()
        make_source(o, "A", "id,name,email,city\n1,Ann Lee,ann@x.com,Pune\n", priority=90)
        make_source(o, "B", "id,name,email,city\n9,Ann Lee,ann@x.com,Mumbai\n", priority=10)
        for s in o.fabric_sources.order_by("id"):
            sync_source(s)
        h = dict(HTTP_X_PRIME_USER="u", HTTP_X_PRIME_ROLE="admin")
        base = f"/api/v1/ontology/{o.id}"
        from prime_ontology.fabric.models import FabricEntity

        e = FabricEntity.objects.get(ontology=o, class_name="Customer")
        self.assertTrue(e.provenance.get("__conflicts"))
        r = self.client.get(f"{base}/fabric/entities/{e.id}/", **h)
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertTrue(r.json()["conflicts"])
        q = self.client.post(f"{base}/fabric/sparql/", data=json.dumps({"query": "SELECT ?s WHERE { ?c a onto:Customer }"}), content_type="application/json", **h)
        self.assertEqual(q.status_code, 200)
        self.assertEqual(self.client.get(f"{base}/fabric/export/?format=cypher", **h).status_code, 200)
        a = self.client.post(f"{base}/rag/ask/", data=json.dumps({"question": "What do we know about Ann Lee?"}), content_type="application/json", **h)
        self.assertEqual(a.status_code, 200, a.content[:300])
