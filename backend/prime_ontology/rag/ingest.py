"""Ontology-aware chunking + indexing of documents.

Chunks never cross a section boundary (headings are respected), carry their heading as context, and are tagged with the
ontology classes they mention and the fabric entities they name - that tagging is what enables graph-aware retrieval.
"""
import base64
import hashlib
import os
import re

import numpy as np

from .. import embeddings, query
from ..fabric.models import FabricEntity
from ..ingest import IngestError
from ..ingest.documents import parse_document, split_sections
from .models import CLASSIFICATIONS, RagChunk, RagDocument

TARGET_WORDS, OVERLAP_WORDS, MAX_DOC_CHARS = 140, 25, 5_000_000
MAX_CHUNKS_PER_DOC = 5000
DOC_EXT = (".pdf", ".docx", ".pptx", ".html", ".htm", ".txt", ".md", ".markdown")
_WORD = re.compile(r"\S+")


def chunk_sections(sections: list[dict]) -> list[dict]:
    out = []
    for s in sections:
        words = _WORD.findall(s.get("text") or "")
        if not words:
            if s.get("heading"):
                out.append({"heading": s["heading"], "page": s.get("page"), "text": s["heading"]})
            continue
        i = 0
        while i < len(words):
            piece = words[i:i + TARGET_WORDS]
            # prefer to end on a sentence boundary when the section continues
            if i + TARGET_WORDS < len(words):
                for j in range(len(piece) - 1, int(len(piece) * 0.6), -1):
                    if piece[j].endswith((".", "!", "?", ";")):
                        piece = piece[:j + 1]
                        break
            out.append({"heading": s.get("heading") or "", "page": s.get("page"), "text": " ".join(piece)})
            if i + len(piece) >= len(words):
                break
            i += max(1, len(piece) - OVERLAP_WORDS)
    return out[:MAX_CHUNKS_PER_DOC]


# ------------------------------------------------------------ entity linking

class EntityIndex(dict):
    cls: dict  # entity id -> class name


def entity_index(ontology) -> EntityIndex:
    """normalised surface form -> [entity ids]. Names and a few identifying values (emails, ids) of active fabric entities."""
    idx = EntityIndex()
    idx.cls = {}
    for eid, cname, name, canon in FabricEntity.objects.filter(ontology=ontology, status="active").values_list(
            "id", "class_name", "display_name", "canonical").iterator():
        idx.cls[eid] = cname
        forms = {name}
        for k, v in (canon or {}).items():
            if isinstance(v, str) and ("email" in k.lower() or k.lower().endswith("id") or "number" in k.lower() or "code" in k.lower()):
                forms.add(v)
        for f in forms:
            key = " ".join(re.findall(r"[a-z0-9@._-]+", str(f).lower()))
            if len(key) >= 3:
                idx.setdefault(key, []).append(eid)
    return idx


def concepts_for(model, text: str, entity_ids: list[int], idx) -> list[str]:
    """Classes named in the text plus the classes of the entities it mentions ('Acme Steel' is a Supplier even if the word never appears)."""
    found = [g["class"] for g in query.ground(model, text)] + [idx.cls[e] for e in entity_ids if e in idx.cls]
    return list(dict.fromkeys(found))[:12]


def link_entities(text: str, idx: dict) -> list[int]:
    toks = re.findall(r"[a-z0-9@._-]+", text.lower())
    toks = [t.strip(".,-_") for t in toks]
    found, longest = [], max((len(k.split()) for k in idx), default=1)
    for i in range(len(toks)):
        for n in range(min(longest, 6, len(toks) - i), 0, -1):
            hit = idx.get(" ".join(toks[i:i + n]))
            if hit and len(hit) <= 3:  # an ambiguous surface form (many entities) is not evidence of any one of them
                found.extend(hit)
                break
    return list(dict.fromkeys(found))[:30]


def vec_bytes(v) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def embed_chunks(chunks: list[RagChunk]):
    prov = embeddings.get_provider()
    if prov is None or not chunks:
        return
    vecs = embeddings.embed([(c.heading + ". " if c.heading else "") + c.text[:2000] for c in chunks])
    for c, v in zip(chunks, vecs):
        c.embedding, c.embedding_provider = vec_bytes(v), prov.name


