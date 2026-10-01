"""Knowledge Explorer + Query Studio (R7)."""
import difflib
import re
from collections import deque

from rdflib import Graph

from . import embeddings, generator
from .naming import singular, words
from .reasoning import inherited_properties
from .validation import ancestors

MAX_ROWS = 1000
_FORBIDDEN = re.compile(r"\b(INSERT|DELETE|LOAD|CLEAR|DROP|CREATE|COPY|MOVE|ADD|SERVICE|WITH)\b", re.I)


# ------------------------------------------------------------ explorer ----

def _tokens(s):
    return [singular(w) for w in words(s)]


def search(model: dict, q: str, limit=25) -> list[dict]:
    q = q.strip().lower()
    if not q:
        return []
    qt = set(_tokens(q))
    hits = []
    entries = [("class", c["name"], c["name"], c) for c in model["classes"]]
    entries += [("dataProperty", f'{p["domain"]}.{p["name"]}', p["name"], p) for p in model["dataProperties"]]
    entries += [("objectProperty", f'{p["domain"]}.{p["name"]}', p["name"], p) for p in model["objectProperties"]]
    for kind, ident, name, obj in entries:
        hay = [name, obj.get("label") or "", obj.get("comment") or ""] + list(obj.get("synonyms", []))
        score = 0.0
        for i, h in enumerate(hay):
            hl = h.lower()
            if not hl:
                continue
            w = (1.0, 0.9, 0.4, 0.85)[min(i, 3)]
            if hl == q:
                score = max(score, 1.0 * w)
            elif q in hl:
                score = max(score, 0.8 * w)
            elif qt and qt & set(_tokens(h)):
                score = max(score, 0.6 * w * len(qt & set(_tokens(h))) / len(qt))
            elif i == 0:
                r = difflib.SequenceMatcher(None, q, hl).ratio()
                if r > 0.75:
                    score = max(score, r * 0.6)
        if score > 0.2:
            hits.append({"kind": kind, "id": ident, "name": name, "label": obj.get("label") or name,
                         "domain": obj.get("domain"), "score": round(score, 3)})
    # Semantic pass (only with a real embedding model): finds concepts that share no words with the query
    # ("client" -> Customer) and re-ranks near matches. Reported as `semantic: true` so the UI can say why it matched.
    prov = embeddings.get_provider()
    if prov is not None and prov.name == "fastembed" and len(q) >= 3:
        texts = [" ".join(_tokens(e[2]) + _tokens(e[3].get("label") or "")) or e[2] for e in entries]
        sims = embeddings.similarities(" ".join(_tokens(q)) or q, texts)
        by_id = {h["id"]: h for h in hits}
        for (kind, ident, name, obj), sim in zip(entries, sims):
            if kind != "class" and sim < 0.6:  # properties are numerous: demand a stronger semantic signal
                continue
            if sim < 0.45:
                continue
            h = by_id.get(ident)
            if h:
                h["score"] = round(min(1.0, h["score"] + 0.25 * sim), 3)
            else:
                hits.append({"kind": kind, "id": ident, "name": name, "label": obj.get("label") or name, "domain": obj.get("domain"),
                             "score": round(0.55 * sim, 3), "semantic": True})
    hits.sort(key=lambda h: -h["score"])
    return hits[:limit]


def concept(model: dict, name: str, mappings: list | None = None) -> dict:
    c = next((c for c in model["classes"] if c["name"] == name), None)
    if not c:
        raise KeyError(name)
    anc = sorted(ancestors(model, name))
    children = sorted(x["name"] for x in model["classes"] if name in x.get("parents", []))
    own_d = [p for p in model["dataProperties"] if p["domain"] == name]
    own_o = [p for p in model["objectProperties"] if p["domain"] == name]
    incoming = [p for p in model["objectProperties"] if p["range"] == name]
    inherited = [p for p in inherited_properties(model, name)]
    sources = []
    for s in [c.get("source")] + list(c.get("sources", [])):
        if s and s not in sources:
            sources.append(s)
    for e in c.get("evidence", []):
        sources.append({"document": e.get("document"), "page": e.get("page"), "section": e.get("section"),
                        "snippet": e.get("snippet")})
    mapped = [m for m in (mappings or []) if str(m.get("target", "")).split(".")[0] == name
              and m.get("status") in ("accepted", "overridden")]
    return {"name": name, "label": c.get("label") or name, "comment": c.get("comment", ""),
            "parents": c.get("parents", []), "ancestors": anc, "children": children,
            "equivalentTo": c.get("equivalentTo", []), "disjointWith": c.get("disjointWith", []),
            "synonyms": c.get("synonyms", []), "confidence": c.get("confidence"),
            "dataProperties": own_d, "relationships": {
                "outgoing": [{"name": p["name"], "target": p["range"], "cardinality": p.get("cardinality")} for p in own_o],
                "incoming": [{"name": p["name"], "source": p["domain"], "cardinality": p.get("cardinality")} for p in incoming]},
            "inheritedProperties": inherited, "sources": sources, "mappings": mapped,
            "individuals": [i for i in model.get("individuals", []) if i.get("class") == name]}


