"""Enterprise Knowledge Fabric API (R11).

Roles: viewer reads; editor syncs; reviewer decides entity-match suggestions; admin manages sources & credentials.
Credentials are write-only: they are encrypted at rest and never appear in any response.
"""
from django.db.models import Count
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .. import assistant, versioning
from ..identity import can, requires
from ..models import Ontology
from ..views import ApiError, api, body, detail, ensure_draft, get_ontology
from . import adapters, crypto, freshness, graph, resolution
from .http import ConnectorError
from .mapping_spec import MappingError, validate_mapping
from .models import property_provenance
from .models import FabricConstraint, FabricDrift, FabricEntity, FabricEvent, FabricRecord, FabricSource, FabricState, FabricSuggestion, FabricSyncRun
from .sync import SyncBusy, sync_source

NAME_MAX = 120


def _need(request, cap, what):
    if not can(request.identity["role"], cap):
        raise ApiError(f"Role cannot {what}.", 403)


def source_json(s: FabricSource) -> dict:
    return {"id": s.id, "name": s.name, "kind": s.kind, "config": s.config, "hasCredentials": bool(s.secret_enc), "mapping": s.mapping, "enabled": s.enabled,
            "syncIntervalMinutes": s.sync_interval_minutes, "freshnessSlaMinutes": s.freshness_sla_minutes, "priority": s.priority, "fullEvery": s.full_every,
            "syncCount": s.sync_count, "running": bool(s.running_since), "nextSyncAt": s.next_sync_at.isoformat() if s.next_sync_at else None,
            "freshness": freshness.source_freshness(s), "records": s.records.filter(deleted=False).count()}


def run_json(r: FabricSyncRun) -> dict:
    return {"id": r.id, "source": r.source_id, "sourceName": r.source.name, "mode": r.mode, "trigger": r.trigger, "status": r.status, "stats": r.stats, "error": r.error,
            "actor": r.actor, "startedAt": r.started_at.isoformat(), "finishedAt": r.finished_at.isoformat() if r.finished_at else None}


def entity_json(e: FabricEntity, full=False) -> dict:
    d = {"id": e.id, "class": e.class_name, "name": e.display_name, "recordCount": e.record_count, "status": e.status, "canonical": e.canonical}
    if full:
        d["provenance"] = e.provenance
        d["mergedInto"] = e.merged_into_id
        d["records"] = [{"id": r.id, "source": r.source.name, "externalId": r.external_id, "data": r.data, "linkReason": r.link_reason, "deleted": r.deleted,
                         "lastChangedAt": r.last_changed_at.isoformat() if r.last_changed_at else None,
                         "sourceUpdatedAt": r.source_updated_at.isoformat() if r.source_updated_at else None}
                        for r in e.records.select_related("source")]
        d["conflicts"] = _conflicts(d["records"], e)
    return d


def _conflicts(records, entity):
    out = []
    props = {k for r in records for k in r["data"]}
    for p in sorted(props):
        vals = {(r["source"], str(r["data"][p])) for r in records if r["data"].get(p) not in (None, "") and not r["deleted"]}
        if len({v for _, v in vals}) > 1:
            out.append({"property": p, "chosen": entity.canonical.get(p), "from": (property_provenance(entity).get(p) or {}).get("source"),
                        "values": [{"source": s, "value": v} for s, v in sorted(vals)]})
    return out


def get_source(request, ontology, sid) -> FabricSource:
    try:
        return FabricSource.objects.get(pk=sid, ontology=ontology)
    except FabricSource.DoesNotExist:
        raise ApiError("Source not found.", 404)


