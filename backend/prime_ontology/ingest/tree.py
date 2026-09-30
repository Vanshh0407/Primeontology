"""JSON / YAML / XML -> tables. Arrays of objects become tables; nested
objects become child tables linked by foreign keys."""
import json
import re
import xml.etree.ElementTree as ET

from .common import IngestError, infer_sql_type, make_table, python_value_type, schema_result
from .tabular import _decode, _stem


def _is_scalar(v):
    return not isinstance(v, (dict, list))


def _xml_to_obj(el: ET.Element):
    tag = lambda t: re.sub(r"^\{.*\}", "", t)
    children = list(el)
    if not children and not el.attrib:
        return (el.text or "").strip()
    obj = {f"@{k}": v for k, v in el.attrib.items()}
    for ch in children:
        val = _xml_to_obj(ch)
        key = tag(ch.tag)
        if key in obj:
            if not isinstance(obj[key], list):
                obj[key] = [obj[key]]
            obj[key].append(val)
        else:
            obj[key] = val
    if not children and (el.text or "").strip():
        obj["value"] = el.text.strip()
    return obj


class _Flattener:
    def __init__(self):
        self.tables: dict[str, dict] = {}  # name -> {"cols": {name: [values]}, "fks": [...], "pk": ...}

    def table(self, name):
        return self.tables.setdefault(name, {"cols": {}, "fks": [], "count": 0})

    def visit_records(self, name, records, parent=None):
        """records: list of dicts destined for table `name`."""
        t = self.table(name)
        for rec in records:
            t["count"] += 1
            if not isinstance(rec, dict):
                t["cols"].setdefault("value", []).append(rec)
                continue
            for k, v in rec.items():
                k = k.lstrip("@")
                if _is_scalar(v):
                    t["cols"].setdefault(k, []).append(v)
                elif isinstance(v, dict):
                    child = f"{name}_{k}" if k in self.tables and k != name else k
                    self.visit_records(child, [v], parent=None)
                    col = f"{k}_id"
                    t["cols"].setdefault(col, [])
                    fk = {"columns": [col], "ref_table": child, "ref_columns": ["id"]}
                    if fk not in t["fks"]:
                        t["fks"].append(fk)
                    self.table(child)["cols"].setdefault("id", [])
                elif isinstance(v, list):
                    if v and all(isinstance(x, dict) for x in v):
                        self.visit_records(k, v, parent=name)
                        ct = self.table(k)
                        col = f"{name}_id"
                        ct["cols"].setdefault(col, [])
                        fk = {"columns": [col], "ref_table": name, "ref_columns": ["id"]}
                        if fk not in ct["fks"]:
                            ct["fks"].append(fk)
                        t["cols"].setdefault("id", [])
                    else:  # list of scalars -> single joined column
                        t["cols"].setdefault(k, []).append(", ".join(str(x) for x in v))

    def to_tables(self):
        out = []
        for name, t in self.tables.items():
            cols = [{"name": c, "type": (infer_sql_type(vals) if all(isinstance(x, str) or x is None for x in vals)
                                         else _mixed_type(vals))} for c, vals in t["cols"].items()]
            tbl = make_table(name, cols, None)
            if not tbl["primary_key"] and any(c["name"] == "id" for c in cols):
                tbl["primary_key"] = ["id"]
                for c in tbl["columns"]:
                    c["pk"] = c["name"] == "id"
            tbl["foreign_keys"] = t["fks"]
            out.append(tbl)
        return out


def _mixed_type(vals):
    vals = [v for v in vals if v is not None]
    types = {python_value_type(v) for v in vals}
    return types.pop() if len(types) == 1 else ("numeric" if types <= {"integer", "bigint", "numeric"} else "varchar")


def _structure_to_schema(filename: str, kind: str, obj) -> dict:
    f = _Flattener()
    stem = _stem(filename)
    if isinstance(obj, list):
        f.visit_records(stem, obj)
    elif isinstance(obj, dict):
        arrays = {k: v for k, v in obj.items() if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)}
        if arrays and len(arrays) >= 1 and len(arrays) >= len([v for v in obj.values() if isinstance(v, (dict, list))]):
            scalars = {k: v for k, v in obj.items() if _is_scalar(v)}
            for k, v in arrays.items():
                f.visit_records(k, v)
            if scalars:
                f.visit_records(stem, [scalars])
        else:
            # single root object with nested structure; unwrap a single wrapper key
            if len(obj) == 1 and isinstance(next(iter(obj.values())), (dict, list)):
                k, v = next(iter(obj.items()))
                f.visit_records(k, v if isinstance(v, list) else [v])
            else:
                f.visit_records(stem, [obj])
    else:
        raise IngestError("Top-level value must be an object or array.")
    tables = f.to_tables()
    if not tables:
        raise IngestError("No structured records found.")
    res = schema_result(filename, kind, tables)
    res["preview"] = {"text": json.dumps(obj, indent=2, default=str)[:3000]}
    return res


def parse_json(filename: str, data: bytes) -> dict:
    try:
        obj = json.loads(_decode(data))
    except json.JSONDecodeError as e:
        raise IngestError(f"Invalid JSON: {e}") from e
    return _structure_to_schema(filename, "json", obj)


def parse_yaml(filename: str, data: bytes) -> dict:
    import yaml

    try:
        obj = yaml.safe_load(_decode(data))
    except yaml.YAMLError as e:
        raise IngestError(f"Invalid YAML: {e}") from e
    return _structure_to_schema(filename, "yaml", obj)


def parse_xml(filename: str, data: bytes) -> dict:
    try:
        root = ET.fromstring(data)  # stdlib expat: external entities are not resolved
    except ET.ParseError as e:
        raise IngestError(f"Invalid XML: {e}") from e
    tag = re.sub(r"^\{.*\}", "", root.tag)
    return _structure_to_schema(filename, "xml", {tag: _xml_to_obj(root)})