def neighbors(model: dict, name: str) -> dict:
    nodes, edges = {name}, []
    for p in model["objectProperties"]:
        if p["domain"] == name or p["range"] == name:
            nodes.update([p["domain"], p["range"]])
            edges.append({"from": p["domain"], "to": p["range"], "label": p["name"], "type": "object"})
    for c in model["classes"]:
        if name in c.get("parents", []) or c["name"] == name:
            for par in c.get("parents", []):
                if par == name or c["name"] == name:
                    nodes.update([c["name"], par])
                    edges.append({"from": c["name"], "to": par, "label": "subClassOf", "type": "subclass"})
    return {"nodes": sorted(nodes), "edges": edges}


# ------------------------------------------------------- path finding -----

def _adjacency(model):
    adj = {c["name"]: [] for c in model["classes"]}
    for p in model["objectProperties"]:
        if p["domain"] in adj and p["range"] in adj:
            adj[p["domain"]].append((p["range"], p, True))
            adj[p["range"]].append((p["domain"], p, False))
    for c in model["classes"]:
        for par in c.get("parents", []):
            if par in adj:
                adj[c["name"]].append((par, {"name": "subClassOf", "domain": c["name"], "range": par, "_sub": True}, True))
                adj[par].append((c["name"], {"name": "subClassOf", "domain": c["name"], "range": par, "_sub": True}, False))
    return adj


def find_path(model: dict, a: str, b: str) -> list[dict] | None:
    """Shortest connection between two classes over relationships (either direction)."""
    adj = _adjacency(model)
    if a not in adj or b not in adj:
        return None
    prev, q = {a: None}, deque([a])
    while q:
        cur = q.popleft()
        if cur == b:
            break
        for nxt, p, fwd in adj[cur]:
            if nxt not in prev:
                prev[nxt] = (cur, p, fwd)
                q.append(nxt)
    if b not in prev:
        return None
    path, cur = [], b
    while prev[cur]:
        pc, p, fwd = prev[cur]
        path.append({"from": pc, "property": p["name"], "to": cur, "forward": fwd, "domain": p["domain"], "range": p["range"],
                     "subclass": bool(p.get("_sub"))})
        cur = pc
    return list(reversed(path))


# ----------------------------------------------------------- SPARQL -------

def _prefixes(base_iri):
    return (f"PREFIX onto: <{base_iri}>\nPREFIX owl: <http://www.w3.org/2002/07/owl#>\n"
            "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\nPREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
            "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\nPREFIX skos: <http://www.w3.org/2004/02/skos/core#>\n")


def run_sparql(model: dict, query: str, base_iri: str = generator.DEFAULT_BASE_IRI, name="Ontology", graph: Graph | None = None) -> dict:
    stripped = re.sub(r"#[^\n]*", "", query)
    stripped_nostr = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|<[^>]*>', "", stripped)
    if _FORBIDDEN.search(stripped_nostr):
        raise ValueError("Only read-only queries (SELECT / ASK / CONSTRUCT / DESCRIBE) are allowed.")
    if not re.search(r"\b(SELECT|ASK|CONSTRUCT|DESCRIBE)\b", stripped_nostr, re.I):
        raise ValueError("Query must be a SELECT, ASK, CONSTRUCT or DESCRIBE query.")
    g = graph or generator.model_to_graph(model, name, base_iri)
    try:
        res = g.query(_prefixes(base_iri) + query)
    except Exception as e:
        raise ValueError(f"SPARQL error: {e}") from e

    def short(t):
        s = str(t)
        return s.replace(base_iri, "onto:") if s.startswith(base_iri) else s

    if res.type == "ASK":
        return {"type": "ASK", "columns": ["result"], "rows": [[bool(res.askAnswer)]], "count": 1, "truncated": False}
    if res.type == "SELECT":
        cols = [str(v) for v in res.vars]
        rows, truncated = [], False
        for i, r in enumerate(res):
            if i >= MAX_ROWS:
                truncated = True
                break
            rows.append([short(r[c]) if r[c] is not None else None for c in cols])
        return {"type": "SELECT", "columns": cols, "rows": rows, "count": len(rows), "truncated": truncated}
    triples = [[short(s), short(p), short(o)] for s, p, o in list(res)[:MAX_ROWS]]
    return {"type": res.type, "columns": ["subject", "predicate", "object"], "rows": triples, "count": len(triples), "truncated": False}


