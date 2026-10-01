"""Hybrid retrieval: BM25 + vector + graph, fused with reciprocal-rank fusion, ontology-aware and provenance-aware.

Signals
  * lexical  - BM25 over chunk text (with ontology query expansion: synonyms, sub-classes, related classes)
  * vector   - cosine similarity (only against vectors made by the CURRENT embedding provider)
  * graph    - chunks that mention entities that the question names, or entities reachable from them over relationships
  * facts    - the golden records of those entities (with the systems that supplied each value)
  * structured - questions like "contracts with suppliers whose invoices were overdue more than 60 days" are answered by
                 traversing the entity graph (not by guessing from text), and every hop is returned as evidence.
"""
import math
import re
import threading
from collections import Counter, defaultdict, deque

import numpy as np

from .. import embeddings, query
from ..fabric.models import FabricEntity, FabricRef, FabricState, property_provenance
from ..naming import singular
from .models import CLASSIFICATIONS, RagChunk

STOP = set("a an the of in on at to for and or is are was were be been by with from that this these those which who whom whose what when where how "
           "do does did has have had it its as not no than then there their them they we you i me my our your can could should would will shall may "
           "find show list get give all any every each me about tell".split())
CLEARANCE = {"viewer": 1, "editor": 2, "reviewer": 3, "admin": 3, "none": 0}  # index into CLASSIFICATIONS (exclusive upper bound = clearance + 1)
RRF_K = 60


def allowed_classifications(role: str) -> list[str]:
    return list(CLASSIFICATIONS[:CLEARANCE.get(role, 0) + 1])


def toks(text: str) -> list[str]:
    return [singular(t) for t in re.findall(r"[a-z0-9@._-]+", text.lower()) if t.strip("._-") and t not in STOP]


# -------------------------------------------------------------------- index

class Index:
    def __init__(self, ontology):
        self.rows = list(RagChunk.objects.filter(ontology=ontology).select_related("document").order_by("id"))
        self.tokens = [toks(f"{r.heading} {r.text}") for r in self.rows]
        self.df = Counter(t for ts in self.tokens for t in set(ts))
        self.avg = (sum(len(t) for t in self.tokens) / len(self.tokens)) if self.tokens else 1
        self.tf = [Counter(t) for t in self.tokens]
        self.provider = (embeddings.get_provider().name if embeddings.get_provider() else None)
        sel = [i for i, r in enumerate(self.rows) if r.embedding and r.embedding_provider == self.provider]
        self.vec_rows = sel
        self.matrix = np.vstack([np.frombuffer(self.rows[i].embedding, dtype=np.float32) for i in sel]) if sel else None

    def bm25(self, qterms: dict[str, float], allowed: set[int]) -> dict[int, float]:
        n, out = len(self.rows), {}
        for i in allowed:
            tf, dl, s = self.tf[i], len(self.tokens[i]) or 1, 0.0
            for t, w in qterms.items():
                f = tf.get(t)
                if f:
                    idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                    s += w * idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * dl / self.avg))
            if s > 0:
                out[i] = s
        return out


_CACHE: dict[int, tuple[tuple, Index]] = {}
_LOCK = threading.Lock()


def get_index(ontology) -> Index:
    from django.db.models import Count, Max

    agg = RagChunk.objects.filter(ontology=ontology).aggregate(n=Count("id"), m=Max("id"))
    prov = embeddings.get_provider()
    key = (agg["n"], agg["m"], prov.name if prov else None)
    with _LOCK:
        hit = _CACHE.get(ontology.id)
        if hit and hit[0] == key:
            return hit[1]
    idx = Index(ontology)
    with _LOCK:
        _CACHE[ontology.id] = (key, idx)
        if len(_CACHE) > 8:
            _CACHE.pop(next(iter(_CACHE)))
    return idx


def invalidate(ontology_id: int):
    with _LOCK:
        _CACHE.pop(ontology_id, None)


# ----------------------------------------------------------- query expansion

