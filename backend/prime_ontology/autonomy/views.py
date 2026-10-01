"""Autonomous Enterprise Agent Platform API (R14)."""
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .. import versioning
from ..identity import can, requires
from ..osplane import policy as policy_engine
from ..views import ApiError, api, body, get_ontology
from . import engine, mcp_client, memory, planner, tools
from .models import Agent, AgentMemory, AgentRun, AgentTool, ApprovalRequest, McpServer

ROLES = ("supervisor", "research", "data", "simulation", "action", "custom")
OPS = ("read", "create", "update", "delete", "execute", "approve")
NAME_OK = lambda s: bool(s) and len(s) <= 120 and all(ch.isalnum() or ch in " _.-" for ch in s)  # noqa: E731


def _need(request, cap, what):
    if not can(request.identity["role"], cap):
        raise ApiError(f"Role cannot {what}.", 403)


def agent_json(a: Agent) -> dict:
    return {"id": a.id, "name": a.name, "role": a.role, "description": a.description, "capabilities": a.capabilities, "permissions": a.permissions, "scope": a.scope,
            "tools": a.tools, "goals": a.goals, "policies": a.policies, "budget": a.budget, "enabled": a.enabled, "memories": a.memories.count()}


def validate_agent(o, b: dict, existing=None) -> dict:
    name = str(b.get("name", existing.name if existing else "")).strip()
    if not NAME_OK(name):
        raise ApiError("Agent name is required (letters, digits, space, _ . -; max 120).")
    role = b.get("role", existing.role if existing else "custom")
    if role not in ROLES:
        raise ApiError(f"role must be one of {', '.join(ROLES)}.")
    out = {"name": name, "role": role, "description": str(b.get("description", existing.description if existing else ""))[:500]}
    classes = {c["name"] for c in o.model["classes"]}
    known_caps = {t["capability"] for t in tools.catalog(o) if t.get("capability")} | {"delegate"}
    for key in ("capabilities", "permissions", "scope", "tools", "goals", "policies"):
        v = b.get(key, getattr(existing, key) if existing else [])
        if not isinstance(v, list) or any(not isinstance(x, str) for x in v) or len(v) > 100:
            raise ApiError(f"{key} must be a list of strings.")
        out[key] = v
    if any(p not in OPS for p in out["permissions"]):
        raise ApiError(f"permissions must be from: {', '.join(OPS)}.")
    if any(c not in classes for c in out["scope"]):
        raise ApiError("scope must list classes of the ontology.")
    unknown = [t for t in out["tools"] if not tools.get_spec(o, t)]
    if unknown:
        raise ApiError(f"Unknown tool(s): {', '.join(unknown)}.")
    badcap = [c for c in out["capabilities"] if c not in known_caps and not c.startswith("tool:")]
    if badcap:
        raise ApiError(f"Unknown capabilit{'y' if len(badcap) == 1 else 'ies'}: {', '.join(badcap)}. Known: {', '.join(sorted(known_caps))}.")
    bud = b.get("budget", existing.budget if existing else {})
    if not isinstance(bud, dict) or any(k not in ("maxSteps", "maxToolCalls", "maxCost") or not isinstance(v, (int, float)) or v <= 0 for k, v in bud.items()):
        raise ApiError("budget may contain positive numbers maxSteps, maxToolCalls, maxCost.")
    out["budget"] = bud
    out["enabled"] = bool(b.get("enabled", existing.enabled if existing else True))
    return out


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def agents(request, pk):
    o = get_ontology(request, pk)
    if request.method == "POST":
        _need(request, "agent_manage", "register agents")
        spec = validate_agent(o, body(request))
        if Agent.objects.filter(ontology=o, name__iexact=spec["name"]).exists():
            raise ApiError(f"An agent named '{spec['name']}' already exists.")
        a = Agent.objects.create(ontology=o, created_by=request.identity["user"], **spec)
        versioning.audit(o, "agent.registered", request.identity["user"], agent=a.name, role=a.role)
        return JsonResponse(agent_json(a), status=201)
    pend = ApprovalRequest.objects.filter(run__ontology=o, status="pending").count()
    return JsonResponse({"agents": [agent_json(a) for a in Agent.objects.filter(ontology=o)], "tools": tools.catalog(o), "roles": ROLES, "operations": OPS,
                         "runs": [engine.run_json(r, False) for r in AgentRun.objects.filter(ontology=o, parent__isnull=True)[:30]], "pendingApprovals": pend,
                         "mcpServers": [{"id": m.id, "name": m.name, "command": m.command, "discoveredAt": m.discovered_at.isoformat() if m.discovered_at else None,
                                         "serverInfo": m.server_info, "lastError": m.last_error} for m in McpServer.objects.filter(ontology=o)]})


