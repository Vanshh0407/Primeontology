"""Semantic Enterprise OS control plane (R15): manifest, capabilities, enterprise snapshot, policies, evaluation, audit, host registration, routing.

Global routes (/api/v1/os/...) take `ontologyId` (query or body). If the caller's tenant has exactly one ontology it may be omitted.
"""
import hashlib
import secrets

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .. import embeddings, llm, versioning
from ..autonomy import tools as agent_tools
from ..autonomy.models import Agent, AgentRun, ApprovalRequest
from ..fabric import freshness
from ..fabric.models import FabricEntity
from ..identity import CAPABILITY, ROLE_RANK, can, requires
from ..models import AuditEvent, Ontology
from ..rag.models import RagChunk, RagDocument
from ..twin.models import TwinEvent, TwinRule
from ..views import ApiError, api, body, get_ontology
from . import policy as engine
from .models import HostApp, Policy

VERSION = "R15"

MODULES = [
    {"key": "ontology", "release": "R1-R10", "title": "Ontology engine", "capabilities": ["read", "write", "commit", "review", "publish"],
     "endpoints": ["/api/v1/ontology/", "/api/v1/ontology/{id}/graph/", "/api/v1/ontology/{id}/query/", "/api/v1/ontology/{id}/versions/"]},
    {"key": "fabric", "release": "R11", "title": "Enterprise Knowledge Fabric", "capabilities": ["fabric_sync", "fabric_decide", "fabric_manage"],
     "endpoints": ["/api/v1/ontology/{id}/fabric/", "/api/v1/ontology/{id}/fabric/sync/", "/api/v1/fabric/sources/"]},
    {"key": "rag", "release": "R12", "title": "Semantic RAG & AI Context", "capabilities": ["read", "write"],
     "endpoints": ["/api/v1/ontology/{id}/rag/documents/", "/api/v1/ontology/{id}/rag/retrieve/", "/api/v1/ontology/{id}/rag/ask/"]},
    {"key": "twin", "release": "R13", "title": "Semantic Digital Twin", "capabilities": ["read", "write"],
     "endpoints": ["/api/v1/ontology/{id}/twin/", "/api/v1/ontology/{id}/twin/events/", "/api/v1/ontology/{id}/twin/simulate/"]},
    {"key": "agents", "release": "R14", "title": "Autonomous Agent Platform", "capabilities": ["read", "write", "review", "agent_manage"],
     "endpoints": ["/api/v1/ontology/{id}/agents/", "/api/v1/ontology/{id}/agents/plan/", "/api/v1/ontology/{id}/agents/execute/", "/api/v1/ontology/{id}/agents/approve/",
                   "/api/v1/autonomous/manifest/"]},
    {"key": "os", "release": "R15", "title": "Semantic Enterprise OS", "capabilities": ["read", "os_manage"],
     "endpoints": ["/api/v1/os/manifest/", "/api/v1/os/capabilities/", "/api/v1/os/snapshot/", "/api/v1/os/policies/", "/api/v1/os/evaluate/", "/api/v1/os/audit/", "/api/v1/os/ask/"]},
]


def ont(request) -> Ontology:
    ident = request.identity
    raw = request.GET.get("ontologyId")
    if raw is None and request.method in ("POST", "PUT", "DELETE"):
        try:
            raw = body(request).get("ontologyId")
        except ApiError:
            raw = None
    if raw is None:
        qs = Ontology.objects.filter(branch_of__isnull=True)
        if ident.get("tenant"):
            qs = qs.filter(tenant=ident["tenant"])
        if qs.count() == 1:
            return qs.first()
        raise ApiError("ontologyId is required.")
    try:
        return get_ontology(request, int(raw))
    except (TypeError, ValueError):
        raise ApiError("ontologyId must be an integer.") from None


def _need(request, cap, what):
    if not can(request.identity["role"], cap):
        raise ApiError(f"Role cannot {what}.", 403)


@api
@require_http_methods(["GET"])
def manifest(request):
    from ..embedded import HOSTS, TABS

    return JsonResponse({"name": "Prime Semantic Enterprise OS", "release": VERSION, "apiVersion": "v1", "modules": MODULES, "hosts": sorted(HOSTS),
                         "workbenchTabs": [*TABS, "fabric", "rag", "twin", "autonomy", "os"], "roles": sorted(ROLE_RANK, key=ROLE_RANK.get),
                         "capabilityRoles": {c: r for c, r in CAPABILITY.items()}, "tools": [t["name"] for t in BUILTIN_NAMES()]})


def BUILTIN_NAMES():
    return [{"name": n} for n in agent_tools.BUILTIN]


@api
@requires("read")
@require_http_methods(["GET"])
def capabilities(request):
    """What THIS caller can do right now, per module, plus the runtime features that are actually switched on."""
    role = request.identity["role"]
    mods = [{**m, "allowed": {c: can(role, c) for c in m["capabilities"] if c in CAPABILITY}, "available": any(can(role, c) for c in m["capabilities"] if c in CAPABILITY)}
            for m in MODULES]
    return JsonResponse({"identity": request.identity, "modules": mods, "runtime": {"embeddings": embeddings.status(), "llm": llm.available(),
                                                                                     "tools": agent_tools.BUILTIN and sorted(agent_tools.BUILTIN)}})


