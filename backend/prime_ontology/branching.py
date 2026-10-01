"""Ontology branching & three-way merge.

A branch is a complete ontology (own working copy, versions and review workflow) linked to its root, plus the model it
was forked from (`base_snapshot`). Merging uses a three-way merge at element level (class / data property / relationship /
individual) with field-level merging inside an element: a change made on only one side is taken; the same change on both
sides is taken once; different changes to the SAME field of the same element — or editing vs deleting — is a conflict that
a human must resolve (`ours` | `theirs`). Nothing is applied while conflicts are unresolved.
"""
import copy
import json

_FIELD_LISTS = {"classes": "class", "dataProperties": "dp", "objectProperties": "op", "individuals": "ind"}
_MISSING = object()
_VOLATILE = {"evidence", "confidence", "occurrences", "mappedFrom"}  # provenance noise never causes a conflict by itself


def _ekey(kind, e):
    if kind == "class":
        return (kind, e["name"])
    if kind == "ind":
        return (kind, e["name"])
    return (kind, e.get("domain"), e["name"])


def entities(model: dict) -> dict:
    out = {}
    for field, kind in _FIELD_LISTS.items():
        for e in model.get(field, []):
            out[_ekey(kind, e)] = e
    return out


def _norm(v):
    return json.dumps(v, sort_keys=True, default=str)


def _same(a, b):
    return _norm(a) == _norm(b)


def _label(key):
    kind = {"class": "Class", "dp": "Data property", "op": "Relationship", "ind": "Individual"}[key[0]]
    return f"{kind} {'.'.join(str(x) for x in key[1:])}"


def _cid(key, field=""):
    return ":".join([key[0], *[str(x) for x in key[1:]], field])


def three_way(base: dict, ours: dict, theirs: dict, resolutions: dict | None = None) -> dict:
    """Return {"model": merged | None, "conflicts": [...unresolved...], "resolvedConflicts": n, "changes": n}."""
    resolutions = resolutions or {}
    eb, eo, et = entities(base), entities(ours), entities(theirs)
    merged: dict = {}
    conflicts = []
    resolved = 0

    def pick(cid, o, t):
        nonlocal resolved
        choice = resolutions.get(cid)
        if choice == "ours":
            resolved += 1
            return o, True
        if choice == "theirs":
            resolved += 1
            return t, True
        return None, False

    for key in list(dict.fromkeys([*eo, *et, *eb])):
        b, o, t = eb.get(key), eo.get(key), et.get(key)
        if _same(o, t):
            res = o
        elif _same(o, b):
            res = t
        elif _same(t, b):
            res = o
        elif isinstance(o, dict) and isinstance(t, dict):  # both edited the same element: merge field by field
            res = {}
            for f in dict.fromkeys([*o, *t, *(b or {})]):
                bf, of, tf = (b or {}).get(f, _MISSING), o.get(f, _MISSING), t.get(f, _MISSING)
                if _same(of, tf):
                    val = of
                elif _same(of, bf):  # only theirs changed it
                    val = tf
                elif _same(tf, bf):  # only ours changed it
                    val = of
                elif f in _VOLATILE:  # provenance noise: ours wins, never a conflict
                    val = of
                else:
                    cid = _cid(key, f)
                    choice, ok = pick(cid, of, tf)
                    if not ok:
                        conflicts.append({"id": cid, "element": _label(key), "kind": "field", "field": f,
                                          "base": None if bf is _MISSING else bf, "ours": None if of is _MISSING else of,
                                          "theirs": None if tf is _MISSING else tf})
                        continue
                    val = choice
                if val is not _MISSING:
                    res[f] = val
        else:  # one side edited, the other deleted
            cid = _cid(key)
            choice, ok = pick(cid, o, t)
            if ok:
                res = choice
            else:
                conflicts.append({"id": cid, "element": _label(key), "kind": "edit-vs-delete",
                                  "base": None if b is None else "exists", "ours": "deleted" if o is None else o, "theirs": "deleted" if t is None else t})
                res = o
        if res is not None:
            merged[key] = res

    if conflicts:
        return {"model": None, "conflicts": conflicts, "resolvedConflicts": resolved, "changes": 0}

    out = {k: copy.deepcopy(v) for k, v in ours.items() if k not in _FIELD_LISTS}
    for k, v in theirs.items():  # model-level extras (agentic registry, layout): theirs fills what ours lacks / is unchanged from base
        if k in _FIELD_LISTS:
            continue
        if k not in out or _same(out[k], base.get(k)):
            out[k] = copy.deepcopy(v)
        elif k == "layout" and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = {**v, **out[k]}
    for field, kind in _FIELD_LISTS.items():
        order = [_ekey(kind, e) for e in ours.get(field, [])] + [_ekey(kind, e) for e in theirs.get(field, [])]
        out[field] = [copy.deepcopy(merged[k]) for k in dict.fromkeys(order) if k in merged]
    # references to deleted classes must not survive silently
    names = {c["name"] for c in out["classes"]}
    for c in out["classes"]:
        c["parents"] = [p for p in c.get("parents", []) if p in names]
    changes = sum(1 for k in merged if not _same(merged[k], eo.get(k))) + sum(1 for k in eo if k not in merged)
    return {"model": out, "conflicts": [], "resolvedConflicts": resolved, "changes": changes}
