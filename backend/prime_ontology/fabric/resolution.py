"""Entity resolution, survivorship (golden record) and match suggestions.

Deterministic resolution: two records of the same class are the same entity when they share an identity token (rules in the mapping,
e.g. normalised e-mail or a shared customer id) — transitively (A~B, B~C => A,B,C), across source systems. Human decisions override:
a MUST link forces records together, a CANNOT link keeps them apart no matter what the tokens say.

Resolution is INCREMENTAL: only the connected neighbourhood of changed records is recomputed. Entity ids are stable: when entities
merge the oldest/largest survives (the others point to it via merged_into); when an entity must split, the largest piece keeps the id.
"""
import difflib
from collections import Counter, defaultdict
from datetime import datetime, timezone

from django.db import transaction

from .models import (FabricConstraint, FabricEntity, FabricEvent, FabricRecord, FabricState, FabricSuggestion, FabricToken)
from .normalize import n_company, n_text

MAX_CLOSURE = 50_000
CHUNK = 800


def now():
    return datetime.now(timezone.utc)


def bump(ontology):
    st, _ = FabricState.objects.get_or_create(ontology=ontology)
    FabricState.objects.filter(pk=st.pk).update(version=st.version + 1)


def spec_index(ontology) -> dict:
    """(source_id, class) -> mapping entity spec."""
    out = {}
    for s in ontology.fabric_sources.all():
        for e in (s.mapping or {}).get("entities", []):
            out[(s.id, e.get("class"))] = e
    return out


def _chunks(seq, n=CHUNK):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _ts(rec):
    return rec.source_updated_at or rec.last_changed_at or rec.last_seen or rec.first_seen


# ------------------------------------------------------------------ survivorship
def build_golden(records, specs, sources) -> tuple[dict, dict, list, str]:
    """-> (canonical, provenance, conflicts, display_name). `records` are the active records of ONE entity."""
    cands = defaultdict(list)
    for r in records:
        for prop, v in (r.data or {}).items():
            if v is not None and v != "":
                cands[prop].append((v, r))
    canonical, prov, conflicts = {}, {}, []
    for prop, lst in cands.items():
        strat = "source_priority"
        for r in records:
            sp = (specs.get((r.source_id, r.class_name)) or {}).get("survivorship") or {}
            if prop in sp:
                strat = sp[prop]
                break
        key_pri = lambda t: (sources[t[1].source_id].priority, _ts(t[1]) or datetime.min.replace(tzinfo=timezone.utc), t[1].id)
        if strat == "most_recent":
            best = max(lst, key=lambda t: (_ts(t[1]) or datetime.min.replace(tzinfo=timezone.utc), sources[t[1].source_id].priority, t[1].id))
        elif strat == "longest":
            best = max(lst, key=lambda t: (len(str(t[0])), *key_pri(t)))
        elif strat == "first_non_null":
            best = min(lst, key=lambda t: t[1].id)
        elif str(strat).startswith("source:"):
            pref = [t for t in lst if sources[t[1].source_id].name == strat[7:]]
            best = max(pref or lst, key=key_pri)
        else:
            best = max(lst, key=key_pri)
        v, r = best
        canonical[prop] = v
        prov[prop] = {"source": sources[r.source_id].name, "recordId": r.id, "externalId": r.external_id, "strategy": strat,
                      "at": (_ts(r).isoformat() if _ts(r) else None)}
        distinct = {}
        for val, rec in lst:
            distinct.setdefault(str(val), []).append(sources[rec.source_id].name)
        if len(distinct) > 1:
            conflicts.append({"property": prop, "chosen": v, "values": [{"value": k, "sources": sorted(set(s))} for k, s in distinct.items()]})
    display = ""
    for r in records:
        d = (specs.get((r.source_id, r.class_name)) or {}).get("display")
        if d and canonical.get(d):
            display = str(canonical[d])
            break
    if not display:
        for k in sorted(canonical, key=lambda k: (0 if k.lower().endswith("name") else 1 if "name" in k.lower() or "title" in k.lower() else 2, k)):
            if isinstance(canonical[k], str) and ("name" in k.lower() or "title" in k.lower()):
                display = canonical[k]
                break
    if not display and records:
        display = f"{records[0].class_name} {records[0].external_id}"
    return canonical, prov, conflicts, display[:300]


