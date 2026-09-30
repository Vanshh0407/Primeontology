"""AI Ontology Assistant (R5 / doc §8).

Flow: request -> PROPOSAL (list of operations) -> diff -> user approval -> commit.
The assistant never mutates an ontology; `apply_operations` is a pure function
the caller runs only after approval. Uses the LLM when configured, otherwise a
rule-based interpreter + domain templates.
"""
import copy
import re

from . import llm
from .model_ops import NAME_RE, XSD_TYPES, delete_class, rename_class
from .naming import to_class_name, to_label, to_property_name

TEMPLATES = {
    "contract": {
        "classes": {"Contract": None, "Party": None, "Organization": "Party", "Person": "Party", "Customer": "Party",
                    "Supplier": "Party", "Obligation": None, "Clause": None, "Payment": None, "Document": None,
                    "Agreement": "Contract", "Risk": None, "Jurisdiction": None, "Regulation": None},
        "relations": [("Contract", "hasParty", "Party"), ("Contract", "contains", "Clause"), ("Contract", "creates", "Obligation"),
                      ("Contract", "requires", "Payment"), ("Contract", "hasDocument", "Document"), ("Contract", "hasRisk", "Risk"),
                      ("Contract", "governedBy", "Jurisdiction"), ("Contract", "subjectTo", "Regulation"), ("Obligation", "boundTo", "Party")],
        "data": [("Contract", "effectiveDate", "date"), ("Contract", "expirationDate", "date"), ("Payment", "amount", "decimal"),
                 ("Clause", "text", "string"), ("Party", "name", "string")]},
    "sales": {
        "classes": {"Customer": None, "SalesOrder": None, "Product": None, "Supplier": None, "Invoice": None},
        "relations": [("SalesOrder", "placedBy", "Customer"), ("SalesOrder", "contains", "Product"), ("Product", "suppliedBy", "Supplier"),
                      ("Invoice", "billsOrder", "SalesOrder")],
        "data": [("Customer", "name", "string"), ("SalesOrder", "orderDate", "date"), ("Product", "price", "decimal")]},
    "agent": {
        "classes": {"Agent": None, "Tool": None, "Workflow": None, "Capability": None, "Policy": None, "BusinessObject": None,
                    "DataSource": None, "MCPServer": "Tool"},
        "relations": [("Agent", "hasCapability", "Capability"), ("Agent", "usesTool", "Tool"), ("Workflow", "invokesTool", "Tool"),
                      ("Policy", "governs", "Agent"), ("Tool", "operatesOn", "BusinessObject"), ("Tool", "readsFrom", "DataSource")],
        "data": [("Agent", "name", "string"), ("Tool", "name", "string")]},
}
TEMPLATE_KEYS = {"contract": "contract", "agreement": "contract", "legal": "contract", "sales": "sales", "order": "sales",
                 "retail": "sales", "agent": "agent", "agentic": "agent", "workflow": "agent"}


def _cls(name):
    return to_class_name(name) if not NAME_RE.match(name) or name.islower() or "_" in name or " " in name else name


def _has_class(m, n):
    return any(c["name"].lower() == n.lower() for c in m["classes"])


def _canon(m, n):
    return next((c["name"] for c in m["classes"] if c["name"].lower() == n.lower()), None)


