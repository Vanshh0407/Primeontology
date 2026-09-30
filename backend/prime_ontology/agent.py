"""Ontology-powered agent layer (R10): semantic planner, tool/policy registry, context packs.

The ontology is the semantic control plane: a request is grounded to concepts,
connected through relationships, bound to registered tools/workflows/MCP
servers, and checked against ontology-level policies BEFORE execution.
"""
import re

from . import query
from .validation import ancestors

WRITE_VERBS = {"create": "create", "add": "create", "update": "update", "change": "update", "modify": "update",
               "delete": "delete", "remove": "delete", "cancel": "delete", "approve": "approve", "sign": "approve",
               "send": "execute", "run": "execute", "trigger": "execute", "notify": "execute"}
EFFECT_RANK = {"allow": 0, "require_approval": 1, "deny": 2}


def registry(model: dict) -> dict:
    a = model.get("agentic") or {}
    return {"tools": a.get("tools", []), "policies": a.get("policies", []), "rules": a.get("rules", [])}


def validate_registry(model: dict, reg: dict) -> list[str]:
    errs, names = [], {c["name"] for c in model["classes"]}
    for t in reg.get("tools", []):
        if not t.get("name"):
            errs.append("Every tool needs a name.")
        for c in t.get("concepts", []):
            if c not in names:
                errs.append(f"Tool '{t.get('name')}' references unknown concept '{c}'.")
        if t.get("type") not in (None, "api", "mcp", "workflow", "function"):
            errs.append(f"Tool '{t.get('name')}' has invalid type.")
    for p in reg.get("policies", []):
        if p.get("effect") not in EFFECT_RANK:
            errs.append(f"Policy '{p.get('name')}' effect must be allow, require_approval or deny.")
        for c in p.get("concepts", []):
            if c not in names:
                errs.append(f"Policy '{p.get('name')}' references unknown concept '{c}'.")
    return errs


def _covers(model, listed, concept):
    """A tool/policy that lists a class also covers its subclasses."""
    return concept in listed or bool(set(listed) & ancestors(model, concept))


def detect_operation(text: str) -> str:
    for w in re.findall(r"[a-z]+", text.lower()):
        if w in WRITE_VERBS:
            return WRITE_VERBS[w]
    return "read"


def evaluate_policies(model: dict, reg: dict, concepts: list[str], operation: str, role: str) -> dict:
    hits, decision = [], "allow"
    for p in reg["policies"]:
        if p.get("operations") and operation not in p["operations"] and "*" not in p["operations"]:
            continue
        if p.get("roles") and role not in p["roles"]:
            continue
        cov = [c for c in concepts if _covers(model, p.get("concepts", []), c)]
        if not cov:
            continue
        hits.append({"policy": p["name"], "effect": p["effect"], "concepts": cov, "description": p.get("description", "")})
        if EFFECT_RANK[p["effect"]] > EFFECT_RANK[decision]:
            decision = p["effect"]
    return {"decision": decision, "matched": hits}


def plan(model: dict, request: str, role: str = "agent", base_iri: str = "") -> dict:
    """Semantic planning: request -> concepts -> path -> tools -> policy decision -> ordered steps."""
    concepts = query.ground(model, request)
    if not concepts:
        return {"request": request, "grounded": False, "concepts": [], "steps": [], "decision": "clarify",
                "message": "No ontology concepts recognised; ask the user to clarify which business objects they mean."}
    seq = [c["class"] for c in concepts]
    chain, hops = [seq[0]], []
    for a, b in zip(seq, seq[1:]):
        if a == b:
            continue
        path = query.find_path(model, a, b)
        if path is None:
            return {"request": request, "grounded": True, "concepts": seq, "steps": [], "decision": "unreachable",
                    "message": f"The ontology has no relationship path between {a} and {b}."}
        hops.extend(path)
        chain.extend(h["to"] for h in path)
    op = detect_operation(request)
    reg = registry(model)
    policy = evaluate_policies(model, reg, list(dict.fromkeys(chain)), op, role)
    steps, used = [], set()
    ordered = list(dict.fromkeys(chain))
    for i, cls in enumerate(ordered):
        step_op = op if i == len(ordered) - 1 else "read"
        tools = [t for t in reg["tools"] if _covers(model, t.get("concepts", []), cls) and t["name"] not in used
                 and (not t.get("operations") or step_op in t["operations"])]
        bind = tools[0] if tools else None
        if bind:
            used.add(bind["name"])
        via = next((h for h in hops if h["to"] == cls), None)
        steps.append({"step": len(steps) + 1, "concept": cls, "operation": step_op,
                      "via": None if not via else {"from": via["from"], "relationship": via["property"], "inverse": not via["forward"]},
                      "tool": None if not bind else {k: bind.get(k) for k in ("name", "type", "endpoint", "description")},
                      "status": "bound" if bind else "unbound"})
    unbound = [s["concept"] for s in steps if s["status"] == "unbound"]
    decision = policy["decision"]
    return {"request": request, "grounded": True, "operation": op, "concepts": seq, "path": hops,
            "explanation": " → ".join(dict.fromkeys(chain)), "steps": steps, "policy": policy,
            "decision": "blocked" if decision == "deny" else "needs_approval" if decision == "require_approval" else
            ("ready" if not unbound else "partial"),
            "unboundConcepts": unbound}


def context_pack(model: dict, request: str | None = None, max_classes=40) -> dict:
    """Compact semantic context to inject into an agent/LLM prompt."""
    names = [c["class"] for c in query.ground(model, request)] if request else []
    focus = set(names)
    for n in list(focus):
        focus |= ancestors(model, n)
        for p in model["objectProperties"]:
            if p["domain"] == n:
                focus.add(p["range"])
            if p["range"] == n:
                focus.add(p["domain"])
    classes = [c for c in model["classes"] if not focus or c["name"] in focus][:max_classes]
    cn = {c["name"] for c in classes}
    lines = []
    for c in classes:
        head = f'- {c["name"]}' + (f' (is a {", ".join(c["parents"])})' if c.get("parents") else "") + (f': {c["comment"]}' if c.get("comment") else "")
        lines.append(head)
        for p in model["dataProperties"]:
            if p["domain"] == c["name"]:
                lines.append(f'    • {p["name"]} ({p["datatype"]}{", required" if p.get("required") else ""})')
        for p in model["objectProperties"]:
            if p["domain"] == c["name"] and p["range"] in cn:
                lines.append(f'    → {p["name"]} → {p["range"]} [{p.get("cardinality", "")}]')
    reg = registry(model)
    return {"concepts": names, "text": "ONTOLOGY:\n" + "\n".join(lines) + ("\nPOLICIES:\n" + "\n".join(
        f'- {p["name"]}: {p["effect"]} on {", ".join(p.get("concepts", []))} [{", ".join(p.get("operations", ["*"]))}]'
        for p in reg["policies"]) if reg["policies"] else ""),
            "classes": [c["name"] for c in classes], "tools": [t["name"] for t in reg["tools"]]}
