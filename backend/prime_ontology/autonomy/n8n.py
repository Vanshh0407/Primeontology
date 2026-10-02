"""Live n8n link: push agent plans straight into an n8n instance (its public REST API) and list them back.

Configure PRIME_N8N_URL (default http://localhost:5678) and PRIME_N8N_API_KEY (n8n -> Settings -> n8n API).
The workflow authenticates to this platform as a host app: the service token is issued here once per ontology and
handed to n8n as an encrypted n8n credential (Header Auth), so it never appears in the workflow JSON.
"""
import hashlib
import json
import secrets
import urllib.error
import urllib.request

from django.conf import settings

from ..osplane.models import HostApp
from . import workflow

HOST_KEY = "n8n-link"
CRED_TYPE = "httpHeaderAuth"


class N8nError(Exception):
    pass


def ui_url() -> str:
    return (getattr(settings, "PRIME_N8N_URL", "") or "http://localhost:5678").rstrip("/")


def configured() -> bool:
    return bool(getattr(settings, "PRIME_N8N_API_KEY", ""))


def _call(method: str, path: str, payload=None):
    req = urllib.request.Request(f"{ui_url()}/api/v1{path}", method=method, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"X-N8N-API-KEY": settings.PRIME_N8N_API_KEY, "Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode(errors="replace")
        if e.code == 401:
            raise N8nError("n8n rejected the API key (PRIME_N8N_API_KEY).") from None
        raise N8nError(f"n8n returned {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise N8nError(f"Cannot reach n8n at {ui_url()}: {getattr(e, 'reason', e)}") from None


def _execute_url(ontology_id: int) -> str:
    return f"/ontology/{ontology_id}/agents/execute/"


def _ours(wf: dict, ontology_id: int) -> bool:
    return any(_execute_url(ontology_id) in str((n.get("parameters") or {}).get("url", "")) for n in wf.get("nodes", []))


def _all_workflows() -> list[dict]:
    out, cursor = [], None
    for _ in range(20):  # 20 pages x 100 is plenty for a workbench view
        page = _call("GET", "/workflows?limit=100" + (f"&cursor={cursor}" if cursor else ""))
        out += page.get("data", [])
        cursor = page.get("nextCursor")
        if not cursor:
            break
    return out


def list_workflows(ontology_id: int) -> list[dict]:
    rows = []
    for wf in _all_workflows():
        if _ours(wf, ontology_id):
            steps = [n["name"] for n in wf.get("nodes", []) if n.get("type") == "n8n-nodes-base.httpRequest"]
            rows.append({"id": wf["id"], "name": wf.get("name", ""), "active": bool(wf.get("active")), "updatedAt": wf.get("updatedAt"),
                         "steps": steps, "url": f"{ui_url()}/workflow/{wf['id']}"})
    return sorted(rows, key=lambda r: r.get("updatedAt") or "", reverse=True)


def _credential(ontology) -> dict:
    """Reuse the Header Auth credential already used by this ontology's workflows; otherwise (re)issue the token and create one."""
    for wf in _all_workflows():
        if _ours(wf, ontology.id):
            for n in wf.get("nodes", []):
                c = (n.get("credentials") or {}).get(CRED_TYPE)
                if c and c.get("id"):
                    return {"id": c["id"], "name": c.get("name", "")}
    # The token is stored only as a hash here, so a new credential always needs a fresh token.
    HostApp.objects.filter(ontology=ontology, key=HOST_KEY).delete()
    token = "pst_" + secrets.token_urlsafe(32)
    HostApp.objects.create(ontology=ontology, key=HOST_KEY, name="n8n (linked)", role="editor",
                           token_hash=hashlib.sha256(token.encode()).hexdigest(), token_hint=token[-4:])
    cred = _call("POST", "/credentials", {"name": f"Prime Ontology #{ontology.id} ({ontology.name[:40]})", "type": CRED_TYPE,
                                          "data": {"name": "X-Prime-Service-Token", "value": token}})
    return {"id": cred["id"], "name": cred.get("name", "")}


def push(ontology, plan: dict, base_url: str) -> dict:
    wf = workflow.to_n8n(plan, ontology.id, base_url)
    cred = _credential(ontology)
    for n in wf["nodes"]:
        if n["type"] != "n8n-nodes-base.httpRequest":
            continue
        p = n["parameters"]
        hp = p["headerParameters"]["parameters"]
        p["headerParameters"]["parameters"] = [h for h in hp if h["name"] != "X-Prime-Service-Token"]  # sent by the credential instead
        p["authentication"], p["genericAuthType"] = "genericCredentialType", CRED_TYPE
        n["credentials"] = {CRED_TYPE: cred}
    created = _call("POST", "/workflows", {"name": wf["name"], "nodes": wf["nodes"], "connections": wf["connections"], "settings": {}})
    return {"id": created["id"], "name": created.get("name", wf["name"]), "url": f"{ui_url()}/workflow/{created['id']}", "credential": cred["name"]}
