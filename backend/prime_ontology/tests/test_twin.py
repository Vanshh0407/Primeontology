import json
from datetime import datetime, timedelta, timezone

from django.test import TestCase

from prime_ontology.fabric.models import FabricEntity
from prime_ontology.fabric.sync import sync_source
from prime_ontology.twin import core, expr
from prime_ontology.twin.models import TwinAttributeHistory, TwinEvent, TwinRule

from .fabric_helpers import make_ontology, make_source, set_csv

CUST = "id,name,limit\nC1,Acme,100\nC2,Borealis,500\n"
INV = "id,cust,amount,days\nI1,C1,60,5\nI2,C1,30,70\nI3,C2,100,0\n"


def build():
    o = make_ontology()
    make_source(o, "CRM", CUST, spec={"class": "Customer", "id": "id", "fields": {"name": "customerName", "limit": "creditLimit"}, "identity": []})
    make_source(o, "ERP", INV, spec={"class": "Invoice", "id": "id", "fields": {"amount": "amount", "days": "overdueDays"}, "identity": [],
                                     "relations": [{"field": "cust", "target": "Customer", "property": "hasCustomer"}]})
    for s in o.fabric_sources.order_by("id"):
        assert sync_source(s).status == "succeeded"
    return o


def ent(o, cls, name_or_ext):
    return FabricEntity.objects.filter(ontology=o, class_name=cls, status="active").filter(
        **({"display_name": name_or_ext} if cls == "Customer" else {"records__external_id": name_or_ext})).first()


class ExprTests(TestCase):
    def ev(self, text, vals=None, rel=None):
        return expr.evaluate(expr.parse(text), vals or {}, lambda c: (rel or {}).get(c, []))

    def test_arithmetic_logic_and_null_propagation(self):
        self.assertEqual(self.ev("a - b * 2", {"a": 10, "b": 3}), 4)
        self.assertEqual(self.ev("a / b", {"a": 1, "b": 0}), None)
        self.assertIsNone(self.ev("a + 1", {}))
        self.assertIsNone(self.ev("a > 1", {}))
        self.assertFalse(self.ev("a > 1 and b > 1", {"a": 0, "b": None}))  # False and unknown = False
        self.assertIsNone(self.ev("a > 1 and b > 1", {"a": 2, "b": None}))
        self.assertTrue(self.ev("a > 1 or b > 1", {"a": 2, "b": None}))
        self.assertEqual(self.ev("coalesce(x, 7)"), 7)
        self.assertTrue(self.ev('status == "PAID"', {"status": "paid"}))
        self.assertEqual(self.ev("1 if a > 0 else 2", {"a": 3}), 1)

    def test_aggregates_over_related_entities(self):
        rel = {"Invoice": [{"amount": 10, "d": 5}, {"amount": "20", "d": None}, {"amount": None}]}
        self.assertEqual(self.ev("sum(Invoice.amount)", rel=rel), 30)
        self.assertEqual(self.ev("count(Invoice)", rel=rel), 3)
        self.assertEqual(self.ev("count(Invoice.amount)", rel=rel), 2)
        self.assertEqual(self.ev("max(Invoice.d)", rel=rel), 5)
        self.assertIsNone(self.ev("max(Invoice.nope)", rel=rel))
        self.assertEqual(self.ev("sum(Order.amount)", rel=rel), 0)

    def test_it_is_not_python_eval(self):
        for bad in ("__import__('os').system('x')", "a.b.c", "[1,2]", "lambda: 1", "open('f')", "a ** 9", "(a for a in b)", "x[0]", "f'{a}'", "a if b",
                    "sum(1)", "sum(Invoice.amount, 1)", "x := 1", "min(a=1)", "sum(Invoice.a.b)"):
            with self.assertRaises(expr.ExprError, msg=bad):
                expr.parse(bad)
        with self.assertRaises(expr.ExprError):
            expr.parse("1+" * 100 + "1")  # too complex
        with self.assertRaises(expr.ExprError):
            expr.parse("")


