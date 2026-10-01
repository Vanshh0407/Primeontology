"""Tool registry: built-in semantic tools + governed API / MCP tools. Every call goes through here (and through policy first)."""
import json

from .. import query
from ..fabric import http as fabric_http
from ..fabric.models import FabricEntity, FabricSource
from ..fabric.sync import SyncBusy, sync_source
from ..rag import answer as rag_answer
from ..rag import retrieve as rag_retrieve
from ..twin import core as twin
from . import mcp_client
from .memory import recall, remember
from .models import AgentTool, McpServer


class ToolError(Exception):
    def __init__(self, msg, retryable=False):
        super().__init__(msg)
        self.retryable = retryable


def _ctx_role(ctx):
    return ctx.get("role", "viewer")


# ------------------------------------------------------------------ builtins

def t_search(ctx, a):
    return {"hits": query.search(ctx["ontology"].model, a["query"], int(a.get("limit", 10)))}


def t_rag_ask(ctx, a):
    out = rag_answer.answer(ctx["ontology"], a["question"], role=_ctx_role(ctx), use_llm=bool(a.get("useLlm")), k=int(a.get("k", 6)))
    return {"answer": out["answer"], "grounding": out["grounding"], "confidence": out["confidence"], "abstained": out["abstained"], "citations": out["citations"],
            "structured": out["structured"] and {"resultClass": out["structured"]["resultClass"], "total": out["structured"]["total"],
                                                 "entityIds": [m["entityId"] for m in out["structured"]["matches"]]}}


def t_rag_retrieve(ctx, a):
    out = rag_retrieve.retrieve(ctx["ontology"], a["question"], role=_ctx_role(ctx), k=int(a.get("k", 6)))
    return {"context": [{k: c.get(k) for k in ("cite", "kind", "title", "document", "heading", "text", "score")} for c in out["context"]], "confidence": out["confidence"]}


def t_entities(ctx, a):
    qs = FabricEntity.objects.filter(ontology=ctx["ontology"], status="active")
    if a.get("class"):
        qs = qs.filter(class_name=a["class"])
    if a.get("q"):
        qs = qs.filter(display_name__icontains=str(a["q"])[:100])
    lim = max(1, min(int(a.get("limit", 20)), 100))
    return {"total": qs.count(), "entities": [{"id": e.id, "class": e.class_name, "name": e.display_name, "canonical": e.canonical, "sources": e.record_count}
                                              for e in qs.order_by("id")[:lim]]}


def t_sync(ctx, a):
    qs = FabricSource.objects.filter(ontology=ctx["ontology"], enabled=True)
    if a.get("sourceId"):
        qs = qs.filter(pk=a["sourceId"])
    runs = []
    for s in qs[:20]:
        try:
            r = sync_source(s, trigger="agent", actor=ctx.get("user", "agent"))
            runs.append({"source": s.name, "status": r.status, "stats": r.stats, "error": r.error})
        except SyncBusy:
            runs.append({"source": s.name, "status": "busy"})
        except fabric_http.ConnectorError as e:
            raise ToolError(str(e), retryable=True) from None
    if not runs:
        raise ToolError("No matching enabled source.")
    return {"runs": runs}


def t_twin_snapshot(ctx, a):
    sn = twin.snapshot(ctx["ontology"], None, 10)
    return {"entities": sn["entities"]["total"], "byClass": sn["entities"]["byClass"], "alerts": sn["alerts"][:20], "processes": sn["processes"]}


def t_twin_simulate(ctx, a):
    try:
        return twin.simulate(ctx["ontology"], a.get("changes") or [], int(a.get("hops", 2)))
    except twin.TwinError as e:
        raise ToolError(str(e)) from None


def t_twin_event(ctx, a):
    try:
        ev = twin.ingest_event(ctx["ontology"], {"type": a.get("type"), "entityId": a.get("entityId"), "set": a.get("set"), "payload": a.get("payload"),
                                                 "source": "agent"}, ctx.get("user", "agent"))
    except twin.TwinError as e:
        raise ToolError(str(e)) from None
    return {"eventId": ev.id}


def t_recall(ctx, a):
    if not ctx.get("agent"):
        raise ToolError("Memory needs an agent.")
    return {"memories": recall(ctx["agent"], a["query"], int(a.get("limit", 5)))}


def t_remember(ctx, a):
    if not ctx.get("agent"):
        raise ToolError("Memory needs an agent.")
    return {"id": remember(ctx["agent"], a["text"], kind=a.get("kind", "semantic"), run_id=ctx.get("run_id")).id}


S = lambda **p: {"type": "object", "properties": p}  # noqa: E731
STR, INT, ARR, OBJ = {"type": "string"}, {"type": "integer"}, {"type": "array"}, {"type": "object"}

