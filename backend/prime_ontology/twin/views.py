"""Semantic Digital Twin API (R13)."""
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .. import versioning
from ..fabric.models import FabricEntity
from ..identity import can, requires
from ..views import ApiError, api, body, get_ontology
from . import core
from .models import TwinAsset, TwinEvent, TwinProcess, TwinRule


def _need(request, cap, what):
    if not can(request.identity["role"], cap):
        raise ApiError(f"Role cannot {what}.", 403)


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except core.TwinError as e:
        raise ApiError(str(e))


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def twin(request, pk):
    o = get_ontology(request, pk)
    if request.method == "POST":  # create an entity directly in the twin
        _need(request, "write", "create twin entities")
        b = body(request)
        e = _wrap(core.create_twin_entity, o, b.get("class"), str(b.get("name", "")).strip(), b.get("attributes") or {}, request.identity["user"])
        versioning.audit(o, "twin.entity.created", request.identity["user"], entity=e.id, cls=e.class_name)
        return JsonResponse({"id": e.id, "class": e.class_name, "name": e.display_name}, status=201)
    as_of = _wrap(core.parse_time, request.GET.get("asOf"))
    try:
        limit = max(1, min(int(request.GET.get("limit", 200)), 1000))
    except ValueError:
        raise ApiError("limit must be an integer.")
    return JsonResponse(core.snapshot(o, as_of, limit))


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def events(request, pk):
    o = get_ontology(request, pk)
    if request.method == "POST":
        _need(request, "write", "post twin events")
        b = body(request)
        batch = b["events"] if isinstance(b.get("events"), list) else [b]
        if not batch or len(batch) > 500:
            raise ApiError("Send 1-500 events.")
        out = []
        for ev in batch:
            if not isinstance(ev, dict):
                raise ApiError("Each event must be an object.")
            out.append(_wrap(core.ingest_event, o, ev, request.identity["user"]).id)
        return JsonResponse({"accepted": len(out), "ids": out}, status=201)
    qs = TwinEvent.objects.filter(ontology=o)
    g = request.GET
    if g.get("entity"):
        qs = qs.filter(entity_id=g["entity"])
    if g.get("type"):
        qs = qs.filter(event_type=g["type"])
    if g.get("since"):
        qs = qs.filter(occurred_at__gte=_wrap(core.parse_time, g["since"]))
    if g.get("until"):
        qs = qs.filter(occurred_at__lte=_wrap(core.parse_time, g["until"]))
    return JsonResponse({"events": [{"id": e.id, "type": e.event_type, "entityId": e.entity_id, "occurredAt": e.occurred_at.isoformat(), "source": e.source,
                                     "actor": e.actor, "payload": e.payload} for e in qs[:300]]})