def refresh_entity(entity, records, specs, sources, run=None, events=None):
    """Rebuild the golden record. Returns the list of {property, old, new} changes."""
    canonical, prov, conflicts, display = build_golden(records, specs, sources)
    old = entity.canonical or {}
    changes = [{"property": k, "old": old.get(k), "new": canonical.get(k)} for k in sorted(set(old) | set(canonical)) if old.get(k) != canonical.get(k)]
    entity.canonical, entity.provenance, entity.display_name = canonical, {**prov, "__conflicts": conflicts} if conflicts else prov, display
    entity.record_count = len(records)
    entity.status = "active"
    entity.save()
    if changes and events is not None and old:
        events.append(FabricEvent(ontology=entity.ontology, kind="entity_updated", entity=entity, run=run, payload={"changes": changes[:50]}))
    if changes:
        from . import hooks

        hooks.fire("entity_changed", entity, changes, bool(old))  # e.g. the digital twin records attribute history
    return changes


# ------------------------------------------------------------------ resolution
class _UF:
    def __init__(self, items):
        self.p = {i: i for i in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def resolve(ontology, seed_record_ids, run=None) -> dict:
    seeds = set(seed_record_ids)
    summary = Counter()
    if not seeds:
        return dict(summary)
    by_class = defaultdict(set)
    for cls, rid in FabricRecord.objects.filter(id__in=seeds).values_list("class_name", "id"):
        by_class[cls].add(rid)
    specs = spec_index(ontology)
    sources = {s.id: s for s in ontology.fabric_sources.all()}
    for cls, ids in by_class.items():
        with transaction.atomic():
            summary.update(_resolve_class(ontology, cls, ids, specs, sources, run))
    bump(ontology)
    return dict(summary)


def _closure(ontology, cls, seeds):
    closure, frontier, touched = set(seeds), set(seeds), set()
    for _ in range(50):
        if not frontier:
            break
        new = set()
        ents = set()
        for part in _chunks(frontier):
            ents |= {e for e in FabricRecord.objects.filter(id__in=part).values_list("entity_id", flat=True) if e}
        ents -= touched
        touched |= ents
        tokens = set()
        for part in _chunks(frontier):
            tokens |= set(FabricToken.objects.filter(record_id__in=part).values_list("token", flat=True))
        for part in _chunks(tokens):
            new |= set(FabricToken.objects.filter(ontology=ontology, class_name=cls, token__in=part).values_list("record_id", flat=True))
        for part in _chunks(ents):
            new |= set(FabricRecord.objects.filter(entity_id__in=part).values_list("id", flat=True))
        for part in _chunks(frontier):
            for a, b in FabricConstraint.objects.filter(ontology=ontology, kind="must", record_a_id__in=part).values_list("record_a_id", "record_b_id"):
                new.add(b)
            for a, b in FabricConstraint.objects.filter(ontology=ontology, kind="must", record_b_id__in=part).values_list("record_a_id", "record_b_id"):
                new.add(a)
        frontier = new - closure
        closure |= frontier
        if len(closure) > MAX_CLOSURE:
            raise RuntimeError("Entity-resolution neighbourhood is too large (>50,000 records); check the identity rules for over-matching keys.")
    return closure


def _resolve_class(ontology, cls, seeds, specs, sources, run) -> Counter:
    stats = Counter()
    closure = _closure(ontology, cls, seeds)
    recs = {r.id: r for r in FabricRecord.objects.filter(id__in=closure)}
    active = {i: r for i, r in recs.items() if not r.deleted}
    old_entity = {i: r.entity_id for i, r in recs.items()}
    uf = _UF(active.keys())
    token_members = defaultdict(list)
    for part in _chunks(active.keys()):
        for rid, tok in FabricToken.objects.filter(record_id__in=part).values_list("record_id", "token"):
            token_members[tok].append(rid)
    for tok, members in token_members.items():
        for m in members[1:]:
            uf.union(members[0], m)
    must = [(c.record_a_id, c.record_b_id) for c in FabricConstraint.objects.filter(ontology=ontology, kind="must", class_name=cls)
            if c.record_a_id in active and c.record_b_id in active]
    for a, b in must:
        uf.union(a, b)
    cannot = [(c.record_a_id, c.record_b_id) for c in FabricConstraint.objects.filter(ontology=ontology, kind="cannot", class_name=cls)
              if c.record_a_id in active and c.record_b_id in active]
    # CANNOT links win: rebuild the union without the token edges of an offending record, keeping must-links
    if any(uf.find(a) == uf.find(b) for a, b in cannot):
        for a, b in cannot:
            if uf.find(a) == uf.find(b):
                victim = max(a, b)
                uf = _UF(active.keys())
                for tok, members in token_members.items():
                    members = [m for m in members if m != victim]
                    for m in members[1:]:
                        uf.union(members[0], m)
                for x, y in must:
                    uf.union(x, y)
                stats["cannot_link_enforced"] += 1
    comps = defaultdict(list)
    for rid in active:
        comps[uf.find(rid)].append(rid)
    ordered = sorted(comps.values(), key=lambda c: (-len(c), min(c)))
    entities = {e.id: e for e in FabricEntity.objects.filter(id__in={e for e in old_entity.values() if e})}
    claimed, comp_entity, events = set(), {}, []
    for comp in ordered:
        cnt = Counter(old_entity[r] for r in comp if old_entity[r] and old_entity[r] in entities and entities[old_entity[r]].status != "merged")
        pick = None
        for eid, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], entities[kv[0]].created_at)):
            if eid not in claimed:
                pick = entities[eid]
                break
        if pick is None:
            pick = FabricEntity.objects.create(ontology=ontology, class_name=cls)
            entities[pick.id] = pick
            stats["entities_created"] += 1
            events.append(FabricEvent(ontology=ontology, kind="entity_created", entity=pick, run=run,
                                      payload={"records": [[recs[r].source_id, recs[r].external_id] for r in comp][:50]}))
        claimed.add(pick.id)
        comp_entity[pick.id] = comp
    # entities that lost all records: merged (everything moved into one other entity) or deleted
    comp_of_record = {r: eid for eid, comp in comp_entity.items() for r in comp}
    for eid, ent in list(entities.items()):
        if eid in comp_entity:
            continue
        olds = [r for r, e in old_entity.items() if e == eid and r in comp_of_record]
        dests = {comp_of_record[r] for r in olds}
        if len(dests) == 1 and olds:
            ent.status, ent.merged_into_id, ent.record_count = "merged", next(iter(dests)), 0
            ent.save()
            stats["entities_merged"] += 1
            events.append(FabricEvent(ontology=ontology, kind="entities_merged", entity_id=next(iter(dests)), run=run,
                                      payload={"survivor": next(iter(dests)), "absorbed": eid, "records": len(olds)}))
        elif ent.origin != "twin":
            ent.status, ent.record_count = "deleted", 0
            ent.save()
            stats["entities_deleted"] += 1
            events.append(FabricEvent(ontology=ontology, kind="entity_deleted", entity=ent, run=run, payload={}))
    # splits: an entity whose old records now live in several components
    for eid, ent in entities.items():
        if eid in comp_entity:
            keep_old = [r for r, e in old_entity.items() if e == eid]
            moved_out = [r for r in keep_old if r in comp_of_record and comp_of_record[r] != eid]
            if moved_out:
                stats["entities_split"] += 1
                events.append(FabricEvent(ontology=ontology, kind="entity_split", entity=ent, run=run,
                                          payload={"records_moved": len(moved_out), "into": sorted({comp_of_record[r] for r in moved_out})}))
    # link records + golden records
    to_update = []
    for eid, comp in comp_entity.items():
        shared = defaultdict(set)
        for tok, members in token_members.items():
            ms = [m for m in members if m in set(comp)]
            if len(ms) > 1:
                for m in ms:
                    shared[m].add(tok)
        for rid in comp:
            r = recs[rid]
            reason = {"rule": "deterministic", "tokens": sorted(shared.get(rid, []))[:6]} if shared.get(rid) else \
                {"rule": "manual" if any(rid in (a, b) for a, b in must) else "singleton"}
            if r.entity_id != eid or r.link_reason != reason:
                r.entity_id, r.link_reason = eid, reason
                to_update.append(r)
    for rid, r in recs.items():
        if r.deleted and r.entity_id:
            r.entity_id = None
            to_update.append(r)
    FabricRecord.objects.bulk_update(to_update, ["entity", "link_reason"], batch_size=500)
    for eid, comp in comp_entity.items():
        refresh_entity(entities[eid], [recs[r] for r in comp], specs, sources, run, events)
        stats["entities_refreshed"] += 1
    FabricEvent.objects.bulk_create(events)
    return stats


