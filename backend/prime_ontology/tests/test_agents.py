import json
import os
import sys
from unittest import mock

from django.test import TestCase, override_settings

from prime_ontology.autonomy import engine, mcp_client, planner, tools
from prime_ontology.autonomy.models import Agent, AgentRun, AgentTool, ApprovalRequest
from prime_ontology.fabric.models import FabricEntity
from prime_ontology.osplane import policy as pol
from prime_ontology.osplane.models import Policy
from prime_ontology.twin.models import TwinEvent
from prime_ontology.twin import core as twin

from .fabric_helpers import make_ontology, make_source
from prime_ontology.fabric.sync import sync_source

FAKE_MCP = os.path.join(os.path.dirname(__file__), "fake_mcp.py")
CUST = "id,name,limit\nC1,Acme,100\nC2,Borealis,500\n"
INV = "id,cust,amount,days\nI1,C1,60,5\nI2,C1,30,70\nI3,C2,100,0\n"
NOW = dict(backoff=0)


def build():
    o = make_ontology()
    make_source(o, "CRM", CUST, spec={"class": "Customer", "id": "id", "fields": {"name": "customerName", "limit": "creditLimit"}, "identity": []})
    make_source(o, "ERP", INV, spec={"class": "Invoice", "id": "id", "fields": {"amount": "amount", "days": "overdueDays"}, "identity": [],
                                     "relations": [{"field": "cust", "target": "Customer", "property": "hasCustomer"}]})
    for s in o.fabric_sources.order_by("id"):
        assert sync_source(s).status == "succeeded"
    return o


def agents(o):
    mk = lambda **k: Agent.objects.create(ontology=o, **k)  # noqa: E731
    return {
        "chief": mk(name="Chief", role="supervisor", capabilities=["delegate"], permissions=["read"]),
        "researcher": mk(name="Researcher", role="research", capabilities=["rag.ask", "rag.retrieve", "ontology.search", "memory"], permissions=["read"]),
        "data": mk(name="DataAgent", role="data", capabilities=["fabric.query"], permissions=["read"]),
        "sim": mk(name="Simulator", role="simulation", capabilities=["twin.simulate", "twin.read"], permissions=["read"]),
        "ops": mk(name="Operator", role="action", capabilities=["twin.write", "fabric.sync"], permissions=["update", "execute"]),
    }


class Base(TestCase):
    def setUp(self):
        self.o = build()
        self.a = agents(self.o)
        self.acme = FabricEntity.objects.get(ontology=self.o, display_name="Acme")
        twin.ensure_history(self.o)

    def run_goal(self, goal, role="editor", user="alice", supervisor=None, **kw):
        run = engine.create_run(self.o, goal, user=user, role=role, supervisor=supervisor or self.a["chief"], **kw)
        return engine.execute(run, user=user, **NOW)

    def step(self, run, key):
        return run.steps.get(key=key)


class PlannerTests(Base):
    def test_question_is_decomposed_into_lookup_then_research_assigned_to_specialists(self):
        p = planner.plan(self.o, "What do we know about Acme?", self.a["chief"])
        self.assertEqual([(s["key"], s["tool"], s["agent"], s["dependsOn"]) for s in p["steps"]],
                         [("lookup", "fabric.entities", "DataAgent", []), ("research", "rag.ask", "Researcher", ["lookup"])])
        self.assertEqual(p["intent"], "question")

    def test_what_if_is_parsed_into_a_concrete_change(self):
        p = planner.plan(self.o, "What if Acme credit limit goes to 200?", self.a["chief"])
        s = p["steps"][0]
        self.assertEqual((s["tool"], s["agent"]), ("twin.simulate", "Simulator"))
        self.assertEqual(s["args"]["changes"], [{"entityId": self.acme.id, "property": "creditLimit", "op": "set", "value": 200.0}])
        pct = planner.plan(self.o, "simulate what happens if we increase Borealis credit limit by 10%", self.a["chief"])["steps"][0]["args"]["changes"][0]
        self.assertEqual((pct["op"], pct["value"]), ("multiply", 1.1))
        both = planner.plan(self.o, "What if Acme credit limit goes to 300, then apply it", self.a["chief"])
        self.assertEqual([(s["key"], s["agent"], s["operation"], s["dependsOn"]) for s in both["steps"]],
                         [("simulate", "Simulator", "read", []), ("apply", "Operator", "update", ["simulate"])])

    def test_unassignable_work_is_reported_not_silently_dropped(self):
        Agent.objects.filter(name="Simulator").update(enabled=False)
        p = planner.plan(self.o, "What if Acme credit limit goes to 200?", self.a["chief"])
        self.assertIsNone(p["steps"][0]["agent"])
        self.assertTrue(any("No enabled agent" in n for n in p["notes"]))
        with self.assertRaises(planner.ValueError if hasattr(planner, "ValueError") else ValueError):
            planner.plan(self.o, "  ")


