"""Incremental synchronisation of one source into the fabric.

fetch (incremental by watermark when the mapping has an updated-at field)
  -> transform -> upsert records (new / changed / unchanged by content hash) -> tokens + references
  -> deletion detection (only on full scans) -> schema-drift sampling
  -> incremental entity resolution of just the touched neighbourhood -> fuzzy suggestions -> lineage events -> freshness.

A source that cannot be reached marks the run failed and leaves existing fabric data untouched. Per-record problems (missing id...)
are counted and sampled, and make the run `partial`, not failed.
"""
import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from django.db import transaction
from django.db.models import Q

from ..ingest.common import infer_sql_type
from ..typemap import sql_to_xsd
from .adapters import build_adapter
from .crypto import SecretError, decrypt_json, redact
from .http import ConnectorError
from .mapping_spec import SkipRecord, transform
from .models import FabricDrift, FabricEvent, FabricRecord, FabricRef, FabricSource, FabricSyncRun, FabricToken
from .resolution import bump, resolve, spec_index, suggest_matches

log = logging.getLogger("prime_ontology.fabric")
BATCH = 500
LOCK_MINUTES = 30


class SyncBusy(Exception):
    pass


def _now():
    return datetime.now(timezone.utc)


def _diff(old: dict, new: dict) -> list[dict]:
    return [{"field": k, "old": old.get(k), "new": new.get(k)} for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)][:50]


def _acquire(source) -> bool:
    cutoff = _now() - timedelta(minutes=LOCK_MINUTES)
    n = FabricSource.objects.filter(pk=source.pk).filter(Q(running_since__isnull=True) | Q(running_since__lt=cutoff)).update(running_since=_now())
    return n == 1


def _release(source):
    FabricSource.objects.filter(pk=source.pk).update(running_since=None)


def sync_source(source: FabricSource, *, full: bool = False, trigger: str = "manual", actor: str = "") -> FabricSyncRun:
    if not source.enabled:
        raise ConnectorError("This source is disabled.")
    if not _acquire(source):
        raise SyncBusy("A synchronisation of this source is already running.")
    source.refresh_from_db()
    ontology = source.ontology
    mode = "full" if (full or (source.full_every and source.sync_count % source.full_every == 0)) else "incremental"
    run = FabricSyncRun.objects.create(source=source, mode=mode, trigger=trigger, actor=actor)
    FabricEvent.objects.create(ontology=ontology, kind="sync_started", source=source, run=run, payload={"mode": mode, "trigger": trigger})
    t0 = time.time()
    stats, errors = Counter(), []
    affected, new_wm = set(), dict(source.watermark or {})
    unmapped = defaultdict(lambda: {"n": 0, "sample": []})
    secrets = {}
    try:
        secrets = decrypt_json(source.secret_enc)
        adapter = build_adapter(source)
        for spec in (source.mapping or {}).get("entities", []):
            cls = spec["class"]
            upd_field = spec.get("updated_at_field")
            since = None if (mode == "full" or not upd_field) else (source.watermark or {}).get(cls)
            complete_scan = since is None and not getattr(adapter, "append_only", False)
            seen, max_ts, batch = set(), None, []

            def flush(batch):
                aff, st = _upsert_batch(source, ontology, spec, batch, run, unmapped)
                affected.update(aff)
                stats.update(st)

            for raw in adapter.fetch(spec, since=since):
                try:
                    t = transform(spec, raw)
                except SkipRecord as e:
                    stats["skipped"] += 1
                    if len(errors) < 10:
                        errors.append(f"{cls}: skipped a record ({e})")
                    continue
                seen.add(t.external_id)
                if t.updated_at and (max_ts is None or t.updated_at > max_ts):
                    max_ts = t.updated_at
                batch.append(t)
                if len(batch) >= BATCH:
                    flush(batch)
                    batch = []
            if batch:
                flush(batch)
            if complete_scan:
                gone = FabricRecord.objects.filter(source=source, class_name=cls, deleted=False).exclude(seen_run=run.id)
                ids = list(gone.values_list("id", flat=True))
                if ids:
                    with transaction.atomic():
                        FabricToken.objects.filter(record_id__in=ids).delete()
                        FabricRecord.objects.filter(id__in=ids).update(deleted=True, deleted_at=_now())
                        FabricEvent.objects.bulk_create([FabricEvent(ontology=ontology, kind="record_deleted", record_id=i, source=source, run=run,
                                                                     payload={"class": cls}) for i in ids])
                    affected.update(ids)
                    stats["deleted"] += len(ids)
            if upd_field and max_ts:
                new_wm[cls] = max_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        # entity resolution of just the touched neighbourhood
        res = resolve(ontology, affected, run=run) if affected else {}
        stats.update({f"resolve_{k}": v for k, v in res.items()})
        ent_ids = set(FabricRecord.objects.filter(id__in=affected, entity__isnull=False).values_list("entity_id", flat=True)) if affected else set()
        if ent_ids:
            stats["suggestions_created"] = suggest_matches(ontology, ent_ids, spec_index(ontology))
        stats["drift_found"] = _record_drift(source, ontology, unmapped)
        status = "partial" if errors else "succeeded"
        run.status, run.error = status, "; ".join(errors)[:1000]
        source.last_status, source.last_error, source.last_synced_at = status, run.error, _now()
        source.watermark = new_wm
        source.sync_count += 1
    except (ConnectorError, SecretError, RuntimeError, ValueError) as e:
        msg = redact(str(e), secrets)[:500]
        run.status, run.error = "failed", msg
        source.last_status, source.last_error = "failed", msg
    except Exception as e:  # noqa: BLE001  never leak internals/credentials; keep the traceback in the server log
        log.exception("fabric sync crashed for source %s", source.id)
        run.status, run.error = "failed", f"Unexpected error ({type(e).__name__}); see the server log."
        source.last_status, source.last_error = "failed", run.error
    finally:
        run.finished_at = _now()
        stats["seconds"] = round(time.time() - t0, 2)
        stats["errorSamples"] = errors
        run.stats = dict(stats)
        run.save()
        source.next_sync_at = _now() + timedelta(minutes=source.sync_interval_minutes) if source.sync_interval_minutes else None
        source.running_since = None
        source.save()
        FabricEvent.objects.create(ontology=ontology, kind="sync_finished", source=source, run=run,
                                   payload={"status": run.status, **{k: v for k, v in stats.items() if isinstance(v, (int, float))}})
        bump(ontology)
        from . import hooks

        hooks.fire("sync_finished", source, run)
    return run


