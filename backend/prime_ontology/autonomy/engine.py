"""Autonomous execution engine: authorise -> (approve) -> contract -> execute -> trace -> recover -> evaluate.

Guarantees
  * every step is authorised against (1) the agent's own capabilities/permissions/scope, (2) the requester's role and
    (3) the enterprise policy engine - before it runs;
  * a step that needs approval does not run until enough DIFFERENT reviewers (never the requester) approve it;
  * the approval/authorisation is bound to the exact tool+arguments by an execution contract (hash); changed arguments => no run;
  * all of it is recorded in an append-only trace; failures are retried (when retryable) and can be recovered.
"""
import time
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.db import transaction

from .. import versioning
from ..identity import can
from ..osplane import policy as policy_engine
from ..validation import ancestors
from . import memory, planner, tools
from .models import Agent, AgentEvent, AgentRun, AgentStep, ApprovalRequest

MAX_DEPTH = 3
MAX_ATTEMPTS = 3
DEFAULTS = {"maxSteps": 30, "maxToolCalls": 30, "maxCost": 100.0}
TERMINAL_BAD = ("failed", "rejected", "blocked", "skipped")


class EngineError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc)


def trace(run, kind, step=None, **detail):
    AgentEvent.objects.create(run=run, step=step, kind=kind, detail=detail)


# ---------------------------------------------------------------- creation

def create_run(ontology, goal: str, *, user: str, role: str, supervisor: Agent | None = None, steps: list | None = None, parent=None,
               only_agent: Agent | None = None, limit_permissions: list | None = None) -> AgentRun:
    depth = parent.depth + 1 if parent else 0
    if depth > MAX_DEPTH:
        raise EngineError(f"Delegation is limited to {MAX_DEPTH} levels.")
    try:
        p = planner.plan(ontology, goal, supervisor, only_agent)
    except ValueError as e:
        raise EngineError(str(e)) from None
    if steps is not None:  # an explicit (user-edited) plan replaces the generated steps; it is still fully authorised step by step
        p["steps"] = _validate_steps(ontology, steps)
    if not p["steps"]:
        raise EngineError("Nothing to run: " + " ".join(p["notes"] or ["the plan has no steps."]))
    if len(p["steps"]) > DEFAULTS["maxSteps"]:
        raise EngineError(f"A plan may have at most {DEFAULTS['maxSteps']} steps.")
    if limit_permissions is not None:
        p["limitPermissions"] = limit_permissions
    if supervisor:
        p["memories"] = memory.recall(supervisor, goal, 3)
    run = AgentRun.objects.create(ontology=ontology, goal=goal, supervisor=supervisor, parent=parent, depth=depth, plan=p, requested_by=user, requester_role=role)
    for i, s in enumerate(p["steps"]):
        agent = Agent.objects.filter(ontology=ontology, name=s.get("agent")).first() if s.get("agent") else None
        AgentStep.objects.create(run=run, key=s["key"], ordinal=i, agent=agent, title=s.get("title", "")[:300], tool=s["tool"], args=s.get("args") or {},
                                 depends_on=s.get("dependsOn", []), operation=s.get("operation", "read"), concepts=s.get("concepts", []),
                                 status="pending" if agent else "blocked", error="" if agent else "No agent is able to perform this step.")
    trace(run, "planned", steps=len(p["steps"]), intent=p["intent"], notes=p["notes"])
    return run


def _validate_steps(ontology, steps):
    if not isinstance(steps, list) or not steps:
        raise EngineError("steps must be a non-empty list.")
    keys, out = set(), []
    classes = {c["name"] for c in ontology.model["classes"]}
    for s in steps:
        if not isinstance(s, dict) or not s.get("key") or not s.get("tool"):
            raise EngineError("Every step needs key and tool.")
        if s["key"] in keys:
            raise EngineError(f"Duplicate step key '{s['key']}'.")
        spec = tools.get_spec(ontology, s["tool"])
        if not spec:
            raise EngineError(f"Unknown tool '{s['tool']}'.")
        deps = s.get("dependsOn") or []
        if any(d not in keys for d in deps):
            raise EngineError(f"Step '{s['key']}' depends on a step that is not defined before it.")  # forward-only => no cycles
        cs = s.get("concepts") or []
        if any(c not in classes for c in cs):
            raise EngineError(f"Step '{s['key']}' lists unknown concepts.")
        agent = planner.choose_agent(ontology, s["tool"], cs)
        if s.get("agent"):
            agent = Agent.objects.filter(ontology=ontology, name=s["agent"], enabled=True).first()
            if not agent:
                raise EngineError(f"Agent '{s['agent']}' not found or disabled.")
        keys.add(s["key"])
        out.append({"key": s["key"], "title": s.get("title", s["tool"]), "tool": s["tool"], "args": s.get("args") or {}, "dependsOn": deps,
                    "operation": s.get("operation") or spec["operation"], "concepts": cs, "agent": agent.name if agent else None})
    return out


