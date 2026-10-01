"""AI Semantic Mapping Studio (R5): propose source-field -> ontology mappings.

Deterministic, explainable scorer (abbreviation expansion, synonym groups,
token overlap, string similarity, datatype compatibility, table/class
context). `propose_mappings` is the single proposal interface: an LLM ranker
can be layered on top (see assistant.rerank_with_llm) without changing the
review workflow. Proposals NEVER modify the ontology.
"""
import difflib
import re

from . import embeddings

from .naming import singular, words

ABBREV = {
    "cust": "customer", "cstmr": "customer", "clnt": "client", "nm": "name", "nme": "name", "qty": "quantity", "amt": "amount",
    "addr": "address", "adr": "address", "desc": "description", "descr": "description", "dt": "date", "num": "number",
    "no": "number", "nbr": "number", "ph": "phone", "tel": "phone", "org": "organization", "orgn": "organization",
    "inv": "invoice", "po": "purchaseorder", "emp": "employee", "dept": "department", "mgr": "manager", "prod": "product",
    "sup": "supplier", "supp": "supplier", "vndr": "vendor", "ctry": "country", "cntry": "country", "st": "state",
    "zip": "postalcode", "pin": "postalcode", "pmt": "payment", "pay": "payment", "acct": "account", "ref": "reference",
    "id": "identifier", "cd": "code", "typ": "type", "ts": "timestamp", "tm": "time", "eff": "effective", "exp": "expiration",
    "expiry": "expiration", "ord": "order", "sls": "sales", "cntrct": "contract", "agmt": "agreement", "cnt": "count",
    "val": "value", "pct": "percent", "mail": "email", "mob": "mobile", "tot": "total", "curr": "currency", "cur": "currency",
}
SYNONYMS = [
    {"customer", "client", "buyer", "purchaser", "account", "consumer"},
    {"supplier", "vendor", "seller", "provider", "contractor"},
    {"contract", "agreement", "deal"},
    {"name", "title", "label"},
    {"identifier", "key", "uid", "guid", "uuid"},
    {"amount", "total", "sum", "price", "cost", "value", "fee"},
    {"quantity", "count", "units"},
    {"date", "timestamp", "datetime", "time", "day"},
    {"party", "counterparty", "signatory"},
    {"phone", "mobile", "telephone", "contact"},
    {"address", "location"},
    {"order", "purchaseorder", "salesorder"},
    {"expiration", "end", "termination", "expiry"},
    {"effective", "start", "commencement"},
    {"postalcode", "postcode"},
    {"organization", "company", "business", "firm", "enterprise"},
]
_CANON = {}
for grp in SYNONYMS:
    rep = sorted(grp)[0]
    for w in grp:
        _CANON[w] = rep

TYPE_GROUPS = {"integer": "num", "int": "num", "long": "num", "short": "num", "decimal": "num", "float": "num", "double": "num",
               "numeric": "num", "bigint": "num", "nonNegativeInteger": "num", "string": "str", "varchar": "str",
               "text": "str", "boolean": "bool", "bool": "bool", "date": "time", "dateTime": "time", "timestamp": "time",
               "time": "time"}


def tokens(s: str, canon=True) -> list[str]:
    out = []
    for w in words(s):
        w = ABBREV.get(w, w)
        w = singular(w)
        out.append(_CANON.get(w, w) if canon else w)
    return out