class ExecutionTests(Base):
    def test_question_run_end_to_end_with_trace_cost_evaluation_and_memory(self):
        from prime_ontology.rag.ingest import ingest_document

        ingest_document(self.o, title="Acme terms", text="# Terms\nAcme is invoiced monthly and pays within 30 days.")
        run = self.run_goal("What do we know about Acme?")
        self.assertEqual(run.status, "succeeded", run.error)
        self.assertEqual([s.status for s in run.steps.all()], ["succeeded", "succeeded"])
        self.assertIn("Found 1 matching entity", run.result["summary"])
        self.assertGreater(run.cost, 0)
        self.assertEqual(run.tool_calls, 2)
        kinds = [e.kind for e in run.trace.all()]
        self.assertEqual(kinds[0], "planned")
        self.assertIn("step_succeeded", kinds)
        self.assertEqual(kinds[-1], "run_succeeded")
        self.assertEqual(run.evaluation["goalCompletion"], 1.0)
        self.assertEqual(run.evaluation["policyCompliance"], 1.0)
        self.assertTrue(self.a["researcher"].memories.filter(kind="episodic", run_id=run.id).exists())
        self.assertTrue(all(s.contract.get("argsHash") for s in run.steps.all()))

    def test_simulation_run_is_read_only(self):
        h0, e0 = twin.TwinAttributeHistory.objects.count(), TwinEvent.objects.count()
        run = self.run_goal("What if Acme credit limit goes to 200?")
        self.assertEqual(run.status, "succeeded", run.error)
        self.assertIn("Simulation (nothing was changed)", run.result["summary"])
        self.assertEqual((twin.TwinAttributeHistory.objects.count(), TwinEvent.objects.count()), (h0, e0))

    def test_changes_need_the_requesters_role(self):
        run = self.run_goal("Set Acme credit limit to 300", role="viewer")
        self.assertEqual(run.status, "failed")
        self.assertIn("cannot authorise changes", run.error)
        self.assertFalse(TwinEvent.objects.filter(event_type="agent.change").exists())

    def test_agent_without_the_capability_or_permission_is_stopped(self):
        run = engine.create_run(self.o, "x", user="a", role="admin", supervisor=self.a["chief"],
                                steps=[{"key": "s", "tool": "twin.event", "args": {"type": "t"}, "agent": "Researcher", "operation": "update"}])
        run = engine.execute(run, **NOW)
        self.assertEqual(run.steps.get().status, "blocked")
        self.assertIn("neither the tool", run.steps.get().error + str(run.steps.get().decision) + run.error)
        Agent.objects.filter(name="Operator").update(permissions=["read"])
        run2 = self.run_goal("Set Acme credit limit to 300", role="admin")
        self.assertIn("not permitted to 'update'", str(run2.steps.get().decision))
        Agent.objects.filter(name="Operator").update(permissions=["update"], scope=["Invoice"])
        run3 = self.run_goal("Set Acme credit limit to 300", role="admin")
        self.assertEqual(run3.steps.get().status, "blocked")  # the planner will not give Customer work to an agent scoped to Invoice
        self.assertIn("No agent", run3.steps.get().error)

    def test_policy_requires_approval_four_eyes_and_contract_binding(self):
        Policy.objects.create(ontology=self.o, name="Updates need review", effect="require_approval", operations=["update"], concepts=["Customer"], approvals_required=1)
        run = self.run_goal("Set Acme credit limit to 300", role="editor", user="alice")
        self.assertEqual(run.status, "awaiting_approval")
        st = self.step(run, "update")
        self.assertEqual(st.status, "awaiting_approval")
        self.assertFalse(TwinEvent.objects.filter(event_type="agent.change").exists())  # nothing happened yet
        ap = run.approvals.get()
        self.assertEqual(ap.required, 1)
        with self.assertRaisesRegex(engine.EngineError, "Four-eyes"):
            engine.decide(ap, user="alice", role="admin", decision="approve")
        with self.assertRaisesRegex(engine.EngineError, "Only reviewers"):
            engine.decide(ap, user="eve", role="editor", decision="approve")
        # the arguments are tampered with while waiting: the contract must refuse to run them
        engine.decide(ap, user="bob", role="reviewer", decision="approve", comment="ok")
        self.assertEqual(ap.status, "approved")
        st.refresh_from_db()
        st.args = {**st.args, "set": {"creditLimit": 99999}}
        st.save()
        run = engine.execute(run, **NOW)
        self.assertEqual(self.step(run, "update").status, "blocked")
        self.assertIn("execution contract", self.step(run, "update").error)
        self.assertFalse(TwinEvent.objects.filter(event_type="agent.change").exists())

    def test_approved_run_executes_and_records_who_approved(self):
        Policy.objects.create(ontology=self.o, name="Updates need review", effect="require_approval", operations=["update"])
        run = self.run_goal("Set Acme credit limit to 300", role="editor", user="alice")
        ap = run.approvals.get()
        engine.decide(ap, user="bob", role="reviewer", decision="approve")
        run = engine.execute(run, **NOW)
        self.assertEqual(run.status, "succeeded", run.error)
        self.assertEqual(self.step(run, "update").contract["approvedBy"], ["bob"])
        self.assertEqual(twin.values_at(self.o, [self.acme.id])[self.acme.id]["creditLimit"], 300)
        self.assertTrue(run.evaluation["approvalsRespected"])

    def test_rejection_stops_the_step_and_dependents(self):
        Policy.objects.create(ontology=self.o, name="Updates need review", effect="require_approval", operations=["update"])
        run = self.run_goal("What if Acme credit limit goes to 300, then apply it", role="editor", user="alice")
        self.assertEqual(run.status, "awaiting_approval")
        engine.decide(run.approvals.get(), user="bob", role="reviewer", decision="reject", comment="not now")
        run = engine.execute(run, **NOW)
        self.assertEqual((self.step(run, "simulate").status, self.step(run, "apply").status), ("succeeded", "rejected"))
        self.assertEqual(run.status, "partial")
        self.assertIn("Rejected by bob", run.error)

    def test_critical_risk_is_denied_by_default_and_deny_policies_win(self):
        AgentTool.objects.create(ontology=self.o, name="purge", type="api", operation="delete", spec={"url": "http://x"}, status="approved")
        Agent.objects.filter(name="Operator").update(tools=["purge"], permissions=["delete", "update"])
        run = engine.create_run(self.o, "purge", user="a", role="admin", supervisor=self.a["chief"], steps=[{"key": "p", "tool": "purge", "agent": "Operator", "operation": "delete"}])
        run = engine.execute(run, **NOW)
        self.assertEqual(run.steps.get().status, "blocked")
        self.assertTrue(run.steps.get().decision["defaultedByRisk"])
        Policy.objects.create(ontology=self.o, name="No agent changes to Customers", effect="deny", operations=["update"], concepts=["Customer"], actors={"actorTypes": ["agent"]})
        run = self.run_goal("Set Acme credit limit to 300", role="admin")
        self.assertEqual(run.status, "failed")
        self.assertIn("No agent changes to Customers", self.step(run, "update").error)

    def test_recovery_retry_after_the_cause_is_fixed(self):
        p = Policy.objects.create(ontology=self.o, name="Freeze", effect="deny", operations=["update"])
        run = self.run_goal("Set Acme credit limit to 300", role="admin")
        self.assertEqual(run.status, "failed")
        with self.assertRaises(engine.EngineError):
            engine.recover(run, "fly")
        p.enabled = False
        p.save()
        run = engine.recover(run, "retry", user="alice", **{}) if False else None
        run = AgentRun.objects.get(goal__startswith="Set Acme")
        with mock.patch.object(engine.time, "sleep"):
            run = engine.recover(run, "retry", user="alice")
        self.assertEqual(run.status, "succeeded", run.error)
        self.assertIn("recovery", [e.kind for e in run.trace.all()])
        with self.assertRaisesRegex(engine.EngineError, "already succeeded"):
            engine.recover(run, "retry")

    def test_skip_recovery_and_dependents(self):
        BAD = {"key": "boom", "tool": "ontology.search", "agent": "Researcher", "args": {"query": "x"}}
        orig = tools.BUILTIN["ontology.search"]["fn"]
        tools.BUILTIN["ontology.search"]["fn"] = lambda ctx, a: (_ for _ in ()).throw(tools.ToolError("permanent failure"))
        try:
            run = engine.create_run(self.o, "x", user="a", role="admin", supervisor=self.a["chief"],
                                    steps=[BAD, {"key": "after", "tool": "memory.recall", "agent": "Researcher", "args": {"query": "x"}, "dependsOn": ["boom"]},
                                           {"key": "indep", "tool": "fabric.entities", "agent": "DataAgent", "args": {}}])
            run = engine.execute(run, **NOW)
            self.assertEqual((self.step(run, "boom").status, self.step(run, "after").status, self.step(run, "indep").status), ("failed", "skipped", "succeeded"))
            self.assertEqual(self.step(run, "boom").attempts, 1)  # a non-retryable error is not retried
            self.assertEqual(run.status, "partial")
            run = engine.recover(run, "skip", user="alice")
            self.assertEqual(run.status, "partial")
            self.assertEqual(self.step(run, "boom").status, "skipped")
        finally:
            tools.BUILTIN["ontology.search"]["fn"] = orig

    def test_retryable_errors_are_retried_with_a_limit(self):
        calls = []

        def flaky(ctx, a):
            calls.append(1)
            if len(calls) < 3:
                raise tools.ToolError("temporarily unavailable", retryable=True)
            return {"hits": []}

        orig = tools.BUILTIN["ontology.search"]["fn"]
        tools.BUILTIN["ontology.search"]["fn"] = flaky
        try:
            run = engine.create_run(self.o, "x", user="a", role="admin", supervisor=self.a["chief"], steps=[{"key": "s", "tool": "ontology.search", "agent": "Researcher", "args": {"query": "x"}}])
            run = engine.execute(run, **NOW)
            self.assertEqual((run.status, self.step(run, "s").attempts), ("succeeded", 3))
            calls.clear()
            tools.BUILTIN["ontology.search"]["fn"] = lambda ctx, a: (_ for _ in ()).throw(tools.ToolError("down", retryable=True))
            run2 = engine.create_run(self.o, "x", user="a", role="admin", supervisor=self.a["chief"], steps=[{"key": "s", "tool": "ontology.search", "agent": "Researcher", "args": {"query": "x"}}])
            run2 = engine.execute(run2, **NOW)
            self.assertEqual((run2.status, self.step(run2, "s").attempts), ("failed", engine.MAX_ATTEMPTS))
        finally:
            tools.BUILTIN["ontology.search"]["fn"] = orig

    def test_budget_caps_tool_calls(self):
        Agent.objects.filter(name="Chief").update(budget={"maxToolCalls": 1})
        run = self.run_goal("What do we know about Acme?", supervisor=Agent.objects.get(name="Chief"))
        self.assertEqual([s.status for s in run.steps.all()], ["succeeded", "blocked"])
        self.assertIn("Budget exhausted", self.step(run, "research").error)
        self.assertEqual(run.status, "partial")

    def test_agent_to_agent_delegation_cannot_escalate_permissions(self):
        Agent.objects.filter(name="Chief").update(tools=["agent.delegate"])
        run = engine.create_run(self.o, "x", user="alice", role="admin", supervisor=self.a["chief"], steps=[
            {"key": "d", "tool": "agent.delegate", "agent": "Chief", "args": {"agent": "Researcher", "goal": "What are the payment terms?"}}])
        run = engine.execute(run, **NOW)
        self.assertEqual(run.status, "succeeded", run.error)
        child = AgentRun.objects.get(parent=run)
        self.assertEqual((child.depth, child.status, child.supervisor.name), (1, "succeeded", "Researcher"))
        self.assertEqual(self.step(run, "d").output["runId"], child.id)
        self.assertIn("delegated", [e.kind for e in run.trace.all()])
        # the operator may update, but a read-only delegator cannot hand that power on
        Agent.objects.filter(name="Chief").update(tools=["agent.delegate"], permissions=["read"])
        run2 = engine.create_run(self.o, "x", user="alice", role="admin", supervisor=self.a["chief"], steps=[
            {"key": "d", "tool": "agent.delegate", "agent": "Chief", "args": {"agent": "Operator", "goal": "Set Acme credit limit to 300"}}])
        run2 = engine.execute(run2, **NOW)
        self.assertEqual(run2.status, "failed")
        child2 = AgentRun.objects.get(parent=run2)
        self.assertIn("not permitted to 'update'", str(child2.steps.get().decision))

    def test_cancel(self):
        Policy.objects.create(ontology=self.o, name="Updates need review", effect="require_approval", operations=["update"])
        run = self.run_goal("Set Acme credit limit to 300", role="editor")
        engine.cancel(run, "alice")
        self.assertEqual(AgentRun.objects.get(pk=run.pk).status, "cancelled")
        self.assertEqual(ApprovalRequest.objects.get(run=run).status, "rejected")

    def test_explicit_plans_are_validated(self):
        for bad in ([], [{"key": "a"}], [{"key": "a", "tool": "nope"}], [{"key": "a", "tool": "rag.ask", "dependsOn": ["b"]}],
                    [{"key": "a", "tool": "rag.ask"}, {"key": "a", "tool": "rag.ask"}], [{"key": "a", "tool": "rag.ask", "concepts": ["Nope"]}]):
            with self.assertRaises(engine.EngineError):
                engine.create_run(self.o, "g", user="a", role="admin", supervisor=self.a["chief"], steps=bad)

    def test_placeholders_pass_data_between_steps(self):
        run = engine.create_run(self.o, "x", user="a", role="admin", supervisor=self.a["chief"], steps=[
            {"key": "find", "tool": "fabric.entities", "agent": "DataAgent", "args": {"q": "Acme"}},
            {"key": "sim", "tool": "twin.simulate", "agent": "Simulator", "dependsOn": ["find"],
             "args": {"changes": [{"entityId": "$find.entities.0.id", "property": "creditLimit", "value": 250}]}}])
        run = engine.execute(run, **NOW)
        self.assertEqual(run.status, "succeeded", run.error)
        self.assertEqual(self.step(run, "sim").args["changes"][0]["entityId"], self.acme.id)


