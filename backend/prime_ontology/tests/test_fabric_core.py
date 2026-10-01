from datetime import datetime, timedelta, timezone

from django.test import TestCase

from prime_ontology.fabric import crypto, hooks, resolution
from prime_ontology.fabric.freshness import classify, worst
from prime_ontology.fabric.mapping_spec import MappingError, transform, validate_mapping
from prime_ontology.fabric.models import (FabricConstraint, FabricDrift, FabricEntity, FabricEvent, FabricRecord, FabricRef, FabricSuggestion)
from prime_ontology.fabric.normalize import n_company, n_email, n_phone, normalize
from prime_ontology.fabric.sync import SyncBusy, sync_source

from .fabric_helpers import MODEL, customer_spec, make_ontology, make_source, set_csv

CRM = "id,name,email,phone,city\nC1001,Ann Lee,Ann@Example.com,+1 (555) 010-2000,Pune\nC1002,Bob Ray,bob@x.com,555-010-3000,Mumbai\n"
ERP = "cust_no,full_name,mail,tel,town\n77,Ann M. Lee,ann@example.com,0015550102000,Pune\n88,Carol Stone,carol@y.com,,Delhi\n"
ERP_SPEC = lambda **kw: customer_spec("cust_no", {"full_name": "customerName", "mail": "email", "tel": "phone", "town": "city"}, **kw)  # noqa: E731


def entities(o, cls="Customer", status="active"):
    return list(FabricEntity.objects.filter(ontology=o, class_name=cls, status=status).order_by("id"))


class NormalizeTests(TestCase):
    def test_normalisers(self):
        self.assertEqual(n_email(" Ann@Example.COM "), "ann@example.com")
        self.assertEqual(n_email("not-an-email"), "")
        self.assertEqual(n_phone("+1 (555) 010-2000"), n_phone("0015550102000"))
        self.assertEqual(n_phone("12"), "")  # too short to be an identity key
        self.assertEqual(n_company("ACME Corporation Ltd."), n_company("Acme, Inc"))
        self.assertEqual(normalize("identifier", "ab-12 c"), "AB12C")
        self.assertEqual(normalize("digits", "GB 123-45"), "12345")


class MappingTests(TestCase):
    def test_validation_reports_every_problem(self):
        with self.assertRaises(MappingError) as cm:
            validate_mapping({"entities": [{"class": "Ghost", "id": "x"}, {"class": "Customer", "fields": {"a": "bad name"}, "identity": [{"keys": []}],
                                            "relations": [{"field": "f", "target": "Nope", "property": "p"}], "survivorship": {"email": "nonsense"}}]}, "file", MODEL)
        msg = str(cm.exception)
        for part in ("Ghost", "bad name", "identity[0]", "Nope", "nonsense"):
            self.assertIn(part, msg)
        clean, warns = validate_mapping({"entities": [customer_spec(fields={"n": "unknownProp"})]}, "file", MODEL)
        self.assertTrue(any("unknownProp" in w for w in warns))

    def test_transform_ids_refs_tokens_hash(self):
        spec = {"class": "Invoice", "id": ["co", "no"], "fields": {"amt": "amount"}, "relations": [{"field": "cust", "target": "Customer", "property": "hasCustomer"}],
                "identity": [{"keys": ["amount", "co"], "normalize": "none"}], "updated_at_field": "chg"}
        t = transform(spec, {"co": "1000", "no": "9", "amt": "12.5", "cust": ["C1", "Ann"], "chg": "2026-01-02T03:04:05Z", "extra": "x"})
        self.assertEqual((t.external_id, t.data, t.refs), ("1000|9", {"amount": "12.5"}, [("hasCustomer", "Customer", "C1", "")]))  # Odoo many2one -> id
        self.assertEqual(t.updated_at, datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))
        self.assertEqual(t.unmapped, {"extra": "x"})
        self.assertEqual(t.tokens, ["amount+co:12.5|1000"])
        same = transform(spec, {"co": "1000", "no": "9", "amt": "12.5", "cust": "C1", "chg": "x"})
        self.assertEqual(t.content_hash, same.content_hash)  # hash ignores presentation of the ref
        from prime_ontology.fabric.mapping_spec import SkipRecord

        with self.assertRaises(SkipRecord):
            transform(spec, {"co": "1000"})


