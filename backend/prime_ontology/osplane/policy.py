"""Enterprise policy engine + risk assessment.

decision = deny > require_approval > allow across every matching policy (plus the ontology-level policies of R10).
With no matching policy the decision comes from the RISK of the action: low/medium allow, high needs approval, critical is denied
(secure by default - an agent can never perform a critical action just because nobody wrote a rule).
"""
from .. import agent as agent_mod
from ..validation import ancestors
from .models import Policy

LEVELS = ["low", "medium", "high", "critical"]
RANK = {"allow": 0, "require_approval": 1, "deny": 2}
OP_RISK = {"read": 0.1, "create": 0.4, "update": 0.5, "execute": 0.6, "approve": 0.6, "delete": 0.9}
CLASSIFICATION_RISK = {"public": 0.0, "internal": 0.0, "confidential": 0.15, "restricted": 0.3}


class PolicyError(ValueError):
    pass


def risk(operation: str, *, tool_risk: float | None = None, external: bool = False, records: int = 1, classification: str = "internal",
         concepts_sensitive: bool = False) -> dict:
    base = OP_RISK.get(operation, 0.5) if tool_risk is None else max(tool_risk, 0.0)
    score, factors = base, [f"operation '{operation}' = {base:.2f}"]
    if records > 100:
        score += 0.2
        factors.append(f"bulk ({records} records) +0.20")
    elif records > 10:
        score += 0.1
        factors.append(f"{records} records +0.10")
    if external:
        score += 0.1
        factors.append("reaches an external system +0.10")
    c = CLASSIFICATION_RISK.get(classification, 0.0)
    if c:
        score += c
        factors.append(f"{classification} data +{c:.2f}")
    if concepts_sensitive:
        score += 0.15
        factors.append("sensitive concept +0.15")
    score = min(score, 1.0)
    level = "low" if score < 0.3 else "medium" if score < 0.6 else "high" if score < 0.8 else "critical"
    return {"score": round(score, 2), "level": level, "factors": factors}


def validate(model: dict, p: dict) -> dict:
    name = str(p.get("name", "")).strip()
    if not name or len(name) > 120:
        raise PolicyError("Policy name is required.")
    if p.get("effect") not in RANK:
        raise PolicyError("effect must be allow, require_approval or deny.")
    if p.get("scope", "agent") not in ("agent", "tool", "approval", "data", "action"):
        raise PolicyError("scope must be agent, tool, approval, data or action.")
    classes = {c["name"] for c in model["classes"]}
    concepts = p.get("concepts") or []
    if not isinstance(concepts, list) or any(c not in classes for c in concepts):
        raise PolicyError("concepts must be classes of the ontology.")
    for k in ("operations", "tools", "classifications"):
        if not isinstance(p.get(k) or [], list):
            raise PolicyError(f"{k} must be a list.")
    actors = p.get("actors") or {}
    if not isinstance(actors, dict):
        raise PolicyError("actors must be an object like {\"roles\": [...], \"agents\": [...]}.")
    if p.get("riskAtLeast") and p["riskAtLeast"] not in LEVELS:
        raise PolicyError("riskAtLeast must be low, medium, high or critical.")
    try:
        approvals = int(p.get("approvalsRequired", 1))
    except (TypeError, ValueError):
        raise PolicyError("approvalsRequired must be an integer.") from None
    if not 1 <= approvals <= 5:
        raise PolicyError("approvalsRequired must be between 1 and 5.")
    return {"name": name, "scope": p.get("scope", "agent"), "effect": p["effect"], "description": str(p.get("description", ""))[:500], "actors": actors,
            "operations": p.get("operations") or [], "concepts": concepts, "tools": p.get("tools") or [], "risk_at_least": p.get("riskAtLeast", ""),
            "classifications": p.get("classifications") or [], "approvals_required": approvals, "priority": int(p.get("priority", 100)),
            "enabled": bool(p.get("enabled", True))}


