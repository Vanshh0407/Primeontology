"""Semantic Digital Twin engine (R13): temporal state, event stream, processes, assets, rules, non-destructive simulation."""
import copy
from collections import Counter, defaultdict
from datetime import datetime, timezone

from django.db import transaction

from ..fabric.models import FabricEntity, FabricEvent
from ..rag.retrieve import get_entity_graph
from . import expr
from .models import TwinAsset, TwinAttributeHistory, TwinEvent, TwinProcess, TwinProcessInstance, TwinRule


class TwinError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc)


def parse_time(v, default=None):
    if v in (None, ""):
        return default
    if isinstance(v, datetime):
        t = v
    else:
        try:
            t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            raise TwinError(f"'{v}' is not a valid ISO-8601 date/time.") from None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------- history ---

def record_value(ontology, entity, prop: str, value, at, source="fabric") -> bool:
    """Temporal upsert: close the interval that covered `at` and open a new one (also correct for late-arriving events)."""
    H = TwinAttributeHistory
    cover = H.objects.filter(entity=entity, property=prop, valid_from__lte=at).exclude(valid_to__lte=at).order_by("-valid_from").first()
    if cover is not None:
        if cover.value == value:
            return False
        end = cover.valid_to
        cover.valid_to = at
        cover.save(update_fields=["valid_to"])
    else:
        nxt = H.objects.filter(entity=entity, property=prop, valid_from__gt=at).order_by("valid_from").first()
        end = nxt.valid_from if nxt else None
    H.objects.create(ontology=ontology, entity=entity, property=prop, value=value, valid_from=at, valid_to=end, source=source)
    return True


def ensure_history(ontology) -> int:
    """Entities that existed before the twin was used get a first history row (valid from their creation)."""
    have = set(TwinAttributeHistory.objects.filter(ontology=ontology).values_list("entity_id", flat=True).distinct())
    n = 0
    rows = []
    for e in FabricEntity.objects.filter(ontology=ontology, status="active").exclude(id__in=have)[:50000]:
        for k, v in (e.canonical or {}).items():
            rows.append(TwinAttributeHistory(ontology=ontology, entity=e, property=k, value=v, valid_from=e.created_at, source="fabric"))
        n += 1
        if len(rows) >= 2000:
            TwinAttributeHistory.objects.bulk_create(rows)
            rows = []
    if rows:
        TwinAttributeHistory.objects.bulk_create(rows)
    return n


def on_entity_changed(entity, changes, existed):
    """Fabric hook: keep the temporal history and the event stream in step with golden-record changes."""
    t = now()
    for c in changes:
        record_value(entity.ontology, entity, c["property"], c["new"], t, "fabric")
    TwinEvent.objects.create(ontology=entity.ontology, entity=entity, event_type="entity.updated" if existed else "entity.created", occurred_at=t, source="fabric",
                             payload={"changes": [{"property": c["property"], "old": c["old"], "new": c["new"]} for c in changes[:40]]})


def register_hooks():
    from ..fabric import hooks

    hooks.register("entity_changed", on_entity_changed)


NUMERIC = {"integer", "int", "long", "decimal", "float", "double"}


def typed(model: dict) -> dict:
    return {p["name"]: p.get("datatype", "string") for p in model.get("dataProperties", [])}


def coerce(value, datatype: str):
    """Source systems deliver text ('100'); rules and simulations need numbers/booleans per the ontology's declared datatype."""
    if isinstance(value, str):
        v = value.strip()
        if datatype in NUMERIC:
            try:
                f = float(v.replace(",", ""))
                return int(f) if f.is_integer() else f
            except ValueError:
                return value
        if datatype == "boolean":
            return {"true": True, "yes": True, "1": True, "false": False, "no": False, "0": False}.get(v.lower(), value)
    return value


def coerce_all(model, values: dict) -> dict:
    t = typed(model)
    return {k: coerce(v, t.get(k, "string")) for k, v in values.items()}


def values_at(ontology, entity_ids=None, as_of=None) -> dict[int, dict]:
    """{entity_id: {property: value}} - current, or as it was at `as_of`. Values are typed per the ontology."""
    qs = TwinAttributeHistory.objects.filter(ontology=ontology)
    if entity_ids is not None:
        qs = qs.filter(entity_id__in=list(entity_ids))
    if as_of is None:
        qs = qs.filter(valid_to__isnull=True)
    else:
        from django.db.models import Q

        qs = qs.filter(valid_from__lte=as_of).filter(Q(valid_to__isnull=True) | Q(valid_to__gt=as_of))
    out: dict[int, dict] = defaultdict(dict)
    t = typed(ontology.model)
    for eid, p, v in qs.values_list("entity_id", "property", "value").iterator():
        out[eid][p] = coerce(v, t.get(p, "string"))
    return out


