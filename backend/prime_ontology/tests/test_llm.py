"""Exercises the LLM code paths against a local stand-in for the Anthropic Messages API.

This proves our side of the integration (request shape, auth header, response parsing, validation, anti-hallucination
checks, graceful fallback). It does NOT prove the real API/model behaves the same — that needs a real ANTHROPIC_API_KEY.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from django.test import TestCase

from prime_ontology import assistant, doc_ai, ingest, llm

from .helpers import sample
from .test_platform import demo_model

SEEN = []
REPLY = {"text": "{}", "status": 200}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append({"path": self.path, "headers": dict(self.headers), "body": body})
        out = json.dumps({"content": [{"type": "text", "text": REPLY["text"]}], "stop_reason": "end_turn"}).encode()
        self.send_response(REPLY["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class LlmTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.srv = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.env = {"ANTHROPIC_API_KEY": "test-key", "PRIME_ONTOLOGY_LLM_URL": f"http://127.0.0.1:{cls.srv.server_port}/v1/messages"}
        cls.old = {k: os.environ.get(k) for k in cls.env}
        os.environ.update(cls.env)

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        cls.srv.shutdown()
        super().tearDownClass()

    def setUp(self):
        SEEN.clear()
        REPLY.update(text="{}", status=200)

    def test_request_shape_and_auth(self):
        REPLY["text"] = "hello"
        self.assertEqual(llm.complete("sys", "user msg"), "hello")
        req = SEEN[0]
        req["headers"] = {k.lower(): v for k, v in req["headers"].items()}
        self.assertEqual(req["path"], "/v1/messages")
        self.assertEqual(req["headers"]["x-api-key"], "test-key")
        self.assertEqual(req["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual((req["body"]["system"], req["body"]["messages"][0]["role"]), ("sys", "user"))
        self.assertTrue(req["body"]["model"])

    def test_refuses_insecure_remote_url(self):
        os.environ["PRIME_ONTOLOGY_LLM_URL"] = "http://evil.example.com/v1/messages"
        try:
            with self.assertRaisesRegex(RuntimeError, "https"):
                llm.complete("s", "u")
        finally:
            os.environ["PRIME_ONTOLOGY_LLM_URL"] = self.env["PRIME_ONTOLOGY_LLM_URL"]

    def test_assistant_uses_llm_ops_and_drops_invalid_ones(self):
        REPLY["text"] = "Sure!\n" + json.dumps({"summary": "add warranty", "ops": [
            {"op": "add_class", "name": "Warranty", "parents": [], "comment": "x"},
            {"op": "add_relationship", "domain": "Product", "name": "hasWarranty", "range": "Warranty"},
            {"op": "add_class", "name": "bad name"}, {"op": "drop_database"}, {"op": "delete_class", "name": "Ghost"}]})
        p = assistant.propose(demo_model(), "add warranty to products")
        self.assertEqual(p["source"], "llm")
        self.assertEqual([o["op"] for o in p["ops"]], ["add_class", "add_relationship"])  # invalid/unknown ops never reach the user
        self.assertIn("Product", SEEN[0]["body"]["messages"][0]["content"])  # the ontology summary was sent as context

    def test_assistant_falls_back_to_rules_with_a_notice_on_llm_failure(self):
        REPLY["status"] = 500
        p = assistant.propose(demo_model(), "Add class Vehicle")
        self.assertEqual(p["source"], "rules")
        self.assertTrue(any("LLM unavailable" in n for n in p["notes"]))
        self.assertEqual(p["ops"][0]["name"], "Vehicle")
        REPLY.update(status=200, text="no json here")
        self.assertEqual(assistant.propose(demo_model(), "Add class Vehicle")["source"], "rules")

    def test_document_refinement_keeps_only_verifiable_quotes(self):
        text = sample("services_agreement.txt")
        REPLY["text"] = json.dumps({"classes": [
            {"name": "ServiceLevelAgreement", "parents": [], "comment": "uptime targets", "quote": "The Service Level Agreement defines uptime targets."},
            {"name": "Hallucinated", "parents": [], "comment": "invented", "quote": "This sentence is nowhere in the document at all."},
            {"name": "bad name", "quote": "Each party agrees to keep Confidential Information secret."}],
            "relationships": [{"domain": "Supplier", "name": "deliversTo", "range": "Customer", "quote": "The Supplier must deliver all deliverables within thirty (30) days of each order."},
                              {"domain": "Supplier", "name": "inventedLink", "range": "Customer", "quote": "never written anywhere"}]})
        res = ingest.analyze("agreement.txt", text, use_llm=True)
        names = {c["name"] for c in res["model"]["classes"]}
        self.assertIn("ServiceLevelAgreement", names)
        self.assertNotIn("Hallucinated", names)
        self.assertNotIn("bad name", names)
        sla = next(c for c in res["model"]["classes"] if c["name"] == "ServiceLevelAgreement")
        self.assertEqual(sla["source"]["origin"], "llm")
        self.assertLessEqual(sla["confidence"], 0.55)
        self.assertTrue(sla["evidence"][0]["section"])  # located in the real document
        rels = {p["name"] for p in res["model"]["objectProperties"]}
        self.assertIn("deliversTo", rels)
        self.assertNotIn("inventedLink", rels)
        self.assertTrue(any("discarded 3" in w for w in res["warnings"]), res["warnings"])

    def test_document_refinement_without_key_is_explained_not_silent(self):
        del os.environ["ANTHROPIC_API_KEY"]
        try:
            res = ingest.analyze("agreement.txt", sample("services_agreement.txt"), use_llm=True)
        finally:
            os.environ["ANTHROPIC_API_KEY"] = "test-key"
        self.assertTrue(any("no LLM is configured" in w for w in res["warnings"]))
        self.assertTrue(res["model"]["classes"])