def _upsert_batch(source, ontology, spec, batch, run, unmapped):
    cls = spec["class"]
    now = _now()
    stats, affected = Counter(), set()
    with transaction.atomic():
        existing = {r.external_id: r for r in FabricRecord.objects.filter(source=source, class_name=cls, external_id__in=[t.external_id for t in batch])}
        events, touch, changed_recs, new_recs = [], [], [], []
        for t in batch:
            for k, v in t.unmapped.items():
                u = unmapped[(cls, k)]
                u["n"] += 1
                if len(u["sample"]) < 5:
                    u["sample"].append(v)
            r = existing.get(t.external_id)
            if r is None:
                r = FabricRecord(source=source, ontology=ontology, class_name=cls, external_id=t.external_id, data=t.data, content_hash=t.content_hash,
                                 source_updated_at=t.updated_at, last_seen=now, last_changed_at=now, seen_run=run.id)
                new_recs.append((r, t))
                stats["new"] += 1
            elif r.content_hash != t.content_hash or r.deleted:
                if r.deleted:
                    stats["restored"] += 1
                    events.append(("record_restored", r, {}))
                else:
                    stats["changed"] += 1
                    events.append(("record_changed", r, {"changes": _diff(r.data, t.data)}))
                r.data, r.content_hash, r.source_updated_at, r.last_seen, r.last_changed_at, r.seen_run = t.data, t.content_hash, t.updated_at, now, now, run.id
                r.deleted, r.deleted_at = False, None
                changed_recs.append((r, t))
            else:
                r.last_seen, r.seen_run = now, run.id
                touch.append(r)
                stats["unchanged"] += 1
                if r.entity_id is None:  # never linked (e.g. a previous resolution failed): do it now
                    affected.add(r.id)
        if new_recs:
            FabricRecord.objects.bulk_create([r for r, _ in new_recs], batch_size=BATCH)
            if not new_recs[0][0].pk:  # MySQL does not return primary keys from bulk_create: fetch them in one query
                pks = dict(FabricRecord.objects.filter(source=source, class_name=cls, external_id__in=[r.external_id for r, _ in new_recs])
                           .values_list("external_id", "id"))
                for r, _ in new_recs:
                    r.pk = pks[r.external_id]
        for r, _ in changed_recs:
            r.save()
        if touch:
            FabricRecord.objects.bulk_update(touch, ["last_seen", "seen_run"], batch_size=BATCH)
        pairs = new_recs + changed_recs
        if pairs:
            ids = [r.id for r, _ in pairs]
            FabricToken.objects.filter(record_id__in=ids).delete()
            FabricToken.objects.bulk_create([FabricToken(ontology=ontology, class_name=cls, token=tok, record=r) for r, t in pairs for tok in t.tokens],
                                            batch_size=BATCH)
            _write_refs(source, ontology, pairs)
            affected.update(ids)
            by_ext = {r.external_id: r for r, _ in new_recs}
            for r, t in new_recs:
                events.append(("record_ingested", r, {"class": cls}))
            _resolve_pending_refs(source, ontology, cls, by_ext)
        FabricEvent.objects.bulk_create([FabricEvent(ontology=ontology, kind=k, record=r, source=source, run=run, payload=p) for k, r, p in events])
    return affected, stats


