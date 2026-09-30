"""Read-only MCP (Model Context Protocol) server exposing the ontology as a semantic layer to agents.

Transport: JSON-RPC 2.0 over stdio (newline-delimited). Run with:
    python manage.py mcp_server [--tenant T]
Register in an MCP client as: {"command": "python", "args": ["manage.py", "mcp_server"], "cwd": "<backend dir>"}

Deliberately read-only: agents may discover concepts, relationships, paths, plans and run read-only SPARQL,
but cannot modify ontologies (governance stays with humans).
"""
import json
import sys

from . import agent, query
from .models import Ontology

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "prime-ontology", "version": "1.0.0"}

_ONT = {"ontology_id": {"type": "integer", "description": "Ontology id (see list_ontologies)"}}
TOOLS = [
    {"name": "list_ontologies", "description": "List available ontologies with status and size.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "search_concepts", "description": "Semantic search over classes, properties and synonyms.",
     "inputSchema": {"type": "object", "properties": {**_ONT, "query": {"type": "string"}}, "required": ["ontology_id", "query"]}},
    {"name": "get_concept", "description": "Full detail of a class: parents, properties, relationships, sources/provenance.",
     "inputSchema": {"type": "object", "properties": {**_ONT, "name": {"type": "string"}}, "required": ["ontology_id", "name"]}},
    {"name": "find_path", "description": "Shortest relationship path connecting two classes.",
     "inputSchema": {"type": "object", "properties": {**_ONT, "from": {"type": "string"}, "to": {"type": "string"}}, "required": ["ontology_id", "from", "to"]}},
    {"name": "plan_request", "description": "Ground a natural-language request in the ontology, bind registered tools and evaluate policies. Returns an ordered plan and a decision (ready/partial/needs_approval/blocked/unreachable/clarify).",
     "inputSchema": {"type": "object", "properties": {**_ONT, "request": {"type": "string"}, "role": {"type": "string"}}, "required": ["ontology_id", "request"]}},
    {"name": "get_context_pack", "description": "Compact ontology + policy context to inject into an LLM prompt, optionally focused on a request.",
     "inputSchema": {"type": "object", "properties": {**_ONT, "request": {"type": "string"}}, "required": ["ontology_id"]}},
    {"name": "run_sparql", "description": "Run a read-only SPARQL query (SELECT/ASK/CONSTRUCT/DESCRIBE) over the ontology graph. Prefixes onto:, owl:, rdfs:, rdf:, xsd:, skos: are predefined.",
     "inputSchema": {"type": "object", "properties": {**_ONT, "sparql": {"type": "string"}}, "required": ["ontology_id", "sparql"]}},
]


class ToolError(Exception):
    pass


def _ontology(args, tenant):
    try:
        qs = Ontology.objects.all()
        if tenant:
            qs = qs.filter(tenant=tenant)
        return qs.get(pk=int(args["ontology_id"]))
    except (KeyError, ValueError, TypeError):
        raise ToolError("ontology_id (integer) is required.")
    except Ontology.DoesNotExist:
        raise ToolError("Ontology not found.")


def call_tool(name: str, args: dict, tenant: str = "") -> dict:
    if name == "list_ontologies":
        qs = Ontology.objects.filter(tenant=tenant) if tenant else Ontology.objects.all()
        return {"ontologies": [{"id": o.id, "name": o.name, "status": o.status, "version": o.current_version,
                                "classes": len(o.model.get("classes", []))} for o in qs]}
    o = _ontology(args, tenant)
    m = o.model
    try:
        if name == "search_concepts":
            return {"results": query.search(m, str(args.get("query", "")))}
        if name == "get_concept":
            return query.concept(m, str(args["name"]))
        if name == "find_path":
            p = query.find_path(m, str(args["from"]), str(args["to"]))
            return {"connected": p is not None, "path": p}
        if name == "plan_request":
            return agent.plan(m, str(args["request"]), str(args.get("role", "agent")))
        if name == "get_context_pack":
            return agent.context_pack(m, args.get("request"))
        if name == "run_sparql":
            return query.run_sparql(m, str(args["sparql"]), o.base_iri, o.name)
    except KeyError as e:
        raise ToolError(f"Missing or unknown: {e}")
    except ValueError as e:
        raise ToolError(str(e))
    raise ToolError(f"Unknown tool '{name}'.")


def handle(msg: dict, tenant: str = ""):
    """Return a JSON-RPC response dict, or None for notifications."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:  # notification (e.g. notifications/initialized)
        return None
    ok = lambda result: {"jsonrpc": "2.0", "id": mid, "result": result}
    err = lambda code, message: {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}
    if method == "initialize":
        return ok({"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        try:
            result = call_tool(params.get("name", ""), params.get("arguments") or {}, tenant)
            return ok({"content": [{"type": "text", "text": json.dumps(result, default=str)}], "isError": False})
        except ToolError as e:
            return ok({"content": [{"type": "text", "text": str(e)}], "isError": True})
    return err(-32601, f"Method not found: {method}")


def serve(tenant: str = "", stdin=None, stdout=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            resp = handle(msg, tenant)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        except Exception as e:  # never crash the transport
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": f"Internal error: {e}"}}
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
