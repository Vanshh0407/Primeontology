"""Ontology validation (R6): structural/semantic checks + health score."""
import difflib
import re
from collections import defaultdict

from .model_ops import NAME_RE, XSD_TYPES
from .naming import singular, words


def _norm(s):
    ws = words(s)
    if ws:
        ws[-1] = singular(ws[-1])
    return "".join(ws)


def ancestors(model, name, seen=None):
    seen = seen if seen is not None else set()
    c = next((c for c in model["classes"] if c["name"] == name), None)
    for p in (c or {}).get("parents", []):
        if p not in seen:
            seen.add(p)
            ancestors(model, p, seen)
    return seen


def validate(model: dict, mappings: list | None = None) -> dict:
    issues, checks = [], 0

    def issue(sev, code, msg, targets):
        issues.append({"id": len(issues) + 1, "severity": sev, "code": code, "message": msg, "targets": targets})

    classes = model["classes"]
    names = [c["name"] for c in classes]
    nameset = set(names)
    props = [("data", p) for p in model["dataProperties"]] + [("object", p) for p in model["objectProperties"]]

    # class-level checks
    lower = defaultdict(list)
    for c in classes:
        lower[c["name"].lower()].append(c["name"])
    for c in classes:
        checks += 1
        if not NAME_RE.match(c["name"]):
            issue("error", "INVALID_IRI", f"Class '{c['name']}' is not a valid IRI local name.", [c["name"]])
    checks += 1
    for k, v in lower.items():
        if len(v) > 1:
            issue("error", "DUPLICATE_CLASS", f"Class '{v[0]}' is defined {len(v)} times.", [v[0]])
    # near-duplicate concepts
    checks += 1
    seen_pairs = set()
    uniq = sorted(nameset)
    for i, a in enumerate(uniq):
        for b in uniq[i + 1:]:
            na, nb = _norm(a), _norm(b)
            if na == nb or (len(na) > 5 and difflib.SequenceMatcher(None, na, nb).ratio() >= 0.9):
                if (a, b) not in seen_pairs:
                    seen_pairs.add((a, b))
                    issue("warning", "DUPLICATE_CONCEPT", f"'{a}' and '{b}' look like the same concept.", [a, b])
    labels = defaultdict(list)
    for c in classes:
        labels[(c.get("label") or "").strip().lower()].append(c["name"])
    for lab, ns in labels.items():
        if lab and len(set(ns)) > 1:
            issue("warning", "DUPLICATE_LABEL", f"Classes {', '.join(sorted(set(ns)))} share the label '{lab}'.", sorted(set(ns)))

    # hierarchy
    for c in classes:
        checks += 3
        for p in c.get("parents", []):
            if p == c["name"]:
                issue("error", "SELF_SUBCLASS", f"'{c['name']}' is declared a subclass of itself.", [c["name"]])
            elif p not in nameset:
                issue("error", "UNKNOWN_PARENT", f"'{c['name']}' has unknown parent class '{p}'.", [c["name"]])
        if c["name"] in ancestors(model, c["name"]) and c["name"] not in c.get("parents", []):
            issue("error", "CIRCULAR_HIERARCHY", f"'{c['name']}' is its own ancestor (circular inheritance).", [c["name"]])
        for d in c.get("disjointWith", []):
            if d in ancestors(model, c["name"]) or c["name"] in ancestors(model, d):
                issue("error", "DISJOINT_CONFLICT", f"'{c['name']}' is disjoint with its own ancestor/descendant '{d}'.", [c["name"], d])

    # properties
    keys = defaultdict(list)
    for kind, p in props:
        checks += 4
        label = f"{p.get('domain')}.{p['name']}"
        if not NAME_RE.match(p["name"]):
            issue("error", "INVALID_IRI", f"Property '{p['name']}' is not a valid IRI local name.", [label])
        keys[(p.get("domain"), p["name"].lower())].append(label)
        if not p.get("domain"):
            issue("warning", "MISSING_DOMAIN", f"Property '{p['name']}' has no domain.", [p["name"]])
        elif p["domain"] not in nameset:
            issue("error", "INVALID_DOMAIN", f"Domain '{p['domain']}' of '{p['name']}' is not a class.", [label])
        if kind == "object":
            if not p.get("range"):
                issue("warning", "MISSING_RANGE", f"Relationship '{label}' has no range.", [label])
            elif p["range"] not in nameset:
                issue("error", "INVALID_RANGE", f"Range '{p['range']}' of '{label}' is not a class.", [label])
        else:
            if not p.get("datatype"):
                issue("warning", "MISSING_RANGE", f"Property '{label}' has no datatype.", [label])
            elif p["datatype"] not in XSD_TYPES:
                issue("error", "INVALID_RANGE", f"Datatype '{p['datatype']}' of '{label}' is not a known XSD type.", [label])
        mn, mx = p.get("minCardinality"), p.get("maxCardinality")
        if mn not in (None, "") and mx not in (None, "") and int(mn) > int(mx):
            issue("error", "CARDINALITY_CONFLICT", f"'{label}' has minCardinality {mn} > maxCardinality {mx}.", [label])
        if p.get("cardinality") == "many-to-one" and mx not in (None, "") and int(mx) > 1:
            issue("error", "CARDINALITY_CONFLICT", f"'{label}' is many-to-one but maxCardinality is {mx}.", [label])
        if p.get("required") and mx not in (None, "") and int(mx) == 0:
            issue("error", "CARDINALITY_CONFLICT", f"'{label}' is required but maxCardinality is 0.", [label])
    checks += 1
    for k, v in keys.items():
        if len(v) > 1:
            issue("error", "DUPLICATE_PROPERTY", f"Property '{k[1]}' is defined {len(v)} times on '{k[0]}'.", v)

    # incompatible range along inheritance (same relationship name redefined narrower/incompatibly)
    checks += 1
    for p in model["objectProperties"]:
        for q in model["objectProperties"]:
            if p is q or p["name"] != q["name"] or not p.get("domain") or not q.get("domain"):
                continue
            if p["domain"] in ancestors(model, q["domain"]) and p.get("range") and q.get("range"):
                rp, rq = p["range"], q["range"]
                if rp != rq and rp not in ancestors(model, rq) and rq not in ancestors(model, rp):
                    issue("warning", "INCOMPATIBLE_RANGE",
                          f"'{q['domain']}.{q['name']}' → {rq} is incompatible with inherited '{p['domain']}.{p['name']}' → {rp}.",
                          [f"{q['domain']}.{q['name']}", f"{p['domain']}.{p['name']}"])

    # orphan classes
    connected = set()
    for p in model["objectProperties"]:
        connected.update([p.get("domain"), p.get("range")])
    for c in classes:
        connected.update(c.get("parents", []))
        if c.get("parents"):
            connected.add(c["name"])
    for c in classes:
        checks += 1
        if c["name"] not in connected:
            issue("warning", "ORPHAN_CLASS", f"'{c['name']}' has no relationship to any other class.", [c["name"]])

    # mapping conflicts
    if mappings:
        tgt = defaultdict(list)
        for m in mappings:
            if m.get("status") in ("accepted", "overridden"):
                checks += 1
                t = m.get("target") or ""
                tgt[(m.get("source"), m.get("field"))].append(t)
                cls = t.split(".")[0]
                if cls and cls not in nameset:
                    issue("error", "MAPPING_TARGET_MISSING", f"Mapping {m.get('source')}.{m.get('field')} → {t}: target does not exist.", [t])
        for k, ts in tgt.items():
            if len(set(ts)) > 1:
                issue("error", "MAPPING_CONFLICT", f"{k[0]}.{k[1]} is mapped to several targets: {', '.join(sorted(set(ts)))}.", sorted(set(ts)))

    errors = sum(1 for i in issues if i["severity"] == "error")
    warns = sum(1 for i in issues if i["severity"] == "warning")
    score = round(max(0.0, 100.0 * (1 - (errors + 0.35 * warns) / max(checks, 1))))
    if errors:
        score = min(score, 95 - 5 * min(errors, 15))
    score = max(0, score)
    return {"health": score, "errors": errors, "warnings": warns, "passed": max(0, checks - errors - warns),
            "checks": checks, "issues": issues,
            "counts": {"classes": len(classes), "dataProperties": len(model["dataProperties"]),
                       "objectProperties": len(model["objectProperties"]),
                       "relationships": len(model["objectProperties"]) + sum(len(c.get("parents", [])) for c in classes)}}
