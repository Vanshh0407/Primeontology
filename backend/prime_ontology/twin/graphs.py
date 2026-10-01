"""Domain graph views over the twin: customer / supplier / financial / contract / organization / process / asset graphs.

A view selects the classes whose names (or ancestors' names) match the domain keywords, adds their 1-hop neighbours as context,
and returns nodes + edges. Views with no matching class report 0 so the UI can say what the ontology is missing.
"""
from ..rag.retrieve import get_entity_graph
from ..validation import ancestors
from .models import TwinAsset, TwinProcess, TwinProcessInstance

KEYWORDS = {
    "customer": ("customer", "client", "account", "buyer"),
    "supplier": ("supplier", "vendor", "manufacturer", "provider"),
    "financial": ("invoice", "payment", "ledger", "budget", "transaction", "expense", "receivable", "payable", "order"),
    "contract": ("contract", "agreement", "clause", "obligation", "obligation", "sla"),
    "organization": ("organization", "organisation", "department", "employee", "person", "team", "company", "party", "user"),
}
VIEWS = ["entity", *KEYWORDS, "process", "asset"]


def classes_for(model: dict, view: str) -> set[str]:
    if view == "entity":
        return {c["name"] for c in model["classes"]}
    kws = KEYWORDS.get(view, ())
    out = set()
    for c in model["classes"]:
        names = {c["name"].lower(), *(a.lower() for a in ancestors(model, c["name"]))}
        if any(k in n for k in kws for n in names):
            out.add(c["name"])
    return out


def _entity_view(ontology, view: str, limit: int) -> dict:
    g = get_entity_graph(ontology)
    cls = classes_for(ontology.model, view)
    focus = [i for c in cls for i in g.by_class.get(c, [])][:limit]
    fset = set(focus)
    context = {}
    for i in focus:
        for nb, _p, _f in g.adj.get(i, []):
            if nb not in fset and nb in g.ents and len(fset) + len(context) < limit * 2:
                context[nb] = True
    ids = fset | set(context)
    nodes = [{"id": f"e{i}", "label": g.ents[i].display_name, "class": g.ents[i].class_name, "focus": i in fset, "entityId": i} for i in sorted(ids)]
    edges, seen = [], set()
    for a in ids:
        for b, prop, fwd in g.adj.get(a, []):
            if fwd and b in ids and (a, b, prop) not in seen:
                seen.add((a, b, prop))
                edges.append({"from": f"e{a}", "to": f"e{b}", "label": prop})
    return {"nodes": nodes, "edges": edges, "classes": sorted(cls)}


def _process_view(ontology) -> dict:
    nodes, edges = [], []
    for p in TwinProcess.objects.filter(ontology=ontology):
        counts = {}
        for st in TwinProcessInstance.objects.filter(process=p).values_list("state", flat=True):
            counts[st] = counts.get(st, 0) + 1
        for s in p.states:
            nodes.append({"id": f"p{p.id}:{s}", "label": f"{p.name} · {s}", "class": p.entity_class, "focus": s == p.initial, "count": counts.get(s, 0)})
        for t in p.transitions:
            edges.append({"from": f"p{p.id}:{t['from']}", "to": f"p{p.id}:{t['to']}", "label": t["name"]})
    return {"nodes": nodes, "edges": edges, "classes": sorted({n["class"] for n in nodes})}


def _asset_view(ontology) -> dict:
    rows = list(TwinAsset.objects.filter(ontology=ontology)[:1000])
    nodes = [{"id": f"a{a.id}", "label": a.name, "class": a.asset_type, "focus": a.parent_id is None, "status": a.status} for a in rows]
    edges = [{"from": f"a{a.parent_id}", "to": f"a{a.id}", "label": "contains"} for a in rows if a.parent_id]
    edges += [{"from": f"a{a.id}", "to": f"e{a.entity_id}", "label": "linked to"} for a in rows if a.entity_id]
    return {"nodes": nodes, "edges": edges, "classes": sorted({a.asset_type for a in rows})}


def graph_view(ontology, view: str, limit: int = 150) -> dict:
    if view not in VIEWS:
        raise ValueError(f"view must be one of {', '.join(VIEWS)}.")
    limit = max(10, min(limit, 500))
    out = _process_view(ontology) if view == "process" else _asset_view(ontology) if view == "asset" else _entity_view(ontology, view, limit)
    return {"view": view, **out, "counts": {"nodes": len(out["nodes"]), "edges": len(out["edges"])}}


def available(ontology) -> dict:
    g = get_entity_graph(ontology)
    out = {}
    for v in VIEWS:
        if v == "process":
            out[v] = TwinProcess.objects.filter(ontology=ontology).count()
        elif v == "asset":
            out[v] = TwinAsset.objects.filter(ontology=ontology).count()
        else:
            out[v] = sum(len(g.by_class.get(c, [])) for c in classes_for(ontology.model, v))
    return out