@api
@requires("read")
@require_http_methods(["PUT", "DELETE"])
def agent_detail(request, pk, aid):
    o = get_ontology(request, pk)
    _need(request, "agent_manage", "manage agents")
    a = Agent.objects.filter(pk=aid, ontology=o).first()
    if not a:
        raise ApiError("Agent not found.", 404)
    if request.method == "DELETE":
        versioning.audit(o, "agent.deleted", request.identity["user"], agent=a.name)
        a.delete()
        return JsonResponse({"deleted": True})
    spec = validate_agent(o, body(request), a)
    if Agent.objects.filter(ontology=o, name__iexact=spec["name"]).exclude(pk=a.pk).exists():
        raise ApiError(f"An agent named '{spec['name']}' already exists.")
    for k, v in spec.items():
        setattr(a, k, v)
    a.save()
    versioning.audit(o, "agent.updated", request.identity["user"], agent=a.name)
    return JsonResponse(agent_json(a))


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def agent_memory(request, pk, aid):
    o = get_ontology(request, pk)
    a = Agent.objects.filter(pk=aid, ontology=o).first()
    if not a:
        raise ApiError("Agent not found.", 404)
    if request.method == "POST":
        _need(request, "write", "write agent memory")
        try:
            m = memory.remember(a, str(body(request).get("text", "")), kind=body(request).get("kind", "semantic"))
        except ValueError as e:
            raise ApiError(str(e)) from None
        return JsonResponse({"id": m.id}, status=201)
    q = request.GET.get("q")
    if q:
        return JsonResponse({"memories": memory.recall(a, q, 10)})
    return JsonResponse({"memories": [{"id": m.id, "kind": m.kind, "text": m.text, "concepts": m.concepts, "at": m.created_at.isoformat(), "runId": m.run_id}
                                      for m in AgentMemory.objects.filter(agent=a)[:100]]})


# -------------------------------------------------------------- tools / MCP

@api
@requires("read")
@require_http_methods(["GET", "POST"])
def tool_registry(request, pk):
    o = get_ontology(request, pk)
    if request.method == "POST":
        _need(request, "agent_manage", "register tools")
        b = body(request)
        name = str(b.get("name", "")).strip()
        if not NAME_OK(name) or name in tools.BUILTIN:
            raise ApiError("A unique tool name is required (not a built-in name).")
        if AgentTool.objects.filter(ontology=o, name=name).exists():
            raise ApiError(f"A tool named '{name}' already exists.")
        ttype = b.get("type", "api")
        spec = b.get("spec") or {}
        if ttype == "api":
            from ..fabric.http import ConnectorError, check_url

            try:
                check_url(str(spec.get("url", "")))
            except ConnectorError as e:
                raise ApiError(str(e)) from None
            if spec.get("method", "GET").upper() not in ("GET", "POST"):
                raise ApiError("method must be GET or POST.")
        elif ttype == "mcp":
            if not McpServer.objects.filter(ontology=o, name=spec.get("server")).exists():
                raise ApiError("spec.server must be a registered MCP server.")
        else:
            raise ApiError("type must be 'api' or 'mcp'.")
        if b.get("operation", "read") not in OPS:
            raise ApiError(f"operation must be one of {', '.join(OPS)}.")
        classes = {c["name"] for c in o.model["classes"]}
        if any(c not in classes for c in b.get("concepts") or []):
            raise ApiError("concepts must be classes of the ontology.")
        t = AgentTool.objects.create(ontology=o, name=name, type=ttype, description=str(b.get("description", ""))[:500], spec=spec, input_schema=b.get("inputSchema") or {},
                                     operation=b.get("operation", "read"), concepts=b.get("concepts") or [], risk=b.get("risk"), external=bool(b.get("external", ttype == "api")),
                                     cost=float(b.get("cost", 1.0)), status="pending")
        versioning.audit(o, "agent.tool.registered", request.identity["user"], tool=name, type=ttype)
        return JsonResponse(tools.get_spec(o, t.name), status=201)
    return JsonResponse({"tools": tools.catalog(o)})