class TwinTests(TestCase):
    def setUp(self):
        core.register_hooks()  # other tests clear the global hook registry
        self.o = build()
        self.acme, self.bor = ent(self.o, "Customer", "Acme"), ent(self.o, "Customer", "Borealis")
        FabricEntity.objects.filter(ontology=self.o).update(created_at=datetime.now(timezone.utc) - timedelta(days=30))  # long-standing customers
        TwinAttributeHistory.objects.update(valid_from=datetime.now(timezone.utc) - timedelta(days=30))
        self.rule = lambda **k: TwinRule.objects.create(ontology=self.o, **k)  # noqa: E731
        self.rule(name="available", class_name="Customer", kind="derive", target="availableCredit", expression="creditLimit - sum(Invoice.amount)")
        self.rule(name="risk", class_name="Customer", kind="alert", expression="availableCredit < 20", message="Credit nearly exhausted", severity="critical")

    def snap(self, **kw):
        return {e["name"]: e for e in core.snapshot(self.o, **kw)["entities"]["items"]}

    def test_fabric_changes_feed_history_and_events(self):
        s = self.o.fabric_sources.get(name="CRM")
        core.ensure_history(self.o)
        set_csv(s, "id,name,limit\nC1,Acme,150\nC2,Borealis,500\n")
        sync_source(s, full=True)
        rows = TwinAttributeHistory.objects.filter(entity=self.acme, property="creditLimit").order_by("valid_from")
        self.assertEqual([r.value for r in rows], ["100", "150"])
        self.assertIsNotNone(rows[0].valid_to)
        self.assertIsNone(rows[1].valid_to)
        self.assertTrue(TwinEvent.objects.filter(entity=self.acme, event_type="entity.updated", payload__changes__0__new="150").exists())

    def test_snapshot_has_derived_values_and_alerts(self):
        sn = self.snap()
        self.assertEqual(sn["Acme"]["derived"]["availableCredit"], 10)  # 100 - (60 + 30)
        self.assertEqual(sn["Acme"]["alerts"], ["risk"])
        self.assertEqual(sn["Borealis"]["derived"]["availableCredit"], 400)
        self.assertEqual(sn["Borealis"]["alerts"], [])

    def test_the_documents_what_if_credit_limit_100_to_200_is_non_destructive(self):
        before = (TwinAttributeHistory.objects.count(), TwinEvent.objects.count(), dict(FabricEntity.objects.get(pk=self.acme.id).canonical))
        core.ensure_history(self.o)
        h0, e0 = TwinAttributeHistory.objects.count(), TwinEvent.objects.count()
        r = core.simulate(self.o, [{"entityId": self.acme.id, "property": "creditLimit", "value": 200}])
        self.assertFalse(r["persisted"])
        self.assertEqual(r["applied"][0]["before"], 100)
        self.assertEqual(r["applied"][0]["after"], 200)
        self.assertEqual([(d["name"], d["attribute"], d["before"], d["after"]) for d in r["derivedChanges"]], [("Acme", "availableCredit", 10, 110)])
        self.assertEqual([a["rule"] for a in r["alertsCleared"]], ["risk"])  # raising the limit clears the credit alert
        self.assertEqual(r["alertsTriggered"], [])
        self.assertEqual({i["class"] for i in r["impacted"]}, {"Customer", "Invoice"})  # the invoices are 1 hop away
        # nothing was written anywhere
        self.assertEqual((TwinAttributeHistory.objects.count(), TwinEvent.objects.count()), (h0, e0))
        self.assertEqual(FabricEntity.objects.get(pk=self.acme.id).canonical, before[2])
        self.assertEqual(self.snap()["Acme"]["derived"]["availableCredit"], 10)

    def test_simulation_ops_and_validation(self):
        r = core.simulate(self.o, [{"entityId": self.bor.id, "property": "creditLimit", "op": "multiply", "value": 0.1}])
        self.assertEqual(r["applied"][0]["after"], 50)
        self.assertEqual([a["rule"] for a in r["alertsTriggered"]], ["risk"])  # 50 - 100 < 20
        self.assertEqual(core.simulate(self.o, [{"entityId": self.bor.id, "property": "creditLimit", "value": 900}])["alertsTriggered"], [])
        for bad in ([], [{"entityId": 99999, "property": "creditLimit", "value": 1}], [{"entityId": self.acme.id, "property": "nope", "value": 1}],
                    [{"entityId": self.acme.id, "property": "customerName", "op": "add", "value": 1}],
                    [{"entityId": self.acme.id, "property": "creditLimit", "op": "pow", "value": 1}]):
            with self.assertRaises(core.TwinError):
                core.simulate(self.o, bad)

    def test_events_change_attributes_and_snapshots_can_travel_in_time(self):
        t1 = datetime.now(timezone.utc) - timedelta(days=10)
        t2 = datetime.now(timezone.utc) - timedelta(days=2)
        core.ensure_history(self.o)
        core.ingest_event(self.o, {"type": "credit.review", "entityId": self.acme.id, "occurredAt": t1.isoformat(), "set": {"creditLimit": 300}})
        core.ingest_event(self.o, {"type": "credit.review", "entityId": self.acme.id, "occurredAt": t2.isoformat(), "set": {"creditLimit": 120}})
        self.assertEqual(self.snap()["Acme"]["attributes"]["creditLimit"], 120)
        mid = datetime.now(timezone.utc) - timedelta(days=5)
        self.assertEqual(self.snap(as_of=mid)["Acme"]["attributes"]["creditLimit"], 300)
        self.assertEqual(self.snap(as_of=mid)["Acme"]["derived"]["availableCredit"], 210)  # the rules run on the past state too
        ancient = datetime.now(timezone.utc) - timedelta(days=400)
        self.assertNotIn("Acme", self.snap(as_of=ancient))  # did not exist yet
        # a late-arriving event is slotted into the right place
        t0 = datetime.now(timezone.utc) - timedelta(days=6)
        core.ingest_event(self.o, {"type": "correction", "entityId": self.acme.id, "occurredAt": t0.isoformat(), "set": {"creditLimit": 250}})
        self.assertEqual(self.snap(as_of=mid)["Acme"]["attributes"]["creditLimit"], 250)  # the correction rewrote history from day -6 on
        self.assertEqual(self.snap(as_of=datetime.now(timezone.utc) - timedelta(days=6, hours=1))["Acme"]["attributes"]["creditLimit"], 300)
        self.assertEqual(self.snap(as_of=datetime.now(timezone.utc) - timedelta(days=5, hours=23))["Acme"]["attributes"]["creditLimit"], 250)
        with self.assertRaises(core.TwinError):
            core.ingest_event(self.o, {"type": "x", "entityId": self.acme.id, "set": {"nope": 1}})
        with self.assertRaises(core.TwinError):
            core.ingest_event(self.o, {"type": "x", "occurredAt": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()})

    def test_process_state_machine(self):
        p = core.validate_process(self.o.model, {"name": "Invoice lifecycle", "entityClass": "Invoice", "states": ["draft", "sent", "paid"],
                                                 "transitions": [{"name": "send", "from": "draft", "to": "sent"}, {"name": "pay", "from": "sent", "to": "paid"}]})
        from prime_ontology.twin.models import TwinProcess

        TwinProcess.objects.create(ontology=self.o, **p)
        inv = ent(self.o, "Invoice", "I1")
        core.start_process(self.o, inv, "Invoice lifecycle")
        core.ingest_event(self.o, {"type": "invoice.sent", "entityId": inv.id, "process": "Invoice lifecycle", "transition": "send"})
        with self.assertRaisesRegex(core.TwinError, "not allowed from state 'sent'"):
            core.ingest_event(self.o, {"type": "again", "entityId": inv.id, "process": "Invoice lifecycle", "transition": "send"})
        out = core.simulate(self.o, [{"entityId": inv.id, "property": "amount", "value": 1}], process={"entityId": inv.id, "process": "Invoice lifecycle", "transition": "pay"})
        self.assertEqual((out["process"]["from"], out["process"]["to"]), ("sent", "paid"))
        self.assertEqual(core.snapshot(self.o)["processes"][0]["instances"], {"draft": 0, "sent": 1, "paid": 0})
        for bad in ({"name": "x", "entityClass": "Nope", "states": ["a", "b"], "transitions": [{"name": "t", "from": "a", "to": "b"}]},
                    {"name": "x", "entityClass": "Invoice", "states": ["a"], "transitions": []},
                    {"name": "x", "entityClass": "Invoice", "states": ["a", "b"], "transitions": [{"name": "t", "from": "a", "to": "zzz"}]}):
            with self.assertRaises(core.TwinError):
                core.validate_process(self.o.model, bad)

    def test_twin_native_entities_and_assets(self):
        e = core.create_twin_entity(self.o, "Customer", "Walk-in Co", {"creditLimit": 50})
        self.assertEqual(e.origin, "twin")
        self.assertEqual(self.snap()["Walk-in Co"]["derived"]["availableCredit"], 50)  # appears in the twin right away
        with self.assertRaises(core.TwinError):
            core.create_twin_entity(self.o, "Customer", "x", {"bogus": 1})

    def test_rule_validation(self):
        m = self.o.model
        ok = core.validate_rule(m, {"name": "n", "class": "Customer", "kind": "alert", "expression": "creditLimit > 1"})
        self.assertEqual(ok["severity"], "warning")
        for bad in ({"class": "Customer", "kind": "alert", "expression": "bogus > 1", "name": "n"}, {"class": "Customer", "kind": "alert", "expression": "sum(Nope.x)", "name": "n"},
                    {"class": "Customer", "kind": "alert", "expression": "sum(Invoice.nope)", "name": "n"}, {"class": "Customer", "kind": "derive", "expression": "1", "name": "n"},
                    {"class": "Customer", "kind": "alert", "expression": "__import__('x')", "name": "n"}):
            with self.assertRaises(core.TwinError, msg=bad["expression"]):
                core.validate_rule(m, bad)


