"""Shared helpers for the ingestion pipeline.

Pipeline: SourceDocument (bytes) -> ParsedDocument (schema | sections | model)
          -> SemanticDocument (candidate model) -> review -> commit.
"""
import re
from datetime import datetime

from ..naming import singular, words

MAX_SAMPLE_ROWS = 5000


class IngestError(Exception):
    pass


def norm(name: str) -> str:
    ws = words(name)
    if ws:
        ws[-1] = singular(ws[-1])
    return "".join(ws)


_INT = re.compile(r"^[+-]?\d+$")
_DEC = re.compile(r"^[+-]?\d*\.\d+$|^[+-]?\d+\.\d*$")
_BOOL = {"true", "false", "yes", "no", "y", "n"}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{1,2}/\d{1,2}/\d{4}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def infer_sql_type(values) -> str:
    vals = [str(v).strip() for v in values if v is not None and str(v).strip() != ""]
    if not vals:
        return "varchar"
    if all(v.lower() in _BOOL for v in vals) and any(not v.isdigit() for v in vals):
        return "boolean"
    if all(_INT.match(v) for v in vals):
        return "bigint" if any(len(v) > 9 for v in vals) else "integer"
    if all(_INT.match(v) or _DEC.match(v) for v in vals):
        return "numeric"
    if all(_DATETIME.match(v) for v in vals):
        return "timestamp"
    if all(_DATE.match(v) for v in vals):
        return "date"
    return "varchar"


def python_value_type(v) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "bigint" if abs(v) > 10**9 else "integer"
    if isinstance(v, float):
        return "numeric"
    if isinstance(v, datetime):
        return "timestamp"
    if hasattr(v, "isoformat") and not isinstance(v, str):
        return "date"
    return infer_sql_type([v])


def guess_primary_key(table: str, columns: list[dict], rows: list[list] | None = None) -> list[str]:
    names = {c["name"].lower(): c["name"] for c in columns}
    t = norm(table)
    for cand in ("id", f"{t}_id", f"{t}id", f"{table.lower()}_id", "uuid"):
        if cand in names:
            return [names[cand]]
    for c in columns:
        if norm(c["name"].replace("_id", "").replace("Id", "")) == t and c["name"].lower().endswith("id"):
            return [c["name"]]
    if rows:
        col = columns[0]["name"]
        vals = [r[0] for r in rows if r and r[0] not in (None, "")]
        if vals and len(set(vals)) == len(vals) and len(vals) == len(rows):
            return [col]
    return []


def infer_foreign_keys(tables: list[dict]) -> None:
    """Add FKs where a column is named like another table's key."""
    by_norm = {norm(t["name"]): t for t in tables}
    for t in tables:
        existing = {c for fk in t["foreign_keys"] for c in fk["columns"]}
        for col in t["columns"]:
            cname = col["name"]
            if cname in t["primary_key"] or cname in existing:
                continue
            target = None
            for u in tables:
                if u is t or not u["primary_key"]:
                    continue
                if cname.lower() == u["primary_key"][0].lower():
                    target = u
                    break
            if not target:
                stem = re.sub(r"(_id|_key|_fk|_code|id)$", "", cname, flags=re.I)
                if stem and stem != cname and norm(stem) in by_norm and by_norm[norm(stem)] is not t:
                    target = by_norm[norm(stem)]
            if target and target["primary_key"]:
                t["foreign_keys"].append({"columns": [cname], "ref_table": target["name"],
                                          "ref_columns": [target["primary_key"][0]]})


def make_table(name, columns, rows=None) -> dict:
    cols = [{"name": c["name"], "type": c["type"], "nullable": True} for c in columns]
    pk = guess_primary_key(name, cols, rows)
    for c in cols:
        c["pk"] = c["name"] in pk
        if c["pk"]:
            c["nullable"] = False
    return {"name": name, "columns": cols, "primary_key": pk, "foreign_keys": []}


def schema_result(source_name, kind, tables) -> dict:
    infer_foreign_keys(tables)
    return {"kind": "schema", "schema": {"source": {"type": kind, "database": source_name, "schema": ""},
                                         "tables": tables}}