class PolicyEngineTests(Base):
    def test_risk_levels_and_default_decisions(self):
        self.assertEqual(pol.risk("read")["level"], "low")
        self.assertEqual(pol.risk("update")["level"], "medium")
        self.assertEqual(pol.risk("update", external=True, records=500, classification="restricted")["level"], "critical")
        self.assertEqual(pol.evaluate(self.o, operation="read")["decision"], "allow")
        self.assertEqual(pol.evaluate(self.o, operation="execute", external=True, records=20)["decision"], "require_approval")
        d = pol.evaluate(self.o, operation="delete")
        self.assertEqual((d["decision"], d["defaultedByRisk"]), ("deny", True))

    def test_deny_beats_require_approval_beats_allow_and_subclasses_are_covered(self):
        Policy.objects.create(ontology=self.o, name="a", effect="allow", operations=["update"])
        Policy.objects.create(ontology=self.o, name="b", effect="require_approval", operations=["update"], concepts=["Customer"], approvals_required=2)
        r = pol.evaluate(self.o, operation="update", concepts=["Customer"])
        self.assertEqual((r["decision"], r["approvalsRequired"]), ("require_approval", 2))
        Policy.objects.create(ontology=self.o, name="c", effect="deny", operations=["update"], actors={"roles": ["viewer"]})
        self.assertEqual(pol.evaluate(self.o, operation="update", concepts=["Customer"], role="viewer")["decision"], "deny")
        self.assertEqual(pol.evaluate(self.o, operation="update", concepts=["Customer"], role="editor")["decision"], "require_approval")
        self.assertEqual(pol.evaluate(self.o, operation="update", concepts=["Invoice"], role="editor")["decision"], "allow")  # b does not cover Invoice, a does

    def test_validation(self):
        for bad in ({"name": "", "effect": "allow"}, {"name": "x", "effect": "maybe"}, {"name": "x", "effect": "allow", "concepts": ["Nope"]},
                    {"name": "x", "effect": "allow", "riskAtLeast": "huge"}, {"name": "x", "effect": "deny", "approvalsRequired": 9}):
            with self.assertRaises(pol.PolicyError):
                pol.validate(self.o.model, bad)

    def test_r10_ontology_level_policies_are_still_honoured(self):
        m = dict(self.o.model)
        m["agentic"] = {"tools": [], "rules": [], "policies": [{"name": "legacy", "effect": "deny", "concepts": ["Invoice"], "operations": ["update"]}]}
        self.o.model = m
        self.o.save()
        self.assertEqual(pol.evaluate(self.o, operation="update", concepts=["Invoice"])["decision"], "deny")