@api
@requires("read")
@require_http_methods(["GET"])
def snapshot(request):
    o = ont(request)
    from ..fabric.views import _snapshot as fabric_snapshot

    fab = fabric_snapshot(o)
    sources = fab["sources"]
    runs = AgentRun.objects.filter(ontology=o)
    pending = ApprovalRequest.objects.filter(run__ontology=o, status="pending").count()
    hosts = list(HostApp.objects.filter(ontology=o).values("key", "name", "enabled", "last_seen_at"))
    # semantic health: the signals an operator needs on one line each
    health = []
    health.append({"area": "ontology", "status": "ok" if o.status in ("published", "approved") else "attention", "detail": f"status: {o.status}"})
    health.append({"area": "fabric", "status": "ok" if fab["freshness"] == "FRESH" else "attention" if sources else "empty",
                   "detail": f"{len(sources)} source(s), freshness {fab['freshness']}, {fab['pendingSuggestions']} match suggestion(s) pending, {fab['pendingDrift']} drift item(s)"})
    health.append({"area": "rag", "status": "ok" if RagChunk.objects.filter(ontology=o).exists() else "empty",
                   "detail": f"{RagDocument.objects.filter(ontology=o).count()} document(s), {RagChunk.objects.filter(ontology=o).count()} chunk(s)"})
    health.append({"area": "twin", "status": "ok" if FabricEntity.objects.filter(ontology=o, status="active").exists() else "empty",
                   "detail": f"{TwinEvent.objects.filter(ontology=o).count()} event(s), {TwinRule.objects.filter(ontology=o).count()} rule(s)"})
    health.append({"area": "agents", "status": "attention" if pending else "ok" if Agent.objects.filter(ontology=o).exists() else "empty",
                   "detail": f"{Agent.objects.filter(ontology=o, enabled=True).count()} agent(s), {runs.count()} run(s), {pending} approval(s) pending"})
    return JsonResponse({"ontology": {"id": o.id, "name": o.name, "status": o.status, "version": o.current_version, "classes": len(o.model.get("classes", [])),
                                      "properties": len(o.model.get("dataProperties", [])) + len(o.model.get("objectProperties", []))},
                         "health": health, "fabric": {"freshness": fab["freshness"], "entities": fab["entities"], "records": fab["records"], "sources": [
                             {"name": s["name"], "kind": s["kind"], "freshness": s["freshness"]["status"], "records": s["records"]} for s in sources]},
                         "rag": {"documents": RagDocument.objects.filter(ontology=o).count(), "chunks": RagChunk.objects.filter(ontology=o).count(), "embedding": embeddings.status()["provider"]},
                         "twin": {"events": TwinEvent.objects.filter(ontology=o).count(), "rules": TwinRule.objects.filter(ontology=o).count()},
                         "agents": {"registered": Agent.objects.filter(ontology=o).count(), "runs": runs.count(), "running": runs.filter(status="running").count(),
                                    "awaitingApproval": pending, "failed": runs.filter(status="failed").count(),
                                    "avgScore": _avg([r.evaluation.get("overall") for r in runs.exclude(evaluation={})[:50]])},
                         "policies": {"total": Policy.objects.filter(ontology=o).count(), "enabled": Policy.objects.filter(ontology=o, enabled=True).count()},
                         "hosts": [{**h, "last_seen_at": h["last_seen_at"].isoformat() if h["last_seen_at"] else None} for h in hosts]})


def _avg(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), 2) if xs else None


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def policies(request):
    o = ont(request)
    if request.method == "POST":
        _need(request, "os_manage", "manage enterprise policies")
        try:
            spec = engine.validate(o.model, body(request))
        except engine.PolicyError as e:
            raise ApiError(str(e)) from None
        if Policy.objects.filter(ontology=o, name=spec["name"]).exists():
            raise ApiError(f"A policy named '{spec['name']}' already exists.")
        p = Policy.objects.create(ontology=o, created_by=request.identity["user"], **spec)
        versioning.audit(o, "os.policy.created", request.identity["user"], policy=p.name, effect=p.effect)
        return JsonResponse(engine.policy_json(p), status=201)
    return JsonResponse({"policies": [engine.policy_json(p) for p in Policy.objects.filter(ontology=o)]})


@api
@requires("read")
@require_http_methods(["PUT", "DELETE"])
def policy_detail(request, pid):
    o = ont(request)
    _need(request, "os_manage", "manage enterprise policies")
    p = Policy.objects.filter(pk=pid, ontology=o).first()
    if not p:
        raise ApiError("Policy not found.", 404)
    if request.method == "DELETE":
        versioning.audit(o, "os.policy.deleted", request.identity["user"], policy=p.name)
        p.delete()
        return JsonResponse({"deleted": True})
    b = body(request)
    try:
        spec = engine.validate(o.model, {**engine.policy_json(p), **b})
    except engine.PolicyError as e:
        raise ApiError(str(e)) from None
    if Policy.objects.filter(ontology=o, name=spec["name"]).exclude(pk=p.pk).exists():
        raise ApiError(f"A policy named '{spec['name']}' already exists.")
    for k, v in spec.items():
        setattr(p, k, v)
    p.save()
    versioning.audit(o, "os.policy.updated", request.identity["user"], policy=p.name)
    return JsonResponse(engine.policy_json(p))