BUILTIN = {
    "ontology.search": dict(fn=t_search, operation="read", cost=0.2, capability="ontology.search", schema={**S(query=STR, limit=INT), "required": ["query"]}, description="Search concepts."),
    "rag.ask": dict(fn=t_rag_ask, operation="read", cost=1.0, capability="rag.ask", schema={**S(question=STR, useLlm={"type": "boolean"}, k=INT), "required": ["question"]},
                    description="Grounded question answering with citations."),
    "rag.retrieve": dict(fn=t_rag_retrieve, operation="read", cost=0.8, capability="rag.retrieve", schema={**S(question=STR, k=INT), "required": ["question"]},
                         description="Hybrid retrieval of context."),
    "fabric.entities": dict(fn=t_entities, operation="read", cost=0.3, capability="fabric.query", schema=S(**{"class": STR, "q": STR, "limit": INT}), description="Look up knowledge-fabric entities."),
    "fabric.sync": dict(fn=t_sync, operation="execute", cost=5.0, external=True, capability="fabric.sync", schema=S(sourceId=INT), description="Synchronise source systems."),
    "twin.snapshot": dict(fn=t_twin_snapshot, operation="read", cost=0.5, capability="twin.read", schema=S(), description="Digital twin overview."),
    "twin.simulate": dict(fn=t_twin_simulate, operation="read", cost=1.0, capability="twin.simulate", schema={**S(changes=ARR, hops=INT), "required": ["changes"]},
                          description="Non-destructive what-if simulation."),
    "twin.event": dict(fn=t_twin_event, operation="update", cost=1.0, capability="twin.write", schema={**S(type=STR, entityId=INT, set=OBJ, payload=OBJ), "required": ["type"]},
                       description="Record an event / attribute change in the twin."),
    "memory.recall": dict(fn=t_recall, operation="read", cost=0.2, capability="memory", schema={**S(query=STR, limit=INT), "required": ["query"]}, description="Recall agent memory."),
    "memory.remember": dict(fn=t_remember, operation="create", cost=0.2, capability="memory", schema={**S(text=STR, kind=STR), "required": ["text"]}, description="Store a memory."),
    "agent.delegate": dict(fn=None, operation="read", cost=0.5, capability="delegate", schema={**S(agent=STR, goal=STR), "required": ["agent", "goal"]},
                           description="Delegate a sub-goal to another agent (handled by the engine)."),
}


# ----------------------------------------------------------------- registry

def get_spec(ontology, name: str) -> dict | None:
    """Uniform tool description: {name, kind, operation, cost, external, risk, concepts, schema, description, status}."""
    if name in BUILTIN:
        b = BUILTIN[name]
        return {"name": name, "kind": "builtin", "operation": b["operation"], "cost": b["cost"], "external": b.get("external", False), "risk": None, "concepts": [],
                "schema": b["schema"], "description": b["description"], "status": "approved", "capability": b["capability"]}
    t = AgentTool.objects.filter(ontology=ontology, name=name).first()
    if not t:
        return None
    return {"name": t.name, "kind": t.type, "operation": t.operation, "cost": t.cost, "external": t.external, "risk": t.risk, "concepts": t.concepts, "schema": t.input_schema,
            "description": t.description, "status": t.status, "capability": f"tool:{t.name}", "spec": t.spec}


def catalog(ontology) -> list[dict]:
    out = [get_spec(ontology, n) for n in BUILTIN]
    out += [get_spec(ontology, t.name) for t in AgentTool.objects.filter(ontology=ontology)]
    return out


def validate_args(schema: dict, args: dict):
    if not isinstance(args, dict):
        raise ToolError("Tool arguments must be an object.")
    for r in schema.get("required", []):
        if args.get(r) in (None, ""):
            raise ToolError(f"Missing required argument '{r}'.")
    types = {"string": str, "integer": int, "array": list, "object": dict, "boolean": bool, "number": (int, float)}
    for k, p in (schema.get("properties") or {}).items():
        if k in args and args[k] is not None and p.get("type") in types:
            ok = isinstance(args[k], types[p["type"]]) and not (p["type"] in ("integer", "number") and isinstance(args[k], bool))
            if not ok:
                raise ToolError(f"Argument '{k}' must be of type {p['type']}.")


def call(ontology, name: str, args: dict, ctx: dict):
    spec = get_spec(ontology, name)
    if not spec:
        raise ToolError(f"Unknown tool '{name}'.")
    if spec["status"] != "approved":
        raise ToolError(f"Tool '{name}' is {spec['status']} (it must be approved by an administrator first).")
    validate_args(spec["schema"], args)
    ctx = {**ctx, "ontology": ontology}
    if spec["kind"] == "builtin":
        try:
            return BUILTIN[name]["fn"](ctx, args)
        except ToolError:
            raise
        except (KeyError, ValueError, TypeError) as e:
            raise ToolError(f"Bad arguments: {e}") from None
    if spec["kind"] == "api":
        return _call_api(spec, args)
    return _call_mcp(ontology, spec, args)


def _call_api(spec, args):
    url, method = spec["spec"].get("url", ""), spec["spec"].get("method", "GET").upper()
    try:
        if method == "GET":
            return fabric_http.json_request("GET", url, params={k: str(v) for k, v in args.items()})
        return fabric_http.json_request("POST", url, body=args)
    except fabric_http.ConnectorError as e:
        raise ToolError(str(e), retryable="HTTP 5" in str(e) or "timed out" in str(e) or "Cannot reach" in str(e)) from None


def _call_mcp(ontology, spec, args):
    srv = McpServer.objects.filter(ontology=ontology, name=spec["spec"].get("server")).first()
    if not srv:
        raise ToolError("The MCP server of this tool is no longer registered.")
    try:
        return mcp_client.call(mcp_client.resolve_command(srv.command, ontology.tenant), spec["spec"].get("tool", spec["name"]), args)
    except mcp_client.McpError as e:
        raise ToolError(str(e), retryable="exited" in str(e) or "did not answer" in str(e)) from None


def args_hash(tool: str, args: dict) -> str:
    import hashlib

    return hashlib.sha256(json.dumps([tool, args], sort_keys=True, default=str).encode()).hexdigest()