@api
@requires("read")
@require_http_methods(["POST"])
def tool_govern(request, pk, name):
    """Governance of discovered/registered tools: an administrator approves or disables them."""
    o = get_ontology(request, pk)
    _need(request, "agent_manage", "govern tools")
    t = AgentTool.objects.filter(ontology=o, name=name).first()
    if not t:
        raise ApiError("Tool not found.", 404)
    action = body(request).get("action")
    if action not in ("approve", "disable"):
        raise ApiError("action must be 'approve' or 'disable'.")
    t.status, t.approved_by = ("approved" if action == "approve" else "disabled"), request.identity["user"]
    t.save()
    versioning.audit(o, f"agent.tool.{action}d", request.identity["user"], tool=name)
    return JsonResponse(tools.get_spec(o, name))


@api
@requires("read")
@require_http_methods(["POST"])
def mcp_servers(request, pk):
    o = get_ontology(request, pk)
    _need(request, "agent_manage", "register MCP servers")
    b = body(request)
    name = str(b.get("name", "")).strip()
    if not NAME_OK(name):
        raise ApiError("A server name is required.")
    try:
        mcp_client.resolve_command(b.get("command"), o.tenant)
    except mcp_client.McpError as e:
        raise ApiError(str(e), 400) from None
    if McpServer.objects.filter(ontology=o, name=name).exists():
        raise ApiError(f"MCP server '{name}' already exists.")
    s = McpServer.objects.create(ontology=o, name=name, command=b["command"])
    versioning.audit(o, "agent.mcp.registered", request.identity["user"], server=name)
    return JsonResponse({"id": s.id, "name": s.name}, status=201)


@api
@requires("read")
@require_http_methods(["POST"])
def mcp_discover(request, pk, sid):
    """Connect to the server, list its tools and register each as a PENDING tool (nothing is usable until an admin approves it)."""
    from django.utils import timezone

    o = get_ontology(request, pk)
    _need(request, "agent_manage", "discover MCP tools")
    s = McpServer.objects.filter(pk=sid, ontology=o).first()
    if not s:
        raise ApiError("MCP server not found.", 404)
    try:
        found = mcp_client.discover(mcp_client.resolve_command(s.command, o.tenant))
    except mcp_client.McpError as e:
        s.last_error = str(e)[:300]
        s.save()
        raise ApiError(str(e), 502) from None
    s.server_info, s.discovered_at, s.last_error = found["serverInfo"], timezone.now(), ""
    s.save()
    created = []
    for t in found["tools"]:
        name = f"{s.name}.{t['name']}"[:120]
        obj, new = AgentTool.objects.get_or_create(ontology=o, name=name, defaults={
            "type": "mcp", "description": t["description"], "spec": {"server": s.name, "tool": t["name"]}, "input_schema": t["inputSchema"], "status": "pending", "operation": "read"})
        if not new:
            obj.input_schema, obj.description = t["inputSchema"], t["description"]
            obj.save()
        created.append({"name": name, "status": obj.status, "new": new})
    versioning.audit(o, "agent.mcp.discovered", request.identity["user"], server=s.name, tools=len(created))
    return JsonResponse({"serverInfo": found["serverInfo"], "tools": created})


# -------------------------------------------------------- plan / run / approve

def _supervisor(o, b) -> Agent | None:
    if b.get("supervisor"):
        a = Agent.objects.filter(ontology=o, name=b["supervisor"], enabled=True).first()
        if not a:
            raise ApiError(f"Supervisor '{b['supervisor']}' not found or disabled.", 404)
        return a
    return Agent.objects.filter(ontology=o, role="supervisor", enabled=True).first()


