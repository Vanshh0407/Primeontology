import json
import re

from django.conf import settings
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import agent as agent_mod
from . import assistant, embedded, embeddings, generator, ingest, llm, mapping as mapping_mod, model_ops, query, reasoning, records, validation, versioning
from .identity import can, get_identity, requires
from .introspect import IntrospectionError, introspect
from .models import AuditEvent, MappingSet, Ontology, OntologyVersion, QueryRecord

# ----------------------------------------------------------------- helpers --


class ApiError(Exception):
    def __init__(self, msg, status=400, payload=None):
        super().__init__(msg)
        self.status = status
        self.payload = payload or {}


def api(view):
    """JSON error handling + CSRF exemption (host supplies auth; see identity.py)."""
    def wrapper(request, *a, **kw):
        try:
            return view(request, *a, **kw)
        except ApiError as e:
            return JsonResponse({**e.payload, "error": str(e)}, status=e.status)
        except versioning.GovernanceForbidden as e:
            return JsonResponse({"error": str(e)}, status=403)
        except (ValueError, KeyError, IntrospectionError, ingest.IngestError, versioning.GovernanceError) as e:
            msg = f"Missing or unknown: {e}" if isinstance(e, KeyError) else str(e)
            return JsonResponse({"error": msg}, status=400)
    wrapper.csrf_exempt = True
    wrapper.__name__ = view.__name__
    return csrf_exempt(wrapper)


def body(request) -> dict:
    try:
        b = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        raise ApiError("Request body must be valid JSON.")
    if not isinstance(b, dict):
        raise ApiError("Request body must be a JSON object.")
    return b


def get_ontology(request, pk) -> Ontology:
    ident = getattr(request, "identity", None) or get_identity(request)
    qs = Ontology.objects.all()
    if ident.get("tenant"):
        qs = qs.filter(tenant=ident["tenant"])
    try:
        return qs.get(pk=pk)
    except Ontology.DoesNotExist:
        raise ApiError("Ontology not found.", 404)


def summary(o: Ontology) -> dict:
    return {"id": o.id, "name": o.name, "context": o.context, "sourceType": o.source_type, "status": o.status,
            "currentVersion": o.current_version, "stats": generator.stats(o.model) if o.model else {},
            "updatedAt": o.updated_at.isoformat(), "modelRevision": o.model_revision,
            "branchOf": o.branch_of_id, "branchName": o.branch_name, "mergedAt": o.merged_at.isoformat() if o.merged_at else None}


def detail(o: Ontology) -> dict:
    return {**summary(o), "baseIri": o.base_iri, "model": o.model}


def check_db_host(cfg):
    allowed = getattr(settings, "PRIME_ONTOLOGY_ALLOWED_DB_HOSTS", None)
    if allowed and cfg.get("host", "localhost") not in allowed:
        raise ApiError("Database host is not in the allow-list configured for this deployment.", 403)


def mapping_rows(ms: MappingSet):
    return {"id": ms.id, "name": ms.name, "mappings": ms.mappings, "updatedAt": ms.updated_at.isoformat()}


def ensure_draft(o: Ontology, actor: str):
    if o.status != "draft":
        versioning.audit(o, "status.reopened", actor, previous=o.status)
        o.status = "draft"


# -------------------------------------------------------------- system -----

@api
@require_http_methods(["GET"])
def health(request):
    return JsonResponse({"status": "ok", "service": "prime-ontology", "release": "R10", "aiConfigured": llm.available(),
                         "embeddings": embeddings.status()["provider"]})


@api
@require_http_methods(["GET"])
def supported(request):
    from . import url_source
    from .ingest.documents import ocr_available

    return JsonResponse({"databases": ["mysql", "sql"], "files": ingest.SUPPORTED, "aiConfigured": llm.available(),
                         "exportFormats": list(generator.FORMATS), "ocr": ocr_available(), "urlSources": url_source.enabled(),
                         "embeddings": embeddings.status(), "sampleRecordsMax": records.MAX_ROWS_HARD})