class McpAndToolTests(Base):
    @override_settings(PRIME_ONTOLOGY_MCP_COMMANDS=[[sys.executable, FAKE_MCP]])
    def test_discovery_governance_and_calls(self):
        argv = mcp_client.resolve_command([sys.executable, FAKE_MCP])
        found = mcp_client.discover(argv)
        self.assertEqual(found["serverInfo"]["name"], "fake")
        self.assertEqual([t["name"] for t in found["tools"]], ["echo", "explode"])
        self.assertEqual(mcp_client.call(argv, "echo", {"text": "hi"}), {"echo": "hi"})
        with self.assertRaisesRegex(mcp_client.McpError, "boom"):
            mcp_client.call(argv, "explode", {})
        from prime_ontology.autonomy.models import McpServer

        McpServer.objects.create(ontology=self.o, name="fake", command=[sys.executable, FAKE_MCP])
        AgentTool.objects.create(ontology=self.o, name="fake.echo", type="mcp", spec={"server": "fake", "tool": "echo"}, status="pending",
                                 input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]})
        with self.assertRaisesRegex(tools.ToolError, "must be approved"):
            tools.call(self.o, "fake.echo", {"text": "x"}, {})
        AgentTool.objects.filter(name="fake.echo").update(status="approved")
        self.assertEqual(tools.call(self.o, "fake.echo", {"text": "x"}, {}), {"echo": "x"})
        with self.assertRaisesRegex(tools.ToolError, "Missing required"):
            tools.call(self.o, "fake.echo", {}, {})
        with self.assertRaisesRegex(tools.ToolError, "must be of type"):
            tools.call(self.o, "fake.echo", {"text": 5}, {})

    def test_only_allow_listed_commands_can_be_spawned(self):
        for cmd in (["cmd", "/c", "calc"], ["python", "-c", "print(1)"], "rm -rf /", [], None):
            with self.assertRaises(mcp_client.McpError):
                mcp_client.resolve_command(cmd)
        argv = mcp_client.resolve_command("builtin", "tenant-1")
        self.assertEqual(argv[-2:], ["--tenant", "tenant-1"])
        self.assertEqual(argv[2], "mcp_server")

    def test_mcp_tool_end_to_end_through_an_agent_run(self):
        from prime_ontology.autonomy.models import McpServer

        with override_settings(PRIME_ONTOLOGY_MCP_COMMANDS=[[sys.executable, FAKE_MCP]]):
            McpServer.objects.create(ontology=self.o, name="fake", command=[sys.executable, FAKE_MCP])
            AgentTool.objects.create(ontology=self.o, name="fake.echo", type="mcp", spec={"server": "fake", "tool": "echo"}, status="approved", operation="read",
                                     input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]})
            Agent.objects.filter(name="Researcher").update(tools=["fake.echo"])
            run = engine.create_run(self.o, "x", user="a", role="viewer", supervisor=self.a["chief"], steps=[
                {"key": "e", "tool": "fake.echo", "agent": "Researcher", "args": {"text": "ping"}}])
            run = engine.execute(run, **NOW)
            self.assertEqual((run.status, self.step(run, "e").output), ("succeeded", {"echo": "ping"}))