def ingest_document(ontology, *, title: str, text: str | None = None, filename: str = "", content_b64: str = "", doc_type="document",
                    classification="internal", metadata=None, source_uri="", actor="") -> RagDocument:
    title = (title or filename or "").strip()[:300]
    if not title:
        raise IngestError("A document needs a title.")
    if classification not in CLASSIFICATIONS:
        raise IngestError(f"classification must be one of {', '.join(CLASSIFICATIONS)}.")
    if metadata is not None and not isinstance(metadata, dict):
        raise IngestError("metadata must be an object.")
    if content_b64:
        ext = os.path.splitext(filename.lower())[1]
        if ext not in DOC_EXT:
            raise IngestError(f"Unsupported file type '{ext}'. Supported: {', '.join(DOC_EXT)}.")
        try:
            data = base64.b64decode(content_b64, validate=True)
        except Exception:  # noqa: BLE001
            raise IngestError("contentBase64 is not valid base64.") from None
        if len(data) > 25 * 1024 * 1024:
            raise IngestError("Document larger than 25 MB.")
        sections = parse_document(ext, data)["sections"]
    else:
        if not text or not text.strip():
            raise IngestError("Provide text or a file (filename + contentBase64).")
        if len(text) > MAX_DOC_CHARS:
            raise IngestError("Text longer than 5 million characters.")
        sections = split_sections(text)
    chunks_raw = chunk_sections(sections)
    if not chunks_raw:
        raise IngestError("No text could be extracted.")
    digest = hashlib.sha1("\n".join(c["text"] for c in chunks_raw).encode()).hexdigest()
    existing = RagDocument.objects.filter(ontology=ontology, title=title).first()
    if existing and existing.content_hash == digest:
        existing.metadata, existing.doc_type, existing.classification = metadata or existing.metadata, doc_type, classification
        existing.save()
        existing.chunks.update(classification=classification)
        return existing  # identical content: nothing to re-index
    doc = existing or RagDocument(ontology=ontology, title=title, created_by=actor)
    doc.doc_type, doc.classification, doc.metadata, doc.source_uri, doc.content_hash = doc_type, classification, metadata or {}, source_uri, digest
    doc.save()
    doc.chunks.all().delete()  # a changed document replaces its previous chunks (no stale evidence)
    idx = entity_index(ontology)
    rows = []
    for i, c in enumerate(chunks_raw):
        full = f"{c['heading']} {c['text']}"
        ents = link_entities(full, idx)
        rows.append(RagChunk(document=doc, ontology=ontology, ordinal=i, heading=c["heading"][:300], page=c["page"], text=c["text"],
                             concepts=concepts_for(ontology.model, full, ents, idx), entities=ents, classification=classification))
    embed_chunks(rows)
    RagChunk.objects.bulk_create(rows, batch_size=500)
    doc.chunk_count = len(rows)
    doc.save(update_fields=["chunk_count", "updated_at"])
    return doc


def reindex(ontology, vectors: bool = True) -> dict:
    """Re-tag concepts/entities and recompute vectors (after a fabric sync, an ontology change or a provider change)."""
    idx = entity_index(ontology)
    n = 0
    batch = []
    for c in RagChunk.objects.filter(ontology=ontology).iterator(chunk_size=500):
        full = f"{c.heading} {c.text}"
        c.entities = link_entities(full, idx)
        c.concepts = concepts_for(ontology.model, full, c.entities, idx)
        batch.append(c)
        if len(batch) >= 200:
            if vectors:
                embed_chunks(batch)
            RagChunk.objects.bulk_update(batch, ["concepts", "entities", "embedding", "embedding_provider"])
            n, batch = n + len(batch), []
    if batch:
        if vectors:
            embed_chunks(batch)
        RagChunk.objects.bulk_update(batch, ["concepts", "entities", "embedding", "embedding_provider"])
        n += len(batch)
    return {"chunks": n, "provider": (embeddings.get_provider().name if embeddings.get_provider() else "off")}


def _on_sync_finished(source, run):
    if RagChunk.objects.filter(ontology_id=source.ontology_id).exists() and run.status in ("succeeded", "partial"):
        reindex(source.ontology, vectors=False)  # only entity links change when fabric data changes


def register_hooks():
    from ..fabric import hooks

    hooks.register("sync_finished", _on_sync_finished)