class ResolutionTests(TestCase):
    def setUp(self):
        self.o = make_ontology()
        self.crm = make_source(self.o, "CRM", CRM, priority=60)
        self.erp = make_source(self.o, "ERP", ERP, spec=ERP_SPEC(), priority=40)

    def sync_all(self, full=False):
        return [sync_source(s, full=full) for s in (self.crm, self.erp)]

    def test_records_from_two_systems_become_one_entity_with_provenance(self):
        runs = self.sync_all()
        self.assertEqual([r.status for r in runs], ["succeeded", "succeeded"])
        ents = entities(self.o)
        self.assertEqual(len(ents), 3)  # Ann (CRM+ERP), Bob, Carol
        ann = next(e for e in ents if e.canonical.get("email") == "ann@example.com" or "ann" in e.display_name.lower())
        self.assertEqual(ann.record_count, 2)
        self.assertEqual(ann.canonical["customerName"], "Ann Lee")  # CRM has the higher priority
        self.assertEqual(ann.provenance["customerName"]["source"], "CRM")
        self.assertEqual(ann.provenance["customerName"]["externalId"], "C1001")
        conflicts = ann.provenance["__conflicts"]
        self.assertTrue(any(c["property"] == "customerName" and len(c["values"]) == 2 for c in conflicts))  # the disagreement is kept visible
        recs = FabricRecord.objects.filter(entity=ann)
        self.assertEqual({r.source.name for r in recs}, {"CRM", "ERP"})
        self.assertTrue(all(r.link_reason["rule"] == "deterministic" and r.link_reason["tokens"] for r in recs))

    def test_matching_is_transitive_and_cross_key(self):
        set_csv(self.crm, "id,name,email,phone,city\nA,Dan,dan@a.com,111-222-3333,X\nB,Dan K,dan@b.com,111-222-3333,X\nC,Daniel K,dan@b.com,,X\n")
        sync_source(self.crm)
        ents = entities(self.o)
        self.assertEqual(len(ents), 1)  # A~B by phone, B~C by email
        self.assertEqual(ents[0].record_count, 3)

    def test_second_sync_is_incremental_noop_and_idempotent(self):
        self.sync_all()
        n_ent, n_rec, n_ev = FabricEntity.objects.count(), FabricRecord.objects.count(), FabricEvent.objects.count()
        r = sync_source(self.crm)
        self.assertEqual((r.stats["unchanged"], r.stats.get("new", 0), r.stats.get("changed", 0)), (2, 0, 0))
        self.assertNotIn("resolve_entities_refreshed", r.stats)  # nothing changed -> no resolution work at all
        self.assertEqual((FabricEntity.objects.count(), FabricRecord.objects.count()), (n_ent, n_rec))
        self.assertEqual(FabricEvent.objects.count() - n_ev, 2)  # only sync_started / sync_finished

    def test_change_detection_updates_golden_record_and_logs_lineage(self):
        self.sync_all()
        set_csv(self.crm, CRM.replace("Pune", "Hyderabad"))
        r = sync_source(self.crm)
        self.assertEqual((r.stats["changed"], r.stats["unchanged"]), (1, 1))
        ann = FabricEntity.objects.get(ontology=self.o, canonical__email="ann@example.com") if False else next(e for e in entities(self.o) if e.record_count == 2)
        self.assertEqual(ann.canonical["city"], "Hyderabad")
        ev = FabricEvent.objects.filter(kind="record_changed").first()
        self.assertEqual(ev.payload["changes"][0], {"field": "city", "old": "Pune", "new": "Hyderabad"})
        upd = FabricEvent.objects.filter(kind="entity_updated", entity=ann).first()
        self.assertEqual(upd.payload["changes"][0]["property"], "city")

    def test_deletion_only_on_full_scan_and_entities_follow(self):
        self.sync_all()
        set_csv(self.crm, "id,name,email,phone,city\nC1002,Bob Ray,bob@x.com,555-010-3000,Mumbai\n")  # Ann removed from CRM
        sync_source(self.crm, full=True)
        self.assertEqual(FabricRecord.objects.filter(source=self.crm, deleted=True).count(), 1)
        ann = next(e for e in entities(self.o) if "ann" in e.display_name.lower())
        self.assertEqual(ann.record_count, 1)  # still described by the ERP
        self.assertEqual(ann.canonical["customerName"], "Ann M. Lee")  # golden record falls back to the surviving source
        set_csv(self.erp, "cust_no,full_name,mail,tel,town\n88,Carol Stone,carol@y.com,,Delhi\n")
        sync_source(self.erp, full=True)
        self.assertEqual(FabricEntity.objects.filter(ontology=self.o, status="deleted").count(), 1)  # nobody describes Ann any more
        self.assertTrue(FabricEvent.objects.filter(kind="entity_deleted").exists())

    def test_a_bridging_record_merges_two_entities_and_the_older_survives(self):
        set_csv(self.crm, "id,name,email,phone,city\nA,Eve,eve@a.com,,X\nC,Eve J,eve@c.com,999-888-7777,X\n")
        sync_source(self.crm)
        a, c = entities(self.o)
        set_csv(self.crm, "id,name,email,phone,city\nA,Eve,eve@a.com,,X\nC,Eve J,eve@c.com,999-888-7777,X\nB,Eve Jay,eve@a.com,999-888-7777,X\n")
        r = sync_source(self.crm)
        self.assertEqual(r.stats["resolve_entities_merged"], 1)
        alive, merged = entities(self.o), entities(self.o, status="merged")
        self.assertEqual(len(alive), 1)
        self.assertEqual(alive[0].id, a.id)  # oldest entity id is stable
        self.assertEqual((merged[0].id, merged[0].merged_into_id), (c.id, a.id))
        self.assertEqual(alive[0].record_count, 3)
        self.assertTrue(FabricEvent.objects.filter(kind="entities_merged", payload__absorbed=c.id).exists())

    def test_changing_an_identity_key_splits_the_entity(self):
        self.sync_all()
        ann = next(e for e in entities(self.o) if e.record_count == 2)
        set_csv(self.erp, ERP.replace("ann@example.com", "other@z.com").replace("0015550102000", "123-456-7890"))
        r = sync_source(self.erp)
        self.assertEqual(r.stats["resolve_entities_split"], 1)
        ann.refresh_from_db()
        self.assertEqual(ann.record_count, 1)  # the id stays with a piece
        self.assertEqual(len(entities(self.o)), 4)  # Ann(CRM), Ann(ERP) now separate, Bob, Carol
        self.assertTrue(FabricEvent.objects.filter(kind="entity_split", entity=ann).exists())

    def test_classes_are_never_mixed(self):
        make_source(self.o, "SupplierSys", "id,name,email\nS1,Ann Lee,ann@example.com\n",
                    spec={"class": "Supplier", "id": "id", "fields": {"name": "supplierName", "email": "email"}, "identity": [{"keys": ["email"], "normalize": "email"}]})
        self.sync_all()
        sync_source(self.o.fabric_sources.get(name="SupplierSys"))
        self.assertEqual(len(entities(self.o, "Supplier")), 1)
        self.assertEqual(len(entities(self.o, "Customer")), 3)  # same email, different class: not merged