def _snapshot(o: Ontology) -> dict:
    sources = list(o.fabric_sources.all())
    fresh = [freshness.source_freshness(s)["status"] for s in sources if s.enabled]
    ents = FabricEntity.objects.filter(ontology=o, status="active")
    by_class = {r["class_name"]: r["n"] for r in ents.values("class_name").annotate(n=Count("id"))}
    multi = ents.filter(record_count__gt=1).count()
    return {"ontology": o.id, "version": (getattr(o, "fabric_state", None) and o.fabric_state.version) or 0, "freshness": freshness.worst(fresh),
            "sources": [source_json(s) for s in sources], "entities": {"total": ents.count(), "byClass": by_class, "linkedAcrossSources": multi},
            "records": FabricRecord.objects.filter(ontology=o, deleted=False).count(),
            "pendingSuggestions": o.fabric_suggestions.filter(status="pending").count(), "pendingDrift": o.fabric_drift.filter(status="pending").count(),
            "recentRuns": [run_json(r) for r in FabricSyncRun.objects.filter(source__ontology=o).select_related("source")[:10]],
            "kinds": adapters.describe_kinds()}


@api
@requires("read")
@require_http_methods(["GET"])
def snapshot(request, pk):
    return JsonResponse(_snapshot(get_ontology(request, pk)))


def _apply_source_fields(request, o, s: FabricSource, b: dict, creating: bool):
    if creating or "name" in b:
        name = str(b.get("name", "")).strip()
        if not name or len(name) > NAME_MAX:
            raise ApiError("Source name is required (max 120 characters).")
        if o.fabric_sources.filter(name__iexact=name).exclude(pk=s.pk).exists():
            raise ApiError(f"A source named '{name}' already exists.")
        s.name = name
    if creating:
        kind = b.get("kind")
        if kind not in adapters.ADAPTERS:
            raise ApiError(f"kind must be one of: {', '.join(adapters.ADAPTERS)}.")
        s.kind = kind
    if "config" in b:
        if not isinstance(b["config"], dict):
            raise ApiError("config must be an object.")
        s.config = b["config"]
    if "credentials" in b:  # write-only; an empty object clears them
        c = b["credentials"]
        if not isinstance(c, dict):
            raise ApiError("credentials must be an object.")
        try:
            s.secret_enc = crypto.encrypt_json(c) if c else ""
        except crypto.SecretError as e:
            raise ApiError(str(e), 500)
    if "mapping" in b:
        try:
            s.mapping, warnings = validate_mapping(b["mapping"], s.kind, o.model)
        except MappingError as e:
            raise ApiError(str(e))
    else:
        warnings = []
    for key, attr, lo, hi in (("syncIntervalMinutes", "sync_interval_minutes", 0, 525600), ("freshnessSlaMinutes", "freshness_sla_minutes", 1, 525600),
                              ("priority", "priority", 0, 1000), ("fullEvery", "full_every", 0, 100000)):
        if key in b:
            try:
                v = int(b[key])
            except (TypeError, ValueError):
                raise ApiError(f"{key} must be an integer.")
            if not lo <= v <= hi:
                raise ApiError(f"{key} must be between {lo} and {hi}.")
            setattr(s, attr, v)
    if "enabled" in b:
        s.enabled = bool(b["enabled"])
    if s.sync_interval_minutes and not s.next_sync_at:
        s.next_sync_at = timezone.now()
    return warnings


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def sources(request, pk):
    o = get_ontology(request, pk)
    if request.method == "POST":
        _need(request, "fabric_manage", "register sources")
        s = FabricSource(ontology=o, created_by=request.identity["user"])
        warnings = _apply_source_fields(request, o, s, body(request), True)
        s.save()
        versioning.audit(o, "fabric.source.created", request.identity["user"], source=s.name, kind=s.kind)
        return JsonResponse({**source_json(s), "warnings": warnings}, status=201)
    return JsonResponse({"sources": [source_json(s) for s in o.fabric_sources.all()], "kinds": adapters.describe_kinds()})


@api
@requires("read")
@require_http_methods(["GET", "PUT", "DELETE"])
def source_detail(request, pk, sid):
    o = get_ontology(request, pk)
    s = get_source(request, o, sid)
    if request.method == "DELETE":
        _need(request, "fabric_manage", "delete sources")
        versioning.audit(o, "fabric.source.deleted", request.identity["user"], source=s.name)
        s.delete()
        resolution.bump(o)
        return JsonResponse({"deleted": True})
    if request.method == "PUT":
        _need(request, "fabric_manage", "edit sources")
        warnings = _apply_source_fields(request, o, s, body(request), False)
        s.save()
        versioning.audit(o, "fabric.source.updated", request.identity["user"], source=s.name)
        return JsonResponse({**source_json(s), "warnings": warnings})
    return JsonResponse(source_json(s))


