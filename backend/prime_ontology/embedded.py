"""Embedding contract (R9): one workbench, per-host context/capabilities."""

TABS = ["sources", "workbench", "mapping", "validation", "explorer", "query", "versions", "assistant", "agentic"]
ENTERPRISE_TABS = ["fabric", "rag", "twin", "autonomy", "os"]  # R11-R15

HOSTS = {
    "unicontractai": {
        "title": "UniContractAI · Contract Ontology",
        "vocabulary": ["Contract", "Party", "Clause", "Obligation", "Payment", "Risk", "Regulation", "Jurisdiction"],
        "template": "contract",
        "capabilities": ["sources", "workbench", "mapping", "validation", "explorer", "query", "versions", "assistant"],
        "defaultDocumentMode": True,
        "description": "Contract management ontology: parties, clauses, obligations, payments, risks and regulations.",
    },
    "primesemonto": {
        "title": "PrimeSemOnto · Enterprise Semantic Model",
        "vocabulary": ["Enterprise", "Customer", "Supplier", "Product", "Process", "Data", "Organization"],
        "template": "sales",
        "capabilities": TABS + ENTERPRISE_TABS,
        "defaultDocumentMode": False,
        "description": "Central semantic modelling platform: data model, mappings, reasoning, knowledge graph.",
    },
    "primeagenticos": {
        "title": "PrimeAgentic OS · Agent Ontology",
        "vocabulary": ["Agent", "Tool", "Workflow", "Capability", "Policy", "Data", "BusinessObject", "MCPServer"],
        "template": "agent",
        "capabilities": TABS + ENTERPRISE_TABS,
        "defaultDocumentMode": False,
        "description": "Semantic control plane for agents: capabilities, tools, workflows, policies and MCP servers.",
    },
}

EVENTS = {"ontology.opened", "ontology.saved", "ontology.published", "concept.selected", "import.completed", "mapping.committed"}


def manifest() -> dict:
    return {"component": "PrimeOntologyWorkbench", "package": "@prime/ontology-workbench", "apiVersion": "v1",
            "hosts": sorted(HOSTS), "tabs": TABS + ENTERPRISE_TABS, "events": sorted(EVENTS),
            "props": {"apiBase": "string (e.g. /api/v1/ontology)", "ontologyId": "number|null", "context": "unicontractai|primesemonto|primeagenticos",
                      "permissions": "{role, user, tenant}", "getAuthHeaders": "() => headers", "onEvent": "(event) => void",
                      "theme": "MUI theme override (host branding)"}}


def host_context(host: str, ontology=None) -> dict:
    if host not in HOSTS:
        raise KeyError(host)
    h = HOSTS[host]
    ctx = {"host": host, **h}
    if ontology is not None:
        names = {c["name"] for c in ontology.model.get("classes", [])}
        ctx["ontology"] = {"id": ontology.id, "name": ontology.name, "status": ontology.status, "version": ontology.current_version}
        ctx["vocabularyCoverage"] = {v: (v in names) for v in h["vocabulary"]}
    return ctx
