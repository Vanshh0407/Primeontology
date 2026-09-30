from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings

from prime_ontology.models import Ontology

from .helpers import jpost

HOST = "prime_ontology.identity.django_user_identity"
MODEL = {"classes": [{"name": "A", "parents": []}], "dataProperties": [], "objectProperties": []}
CLIENT = {"HTTP_X_PRIME_CLIENT": "workbench"}


@override_settings(PRIME_ONTOLOGY_IDENTITY=HOST, PRIME_ONTOLOGY_ROLE_MAP={"Ontology Reviewers": "reviewer", "Ontology Editors": "editor"},
                   PRIME_ONTOLOGY_TENANT_ATTR="username")
class HostIdentityTests(TestCase):
    def setUp(self):
        self.ed = User.objects.create_user("alice", password="x")
        self.ed.groups.add(Group.objects.create(name="Ontology Editors"))
        self.rev = User.objects.create_user("bob", password="x")
        self.rev.groups.add(Group.objects.create(name="Ontology Reviewers"))
        self.root = User.objects.create_superuser("root", password="x")

    def test_unauthenticated_gets_nothing(self):
        self.assertEqual(self.client.get("/api/v1/ontology/").status_code, 403)

    def test_csrf_header_required_for_writes_with_host_auth(self):
        self.client.force_login(self.ed)
        body = {"name": "x", "model": MODEL}
        self.assertEqual(jpost(self.client, "/api/v1/ontology/", body).status_code, 403)
        ok = jpost(self.client, "/api/v1/ontology/", body, **CLIENT)
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(Ontology.objects.get().tenant, "alice")  # tenant comes from the host user, not from headers

    def test_role_and_tenant_headers_cannot_be_spoofed(self):
        self.client.force_login(self.ed)
        h = {**CLIENT, "HTTP_X_PRIME_ROLE": "admin", "HTTP_X_PRIME_TENANT": "someone-else"}
        o = jpost(self.client, "/api/v1/ontology/", {"name": "x", "model": MODEL}, **h).json()
        self.assertEqual(self.client.delete(f"/api/v1/ontology/{o['id']}/", **h).status_code, 403)  # editor, not admin
        self.assertEqual(self.client.get("/api/v1/ontology/", **h).json()["results"][0]["id"], o["id"])

    def test_tenant_isolation_between_users(self):
        self.client.force_login(self.ed)
        o = jpost(self.client, "/api/v1/ontology/", {"name": "x", "model": MODEL}, **CLIENT).json()
        self.client.force_login(self.rev)
        self.assertEqual(self.client.get(f"/api/v1/ontology/{o['id']}/").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/ontology/").json()["results"], [])

    def test_group_roles_and_superuser(self):
        self.client.force_login(self.rev)
        self.assertEqual(self.client.get("/api/v1/ontology/embedded/context/?host=primesemonto").json()["identity"]["role"], "reviewer")
        self.client.force_login(self.root)
        o = jpost(self.client, "/api/v1/ontology/", {"name": "x", "model": MODEL}, **CLIENT).json()
        self.assertEqual(self.client.delete(f"/api/v1/ontology/{o['id']}/", **CLIENT).status_code, 204)


class DefaultIdentityTests(TestCase):
    def test_fail_closed_outside_debug(self):
        from django.conf import settings

        with override_settings(DEBUG=False):
            del settings.PRIME_ONTOLOGY_DEFAULT_ROLE  # the un-configured production default
            self._assert_read_only()

    def _assert_read_only(self):
        self.assertEqual(jpost(self.client, "/api/v1/ontology/", {"name": "x", "model": MODEL}).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/ontology/").status_code, 200)

    def test_dev_default_admin(self):
        from django.conf import settings

        with override_settings(DEBUG=True):
            del settings.PRIME_ONTOLOGY_DEFAULT_ROLE
            self._assert_dev_admin()

    def _assert_dev_admin(self):
        self.assertEqual(jpost(self.client, "/api/v1/ontology/", {"name": "x", "model": MODEL}).status_code, 201)

    def test_db_host_allowlist(self):
        with override_settings(PRIME_ONTOLOGY_ALLOWED_DB_HOSTS=["db.internal"]):
            r = jpost(self.client, "/api/v1/ontology/inspect/", {"type": "mysql", "config": {"host": "169.254.169.254", "database": "x"}})
            self.assertEqual(r.status_code, 403)