def history_of(ontology, entity, prop=None, limit=500) -> list[dict]:
    qs = TwinAttributeHistory.objects.filter(ontology=ontology, entity=entity)
    if prop:
        qs = qs.filter(property=prop)
    return [{"property": h.property, "value": h.value, "validFrom": h.valid_from.isoformat(), "validTo": h.valid_to.isoformat() if h.valid_to else None,
             "source": h.source} for h in qs.order_by("property", "valid_from")[:limit]]


# ---------------------------------------------------------------- events ---

def _entity(ontology, eid) -> FabricEntity:
    e = FabricEntity.objects.filter(pk=eid, ontology=ontology).first()
    if not e:
        raise TwinError(f"Entity {eid} not found.")
    if e.status == "merged" and e.merged_into_id:
        e = FabricEntity.objects.get(pk=e.merged_into_id)
    if e.status != "active":
        raise TwinError(f"Entity {eid} is {e.status}.")
    return e


def _declared_props(model, cls) -> set:
    anc, seen, todo = {cls}, set(), [cls]
    by = {c["name"]: c for c in model["classes"]}
    while todo:
        c = todo.pop()
        if c in seen:
            continue
        seen.add(c)
        for p in by.get(c, {}).get("parents", []):
            anc.add(p)
            todo.append(p)
    return {p["name"] for p in model["dataProperties"] if p["domain"] in anc}


def ingest_event(ontology, ev: dict, actor="") -> TwinEvent:
    etype = str(ev.get("type", "")).strip()
    if not etype or len(etype) > 80:
        raise TwinError("Event type is required (max 80 characters).")
    entity = _entity(ontology, ev["entityId"]) if ev.get("entityId") is not None else None
    at = parse_time(ev.get("occurredAt"), now())
    if at > now().replace(microsecond=0) and (at - now()).total_seconds() > 300:
        raise TwinError("occurredAt is in the future.")
    payload = ev.get("payload") or {}
    sets = ev.get("set") or {}
    if not isinstance(payload, dict) or not isinstance(sets, dict):
        raise TwinError("payload and set must be objects.")
    if sets and not entity:
        raise TwinError("Setting attributes requires entityId.")
    trans = ev.get("transition")
    if trans and not entity:
        raise TwinError("A process transition requires entityId.")
    with transaction.atomic():
        if sets:
            ok = _declared_props(ontology.model, entity.class_name)
            for k in sets:
                if k not in ok:
                    raise TwinError(f"'{k}' is not an attribute of {entity.class_name}.")
            ensure_history_for(ontology, entity)
            for k, v in sets.items():
                record_value(ontology, entity, k, v, at, "event")
        if trans:
            advance_process(ontology, entity, ev.get("process"), trans, at, actor)
        te = TwinEvent.objects.create(ontology=ontology, entity=entity, event_type=etype, occurred_at=at, payload={**payload, **({"set": sets} if sets else {}),
                                                                                                                   **({"transition": trans} if trans else {})},
                                      source=str(ev.get("source", "api"))[:60], actor=actor)
    return te


def ensure_history_for(ontology, entity):
    if not TwinAttributeHistory.objects.filter(entity=entity).exists():
        for k, v in (entity.canonical or {}).items():
            TwinAttributeHistory.objects.create(ontology=ontology, entity=entity, property=k, value=v, valid_from=entity.created_at, source="fabric")


def create_twin_entity(ontology, cls: str, name: str, attributes: dict, actor="") -> FabricEntity:
    if cls not in {c["name"] for c in ontology.model["classes"]}:
        raise TwinError(f"Unknown class '{cls}'.")
    if not name or len(name) > 300:
        raise TwinError("name is required (max 300 characters).")
    ok = _declared_props(ontology.model, cls)
    bad = [k for k in attributes if k not in ok]
    if bad:
        raise TwinError(f"Not attributes of {cls}: {', '.join(bad)}.")
    t = now()
    e = FabricEntity.objects.create(ontology=ontology, class_name=cls, display_name=name, canonical=dict(attributes), origin="twin", record_count=0)
    for k, v in attributes.items():
        TwinAttributeHistory.objects.create(ontology=ontology, entity=e, property=k, value=v, valid_from=t, source="twin")
    TwinEvent.objects.create(ontology=ontology, entity=e, event_type="entity.created", occurred_at=t, source="twin", actor=actor, payload={"attributes": attributes})
    FabricEvent.objects.create(ontology=ontology, kind="entity_created_in_twin", entity=e, payload={"by": actor})
    from ..fabric.resolution import bump

    bump(ontology)  # the entity graph caches follow FabricState.version
    return e


