import copy

from django.test import TestCase

from prime_ontology import branching, validation

from .helpers import jpost, jput
from .test_platform import demo_model


def base_model():
    return {"classes": [{"name": "Customer", "label": "Customer", "comment": "", "parents": []},
                        {"name": "Order", "label": "Order", "comment": "", "parents": []}],
            "dataProperties": [{"name": "name", "domain": "Customer", "datatype": "string", "required": False}],
            "objectProperties": [{"name": "places", "domain": "Customer", "range": "Order", "cardinality": "many-to-many"}]}


def cls(m, n):
    return next(c for c in m["classes"] if c["name"] == n)


class ThreeWayTests(TestCase):
    def test_independent_changes_merge_cleanly(self):
        base = base_model()
        ours, theirs = copy.deepcopy(base), copy.deepcopy(base)
        cls(ours, "Customer")["comment"] = "ours comment"
        ours["classes"].append({"name": "Product", "label": "Product", "parents": []})
        cls(theirs, "Order")["label"] = "Sales Order"  # different class
        theirs["dataProperties"].append({"name": "email", "domain": "Customer", "datatype": "string"})
        r = branching.three_way(base, ours, theirs)
        self.assertEqual(r["conflicts"], [])
        m = r["model"]
        self.assertEqual(cls(m, "Customer")["comment"], "ours comment")
        self.assertEqual(cls(m, "Order")["label"], "Sales Order")
        self.assertEqual({c["name"] for c in m["classes"]}, {"Customer", "Order", "Product"})
        self.assertEqual({p["name"] for p in m["dataProperties"]}, {"name", "email"})

    def test_different_fields_of_same_class_merge_but_same_field_conflicts(self):
        base = base_model()
        ours, theirs = copy.deepcopy(base), copy.deepcopy(base)
        cls(ours, "Customer")["comment"] = "A"
        cls(theirs, "Customer")["label"] = "Client"
        ok = branching.three_way(base, ours, theirs)
        self.assertEqual(ok["conflicts"], [])
        self.assertEqual((cls(ok["model"], "Customer")["comment"], cls(ok["model"], "Customer")["label"]), ("A", "Client"))
        cls(theirs, "Customer")["comment"] = "B"
        r = branching.three_way(base, ours, theirs)
        self.assertIsNone(r["model"])
        self.assertEqual([(c["kind"], c["field"], c["ours"], c["theirs"]) for c in r["conflicts"]], [("field", "comment", "A", "B")])

    def test_resolutions(self):
        base = base_model()
        ours, theirs = copy.deepcopy(base), copy.deepcopy(base)
        cls(ours, "Customer")["comment"] = "A"
        cls(theirs, "Customer")["comment"] = "B"
        cid = branching.three_way(base, ours, theirs)["conflicts"][0]["id"]
        self.assertEqual(cls(branching.three_way(base, ours, theirs, {cid: "ours"})["model"], "Customer")["comment"], "A")
        self.assertEqual(cls(branching.three_way(base, ours, theirs, {cid: "theirs"})["model"], "Customer")["comment"], "B")

    def test_delete_vs_edit_conflicts_and_one_sided_delete_applies(self):
        base = base_model()
        ours, theirs = copy.deepcopy(base), copy.deepcopy(base)
        ours["classes"] = [c for c in ours["classes"] if c["name"] != "Order"]
        ours["objectProperties"] = []
        cls(theirs, "Order")["comment"] = "edited"
        r = branching.three_way(base, ours, theirs)
        self.assertEqual([c["kind"] for c in r["conflicts"]], ["edit-vs-delete"])
        deleted = branching.three_way(base, ours, copy.deepcopy(base))  # theirs untouched -> deletion simply applies
        self.assertEqual({c["name"] for c in deleted["model"]["classes"]}, {"Customer"})

    def test_both_sides_same_change_is_not_a_conflict_and_parents_of_deleted_classes_are_cleaned(self):
        base = base_model()
        ours, theirs = copy.deepcopy(base), copy.deepcopy(base)
        for m in (ours, theirs):
            cls(m, "Customer")["comment"] = "same"
        self.assertEqual(branching.three_way(base, ours, theirs)["conflicts"], [])
        base2 = copy.deepcopy(base)
        cls(base2, "Order")["parents"] = ["Customer"]
        o2, t2 = copy.deepcopy(base2), copy.deepcopy(base2)
        o2["classes"] = [c for c in o2["classes"] if c["name"] != "Customer"]
        o2["dataProperties"] = []
        o2["objectProperties"] = []
        merged = branching.three_way(base2, o2, t2)["model"]
        self.assertEqual(cls(merged, "Order")["parents"], [])  # no dangling parent reference

    def test_provenance_noise_never_conflicts(self):
        base = base_model()
        ours, theirs = copy.deepcopy(base), copy.deepcopy(base)
        cls(ours, "Customer")["evidence"] = [{"page": 1}]
        cls(theirs, "Customer")["evidence"] = [{"page": 9}]
        self.assertEqual(branching.three_way(base, ours, theirs)["conflicts"], [])

    def test_individuals_merge_by_name(self):
        base = {**base_model(), "individuals": [{"name": "c1", "class": "Customer", "data": {"name": "A"}, "links": {}}]}
        ours, theirs = copy.deepcopy(base), copy.deepcopy(base)
        ours["individuals"].append({"name": "c2", "class": "Customer", "data": {}, "links": {}})
        theirs["individuals"][0]["data"]["name"] = "Renamed"
        m = branching.three_way(base, ours, theirs)["model"]
        self.assertEqual({i["name"] for i in m["individuals"]}, {"c1", "c2"})
        self.assertEqual(next(i for i in m["individuals"] if i["name"] == "c1")["data"]["name"], "Renamed")