def expand_query(model: dict, text: str) -> dict:
    """Ontology-driven expansion. Original words weigh 1.0; expansions less, so they help recall without drowning the question."""
    terms = {t: 1.0 for t in toks(text)}
    grounded = query.ground(model, text)
    added = []
    classes = {c["name"]: c for c in model["classes"]}
    for g in grounded:
        c = classes[g["class"]]
        forms = [(c.get("label") or c["name"], 0.8), *[(s, 0.8) for s in c.get("synonyms", [])]]
        for sub in model["classes"]:  # sub-classes: a question about 'Party' also concerns 'Customer'
            if g["class"] in sub.get("parents", []):
                forms.append((sub.get("label") or sub["name"], 0.5))
        for p in model["objectProperties"]:  # related classes (1 hop) at a low weight
            if p["domain"] == g["class"] and p["range"] in classes:
                forms.append((classes[p["range"]].get("label") or p["range"], 0.25))
            elif p["range"] == g["class"] and p["domain"] in classes:
                forms.append((classes[p["domain"]].get("label") or p["domain"], 0.25))
        for text_form, w in forms:
            for t in toks(str(text_form)):
                if t not in terms or terms[t] < w:
                    terms[t] = max(terms.get(t, 0), w)
                    added.append(t)
    return {"terms": terms, "classes": [g["class"] for g in grounded], "expandedWith": sorted(set(added) - set(toks(text)))}


# ------------------------------------------------------------- entity graph

class EntityGraph:
    """Entity-level adjacency (from resolved fabric references), cached per fabric version."""

    def __init__(self, ontology):
        self.ontology = ontology
        self.adj: dict[int, list[tuple[int, str, bool]]] = defaultdict(list)
        refs = FabricRef.objects.filter(ontology=ontology, to_record__isnull=False, from_record__entity__isnull=False, to_record__entity__isnull=False,
                                        from_record__deleted=False, to_record__deleted=False, from_record__entity__status="active",
                                        to_record__entity__status="active").values_list("property", "from_record__entity_id", "to_record__entity_id")
        seen = set()
        for prop, a, b in refs.iterator():
            if (prop, a, b) not in seen and a != b:
                seen.add((prop, a, b))
                self.adj[a].append((b, prop, True))
                self.adj[b].append((a, prop, False))
        self.ents = {e.id: e for e in FabricEntity.objects.filter(ontology=ontology, status="active")}
        self.by_class: dict[str, list[int]] = defaultdict(list)
        for e in self.ents.values():
            self.by_class[e.class_name].append(e.id)
        self.names: dict[str, list[int]] = defaultdict(list)
        for e in self.ents.values():
            key = " ".join(re.findall(r"[a-z0-9]+", (e.display_name or "").lower()))
            if len(key) >= 3:
                self.names[key].append(e.id)

    def mentioned(self, text: str) -> list[int]:
        words = re.findall(r"[a-z0-9]+", text.lower())
        found = []
        for i in range(len(words)):
            for n in range(min(6, len(words) - i), 0, -1):
                hit = self.names.get(" ".join(words[i:i + n]))
                if hit and len(hit) <= 3:
                    found.extend(hit)
                    break
        return list(dict.fromkeys(found))

    def reach(self, seeds, hops=2, limit=400) -> dict[int, int]:
        dist, q = {s: 0 for s in seeds}, deque(seeds)
        while q and len(dist) < limit:
            cur = q.popleft()
            if dist[cur] >= hops:
                continue
            for nb, _p, _f in self.adj.get(cur, []):
                if nb not in dist:
                    dist[nb] = dist[cur] + 1
                    q.append(nb)
        return dist

    def connected(self, start: int, class_name: str, targets: set[int], max_hops=4):
        """Shortest relationship path from entity `start` to any entity in `targets` (list of steps) or None."""
        if start in targets:
            return []
        prev, q, hit = {start: None}, deque([start]), None
        depth = {start: 0}
        while q and hit is None:
            cur = q.popleft()
            if depth[cur] >= max_hops:
                continue
            for nb, prop, fwd in self.adj.get(cur, []):
                if nb not in prev:
                    prev[nb], depth[nb] = (cur, prop, fwd), depth[cur] + 1
                    if nb in targets:
                        hit = nb
                        break
                    q.append(nb)
        if hit is None:
            return None
        steps, c = [], hit
        while prev[c]:
            p, prop, fwd = prev[c]
            steps.append({"from": p, "property": prop, "to": c, "forward": fwd})
            c = p
        return list(reversed(steps))


_GCACHE: dict[int, tuple[tuple, EntityGraph]] = {}


def get_entity_graph(ontology) -> EntityGraph:
    st = FabricState.objects.filter(ontology=ontology).first()
    key = (st.version if st else 0, ontology.model_revision)
    with _LOCK:
        hit = _GCACHE.get(ontology.id)
        if hit and hit[0] == key:
            return hit[1]
    g = EntityGraph(ontology)
    with _LOCK:
        _GCACHE[ontology.id] = (key, g)
        if len(_GCACHE) > 8:
            _GCACHE.pop(next(iter(_GCACHE)))
    return g