def _adapter(s: FabricSource):
    try:
        return adapters.build_adapter(s)
    except crypto.SecretError as e:
        raise ApiError(str(e))
    except ConnectorError as e:
        raise ApiError(str(e))


@api
@requires("read")
@require_http_methods(["POST"])
def source_test(request, pk, sid):
    o = get_ontology(request, pk)
    s = get_source(request, o, sid)
    _need(request, "fabric_manage", "test connections")
    try:
        return JsonResponse(_adapter(s).test())
    except ConnectorError as e:
        return JsonResponse({"ok": False, "error": str(e)})


@api
@requires("read")
@require_http_methods(["GET"])
def source_discover(request, pk, sid):
    o = get_ontology(request, pk)
    s = get_source(request, o, sid)
    _need(request, "fabric_manage", "browse source schemas")
    try:
        return JsonResponse({"objects": _adapter(s).discover()})
    except ConnectorError as e:
        raise ApiError(str(e), 502)


@api
@requires("read")
@require_http_methods(["POST"])
def source_sync(request, pk, sid):
    o = get_ontology(request, pk)
    s = get_source(request, o, sid)
    _need(request, "fabric_sync", "synchronise sources")
    try:
        run = sync_source(s, full=bool(body(request).get("full")), trigger="api", actor=request.identity["user"])
    except SyncBusy as e:
        raise ApiError(str(e), 409)
    except ConnectorError as e:
        raise ApiError(str(e))
    return JsonResponse(run_json(run), status=200)


@api
@requires("read")
@require_http_methods(["POST"])
def sync_all(request, pk):
    o = get_ontology(request, pk)
    _need(request, "fabric_sync", "synchronise sources")
    b, out = body(request), []
    for s in o.fabric_sources.filter(enabled=True).order_by("-priority", "id"):
        try:
            out.append(run_json(sync_source(s, full=bool(b.get("full")), trigger="api", actor=request.identity["user"])))
        except (SyncBusy, ConnectorError) as e:
            out.append({"source": s.id, "sourceName": s.name, "status": "skipped", "error": str(e)})
    return JsonResponse({"runs": out, **{k: v for k, v in _snapshot(o).items() if k in ("freshness", "entities")}})


@api
@requires("read")
@require_http_methods(["GET"])
def runs(request, pk):
    o = get_ontology(request, pk)
    qs = FabricSyncRun.objects.filter(source__ontology=o).select_related("source")
    if request.GET.get("source"):
        qs = qs.filter(source_id=request.GET["source"])
    return JsonResponse({"runs": [run_json(r) for r in qs[:100]]})


@api
@requires("read")
@require_http_methods(["GET"])
def entities(request, pk):
    o = get_ontology(request, pk)
    g = request.GET
    qs = FabricEntity.objects.filter(ontology=o, status="active")
    if g.get("class"):
        qs = qs.filter(class_name=g["class"])
    if g.get("q"):
        qs = qs.filter(display_name__icontains=g["q"][:100])
    if g.get("linked") == "1":
        qs = qs.filter(record_count__gt=1)
    if g.get("source"):
        qs = qs.filter(records__source_id=g["source"], records__deleted=False).distinct()
    try:
        limit, offset = min(int(g.get("limit", 50)), 200), max(int(g.get("offset", 0)), 0)
    except ValueError:
        raise ApiError("limit/offset must be integers.")
    total = qs.count()
    return JsonResponse({"total": total, "entities": [entity_json(e) for e in qs.order_by("class_name", "display_name", "id")[offset:offset + limit]]})


