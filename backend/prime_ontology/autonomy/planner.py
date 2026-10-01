"""Multi-agent planner: decomposes a goal into a DAG of steps and assigns each to the agent best suited for it.

Roles: supervisor (plans / merges), research (RAG), data (fabric lookups), simulation (twin what-if), action (changes the world).
Deterministic and inspectable - the plan is data the user sees (and can edit) before anything runs.
"""
import re

from .. import agent as agent_mod
from .. import query
from ..rag.retrieve import get_entity_graph
from ..twin.core import _declared_props
from ..validation import ancestors
from .models import Agent
from .tools import get_spec

ROLE_FOR = {"rag.ask": "research", "rag.retrieve": "research", "ontology.search": "research", "fabric.entities": "data", "fabric.sync": "action",
            "twin.simulate": "simulation", "twin.snapshot": "simulation", "twin.event": "action", "memory.recall": "research", "memory.remember": "research"}
WHATIF = re.compile(r"\b(what[ -]?if|simulate|suppose|scenario|what would happen)\b", re.I)
APPLY = re.compile(r"\b(then|and)\s+(apply|commit|do it|make it so|update (?:it|them)|set (?:it|them))\b", re.I)
SYNC = re.compile(r"\b(sync|synchroni[sz]e|refresh|re-?import)\b.*\b(source|sources|system|systems|data|sap|salesforce|odoo|erp|crm)\b", re.I)
UPDATE = re.compile(r"\b(update|change|set|modify)\b", re.I)


