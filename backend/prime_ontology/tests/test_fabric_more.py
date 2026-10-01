from datetime import datetime, timedelta, timezone

from django.test import TestCase

from prime_ontology.fabric import crypto, hooks, resolution
from prime_ontology.fabric.freshness import classify, source_freshness, worst
from prime_ontology.fabric.models import (FabricConstraint, FabricDrift, FabricEntity, FabricEvent, FabricRecord, FabricRef, FabricSuggestion)
from prime_ontology.fabric.sync import SyncBusy, sync_source

from .fabric_helpers import customer_spec, make_ontology, make_source, set_csv
from .test_fabric_core import entities


class SuggestionTests(TestCase):
    CSV = "id,name,email,phone,city\n1,Acme Corp,a@acme.com,,Pune\n2,ACME Corporation Ltd,b@acme.org,,Pune\n3,Acme Corp,c@other.com,,Delhi\n4,Zenith,z@z.com,,Pune\n"

    def setUp(self):
        self.o = make_ontology()
        self.src = make_source(self.o, "CRM", self.CSV, spec=customer_spec(fuzzy={"fields": ["customerName"], "threshold": 0.9, "corroborate": ["city"]}))
        sync_source(self.src)

    def test_similar_names_are_only_suggested_when_corroborated_and_never_auto_merged(self):
        self.assertEqual(len(entities(self.o)), 4)  # nothing merged automatically
        s = list(FabricSuggestion.objects.filter(ontology=self.o))
        self.assertEqual(len(s), 1)  # Acme Corp(Pune) ~ ACME Corporation Ltd(Pune); the Delhi one lacks corroboration
        self.assertTrue({s[0].entity_a.canonical["city"], s[0].entity_b.canonical["city"]} == {"Pune"})
        self.assertIn("also agree on city", " ".join(s[0].reasons))
        self.assertEqual(s[0].status, "pending")

    def test_accept_creates_a_must_link_and_merges(self):
        s = FabricSuggestion.objects.get(ontology=self.o)
        resolution.decide_suggestion(s, "accept", "reviewer1")
        self.assertEqual(len(entities(self.o)), 3)
        self.assertTrue(FabricConstraint.objects.filter(kind="must").exists())
        s.refresh_from_db()
        self.assertEqual((s.status, s.decided_by), ("accepted", "reviewer1"))
        sync_source(self.src, full=True)  # the human decision survives later resyncs
        self.assertEqual(len(entities(self.o)), 3)
        with self.assertRaises(ValueError):
            resolution.decide_suggestion(s, "reject", "x")  # cannot decide twice

    def test_reject_is_a_cannot_link_that_beats_identity_tokens(self):
        s = FabricSuggestion.objects.get(ontology=self.o)
        resolution.decide_suggestion(s, "reject", "reviewer1")
        # now the sources start sharing an e-mail: tokens say "same", the human said "different"
        set_csv(self.src, self.CSV.replace("b@acme.org", "a@acme.com"))
        r = sync_source(self.src)
        self.assertEqual(len(entities(self.o)), 4)
        self.assertGreaterEqual(r.stats.get("resolve_cannot_link_enforced", 0), 1)
        self.assertEqual(FabricSuggestion.objects.filter(ontology=self.o).count(), 1)  # not re-suggested


class ReferenceTests(TestCase):
    def test_references_resolve_lazily_even_when_the_target_arrives_later(self):
        o = make_ontology()
        inv = make_source(o, "Billing", "inv,cust,amt,od\nI1,C1,100,75\nI2,C1,50,10\nI3,C9,10,0\n",
                          spec={"class": "Invoice", "id": "inv", "fields": {"amt": "amount", "od": "overdueDays"},
                                "relations": [{"field": "cust", "target": "Customer", "property": "hasCustomer"}]})
        sync_source(inv)
        self.assertEqual(FabricRef.objects.filter(ontology=o, to_record__isnull=True).count(), 3)  # customers not synced yet
        crm = make_source(o, "CRM", "id,name,email,phone,city\nC1,Ann,ann@a.com,,P\n", spec=customer_spec())
        sync_source(crm)
        linked = FabricRef.objects.filter(ontology=o, to_record__isnull=False)
        self.assertEqual(linked.count(), 2)  # I1, I2 -> C1 ; I3 -> C9 stays dangling (unknown customer)
        ann = entities(o)[0]
        self.assertEqual({r.from_record.external_id for r in linked}, {"I1", "I2"})
        self.assertTrue(all(r.to_record.entity_id == ann.id for r in linked))

    def test_references_follow_entity_merges(self):
        o = make_ontology()
        make_source(o, "Billing", "inv,cust\nI1,C2\n", spec={"class": "Invoice", "id": "inv", "relations": [{"field": "cust", "target": "Customer", "property": "hasCustomer"}]})
        crm = make_source(o, "CRM", "id,name,email,phone,city\nC1,Ann,ann@a.com,,P\nC2,Ann L,ann@a.com,,P\n", spec=customer_spec())
        sync_source(o.fabric_sources.get(name="Billing"))
        sync_source(crm)
        ent = entities(o)
        self.assertEqual(len(ent), 1)  # C1 and C2 are the same person
        ref = FabricRef.objects.get(ontology=o)
        self.assertEqual(ref.to_record.entity_id, ent[0].id)  # the invoice points at the merged entity without being re-synced


