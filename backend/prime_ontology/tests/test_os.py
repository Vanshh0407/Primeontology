import json

from django.test import TestCase

from prime_ontology.autonomy.models import Agent
from prime_ontology.fabric.sync import sync_source
from prime_ontology.osplane.models import Policy

from .test_agents import CUST, INV, build, agents


class OsAndAgentApiTests(TestCase):
    def setUp(self):
        self.o = build()
        agents(self.o)
        self.b = f"/api/v1/ontology/{self.o.id}"

    def call(self, method, url, data=None, role="admin", user="alice", **h):
        return getattr(self.client, method)(url, data=json.dumps(data) if data is not None else None, content_type="application/json",
                                            HTTP_X_PRIME_USER=user, HTTP_X_PRIME_ROLE=role, **h)

    def test_manifest_capabilities_and_snapshot(self):
        m = self.client.get("/api/v1/os/manifest/").json()
        self.assertEqual({x["key"] for x in m["modules"]}, {"ontology", "fabric", "rag", "twin", "agents", "os"})
        c = self.call("get", "/api/v1/os/capabilities/", role="viewer").json()
        mods = {x["key"]: x for x in c["modules"]}
        self.assertTrue(mods["rag"]["allowed"]["read"])
        self.assertFalse(mods["agents"]["allowed"]["agent_manage"])
        s = self.call("get", "/api/v1/os/snapshot/").json()  # one ontology in the tenant -> ontologyId optional
        self.assertEqual({h["area"] for h in s["health"]}, {"ontology", "fabric", "rag", "twin", "agents"})
        self.assertEqual(s["fabric"]["entities"]["total"], 5)
        self.assertEqual(s["agents"]["registered"], 5)

    def test_policies_crud_evaluate_and_audit(self):
        self.assertEqual(self.call("post", "/api/v1/os/policies/", {"name": "P", "effect": "deny"}, role="editor").status_code, 403)
        r = self.call("post", "/api/v1/os/policies/", {"name": "No deletes", "effect": "deny", "operations": ["delete"], "concepts": ["Customer"]})
        self.assertEqual(r.status_code, 201, r.content)
        pid = r.json()["id"]
        self.assertEqual(self.call("post", "/api/v1/os/policies/", {"name": "No deletes", "effect": "deny"}).status_code, 400)
        ev = self.call("post", "/api/v1/os/evaluate/", {"operation": "delete", "concepts": ["Customer"], "role": "editor"}, role="editor").json()
        self.assertEqual((ev["decision"], ev["matched"][0]["policy"]), ("deny", "No deletes"))
        self.assertEqual(self.call("post", "/api/v1/os/evaluate/", {"operation": "read", "role": "admin"}, role="viewer").status_code, 403)
        self.assertEqual(self.call("put", f"/api/v1/os/policies/{pid}/", {"effect": "require_approval"}).json()["effect"], "require_approval")
        self.assertEqual(self.call("delete", f"/api/v1/os/policies/{pid}/").status_code, 200)
        acts = [e["action"] for e in self.call("get", "/api/v1/os/audit/?prefix=os.policy").json()["events"]]
        self.assertEqual(acts, ["os.policy.deleted", "os.policy.updated", "os.policy.created"])

    def test_host_registration_service_token_and_role_cap(self):
        self.assertEqual(self.call("post", "/api/v1/os/hosts/", {"key": "x", "name": "X"}, role="editor").status_code, 403)
        self.assertEqual(self.call("post", "/api/v1/os/hosts/", {"key": "x", "name": "X", "role": "admin"}).status_code, 400)
        r = self.call("post", "/api/v1/os/hosts/", {"key": "unicontractai", "name": "UniContractAI", "role": "viewer"})
        token = r.json()["token"]
        self.assertTrue(token.startswith("pst_"))
        self.assertNotIn(token, json.dumps(self.call("get", "/api/v1/os/hosts/").json()))  # never shown again
        # the host authenticates ONLY with its token (the forged role header is ignored) and acts as a viewer
        ok = self.client.get("/api/v1/os/snapshot/", HTTP_X_PRIME_SERVICE_TOKEN=token, HTTP_X_PRIME_ROLE="admin")
        self.assertEqual(ok.status_code, 200)
        denied = self.client.post("/api/v1/os/policies/", data=json.dumps({"name": "n", "effect": "deny"}), content_type="application/json",
                                  HTTP_X_PRIME_SERVICE_TOKEN=token, HTTP_X_PRIME_ROLE="admin")
        self.assertEqual(denied.status_code, 403)
        bad = self.client.get("/api/v1/os/snapshot/", HTTP_X_PRIME_SERVICE_TOKEN="pst_wrong")
        self.assertEqual(bad.status_code, 403)
        self.assertEqual([h["key"] for h in self.call("get", "/api/v1/os/hosts/").json()["hosts"]], ["unicontractai"])

    def test_os_ask_routes_to_the_right_module(self):
        r = self.call("post", "/api/v1/os/ask/", {"question": "What if Acme credit limit goes to 200?"}).json()
        self.assertEqual((r["module"], r["result"]["persisted"]), ("twin", False))
        r = self.call("post", "/api/v1/os/ask/", {"question": "Are my sources stale?"}).json()
        self.assertEqual(r["module"], "fabric")
        self.assertEqual(self.call("post", "/api/v1/os/ask/", {"question": "contracts with invoices overdue more than 60 days"}).json()["module"], "rag")
        self.assertEqual(self.call("post", "/api/v1/os/ask/", {"question": ""}).status_code, 400)

    def test_agent_api_registry_plan_run_approve_recover(self):
        base = f"{self.b}/agents"
        self.assertEqual(self.call("post", f"{base}/", {"name": "Z"}, role="editor").status_code, 403)
        for bad in ({"name": "Z", "role": "wizard"}, {"name": "Z", "permissions": ["fly"]}, {"name": "Z", "scope": ["Nope"]}, {"name": "Z", "tools": ["nope"]},
                    {"name": "Z", "capabilities": ["telepathy"]}, {"name": "Z", "budget": {"maxCost": -1}}, {"name": "Researcher"}):
            self.assertEqual(self.call("post", f"{base}/", bad).status_code, 400, bad)
        z = self.call("post", f"{base}/", {"name": "Zed", "role": "research", "capabilities": ["rag.ask"], "permissions": ["read"]})
        self.assertEqual(z.status_code, 201)
        self.assertEqual(self.call("put", f"{base}/{z.json()['id']}/", {"enabled": False}).json()["enabled"], False)
        self.assertEqual(self.call("delete", f"{base}/{z.json()['id']}/").status_code, 200)
        reg = self.call("get", f"{base}/").json()
        self.assertEqual(len(reg["agents"]), 5)
        self.assertIn("rag.ask", [t["name"] for t in reg["tools"]])
        plan = self.call("post", f"{base}/plan/", {"goal": "What do we know about Acme?"}).json()
        self.assertEqual([s["tool"] for s in plan["steps"]], ["fabric.entities", "rag.ask"])
        az = self.call("post", f"{base}/authorize/", {"agent": "Operator", "tool": "twin.event", "args": {"type": "x"}, "concepts": ["Customer"]}).json()
        self.assertEqual(az["decision"], "allow")
        az = self.call("post", f"{base}/authorize/", {"agent": "Researcher", "tool": "twin.event", "operation": "update"}).json()
        self.assertEqual(az["decision"], "deny")
        Policy.objects.create(ontology=self.o, name="Review updates", effect="require_approval", operations=["update"])
        run = self.call("post", f"{base}/execute/", {"goal": "Set Acme credit limit to 300"}, role="editor", user="alice").json()
        self.assertEqual(run["status"], "awaiting_approval")
        ap = self.call("get", f"{base}/approvals/?status=pending").json()["approvals"][0]
        self.assertEqual(self.call("post", f"{base}/approve/", {"approvalId": ap["id"], "decision": "approve"}, role="admin", user="alice").status_code, 403)  # four-eyes
        self.assertEqual(self.call("post", f"{base}/approve/", {"approvalId": ap["id"], "decision": "approve"}, role="editor", user="eve").status_code, 403)
        done = self.call("post", f"{base}/approve/", {"approvalId": ap["id"], "decision": "approve", "comment": "ok"}, role="reviewer", user="bob").json()
        self.assertEqual(done["run"]["status"], "succeeded")
        got = self.call("get", f"{base}/runs/{run['id']}/").json()
        self.assertEqual(got["steps"][0]["contract"]["approvedBy"], ["bob"])
        self.assertIn("approval_approve", [e["kind"] for e in got["trace"]])
        self.assertEqual(self.call("post", f"{base}/recover/", {"runId": run["id"]}).status_code, 400)  # nothing to recover
        self.assertEqual(self.call("get", f"{base}/runs/9999/").status_code, 404)
        self.assertEqual(self.call("get", f"{base}/runs/").json()["runs"][0]["id"], run["id"])

    def test_autonomous_alias_endpoints_and_tool_governance(self):
        man = self.call("get", "/api/v1/autonomous/manifest/").json()
        self.assertEqual(len(man["agents"]), 5)
        self.assertIn("guarantees", man)
        r = self.call("post", "/api/v1/autonomous/run/", {"goal": "What if Acme credit limit goes to 200?"}).json()
        self.assertEqual(r["status"], "succeeded")
        self.assertEqual(self.call("post", "/api/v1/autonomous/plan/", {"goal": "What do we know about Acme?"}).status_code, 200)
        self.assertEqual(len(self.call("get", "/api/v1/autonomous/agents/").json()["agents"]), 5)
        # tool governance: registering requires an allow-listed host; new tools are pending until an admin approves
        t = self.call("post", f"{self.b}/agents/tools/", {"name": "crm", "type": "api", "spec": {"url": "http://127.0.0.1:1/x"}})
        self.assertEqual(t.status_code, 400)  # connectors are disabled unless the host is allow-listed
        from prime_ontology.autonomy.models import AgentTool

        AgentTool.objects.create(ontology=self.o, name="crm", type="api", spec={"url": "http://x"})
        self.assertEqual(self.call("post", f"{self.b}/agents/tools/crm/govern/", {"action": "approve"}, role="editor").status_code, 403)
        self.assertEqual(self.call("post", f"{self.b}/agents/tools/crm/govern/", {"action": "approve"}).json()["status"], "approved")
