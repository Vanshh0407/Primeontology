"""Identity, tenancy and role-based permissions.

The host application owns authentication. Configure
PRIME_ONTOLOGY_IDENTITY = "myproject.auth.ontology_identity" (dotted path to
`def f(request) -> {"user": str, "role": str, "tenant": str}`) to plug in the
host's session/JWT. Default (standalone dev): trusted headers, else admin.
"""
from functools import wraps

from django.conf import settings
from django.http import JsonResponse
from django.utils.module_loading import import_string

ROLE_RANK = {"viewer": 1, "editor": 2, "reviewer": 3, "admin": 4}
# minimum role per capability
CAPABILITY = {
    "read": "viewer", "write": "editor", "commit": "editor", "submit": "editor",
    "review": "reviewer", "agent_manage": "admin", "os_manage": "admin", "fabric_manage": "admin", "fabric_sync": "editor", "fabric_decide": "reviewer", "fabric_manage": "admin", "fabric_sync": "editor", "fabric_decide": "reviewer", "publish": "admin", "rollback": "admin", "delete": "admin", "govern": "admin",
}


def default_identity(request) -> dict:
    """Standalone/dev identity from trusted headers. NOT for production: set PRIME_ONTOLOGY_IDENTITY to the
    host's resolver (e.g. "prime_ontology.identity.django_user_identity"). Outside DEBUG the fallback role is viewer."""
    fallback = getattr(settings, "PRIME_ONTOLOGY_DEFAULT_ROLE", "admin" if settings.DEBUG else "viewer")
    return {
        "user": request.headers.get("X-Prime-User", "anonymous"),
        "role": request.headers.get("X-Prime-Role") or fallback,
        "tenant": request.headers.get("X-Prime-Tenant", ""),
    }


def django_user_identity(request) -> dict:
    """Host-auth identity: uses request.user (session/JWT middleware of the host application).
    Roles: superuser -> admin; groups mapped via settings.PRIME_ONTOLOGY_ROLE_MAP = {"group name": "role"};
    otherwise PRIME_ONTOLOGY_AUTHENTICATED_ROLE (default editor). Tenant: settings.PRIME_ONTOLOGY_TENANT_ATTR
    (attribute path on the user, e.g. "organization_id"). Unauthenticated -> viewer with no tenant access."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"user": "anonymous", "role": "none", "tenant": "__anonymous__"}
    role = getattr(settings, "PRIME_ONTOLOGY_AUTHENTICATED_ROLE", "editor")
    if user.is_superuser:
        role = "admin"
    else:
        rmap = getattr(settings, "PRIME_ONTOLOGY_ROLE_MAP", {})
        groups = set(user.groups.values_list("name", flat=True)) if hasattr(user, "groups") else set()
        ranked = [rmap[g] for g in groups if g in rmap and rmap[g] in ROLE_RANK]
        if ranked:
            role = max(ranked, key=ROLE_RANK.get)
    tenant = ""
    attr = getattr(settings, "PRIME_ONTOLOGY_TENANT_ATTR", None)
    if attr:
        obj = user
        for part in attr.split("."):
            obj = getattr(obj, part, None)
        tenant = str(obj) if obj is not None else "__none__"
    return {"user": user.get_username(), "role": role, "tenant": tenant}


def service_identity(request):
    """A registered host application (R15) authenticates with its service token; it acts with the role it was registered with."""
    token = request.headers.get("X-Prime-Service-Token", "")
    if not token:
        return None
    import hashlib

    from django.utils import timezone

    from .osplane.models import HostApp

    h = HostApp.objects.select_related("ontology").filter(token_hash=hashlib.sha256(token.encode()).hexdigest(), enabled=True).first()
    if not h:
        return {"user": "invalid-service-token", "role": "none", "tenant": "__invalid__"}
    HostApp.objects.filter(pk=h.pk).update(last_seen_at=timezone.now())
    return {"user": f"host:{h.key}", "role": h.role, "tenant": h.ontology.tenant}


def get_identity(request) -> dict:
    svc = service_identity(request)
    if svc is not None:
        return svc
    path = getattr(settings, "PRIME_ONTOLOGY_IDENTITY", None)
    ident = import_string(path)(request) if path else default_identity(request)
    if ident.get("role") not in ROLE_RANK:
        ident["role"] = "none"  # unknown/unauthenticated: no capabilities at all
    return ident


def can(role: str, capability: str) -> bool:
    return ROLE_RANK.get(role, 0) >= ROLE_RANK[CAPABILITY[capability]]


CLIENT_HEADER = "X-Prime-Client"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def csrf_guard(request):
    """With host-managed (cookie) auth the mutating endpoints are CSRF-exempt, so require a custom header.
    Browsers cannot attach custom headers cross-site without a CORS preflight the host does not approve."""
    if request.method in SAFE_METHODS or not getattr(settings, "PRIME_ONTOLOGY_IDENTITY", None):
        return None
    if request.headers.get(CLIENT_HEADER):
        return None
    return JsonResponse({"error": f"Missing {CLIENT_HEADER} header (CSRF protection)."}, status=403)


def requires(capability: str):
    def deco(view):
        @wraps(view)
        def wrapper(request, *a, **kw):
            blocked = csrf_guard(request)
            if blocked:
                return blocked
            request.identity = get_identity(request)
            if not can(request.identity["role"], capability):
                return JsonResponse({"error": f"Role '{request.identity['role']}' cannot {capability} ontologies."}, status=403)
            return view(request, *a, **kw)

        return wrapper

    return deco
