"""Guards against quadratic blow-ups on large ontologies (a 2,000-class ontology used to take >60 s to validate)."""
import time

from django.test import TestCase

from prime_ontology import branching, query, validation, versioning


def big_model(n):
    classes = [{"name": f"Class{i}", "label": f"Class {i}", "comment": "", "parents": [f"Class{i - 7}"] if i > 0 and i % 7 == 0 else []} for i in range(n)]
    dps = [{"name": f"prop{k}", "domain": c["name"], "datatype": "string", "required": False} for c in classes for k in range(3)]
    ops = [{"name": f"rel{i}", "domain": f"Class{i % n}", "range": f"Class{(i * 7 + 13) % n}", "cardinality": "many-to-many"} for i in range(int(n * 1.5))]
    ops += [{"name": f"ring{i}", "domain": f"Class{i}", "range": f"Class{(i + 1) % n}", "cardinality": "many-to-many"} for i in range(n)]  # no orphans
    return {"classes": classes, "dataProperties": dps, "objectProperties": ops}


class ScaleTests(TestCase):
    def timed(self, fn, limit):
        t = time.time()
        out = fn()
        self.assertLess(time.time() - t, limit, f"took {time.time() - t:.1f}s (limit {limit}s)")
        return out

    def test_validation_stays_fast_on_a_very_large_ontology(self):
        m = big_model(1500)
        v = self.timed(lambda: validation.validate(m), 8)
        self.assertEqual(v["errors"], 0)
        self.assertEqual(v["counts"]["classes"], 1500)

    def test_exact_and_fuzzy_duplicate_detection_still_works_in_a_big_model(self):
        m = big_model(300)
        m["classes"] += [{"name": "ShipmentAddr", "label": "ShipmentAddr", "parents": []}, {"name": "ShipmentAdr", "label": "ShipmentAdr", "parents": []},
                         {"name": "customer", "label": "x", "parents": []}, {"name": "Customers", "label": "y", "parents": []}]
        pairs = {tuple(sorted(i["targets"])) for i in validation.validate(m)["issues"] if i["code"] == "DUPLICATE_CONCEPT"}
        self.assertIn(("ShipmentAddr", "ShipmentAdr"), pairs)  # fuzzy (typo)
        self.assertEqual({p for p in pairs if p[0].startswith("Class")}, set())  # numbered names (Class17 / Class177) are not "duplicates"
        self.assertIn(("Customers", "customer"), pairs)  # exact after normalisation

    def test_the_fuzzy_budget_is_reported_not_silent_and_does_not_hurt_the_score(self):
        m = big_model(650)  # < 26*26 so every generated name is unique
        # 700 similar-looking WORD names (not just numbered ones) exhaust the comparison budget
        for i, c in enumerate(m["classes"]):
            c["name"] = c["label"] = f"Item{chr(65 + i % 26)}{chr(65 + (i // 26) % 26)}x"
        names = {f"Class{i}": c["name"] for i, c in enumerate(m["classes"])}
        for c in m["classes"]:
            c["parents"] = [names.get(p, p) for p in c["parents"]]
        for key in ("dataProperties", "objectProperties"):
            for p in m[key]:
                p["domain"] = names.get(p["domain"], p["domain"])
                if "range" in p:
                    p["range"] = names.get(p["range"], p["range"])
        v = validation.validate(m)
        codes = {i["code"] for i in v["issues"]}
        self.assertIn("CHECK_TRUNCATED", codes)
        note = next(i for i in v["issues"] if i["code"] == "CHECK_TRUNCATED")
        self.assertEqual(note["severity"], "info")
        # the info note itself must not count toward errors/warnings (and so cannot lower the health score)
        self.assertEqual(v["warnings"], sum(1 for i in v["issues"] if i["severity"] == "warning"))
        self.assertEqual(v["errors"], 0)

    def test_other_operations_scale(self):
        m = big_model(2000)
        self.timed(lambda: query.search(m, "Class7"), 3)
        self.timed(lambda: versioning.diff(m, m), 3)
        self.timed(lambda: branching.three_way(m, m, m), 3)