class BranchApiTests(TestCase):
    def setUp(self):
        self.o = jpost(self.client, "/api/v1/ontology/", {"name": "Main", "model": base_model()}).json()
        self.base = f"/api/v1/ontology/{self.o['id']}"

    def mk_branch(self, name="feature-x", **kw):
        return jpost(self.client, f"{self.base}/branches/", {"name": name, **kw})

    def edit(self, oid, fn):
        cur = self.client.get(f"/api/v1/ontology/{oid}/").json()
        m = fn(copy.deepcopy(cur["model"]))
        r = jput(self.client, f"/api/v1/ontology/{oid}/", {"model": m, "baseRevision": cur["modelRevision"]})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_create_list_and_isolation(self):
        r = self.mk_branch()
        self.assertEqual(r.status_code, 201)
        b = r.json()
        self.assertEqual((b["branchOf"], b["branchName"]), (self.o["id"], "feature-x"))
        self.edit(b["id"], lambda m: m["classes"].append({"name": "Product", "label": "Product", "parents": []}) or m)
        main = self.client.get(f"{self.base}/").json()
        self.assertEqual({c["name"] for c in main["model"]["classes"]}, {"Customer", "Order"})  # main untouched
        rows = self.client.get(f"{self.base}/branches/").json()
        self.assertEqual(len(rows["branches"]), 2)
        brow = next(x for x in rows["branches"] if not x["isRoot"])
        self.assertEqual(brow["changesSinceFork"], 1)
        self.assertEqual(self.client.get(f"/api/v1/ontology/{b['id']}/branches/").json()["root"], self.o["id"])  # listable from the branch too

    def test_branch_name_rules(self):
        self.assertEqual(self.mk_branch("main").status_code, 400)
        self.assertEqual(self.mk_branch("bad<name>").status_code, 400)
        self.assertEqual(self.mk_branch("ok").status_code, 201)
        self.assertEqual(self.mk_branch("OK").status_code, 400)  # case-insensitive unique

    def test_branch_from_a_version(self):
        jpost(self.client, f"{self.base}/versions/", {"message": "v1"})
        self.edit(self.o["id"], lambda m: m["classes"].append({"name": "Later", "label": "Later", "parents": []}) or m)
        b = self.mk_branch("from-v1", fromVersion="1.0").json()
        self.assertEqual({c["name"] for c in b["model"]["classes"]}, {"Customer", "Order"})  # forked from the snapshot, not the working copy
        self.assertEqual(self.mk_branch("x", fromVersion="9.9").status_code, 404)

    def test_merge_flow_clean(self):
        b = self.mk_branch().json()
        self.edit(b["id"], lambda m: m["classes"].append({"name": "Product", "label": "Product", "parents": []}) or m)
        self.edit(self.o["id"], lambda m: m["dataProperties"].append({"name": "email", "domain": "Customer", "datatype": "string"}) or m)
        dry = jpost(self.client, f"{self.base}/merge/", {"sourceId": b["id"], "dryRun": True}).json()
        self.assertEqual((dry["merged"], dry["conflicts"], dry["dryRun"]), (False, [], True))
        self.assertIn("+ Class: Product", dry["changes"])
        self.assertEqual({c["name"] for c in self.client.get(f"{self.base}/").json()["model"]["classes"]}, {"Customer", "Order"})  # dry run applied nothing
        done = jpost(self.client, f"{self.base}/merge/", {"sourceId": b["id"], "dryRun": False}).json()
        self.assertTrue(done["merged"])
        m = self.client.get(f"{self.base}/").json()["model"]
        self.assertEqual({c["name"] for c in m["classes"]}, {"Customer", "Order", "Product"})
        self.assertIn("email", {p["name"] for p in m["dataProperties"]})  # main's own change survived
        actions = [e["action"] for e in self.client.get(f"{self.base}/audit/").json()["results"]]
        self.assertIn("branch.merged", actions)
        again = jpost(self.client, f"{self.base}/merge/", {"sourceId": b["id"], "dryRun": True}).json()
        self.assertEqual(again["changes"], [])  # base advanced: merging again is a no-op

    def test_conflict_must_be_resolved_then_applies(self):
        b = self.mk_branch().json()
        self.edit(b["id"], lambda m: cls(m, "Customer").update(comment="from branch") or m)
        self.edit(self.o["id"], lambda m: cls(m, "Customer").update(comment="from main") or m)
        r = jpost(self.client, f"{self.base}/merge/", {"sourceId": b["id"], "dryRun": False}).json()
        self.assertFalse(r["merged"])
        self.assertEqual(r["conflicts"][0]["field"], "comment")
        self.assertEqual(cls(self.client.get(f"{self.base}/").json()["model"], "Customer")["comment"], "from main")  # nothing applied
        cid = r["conflicts"][0]["id"]
        ok = jpost(self.client, f"{self.base}/merge/", {"sourceId": b["id"], "dryRun": False, "resolutions": {cid: "theirs"}}).json()
        self.assertTrue(ok["merged"])
        self.assertEqual(cls(self.client.get(f"{self.base}/").json()["model"], "Customer")["comment"], "from branch")
        self.assertEqual(jpost(self.client, f"{self.base}/merge/", {"sourceId": b["id"], "resolutions": {cid: "maybe"}}).status_code, 400)

    def test_guards(self):
        b = self.mk_branch().json()
        self.assertEqual(jpost(self.client, f"{self.base}/merge/", {"sourceId": self.o["id"]}).status_code, 400)  # into itself
        other = jpost(self.client, "/api/v1/ontology/", {"name": "Other", "model": base_model()}).json()
        self.assertEqual(jpost(self.client, f"{self.base}/merge/", {"sourceId": other["id"]}).status_code, 400)  # unrelated ontology
        self.assertEqual(jpost(self.client, f"/api/v1/ontology/{other['id']}/merge/", {"sourceId": b["id"]}).status_code, 400)
        hdr = {"HTTP_X_PRIME_ROLE": "viewer"}
        from django.test import override_settings

        with override_settings(PRIME_ONTOLOGY_DEFAULT_ROLE="viewer"):
            self.assertEqual(self.mk_branch("v", **{}).status_code, 403)
            self.assertEqual(jpost(self.client, f"{self.base}/merge/", {"sourceId": b["id"], "dryRun": False}).status_code, 403)
            self.assertEqual(jpost(self.client, f"{self.base}/merge/", {"sourceId": b["id"], "dryRun": True}).status_code, 200)  # preview is read-only
        del hdr

    def test_merge_that_introduces_validation_errors_is_blocked_unless_forced(self):
        b = self.mk_branch().json()
        self.edit(b["id"], lambda m: m["objectProperties"].append({"name": "x", "domain": "Customer", "range": "Ghost", "cardinality": "many-to-many"}) or m)
        r = jpost(self.client, f"{self.base}/merge/", {"sourceId": b["id"], "dryRun": False})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "merge-introduces-errors")
        forced = jpost(self.client, f"{self.base}/merge/", {"sourceId": b["id"], "dryRun": False, "allowErrors": True})
        self.assertEqual(forced.status_code, 200)


