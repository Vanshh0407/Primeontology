"""Source / entity freshness: FRESH | STALE | VERY_STALE | NEVER_SYNCED.

age <= SLA            -> FRESH
age <= 4 x SLA        -> STALE
older                 -> VERY_STALE
never synced          -> NEVER_SYNCED
An entity is as fresh as its LEAST fresh contributing source.
"""
from datetime import datetime, timezone

ORDER = {"FRESH": 0, "STALE": 1, "VERY_STALE": 2, "NEVER_SYNCED": 3}


def classify(last_synced_at, sla_minutes: int, now=None) -> str:
    if last_synced_at is None:
        return "NEVER_SYNCED"
    now = now or datetime.now(timezone.utc)
    age_min = (now - last_synced_at).total_seconds() / 60
    sla = max(1, int(sla_minutes or 1440))
    return "FRESH" if age_min <= sla else "STALE" if age_min <= 4 * sla else "VERY_STALE"


def source_freshness(source, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    status = classify(source.last_synced_at, source.freshness_sla_minutes, now)
    return {"status": status, "lastSyncedAt": source.last_synced_at.isoformat() if source.last_synced_at else None,
            "ageMinutes": None if source.last_synced_at is None else round((now - source.last_synced_at).total_seconds() / 60, 1),
            "slaMinutes": source.freshness_sla_minutes, "lastStatus": source.last_status, "lastError": source.last_error[:300]}


def worst(statuses) -> str:
    statuses = list(statuses)
    return max(statuses, key=lambda s: ORDER[s]) if statuses else "NEVER_SYNCED"
