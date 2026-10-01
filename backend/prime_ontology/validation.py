"""Ontology validation (R6): structural/semantic checks + health score."""
import difflib
import re
from collections import defaultdict

from .model_ops import NAME_RE, XSD_TYPES
from .naming import singular, words


FUZZY_BUDGET = 60_000  # max fuzzy name comparisons per validation run
_DIGITS = re.compile(r"\d+")


def _skeleton(norm_name: str) -> str:
    """Name with digit runs collapsed: Address1 / Address10 differ only by numbering and are distinct concepts, not typos."""
    return _DIGITS.sub("#", norm_name)


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


_NUMERIC_INT = {"integer", "int", "long", "short", "nonNegativeInteger", "positiveInteger", "byte"}
_NUMERIC = _NUMERIC_INT | {"decimal", "float", "double"}


def _datatype_ok(dt, v):
    if v is None:
        return True
    if dt == "boolean":
        return isinstance(v, bool) or str(v).lower() in ("true", "false", "0", "1")
    if dt in _NUMERIC_INT:
        try:
            return float(v) == int(float(v)) and not isinstance(v, bool)
        except (TypeError, ValueError):
            return False
    if dt in _NUMERIC:
        try:
            float(v)
            return not isinstance(v, bool)
        except (TypeError, ValueError):
            return False
    if dt in ("date", "dateTime"):
        return bool(re.match(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$", str(v)))
    return True


def _individual_issues(model):
    """Checks on instance data. Returns (number_of_checks, [(severity, code, message, targets)])."""
    inds = model.get("individuals") or []
    if not inds:
        return 0, []
    out, n = [], 0
    classes = {c["name"] for c in model["classes"]}
    seen = {}
    for i in inds:
        seen.setdefault(i.get("name"), []).append(i)
    for name, lst in seen.items():
        n += 1
        if len(lst) > 1:
            out.append(("error", "DUPLICATE_INDIVIDUAL", f"Individual '{name}' is defined {len(lst)} times.", [str(name)]))
    for ind in inds:
        n += 3
        name, cls = ind.get("name"), ind.get("class")
        if not name or not NAME_RE.match(str(name)):
            out.append(("error", "INVALID_IRI", f"Individual '{name}' is not a valid IRI local name.", [str(name)]))
        if cls not in classes:
            out.append(("error", "UNKNOWN_CLASS", f"Individual '{name}' is an instance of unknown class '{cls}'.", [str(name)]))
            continue
        scope = {cls} | ancestors(model, cls)
        dprops = {p["name"]: p for p in model["dataProperties"] if p["domain"] in scope}
        oprops = {p["name"]: p for p in model["objectProperties"] if p["domain"] in scope}
        for k, v in (ind.get("data") or {}).items():
            n += 1
            if k not in dprops:
                out.append(("warning", "UNKNOWN_PROPERTY", f"{name}: '{k}' is not a data property of {cls}.", [str(name)]))
            elif not _datatype_ok(dprops[k].get("datatype"), v):
                out.append(("error", "DATATYPE_MISMATCH", f"{name}.{k} = {v!r} is not a valid {dprops[k].get('datatype')}.", [str(name)]))
        for k, p in dprops.items():
            n += 1
            if p.get("required") and (ind.get("data") or {}).get(k) in (None, ""):
                out.append(("warning", "MISSING_REQUIRED", f"{name} has no value for required property '{k}'.", [str(name)]))
        for k, targets in (ind.get("links") or {}).items():
            for t in (targets if isinstance(targets, list) else [targets]):
                n += 1
                tgt = seen.get(t, [None])[0]
                if k not in oprops:
                    out.append(("warning", "UNKNOWN_PROPERTY", f"{name}: '{k}' is not a relationship of {cls}.", [str(name)]))
                elif tgt is None:
                    out.append(("error", "BROKEN_LINK", f"{name} —{k}→ '{t}': no such individual.", [str(name)]))
                else:
                    rng = oprops[k].get("range")
                    if tgt.get("class") != rng and rng not in ancestors(model, tgt.get("class")):
                        out.append(("error", "WRONG_LINK_TYPE", f"{name} —{k}→ {t}: {t} is a {tgt.get('class')}, but the range is {rng}.", [str(name)]))
    return n, out


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
    # near-duplicate concepts. Exact normalised duplicates are found with a hash lookup; fuzzy ones are only compared inside
    # blocks (same first letter, similar length) and within a fixed comparison budget, so cost stays bounded for huge ontologies.
    checks += 1
    norms = {n: _norm(n) for n in sorted(nameset)}
    by_norm = defaultdict(list)
    for n, na in norms.items():
        by_norm[na].append(n)
    for ns in by_norm.values():
        for i, a in enumerate(ns):
            for b in ns[i + 1:]:
                issue("warning", "DUPLICATE_CONCEPT", f"'{a}' and '{b}' look like the same concept.", [a, b])
    blocks = defaultdict(list)
    for n, na in norms.items():
        if len(na) > 5:
            blocks[na[:1]].append(n)
    budget, compared, truncated = FUZZY_BUDGET, 0, False
    for ns in blocks.values():
        ns.sort(key=lambda n: len(norms[n]))
        for i, a in enumerate(ns):
            na = norms[a]
            for b in ns[i + 1:]:
                nb = norms[b]
                if len(nb) - len(na) > max(1, 0.15 * len(nb)):
                    break  # sorted by length: nothing further can be similar enough
                if na == nb or _skeleton(na) == _skeleton(nb):
                    continue
                compared += 1
                if compared > budget:
                    truncated = True
                    break
                if difflib.SequenceMatcher(None, na, nb).ratio() >= 0.9:
                    issue("warning", "DUPLICATE_CONCEPT", f"'{a}' and '{b}' look like the same concept.", [a, b])
            if truncated:
                break
        if truncated:
            break
    if truncated:
        issue("info", "CHECK_TRUNCATED", f"The near-duplicate-name check stopped after {FUZZY_BUDGET:,} comparisons because this ontology is very large; "
              "exact duplicates were still checked everywhere.", [])
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
    same_name = defaultdict(list)
    for p in model["objectProperties"]:
        if p.get("domain"):
            same_name[p["name"]].append(p)
    anc_cache = {}
    anc = lambda c: anc_cache.setdefault(c, ancestors(model, c))
    for group in same_name.values():
        if len(group) < 2:
            continue  # only relationships that share a name can conflict: skips the quadratic scan
        for p in group:
            for q in group:
                if p is q or not (p["domain"] in anc(q["domain"]) and p.get("range") and q.get("range")):
                    continue
                rp, rq = p["range"], q["range"]
                if rp != rq and rp not in anc(rq) and rq not in anc(rp):
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

    ic, iissues = _individual_issues(model)
    checks += ic
    for sev, code, msg, targets in iissues:
        issue(sev, code, msg, targets)

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