# ----------------------------------------------------------- authorisation

def _covers(model, scope, c):
    return c in scope or bool(set(scope) & ancestors(model, c))


def authorize(run: AgentRun, step: AgentStep, args: dict | None = None) -> dict:
    """The full decision for one step. Pure (writes nothing): used both by execution and by the /authorize dry-run endpoint."""
    o = run.ontology
    spec = tools.get_spec(o, step.tool)
    agent = step.agent
    reasons = []
    if not spec:
        return _deny(f"Unknown tool '{step.tool}'.")
    if spec["status"] != "approved":
        return _deny(f"Tool '{step.tool}' is {spec['status']}: an administrator must approve it first.")
    if agent is None or not agent.enabled:
        return _deny("The assigned agent is missing or disabled.")
    if not (step.tool in agent.tools or spec["capability"] in agent.capabilities):
        return _deny(f"Agent '{agent.name}' has neither the tool '{step.tool}' nor the capability '{spec['capability']}'.")
    perms = agent.permissions or ["read"]
    lim = run.plan.get("limitPermissions")
    if lim is not None:
        perms = [p for p in perms if p in lim]  # a delegate can never do more than its delegator
    if step.operation not in perms:
        return _deny(f"Agent '{agent.name}' is not permitted to '{step.operation}'.")
    if agent.scope:
        out = [c for c in step.concepts if not _covers(o.model, agent.scope, c)]
        if out:
            return _deny(f"{', '.join(out)} is outside the scope of agent '{agent.name}'.")
    if step.operation != "read" and not can(run.requester_role, "write"):
        return _deny(f"The requester's role ({run.requester_role}) cannot authorise changes.")
    records = len((args or step.args).get("changes") or []) if isinstance((args or step.args).get("changes"), list) else 1
    d = policy_engine.evaluate(o, actor=run.requested_by, actor_type="agent", role=run.requester_role, agent=agent.name, operation=step.operation, concepts=step.concepts,
                               tool=step.tool, external=spec["external"], tool_risk=spec["risk"], records=records)
    d["agentChecks"] = reasons or ["capability, permission and scope checks passed"]
    return d


def _deny(why: str) -> dict:
    return {"decision": "deny", "risk": {"score": 1.0, "level": "critical", "factors": []}, "matched": [{"policy": "agent-governance", "effect": "deny", "scope": "agent", "why": [why],
                                                                                                          "description": why}], "defaultedByRisk": False, "approvalsRequired": 0,
            "agentChecks": [why]}


# --------------------------------------------------------------- execution

def _resolve(args, outputs):
    """'$stepKey.path.0.field' placeholders refer to outputs of earlier steps."""
    if isinstance(args, dict):
        return {k: _resolve(v, outputs) for k, v in args.items()}
    if isinstance(args, list):
        return [_resolve(v, outputs) for v in args]
    if isinstance(args, str) and args.startswith("$") and len(args) > 1:
        key, *path = args[1:].split(".")
        cur = outputs.get(key)
        for p in path:
            try:
                cur = cur[int(p)] if isinstance(cur, list) else cur[p]
            except (KeyError, IndexError, ValueError, TypeError):
                raise EngineError(f"Cannot resolve {args}.") from None
        return cur
    return args


def _budget(run):
    b = dict(DEFAULTS)
    if run.supervisor and run.supervisor.budget:
        b.update({k: v for k, v in run.supervisor.budget.items() if k in b})
    return b