def _camel_tokens(name: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[A-Z]?[a-z]+|\d+", name.replace("_", " ").replace("-", " "))}


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse_change(ontology, text: str) -> dict | None:
    """'what if Acme credit limit goes to 200' / 'increase Acme credit limit by 10%' -> {entityId, property, op, value} or None."""
    g = get_entity_graph(ontology)
    ids = g.mentioned(text)
    if not ids:
        return None
    ent = g.ents[ids[0]]
    words = _camel_tokens(re.sub(r"[^A-Za-z0-9 _-]", " ", text))
    best, score = None, 0.0
    for p in _declared_props(ontology.model, ent.class_name):
        pt = _camel_tokens(p)
        s = len(pt & words) / len(pt) if pt else 0
        if s > score:
            best, score = p, s
    if not best or score < 0.5:
        return None
    low = text.lower()
    m = re.search(r"(increase|raise|grow|add|reduce|decrease|cut|lower|drop)\w*\s+(?:\w+\s+){0,5}?by\s+(-?\d[\d,.]*)\s*(%|percent)?", low)
    if m:
        v, pct = _num(m.group(2)), bool(m.group(3))
        neg = m.group(1) in ("reduce", "decrease", "cut", "lower", "drop")
        if pct:
            return {"entityId": ent.id, "property": best, "op": "multiply", "value": round(1 + (-v if neg else v) / 100, 6)}
        return {"entityId": ent.id, "property": best, "op": "add", "value": -v if neg else v}
    if re.search(r"\bdoubl\w+", low):
        return {"entityId": ent.id, "property": best, "op": "multiply", "value": 2}
    if re.search(r"\bhalv\w+", low):
        return {"entityId": ent.id, "property": best, "op": "multiply", "value": 0.5}
    m = re.search(r"\b(?:to|=|at|become|becomes|goes to|is)\s*(-?\d[\d,.]*)", low)
    if m:
        return {"entityId": ent.id, "property": best, "op": "set", "value": _num(m.group(1))}
    return None


def choose_agent(ontology, tool: str, concepts: list[str], prefer: Agent | None = None) -> Agent | None:
    spec = get_spec(ontology, tool)
    cap = spec["capability"] if spec else tool
    role = ROLE_FOR.get(tool, "custom")
    cands = []
    for a in Agent.objects.filter(ontology=ontology, enabled=True):
        if not (tool in a.tools or cap in a.capabilities):
            continue
        if a.scope and concepts and not all(c in a.scope or set(a.scope) & ancestors(ontology.model, c) for c in concepts):
            continue
        cands.append((0 if a.role == role else 1 if a.role != "supervisor" else 2, a.id, a))
    if prefer and any(x[2].id == prefer.id for x in cands):
        return prefer
    return sorted(cands, key=lambda c: c[:2])[0][2] if cands else None


def plan(ontology, goal: str, supervisor: Agent | None = None, only_agent: Agent | None = None) -> dict:
    goal = (goal or "").strip()
    if not goal or len(goal) > 2000:
        raise ValueError("goal is required (max 2000 characters).")
    model = ontology.model
    grounded = query.ground(model, goal)
    concepts = list(dict.fromkeys(g["class"] for g in grounded))
    graph = get_entity_graph(ontology)
    ents = graph.mentioned(goal)
    steps, notes = [], []
    op = agent_mod.detect_operation(goal)

    def add(key, tool, args, title, deps=(), operation=None, cs=None):
        spec = get_spec(ontology, tool)
        a = choose_agent(ontology, tool, cs if cs is not None else concepts, only_agent or supervisor)
        if only_agent and a and a.id != only_agent.id:
            a = None
        steps.append({"key": key, "title": title, "tool": tool, "args": args, "dependsOn": list(deps), "operation": operation or (spec or {}).get("operation", "read"),
                      "concepts": cs if cs is not None else concepts, "agent": a.name if a else None})
        if not a:
            notes.append(f"No enabled agent can perform '{tool}'" + (f" within the scope of {', '.join(concepts)}" if concepts else "") + ".")
        return key

    if WHATIF.search(goal):
        ch = parse_change(ontology, goal)
        if ch:
            cls = graph.ents[ch["entityId"]].class_name
            k = add("simulate", "twin.simulate", {"changes": [ch]}, f"Simulate: {ch['property']} {ch['op']} {ch['value']} on {graph.ents[ch['entityId']].display_name}", cs=[cls])
            if APPLY.search(goal) and ch["op"] == "set":
                add("apply", "twin.event", {"type": "agent.change", "entityId": ch["entityId"], "set": {ch["property"]: ch["value"]}},
                    f"Apply: set {ch['property']} = {ch['value']}", deps=[k], operation="update", cs=[cls])
        else:
            notes.append("Could not extract a concrete change (entity, attribute and value) from the goal; name the entity and the new value.")
            add("research", "rag.ask", {"question": goal}, "Answer from the knowledge base")
    elif SYNC.search(goal) and not goal.strip().endswith("?"):
        add("sync", "fabric.sync", {}, "Synchronise the connected source systems", operation="execute", cs=concepts)
    elif UPDATE.search(goal) and ents and (ch := parse_change(ontology, goal)) and ch["op"] == "set":
        cls = graph.ents[ch["entityId"]].class_name
        add("update", "twin.event", {"type": "agent.change", "entityId": ch["entityId"], "set": {ch["property"]: ch["value"]}},
            f"Set {ch['property']} = {ch['value']} on {graph.ents[ch['entityId']].display_name}", operation="update", cs=[cls])
    else:
        deps = []
        if ents:
            e = graph.ents[ents[0]]
            deps.append(add("lookup", "fabric.entities", {"q": e.display_name, "class": e.class_name, "limit": 5}, f"Look up {e.display_name} in the knowledge fabric", cs=[e.class_name]))
        add("research", "rag.ask", {"question": goal}, "Answer from documents and the knowledge graph", deps=deps)
    if not steps:
        notes.append("The goal could not be decomposed.")
    return {"goal": goal, "intent": "whatif" if WHATIF.search(goal) else "action" if op != "read" else "question", "operation": op, "concepts": concepts,
            "entities": [{"id": i, "name": graph.ents[i].display_name, "class": graph.ents[i].class_name} for i in ents[:10]], "steps": steps, "notes": notes,
            "supervisor": supervisor.name if supervisor else None}
