import io
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

from .helpers import jpost

H = {"HTTP_X_PRIME_CLIENT": "workbench"}
IDENT = {"PRIME_ONTOLOGY_IDENTITY": "prime_ontology.identity.django_user_identity", "PRIME_ONTOLOGY_AUTHENTICATED_ROLE": "viewer",
         "PRIME_ONTOLOGY_ROLE_MAP": {"Ontology Viewers": "viewer"}}


@override_settings(**IDENT)
class LoginTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_demo_users", "--with-viewer", stdout=io.StringIO())

    def login(self, u, p, **kw):
        return jpost(self.client, "/api/v1/ontology/auth/login/", {"username": u, "password": p}, **{**H, **kw})

    def test_demo_admin_login_logout_me(self):
        self.assertEqual(self.client.get("/api/v1/ontology/auth/me/").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/ontology/").status_code, 403)  # API needs a login
        r = self.login("admin", "admin123")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["role"], "admin")
        me = self.client.get("/api/v1/ontology/auth/me/").json()
        self.assertEqual((me["authenticated"], me["username"]), (True, "admin"))
        self.assertEqual(self.client.get("/api/v1/ontology/").status_code, 200)
        self.assertEqual(jpost(self.client, "/api/v1/ontology/auth/logout/", **H).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/ontology/auth/me/").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/ontology/").status_code, 403)

    def test_viewer_is_read_only(self):
        self.login("viewer", "viewer123")
        self.assertEqual(self.client.get("/api/v1/ontology/auth/me/").json()["role"], "viewer")
        model = {"classes": [], "dataProperties": [], "objectProperties": []}
        self.assertEqual(jpost(self.client, "/api/v1/ontology/", {"name": "x", "model": model}, **H).status_code, 403)

    def test_bad_credentials_and_validation(self):
        r = self.login("admin", "wrong")
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("admin123", r.content.decode())
        self.assertEqual(self.login("", "").status_code, 400)
        self.assertEqual(self.login("nobody", "x").json(), self.login("admin", "wrong").json())  # no user enumeration

    def test_lockout_after_repeated_failures(self):
        for _ in range(5):
            self.assertEqual(self.login("admin", "bad").status_code, 401)
        self.assertEqual(self.login("admin", "admin123").status_code, 429)  # locked even with the right password
        cache.clear()
        self.assertEqual(self.login("admin", "admin123").status_code, 200)

    def test_login_requires_client_header_and_inactive_users_rejected(self):
        self.assertEqual(jpost(self.client, "/api/v1/ontology/auth/login/", {"username": "admin", "password": "admin123"}).status_code, 403)
        User.objects.filter(username="viewer").update(is_active=False)
        self.assertEqual(self.login("viewer", "viewer123").status_code, 401)

    def test_seed_is_idempotent_and_resets_password(self):
        u = User.objects.get(username="admin")
        u.set_password("changed")
        u.save()
        call_command("seed_demo_users", stdout=io.StringIO())
        self.assertEqual(User.objects.filter(username="admin").count(), 1)
        self.assertEqual(self.login("admin", "admin123").status_code, 200)