@api
@requires("read")
@require_http_methods(["POST"])
def agent_plan(request, pk):
    o = get_ontology(request, pk)
    b = body(request)
    try:
        return JsonResponse(planner.plan(o, b.get("goal", ""), _supervisor(o, b)))
    except ValueError as e:
        raise ApiError(str(e)) from None


@api
@requires("read")
@require_http_methods(["POST"])
def agent_authorize(request, pk):
    """Dry-run: would this agent be allowed to call this tool like that? (no side effects)"""
    o = get_ontology(request, pk)
    b = body(request)
    a = Agent.objects.filter(ontology=o, name=b.get("agent", "")).first()
    spec = tools.get_spec(o, b.get("tool", ""))
    if not a or not spec:
        raise ApiError("agent and tool must exist.", 404)
    run = AgentRun(ontology=o, goal="(authorisation check)", requested_by=request.identity["user"], requester_role=request.identity["role"], plan={})
    step = AgentStep_stub(a, spec, b)
    return JsonResponse(engine.authorize(run, step, b.get("args") or {}))


def AgentStep_stub(agent, spec, b):
    from .models import AgentStep

    return AgentStep(agent=agent, tool=spec["name"], args=b.get("args") or {}, operation=b.get("operation") or spec["operation"], concepts=b.get("concepts") or [])


@api
@requires("read")
@require_http_methods(["POST"])
def agent_execute(request, pk):
    o = get_ontology(request, pk)
    b = body(request)
    ident = request.identity
    try:
        run = engine.create_run(o, b.get("goal", ""), user=ident["user"], role=ident["role"], supervisor=_supervisor(o, b), steps=b.get("steps"))
        if b.get("planOnly"):
            return JsonResponse(engine.run_json(run))
        run = engine.execute(run, user=ident["user"])
    except engine.EngineError as e:
        raise ApiError(str(e)) from None
    versioning.audit(o, "agent.run", ident["user"], run=run.id, status=run.status, goal=run.goal[:200])
    return JsonResponse(engine.run_json(run), status=200)


@api
@requires("read")
@require_http_methods(["POST"])
def agent_resume(request, pk):
    """Run a planned run (planOnly) or continue one after approvals."""
    o = get_ontology(request, pk)
    run = _run(o, body(request).get("runId"))
    if run.requested_by != request.identity["user"] and not can(request.identity["role"], "review"):
        raise ApiError("Only the requester or a reviewer can resume a run.", 403)
    return JsonResponse(engine.run_json(engine.execute(run, user=request.identity["user"])))


def _run(o, rid) -> AgentRun:
    try:
        run = AgentRun.objects.filter(pk=int(rid), ontology=o).first()
    except (TypeError, ValueError):
        run = None
    if not run:
        raise ApiError("Run not found.", 404)
    return run


@api
@requires("read")
@require_http_methods(["GET"])
def runs(request, pk):
    o = get_ontology(request, pk)
    qs = AgentRun.objects.filter(ontology=o)
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET["status"])
    return JsonResponse({"runs": [engine.run_json(r, False) for r in qs[:100]]})


@api
@requires("read")
@require_http_methods(["GET"])
def run_detail(request, pk, run_id):
    o = get_ontology(request, pk)
    return JsonResponse(engine.run_json(_run(o, run_id)))


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def approvals(request, pk):
    o = get_ontology(request, pk)
    if request.method == "POST":
        return _decide(request, o)
    qs = ApprovalRequest.objects.filter(run__ontology=o).select_related("step", "run")
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET["status"])
    return JsonResponse({"approvals": [engine.approval_json(a) for a in qs[:100]]})


@api
@requires("read")
@require_http_methods(["POST"])
def agent_approve(request, pk):
    return _decide(request, get_ontology(request, pk))


def _decide(request, o):
    b = body(request)
    ident = request.identity
    a = ApprovalRequest.objects.select_related("step", "run").filter(pk=b.get("approvalId") or -1, run__ontology=o).first()
    if not a:
        raise ApiError("Approval request not found.", 404)
    try:
        engine.decide(a, user=ident["user"], role=ident["role"], decision=b.get("decision", ""), comment=str(b.get("comment", "")))
        run = engine.execute(a.run, user=ident["user"]) if (b.get("resume", True) and a.status != "pending") else a.run
    except engine.EngineError as e:
        raise ApiError(str(e), 403 if "reviewers" in str(e) or "Four-eyes" in str(e) else 400) from None
    return JsonResponse({"approval": engine.approval_json(a), "run": engine.run_json(run)})