# ---------------------------------------------------- sources & ingestion ---

@api
@requires("write")
@require_http_methods(["POST"])
def inspect_source(request):
    """Connect + introspect. Credentials are used for this call only and never stored."""
    b = body(request)
    check_db_host(b.get("config", {}))
    return JsonResponse({"schema": introspect(b.get("type", ""), b.get("config", {}))})


@api
@requires("write")
@require_http_methods(["POST"])
def generate(request):
    """{name, type, config, save?:bool=true} -> candidate or saved ontology."""
    b = body(request)
    check_db_host(b.get("config", {}))
    cfg = dict(b.get("config", {}))
    if b.get("sampleRows"):
        cfg["sampleRows"] = b["sampleRows"]  # opt-in: copy up to N rows per table in as individuals
    schema = b.get("schema") or introspect(b.get("type", ""), cfg)
    model = generator.generate_model(schema)
    mapping = ingest.source_mapping(schema, model)
    rec_report = None
    if any(t.get("sample") for t in schema["tables"]):
        model, rec_report = records.attach_individuals(model, schema, cfg.get("sampleRows"))
    for t in schema["tables"]:
        t.pop("sample", None)  # row data stays out of the schema preview
    result = {"schema": schema, "model": model, "stats": generator.stats(model), "mapping": mapping, "recordsReport": rec_report}
    if b.get("save", True):
        o = Ontology.objects.create(
            name=b.get("name") or f'{schema["source"].get("database", "database")} ontology',
            base_iri=b.get("baseIri") or generator.DEFAULT_BASE_IRI, context=b.get("context", ""),
            tenant=request.identity.get("tenant", ""), source_type=schema["source"].get("type", ""), model=model)
        versioning.audit(o, "ontology.generated", request.identity["user"], source=schema["source"].get("type"),
                         database=schema["source"].get("database"))
        result["ontology"] = detail(o)
    return JsonResponse(result, status=201 if b.get("save", True) else 200)


@api
@requires("write")
@require_http_methods(["POST"])
def ingest_url(request):
    """{url, authorization?} -> reviewable candidate from a JSON REST endpoint. Disabled unless hosts are allow-listed."""
    from . import url_source

    b = body(request)
    if not str(b.get("url", "")).strip():
        raise ApiError("url is required.")
    try:
        return JsonResponse(url_source.analyze_url(str(b["url"]).strip(), b.get("authorization") or None))
    except PermissionError as e:
        raise ApiError(str(e), 403)


@api
@requires("write")
@require_http_methods(["POST"])
def ingest_file(request):
    """multipart: file=<upload>, [ai=1]. Returns a REVIEWABLE candidate; nothing is persisted."""
    f = request.FILES.get("file")
    if not f:
        raise ApiError("Upload a file in the 'file' field.")
    if f.size > ingest.MAX_BYTES:
        raise ApiError("File too large.", 413)
    # records=N: opt-in copy of up to N rows per table as individuals; ai=1: ask the LLM for extra (verified-quote) concepts
    res = ingest.analyze(f.name, f.read(), records_per_table=request.POST.get("records") or 0, use_llm=bool(request.POST.get("ai")))
    return JsonResponse(res)


# ---------------------------------------------------------- ontology CRUD --