def rule_based(model: dict, prompt: str) -> dict:
    p = prompt.strip()
    low = p.lower()
    ops, notes = [], []
    m = re.match(r"(?:create|build|generate|design|make)\b.*\b(?:ontology|model)\b.*?\bfor\b\s+(.+)", low)
    if m or any(w in low for w in ("starter ontology", "create an ontology")):
        domain = (m.group(1) if m else low)
        key = next((TEMPLATE_KEYS[k] for k in TEMPLATE_KEYS if k in domain), None)
        if key:
            t = TEMPLATES[key]
            for name, parent in t["classes"].items():
                if not _has_class(model, name):
                    ops.append({"op": "add_class", "name": name, "parents": [parent] if parent else [],
                                "comment": f"{to_label(name)} ({key} domain)."})
            for d, n, r in t["relations"]:
                ops.append({"op": "add_relationship", "domain": d, "name": n, "range": r})
            for d, n, dt in t["data"]:
                ops.append({"op": "add_property", "domain": d, "name": n, "datatype": dt})
        else:
            notes.append("No built-in template for that domain; configure an LLM (ANTHROPIC_API_KEY) for open-ended generation, "
                         "or describe classes explicitly, e.g. 'add class Vehicle'.")
    for mm in re.finditer(r"add (?:a )?class(?:es)? ([A-Za-z0-9_ ,]+?)(?: (?:under|as a subclass of|extends) ([A-Za-z0-9_]+))?(?:$|\.|;)", p, re.I):
        for nm in re.split(r",| and ", mm.group(1)):
            if nm.strip():
                ops.append({"op": "add_class", "name": _cls(nm.strip()), "parents": [_canon(model, mm.group(2)) or _cls(mm.group(2))] if mm.group(2) else []})
    for mm in re.finditer(r"(?:connect|link|relate)\s+([A-Za-z0-9_]+)\s+(?:to|with)\s+([A-Za-z0-9_]+)(?:\s+(?:as|via|using)\s+([A-Za-z0-9_]+))?", p, re.I):
        a, b = _canon(model, mm.group(1)) or _cls(mm.group(1)), _canon(model, mm.group(2)) or _cls(mm.group(2))
        ops.append({"op": "add_relationship", "domain": a, "range": b, "name": mm.group(3) or f"has{b}"})
    for mm in re.finditer(r"add (?:a )?(?:(\w+) )?propert(?:y|ies) ([A-Za-z0-9_]+) (?:to|on) ([A-Za-z0-9_]+)(?: as (?:a )?(\w+))?", p, re.I):
        dt = (mm.group(4) or mm.group(1) or "string")
        dt = {"text": "string", "number": "decimal", "int": "integer", "datetime": "dateTime"}.get(dt.lower(), dt)
        ops.append({"op": "add_property", "domain": _canon(model, mm.group(3)) or _cls(mm.group(3)),
                    "name": to_property_name(mm.group(2)), "datatype": dt if dt in XSD_TYPES else "string"})
    for mm in re.finditer(r"rename (?:class )?([A-Za-z0-9_]+) to ([A-Za-z0-9_]+)", p, re.I):
        ops.append({"op": "rename_class", "from": _canon(model, mm.group(1)) or mm.group(1), "to": mm.group(2)})
    for mm in re.finditer(r"(?:delete|remove) (?:class )?([A-Za-z0-9_]+)", p, re.I):
        ops.append({"op": "delete_class", "name": _canon(model, mm.group(1)) or mm.group(1)})
    if not ops and not notes:
        notes.append("I could not interpret that request. Try: 'Create an ontology for contract management', "
                     "'Connect Contract to Customer', 'Add class Vehicle under Asset', "
                     "'Add property price to Product as decimal', 'Rename Client to Customer'.")
    return {"source": "rules", "ops": ops, "notes": notes}


LLM_SYSTEM = """You are an ontology engineering assistant. Given the current ontology summary and a user request, output ONLY a JSON
object {"summary": str, "ops": [...]} where each op is one of:
{"op":"add_class","name":PascalCase,"parents":[names],"comment":str}
{"op":"add_property","domain":Class,"name":camelCase,"datatype":xsd name (string,integer,decimal,boolean,date,dateTime)}
{"op":"add_relationship","domain":Class,"name":camelCase verb,"range":Class}
{"op":"rename_class","from":Class,"to":Class}
{"op":"delete_class","name":Class}
Reuse existing classes; do not duplicate; keep names valid identifiers. Do not include prose outside the JSON."""


