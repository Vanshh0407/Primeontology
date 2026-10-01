"""Optional LLM refinement of a document candidate.

The LLM may *propose* extra concepts/relationships. A proposal is kept only if it quotes text that really occurs in the
document (verified here, with page/section located), so the model cannot invent evidence. Everything it adds is flagged
(`source.origin = "llm"`, confidence capped at 0.55) and, like every candidate, still needs human review before saving.
"""
import copy
import re

from . import llm
from .model_ops import NAME_RE
from .naming import to_label

MAX_NEW_CLASSES = 15
MAX_CHARS = 14000

SYSTEM = """You extract an ontology (concepts and relationships) from a document for human review.
Return ONLY JSON: {"classes":[{"name":PascalCase,"parents":[existing or new class names],"comment":str,"quote":str}],
"relationships":[{"domain":Class,"name":camelCaseVerb,"range":Class,"quote":str}]}.
Rules: every item MUST include "quote": an exact, verbatim sentence or phrase copied from the document that supports it.
Do not repeat concepts that are already listed. At most 15 classes. No prose outside the JSON."""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _locate(quote: str, sections: list[dict]):
    q = _norm(quote)
    if len(q) < 8:
        return None
    for s in sections:
        hay = _norm(f'{s.get("heading") or ""} {s["text"]}')
        i = hay.find(q)
        if i >= 0:
            return {"page": s.get("page"), "section": s.get("heading"), "snippet": quote.strip()[:200]}
    return None


def refine(model: dict, sections: list[dict], document: str) -> tuple[dict, str]:
    if not llm.available():
        return model, "AI refinement requested but no LLM is configured (set ANTHROPIC_API_KEY); showing the rule-based extraction only."
    existing = [c["name"] for c in model["classes"]]
    text = "\n\n".join(f'[{s.get("heading") or "section"}]\n{s["text"]}' for s in sections)[:MAX_CHARS]
    try:
        data = llm.extract_json(llm.complete(SYSTEM, f"Already extracted concepts: {', '.join(existing) or '(none)'}\n\nDOCUMENT:\n{text}", max_tokens=2500))
    except Exception as e:
        return model, f"AI refinement failed ({e}); showing the rule-based extraction only."
    out = copy.deepcopy(model)
    names = {c["name"].lower(): c["name"] for c in out["classes"]}
    added, dropped = 0, 0
    for c in (data.get("classes") or [])[:MAX_NEW_CLASSES * 2]:
        name = str(c.get("name", "")).strip()
        ev = _locate(str(c.get("quote", "")), sections)
        if not NAME_RE.match(name) or name.lower() in names or ev is None or added >= MAX_NEW_CLASSES:
            dropped += 1
            continue
        out["classes"].append({"name": name, "label": to_label(name), "comment": str(c.get("comment", ""))[:300], "parents": [],
                               "confidence": 0.55, "occurrences": 1, "evidence": [{"document": document, **ev}],
                               "source": {"document": document, "origin": "llm"}})
        names[name.lower()] = name
        added += 1
    for c in (data.get("classes") or []):  # parents resolved after all classes exist
        nm = names.get(str(c.get("name", "")).lower())
        cls = next((x for x in out["classes"] if x["name"] == nm), None)
        if cls and cls.get("source", {}).get("origin") == "llm":
            cls["parents"] = [names[p.lower()] for p in (c.get("parents") or []) if isinstance(p, str) and p.lower() in names and names[p.lower()] != nm]
    rel_added = 0
    have = {(p["domain"], p["name"], p["range"]) for p in out["objectProperties"]}
    for r in (data.get("relationships") or [])[:40]:
        d, n, g = names.get(str(r.get("domain", "")).lower()), str(r.get("name", "")).strip(), names.get(str(r.get("range", "")).lower())
        ev = _locate(str(r.get("quote", "")), sections)
        if not (d and g and NAME_RE.match(n) and ev) or (d, n, g) in have:
            dropped += 1
            continue
        out["objectProperties"].append({"name": n, "label": to_label(n), "domain": d, "range": g, "cardinality": "many-to-many", "required": False,
                                        "confidence": 0.5, "evidence": [{"document": document, **ev}], "source": {"document": document, "origin": "llm"}})
        have.add((d, n, g))
        rel_added += 1
    note = f"AI added {added} concept(s) and {rel_added} relationship(s), each backed by a verified quote from the document"
    if dropped:
        note += f"; discarded {dropped} suggestion(s) that were duplicates, invalid or had a quote not found in the document"
    return out, note + ". Review them like any other candidate."