# --------------------------------------------------- structured question plan

OPS = [(r"(?:more than|greater than|over|above|exceed(?:s|ing)?|longer than|higher than|bigger than|larger than|>)", "gt"),
       (r"(?:at least|no less than|minimum of|>=)", "gte"),
       (r"(?:less than|fewer than|under|below|lower than|smaller than|<)", "lt"),
       (r"(?:at most|no more than|maximum of|up to|<=)", "lte"),
       (r"(?:exactly|equal to|equals|=)", "eq")]
_NUM = r"(-?\d[\d,]*\.?\d*)"
COMPARE = {"gt": lambda a, b: a > b, "gte": lambda a, b: a >= b, "lt": lambda a, b: a < b, "lte": lambda a, b: a <= b, "eq": lambda a, b: a == b}
SYMBOL = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "="}


def parse_constraints(model: dict, text: str, classes: list[str]) -> list[dict]:
    """Numeric constraints such as 'overdue more than 60 days' -> {class, property, op, value}, resolved against the ontology."""
    numeric = [p for p in model["dataProperties"] if p.get("datatype") in ("integer", "decimal", "float", "double", "long", "int")]
    if not numeric:
        return []
    out = []
    low = text.lower()
    for pat, op in OPS:
        for m in re.finditer(rf"(?<![a-z])({pat})\s*(?:the\s+)?(?:\$|₹|€|£)?\s*{_NUM}\s*([a-z%]+)?", low):
            val = float(m.group(2).replace(",", ""))
            before = toks(low[max(0, m.start() - 70):m.start()])[-6:]
            after = toks(m.group(3) or "")
            window = set(before + after)
            best, score = None, 0.0
            for p in numeric:
                ptoks = set(toks(" ".join(re.findall(r"[A-Z]?[a-z0-9]+", p["name"]))) + toks(p.get("label") or ""))
                if not ptoks:
                    continue
                s = len(ptoks & window) / len(ptoks)
                if p["domain"] in classes:
                    s += 0.15
                if s > score:
                    best, score = p, s
            if best and score >= 0.5:
                out.append({"class": best["domain"], "property": best["name"], "op": op, "value": int(val) if val.is_integer() else val})
    uniq = {(c["class"], c["property"], c["op"], c["value"]): c for c in out}
    return list(uniq.values())


def parse_equalities(g: EntityGraph, text: str, classes: list[str], model: dict) -> list[dict]:
    """'unpaid / status paid / city Pune' - a word of the question equal to a (low-cardinality) value of a text property of a mentioned class."""
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    out = []
    for c in classes:
        props = [p["name"] for p in model["dataProperties"] if p["domain"] == c and p.get("datatype", "string") == "string"]
        for pn in props:
            vals = {str(g.ents[i].canonical.get(pn)) for i in g.by_class.get(c, [])[:5000] if g.ents[i].canonical.get(pn) not in (None, "")}
            if 1 < len(vals) <= 25:
                for v in vals:
                    if len(v) >= 3 and v.lower() in words:
                        out.append({"class": c, "property": pn, "op": "eq", "value": v})
    return out


def _holds(entity, c) -> bool:
    v = entity.canonical.get(c["property"])
    if v is None:
        return False
    if isinstance(c["value"], str):
        return str(v).lower() == c["value"].lower()
    try:
        return COMPARE[c["op"]](float(v), float(c["value"]))
    except (TypeError, ValueError):
        return False


def structured(ontology, text: str, g: EntityGraph, limit=50) -> dict | None:
    """Answer 'A with B whose C ...' by graph traversal. Returns None when the question is not of this form."""
    model = ontology.model
    mentioned = query.ground(model, text)
    classes = list(dict.fromkeys(m["class"] for m in mentioned))
    cons = parse_constraints(model, text, classes) + parse_equalities(g, text, classes, model)
    if not classes or not (cons or len(classes) > 1):
        return None
    for c in cons:
        if c["class"] not in classes:
            classes.append(c["class"])
    if len(classes) < 2 and not cons:
        return None
    result_class = classes[0]
    if not g.by_class.get(result_class):
        return None
    # satisfying entity sets per required class
    required = {}
    for cls in classes[1:] if len(classes) > 1 else []:
        ids = set(g.by_class.get(cls, []))
        for c in cons:
            if c["class"] == cls:
                ids = {i for i in ids if _holds(g.ents[i], c)}
        required[cls] = ids
    own = set(g.by_class[result_class])
    for c in cons:
        if c["class"] == result_class:
            own = {i for i in own if _holds(g.ents[i], c)}
    matches = []
    for eid in sorted(own):
        evidence, ok = [], True
        for cls, targets in required.items():
            if not targets:
                ok = False
                break
            steps = g.connected(eid, cls, targets)
            if steps is None:
                ok = False
                break
            evidence.append({"class": cls, "via": steps})
        if ok:
            matches.append({"entityId": eid, "evidence": evidence})
        if len(matches) >= limit:
            break
    return {"resultClass": result_class, "classes": classes, "constraints": cons, "matches": matches, "total": len(matches),
            "truncated": len(matches) >= limit}