def _write_refs(source, ontology, pairs):
    ids = [r.id for r, _ in pairs]
    FabricRef.objects.filter(from_record_id__in=ids).delete()
    wanted = defaultdict(set)
    for r, t in pairs:
        for prop, tcls, tid, tsrc in t.refs:
            wanted[tcls].add(tid)
    found = defaultdict(list)  # (class, external id) -> [(record id, source id, source name)]
    for tcls, tids in wanted.items():
        for rec in FabricRecord.objects.filter(ontology=ontology, class_name=tcls, external_id__in=list(tids), deleted=False).select_related("source"):
            found[(tcls, rec.external_id)].append((rec.id, rec.source_id, rec.source.name, rec.source.priority))

    def pick(tcls, tid, tsrc):
        cands = found.get((tcls, tid), [])
        if tsrc:
            cands = [c for c in cands if c[2] == tsrc]
        same = [c for c in cands if c[1] == source.id]
        pool = same or cands  # the referencing system's own ids first, then any system that knows this id
        return max(pool, key=lambda c: c[3])[0] if pool else None

    objs = []
    for r, t in pairs:
        for prop, tcls, tid, tsrc in t.refs:
            objs.append(FabricRef(ontology=ontology, from_record=r, to_record_id=pick(tcls, tid, tsrc), property=prop, target_class=tcls,
                                  target_external_id=tid, target_source=tsrc))
    FabricRef.objects.bulk_create(objs, batch_size=BATCH, ignore_conflicts=True)


def _resolve_pending_refs(source, ontology, cls, by_ext):
    """Records that were referenced before they were synced (e.g. an invoice arrived before its customer)."""
    for ext, rec in by_ext.items():
        qs = FabricRef.objects.filter(ontology=ontology, target_class=cls, target_external_id=ext, to_record__isnull=True)
        qs.filter(target_source="").update(to_record=rec)
        qs.filter(target_source=source.name).update(to_record=rec)


def _record_drift(source, ontology, unmapped) -> int:
    n = 0
    explicit = {e["class"]: e for e in (source.mapping or {}).get("entities", []) if e.get("fields")}
    for (cls, field), info in unmapped.items():
        if cls not in explicit:
            continue
        xsd = sql_to_xsd(infer_sql_type(info["sample"]))
        _, created = FabricDrift.objects.get_or_create(source=source, class_name=cls, field=field,
                                                       defaults={"ontology": ontology, "inferred_type": xsd, "sample": info["sample"]})
        n += 1 if created else 0
    return n


def due_sources(now=None):
    now = now or _now()
    return FabricSource.objects.filter(enabled=True, sync_interval_minutes__gt=0).filter(Q(next_sync_at__isnull=True) | Q(next_sync_at__lte=now))