def summarize(model):
    return "Classes: " + ", ".join(f'{c["name"]}' + (f'<{",".join(c["parents"])}' if c.get("parents") else "") for c in model["classes"]) + \
        "\nRelationships: " + "; ".join(f'{p["domain"]}-{p["name"]}->{p["range"]}' for p in model["objectProperties"]) + \
        "\nProperties: " + "; ".join(f'{p["domain"]}.{p["name"]}:{p["datatype"]}' for p in model["dataProperties"][:80])


def propose(model: dict, prompt: str, use_llm: bool = True) -> dict:
    proposal = None
    if use_llm and llm.available():
        try:
            data = llm.extract_json(llm.complete(LLM_SYSTEM, f"{summarize(model)}\n\nRequest: {prompt}"))
            ops = [o for o in data.get("ops", []) if isinstance(o, dict) and o.get("op")]
            proposal = {"source": "llm", "ops": ops, "notes": [data.get("summary", "")] if data.get("summary") else []}
        except Exception as e:  # fall back, but tell the user
            proposal = rule_based(model, prompt)
            proposal["notes"].append(f"LLM unavailable ({e}); used rule-based interpretation.")
    if proposal is None:
        proposal = rule_based(model, prompt)
    proposal["ops"] = normalize_ops(model, proposal["ops"])
    return proposal


def normalize_ops(model, ops):
    """Drop invalid / duplicate ops so the proposal is always safely appliable."""
    sim = copy.deepcopy(model)
    good = []
    for op in ops:
        try:
            sim = apply_operations(sim, [op])
            good.append(op)
        except (ValueError, KeyError):
            continue
    return good


def apply_operations(model: dict, ops: list[dict]) -> dict:
    m = copy.deepcopy(model)
    m.setdefault("classes", [])
    for op in ops:
        kind = op.get("op")
        if kind == "add_class":
            name = op["name"]
            if not NAME_RE.match(name):
                raise ValueError(f"invalid class name {name}")
            if _has_class(m, name):
                continue
            parents = [_canon(m, x) or x for x in op.get("parents", []) if x]
            m["classes"].append({"name": name, "label": to_label(name), "comment": op.get("comment", ""), "parents": parents,
                                 "source": {"origin": "ai-assistant"}})
        elif kind in ("add_property", "add_relationship"):
            dom = _canon(m, op["domain"])
            if not dom and NAME_RE.match(op["domain"]):
                m["classes"].append({"name": op["domain"], "label": to_label(op["domain"]), "comment": "", "parents": [],
                                     "source": {"origin": "ai-assistant"}})
                dom = op["domain"]
            if not NAME_RE.match(op["name"]):
                raise ValueError("invalid property name")
            if kind == "add_property":
                if any(p["domain"] == dom and p["name"] == op["name"] for p in m["dataProperties"]):
                    continue
                dt = op.get("datatype", "string")
                if dt not in XSD_TYPES:
                    raise ValueError("bad datatype")
                m["dataProperties"].append({"name": op["name"], "label": to_label(op["name"]), "domain": dom, "datatype": dt,
                                            "required": False, "identifier": False, "source": {"origin": "ai-assistant"}})
            else:
                rng = _canon(m, op["range"])
                if not rng and NAME_RE.match(op["range"]):
                    m["classes"].append({"name": op["range"], "label": to_label(op["range"]), "comment": "", "parents": [],
                                         "source": {"origin": "ai-assistant"}})
                    rng = op["range"]
                if any(p["domain"] == dom and p["name"] == op["name"] for p in m["objectProperties"]):
                    continue
                m["objectProperties"].append({"name": op["name"], "label": to_label(op["name"]), "domain": dom, "range": rng,
                                              "cardinality": "many-to-many", "required": False, "source": {"origin": "ai-assistant"}})
        elif kind == "rename_class":
            if not _canon(m, op["from"]):
                raise ValueError("unknown class")
            m = rename_class(m, _canon(m, op["from"]), op["to"])
        elif kind == "delete_class":
            if not _canon(m, op["name"]):
                raise ValueError("unknown class")
            m = delete_class(m, _canon(m, op["name"]))
        else:
            raise ValueError(f"unknown op {kind}")
    return m
