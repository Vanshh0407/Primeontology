import io
import json

from django.test import TestCase

from prime_ontology import mcp_server
from prime_ontology.models import Ontology

from .test_platform import demo_model


def rpc(id_, method, params=None):
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


class McpTests(TestCase):
    def setUp(self):
        m = demo_model()
        m["agentic"] = {"tools": [{"name": "crm", "type": "api", "concepts": ["Customer"], "operations": ["read"]}],
                        "policies": [{"name": "nodel", "effect": "deny", "concepts": ["Customer"], "operations": ["delete"]}], "rules": []}
        self.o = Ontology.objects.create(name="M", model=m, tenant="t1")
        self.other = Ontology.objects.create(name="Other", model=demo_model(), tenant="t2")

    def call(self, name, args, tenant=""):
        r = mcp_server.handle(rpc(1, "tools/call", {"name": name, "arguments": args}), tenant)
        return r["result"]["isError"], json.loads(r["result"]["content"][0]["text"]) if not r["result"]["isError"] else r["result"]["content"][0]["text"]

    def test_protocol(self):
        init = mcp_server.handle(rpc(1, "initialize"))["result"]
        self.assertEqual(init["serverInfo"]["name"], "prime-ontology")
        self.assertIsNone(mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        names = {t["name"] for t in mcp_server.handle(rpc(2, "tools/list"))["result"]["tools"]}
        self.assertEqual(names, {"list_ontologies", "search_concepts", "get_concept", "find_path", "plan_request", "get_context_pack", "run_sparql"})
        self.assertEqual(mcp_server.handle(rpc(3, "nope"))["error"]["code"], -32601)

    def test_tools(self):
        err, r = self.call("list_ontologies", {})
        self.assertFalse(err)
        self.assertEqual(len(r["ontologies"]), 2)
        err, r = self.call("search_concepts", {"ontology_id": self.o.id, "query": "supplier"})
        self.assertEqual(r["results"][0]["name"], "Supplier")
        err, r = self.call("find_path", {"ontology_id": self.o.id, "from": "Customer", "to": "Product"})
        self.assertTrue(r["connected"])
        err, r = self.call("plan_request", {"ontology_id": self.o.id, "request": "Delete the customer"})
        self.assertEqual(r["decision"], "blocked")
        err, r = self.call("run_sparql", {"ontology_id": self.o.id, "sparql": "ASK { ?s a owl:Class }"})
        self.assertTrue(r["rows"][0][0])
        err, r = self.call("get_context_pack", {"ontology_id": self.o.id, "request": "customer orders"})
        self.assertIn("SalesOrder", r["text"])

    def test_read_only_and_errors_and_tenant(self):
        err, msg = self.call("run_sparql", {"ontology_id": self.o.id, "sparql": "DELETE WHERE { ?s ?p ?o }"})
        self.assertTrue(err)
        self.assertIn("read-only", msg)
        self.assertTrue(self.call("get_concept", {"ontology_id": self.o.id, "name": "Ghost"})[0])
        self.assertTrue(self.call("nope", {"ontology_id": self.o.id})[0])
        self.assertTrue(self.call("search_concepts", {"query": "x"})[0])
        self.assertTrue(self.call("search_concepts", {"ontology_id": self.other.id, "query": "x"}, tenant="t1")[0])  # tenant isolation
        self.assertEqual(len(self.call("list_ontologies", {}, tenant="t1")[1]["ontologies"]), 1)

    def test_stdio_transport_survives_garbage(self):
        stdin = io.StringIO("not json\n" + json.dumps(rpc(1, "ping")) + "\n\n" + json.dumps(rpc(2, "tools/list")) + "\n")
        out = io.StringIO()
        mcp_server.serve("", stdin, out)
        lines = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual(lines[0]["error"]["code"], -32700)
        self.assertEqual(lines[1]["result"], {})
        self.assertEqual(len(lines), 3)