# ---------------------------------------------------------------- facts text

def fact_text(e: FabricEntity) -> str:
    props = "; ".join(f"{k}: {v}" for k, v in list((e.canonical or {}).items())[:14] if v not in (None, ""))
    sources = sorted({p.get("source", "") for p in property_provenance(e).values()} - {""})
    return f"{e.class_name} '{e.display_name}' ({props})" + (f" - recorded in: {', '.join(sources)}" if sources else "")


def fact_item(e: FabricEntity, score: float, why: str) -> dict:
    return {"kind": "entity", "entityId": e.id, "class": e.class_name, "title": e.display_name, "text": fact_text(e), "score": round(score, 4), "why": why,
            "provenance": e.provenance, "sources": sorted({p.get("source", "") for p in property_provenance(e).values()} - {""})}


# ------------------------------------------------------------------ retrieve

def _filter_ok(row: RagChunk, filters: dict) -> bool:
    d = row.document
    if filters.get("docType") and d.doc_type not in (filters["docType"] if isinstance(filters["docType"], list) else [filters["docType"]]):
        return False
    if filters.get("documentIds") and d.id not in filters["documentIds"]:
        return False
    if filters.get("concepts") and not set(filters["concepts"]) & set(row.concepts):
        return False
    if filters.get("entityIds") and not set(filters["entityIds"]) & set(row.entities):
        return False
    for k, v in (filters.get("metadata") or {}).items():
        have = d.metadata.get(k)
        if have != v and not (isinstance(v, list) and have in v):
            return False
    return True


