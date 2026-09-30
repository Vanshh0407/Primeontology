import csv
import io
import os
import re

from ..introspect import parse_sql_ddl
from .common import MAX_SAMPLE_ROWS, IngestError, infer_sql_type, make_table, python_value_type, schema_result


def _stem(filename: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", os.path.splitext(os.path.basename(filename))[0]).strip("_") or "table"


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    raise IngestError("Cannot decode file text.")


def parse_delimited(filename: str, data: bytes, delimiter: str | None = None) -> dict:
    text = _decode(data)
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 1:
        raise IngestError("File is empty.")
    header, body = [h.strip() or f"column{i+1}" for i, h in enumerate(rows[0])], rows[1:MAX_SAMPLE_ROWS + 1]
    cols = [{"name": h, "type": infer_sql_type([r[i] for r in body if i < len(r)])} for i, h in enumerate(header)]
    table = make_table(_stem(filename), cols, body)
    res = schema_result(filename, "csv", [table])
    res["preview"] = {"columns": header, "rows": body[:10], "rowCount": len(rows) - 1}
    return res


def parse_excel(filename: str, data: bytes) -> dict:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise IngestError(f"Cannot read workbook: {e}") from e
    tables, preview = [], {}
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(c not in (None, "") for c in r)]
        if not rows:
            continue
        header = [str(h).strip() if h not in (None, "") else f"column{i+1}" for i, h in enumerate(rows[0])]
        body = rows[1:MAX_SAMPLE_ROWS + 1]
        cols = [{"name": h, "type": python_value_type_col([r[i] for r in body if i < len(r)])} for i, h in enumerate(header)]
        tables.append(make_table(re.sub(r"[^A-Za-z0-9_]+", "_", ws.title).strip("_") or "sheet", cols, body))
        preview[ws.title] = {"columns": header, "rows": [[str(c) if c is not None else "" for c in r] for r in body[:10]],
                             "rowCount": len(rows) - 1}
    if not tables:
        raise IngestError("Workbook has no data.")
    res = schema_result(filename, "excel", tables)
    res["preview"] = preview
    return res


def python_value_type_col(values) -> str:
    vals = [v for v in values if v not in (None, "")]
    if not vals:
        return "varchar"
    types = {python_value_type(v) for v in vals}
    if types == {"integer"} or types <= {"integer", "bigint"}:
        return "bigint" if "bigint" in types else "integer"
    if types <= {"integer", "bigint", "numeric"}:
        return "numeric"
    return types.pop() if len(types) == 1 else "varchar"


def parse_sql(filename: str, data: bytes) -> dict:
    schema = parse_sql_ddl(_decode(data))
    schema["source"]["database"] = filename
    return {"kind": "schema", "schema": schema}