@api
@requires("read")
@require_http_methods(["GET"])
def entity_detail(request, pk, eid):
    o = get_ontology(request, pk)
    e = FabricEntity.objects.filter(pk=eid, ontology=o).first()
    if not e:
        raise ApiError("Entity not found.", 404)
    if e.status == "merged" and e.merged_into_id:  # stable ids: a merged id keeps working and points to the survivor
        e = FabricEntity.objects.get(pk=e.merged_into_id)
    return JsonResponse({**entity_json(e, True), "neighbours": graph.neighbours(o, e), "provenanceGraph": graph.provenance_graph(e)})


@api
@requires("read")
@require_http_methods(["GET"])
def entity_lineage(request, pk, eid):
    o = get_ontology(request, pk)
    e = FabricEntity.objects.filter(pk=eid, ontology=o).first()
    if not e:
        raise ApiError("Entity not found.", 404)
    ev = FabricEvent.objects.filter(ontology=o, entity=e).select_related("source")[:200]
    return JsonResponse({"entity": e.id, "events": [{"id": x.id, "kind": x.kind, "source": x.source.name if x.source else None, "at": x.at.isoformat(), "payload": x.payload} for x in ev]})


@api
@requires("read")
@require_http_methods(["GET"])
def events(request, pk):
    o = get_ontology(request, pk)
    qs = FabricEvent.objects.filter(ontology=o).select_related("source")
    if request.GET.get("kind"):
        qs = qs.filter(kind=request.GET["kind"])
    return JsonResponse({"events": [{"id": x.id, "kind": x.kind, "entity": x.entity_id, "source": x.source.name if x.source else None, "at": x.at.isoformat(),
                                     "payload": x.payload} for x in qs[:200]]})


@api
@requires("read")
@require_http_methods(["GET"])
def suggestions(request, pk):
    o = get_ontology(request, pk)
    qs = o.fabric_suggestions.filter(status=request.GET.get("status", "pending")).select_related("entity_a", "entity_b")[:100]
    return JsonResponse({"suggestions": [{"id": s.id, "class": s.class_name, "score": s.score, "reasons": s.reasons, "a": entity_json(s.entity_a), "b": entity_json(s.entity_b)} for s in qs]})


@api
@requires("read")
@require_http_methods(["POST"])
def suggestion_decide(request, pk, sid):
    o = get_ontology(request, pk)
    _need(request, "fabric_decide", "decide entity matches")
    s = o.fabric_suggestions.filter(pk=sid).first()
    if not s:
        raise ApiError("Suggestion not found.", 404)
    decision = body(request).get("decision")
    if decision not in ("accept", "reject"):
        raise ApiError("decision must be 'accept' or 'reject'.")
    out = resolution.decide_suggestion(s, decision, request.identity["user"])
    versioning.audit(o, f"fabric.match.{decision}ed", request.identity["user"], suggestion=s.id)
    return JsonResponse({"status": s.status, "resolution": out})


@api
@requires("read")
@require_http_methods(["GET"])
def drift(request, pk):
    o = get_ontology(request, pk)
    rows = o.fabric_drift.filter(status=request.GET.get("status", "pending")).select_related("source")
    return JsonResponse({"drift": [{"id": d.id, "source": d.source.name, "class": d.class_name, "field": d.field, "type": d.inferred_type, "sample": d.sample, "status": d.status} for d in rows[:200]]})


_XSD = {"string": "string", "integer": "integer", "decimal": "decimal", "boolean": "boolean", "date": "date", "dateTime": "dateTime"}


