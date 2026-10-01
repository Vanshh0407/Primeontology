"""Workflow generation: turn an agent plan (or a finished run) into a portable workflow definition.

  n8n   - importable n8n workflow JSON (one HTTP Request node per step, calling this platform's execute endpoint)
  bpmn  - BPMN 2.0 XML (serviceTask per step, userTask for steps that need human approval, sequence flows from dependencies)

These are generated definitions; they have been checked for structure only (valid JSON / well-formed XML), NOT imported into a live
n8n instance or a BPM engine.
"""
import json
from xml.etree import ElementTree as ET

FORMATS = ("n8n", "bpmn")


def _steps(plan: dict) -> list[dict]:
    return plan.get("steps", [])


def to_n8n(plan: dict, ontology_id: int, base_url: str = "http://localhost:8008") -> dict:
    steps = _steps(plan)
    nodes = [{"parameters": {}, "name": "Start", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 0]}]
    conns, pos = {}, {}
    for i, s in enumerate(steps):
        pos[s["key"]] = i
        needs_approval = (s.get("decision") or {}).get("decision") == "require_approval"
        nodes.append({
            "parameters": {"method": "POST", "url": f"{base_url}/api/v1/ontology/{ontology_id}/agents/execute/", "sendBody": True, "specifyBody": "json",
                           "jsonBody": json.dumps({"goal": s.get("title") or s["tool"], "steps": [{"key": s["key"], "tool": s["tool"], "args": s.get("args") or {},
                                                                                                   "agent": s.get("agent"), "operation": s.get("operation", "read"),
                                                                                                   "concepts": s.get("concepts", [])}]}),
                           "options": {}},
            "name": s["key"], "type": "n8n-nodes-base.httpRequest", "typeVersion": 4, "position": [250 * (i + 1), 0],
            "notes": f"{s.get('agent') or 'unassigned'} · {s['tool']}" + (" · needs human approval" if needs_approval else "")})
    for s in steps:
        for d in s.get("dependsOn", []):
            conns.setdefault(d, {"main": [[]]})["main"][0].append({"node": s["key"], "type": "main", "index": 0})
    roots = [s["key"] for s in steps if not s.get("dependsOn")]
    conns["Start"] = {"main": [[{"node": r, "type": "main", "index": 0} for r in roots]]}
    return {"name": f"Prime agents: {str(plan.get('goal', ''))[:60]}", "nodes": nodes, "connections": conns, "active": False, "settings": {}}


def to_bpmn(plan: dict) -> str:
    ns = "http://www.omg.org/spec/BPMN/20100524/MODEL"
    ET.register_namespace("", ns)
    root = ET.Element(f"{{{ns}}}definitions", {"id": "defs", "targetNamespace": "https://primeontology.io/workflow"})
    proc = ET.SubElement(root, f"{{{ns}}}process", {"id": "agentPlan", "isExecutable": "false", "name": str(plan.get("goal", ""))[:120]})
    ET.SubElement(proc, f"{{{ns}}}startEvent", {"id": "start"})
    ET.SubElement(proc, f"{{{ns}}}endEvent", {"id": "end"})
    steps = _steps(plan)
    last_users = {s["key"] for s in steps}
    flows = 0

    def flow(a, b):
        nonlocal flows
        flows += 1
        ET.SubElement(proc, f"{{{ns}}}sequenceFlow", {"id": f"f{flows}", "sourceRef": a, "targetRef": b})

    for s in steps:
        k = s["key"]
        approval = (s.get("decision") or {}).get("decision") == "require_approval"
        if approval:
            ET.SubElement(proc, f"{{{ns}}}userTask", {"id": f"approve_{k}", "name": f"Approve: {s.get('title') or s['tool']}"})
        ET.SubElement(proc, f"{{{ns}}}serviceTask", {"id": k, "name": f"{s.get('title') or s['tool']} [{s.get('agent') or 'unassigned'}]"})
        if approval:
            flow(f"approve_{k}", k)
    entry = lambda s: f"approve_{s['key']}" if (s.get("decision") or {}).get("decision") == "require_approval" else s["key"]  # noqa: E731
    for s in steps:
        deps = s.get("dependsOn", [])
        if not deps:
            flow("start", entry(s))
        for d in deps:
            flow(d, entry(s))
            last_users.discard(d)
    for k in last_users:
        flow(k, "end")
    if not steps:
        flow("start", "end")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def export(plan: dict, fmt: str, ontology_id: int, base_url: str):
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {', '.join(FORMATS)}.")
    return to_n8n(plan, ontology_id, base_url) if fmt == "n8n" else to_bpmn(plan)