def execute(run: AgentRun, *, user: str = "", backoff: float | None = None) -> AgentRun:
    if run.status in ("succeeded", "cancelled"):
        return run
    backoff = getattr(settings, "PRIME_ONTOLOGY_AGENT_BACKOFF", 0.5) if backoff is None else backoff
    run.status = "running"
    run.save(update_fields=["status"])
    budget = _budget(run)
    while True:
        steps = list(run.steps.select_related("agent"))
        by_key = {s.key: s for s in steps}
        progress = False
        for s in steps:
            if s.status == "awaiting_approval" and s.tool == "agent.delegate":
                progress |= _refresh_delegate(run, s, user)
            if s.status != "pending":
                continue
            deps = [by_key[d] for d in s.depends_on if d in by_key]
            if any(d.status in TERMINAL_BAD for d in deps):
                s.status, s.error = "skipped", "A step it depends on did not succeed."
                s.save()
                trace(run, "step_skipped", s, reason=s.error)
                progress = True
                continue
            if any(d.status != "succeeded" for d in deps):
                continue
            if run.tool_calls >= budget["maxToolCalls"] or run.cost >= budget["maxCost"]:
                s.status, s.error = "blocked", f"Budget exhausted (calls {run.tool_calls}/{budget['maxToolCalls']}, cost {run.cost:.1f}/{budget['maxCost']})."
                s.save()
                trace(run, "budget_exceeded", s, calls=run.tool_calls, cost=run.cost)
                progress = True
                continue
            try:
                outputs = {k: v.output for k, v in by_key.items() if v.status == "succeeded"}  # fresh: earlier steps may have just finished
                args = _resolve(s.args, outputs) if not s.contract else s.args
            except EngineError as e:
                s.status, s.error = "failed", str(e)
                s.save()
                progress = True
                continue
            progress |= _run_step(run, s, args, user, backoff)
        run.refresh_from_db(fields=["cost", "tool_calls"])
        if not progress:
            break
    return finalize(run)


def _run_step(run, s: AgentStep, args: dict, user: str, backoff: float) -> bool:
    h = tools.args_hash(s.tool, args)
    if s.contract and s.contract.get("argsHash") != h:
        s.status, s.error = "blocked", "The arguments changed after authorisation; the execution contract no longer matches."
        s.save()
        trace(run, "contract_violation", s)
        return True
    if not s.contract:
        d = authorize(run, s, args)
        s.decision, s.args = d, args
        if d["decision"] == "deny":
            s.status, s.error = "blocked", "Denied by policy: " + "; ".join(m["description"] or m["policy"] for m in d["matched"] if m["effect"] == "deny")[:400]
            s.save()
            trace(run, "step_denied", s, decision=d)
            return True
        if d["decision"] == "require_approval":
            if not s.approvals.exists():
                ApprovalRequest.objects.create(run=run, step=s, requested_by=run.requested_by, risk=d["risk"], required=d["approvalsRequired"] or 1,
                                               reason="; ".join(m["description"] or m["policy"] for m in d["matched"])[:500] or f"{d['risk']['level']} risk action")
                trace(run, "approval_requested", s, risk=d["risk"], required=d["approvalsRequired"])
            s.status = "awaiting_approval"
            s.save()
            return True
        s.contract = {"argsHash": h, "tool": s.tool, "decision": "allow", "approvedBy": [], "risk": d["risk"], "issuedAt": now().isoformat()}
        s.save()
    return _call_with_retry(run, s, args, user, backoff)


def _call_with_retry(run, s, args, user, backoff) -> bool:
    spec = tools.get_spec(run.ontology, s.tool)
    s.status, s.started_at = "running", now()
    s.save()
    ctx = {"agent": s.agent, "role": run.requester_role, "user": f"{s.agent.name if s.agent else 'agent'} (for {run.requested_by})", "run_id": run.id}
    while True:
        s.attempts += 1
        try:
            if s.tool == "agent.delegate":
                out = _delegate(run, s, args, user)
                if out.get("status") == "awaiting_approval":
                    s.status, s.output = "awaiting_approval", out
                    s.save()
                    trace(run, "delegated_waiting", s, child=out["runId"])
                    return True
            else:
                out = tools.call(run.ontology, s.tool, args, ctx)
            s.output, s.status, s.error, s.finished_at, s.cost = _jsonable(out), "succeeded", "", now(), spec["cost"] if spec else 0
            s.save()
            AgentRun.objects.filter(pk=run.pk).update(cost=run.cost + s.cost, tool_calls=run.tool_calls + 1)
            run.cost, run.tool_calls = run.cost + s.cost, run.tool_calls + 1
            trace(run, "step_succeeded", s, attempts=s.attempts, cost=s.cost)
            return True
        except tools.ToolError as e:
            trace(run, "step_error", s, attempt=s.attempts, error=str(e)[:300], retryable=e.retryable)
            AgentRun.objects.filter(pk=run.pk).update(tool_calls=run.tool_calls + 1)
            run.tool_calls += 1
            if e.retryable and s.attempts < MAX_ATTEMPTS:
                time.sleep(backoff * (2 ** (s.attempts - 1)))
                continue
            s.status, s.error, s.finished_at = "failed", str(e)[:500], now()
            s.save()
            return True
        except Exception as e:  # noqa: BLE001 - a tool bug must not kill the run
            s.status, s.error, s.finished_at = "failed", f"Unexpected {type(e).__name__}: {str(e)[:300]}", now()
            s.save()
            trace(run, "step_crashed", s, error=s.error)
            return True