@api
@requires("read")
@require_http_methods(["POST"])
def drift_decide(request, pk, did):
    """Apply = turn the new source field into an ontology property (as an unpublished DRAFT change). Dismiss = ignore it."""
    o = get_ontology(request, pk)
    d = o.fabric_drift.filter(pk=did).first()
    if not d:
        raise ApiError("Drift item not found.", 404)
    if d.status != "pending":
        raise ApiError("Already decided.")
    action = body(request).get("action")
    if action == "dismiss":
        d.status = "dismissed"
        d.save()
        return JsonResponse({"status": d.status})
    if action != "apply":
        raise ApiError("action must be 'apply' or 'dismiss'.")
    _need(request, "write", "change the ontology")
    import re

    name = re.sub(r"[^A-Za-z0-9]+(.)", lambda m: m.group(1).upper(), d.field.strip())
    name = (name[:1].lower() + name[1:]) or "field"
    op = {"op": "add_property", "domain": d.class_name, "name": name, "datatype": _XSD.get(d.inferred_type, "string")}
    good = assistant.normalize_ops(o.model, [op])
    if not good:
        raise ApiError(f"Cannot add property '{name}' to {d.class_name} (it may already exist).")
    o.model = assistant.apply_operations(o.model, good)
    ensure_draft(o, request.identity["user"])
    o.save()
    d.status = "applied"
    d.save()
    versioning.audit(o, "fabric.drift.applied", request.identity["user"], field=d.field, cls=d.class_name, property=name)
    return JsonResponse({"status": d.status, "property": name, "ontology": detail(o), "note": "Map the field in the source mapping, then sync again."})


@api
@requires("read")
@require_http_methods(["POST"])
def fabric_sparql(request, pk):
    o = get_ontology(request, pk)
    text = str(body(request).get("query", "")).strip()
    if not text:
        raise ApiError("query is required.")
    return JsonResponse(graph.sparql(o, text))


@api
@requires("read")
@require_http_methods(["GET"])
def fabric_export(request, pk):
    from django.http import HttpResponse

    o = get_ontology(request, pk)
    fmt = request.GET.get("format", "turtle")
    if fmt == "cypher":
        return HttpResponse(graph.to_cypher(o), content_type="text/plain; charset=utf-8")
    if fmt != "turtle":
        raise ApiError("format must be turtle or cypher.")
    return HttpResponse(graph.get_graph(o).serialize(format="turtle"), content_type="text/turtle; charset=utf-8")


@api
@requires("read")
@require_http_methods(["POST"])
def suggest_mapping(request, pk, sid):
    """Propose a mapping from the source's real columns/fields to the ontology (deterministic name+type matching)."""
    o = get_ontology(request, pk)
    s = get_source(request, o, sid)
    _need(request, "fabric_manage", "design mappings")
    b = body(request)
    obj = str(b.get("object", ""))
    cls = b.get("class")
    classes = {c["name"] for c in o.model["classes"]}
    if cls not in classes:
        raise ApiError("class must be a class of this ontology.")
    ad = _adapter(s)
    try:
        sample = next(iter(ad.fetch({**(b.get("spec") or {}), s.kind == "mysql" and "table" or "path": obj}, None)), {}) if obj else {}
    except ConnectorError as e:
        raise ApiError(str(e), 502)
    props = [p for p in o.model.get("dataProperties", []) if p.get("domain") == cls]
    norm = lambda x: "".join(ch for ch in str(x).lower() if ch.isalnum())  # noqa: E731
    fields, used = {}, set()
    for col in sample:
        for p in props:
            if p["name"] not in used and (norm(col) == norm(p["name"]) or norm(p["name"]) in norm(col) and len(norm(p["name"])) > 3):
                fields[col] = p["name"]
                used.add(p["name"])
                break
    return JsonResponse({"class": cls, "fields": fields, "unmatchedColumns": [c for c in sample if c not in fields], "unmappedProperties": [p["name"] for p in props if p["name"] not in used]})


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def global_sources(request):
    """GET /api/v1/fabric/sources/ — every source the caller can see; POST registers one ({"ontologyId": n, ...})."""
    if request.method == "POST":
        b = body(request)
        return sources(request, get_ontology(request, b.get("ontologyId", -1)).id)
    ident = request.identity
    qs = FabricSource.objects.select_related("ontology")
    if ident.get("tenant"):
        qs = qs.filter(ontology__tenant=ident["tenant"])
    return JsonResponse({"sources": [{**source_json(s), "ontologyId": s.ontology_id, "ontologyName": s.ontology.name} for s in qs[:500]], "kinds": adapters.describe_kinds()})


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def global_sources(request):
    """GET /api/v1/fabric/sources/ - every source the caller can see; POST registers one ({"ontologyId": n, ...})."""
    if request.method == "POST":
        b = body(request)
        return sources(request, get_ontology(request, b.get("ontologyId", -1)).id)
    ident = request.identity
    qs = FabricSource.objects.select_related("ontology")
    if ident.get("tenant"):
        qs = qs.filter(ontology__tenant=ident["tenant"])
    return JsonResponse({"sources": [{**source_json(s), "ontologyId": s.ontology_id, "ontologyName": s.ontology.name} for s in qs[:500]],
                         "kinds": adapters.describe_kinds()})