# ------------------------------------------------------------- processes ---

def validate_process(model, p: dict) -> dict:
    name, cls = str(p.get("name", "")).strip(), p.get("entityClass")
    states, trans = p.get("states"), p.get("transitions")
    if not name or len(name) > 120:
        raise TwinError("Process name is required.")
    if cls not in {c["name"] for c in model["classes"]}:
        raise TwinError("entityClass must be a class of the ontology.")
    if not isinstance(states, list) or len(states) < 2 or any(not isinstance(s, str) or not s for s in states) or len(set(states)) != len(states):
        raise TwinError("states must be a list of at least two distinct names.")
    initial = p.get("initial") or states[0]
    if initial not in states:
        raise TwinError("initial must be one of the states.")
    if not isinstance(trans, list) or not trans:
        raise TwinError("transitions must be a non-empty list.")
    seen = set()
    for t in trans:
        if not isinstance(t, dict) or not all(isinstance(t.get(k), str) for k in ("name", "from", "to")):
            raise TwinError("Each transition needs name, from and to.")
        if t["from"] not in states or t["to"] not in states:
            raise TwinError(f"Transition '{t['name']}' uses an unknown state.")
        if (t["name"], t["from"]) in seen:
            raise TwinError(f"Transition '{t['name']}' from '{t['from']}' is defined twice.")
        seen.add((t["name"], t["from"]))
    return {"name": name, "entity_class": cls, "states": states, "initial": initial, "transitions": trans, "description": str(p.get("description", ""))[:500]}


def _instance(ontology, entity, process_ref, create=False) -> TwinProcessInstance:
    qs = TwinProcess.objects.filter(ontology=ontology, entity_class=entity.class_name)
    proc = qs.filter(pk=process_ref).first() if isinstance(process_ref, int) else qs.filter(name=process_ref).first() if process_ref else (qs.first() if qs.count() == 1 else None)
    if not proc:
        raise TwinError("Process not found for this entity's class (pass 'process' when several exist).")
    inst = TwinProcessInstance.objects.filter(process=proc, entity=entity).first()
    if not inst and create:
        inst = TwinProcessInstance.objects.create(process=proc, entity=entity, state=proc.initial, history=[{"state": proc.initial, "transition": None, "at": now().isoformat()}])
    if not inst:
        raise TwinError("This entity is not in the process yet (start it first).")
    return inst


def next_state(inst, transition: str) -> str:
    for t in inst.process.transitions:
        if t["name"] == transition and t["from"] == inst.state:
            return t["to"]
    allowed = [t["name"] for t in inst.process.transitions if t["from"] == inst.state]
    raise TwinError(f"Transition '{transition}' is not allowed from state '{inst.state}'. Allowed: {', '.join(allowed) or 'none (final state)'}.")


def advance_process(ontology, entity, process_ref, transition, at, actor):
    inst = _instance(ontology, entity, process_ref)
    to = next_state(inst, transition)
    inst.history = [*inst.history, {"state": to, "transition": transition, "at": at.isoformat(), "actor": actor}]
    inst.state = to
    inst.save()
    return inst


def start_process(ontology, entity, process_ref) -> TwinProcessInstance:
    return _instance(ontology, entity, process_ref, create=True)


# ----------------------------------------------------------------- rules ---

def validate_rule(model, r: dict) -> dict:
    name, cls, kind = str(r.get("name", "")).strip(), r.get("class"), r.get("kind")
    if not name or len(name) > 120:
        raise TwinError("Rule name is required.")
    if cls not in {c["name"] for c in model["classes"]}:
        raise TwinError("class must be a class of the ontology.")
    if kind not in ("derive", "alert"):
        raise TwinError("kind must be 'derive' or 'alert'.")
    target = str(r.get("target", "")).strip()
    if kind == "derive" and not target.isidentifier():
        raise TwinError("A derive rule needs a target attribute name (letters/digits).")
    try:
        tree = expr.parse(r.get("expression"))
    except expr.ExprError as e:
        raise TwinError(str(e)) from None
    own, refs = expr.names_used(tree)
    known = _declared_props(model, cls) | ({target} if kind == "derive" else set())
    derived_ok = {x["target"] for x in r.get("_siblings", [])}
    unknown = sorted(own - known - derived_ok)
    if unknown:
        raise TwinError(f"Unknown attribute(s) for {cls}: {', '.join(unknown)}.")
    classes = {c["name"] for c in model["classes"]}
    for c, p in refs:
        if c not in classes:
            raise TwinError(f"Unknown class '{c}' in the expression.")
        if p and p not in _declared_props(model, c):
            raise TwinError(f"'{p}' is not an attribute of {c}.")
    return {"name": name, "class_name": cls, "kind": kind, "target": target, "expression": r["expression"].strip(), "message": str(r.get("message", ""))[:300],
            "severity": r.get("severity") if r.get("severity") in ("info", "warning", "critical") else "warning"}


