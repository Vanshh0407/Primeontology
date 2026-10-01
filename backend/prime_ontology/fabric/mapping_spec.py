"""Source -> ontology mapping specification.

{
  "entities": [{
      "class": "Customer",                      # ontology class the records become
      "table" | "model" | "sobject" | "entity_set" | "path": "...",   # which object in the source (kind specific)
      "id": "customer_id" | ["a", "b"],         # source id field(s)
      "fields": {"cust_nm": "customerName"},    # source field -> ontology property (omit: all scalar fields, camelCased)
      "columns": [...],                         # optional: restrict the fetched fields
      "updated_at_field": "write_date",         # enables incremental sync
      "identity": [{"keys": ["email"], "normalize": "email"}],   # deterministic matching rules (property or source field names)
      "relations": [{"field": "customer_id", "target": "Customer", "property": "hasCustomer"}],
      "survivorship": {"email": "most_recent", "customerName": "source_priority"},
      "fuzzy": {"fields": ["customerName"], "threshold": 0.92, "corroborate": ["city"]},
      "display": "customerName"
  }]
}
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..model_ops import NAME_RE
from ..naming import to_property_name
from ..records import json_value
from .adapters import ADAPTERS, dig
from .normalize import NORMALIZERS, normalize

STRATEGIES = {"source_priority", "most_recent", "longest", "first_non_null"}


class MappingError(ValueError):
    pass


class SkipRecord(Exception):
    pass


def _class_names(model):
    return {c["name"] for c in model["classes"]}


def _properties(model, cls):
    from ..validation import ancestors

    scope = {cls} | ancestors(model, cls)
    return {p["name"] for p in model["dataProperties"] + model["objectProperties"] if p.get("domain") in scope}


def validate_mapping(mapping, kind: str, model: dict) -> tuple[dict, list[str]]:
    """Returns (clean mapping, warnings). Raises MappingError listing every problem."""
    errors, warnings = [], []
    if not isinstance(mapping, dict) or not isinstance(mapping.get("entities"), list) or not mapping["entities"]:
        raise MappingError("mapping.entities must be a non-empty list.")
    classes = _class_names(model)
    hint = ADAPTERS[kind].spec_hint if kind in ADAPTERS else ""
    clean = {"entities": []}
    for i, e in enumerate(mapping["entities"]):
        where = f"entities[{i}]"
        if not isinstance(e, dict):
            errors.append(f"{where} must be an object.")
            continue
        cls = e.get("class")
        if cls not in classes:
            errors.append(f"{where}: class '{cls}' does not exist in the ontology.")
            continue
        if hint and not e.get(hint):
            errors.append(f"{where}: '{hint}' is required for this source kind.")
        if kind != "file" and not e.get("id"):
            errors.append(f"{where}: 'id' (the source's identifier field) is required.")
        props = _properties(model, cls)
        fields = e.get("fields")
        if fields is not None:
            if not isinstance(fields, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in fields.items()):
                errors.append(f"{where}.fields must map source field -> property name.")
            else:
                for _, p in fields.items():
                    if not NAME_RE.match(p):
                        errors.append(f"{where}: '{p}' is not a valid property name.")
                    elif p not in props:
                        warnings.append(f"{where}: property '{p}' is not defined on {cls}; it will be stored but not typed.")
        for j, rule in enumerate(e.get("identity") or []):
            if not isinstance(rule, dict) or not rule.get("keys") or not isinstance(rule["keys"], list):
                errors.append(f"{where}.identity[{j}] needs a 'keys' list.")
            elif rule.get("normalize", "text") not in NORMALIZERS:
                errors.append(f"{where}.identity[{j}]: unknown normalize '{rule.get('normalize')}' (use {', '.join(NORMALIZERS)}).")
        for j, rel in enumerate(e.get("relations") or []):
            if not isinstance(rel, dict) or not all(rel.get(k) for k in ("field", "target", "property")):
                errors.append(f"{where}.relations[{j}] needs field, target and property.")
            elif rel["target"] not in classes:
                errors.append(f"{where}.relations[{j}]: target class '{rel['target']}' does not exist.")
        for p, s in (e.get("survivorship") or {}).items():
            if s not in STRATEGIES and not str(s).startswith("source:"):
                errors.append(f"{where}.survivorship.{p}: unknown strategy '{s}' (use {', '.join(sorted(STRATEGIES))} or source:<name>).")
        fz = e.get("fuzzy")
        if fz is not None and (not isinstance(fz, dict) or not fz.get("fields") or not 0.5 <= float(fz.get("threshold", 0.92)) <= 1):
            errors.append(f"{where}.fuzzy needs fields and a threshold between 0.5 and 1.")
        clean["entities"].append(e)
    if errors:
        raise MappingError("; ".join(errors))
    return clean, warnings


def parse_time(v):
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc)
    s = str(v).strip()
    m = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d+)?\)/", s)
    if m:
        return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc)
    for f in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:26] if "%f" in f else s, f).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@dataclass
class Transformed:
    external_id: str
    data: dict
    refs: list = field(default_factory=list)  # (property, target_class, target_external_id, target_source or "")
    updated_at: datetime | None = None
    tokens: list = field(default_factory=list)
    content_hash: str = ""
    unmapped: dict = field(default_factory=dict)  # fields seen in the source but not mapped (schema drift sampling)


def _scalar(v):
    if isinstance(v, (list, tuple)) and v and not isinstance(v[0], (dict, list)):
        return v[0]  # Odoo many2one: [id, "display name"]
    return v


def transform(spec: dict, raw: dict) -> Transformed:
    ids = spec.get("id")
    id_fields = [ids] if isinstance(ids, str) else list(ids or [])
    vals = [_scalar(dig(raw, f)) if "." in f else _scalar(raw.get(f)) for f in id_fields]
    if not id_fields:  # file sources may rely on row order
        raise SkipRecord("no id field configured")
    if any(v in (None, "") for v in vals):
        raise SkipRecord("missing id")
    ext = "|".join(str(json_value(v)) for v in vals)
    fields = spec.get("fields")
    data, unmapped = {}, {}
    if fields:
        for src, prop in fields.items():
            v = _scalar(dig(raw, src) if "." in src else raw.get(src))
            if v is not None and not isinstance(v, (dict, list)):
                data[prop] = json_value(v)
        # fields that are used for something else (id, change tracking, references, identity rules) are not "unmapped" data
        mapped_src = set(fields) | set(id_fields) | ({spec['updated_at_field']} if spec.get('updated_at_field') else set()) \
            | {r['field'] for r in spec.get('relations') or []} | {k for rule in spec.get('identity') or [] for k in rule.get('keys', [])}
        for k, v in raw.items():
            if k not in mapped_src and v is not None and not isinstance(v, (dict, list)) and not str(k).startswith("__"):
                unmapped[k] = json_value(v)
    else:
        for k, v in raw.items():
            if v is not None and not isinstance(v, (dict, list)) and not str(k).startswith("__"):
                data[to_property_name(str(k))] = json_value(v)
    refs = []
    for rel in spec.get("relations") or []:
        rv = _scalar(dig(raw, rel["field"]) if "." in rel["field"] else raw.get(rel["field"]))
        if rv not in (None, ""):
            refs.append((rel["property"], rel["target"], str(json_value(rv)), rel.get("target_source") or ""))
    tokens = []
    for rule in spec.get("identity") or []:
        parts = []
        for key in rule["keys"]:
            v = data.get(key, raw.get(key))
            n = normalize(rule.get("normalize", "text"), v)
            if not n:
                parts = []
                break
            parts.append(n)
        if parts:
            tokens.append(f"{'+'.join(rule['keys'])}:{'|'.join(parts)}"[:190])
    ts = parse_time(raw.get(spec["updated_at_field"])) if spec.get("updated_at_field") else None
    h = hashlib.sha1(json.dumps([data, sorted(refs)], sort_keys=True, default=str).encode()).hexdigest()
    return Transformed(ext, data, refs, ts, sorted(set(tokens)), h, unmapped)