def _sim(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    if a == b or sorted(a) == sorted(b):
        return 1.0
    sa, sb = set(a), set(b)
    jac = len(sa & sb) / len(sa | sb)
    seq = difflib.SequenceMatcher(None, "".join(a), "".join(b)).ratio()
    return 0.6 * jac + 0.4 * seq


def _type_score(src_type, xsd):
    if not src_type:
        return None
    g1, g2 = TYPE_GROUPS.get(str(src_type).lower(), TYPE_GROUPS.get(src_type, "str")), TYPE_GROUPS.get(xsd, "str")
    if g1 == g2:
        return 1.0
    return 0.5 if "str" in (g1, g2) else 0.0


def _label(score):
    return "HIGH" if score >= 0.85 else "MEDIUM" if score >= 0.6 else "LOW"


def propose_mappings(sources: list[dict], model: dict, top_k=3, min_score=0.35) -> dict:
    """sources: [{"name": "CRM", "fields": [{"name": "cust_nm", "type": "varchar"}]}]"""
    class_toks = {c["name"]: [tokens(c["name"]), *[tokens(s) for s in c.get("synonyms", [])]] for c in model["classes"]}
    targets = []
    for c in model["classes"]:
        targets.append({"target": c["name"], "kind": "class", "cls": c["name"], "toks": tokens(c["name"]), "xsd": None,
                        "alts": [tokens(s) for s in c.get("synonyms", [])] + [tokens(c.get("label") or "")]})
    for p in model["dataProperties"]:
        targets.append({"target": f'{p["domain"]}.{p["name"]}', "kind": "dataProperty", "cls": p["domain"],
                        "toks": tokens(p["name"]), "xsd": p.get("datatype"), "alts": [tokens(p.get("label") or "")]})
    for p in model["objectProperties"]:
        targets.append({"target": f'{p["domain"]}.{p["name"]}', "kind": "objectProperty", "cls": p["domain"],
                        "toks": tokens(p["name"]), "xsd": None, "alts": [tokens(p["range"])], "range": p["range"]})
    # Semantic booster: only when a real embedding model is running (the hash fallback adds nothing over the lexical scorer).
    prov = embeddings.get_provider()
    use_sem = bool(prov and prov.name == "fastembed")
    vec = {}
    if use_sem:
        readable = lambda s_: " ".join(tokens(s_, canon=False))
        for t in targets:
            t["text"] = readable(t["target"].replace(".", " "))
        field_texts = [readable(f["name"]) for src_ in sources for f in src_["fields"]]
        texts = list(dict.fromkeys([t["text"] for t in targets] + field_texts))
        vec = dict(zip(texts, embeddings.embed(texts)))

    def semantic(ftext, ttext):
        if not use_sem or ftext not in vec or ttext not in vec:
            return 0.0
        return max(0.0, min(1.0, (embeddings.cosine(vec[ftext], vec[ttext]) - prov.lo) / (prov.hi - prov.lo)))

    out, by_target = [], {}
    for src in sources:
        s_ctx = tokens(src["name"])
        for f in src["fields"]:
            f_toks = tokens(f["name"])
            f_text = " ".join(tokens(f["name"], canon=False))
            cands = []
            for t in targets:
                reasons = []
                if t["kind"] == "class":
                    name_s = max([_sim(f_toks, t["toks"])] + [_sim(f_toks, a) for a in t["alts"] if a]) * 0.92
                    sem = semantic(f_text, t.get("text", ""))
                    if sem and 0.5 * name_s + 0.5 * sem > name_s and name_s < 0.9:
                        name_s = min(0.9, 0.5 * name_s + 0.5 * sem)
                        reasons.append("semantic similarity (embedding model)")
                    if name_s >= 0.35:
                        reasons.append("field name matches concept name" if name_s > 0.85 else "similar to concept name")
                    score = name_s
                else:
                    # property tokens; class tokens may be embedded in the field name (customer_name -> Customer.name)
                    full = t["toks"]
                    prop_only = [x for x in full if x not in class_toks[t["cls"]][0]] or full
                    name_s = max(_sim(f_toks, full), _sim(f_toks, prop_only) * 0.9,
                                 _sim(f_toks, class_toks[t["cls"]][0] + prop_only) if prop_only else 0)
                    for a in t["alts"]:
                        if a:
                            name_s = max(name_s, _sim(f_toks, a) * 0.9)
                    sem = semantic(f_text, t.get("text", ""))
                    if sem and 0.5 * name_s + 0.5 * sem > name_s and name_s < 0.9:
                        name_s = min(0.9, 0.5 * name_s + 0.5 * sem)
                        reasons.append("semantic similarity (embedding model)")
                    ctx = max(_sim(s_ctx, class_toks[t["cls"]][0]), *[_sim(s_ctx, a) for a in class_toks[t["cls"]][1:]] or [0])
                    tscore = _type_score(f.get("type"), t["xsd"]) if t["kind"] == "dataProperty" else None
                    if name_s >= 0.99:
                        reasons.append("normalized-name match")
                    elif name_s >= 0.5:
                        reasons.append("semantic token match")
                    if set(f_toks) & set(class_toks[t["cls"]][0]):
                        reasons.append("field name includes concept")
                    parts, weights = [name_s], [0.7]
                    if ctx > 0.5:
                        parts.append(ctx); weights.append(0.15); reasons.append("source name matches concept")
                    if tscore is not None:
                        parts.append(tscore); weights.append(0.15)
                        reasons.append("compatible datatype" if tscore >= 0.99 else "datatype partly compatible" if tscore > 0 else "datatype mismatch")
                    score = sum(p * w for p, w in zip(parts, weights)) / sum(weights)
                    if name_s < 0.35:
                        score *= 0.4
                if score >= min_score:
                    cands.append({"target": t["target"], "kind": t["kind"], "score": round(min(score, 0.999), 3),
                                  "confidence": _label(score), "reasons": reasons or ["weak similarity"]})
            cands.sort(key=lambda c: -c["score"])
            best = cands[0] if cands else None
            row = {"source": src["name"], "field": f["name"], "type": f.get("type"), "candidates": cands[:top_k],
                   "best": best, "status": "proposed" if best else "unmapped",
                   "target": best["target"] if best else None, "score": best["score"] if best else 0}
            out.append(row)
            if best:
                by_target.setdefault(best["target"], []).append(row)
    dups = [{"target": t, "fields": [f'{r["source"]}.{r["field"]}' for r in rows]} for t, rows in by_target.items() if len(rows) > 1]
    n = len(out)
    return {"proposals": out, "duplicates": dups,
            "summary": {"fields": n, "proposed": sum(1 for r in out if r["best"]), "unmapped": sum(1 for r in out if not r["best"]),
                        "high": sum(1 for r in out if r["best"] and r["best"]["confidence"] == "HIGH")},
            "embeddings": embeddings.status()}


def sources_from_schema(schema: dict, name: str | None = None) -> list[dict]:
    """Uploaded/introspected schema -> mapping sources (one per table)."""
    return [{"name": t["name"] if not name else f'{name}.{t["name"]}',
             "fields": [{"name": c["name"], "type": c["type"]} for c in t["columns"]]} for t in schema["tables"]]


def commit_mappings(model: dict, mappings: list[dict], set_name: str) -> tuple[dict, int]:
    """Record accepted mappings as lineage on ontology elements (explicit user action)."""
    import copy

    m = copy.deepcopy(model)
    n = 0
    for x in mappings:
        if x.get("status") not in ("accepted", "overridden") or not x.get("target"):
            continue
        cls, _, prop = x["target"].partition(".")
        elem = next((c for c in m["classes"] if c["name"] == cls), None) if not prop else next(
            (p for p in m["dataProperties"] + m["objectProperties"] if p["domain"] == cls and p["name"] == prop), None)
        if elem is None:
            continue
        lin = elem.setdefault("mappedFrom", [])
        entry = {"source": x["source"], "field": x["field"], "mappingSet": set_name, "score": x.get("score")}
        if entry not in lin:
            lin.append(entry)
            n += 1
    return m, n