# ---------------------------------------------------------------- suggestions
def suggest_matches(ontology, entity_ids, specs, limit=200) -> int:
    """Fuzzy candidates for human review. Never merges anything."""
    created = 0
    ents = list(FabricEntity.objects.filter(ontology=ontology, id__in=entity_ids, status="active"))
    by_class = defaultdict(list)
    for e in ents:
        by_class[e.class_name].append(e)
    for cls, mine in by_class.items():
        fz = next((s.get("fuzzy") for (sid, c), s in specs.items() if c == cls and s.get("fuzzy")), None)
        if not fz:
            continue
        fields, thr, corr = fz["fields"], float(fz.get("threshold", 0.92)), fz.get("corroborate") or []
        key = lambda e: n_company(" ".join(str(e.canonical.get(f, "")) for f in fields))
        pool = defaultdict(list)
        for e in FabricEntity.objects.filter(ontology=ontology, class_name=cls, status="active"):
            k = key(e)
            if k:
                pool[k[:3]].append((e, k))
        decided = {(s.entity_a_id, s.entity_b_id) for s in FabricSuggestion.objects.filter(ontology=ontology, class_name=cls)}
        for e in mine:
            ka = key(e)
            if not ka:
                continue
            for other, kb in pool.get(ka[:3], []):
                if other.id == e.id:
                    continue
                a, b = sorted((e, other), key=lambda x: x.id)
                if (a.id, b.id) in decided:
                    continue
                score = difflib.SequenceMatcher(None, ka, kb).ratio()
                if score < thr:
                    continue
                reasons = [f"names are {score:.0%} similar ('{a.display_name}' ~ '{b.display_name}')"]
                if corr:
                    agree = [c for c in corr if n_text(a.canonical.get(c)) and n_text(a.canonical.get(c)) == n_text(b.canonical.get(c))]
                    if not agree:
                        continue  # a similar name alone is not enough when corroboration is required
                    reasons.append("also agree on " + ", ".join(agree))
                FabricSuggestion.objects.create(ontology=ontology, class_name=cls, entity_a=a, entity_b=b, score=round(score, 3), reasons=reasons)
                decided.add((a.id, b.id))
                created += 1
                if created >= limit:
                    return created
    return created