@api
@requires("read")
@require_http_methods(["GET", "POST"])
def ontology_list(request):
    if request.method == "POST":
        if not can(request.identity["role"], "write"):
            raise ApiError("Role cannot create ontologies.", 403)
        b = body(request)
        model = b.get("model") or model_ops.empty_model()
        model_ops.check_shape(model)
        merged_into = b.get("mergeInto")
        if merged_into:
            o = get_ontology(request, merged_into)
            o.model, rep = model_ops.merge_models(o.model, model)
            ensure_draft(o, request.identity["user"])
            o.save()
            versioning.audit(o, "ontology.merged", request.identity["user"], **rep)
            return JsonResponse({**detail(o), "mergeReport": rep})
        o = Ontology.objects.create(name=b.get("name") or "Untitled ontology", base_iri=b.get("baseIri") or generator.DEFAULT_BASE_IRI,
                                    context=b.get("context", ""), tenant=request.identity.get("tenant", ""),
                                    source_type=b.get("sourceType", "manual"), model=model)
        versioning.audit(o, "ontology.created", request.identity["user"], name=o.name)
        return JsonResponse(detail(o), status=201)
    qs = Ontology.objects.all()
    if request.identity.get("tenant"):
        qs = qs.filter(tenant=request.identity["tenant"])
    if request.GET.get("context"):
        # a host sees its own ontologies plus shared ones with no context tag
        qs = qs.filter(Q(context=request.GET["context"]) | Q(context=""))
    return JsonResponse({"results": [summary(o) for o in qs]})


@api
@requires("read")
@require_http_methods(["GET", "PUT", "DELETE"])
def ontology_detail(request, pk):
    o = get_ontology(request, pk)
    if request.method == "DELETE":
        if not can(request.identity["role"], "delete"):
            raise ApiError("Role cannot delete ontologies.", 403)
        versioning.audit(None, "ontology.deleted", request.identity["user"], id=o.id, name=o.name)
        o.delete()
        return HttpResponse(status=204)
    if request.method == "PUT":
        if not can(request.identity["role"], "write"):
            raise ApiError("Role cannot edit ontologies.", 403)
        b = body(request)
        if "model" in b:
            model_ops.check_shape(b["model"])
            # Optimistic concurrency: the client says which revision it edited. If someone else saved in between,
            # refuse (409) with who/when and what overwriting would change, unless the user chose to overwrite.
            base = b.get("baseRevision")
            if base is not None and int(base) != o.model_revision and not b.get("force"):
                last = o.audit.filter(action__in=["model.update", "ai.applied", "mapping.committed", "ontology.merged", "branch.merged",
                                                  "version.rollback"]).first()
                raise ApiError("This ontology was changed by someone else since you opened it.", 409, {
                    "code": "conflict", "currentRevision": o.model_revision, "yourRevision": int(base),
                    "lastEditor": last.actor if last else None, "lastEditedAt": last.created_at.isoformat() if last else None,
                    "lastAction": last.action if last else None,
                    "overwriteWouldChange": versioning.diff(o.model, b["model"])["lines"][:40]})
            before = o.model
            o.model = b["model"]
            ensure_draft(o, request.identity["user"])
            d = versioning.diff(before, o.model)
            if d["summary"]["changes"]:
                versioning.audit(o, "model.update", request.identity["user"], changes=d["lines"][:50], total=d["summary"]["changes"])
        for k, attr in (("name", "name"), ("baseIri", "base_iri")):
            if k in b:
                setattr(o, attr, b[k])
        o.save()
    return JsonResponse(detail(o))


@api
@requires("read")
@require_http_methods(["GET"])
def ontology_export(request, pk):
    o = get_ontology(request, pk)
    fmt = request.GET.get("format", "turtle")
    if fmt not in generator.FORMATS:
        raise ApiError(f"format must be one of {', '.join(generator.FORMATS)}.")
    model = o.model
    if request.GET.get("version"):
        v = o.versions.filter(number=request.GET["version"]).first()
        if not v:
            raise ApiError("Version not found.", 404)
        model = v.snapshot
    data, mime, ext = generator.serialize(model, fmt, o.name, o.base_iri)
    resp = HttpResponse(data, content_type=f"{mime}; charset=utf-8")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", o.name) or "ontology"
    resp["Content-Disposition"] = f'attachment; filename="{safe}.{ext}"'
    versioning.audit(o, "ontology.exported", request.identity["user"], format=fmt)
    return resp


