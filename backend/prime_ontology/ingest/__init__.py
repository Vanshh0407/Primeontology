"""Universal ingestion entry point."""
import os

from .. import generator
from . import documents, rdfio, semantic, tabular, tree
from ..introspect import IntrospectionError
from .common import IngestError

MAX_BYTES = 25 * 1024 * 1024

SUPPORTED = {
    "structured": [".csv", ".tsv", ".xlsx", ".xlsm", ".sql", ".json"],
    "semi-structured": [".xml", ".yaml", ".yml", ".rdf", ".owl", ".ttl", ".nt", ".nq", ".jsonld", ".n3", ".trig"],
    "documents": [".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown", ".html", ".htm"],
}


def analyze(filename: str, data: bytes) -> dict:
    try:
        return _analyze(filename, data)
    except IntrospectionError as e:
        raise IngestError(str(e)) from e


def _analyze(filename: str, data: bytes) -> dict:
    """Return {kind, format, preview, candidate model, stats, warnings, mappingOverview}.
    Nothing is persisted: this is the review step."""
    if len(data) > MAX_BYTES:
        raise IngestError(f"File too large (max {MAX_BYTES // (1024 * 1024)} MB).")
    if not data:
        raise IngestError("File is empty.")
    ext = os.path.splitext(filename)[1].lower()
    warnings, preview, category = [], {}, "structured"
    if ext in (".csv", ".tsv"):
        parsed = tabular.parse_delimited(filename, data, "\t" if ext == ".tsv" else None)
    elif ext in (".xlsx", ".xlsm"):
        parsed = tabular.parse_excel(filename, data)
    elif ext == ".sql":
        parsed = tabular.parse_sql(filename, data)
    elif ext == ".json":
        parsed = tree.parse_json(filename, data)
    elif ext in (".yaml", ".yml"):
        parsed, category = tree.parse_yaml(filename, data), "semi-structured"
    elif ext == ".xml" and b"rdf:RDF" not in data[:2000]:
        parsed, category = tree.parse_xml(filename, data), "semi-structured"
    elif ext in rdfio.RDF_FORMATS:
        parsed, category = rdfio.parse_rdf(filename, data), "semi-structured"
    elif ext in SUPPORTED["documents"]:
        category = "documents"
        doc = documents.parse_document(ext, data)
        warnings += doc["warnings"]
        model = semantic.extract_candidate(doc["sections"], filename)
        if not model["classes"]:
            warnings.append("No known concepts were detected; try a different document or use the AI assistant.")
        return {"kind": "document", "category": category, "format": ext.lstrip("."), "model": model,
                "stats": generator.stats(model), "warnings": warnings,
                "preview": {"pages": doc["pages"], "sections": [
                    {"page": s["page"], "heading": s["heading"], "text": s["text"][:400]} for s in doc["sections"][:40]]}}
    else:
        raise IngestError(f"Unsupported file type '{ext or filename}'. Supported: "
                          + ", ".join(e for v in SUPPORTED.values() for e in v))

    warnings += parsed.get("warnings", [])
    if parsed["kind"] == "schema":
        schema = parsed["schema"]
        model = generator.generate_model(schema)
        mapping = source_mapping(schema, model)
    else:
        model, mapping = parsed["model"], []
    return {"kind": parsed["kind"], "category": category, "format": ext.lstrip("."), "model": model,
            "schema": parsed.get("schema"), "mapping": mapping, "stats": generator.stats(model),
            "warnings": warnings, "preview": parsed.get("preview", {})}


def source_mapping(schema: dict, model: dict) -> list[dict]:
    """Source-to-ontology mapping rows (customers.csv / customer_id -> Customer.customerId)."""
    rows = []
    for c in model["classes"]:
        rows.append({"source": c["source"].get("table"), "column": None, "target": c["name"], "kind": "class"})
    for p in model["dataProperties"]:
        rows.append({"source": p["source"].get("table"), "column": p["source"].get("column"),
                     "target": f'{p["domain"]}.{p["name"]}', "kind": "dataProperty", "datatype": p["datatype"]})
    for p in model["objectProperties"]:
        rows.append({"source": p["source"].get("table"), "column": p["source"].get("column"),
                     "target": f'{p["domain"]}.{p["name"]} → {p["range"]}', "kind": "objectProperty"})
    return rows