# ------------------------------------------------- natural language -------

_STOP = {"find", "show", "list", "get", "all", "the", "who", "which", "that", "whose", "with", "for", "of", "in", "by", "and",
         "to", "a", "an", "have", "has", "placed", "are", "is", "what", "me", "give", "from", "their", "its", "on", "at"}


def _concept_index(model):
    idx = {}
    for c in model["classes"]:
        forms = {c["name"], c.get("label") or c["name"], *c.get("synonyms", [])}
        for f in forms:
            key = " ".join(_tokens(f))
            if key:
                idx.setdefault(key, c["name"])
    return idx


def ground(model: dict, text: str) -> list[dict]:
    """Ground free text to ontology classes, in order of mention (longest-match)."""
    idx = _concept_index(model)
    toks = [singular(t) for t in re.findall(r"[A-Za-z0-9]+", text.lower())]
    found, i = [], 0
    max_n = max((len(k.split()) for k in idx), default=1)
    while i < len(toks):
        for n in range(min(max_n, len(toks) - i), 0, -1):
            key = " ".join(toks[i:i + n])
            if key in idx:
                if not found or found[-1]["class"] != idx[key]:
                    found.append({"class": idx[key], "matched": key, "position": i})
                i += n
                break
        else:
            i += 1
    return found


def _iri(model, base_iri, pname, domain):
    p = next((q for q in model["objectProperties"] if q["name"] == pname and q["domain"] == domain), None)
    return generator.prop_local(model, p) if p else pname


def natural_query(model: dict, text: str, base_iri: str = generator.DEFAULT_BASE_IRI, name="Ontology") -> dict:
    """Translate a business question into a chain of concepts + SPARQL and run it."""
    concepts = ground(model, text)
    if not concepts:
        raise ValueError("I could not recognise any ontology concept in the question. "
                         "Try naming classes, e.g. 'customers who placed sales orders'.")
    seq = [c["class"] for c in concepts]
    steps, chain = [], [seq[0]]
    for a, b in zip(seq, seq[1:]):
        if a == b:
            continue
        path = find_path(model, a, b)
        if path is None:
            raise ValueError(f"No relationship path connects {a} and {b} in the ontology.")
        steps.extend(path)
        chain.extend(s["to"] for s in path)
    # SPARQL over instances (variables per hop)
    lines, var = [f"?v0 a onto:{chain[0]} ."], 0
    for s in steps:
        if s["subclass"]:
            continue
        var += 1
        prop = f"onto:{_iri(model, base_iri, s['property'], s['domain'])}"
        cur, nxt = f"?v{var-1}", f"?v{var}"
        lines.append(f"{cur} {prop} {nxt} ." if s["forward"] else f"{nxt} {prop} {cur} .")
        lines.append(f"{nxt} a onto:{s['to']} .")
    vars_ = " ".join(f"?v{i}" for i in range(var + 1))
    instance_sparql = f"SELECT DISTINCT {vars_} WHERE {{\n  " + "\n  ".join(lines) + "\n}"
    # SPARQL over the ontology structure (always answerable)
    schema_lines = [f"onto:{chain[0]} a owl:Class ."]
    schema_sparql = None
    if steps:
        parts = []
        for s in steps:
            if s["subclass"]:
                parts.append(f"onto:{s['domain']} rdfs:subClassOf onto:{s['range']} .")
            else:
                prop = _iri(model, base_iri, s["property"], s["domain"])
                parts.append(f"onto:{prop} rdfs:domain onto:{s['domain']} ; rdfs:range onto:{s['range']} .")
        schema_sparql = "SELECT * WHERE {\n  " + "\n  ".join(parts) + "\n}"
    result = run_sparql(model, instance_sparql, base_iri, name)
    explanation = " → ".join(
        f"{s['from']} —{s['property']}{'' if s['forward'] else ' (inverse)'}→ {s['to']}" for s in steps) or chain[0]
    return {"interpretation": [{"class": c["class"], "matched": c["matched"]} for c in concepts], "path": steps,
            "explanation": explanation, "sparql": instance_sparql, "schemaSparql": schema_sparql, "result": result,
            "hasIndividuals": bool(model.get("individuals")),
            "note": None if model.get("individuals") else
            "This ontology has no individuals yet, so the instance query returns no rows; the path above shows how the concepts connect."}
