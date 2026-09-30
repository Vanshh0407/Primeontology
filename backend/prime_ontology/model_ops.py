"""Pure functions over the ontology model dict."""
import copy
import re

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")
XSD_TYPES = {"string", "integer", "int", "long", "short", "decimal", "float", "double", "boolean", "date", "dateTime",
             "time", "base64Binary", "anyURI", "nonNegativeInteger", "positiveInteger", "byte", "gYear", "duration"}


def empty_model() -> dict:
    return {"classes": [], "dataProperties": [], "objectProperties": []}


def check_shape(model) -> None:
    """Structural check for API input. Semantic problems are reported by validation.py, not rejected."""
    if not isinstance(model, dict):
        raise ValueError("model must be an object.")
    for k in ("classes", "dataProperties", "objectProperties"):
        if not isinstance(model.get(k), list):
            raise ValueError(f"model.{k} must be a list.")
    for c in model["classes"]:
        if not isinstance(c, dict) or not c.get("name"):
            raise ValueError("Every class needs a name.")
    for p in model["dataProperties"] + model["objectProperties"]:
        if not isinstance(p, dict) or not p.get("name"):
            raise ValueError("Every property needs a name.")
    if len(str(model)) > 20_000_000:
        raise ValueError("model too large.")


def find_class(model, name):
    return next((c for c in model["classes"] if c["name"] == name), None)


def merge_models(base: dict, cand: dict) -> tuple[dict, dict]:
    """Merge a candidate into an existing ontology (case-insensitive by name).
    Existing definitions win; parents/evidence/sources accumulate."""
    out = copy.deepcopy(base)
    out.setdefault("classes", [])
    out.setdefault("dataProperties", [])
    out.setdefault("objectProperties", [])
    rep = {"classesAdded": 0, "classesMerged": 0, "propertiesAdded": 0, "propertiesMerged": 0}
    by_lower = {c["name"].lower(): c for c in out["classes"]}
    for c in cand["classes"]:
        ex = by_lower.get(c["name"].lower())
        if ex:
            rep["classesMerged"] += 1
            ex["parents"] = sorted(set(ex.get("parents", [])) | set(c.get("parents", [])))
            ex["evidence"] = (ex.get("evidence", []) + c.get("evidence", []))[:20]
            srcs = ex.setdefault("sources", [ex["source"]] if ex.get("source") else [])
            if c.get("source") and c["source"] not in srcs:
                srcs.append(c["source"])
            if not ex.get("comment"):
                ex["comment"] = c.get("comment", "")
        else:
            rep["classesAdded"] += 1
            out["classes"].append(copy.deepcopy(c))
            by_lower[c["name"].lower()] = out["classes"][-1]
    canon = {c["name"].lower(): c["name"] for c in out["classes"]}
    for key in ("dataProperties", "objectProperties"):
        idx = {(p["domain"].lower(), p["name"].lower()): p for p in out[key]}
        for p in cand[key]:
            p = copy.deepcopy(p)
            p["domain"] = canon.get((p.get("domain") or "").lower(), p.get("domain"))
            if key == "objectProperties":
                p["range"] = canon.get((p.get("range") or "").lower(), p.get("range"))
            ex = idx.get((str(p["domain"]).lower(), p["name"].lower()))
            if ex:
                rep["propertiesMerged"] += 1
                srcs = ex.setdefault("sources", [ex["source"]] if ex.get("source") else [])
                if p.get("source") and p["source"] not in srcs:
                    srcs.append(p["source"])
            else:
                rep["propertiesAdded"] += 1
                out[key].append(p)
                idx[(str(p["domain"]).lower(), p["name"].lower())] = p
    return out, rep


def rename_class(model: dict, old: str, new: str) -> dict:
    """Rename a class and cascade to parents, domains, ranges, individuals."""
    m = copy.deepcopy(model)
    if not NAME_RE.match(new):
        raise ValueError(f"'{new}' is not a valid name.")
    if any(c["name"] == new for c in m["classes"]):
        raise ValueError(f"Class '{new}' already exists.")
    for c in m["classes"]:
        if c["name"] == old:
            if c.get("label") in (None, "", old):
                c["label"] = new
            c["name"] = new
        for key in ("parents", "equivalentTo", "disjointWith"):
            if key in c:
                c[key] = [new if x == old else x for x in c[key]]
    for p in m["dataProperties"] + m["objectProperties"]:
        if p.get("domain") == old:
            p["domain"] = new
        if p.get("range") == old:
            p["range"] = new
    for i in m.get("individuals", []):
        if i.get("class") == old:
            i["class"] = new
    return m


def delete_class(model: dict, name: str) -> dict:
    m = copy.deepcopy(model)
    m["classes"] = [c for c in m["classes"] if c["name"] != name]
    for c in m["classes"]:
        c["parents"] = [p for p in c.get("parents", []) if p != name]
    m["dataProperties"] = [p for p in m["dataProperties"] if p["domain"] != name]
    m["objectProperties"] = [p for p in m["objectProperties"] if p["domain"] != name and p["range"] != name]
    m["individuals"] = [i for i in m.get("individuals", []) if i.get("class") != name]
    return m
