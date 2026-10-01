"""Semantic agent memory: remember (embedding + ontology tags) and recall (vector + lexical blend). Scoped per agent."""
import numpy as np

from .. import embeddings, query
from ..rag.ingest import vec_bytes
from ..rag.retrieve import toks
from .models import AgentMemory

MAX_TEXT = 2000
MAX_PER_AGENT = 2000


def remember(agent, text: str, kind="semantic", run_id=None) -> AgentMemory:
    text = (text or "").strip()[:MAX_TEXT]
    if not text:
        raise ValueError("Nothing to remember.")
    if kind not in ("episodic", "semantic"):
        kind = "semantic"
    prov = embeddings.get_provider()
    emb, name = (None, "")
    if prov is not None:
        emb, name = vec_bytes(embeddings.embed([text])[0]), prov.name
    m = AgentMemory.objects.create(agent=agent, ontology=agent.ontology, kind=kind, text=text, run_id=run_id, embedding=emb, embedding_provider=name,
                                   concepts=list(dict.fromkeys(g["class"] for g in query.ground(agent.ontology.model, text)))[:10])
    extra = AgentMemory.objects.filter(agent=agent).count() - MAX_PER_AGENT
    if extra > 0:  # bounded: forget the oldest episodic memories first
        old = list(AgentMemory.objects.filter(agent=agent).order_by("kind", "id").values_list("id", flat=True)[:extra])
        AgentMemory.objects.filter(id__in=old).delete()
    return m


def recall(agent, text: str, limit=5) -> list[dict]:
    qt = set(toks(text))
    if not qt:
        return []
    prov = embeddings.get_provider()
    qv = np.asarray(embeddings.embed([text])[0], dtype=np.float32) if prov is not None else None
    scored = []
    for m in AgentMemory.objects.filter(agent=agent)[:1500]:
        mt = set(toks(m.text))
        lex = len(qt & mt) / len(qt)
        vec = 0.0
        if qv is not None and m.embedding and m.embedding_provider == prov.name:
            vec = max(0.0, min(1.0, (float(np.frombuffer(m.embedding, dtype=np.float32) @ qv) - prov.lo) / (prov.hi - prov.lo)))
            vec = vec if prov.name == "fastembed" else vec * 0.5
        s = max(lex, 0.9 * vec) + 0.1 * min(lex, vec)
        if s >= 0.25:
            scored.append((s, m))
    scored.sort(key=lambda x: (-x[0], -x[1].id))
    return [{"id": m.id, "kind": m.kind, "text": m.text, "score": round(s, 3), "at": m.created_at.isoformat(), "runId": m.run_id} for s, m in scored[:limit]]