def _jsonable(x):
    import json

    return json.loads(json.dumps(x, default=str))


# -------------------------------------------------------------- delegation

def _delegate(run, s, args, user):
    target = Agent.objects.filter(ontology=run.ontology, name=args["agent"], enabled=True).first()
    if not target:
        raise tools.ToolError(f"Agent '{args['agent']}' not found or disabled.")
    if s.output and s.output.get("runId"):  # resumed
        child = AgentRun.objects.get(pk=s.output["runId"])
    else:
        try:
            child = create_run(run.ontology, args["goal"], user=run.requested_by, role=run.requester_role, supervisor=target, parent=run, only_agent=target,
                               limit_permissions=(s.agent.permissions if s.agent else []) or ["read"])
        except EngineError as e:
            raise tools.ToolError(str(e)) from None
        trace(run, "delegated", s, child=child.id, to=target.name)
    child = execute(child, user=user)
    if child.status == "awaiting_approval":
        return {"runId": child.id, "status": "awaiting_approval"}
    if child.status not in ("succeeded", "partial"):
        raise tools.ToolError(f"The delegate run {child.status}: {child.error or 'see its trace'}")
    return {"runId": child.id, "status": child.status, "result": child.result}


def _refresh_delegate(run, s, user) -> bool:
    child = AgentRun.objects.filter(pk=(s.output or {}).get("runId")).first()
    if not child:
        return False
    child = execute(child, user=user)
    if child.status == "awaiting_approval":
        return False
    if child.status in ("succeeded", "partial"):
        s.status, s.output, s.error, s.finished_at = "succeeded", {"runId": child.id, "status": child.status, "result": child.result}, "", now()
    else:
        s.status, s.error, s.finished_at = "failed", f"The delegate run {child.status}.", now()
    s.save()
    trace(run, "delegate_finished", s, child=child.id, status=child.status)
    return True


# ---------------------------------------------------------------- approval

def decide(approval: ApprovalRequest, *, user: str, role: str, decision: str, comment: str = "") -> ApprovalRequest:
    if approval.status != "pending":
        raise EngineError(f"This approval is already {approval.status}.")
    if decision not in ("approve", "reject"):
        raise EngineError("decision must be 'approve' or 'reject'.")
    if not can(role, "review"):
        raise EngineError("Only reviewers and administrators can decide approvals.")
    if user == approval.requested_by:
        raise EngineError("Four-eyes principle: you cannot approve your own request.")
    if any(d["by"] == user for d in approval.decisions):
        raise EngineError("You already decided this request.")
    approval.decisions = [*approval.decisions, {"by": user, "role": role, "decision": decision, "comment": comment[:500], "at": now().isoformat()}]
    step, run = approval.step, approval.run
    if decision == "reject":
        approval.status = "rejected"
        step.status, step.error = "rejected", f"Rejected by {user}" + (f": {comment[:200]}" if comment else "")
        step.save()
    elif sum(1 for d in approval.decisions if d["decision"] == "approve") >= approval.required:
        approval.status = "approved"
        args = step.args
        step.contract = {"argsHash": tools.args_hash(step.tool, args), "tool": step.tool, "decision": "approved", "approvedBy": [d["by"] for d in approval.decisions if d["decision"] == "approve"],
                         "risk": approval.risk, "issuedAt": now().isoformat()}
        step.status = "pending"  # the engine runs it on resume, bound to this exact contract
        step.save()
    approval.save()
    trace(run, f"approval_{decision}", step, by=user, comment=comment[:200], status=approval.status)
    versioning.audit(run.ontology, f"agent.approval.{decision}", user, run=run.id, step=step.key, tool=step.tool)
    return approval


