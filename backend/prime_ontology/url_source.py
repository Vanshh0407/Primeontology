"""REST/JSON API source (server fetches a URL and ontologises the JSON).

SECURITY: letting a server fetch user-supplied URLs is an SSRF vector, so this is DISABLED unless an administrator lists the exact
hosts in settings.PRIME_ONTOLOGY_ALLOWED_URL_HOSTS. Also: https only (http only if PRIME_ONTOLOGY_ALLOW_HTTP_URLS), redirects are
refused (they could bounce to an internal host), response size and time are capped, only JSON is accepted, and a user-supplied
Authorization header is used for the single request and never stored or logged.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from .ingest import tree
from .ingest.common import IngestError

MAX_BYTES = 10 * 1024 * 1024
TIMEOUT = 15


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def allowed_hosts() -> list[str]:
    return [h.lower() for h in getattr(settings, "PRIME_ONTOLOGY_ALLOWED_URL_HOSTS", []) or []]


def enabled() -> bool:
    return bool(allowed_hosts())


def fetch_json(url: str, auth_header: str | None = None) -> tuple[str, bytes]:
    if not enabled():
        raise PermissionError("URL sources are disabled on this server (an administrator must set PRIME_ONTOLOGY_ALLOWED_URL_HOSTS).")
    u = urllib.parse.urlparse(url)
    allow_http = getattr(settings, "PRIME_ONTOLOGY_ALLOW_HTTP_URLS", False)
    if u.scheme not in ("https", "http") or (u.scheme == "http" and not allow_http):
        raise PermissionError("Only https URLs are allowed.")
    if (u.hostname or "").lower() not in allowed_hosts():
        raise PermissionError(f"Host '{u.hostname}' is not in the allow-list for URL sources.")
    if u.username or u.password:
        raise PermissionError("Credentials in the URL are not allowed; use the Authorization field.")
    headers = {"Accept": "application/json", "User-Agent": "prime-ontology/1.0"}
    if auth_header:
        headers["Authorization"] = auth_header
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=TIMEOUT) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "json" not in ctype:
                raise IngestError(f"The URL did not return JSON (Content-Type: {ctype or 'none'}).")
            data = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise IngestError(f"The server answered HTTP {e.code}" + (" (redirects are not followed)." if 300 <= e.code < 400 else "."))
    except urllib.error.URLError as e:
        raise IngestError(f"Cannot reach the URL: {e.reason}")
    except TimeoutError:
        raise IngestError("The request timed out.")
    if len(data) > MAX_BYTES:
        raise IngestError("The response is larger than 10 MB.")
    name = (u.path.rstrip("/").rsplit("/", 1)[-1] or u.hostname or "api") + ".json"
    return name, data


def analyze_url(url: str, auth_header: str | None = None) -> dict:
    from . import generator, ingest

    name, data = fetch_json(url, auth_header)
    try:
        json.loads(data)
    except json.JSONDecodeError as e:
        raise IngestError(f"Invalid JSON from the URL: {e}") from e
    res = ingest.analyze(name, data)
    res["source"] = {"url": url, "stats": generator.stats(res["model"])}
    return res