def _model_for(o: Ontology, request):
    v = request.GET.get("version")
    if v:
        ver = o.versions.filter(number=v).first()
        if not ver:
            raise ApiError("Version not found.", 404)
        return ver.snapshot
    return o.model


@api
@requires("read")
@require_http_methods(["GET"])
def graph(request, pk):
    m = _model_for(get_ontology(request, pk), request)
    nodes = [{"id": c["name"], "label": c.get("label") or c["name"], "comment": c.get("comment", ""),
              "dataProperties": [p["name"] for p in m["dataProperties"] if p["domain"] == c["name"]]} for c in m["classes"]]
    edges = [{"id": f'{p["domain"]}.{p["name"]}', "source": p["domain"], "target": p["range"], "label": p["name"], "type": "object"}
             for p in m["objectProperties"]]
    edges += [{"id": f'{c["name"]}<{par}', "source": c["name"], "target": par, "label": "subClassOf", "type": "subclass"}
              for c in m["classes"] for par in c.get("parents", [])]
    return JsonResponse({"nodes": nodes, "edges": edges})


# ---------------------------------------------- validation & reasoning ------

@api
@requires("read")
@require_http_methods(["GET", "POST"])
def validate_view(request, pk):
    o = get_ontology(request, pk)
    m = _model_for(o, request)
    maps = [x for ms in o.mapping_sets.all() for x in ms.mappings]
    return JsonResponse(validation.validate(m, maps))


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def reason_view(request, pk):
    o = get_ontology(request, pk)
    profile = request.GET.get("profile", "owlrl")
    if profile not in ("owlrl", "rdfs"):
        raise ApiError("profile must be owlrl or rdfs.")
    return JsonResponse(reasoning.reason(_model_for(o, request), profile, o.base_iri))


@api
@requires("read")
@require_http_methods(["GET"])
def shapes_view(request, pk):
    o = get_ontology(request, pk)
    ttl = reasoning.build_shapes(_model_for(o, request), o.base_iri).serialize(format="turtle")
    return HttpResponse(ttl, content_type="text/turtle; charset=utf-8")


@api
@requires("read")
@require_http_methods(["POST"])
def shacl_view(request, pk):
    o = get_ontology(request, pk)
    return JsonResponse(reasoning.shacl_validate(_model_for(o, request), body(request).get("data"), o.base_iri))


# ------------------------------------------------- explorer & query --------

@api
@requires("read")
@require_http_methods(["GET"])
def search_view(request, pk):
    return JsonResponse({"results": query.search(_model_for(get_ontology(request, pk), request), request.GET.get("q", ""))})


@api
@requires("read")
@require_http_methods(["GET"])
def concept_view(request, pk, name):
    o = get_ontology(request, pk)
    maps = [x for ms in o.mapping_sets.all() for x in ms.mappings]
    try:
        d = query.concept(_model_for(o, request), name, maps)
    except KeyError:
        raise ApiError("Concept not found.", 404)
    d["neighbors"] = query.neighbors(_model_for(o, request), name)
    return JsonResponse(d)


@api
@requires("read")
@require_http_methods(["GET"])
def path_view(request, pk):
    m = _model_for(get_ontology(request, pk), request)
    path = query.find_path(m, request.GET.get("from", ""), request.GET.get("to", ""))
    return JsonResponse({"path": path, "connected": path is not None})