def evaluate_rules(rules, graph, values: dict[int, dict], entity_ids) -> dict[int, dict]:
    """-> {entity_id: {"derived": {target: value}, "alerts": [{rule, severity, message}], "unknown": [rule names]}}."""
    parsed = [(r, expr.parse(r.expression)) for r in rules if r.enabled]
    out = {}
    for eid in entity_ids:
        e = graph.ents.get(eid)
        if not e:
            continue
        own = dict(values.get(eid, {}))

        def related(cls, eid=eid):
            return [values.get(nb, {}) for nb, _p, _f in graph.adj.get(eid, []) if graph.ents.get(nb) and graph.ents[nb].class_name == cls]

        derived, alerts, unknown = {}, [], []
        for _ in range(3):  # derived values may feed other derived values
            changed = False
            for r, tree in parsed:
                if r.kind == "derive" and r.class_name == e.class_name:
                    v = expr.evaluate(tree, {**own, **derived}, related)
                    if derived.get(r.target) != v:
                        derived[r.target] = v
                        changed = True
            if not changed:
                break
        for r, tree in parsed:
            if r.kind == "alert" and r.class_name == e.class_name:
                v = expr.evaluate(tree, {**own, **derived}, related)
                if v is None:
                    unknown.append(r.name)
                elif v:
                    alerts.append({"rule": r.name, "severity": r.severity, "message": r.message or r.name})
        out[eid] = {"derived": derived, "alerts": alerts, "unknown": unknown}
    return out


# ------------------------------------------------------------ simulation ---

def simulate(ontology, changes: list[dict], hops: int = 2, process: dict | None = None) -> dict:
    """What-if. Reads only: nothing is written (no history, no events, no fabric changes)."""
    if not isinstance(changes, list) or not (changes or process) or len(changes) > 200:
        raise TwinError("Provide 1-200 changes (or a process step).")
    graph = get_entity_graph(ontology)
    ensure_hist = TwinAttributeHistory.objects.filter(ontology=ontology).exists()
    base_all = values_at(ontology) if ensure_hist else {}
    for eid, e in graph.ents.items():  # entities with no history yet fall back to the golden record
        base_all.setdefault(eid, coerce_all(ontology.model, e.canonical or {}))
    sim_all, applied = {k: dict(v) for k, v in base_all.items()}, []
    for c in changes:
        eid, prop, op = c.get("entityId"), c.get("property"), c.get("op", "set")
        e = graph.ents.get(eid)
        if e is None:
            raise TwinError(f"Entity {eid} not found.")
        if prop not in _declared_props(ontology.model, e.class_name):
            raise TwinError(f"'{prop}' is not an attribute of {e.class_name}.")
        if op not in ("set", "add", "multiply"):
            raise TwinError("op must be set, add or multiply.")
        before = sim_all[eid].get(prop)
        val = c.get("value")
        if op != "set":
            try:
                before_n, val_n = float(before), float(val)
            except (TypeError, ValueError):
                raise TwinError(f"'{op}' needs numeric values ({prop} is {before!r}).") from None
            val = before_n + val_n if op == "add" else before_n * val_n
            val = int(val) if float(val).is_integer() else val
        sim_all[eid][prop] = val
        applied.append({"entityId": eid, "class": e.class_name, "name": e.display_name, "property": prop, "before": before, "after": val, "op": op})
    seeds = list({a["entityId"] for a in applied})
    reach = graph.reach(seeds, max(0, min(hops, 3)), limit=500) if seeds else {}
    rules = list(TwinRule.objects.filter(ontology=ontology, enabled=True))
    before_r = evaluate_rules(rules, graph, base_all, reach)
    after_r = evaluate_rules(rules, graph, sim_all, reach)
    derived_changes, alert_new, alert_cleared = [], [], []
    for eid in reach:
        b, a = before_r.get(eid, {}), after_r.get(eid, {})
        for k in sorted(set(b.get("derived", {})) | set(a.get("derived", {}))):
            if b.get("derived", {}).get(k) != a.get("derived", {}).get(k):
                derived_changes.append({"entityId": eid, "class": graph.ents[eid].class_name, "name": graph.ents[eid].display_name, "attribute": k,
                                        "before": b.get("derived", {}).get(k), "after": a.get("derived", {}).get(k)})
        ba, aa = {x["rule"] for x in b.get("alerts", [])}, {x["rule"] for x in a.get("alerts", [])}
        for x in a.get("alerts", []):
            if x["rule"] not in ba:
                alert_new.append({"entityId": eid, "name": graph.ents[eid].display_name, **x})
        for x in b.get("alerts", []):
            if x["rule"] not in aa:
                alert_cleared.append({"entityId": eid, "name": graph.ents[eid].display_name, **x})
    proc_out = None
    if process:
        e = _entity(ontology, process.get("entityId"))
        inst = _instance(ontology, e, process.get("process"))
        proc_out = {"entityId": e.id, "from": inst.state, "transition": process.get("transition"), "to": next_state(inst, process.get("transition"))}
    return {"persisted": False, "applied": applied, "impacted": [{"entityId": i, "class": graph.ents[i].class_name, "name": graph.ents[i].display_name, "hops": d}
                                                                 for i, d in sorted(reach.items(), key=lambda kv: (kv[1], kv[0]))],
            "derivedChanges": derived_changes, "alertsTriggered": alert_new, "alertsCleared": alert_cleared, "process": proc_out,
            "summary": {"changes": len(applied), "impactedEntities": len(reach), "derivedChanged": len(derived_changes), "newAlerts": len(alert_new),
                        "clearedAlerts": len(alert_cleared)}}