class SurvivorshipTests(TestCase):
    def test_strategies(self):
        o = make_ontology()
        a = make_source(o, "A", "id,name,email,phone,city\n1,Short,x@y.com,,Old\n", priority=80,
                        spec=customer_spec(survivorship={"city": "most_recent", "customerName": "longest"}, updated_at_field="chg"))
        b = make_source(o, "B", "id,name,email,phone,city\n9,A Much Longer Name,x@y.com,,New\n", priority=10,
                        spec=customer_spec(survivorship={"city": "most_recent", "customerName": "longest"}, updated_at_field="chg"))
        set_csv(a, "id,name,email,phone,city,chg\n1,Short,x@y.com,,Old,2026-01-01T00:00:00Z\n")
        set_csv(b, "id,name,email,phone,city,chg\n9,A Much Longer Name,x@y.com,,New,2026-03-01T00:00:00Z\n")
        sync_source(a)
        sync_source(b)
        e = entities(o)[0]
        self.assertEqual(e.canonical["city"], "New")  # most_recent beats the higher source priority
        self.assertEqual(e.canonical["customerName"], "A Much Longer Name")  # longest
        self.assertEqual(e.provenance["city"]["strategy"], "most_recent")

    def test_source_specific_and_default_priority(self):
        o = make_ontology()
        hi = make_source(o, "Hi", "id,name,email,phone,city\n1,HiName,x@y.com,,HiCity\n", priority=90)
        lo = make_source(o, "Lo", "id,name,email,phone,city\n2,LoName,x@y.com,,LoCity\n", priority=10,
                         spec=customer_spec(survivorship={"city": "source:Lo"}))
        sync_source(hi)
        sync_source(lo)
        e = entities(o)[0]
        self.assertEqual((e.canonical["customerName"], e.canonical["city"]), ("HiName", "LoCity"))  # name: highest priority wins; city: the mapping prefers source "Lo"