def decide_suggestion(suggestion, decision: str, actor: str) -> dict:
    """accept -> MUST link (entities merge); reject -> CANNOT link (never suggested/merged again)."""
    if suggestion.status != "pending":
        raise ValueError("This suggestion was already decided.")
    ontology = suggestion.ontology
    ra = list(FabricRecord.objects.filter(entity=suggestion.entity_a, deleted=False).values_list("id", flat=True)[:50])
    rb = list(FabricRecord.objects.filter(entity=suggestion.entity_b, deleted=False).values_list("id", flat=True)[:50])
    if not ra or not rb:
        raise ValueError("One of the entities no longer has records.")
    if decision == "accept":
        FabricConstraint.objects.get_or_create(ontology=ontology, class_name=suggestion.class_name, record_a_id=ra[0], record_b_id=rb[0], kind="must",
                                               defaults={"created_by": actor})
    else:
        for a in ra[:10]:
            for b in rb[:10]:
                FabricConstraint.objects.get_or_create(ontology=ontology, class_name=suggestion.class_name, record_a_id=a, record_b_id=b, kind="cannot",
                                                       defaults={"created_by": actor})
    suggestion.status, suggestion.decided_by, suggestion.decided_at = ("accepted" if decision == "accept" else "rejected"), actor, now()
    suggestion.save()
    FabricEvent.objects.create(ontology=ontology, kind=f"match_{suggestion.status}", entity=suggestion.entity_a,
                               payload={"other": suggestion.entity_b_id, "score": suggestion.score, "by": actor})
    out = resolve(ontology, ra[:1] + rb[:1])
    return out
