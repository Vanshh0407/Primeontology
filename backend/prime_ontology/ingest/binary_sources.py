"""Parquet and SQLite database files -> schema (+ optional sample rows)."""
import os
import sqlite3
import tempfile

from .common import MAX_SAMPLE_ROWS, IngestError, make_table, schema_result
from .tabular import _stem


def _arrow_sql_type(pa, t) -> str:
    if pa.types.is_boolean(t):
        return "boolean"
    if pa.types.is_integer(t):
        return "bigint" if t.bit_width > 32 else "integer"
    if pa.types.is_floating(t):
        return "double"
    if pa.types.is_decimal(t):
        return "numeric"
    if pa.types.is_timestamp(t):
        return "timestamp"
    if pa.types.is_date(t):
        return "date"
    if pa.types.is_time(t):
        return "time"
    if pa.types.is_binary(t) or pa.types.is_large_binary(t):
        return "blob"
    return "varchar"


def parse_parquet(filename: str, data: bytes, keep_rows: int = 0) -> dict:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as e:
        raise IngestError("Parquet support needs the 'pyarrow' package on the server (pip install pyarrow).") from e
    try:
        pf = pq.ParquetFile(pa.BufferReader(data))
    except Exception as e:
        raise IngestError(f"Cannot read Parquet file: {e}") from e
    cols = [{"name": f.name, "type": _arrow_sql_type(pa, f.type)} for f in pf.schema_arrow]
    names = [c["name"] for c in cols]
    rows = []
    for batch in pf.iter_batches(batch_size=min(MAX_SAMPLE_ROWS, 5000)):
        d = batch.to_pydict()
        rows = [[d[n][i] for n in names] for i in range(batch.num_rows)]
        break
    table = make_table(_stem(filename), cols, rows)
    if keep_rows:
        table["sample"] = {"columns": names, "rows": rows[:keep_rows + 1]}
    res = schema_result(filename, "parquet", [table])
    res["preview"] = {"columns": names, "rows": [[str(v) if v is not None else "" for v in r] for r in rows[:10]],
                      "rowCount": pf.metadata.num_rows}
    return res


def parse_sqlite(filename: str, data: bytes, keep_rows: int = 0) -> dict:
    """SQLite database file -> schema. Opened read-only; nothing in the file is executed."""
    if not data.startswith(b"SQLite format 3\x00"):
        raise IngestError("Not a SQLite database file.")
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp.write(data)
        path = tmp.name
    tables = []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            con.execute("PRAGMA query_only = ON")
            names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            if not names:
                raise IngestError("The SQLite database has no tables.")
            for n in names:
                q = '"' + n.replace('"', '""') + '"'
                info = con.execute(f"PRAGMA table_info({q})").fetchall()  # cid, name, type, notnull, dflt, pk
                columns = [{"name": r[1], "type": r[2] or "varchar", "nullable": not r[3] and not r[5], "pk": bool(r[5])} for r in info]
                pk = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
                fks = {}
                for r in con.execute(f"PRAGMA foreign_key_list({q})").fetchall():  # id, seq, table, from, to, ...
                    g = fks.setdefault(r[0], {"columns": [], "ref_table": r[2], "ref_columns": []})
                    g["columns"].append(r[3])
                    g["ref_columns"].append(r[4])
                t = {"name": n, "columns": columns, "primary_key": pk, "foreign_keys": list(fks.values())}
                if keep_rows:
                    cur = con.execute(f"SELECT * FROM {q} LIMIT ?", (keep_rows + 1,))
                    t["sample"] = {"columns": [d[0] for d in cur.description], "rows": [list(r) for r in cur.fetchall()]}
                tables.append(t)
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        raise IngestError(f"Cannot read SQLite file: {e}") from e
    finally:
        os.unlink(path)
    byname = {t["name"]: t for t in tables}
    for t in tables:  # REFERENCES parent (no column list) -> parent's primary key
        for fk in t["foreign_keys"]:
            if not all(fk["ref_columns"]) and fk["ref_table"] in byname:
                fk["ref_columns"] = byname[fk["ref_table"]]["primary_key"][: len(fk["columns"])]
    return {"kind": "schema", "schema": {"source": {"type": "sqlite", "database": filename, "schema": ""}, "tables": tables}}