# -------------------------------------------------------------- snapshot ---

def snapshot(ontology, as_of=None, limit=200) -> dict:
    ensure_history(ontology)
    graph = get_entity_graph(ontology)
    vals = values_at(ontology, as_of=as_of)
    rules = list(TwinRule.objects.filter(ontology=ontology))
    ids = sorted(graph.ents)
    if as_of is not None:
        ids = [i for i in ids if i in vals]
    res = evaluate_rules(rules, graph, vals, ids[:5000]) if rules else {}
    by_class = Counter(graph.ents[i].class_name for i in ids)
    alerts = [{"entityId": i, "class": graph.ents[i].class_name, "name": graph.ents[i].display_name, **a} for i in ids[:5000] for a in res.get(i, {}).get("alerts", [])]
    ents = [{"id": i, "class": graph.ents[i].class_name, "name": graph.ents[i].display_name, "origin": graph.ents[i].origin, "attributes": vals.get(i, {}),
             "derived": res.get(i, {}).get("derived", {}), "alerts": [a["rule"] for a in res.get(i, {}).get("alerts", [])]} for i in ids[:limit]]
    procs = []
    for p in TwinProcess.objects.filter(ontology=ontology):
        counts = Counter(p.instances.values_list("state", flat=True))
        procs.append({"id": p.id, "name": p.name, "entityClass": p.entity_class, "states": p.states, "initial": p.initial, "transitions": p.transitions,
                      "instances": {s: counts.get(s, 0) for s in p.states}})
    assets = [{"id": a.id, "name": a.name, "type": a.asset_type, "parentId": a.parent_id, "entityId": a.entity_id, "attributes": a.attributes, "status": a.status}
              for a in TwinAsset.objects.filter(ontology=ontology)[:1000]]
    events = [{"id": e.id, "type": e.event_type, "entityId": e.entity_id, "occurredAt": e.occurred_at.isoformat(), "source": e.source, "payload": e.payload}
              for e in TwinEvent.objects.filter(ontology=ontology)[:30]]
    return {"asOf": as_of.isoformat() if as_of else None, "entities": {"total": len(ids), "byClass": dict(by_class), "items": ents},
            "relationships": sum(len(v) for v in graph.adj.values()) // 2, "rules": [rule_json(r) for r in rules], "alerts": alerts[:200], "processes": procs,
            "assets": assets, "recentEvents": events}


def rule_json(r: TwinRule) -> dict:
    return {"id": r.id, "name": r.name, "class": r.class_name, "kind": r.kind, "target": r.target, "expression": r.expression, "message": r.message,
            "severity": r.severity, "enabled": r.enabled}


def deepcopy(x):
    return copy.deepcopy(x)