@api
@requires("read")
@require_http_methods(["POST"])
def query_view(request, pk):
    o = get_ontology(request, pk)
    b = body(request)
    text, kind = (b.get("text") or "").strip(), b.get("kind", "sparql")
    if not text:
        raise ApiError("Query text is required.")
    m = _model_for(o, request)
    if kind == "natural":
        res = query.natural_query(m, text, o.base_iri, o.name)
        count = res["result"]["count"]
    elif kind == "sparql":
        res = query.run_sparql(m, text, o.base_iri, o.name)
        count = res["count"]
    else:
        raise ApiError("kind must be 'sparql' or 'natural'.")
    QueryRecord.objects.create(ontology=o, text=text, kind=kind, result_count=count)
    return JsonResponse(res)


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def queries_view(request, pk):
    o = get_ontology(request, pk)
    if request.method == "POST":
        if not can(request.identity["role"], "write"):
            raise ApiError("Role cannot save queries.", 403)
        b = body(request)
        q = QueryRecord.objects.create(ontology=o, name=b.get("name", ""), text=b["text"], kind=b.get("kind", "sparql"), saved=True)
        return JsonResponse({"id": q.id}, status=201)
    qs = o.queries.all()
    if request.GET.get("saved"):
        qs = qs.filter(saved=True)
    return JsonResponse({"results": [{"id": q.id, "name": q.name, "text": q.text, "kind": q.kind, "saved": q.saved,
                                      "resultCount": q.result_count, "at": q.created_at.isoformat()} for q in qs[:100]]})


@api
@requires("write")
@require_http_methods(["DELETE"])
def query_delete(request, pk, qid):
    n, _ = get_ontology(request, pk).queries.filter(pk=qid).delete()
    if not n:
        raise ApiError("Query not found.", 404)
    return HttpResponse(status=204)


# -------------------------------------------------------------- mapping ----

@api
@requires("write")
@require_http_methods(["POST"])
def mapping_extract(request, pk):
    """Upload files and return mapping sources (fields) for the mapping studio."""
    get_ontology(request, pk)
    sources = []
    for f in request.FILES.getlist("file"):
        res = ingest.analyze(f.name, f.read())
        if res.get("schema"):
            sources += mapping_mod.sources_from_schema(res["schema"], None if len(res["schema"]["tables"]) == 1 else f.name)
            if len(res["schema"]["tables"]) == 1:
                sources[-1]["name"] = f.name
        elif res["kind"] == "document":
            sources.append({"name": f.name, "fields": [{"name": c["name"], "type": None} for c in res["model"]["classes"]]})
        else:
            sources.append({"name": f.name, "fields": [{"name": p["name"], "type": p.get("datatype")}
                                                       for p in res["model"]["dataProperties"]]})
    if not sources:
        raise ApiError("Upload at least one file in the 'file' field.")
    return JsonResponse({"sources": sources})


@api
@requires("read")
@require_http_methods(["POST"])
def mapping_propose(request, pk):
    o = get_ontology(request, pk)
    b = body(request)
    srcs = b.get("sources")
    if not isinstance(srcs, list) or not srcs:
        raise ApiError("sources must be a non-empty list of {name, fields:[{name,type}]}.")
    for s in srcs:
        if not s.get("name") or not isinstance(s.get("fields"), list):
            raise ApiError("Each source needs a name and fields list.")
    return JsonResponse(mapping_mod.propose_mappings(srcs, o.model, top_k=int(b.get("topK", 3))))


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def mapping_sets(request, pk):
    o = get_ontology(request, pk)
    if request.method == "POST":
        if not can(request.identity["role"], "write"):
            raise ApiError("Role cannot save mappings.", 403)
        b = body(request)
        ms = MappingSet.objects.create(ontology=o, name=b.get("name") or "Mapping set", mappings=b.get("mappings", []))
        versioning.audit(o, "mapping.saved", request.identity["user"], name=ms.name, count=len(ms.mappings))
        return JsonResponse(mapping_rows(ms), status=201)
    return JsonResponse({"results": [mapping_rows(m) for m in o.mapping_sets.all()]})


