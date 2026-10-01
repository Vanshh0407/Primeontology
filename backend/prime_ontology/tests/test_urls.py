from django.test import TestCase


class TrailingSlashTests(TestCase):
    def test_routes_work_with_and_without_slash(self):
        for path in ("health", "health/", "supported", "embedded/manifest", "embedded/manifest/", ""):
            r = self.client.get(f"/api/v1/ontology/{path}")
            self.assertEqual(r.status_code, 200, path)  # no 301 redirect loop behind Next.js rewrites


class ContextListTests(TestCase):
    def test_host_list_includes_own_and_untagged_but_not_other_hosts(self):
        import json

        from .helpers import jpost

        m = {"classes": [], "dataProperties": [], "objectProperties": []}
        for name, ctx in (("mine", "primesemonto"), ("untagged", ""), ("other", "unicontractai")):
            jpost(self.client, "/api/v1/ontology/", {"name": name, "model": m, "context": ctx})
        names = {o["name"] for o in self.client.get("/api/v1/ontology/?context=primesemonto").json()["results"]}
        self.assertEqual(names, {"mine", "untagged"})  # regression: approved ontologies must not vanish from the list
        self.assertEqual(len(self.client.get("/api/v1/ontology/").json()["results"]), 3)


class ConcurrencyTests(TestCase):
    def setUp(self):
        from .helpers import jpost

        m = {"classes": [{"name": "A", "parents": []}], "dataProperties": [], "objectProperties": []}
        self.o = jpost(self.client, "/api/v1/ontology/", {"name": "c", "model": m}).json()
        self.url = f"/api/v1/ontology/{self.o['id']}/"

    def put(self, model, **extra):
        from .helpers import jput

        return jput(self.client, self.url, {"model": model, **extra})

    def test_stale_save_is_rejected_then_can_be_forced(self):
        base = self.o["modelRevision"]
        m1 = {**self.o["model"], "classes": self.o["model"]["classes"] + [{"name": "FromAlice", "parents": []}]}
        ok = self.put(m1, baseRevision=base)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["modelRevision"], base + 1)
        m2 = {**self.o["model"], "classes": self.o["model"]["classes"] + [{"name": "FromBob", "parents": []}]}
        r = self.put(m2, baseRevision=base)  # Bob still on the old revision
        self.assertEqual(r.status_code, 409)
        j = r.json()
        self.assertEqual((j["code"], j["currentRevision"], j["yourRevision"]), ("conflict", base + 1, base))
        self.assertTrue(j["lastEditor"] is not None and any("FromAlice" in l for l in j["overwriteWouldChange"]))
        names = {c["name"] for c in self.client.get(self.url).json()["model"]["classes"]}
        self.assertEqual(names, {"A", "FromAlice"})  # Bob's save did not land
        self.assertEqual(self.put(m2, baseRevision=base, force=True).status_code, 200)

    def test_status_changes_do_not_bump_model_revision_and_no_base_means_no_check(self):
        from .helpers import jpost

        rev = self.client.get(self.url).json()["modelRevision"]
        jpost(self.client, self.url + "versions/", {"message": "x"})  # commit changes status, not the model
        after = self.client.get(self.url).json()
        self.assertEqual(after["modelRevision"], rev)
        self.assertEqual(self.put(after["model"], baseRevision=rev).status_code, 200)  # reviewer activity does not conflict
        self.assertEqual(self.put(after["model"]).status_code, 200)  # legacy clients without baseRevision still work
