"""Tiny in-process event hooks, so later modules (digital twin, RAG index) can follow fabric changes without the fabric knowing them.
A failing handler is logged and swallowed: it must never break a synchronisation."""
import logging
from collections import defaultdict

log = logging.getLogger("prime_ontology.fabric")
_handlers = defaultdict(list)


def register(event: str, fn):
    if fn not in _handlers[event]:
        _handlers[event].append(fn)
    return fn


def clear(event: str | None = None):  # used by tests
    if event:
        _handlers.pop(event, None)
    else:
        _handlers.clear()


def fire(event: str, *args, **kw):
    for fn in list(_handlers.get(event, [])):
        try:
            fn(*args, **kw)
        except Exception:  # noqa: BLE001
            log.exception("fabric hook %s failed", getattr(fn, "__name__", fn))