class DriftTests(TestCase):
    def test_new_source_fields_become_ontology_change_proposals(self):
        o = make_ontology()
        src = make_source(o, "CRM", "id,name,email,phone,city,loyalty_tier,score\n1,Ann,a@a.com,,P,gold,42\n2,Bob,b@b.com,,P,silver,17\n",
                          spec=customer_spec())
        r = sync_source(src)
        self.assertEqual(r.stats["drift_found"], 2)
        d = {x.field: x for x in FabricDrift.objects.filter(ontology=o)}
        self.assertEqual(d["score"].inferred_type, "integer")
        self.assertEqual(d["loyalty_tier"].inferred_type, "string")
        self.assertEqual(d["score"].status, "pending")
        r2 = sync_source(src)
        self.assertEqual(r2.stats["drift_found"], 0)  # reported once, not on every sync
        self.assertEqual(FabricDrift.objects.count(), 2)


class FreshnessTests(TestCase):
    def test_classification(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(classify(None, 60, now), "NEVER_SYNCED")
        self.assertEqual(classify(now - timedelta(minutes=59), 60, now), "FRESH")
        self.assertEqual(classify(now - timedelta(minutes=61), 60, now), "STALE")
        self.assertEqual(classify(now - timedelta(minutes=241), 60, now), "VERY_STALE")
        self.assertEqual(worst(["FRESH", "VERY_STALE", "STALE"]), "VERY_STALE")
        self.assertEqual(worst([]), "NEVER_SYNCED")

    def test_source_becomes_fresh_after_sync_and_failure_keeps_old_freshness(self):
        o = make_ontology()
        src = make_source(o, "CRM", "id,name,email,phone,city\n1,A,a@a.com,,P\n", freshness_sla_minutes=60)
        self.assertEqual(source_freshness(src)["status"], "NEVER_SYNCED")
        sync_source(src)
        src.refresh_from_db()
        self.assertEqual(source_freshness(src)["status"], "FRESH")
        src.config = {}  # break the source
        src.save()
        r = sync_source(src)
        src.refresh_from_db()
        self.assertEqual(r.status, "failed")
        self.assertIn("No file", r.error)
        self.assertEqual(source_freshness(src)["status"], "FRESH")  # data from the last good sync is still there...
        self.assertEqual(src.last_status, "failed")  # ...but the failure is visible
        self.assertEqual(len(entities(o)), 1)  # and untouched


class SafetyTests(TestCase):
    def test_credentials_are_encrypted_roundtrip_tamper_proof_and_redacted(self):
        tok = crypto.encrypt_json({"password": "hunter2-very-secret", "user": "svc"})
        self.assertNotIn("hunter2", tok)
        self.assertEqual(crypto.decrypt_json(tok)["password"], "hunter2-very-secret")
        with self.assertRaises(crypto.SecretError):
            crypto.decrypt_json(tok[:-4] + "AAAA")
        self.assertEqual(crypto.redact("login failed for hunter2-very-secret!", {"password": "hunter2-very-secret"}), "login failed for ***!")
        self.assertEqual(crypto.encrypt_json({}), "")

    def test_a_second_concurrent_sync_is_refused(self):
        o = make_ontology()
        src = make_source(o, "CRM", "id,name,email,phone,city\n1,A,a@a.com,,P\n")
        src.running_since = datetime.now(timezone.utc)
        src.save()
        with self.assertRaises(SyncBusy):
            sync_source(src)
        src.running_since = datetime.now(timezone.utc) - timedelta(hours=2)  # a crashed worker's stale lock expires
        src.save()
        self.assertEqual(sync_source(src).status, "succeeded")

    def test_bad_rows_make_the_run_partial_not_failed(self):
        o = make_ontology()
        src = make_source(o, "CRM", "id,name,email,phone,city\n1,Good,g@g.com,,P\n,NoId,n@n.com,,P\n")
        r = sync_source(src)
        self.assertEqual((r.status, r.stats["skipped"], r.stats["new"]), ("partial", 1, 1))
        self.assertIn("missing id", r.error)

    def test_hooks_fire_and_a_failing_hook_cannot_break_a_sync(self):
        seen = []
        hooks.clear("entity_changed")
        hooks.register("entity_changed", lambda entity, changes, existed: seen.append((entity.class_name, len(changes), existed)))
        hooks.register("entity_changed", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
        try:
            o = make_ontology()
            src = make_source(o, "CRM", "id,name,email,phone,city\n1,A,a@a.com,,P\n")
            with self.assertLogs("prime_ontology.fabric", level="ERROR"):  # the failing hook is logged, not raised
                self.assertEqual(sync_source(src).status, "succeeded")
            self.assertEqual(seen[0][0], "Customer")
            self.assertFalse(seen[0][2])  # first appearance
            set_csv(src, "id,name,email,phone,city\n1,A,a@a.com,,Q\n")
            with self.assertLogs("prime_ontology.fabric", level="ERROR"):
                sync_source(src)
            self.assertTrue(seen[-1][2] and seen[-1][1] == 1)  # an update with one changed property
        finally:
            hooks.clear("entity_changed")