class IndividualValidationTests(TestCase):
    def issues(self, inds):
        m = demo_model()
        m["individuals"] = inds
        return {i["code"] for i in validation.validate(m)["issues"]}

    def test_demo_individuals_have_no_errors(self):
        m = demo_model()
        v = validation.validate(m)
        self.assertEqual(v["errors"], 0, [i for i in v["issues"] if i["severity"] == "error"])
        self.assertEqual({i["code"] for i in v["issues"]} - {"MISSING_REQUIRED"}, set())  # sparse demo records only trigger required-value warnings

    def test_detects_bad_instance_data(self):
        codes = self.issues([
            {"name": "a", "class": "Nope", "data": {}, "links": {}},
            {"name": "b", "class": "Product", "data": {"unitPrice": "not-a-number", "ghost": 1}, "links": {"hasSupplier": "missing", "hasGhost": "b"}},
            {"name": "b", "class": "Customer", "data": {}, "links": {}},
            {"name": "c", "class": "SalesOrder", "data": {"orderDate": "yesterday"}, "links": {"hasCustomer": "d"}},
            {"name": "d", "class": "Product", "data": {}, "links": {}},
            {"name": "bad name", "class": "Customer", "data": {}, "links": {}}])
        for c in ("UNKNOWN_CLASS", "DATATYPE_MISMATCH", "UNKNOWN_PROPERTY", "BROKEN_LINK", "WRONG_LINK_TYPE", "DUPLICATE_INDIVIDUAL", "INVALID_IRI", "MISSING_REQUIRED"):
            self.assertIn(c, codes)