@api
@requires("read")
@require_http_methods(["GET"])
def entity_history(request, pk, eid):
    o = get_ontology(request, pk)
    e = FabricEntity.objects.filter(pk=eid, ontology=o).first()
    if not e:
        raise ApiError("Entity not found.", 404)
    core.ensure_history_for(o, e)
    as_of = _wrap(core.parse_time, request.GET.get("asOf"))
    out = {"entityId": e.id, "class": e.class_name, "name": e.display_name, "history": core.history_of(o, e, request.GET.get("property")),
           "events": [{"id": x.id, "type": x.event_type, "occurredAt": x.occurred_at.isoformat(), "payload": x.payload} for x in e.twin_events.all()[:100]],
           "processes": [{"process": i.process.name, "state": i.state, "history": i.history} for i in e.process_instances.select_related("process")]}
    if as_of:
        out["asOf"] = as_of.isoformat()
        out["attributesAsOf"] = core.values_at(o, [e.id], as_of).get(e.id, {})
    return JsonResponse(out)


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def processes(request, pk):
    o = get_ontology(request, pk)
    if request.method == "POST":
        _need(request, "write", "define processes")
        b = body(request)
        if b.get("start"):  # start (or fetch) an instance of a process for an entity
            s = b["start"]
            e = _wrap(core._entity, o, s.get("entityId"))
            inst = _wrap(core.start_process, o, e, s.get("process"))
            return JsonResponse({"instanceId": inst.id, "process": inst.process.name, "state": inst.state}, status=201)
        spec = _wrap(core.validate_process, o.model, b)
        if TwinProcess.objects.filter(ontology=o, name=spec["name"]).exists():
            raise ApiError(f"A process named '{spec['name']}' already exists.")
        p = TwinProcess.objects.create(ontology=o, **spec)
        versioning.audit(o, "twin.process.created", request.identity["user"], process=p.name)
        return JsonResponse({"id": p.id, "name": p.name}, status=201)
    return JsonResponse({"processes": core.snapshot(o, None, 1)["processes"]})


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def assets(request, pk):
    o = get_ontology(request, pk)
    if request.method == "POST":
        _need(request, "write", "register assets")
        b = body(request)
        name = str(b.get("name", "")).strip()
        if not name or len(name) > 200:
            raise ApiError("Asset name is required.")
        parent = TwinAsset.objects.filter(pk=b["parentId"], ontology=o).first() if b.get("parentId") else None
        if b.get("parentId") and not parent:
            raise ApiError("parentId not found.", 404)
        ent = _wrap(core._entity, o, b["entityId"]) if b.get("entityId") is not None else None
        if not isinstance(b.get("attributes") or {}, dict):
            raise ApiError("attributes must be an object.")
        a = TwinAsset.objects.create(ontology=o, name=name, asset_type=str(b.get("type", "asset"))[:60], parent=parent, entity=ent, attributes=b.get("attributes") or {},
                                     status=str(b.get("status", "active"))[:30])
        return JsonResponse({"id": a.id, "name": a.name}, status=201)
    return JsonResponse({"assets": core.snapshot(o, None, 1)["assets"]})


@api
@requires("read")
@require_http_methods(["GET", "POST"])
def rules(request, pk):
    o = get_ontology(request, pk)
    if request.method == "POST":
        _need(request, "write", "define twin rules")
        b = body(request)
        sibs = list(TwinRule.objects.filter(ontology=o, kind="derive", class_name=b.get("class")).values("target"))
        spec = _wrap(core.validate_rule, o.model, {**b, "_siblings": sibs})
        if TwinRule.objects.filter(ontology=o, name=spec["name"]).exists():
            raise ApiError(f"A rule named '{spec['name']}' already exists.")
        r = TwinRule.objects.create(ontology=o, **spec)
        versioning.audit(o, "twin.rule.created", request.identity["user"], rule=r.name)
        return JsonResponse(core.rule_json(r), status=201)
    return JsonResponse({"rules": [core.rule_json(r) for r in TwinRule.objects.filter(ontology=o)]})


@api
@requires("read")
@require_http_methods(["DELETE"])
def rule_detail(request, pk, rid):
    o = get_ontology(request, pk)
    _need(request, "write", "delete twin rules")
    n, _ = TwinRule.objects.filter(pk=rid, ontology=o).delete()
    if not n:
        raise ApiError("Rule not found.", 404)
    return JsonResponse({"deleted": True})


@api
@requires("read")
@require_http_methods(["POST"])
def simulate(request, pk):
    o = get_ontology(request, pk)
    b = body(request)
    out = _wrap(core.simulate, o, b.get("changes") or [], int(b.get("hops", 2)), b.get("process"))
    versioning.audit(o, "twin.simulated", request.identity["user"], changes=len(out["applied"]), impacted=out["summary"]["impactedEntities"])
    return JsonResponse(out)


@api
@requires("read")
@require_http_methods(["GET"])
def graphs(request, pk):
    from . import graphs as tg

    o = get_ontology(request, pk)
    view = request.GET.get("view")
    if not view:
        return JsonResponse({"views": tg.available(o)})
    try:
        return JsonResponse(tg.graph_view(o, view, int(request.GET.get("limit", 150))))
    except ValueError as e:
        raise ApiError(str(e)) from None