# ----------------------------------------------------------------- finish

def summarize(run) -> dict:
    parts, brief = [], {}
    for s in run.steps.all():
        o = s.output or {}
        if s.status != "succeeded":
            brief[s.key] = {"status": s.status, "error": s.error}
            continue
        if s.tool == "rag.ask":
            parts.append(o.get("answer", ""))
            brief[s.key] = {"status": "succeeded", "grounding": o.get("grounding"), "confidence": (o.get("confidence") or {}).get("score")}
        elif s.tool == "twin.simulate":
            sm = o.get("summary", {})
            parts.append(f"Simulation (nothing was changed): {sm.get('derivedChanged', 0)} derived value(s) change, {sm.get('newAlerts', 0)} new alert(s), {sm.get('clearedAlerts', 0)} cleared, "
                         f"{sm.get('impactedEntities', 0)} entities affected.")
            brief[s.key] = {"status": "succeeded", **sm}
        elif s.tool == "fabric.entities":
            parts.append(f"Found {o.get('total', 0)} matching entit{'y' if o.get('total') == 1 else 'ies'} in the knowledge fabric.")
            brief[s.key] = {"status": "succeeded", "total": o.get("total")}
        elif s.tool == "twin.event":
            parts.append(f"Recorded event #{o.get('eventId')} in the digital twin.")
            brief[s.key] = {"status": "succeeded", "eventId": o.get("eventId")}
        elif s.tool == "fabric.sync":
            parts.append("Synchronised " + ", ".join(f"{r['source']} ({r['status']})" for r in o.get("runs", [])) + ".")
            brief[s.key] = {"status": "succeeded"}
        else:
            parts.append(f"{s.tool} completed.")
            brief[s.key] = {"status": "succeeded"}
    return {"summary": " ".join(p for p in parts if p), "steps": brief}


def evaluate_run(run) -> dict:
    steps = list(run.steps.all())
    ran = [s for s in steps if s.status not in ("skipped",)]
    ok = [s for s in steps if s.status == "succeeded"]
    attempts = sum(s.attempts for s in steps) or 1
    rag = [s.output.get("grounding") for s in ok if s.tool == "rag.ask" and s.output]
    ground = {"grounded": 1.0, "partially_grounded": 0.5, "ungrounded": 0.0}
    violations = sum(1 for s in ok if s.operation != "read" and not s.contract)
    scores = {"goalCompletion": round(len(ok) / (len(ran) or 1), 2), "policyCompliance": 1.0 if not violations else round(1 - violations / len(ok), 2),
              "toolSuccess": round(min(1.0, len(ok) / attempts), 2), "groundedness": round(sum(ground.get(g, 0) for g in rag) / len(rag), 2) if rag else None,
              "approvalsRespected": all(s.contract.get("approvedBy") for s in ok if s.decision.get("decision") == "require_approval")}
    nums = [v for v in (scores["goalCompletion"], scores["policyCompliance"], scores["toolSuccess"], scores["groundedness"]) if v is not None]
    scores["overall"] = round(sum(nums) / len(nums), 2)
    return scores


def finalize(run: AgentRun) -> AgentRun:
    steps = list(run.steps.all())
    st = [s.status for s in steps]
    if any(s in ("awaiting_approval",) for s in st) or (any(s == "pending" for s in st) and any(s == "awaiting_approval" for s in st)):
        run.status = "awaiting_approval"
    elif all(s == "succeeded" for s in st):
        run.status = "succeeded"
    elif any(s == "succeeded" for s in st):
        run.status = "partial"
    elif st and all(s == "rejected" for s in st):
        run.status = "rejected"
    else:
        run.status = "failed"
    bad = [s for s in steps if s.status in TERMINAL_BAD and s.status != "skipped"]
    run.error = "; ".join(f"{s.key}: {s.error}" for s in bad)[:1000]
    run.result = summarize(run)
    if run.status != "awaiting_approval":
        run.finished_at = now()
        run.evaluation = evaluate_run(run)
        _write_memory(run)
    run.save()
    trace(run, "run_" + run.status, cost=run.cost, calls=run.tool_calls)
    return run


