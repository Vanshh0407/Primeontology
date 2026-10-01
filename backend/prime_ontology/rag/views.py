"""Semantic RAG API (R12)."""
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .. import embeddings, llm, versioning
from ..identity import can, requires
from ..ingest import IngestError
from ..views import ApiError, api, body, get_ontology
from . import answer as ans
from . import ingest, retrieve
from .models import RagChunk, RagDocument


def doc_json(d: RagDocument) -> dict:
    return {"id": d.id, "title": d.title, "docType": d.doc_type, "classification": d.classification, "metadata": d.metadata, "chunks": d.chunk_count,
            "sourceUri": d.source_uri, "createdBy": d.created_by, "updatedAt": d.updated_at.isoformat()}


def _doc(o, did) -> RagDocument:
    d = RagDocument.objects.filter(pk=did, ontology=o).first()
    if not d:
        raise ApiError("Document not found.", 404)
    return d


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def documents(request, pk):
    o = get_ontology(request, pk)
    role = request.identity["role"]
    if request.method == "POST":
        if not can(role, "write"):
            raise ApiError("Role cannot add documents.", 403)
        b = body(request)
        if b.get("classification") == "restricted" and not can(role, "review"):
            raise ApiError("Only reviewers/admins can add restricted documents.", 403)
        try:
            d = ingest.ingest_document(o, title=b.get("title", ""), text=b.get("text"), filename=b.get("filename", ""), content_b64=b.get("contentBase64", ""),
                                       doc_type=str(b.get("docType", "document"))[:40], classification=b.get("classification", "internal"),
                                       metadata=b.get("metadata"), source_uri=str(b.get("sourceUri", ""))[:500], actor=request.identity["user"])
        except IngestError as e:
            raise ApiError(str(e))
        retrieve.invalidate(o.id)
        versioning.audit(o, "rag.document.indexed", request.identity["user"], document=d.title, chunks=d.chunk_count, classification=d.classification)
        return JsonResponse(doc_json(d), status=201)
    allowed = retrieve.allowed_classifications(role)
    docs = RagDocument.objects.filter(ontology=o, classification__in=allowed)
    return JsonResponse({"documents": [doc_json(d) for d in docs[:500]], "chunks": RagChunk.objects.filter(ontology=o, classification__in=allowed).count(),
                         "embedding": embeddings.status(), "llm": llm.available()})


@api
@requires("read")
@require_http_methods(["GET", "DELETE"])
def document_detail(request, pk, did):
    o = get_ontology(request, pk)
    d = _doc(o, did)
    if d.classification not in retrieve.allowed_classifications(request.identity["role"]):
        raise ApiError("Document not found.", 404)
    if request.method == "DELETE":
        if not can(request.identity["role"], "write"):
            raise ApiError("Role cannot delete documents.", 403)
        versioning.audit(o, "rag.document.deleted", request.identity["user"], document=d.title)
        d.delete()
        retrieve.invalidate(o.id)
        return JsonResponse({"deleted": True})
    return JsonResponse({**doc_json(d), "chunkList": [{"id": c.id, "ordinal": c.ordinal, "heading": c.heading, "page": c.page, "text": c.text, "concepts": c.concepts,
                                                       "entities": c.entities} for c in d.chunks.all()[:300]]})


def _opts(b: dict) -> dict:
    f = b.get("filters") or {}
    if not isinstance(f, dict):
        raise ApiError("filters must be an object.")
    return {"k": b.get("k", 8), "filters": f, "max_context_chars": b.get("maxContextChars", 6000), "use_vectors": b.get("vectors", True),
            "use_graph": b.get("graph", True), "expand": b.get("expand", True), "hops": max(0, min(int(b.get("hops", 2)), 3))}


@api
@requires("read")
@require_http_methods(["POST"])
def retrieve_view(request, pk):
    o = get_ontology(request, pk)
    b = body(request)
    try:
        res = retrieve.retrieve(o, b.get("question", ""), role=request.identity["role"], **_opts(b))
    except (TypeError, ValueError) as e:
        raise ApiError(str(e))
    return JsonResponse(res)


@api
@requires("read")
@require_http_methods(["POST"])
def ask_view(request, pk):
    o = get_ontology(request, pk)
    b = body(request)
    try:
        out = ans.answer(o, b.get("question", ""), role=request.identity["role"], use_llm=bool(b.get("useLlm")),
                         min_confidence=float(b.get("minConfidence", ans.MIN_CONFIDENCE)), **_opts(b))
    except (TypeError, ValueError) as e:
        raise ApiError(str(e))
    versioning.audit(o, "rag.asked", request.identity["user"], question=str(b.get("question", ""))[:200], grounding=out["grounding"], confidence=out["confidence"]["score"])
    return JsonResponse(out)


@api
@requires("read")
@require_http_methods(["POST"])
def reindex_view(request, pk):
    o = get_ontology(request, pk)
    if not can(request.identity["role"], "write"):
        raise ApiError("Role cannot re-index.", 403)
    out = ingest.reindex(o)
    retrieve.invalidate(o.id)
    return JsonResponse(out)


@api
@requires("read")
@require_http_methods(["GET"])
def export_vectors(request, pk):
    """JSON-lines for loading into an external vector database (Qdrant, pgvector, Pinecone, ...): id, text, metadata, vector.
    Only passages the caller's role may see, and only vectors made by the current embedding provider."""
    import json

    from django.http import HttpResponse

    o = get_ontology(request, pk)
    prov = embeddings.get_provider()
    allowed = retrieve.allowed_classifications(request.identity["role"])
    lines = []
    for c in RagChunk.objects.filter(ontology=o, classification__in=allowed).select_related("document").iterator():
        if not c.embedding or not prov or c.embedding_provider != prov.name:
            continue
        import numpy as np

        lines.append(json.dumps({"id": f"chunk-{c.id}", "text": c.text, "vector": [round(float(x), 6) for x in np.frombuffer(c.embedding, dtype=np.float32)],
                                 "metadata": {"document": c.document.title, "docType": c.document.doc_type, "heading": c.heading, "page": c.page,
                                              "concepts": c.concepts, "classification": c.classification, **c.document.metadata}}))
        if len(lines) >= 100000:
            break
    resp = HttpResponse("\n".join(lines) + ("\n" if lines else ""), content_type="application/x-ndjson")
    resp["X-Embedding-Provider"] = prov.name if prov else "off"
    return resp