@api
@requires("read")
@require_http_methods(["POST"])
def agent_recover(request, pk):
    o = get_ontology(request, pk)
    b = body(request)
    run = _run(o, b.get("runId"))
    if run.requested_by != request.identity["user"] and not can(request.identity["role"], "review"):
        raise ApiError("Only the requester or a reviewer can recover a run.", 403)
    try:
        return JsonResponse(engine.run_json(engine.recover(run, b.get("strategy", "retry"), b.get("steps"), request.identity["user"])))
    except engine.EngineError as e:
        raise ApiError(str(e)) from None


@api
@requires("read")
@require_http_methods(["POST"])
def agent_cancel(request, pk):
    o = get_ontology(request, pk)
    run = _run(o, body(request).get("runId"))
    if run.requested_by != request.identity["user"] and not can(request.identity["role"], "review"):
        raise ApiError("Only the requester or a reviewer can cancel a run.", 403)
    try:
        return JsonResponse(engine.run_json(engine.cancel(run, request.identity["user"])))
    except engine.EngineError as e:
        raise ApiError(str(e)) from None


# ------------------------------------------------- /api/v1/autonomous/* alias

def _alias_ont(request):
    from ..osplane.views import ont

    return ont(request).id


def make_alias(view, method_filter=None):
    @api
    @requires("read")
    def wrapper(request):
        return view(request, _alias_ont(request))

    wrapper.csrf_exempt = True
    return wrapper


@api
@requires("read")
@require_http_methods(["GET"])
def autonomous_manifest(request, pk=None):
    from ..osplane.models import Policy

    o = get_ontology(request, pk if pk is not None else _alias_ont(request))
    return JsonResponse({"name": "Prime Autonomous Enterprise Agent Platform", "release": "R14", "ontology": {"id": o.id, "name": o.name},
                         "agents": [agent_json(a) for a in Agent.objects.filter(ontology=o)], "tools": tools.catalog(o), "roles": ROLES,
                         "policies": [policy_engine.policy_json(p) for p in Policy.objects.filter(ontology=o, enabled=True)],
                         "endpoints": {"agents": "/api/v1/autonomous/agents/", "plan": "/api/v1/autonomous/plan/", "authorize": "/api/v1/autonomous/authorize/",
                                       "run": "/api/v1/autonomous/run/", "recover": "/api/v1/autonomous/recover/"},
                         "guarantees": ["every step is authorised by agent permissions + requester role + enterprise policy before it runs",
                                        "approvals follow the four-eyes principle and are bound to the exact tool arguments (execution contract)",
                                        "all steps are traced; failed steps can be retried or skipped; budgets cap cost and tool calls"]})


@api
@requires("read")
@require_http_methods(["POST"])
def agent_workflow(request, pk):
    """Export a plan (goal) or a finished run (runId) as an n8n workflow (JSON) or BPMN 2.0 (XML)."""
    from django.http import HttpResponse

    from . import workflow

    o = get_ontology(request, pk)
    b = body(request)
    fmt = b.get("format", "n8n")
    if b.get("runId"):
        r = _run(o, b["runId"])
        plan = {"goal": r.goal, "steps": [{"key": s.key, "title": s.title, "tool": s.tool, "args": s.args, "dependsOn": s.depends_on, "operation": s.operation,
                                          "concepts": s.concepts, "agent": s.agent.name if s.agent else None, "decision": s.decision} for s in r.steps.select_related("agent")]}
    else:
        try:
            plan = planner.plan(o, b.get("goal", ""), _supervisor(o, b))
        except ValueError as e:
            raise ApiError(str(e)) from None
    base = request.build_absolute_uri("/").rstrip("/")
    try:
        out = workflow.export(plan, fmt, o.id, base)
    except ValueError as e:
        raise ApiError(str(e)) from None
    return JsonResponse(out) if fmt == "n8n" else HttpResponse(out, content_type="application/xml")
