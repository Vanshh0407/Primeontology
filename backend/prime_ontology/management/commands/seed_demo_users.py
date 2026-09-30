from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create/reset the DEMO login (admin / admin123). For demos and local development only."

    def add_arguments(self, parser):
        parser.add_argument("--with-viewer", action="store_true", help="Also create viewer / viewer123 (read-only) for testing.")
        parser.add_argument("--with-reviewer", action="store_true", help="Also create reviewer / reviewer123 (can approve/reject, not publish).")

    def handle(self, *args, **opts):
        u, _ = User.objects.get_or_create(username="admin", defaults={"is_staff": True, "is_superuser": True})
        u.is_staff = u.is_superuser = u.is_active = True
        u.set_password("admin123")
        u.save()
        self.stdout.write("demo user: admin / admin123 (administrator)")
        if opts["with_viewer"]:
            v, _ = User.objects.get_or_create(username="viewer")
            v.set_password("viewer123")
            v.is_active = True
            v.save()
            v.groups.add(Group.objects.get_or_create(name="Ontology Viewers")[0])
            self.stdout.write("demo user: viewer / viewer123 (read-only)")
        if opts["with_reviewer"]:
            r, _ = User.objects.get_or_create(username="reviewer")
            r.set_password("reviewer123")
            r.is_active = True
            r.save()
            r.groups.add(Group.objects.get_or_create(name="Ontology Reviewers")[0])
            self.stdout.write("demo user: reviewer / reviewer123 (can approve or reject, not publish)")