@api
@requires("write")
@require_http_methods(["PUT", "DELETE"])
def mapping_set_detail(request, pk, mid):
    o = get_ontology(request, pk)
    ms = o.mapping_sets.filter(pk=mid).first()
    if not ms:
        raise ApiError("Mapping set not found.", 404)
    if request.method == "DELETE":
        ms.delete()
        return HttpResponse(status=204)
    b = body(request)
    ms.name, ms.mappings = b.get("name", ms.name), b.get("mappings", ms.mappings)
    ms.save()
    return JsonResponse(mapping_rows(ms))


@api
@requires("write")
@require_http_methods(["POST"])
def mapping_commit(request, pk, mid):
    """Explicit user action: record accepted mappings as lineage in the ontology."""
    o = get_ontology(request, pk)
    ms = o.mapping_sets.filter(pk=mid).first()
    if not ms:
        raise ApiError("Mapping set not found.", 404)
    o.model, n = mapping_mod.commit_mappings(o.model, ms.mappings, ms.name)
    ensure_draft(o, request.identity["user"])
    o.save()
    versioning.audit(o, "mapping.committed", request.identity["user"], name=ms.name, lineage=n)
    return JsonResponse({"committed": n, "ontology": detail(o)})


# ----------------------------------------------------------- versioning ----

def ver_json(v: OntologyVersion):
    return {"id": v.id, "number": v.number, "status": v.status, "message": v.message, "createdBy": v.created_by,
            "reviewedBy": v.reviewed_by, "createdAt": v.created_at.isoformat(), "stats": generator.stats(v.snapshot)}


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def versions_view(request, pk):
    o = get_ontology(request, pk)
    if request.method == "POST":
        if not can(request.identity["role"], "commit"):
            raise ApiError("Role cannot commit versions.", 403)
        b = body(request)
        v = versioning.commit(o, b.get("message", ""), request.identity["user"], bool(b.get("major")))
        return JsonResponse(ver_json(v), status=201)
    return JsonResponse({"results": [ver_json(v) for v in o.versions.all()], "current": o.current_version, "status": o.status})


@api
@requires("read")
@require_http_methods(["GET"])
def version_detail(request, pk, number):
    o = get_ontology(request, pk)
    v = o.versions.filter(number=number).first()
    if not v:
        raise ApiError("Version not found.", 404)
    return JsonResponse({**ver_json(v), "model": v.snapshot})


@api
@requires("read")
@require_http_methods(["POST"])
def version_action(request, pk, number, action):
    o = get_ontology(request, pk)
    v = o.versions.filter(number=number).first()
    if not v:
        raise ApiError("Version not found.", 404)
    ident = request.identity
    if action == "rollback":
        if not can(ident["role"], "rollback"):
            raise ApiError("Role cannot roll back.", 403)
        nv = versioning.rollback(o, v, ident["user"])
        return JsonResponse({"version": ver_json(nv), "ontology": detail(o)})
    v = versioning.transition(o, v, action, ident["user"], lambda cap: can(ident["role"], cap))
    return JsonResponse({"version": ver_json(v), "ontology": summary(o)})


@api
@requires("read")
@require_http_methods(["GET"])
def diff_view(request, pk):
    o = get_ontology(request, pk)

    def snap(label):
        if label in (None, "", "working"):
            return o.model
        v = o.versions.filter(number=label).first()
        if not v:
            raise ApiError(f"Version {label} not found.", 404)
        return v.snapshot

    frm = request.GET.get("from") or (o.versions.first().number if o.versions.exists() else "working")
    to = request.GET.get("to") or "working"
    return JsonResponse({"from": frm, "to": to, **versioning.diff(snap(frm), snap(to))})


@api
@requires("read")
@require_http_methods(["GET"])
def audit_view(request, pk):
    o = get_ontology(request, pk)
    return JsonResponse({"results": [{"id": e.id, "action": e.action, "actor": e.actor, "detail": e.detail,
                                      "at": e.created_at.isoformat()} for e in o.audit.all()[:int(request.GET.get("limit", 200))]]})


# ----------------------------------------------------------- AI assistant --

