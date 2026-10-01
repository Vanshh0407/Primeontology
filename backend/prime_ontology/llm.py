"""Optional LLM provider (Anthropic Messages API over HTTPS, stdlib only).

Set ANTHROPIC_API_KEY to enable. PRIME_ONTOLOGY_LLM_MODEL overrides the model.
Everything that uses this has a deterministic fallback; LLM output is always
parsed and validated, and only ever produces *proposals* for human review.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def model_name() -> str:
    return os.environ.get("PRIME_ONTOLOGY_LLM_MODEL", "claude-sonnet-5-5")


def complete(system: str, user: str, max_tokens: int = 2000, timeout: int = 60) -> str:
    if not available():
        raise RuntimeError("LLM not configured (set ANTHROPIC_API_KEY).")
    body = json.dumps({"model": model_name(), "max_tokens": max_tokens, "system": system,
                       "messages": [{"role": "user", "content": user}]}).encode()
    url = os.environ.get("PRIME_ONTOLOGY_LLM_URL", API_URL)  # override for a gateway/proxy (and for tests against a local stand-in)
    host = urllib.parse.urlparse(url)
    if host.scheme != "https" and not (host.scheme == "http" and host.hostname in ("127.0.0.1", "localhost")):
        raise RuntimeError("PRIME_ONTOLOGY_LLM_URL must be https (http is allowed only for localhost).")
    req = urllib.request.Request(url, data=body, headers={
        "content-type": "application/json", "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"LLM request failed: HTTP {e.code}") from e
    except Exception as e:
        raise RuntimeError(f"LLM request failed: {e}") from e
    return "".join(b.get("text", "") for b in data.get("content", []))


def extract_json(text: str):
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("No JSON in LLM response.")
    dec = json.JSONDecoder()
    obj, _ = dec.raw_decode(text[start:])
    return obj