@api
@requires("read")
@require_http_methods(["POST"])
def evaluate(request):
    """Dry-run the policy engine for a hypothetical action (no side effects)."""
    o = ont(request)
    b = body(request)
    ident = request.identity
    out = engine.evaluate(o, actor=b.get("actor", ident["user"]), actor_type=b.get("actorType", "user"), role=b.get("role", ident["role"]), agent=b.get("agent", ""),
                          operation=b.get("operation", "read"), concepts=b.get("concepts") or [], tool=b.get("tool", ""), classification=b.get("classification", "internal"),
                          records=int(b.get("records", 1)), external=bool(b.get("external")), tool_risk=b.get("toolRisk"))
    if b.get("role") and b["role"] != ident["role"] and ROLE_RANK.get(b["role"], 0) > ROLE_RANK.get(ident["role"], 0):
        raise ApiError("You cannot evaluate as a role higher than your own.", 403)
    return JsonResponse(out)


@api
@requires("read")
@require_http_methods(["GET"])
def audit(request):
    o = ont(request)
    qs = AuditEvent.objects.filter(ontology=o)
    g = request.GET
    if g.get("prefix"):
        qs = qs.filter(action__startswith=g["prefix"][:40])
    if g.get("actor"):
        qs = qs.filter(actor=g["actor"][:200])
    try:
        limit = max(1, min(int(g.get("limit", 100)), 500))
    except ValueError:
        raise ApiError("limit must be an integer.") from None
    return JsonResponse({"events": [{"id": e.id, "action": e.action, "actor": e.actor, "detail": e.detail, "at": e.created_at.isoformat()} for e in qs[:limit]]})


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def hosts(request):
    o = ont(request)
    if request.method == "POST":
        _need(request, "os_manage", "register host applications")
        b = body(request)
        key, name = str(b.get("key", "")).strip().lower(), str(b.get("name", "")).strip()
        if not key.replace("-", "").replace("_", "").isalnum() or len(key) > 60 or not name:
            raise ApiError("key (letters, digits, - _) and name are required.")
        role = b.get("role", "editor")
        if role not in ROLE_RANK or ROLE_RANK[role] > ROLE_RANK["reviewer"]:
            raise ApiError("A host's service role can be viewer, editor or reviewer.")
        if HostApp.objects.filter(ontology=o, key=key).exists():
            raise ApiError(f"Host '{key}' is already registered.")
        token = "pst_" + secrets.token_urlsafe(32)
        h = HostApp.objects.create(ontology=o, key=key, name=name[:120], capabilities=b.get("capabilities") or [], role=role, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                                   token_hint=token[-4:])
        versioning.audit(o, "os.host.registered", request.identity["user"], host=key, role=role)
        return JsonResponse({"key": h.key, "name": h.name, "role": h.role, "token": token, "note": "Store this token now: it is shown only once."}, status=201)
    return JsonResponse({"hosts": [{"key": h.key, "name": h.name, "capabilities": h.capabilities, "role": h.role, "enabled": h.enabled, "tokenHint": "…" + h.token_hint,
                                    "lastSeenAt": h.last_seen_at.isoformat() if h.last_seen_at else None} for h in HostApp.objects.filter(ontology=o)]})


@api
@requires("read")
@require_http_methods(["POST"])
def ask(request):
    """Cross-module orchestration: routes a question to the module that can answer it (twin what-if / fabric status / RAG)."""
    from ..autonomy.planner import WHATIF, parse_change
    from ..fabric.views import _snapshot as fabric_snapshot
    from ..rag.answer import answer
    from ..twin import core

    o = ont(request)
    q = str(body(request).get("question", "")).strip()
    if not q:
        raise ApiError("question is required.")
    if WHATIF.search(q):
        ch = parse_change(o, q)
        if ch:
            try:
                return JsonResponse({"module": "twin", "routedBy": "what-if pattern", "result": core.simulate(o, [ch])})
            except core.TwinError as e:
                raise ApiError(str(e)) from None
    low = q.lower()
    if any(w in low for w in ("freshness", "stale", "last sync", "source status", "sources up to date")):
        fs = fabric_snapshot(o)
        return JsonResponse({"module": "fabric", "routedBy": "source-status keywords", "result": {"freshness": fs["freshness"], "sources": [
            {"name": s["name"], "freshness": s["freshness"]["status"], "lastSyncedAt": s["freshness"]["lastSyncedAt"]} for s in fs["sources"]]}})
    out = answer(o, q, role=request.identity["role"])
    return JsonResponse({"module": "rag", "routedBy": "default", "result": out})