def retrieve(ontology, text: str, *, role="viewer", k=8, filters=None, max_context_chars=6000, use_vectors=True, use_graph=True, expand=True, hops=2) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("question is required.")
    if len(text) > 2000:
        raise ValueError("question is longer than 2000 characters.")
    k = max(1, min(int(k), 30))
    max_context_chars = max(500, min(int(max_context_chars), 40000))
    filters = filters or {}
    model = ontology.model
    exp = expand_query(model, text) if expand else {"terms": {t: 1.0 for t in toks(text)}, "classes": [], "expandedWith": []}
    idx = get_index(ontology)
    allowed_cls = set(allowed_classifications(role))
    allowed = {i for i, r in enumerate(idx.rows) if r.classification in allowed_cls and _filter_ok(r, filters)}
    withheld = sum(1 for r in idx.rows if r.classification not in allowed_cls)
    signals = {}
    lex = idx.bm25(exp["terms"], allowed)
    if lex:
        signals["lexical"] = lex
    prov = embeddings.get_provider()
    if use_vectors and idx.matrix is not None and prov is not None and prov.name == idx.provider:
        q = np.asarray(embeddings.embed([text])[0], dtype=np.float32)
        sims = idx.matrix @ q
        vec = {}
        for pos, i in enumerate(idx.vec_rows):
            if i in allowed:
                cal = max(0.0, min(1.0, (float(sims[pos]) - prov.lo) / (prov.hi - prov.lo)))
                if cal > (0.35 if prov.name == "fastembed" else 0.2):
                    vec[i] = cal
        if vec:
            signals["vector"] = vec
    g = get_entity_graph(ontology) if use_graph else None
    seeds, reach, struct = [], {}, None
    items_entities = []
    if g:
        seeds = g.mentioned(text)
        if seeds:
            reach = g.reach(seeds, hops)
            gs = {}
            for i in allowed:
                hits = [reach[e] for e in idx.rows[i].entities if e in reach]
                if hits:
                    gs[i] = sum(1.0 / (1 + h) for h in hits)
            if gs:
                signals["graph"] = gs
            for eid, d in sorted(reach.items(), key=lambda kv: kv[1])[:6]:
                items_entities.append(fact_item(g.ents[eid], 1.0 / (1 + d), "named in the question" if d == 0 else f"{d} hop(s) from an entity named in the question"))
        struct = structured(ontology, text, g)
        if struct and struct["matches"]:
            for m in struct["matches"][:6]:
                items_entities.append(fact_item(g.ents[m["entityId"]], 0.9, "satisfies the question's graph pattern"))
    # reciprocal rank fusion
    fused, why = defaultdict(float), defaultdict(list)
    weights = {"lexical": 1.0, "vector": 1.0, "graph": 0.8}
    for name, sc in signals.items():
        for rank, (i, _s) in enumerate(sorted(sc.items(), key=lambda kv: -kv[1])[:100]):
            fused[i] += weights[name] / (RRF_K + rank + 1)
            why[i].append(name)
    ranked = sorted(fused.items(), key=lambda kv: -kv[1])
    maxf = ranked[0][1] if ranked else 1.0
    chunks, used, seen_sig = [], 0, []
    for i, f in ranked:
        r = idx.rows[i]
        sig = set(idx.tokens[i])
        if any(len(sig & s) / max(1, len(sig | s)) > 0.85 for s in seen_sig):
            continue  # near-duplicate evidence adds nothing
        body = r.text if used + len(r.text) <= max_context_chars else r.text[:max(0, max_context_chars - used)]
        if not body.strip():
            break
        seen_sig.append(sig)
        used += len(body)
        chunks.append({"kind": "chunk", "chunkId": r.id, "documentId": r.document_id, "document": r.document.title, "docType": r.document.doc_type,
                       "heading": r.heading, "page": r.page, "text": body, "truncated": len(body) < len(r.text), "score": round(f / maxf, 4),
                       "signals": why[i], "concepts": r.concepts, "entities": r.entities, "classification": r.classification,
                       "lexicalScore": round(lex.get(i, 0), 3), "vectorScore": round(signals.get("vector", {}).get(i, 0), 3)})
        if len(chunks) >= k:
            break
    ent_items, budget = [], max_context_chars - used
    for it in sorted(items_entities, key=lambda x: -x["score"]):
        if any(e["entityId"] == it["entityId"] for e in ent_items):
            continue
        if budget - len(it["text"]) < 0:
            break
        budget -= len(it["text"])
        ent_items.append(it)
    context = [*ent_items, *chunks]
    for n, c in enumerate(context, 1):
        c["cite"] = n
    return {"question": text, "context": context, "structured": struct, "expansion": {"classes": exp["classes"], "added": exp["expandedWith"]},
            "seedEntities": seeds[:20], "signals": {k_: len(v) for k_, v in signals.items()}, "withheldByPolicy": withheld,
            "contextChars": max_context_chars - budget, "embedding": prov.name if prov else "off",
            "confidence": confidence(text, exp["terms"], chunks, ent_items, struct, signals, idx)}


# ---------------------------------------------------------------- confidence

def confidence(text, terms, chunks, ents, struct, signals, idx) -> dict:
    """0..1 with the reasons. Honest by construction: a structured graph hit is strong evidence; text needs real term coverage."""
    qt = {t for t, w in terms.items() if w >= 1.0}
    reasons, score = [], 0.0
    if struct and struct["matches"]:
        score = max(score, 0.9)
        reasons.append(f"graph pattern matched {struct['total']} {struct['resultClass']} entit{'y' if struct['total'] == 1 else 'ies'}")
    elif struct is not None and not struct["matches"]:
        reasons.append("the question's graph pattern matched nothing")
    if chunks and qt:
        top = chunks[0]
        covered = len(qt & set(toks(f"{top['heading']} {top['text']}"))) / len(qt)
        s = 0.15 + 0.75 * covered
        if "vector" in top["signals"] and top["vectorScore"] >= 0.6:
            s = max(s, 0.5 + 0.4 * top["vectorScore"])
        score = max(score, min(s, 0.95))
        reasons.append(f"top passage covers {round(covered * 100)}% of the question's terms")
        if len(chunks) > 1 and len(qt & set(toks(chunks[1]["text"]))) / len(qt) > 0.5:
            score = min(0.98, score + 0.05)
            reasons.append("a second passage corroborates")
    if ents and not chunks and not (struct and struct["matches"]):
        score = max(score, 0.55)
        reasons.append("entities named in the question were found in the knowledge graph")
    if not chunks and not ents and not (struct and struct["matches"]):
        reasons.append("no relevant evidence was found")
    label = "high" if score >= 0.7 else "medium" if score >= 0.45 else "low"
    return {"score": round(score, 3), "label": label, "reasons": reasons}
