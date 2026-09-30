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