@api
@requires("read")
@require_http_methods(["POST"])
def ai_propose(request, pk):
    """Proposal only — never mutates. Returns ops + diff + validation preview for user approval."""
    o = get_ontology(request, pk)
    b = body(request)
    if not (b.get("prompt") or "").strip():
        raise ApiError("prompt is required.")
    prop = assistant.propose(o.model, b["prompt"], use_llm=b.get("useLlm", True))
    after = assistant.apply_operations(o.model, prop["ops"])
    return JsonResponse({**prop, "diff": versioning.diff(o.model, after), "validationAfter": {
        k: v for k, v in validation.validate(after).items() if k != "issues"}, "requiresApproval": True})


@api
@requires("write")
@require_http_methods(["POST"])
def ai_apply(request, pk):
    """The user approved the proposal: apply the (re-validated) operations."""
    o = get_ontology(request, pk)
    ops = body(request).get("ops")
    if not isinstance(ops, list) or not ops:
        raise ApiError("ops must be a non-empty list.")
    o.model = assistant.apply_operations(o.model, assistant.normalize_ops(o.model, ops))
    ensure_draft(o, request.identity["user"])
    o.save()
    versioning.audit(o, "ai.applied", request.identity["user"], ops=ops[:50])
    return JsonResponse(detail(o))


# ------------------------------------------------------- agentic (R10) -----

@api
@requires("read")
@require_http_methods(["GET", "PUT"])
def agentic_registry(request, pk):
    o = get_ontology(request, pk)
    if request.method == "PUT":
        if not can(request.identity["role"], "write"):
            raise ApiError("Role cannot edit the agent registry.", 403)
        reg = body(request)
        reg = {k: reg.get(k, []) for k in ("tools", "policies", "rules")}
        errs = agent_mod.validate_registry(o.model, reg)
        if errs:
            raise ApiError("; ".join(errs))
        o.model = {**o.model, "agentic": reg}
        ensure_draft(o, request.identity["user"])
        o.save()
        versioning.audit(o, "agentic.registry.update", request.identity["user"], tools=len(reg["tools"]), policies=len(reg["policies"]))
    return JsonResponse(agent_mod.registry(o.model))


@api
@requires("read")
@require_http_methods(["POST"])
def agent_plan(request, pk):
    o = get_ontology(request, pk)
    b = body(request)
    if not (b.get("request") or "").strip():
        raise ApiError("request is required.")
    return JsonResponse(agent_mod.plan(_model_for(o, request), b["request"], b.get("role", "agent")))


@api
@requires("read")
@require_http_methods(["GET"])
def agent_context(request, pk):
    return JsonResponse(agent_mod.context_pack(_model_for(get_ontology(request, pk), request), request.GET.get("q")))


# ------------------------------------------------------- embedding (R9) ----

@api
@require_http_methods(["GET"])
def embedded_manifest(request):
    return JsonResponse(embedded.manifest())


@api
@requires("read")
@require_http_methods(["GET"])
def embedded_context(request):
    host = request.GET.get("host", "")
    o = get_ontology(request, request.GET["ontology_id"]) if request.GET.get("ontology_id") else None
    try:
        ctx = embedded.host_context(host, o)
    except KeyError:
        raise ApiError(f"Unknown host '{host}'. Known: {', '.join(sorted(embedded.HOSTS))}", 404)
    ctx["identity"] = request.identity
    return JsonResponse(ctx)


@api
@requires("read")
@require_http_methods(["POST"])
def embedded_event(request):
    b = body(request)
    if b.get("type") not in embedded.EVENTS:
        raise ApiError(f"Unknown event type. Known: {', '.join(sorted(embedded.EVENTS))}")
    o = get_ontology(request, b["ontologyId"]) if b.get("ontologyId") else None
    versioning.audit(o, f'host.{b["type"]}', request.identity["user"], host=b.get("host"), payload=b.get("payload", {}))
    return JsonResponse({"accepted": True})