def _write_memory(run):
    agents = {s.agent for s in run.steps.all() if s.agent} | ({run.supervisor} if run.supervisor else set())
    text = f"Goal '{run.goal[:200]}' finished {run.status}. {run.result.get('summary', '')[:500]}"
    for a in agents:
        try:
            memory.remember(a, text, kind="episodic", run_id=run.id)
        except Exception:  # noqa: BLE001 - memory is best-effort
            pass


# ---------------------------------------------------------------- recovery

def recover(run: AgentRun, strategy: str = "retry", keys: list[str] | None = None, user: str = "") -> AgentRun:
    if strategy not in ("retry", "skip"):
        raise EngineError("strategy must be 'retry' or 'skip'.")
    if run.status in ("succeeded", "cancelled"):
        raise EngineError(f"The run is already {run.status}.")
    changed = 0
    with transaction.atomic():
        steps = list(run.steps.all())
        failed = [s for s in steps if s.status in ("failed", "blocked") and (not keys or s.key in keys)]
        for s in failed:
            if strategy == "retry":
                if s.status == "blocked" and "No agent" in s.error:
                    continue  # needs a human to assign an agent; retrying cannot help
                if s.contract and "arguments changed" in (s.error or ""):
                    s.contract = {}
                s.status, s.error, s.attempts = "pending", "", 0
            else:
                s.status, s.error = "skipped", f"Skipped during recovery by {user}."
            s.save()
            changed += 1
        if strategy == "retry":  # dependents that were skipped because of these steps get another chance
            for s in steps:
                if s.status == "skipped" and "depends on" in s.error:
                    s.status, s.error = "pending", ""
                    s.save()
        run.error = ""
        run.finished_at = None
        run.save()
    if not changed:
        raise EngineError("There is nothing to recover (no failed or blocked steps).")
    trace(run, "recovery", strategy=strategy, steps=changed, by=user)
    return execute(run, user=user)


def cancel(run: AgentRun, user: str):
    if run.status in ("succeeded", "cancelled"):
        raise EngineError(f"The run is already {run.status}.")
    run.steps.filter(status__in=["pending", "awaiting_approval"]).update(status="skipped", error=f"Cancelled by {user}.")
    run.approvals.filter(status="pending").update(status="rejected")
    run.status, run.finished_at = "cancelled", now()
    run.save()
    trace(run, "cancelled", by=user)
    return run


def run_json(run: AgentRun, full=True) -> dict:
    d = {"id": run.id, "goal": run.goal, "status": run.status, "supervisor": run.supervisor.name if run.supervisor else None, "depth": run.depth, "parent": run.parent_id,
         "cost": run.cost, "toolCalls": run.tool_calls, "requestedBy": run.requested_by, "result": run.result, "evaluation": run.evaluation, "error": run.error,
         "createdAt": run.created_at.isoformat(), "finishedAt": run.finished_at.isoformat() if run.finished_at else None}
    if full:
        d["plan"] = run.plan
        d["steps"] = [{"key": s.key, "title": s.title, "agent": s.agent.name if s.agent else None, "tool": s.tool, "args": s.args, "dependsOn": s.depends_on,
                       "operation": s.operation, "status": s.status, "decision": s.decision, "contract": s.contract, "output": s.output, "error": s.error,
                       "attempts": s.attempts, "cost": s.cost} for s in run.steps.select_related("agent")]
        d["approvals"] = [approval_json(a) for a in run.approvals.select_related("step")]
        d["trace"] = [{"id": e.id, "kind": e.kind, "step": e.step.key if e.step else None, "detail": e.detail, "at": e.at.isoformat()} for e in run.trace.select_related("step")]
        d["children"] = [c.id for c in run.children.all()]
    return d


def approval_json(a: ApprovalRequest) -> dict:
    return {"id": a.id, "run": a.run_id, "step": a.step.key, "tool": a.step.tool, "args": a.step.args, "requestedBy": a.requested_by, "reason": a.reason, "risk": a.risk,
            "required": a.required, "decisions": a.decisions, "status": a.status, "createdAt": a.created_at.isoformat()}