class TwinApiTests(TestCase):
    def setUp(self):
        self.o = build()
        self.base = f"/api/v1/ontology/{self.o.id}/twin"
        self.acme = ent(self.o, "Customer", "Acme")

    def call(self, method, path, data=None, role="admin"):
        return getattr(self.client, method)(f"{self.base}{path}", data=json.dumps(data) if data is not None else None, content_type="application/json",
                                            HTTP_X_PRIME_USER="u", HTTP_X_PRIME_ROLE=role)

    def test_api_flow(self):
        r = self.call("post", "/rules/", {"name": "avail", "class": "Customer", "kind": "derive", "target": "availableCredit", "expression": "creditLimit - sum(Invoice.amount)"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.call("post", "/rules/", {"name": "avail", "class": "Customer", "kind": "derive", "target": "a", "expression": "1"}).status_code, 400)
        self.assertEqual(self.call("post", "/rules/", {"name": "z", "class": "Customer", "kind": "alert", "expression": "availableCredit < 5"}).status_code, 201)
        sn = self.call("get", "/").json()
        self.assertEqual(sn["entities"]["total"], 5)
        sim = self.call("post", "/simulate/", {"changes": [{"entityId": self.acme.id, "property": "creditLimit", "value": 200}]}).json()
        self.assertEqual(sim["summary"]["derivedChanged"], 1)
        self.assertFalse(sim["persisted"])
        self.assertEqual(self.call("post", "/simulate/", {"changes": []}).status_code, 400)
        ev = self.call("post", "/events/", {"type": "credit.review", "entityId": self.acme.id, "set": {"creditLimit": 175}})
        self.assertEqual(ev.status_code, 201)
        self.assertEqual(self.call("post", "/events/", {"type": "x"}, role="viewer").status_code, 403)
        h = self.call("get", f"/entities/{self.acme.id}/history/?property=creditLimit").json()
        self.assertEqual([x["value"] for x in h["history"]], ["100", 175])
        self.assertEqual(self.call("get", "/events/?type=credit.review").json()["events"][0]["payload"]["set"], {"creditLimit": 175})
        self.assertEqual(self.call("get", "/?asOf=garbage").status_code, 400)
        a = self.call("post", "/assets/", {"name": "Warehouse A", "type": "warehouse", "attributes": {"capacity": 100}}).json()
        self.assertEqual(self.call("post", "/assets/", {"name": "Dock 1", "parentId": a["id"], "entityId": self.acme.id}).status_code, 201)
        self.assertEqual(len(self.call("get", "/assets/").json()["assets"]), 2)
        p = self.call("post", "/processes/", {"name": "Lifecycle", "entityClass": "Invoice", "states": ["a", "b"], "transitions": [{"name": "go", "from": "a", "to": "b"}]})
        self.assertEqual(p.status_code, 201)
        self.assertEqual(self.call("post", "/", {"class": "Customer", "name": "N", "attributes": {"creditLimit": 5}}).status_code, 201)
        self.assertEqual(self.call("get", "/", role="viewer").status_code, 200)
        self.assertEqual(self.client.get(f"{self.base}/", HTTP_X_PRIME_USER="u", HTTP_X_PRIME_ROLE="admin", HTTP_X_PRIME_TENANT="other").status_code, 404)
