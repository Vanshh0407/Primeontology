"""Sample records -> ontology individuals.

Opt-in: row data from a source is copied INTO the ontology (visible to everyone with access), so it is limited
(default 50 rows per table, hard cap 500) and columns that look sensitive (passwords, tokens, card numbers, ...) are never copied.
Individuals let natural-language / SPARQL queries return real rows and make SHACL / reasoning meaningful.
"""
import datetime
import decimal
import re

from .model_ops import NAME_RE
from .naming import to_class_name, to_property_name

MAX_ROWS_HARD = 500
DEFAULT_ROWS = 50
MAX_INDIVIDUALS = 5000
SENSITIVE = re.compile(r"pass(word|wd)?|pwd|secret|token|ssn|social_?sec|credit|card_?n|cvv|cvc|iban|api_?key|private|salt|hash|otp|auth", re.I)


def clamp_rows(n) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return 0
    return max(0, min(n, MAX_ROWS_HARD))


def json_value(v):
    """DB/file value -> JSON-safe, model-friendly scalar."""
    if v is None or isinstance(v, (bool, int)):
        return v
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else None
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return None  # binary content is never copied
    return str(v)


def _slug(s) -> str:
    out = re.sub(r"[^A-Za-z0-9_-]+", "_", str(s)).strip("_")
    return out or "x"


def attach_individuals(model: dict, schema: dict, max_rows: int = DEFAULT_ROWS) -> dict:
    """Return (model with `individuals`, report). `schema` tables must carry `sample: {columns, rows}`."""
    max_rows = clamp_rows(max_rows) or DEFAULT_ROWS
    tables = {t["name"]: t for t in schema["tables"] if t.get("sample")}
    cls_of = {c["source"].get("table"): c["name"] for c in model["classes"] if c.get("source", {}).get("table")}
    prop_of = {}  # (table, column) -> data property name ; (table, "fk:a,b") -> object property name
    for p in model["dataProperties"]:
        s = p.get("source", {})
        prop_of[(s.get("table"), s.get("column"))] = p["name"]
    for p in model["objectProperties"]:
        s = p.get("source", {})
        if s.get("viaJunction"):
            prop_of[(s["viaJunction"], p["domain"], p["range"], p["name"])] = p["name"]
        else:
            prop_of[(s.get("table"), "fk:" + str(s.get("column")))] = p["name"]

    individuals, key_to_name, skipped_sensitive, orphans, truncated = [], {}, set(), 0, []
    used_names = set()

    def unique(base):
        n, i = base, 2
        while n in used_names or not NAME_RE.match(n):
            n = f"{base}_{i}"
            i += 1
        used_names.add(n)
        return n

    # pass 1: individuals + data values
    for tname, cname in cls_of.items():
        t = tables.get(tname)
        if not t:
            continue
        cols = t["sample"]["columns"]
        rows = t["sample"]["rows"][:max_rows]
        if len(t["sample"]["rows"]) > max_rows:
            truncated.append(tname)
        pk = t.get("primary_key") or []
        fk_cols = {c for fk in t["foreign_keys"] for c in fk["columns"]}
        for i, row in enumerate(rows):
            rec = dict(zip(cols, row))
            keyval = tuple(json_value(rec.get(c)) for c in pk) if pk else None
            ident = _slug("_".join(str(x) for x in keyval)) if keyval and all(x is not None for x in keyval) else str(i + 1)
            name = unique(f"{_slug(cname.lower())}_{ident}")
            data = {}
            for col, val in rec.items():
                if col in fk_cols:
                    continue
                if SENSITIVE.search(col):
                    skipped_sensitive.add(f"{tname}.{col}")
                    continue
                pname = prop_of.get((tname, col))
                v = json_value(val)
                if pname and v is not None:
                    data[pname] = v
            individuals.append({"name": name, "class": cname, "label": name, "data": data, "links": {},
                                "source": {"table": tname, "row": i + 1}})
            if keyval and all(x is not None for x in keyval):
                key_to_name[(tname, keyval)] = name
            key_to_name[(tname, "row", i)] = name
    # pass 2: links via foreign keys
    by_name = {ind["name"]: ind for ind in individuals}
    for tname, cname in cls_of.items():
        t = tables.get(tname)
        if not t:
            continue
        cols = t["sample"]["columns"]
        for i, row in enumerate(t["sample"]["rows"][:max_rows]):
            me = by_name.get(key_to_name.get((tname, "row", i)))
            if not me:
                continue
            rec = dict(zip(cols, row))
            for fk in t["foreign_keys"]:
                pname = prop_of.get((tname, "fk:" + ",".join(fk["columns"])))
                vals = tuple(json_value(rec.get(c)) for c in fk["columns"])
                if not pname or any(v is None for v in vals):
                    continue
                target = key_to_name.get((fk["ref_table"], vals))
                if target:
                    me["links"].setdefault(pname, []).append(target)
                else:
                    orphans += 1  # referenced row is outside the sampled window
    # many-to-many junction tables
    for tname, t in tables.items():
        if tname in cls_of:
            continue
        fks = [fk for fk in t["foreign_keys"] if fk["ref_table"] in cls_of]
        cols = t["sample"]["columns"]
        for row in t["sample"]["rows"][:max_rows]:
            rec = dict(zip(cols, row))
            for ai, a in enumerate(fks):
                for b in fks[ai + 1:]:
                    na = key_to_name.get((a["ref_table"], tuple(json_value(rec.get(c)) for c in a["columns"])))
                    nb = key_to_name.get((b["ref_table"], tuple(json_value(rec.get(c)) for c in b["columns"])))
                    pname = next((p["name"] for p in model["objectProperties"] if p.get("source", {}).get("viaJunction") == tname
                                  and p["domain"] == cls_of[a["ref_table"]] and p["range"] == cls_of[b["ref_table"]]), None)
                    if na and nb and pname:
                        by_name[na]["links"].setdefault(pname, []).append(nb)
    individuals = individuals[:MAX_INDIVIDUALS]
    m = dict(model)
    m["individuals"] = individuals
    report = {"individuals": len(individuals), "maxRowsPerTable": max_rows, "truncatedTables": truncated,
              "skippedSensitiveColumns": sorted(skipped_sensitive), "linksOutsideSample": orphans}
    return m, report
