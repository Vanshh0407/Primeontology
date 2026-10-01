"""Grounded answers with citations and hallucination controls.

Model independent: the default answerer is extractive (deterministic, nothing is invented). When an LLM is configured
(`use_llm`) it may phrase the answer, but every sentence must (1) cite a context item that exists and (2) be supported by it
(content-word + number check); unsupported sentences are removed, and if nothing survives the system abstains.
"""
import re

from .. import llm
from .retrieve import SYMBOL, retrieve, toks

ABSTAIN = "I could not find enough evidence in the connected knowledge to answer this."
MIN_CONFIDENCE = 0.3
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[])")
_NUMS = re.compile(r"\d[\d,]*\.?\d*")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(re.sub(r"\s+", " ", text)) if len(s.strip()) > 3]


def _support(sentence: str, evidence: str) -> float:
    st = {t for t in toks(sentence) if not t.isdigit()}
    if not st:
        return 1.0
    return len(st & set(toks(evidence))) / len(st)


def _extractive(res: dict) -> tuple[list[dict], str]:
    """Answer sentences built only from retrieved material: [{text, cites}]."""
    out, ctx = [], res["context"]
    s = res["structured"]
    by_entity = {c["entityId"]: c for c in ctx if c["kind"] == "entity"}
    if s and s["matches"]:
        names = []
        for m in s["matches"][:10]:
            c = by_entity.get(m["entityId"])
            names.append((c["title"] if c else f"#{m['entityId']}", c["cite"] if c else None))
        conds = ", ".join(f"{c['class']}.{c['property']} {SYMBOL[c['op']]} {c['value']}" for c in s["constraints"])
        listed = ", ".join(n for n, _ in names) + (f" (and {s['total'] - len(names)} more)" if s["total"] > len(names) else "")
        cites = [c for _, c in names if c]
        out.append({"text": f"{s['total']} {s['resultClass']} match{'es' if s['total'] != 1 else ''}"
                            + (f" (condition: {conds})" if conds else "") + f": {listed}.", "cites": cites, "basis": "graph"})
    texts = [c for c in ctx if c["kind"] == "chunk"]
    qt = {t for t in toks(res["question"])}
    scored = []
    for c in texts[:4]:
        for sent in _sentences(c["text"]):
            st = set(toks(sent))
            if qt and st:
                scored.append((len(qt & st) / len(qt) + 0.1 * len(qt & st) / len(st), sent, c["cite"]))
    for score, sent, cite in sorted(scored, key=lambda x: -x[0])[:3]:
        if score >= 0.2:
            out.append({"text": sent, "cites": [cite], "basis": "document"})
    if not out:
        for c in ctx:
            if c["kind"] == "entity":
                out.append({"text": c["text"] + ".", "cites": [c["cite"]], "basis": "graph"})
                break
    return out, "extractive"


def _llm_answer(res: dict) -> tuple[list[dict], str, dict]:
    ctx = res["context"]
    block = "\n\n".join(f"[{c['cite']}] ({c['kind']}{' - ' + c['document'] if c.get('document') else ''}) {c['text']}" for c in ctx)
    system = ("Answer ONLY from the numbered context. Every sentence must end with the citation numbers of the context items that support it, "
              "like [1][3]. If the context does not contain the answer, reply exactly: INSUFFICIENT. Never use outside knowledge.")
    raw = llm.complete(system, f"Context:\n{block}\n\nQuestion: {res['question']}", max_tokens=700)
    stats = {"sentences": 0, "droppedUncited": 0, "droppedUnsupported": 0}
    if raw.strip().upper().startswith("INSUFFICIENT"):
        return [], "llm", stats
    by_cite = {c["cite"]: c for c in ctx}
    kept = []
    for sent in _sentences(raw):
        stats["sentences"] += 1
        cites = [int(x) for x in re.findall(r"\[(\d+)\]", sent)]
        valid = [c for c in cites if c in by_cite]
        clean = re.sub(r"\s*\[\d+\]", "", sent).strip()
        if not valid:
            stats["droppedUncited"] += 1
            continue
        evidence = " ".join(by_cite[c]["text"] for c in valid)
        nums = {n.replace(",", "") for n in _NUMS.findall(clean)}
        if _support(clean, evidence) < 0.5 or any(n not in evidence.replace(",", "") for n in nums):
            stats["droppedUnsupported"] += 1
            continue
        kept.append({"text": clean, "cites": valid, "basis": "llm"})
    return kept, "llm", stats


def classify(sentences: list[dict], confidence: dict, stats: dict | None) -> str:
    if not sentences:
        return "ungrounded"
    dropped = (stats or {}).get("droppedUncited", 0) + (stats or {}).get("droppedUnsupported", 0)
    if confidence["score"] >= 0.6 and not dropped:
        return "grounded"
    return "partially_grounded"


def answer(ontology, question: str, *, role="viewer", use_llm=False, min_confidence=MIN_CONFIDENCE, **kw) -> dict:
    res = retrieve(ontology, question, role=role, **kw)
    conf = res["confidence"]
    sentences, mode, stats, note = [], "extractive", None, None
    if conf["score"] < min_confidence:
        note = "abstained: retrieval confidence below the threshold"
    else:
        if use_llm and llm.available():
            try:
                sentences, mode, stats = _llm_answer(res)
                if not sentences:  # the model abstained or nothing it said was supported -> fall back to what we can prove
                    sentences, mode = _extractive(res)[0], "extractive (model output rejected)"
            except Exception as e:  # noqa: BLE001 - never fail the request because the model is down
                sentences, mode = _extractive(res)[0], "extractive (model unavailable)"
                note = f"model unavailable: {str(e)[:120]}"
        else:
            sentences, mode = _extractive(res)
            if use_llm:
                note = "no LLM configured - extractive answer"
    by_cite = {c["cite"]: c for c in res["context"]}
    cited = sorted({c for s in sentences for c in s["cites"]})
    citations = []
    for n in cited:
        c = by_cite[n]
        citations.append({"cite": n, "kind": c["kind"], "title": c.get("document") or c.get("title"), "heading": c.get("heading"), "page": c.get("page"),
                          "entityId": c.get("entityId"), "documentId": c.get("documentId"), "chunkId": c.get("chunkId"), "sources": c.get("sources"),
                          "classification": c.get("classification"), "excerpt": c["text"][:300]})
    grounding = classify(sentences, conf, stats)
    text = " ".join(f"{s['text']} " + "".join(f"[{c}]" for c in s["cites"]) for s in sentences).strip() if sentences else ABSTAIN
    return {"question": question, "answer": text, "grounding": grounding, "abstained": not sentences, "confidence": conf, "mode": mode, "note": note,
            "citations": citations, "sentences": sentences, "verification": stats, "structured": res["structured"], "expansion": res["expansion"],
            "context": res["context"], "withheldByPolicy": res["withheldByPolicy"], "embedding": res["embedding"], "signals": res["signals"]}