def policy_json(p: Policy) -> dict:
    return {"id": p.id, "name": p.name, "scope": p.scope, "effect": p.effect, "description": p.description, "actors": p.actors, "operations": p.operations,
            "concepts": p.concepts, "tools": p.tools, "riskAtLeast": p.risk_at_least, "classifications": p.classifications, "approvalsRequired": p.approvals_required,
            "priority": p.priority, "enabled": p.enabled}


def _covers(model, listed, concept):
    return concept in listed or bool(set(listed) & ancestors(model, concept))


def _matches(model, p: Policy, ctx: dict) -> list[str] | None:
    """Returns the reasons it matched, or None."""
    if p.operations and "*" not in p.operations and ctx["operation"] not in p.operations:
        return None
    a = p.actors or {}
    if a.get("roles") and ctx.get("role") not in a["roles"]:
        return None
    if a.get("agents") and ctx.get("agent") not in a["agents"]:
        return None
    if a.get("actorTypes") and ctx.get("actorType", "user") not in a["actorTypes"]:
        return None
    if p.tools and ctx.get("tool") not in p.tools:
        return None
    if p.classifications and ctx.get("classification", "internal") not in p.classifications:
        return None
    if p.risk_at_least and LEVELS.index(ctx["riskLevel"]) < LEVELS.index(p.risk_at_least):
        return None
    why = []
    if p.concepts:
        cov = [c for c in ctx.get("concepts", []) if _covers(model, p.concepts, c)]
        if not cov:
            return None
        why.append(f"concepts {', '.join(cov)}")
    return why or ["applies generally"]


def evaluate(ontology, *, actor: str = "", actor_type: str = "user", role: str = "viewer", agent: str = "", operation: str = "read", concepts=None,
             tool: str = "", classification: str = "internal", records: int = 1, external: bool = False, tool_risk: float | None = None, scope: str | None = None) -> dict:
    model = ontology.model
    concepts = list(concepts or [])
    rk = risk(operation, tool_risk=tool_risk, external=external, records=records, classification=classification)
    ctx = {"operation": operation, "role": role, "agent": agent, "actorType": actor_type, "tool": tool, "classification": classification, "concepts": concepts,
           "riskLevel": rk["level"]}
    matched, decision, approvals = [], "allow", 1
    for p in Policy.objects.filter(ontology=ontology, enabled=True):
        if scope and p.scope != scope and p.scope not in ("agent", "action"):
            continue
        why = _matches(model, p, ctx)
        if why is None:
            continue
        matched.append({"policy": p.name, "effect": p.effect, "scope": p.scope, "why": why, "description": p.description})
        if RANK[p.effect] > RANK[decision]:
            decision = p.effect
        if p.effect == "require_approval":
            approvals = max(approvals, p.approvals_required)
    # the R10 ontology-level policies keep working and are folded in
    reg = agent_mod.registry(model)
    if reg["policies"] and concepts:
        r10 = agent_mod.evaluate_policies(model, reg, concepts, operation, role if actor_type == "user" else (agent or role))
        for m in r10["matched"]:
            matched.append({**m, "scope": "ontology", "why": ["ontology-level policy"]})
        if RANK[r10["decision"]] > RANK[decision]:
            decision = r10["decision"]
    default = False
    if not matched:  # nothing explicit: the risk decides
        decision = {"low": "allow", "medium": "allow", "high": "require_approval", "critical": "deny"}[rk["level"]]
        default = True
    if decision == "require_approval" and rk["level"] == "critical":
        approvals = max(approvals, 2)  # four-eyes for critical actions
    return {"decision": decision, "risk": rk, "matched": matched, "defaultedByRisk": default, "approvalsRequired": approvals if decision == "require_approval" else 0,
            "context": {"actor": actor, "actorType": actor_type, "role": role, "agent": agent, "operation": operation, "concepts": concepts, "tool": tool,
                        "classification": classification}}