@api
@requires("read")
@require_http_methods(["POST"])
def source_upload(request, pk, sid):
    """File sources: store a CSV/JSON document (text) with the source. Re-uploading a newer export is how a file source 'syncs'."""
    o = get_ontology(request, pk)
    s = get_source(request, o, sid)
    _need(request, "fabric_sync", "upload data")
    if s.kind != "file":
        raise ApiError("Only 'file' sources accept uploads.")
    b = body(request)
    text, fmt = b.get("text"), b.get("format", "csv")
    if fmt not in ("csv", "json") or not isinstance(text, str) or not text.strip():
        raise ApiError("Provide format ('csv' or 'json') and non-empty text.")
    if len(text) > 20 * 1024 * 1024:
        raise ApiError("File larger than 20 MB.", 413)
    s.config = {**s.config, "inline": {"format": fmt, "text": text}}
    s.save(update_fields=["config"])
    versioning.audit(o, "fabric.source.uploaded", request.identity["user"], source=s.name, bytes=len(text))
    run = None
    if b.get("sync", True):
        try:
            run = run_json(sync_source(s, trigger="api", actor=request.identity["user"]))
        except (SyncBusy, ConnectorError) as e:
            raise ApiError(str(e), 409)
    return JsonResponse({"uploaded": True, "run": run})


@api
@requires("read")
@require_http_methods(["POST"])
def source_push(request, pk, sid):
    """Webhook-style ingestion for 'push' sources: {"class": "Customer", "records": [{...}], "sync": true}.
    A record with "_deleted": true retires the matching record instead of upserting it."""
    from .mapping_spec import SkipRecord, transform
    from .models import FabricInbox

    o = get_ontology(request, pk)
    s = get_source(request, o, sid)
    _need(request, "fabric_sync", "push data")
    if s.kind != "push":
        raise ApiError("Only 'push' sources accept pushed records.")
    b = body(request)
    spec = next((e for e in (s.mapping or {}).get("entities", []) if e["class"] == b.get("class")), None)
    recs = b.get("records")
    if not spec:
        raise ApiError("class must be one of the classes in this source's mapping.")
    if not isinstance(recs, list) or not recs or len(recs) > 1000 or any(not isinstance(r, dict) for r in recs):
        raise ApiError("records must be a list of 1-1000 objects.")
    deleted, queued = 0, []
    for r in recs:
        if r.get("_deleted"):
            try:
                ext = transform(spec, {k: v for k, v in r.items() if k != "_deleted"}).external_id
            except SkipRecord:
                continue
            deleted += FabricRecord.objects.filter(source=s, class_name=spec["class"], external_id=ext, deleted=False).update(deleted=True, deleted_at=timezone.now())
        else:
            queued.append(FabricInbox(source=s, class_name=spec["class"], payload=r))
    FabricInbox.objects.bulk_create(queued)
    out = {"queued": len(queued), "deleted": deleted, "run": None}
    if deleted:
        from .resolution import bump

        bump(o)
    if b.get("sync", True) and queued:
        try:
            out["run"] = run_json(sync_source(s, trigger="api", actor=request.identity["user"]))
        except SyncBusy:
            out["note"] = "A sync is already running; the records will be picked up by the next one."
        except ConnectorError as e:
            raise ApiError(str(e)) from None
    return JsonResponse(out, status=202)
