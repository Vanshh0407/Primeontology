"""HTTP client for enterprise connectors (SAP OData, Salesforce, Odoo, REST).

SSRF / abuse controls (same philosophy as url_source.py, but for connectors an admin configures):
  * disabled unless an administrator lists the exact hosts in settings.PRIME_ONTOLOGY_CONNECTOR_HOSTS
    (falls back to PRIME_ONTOLOGY_ALLOWED_URL_HOSTS);
  * https only (http only when PRIME_ONTOLOGY_ALLOW_HTTP_URLS is set, e.g. for local mocks);
  * redirects are refused, no credentials inside URLs, 30 MB / 30 s caps;
  * error messages never contain credentials.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

MAX_BYTES = 30 * 1024 * 1024
TIMEOUT = 30


class ConnectorError(Exception):
    """A problem talking to a source system. The message is safe to show to users."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def allowed_hosts() -> list[str]:
    hosts = getattr(settings, "PRIME_ONTOLOGY_CONNECTOR_HOSTS", None) or getattr(settings, "PRIME_ONTOLOGY_ALLOWED_URL_HOSTS", None) or []
    return [h.lower() for h in hosts]


def check_url(url: str) -> urllib.parse.ParseResult:
    u = urllib.parse.urlparse(url)
    if not allowed_hosts():
        raise ConnectorError("Connectors to external systems are disabled on this server "
                             "(an administrator must set PRIME_ONTOLOGY_CONNECTOR_HOSTS).")
    if u.scheme not in ("https", "http") or (u.scheme == "http" and not getattr(settings, "PRIME_ONTOLOGY_ALLOW_HTTP_URLS", False)):
        raise ConnectorError("Only https URLs are allowed.")
    if (u.hostname or "").lower() not in allowed_hosts():
        raise ConnectorError(f"Host '{u.hostname}' is not in this server's connector allow-list.")
    if u.username or u.password:
        raise ConnectorError("Credentials in the URL are not allowed.")
    return u


def request(method: str, url: str, *, headers: dict | None = None, params: dict | None = None, body=None, form: dict | None = None,
            secrets: dict | None = None) -> tuple[int, dict, bytes]:
    """Returns (status, headers, body). Raises ConnectorError (HTTP >= 400 is an error)."""
    from .crypto import redact

    check_url(url)
    # everything sensitive that we SEND must never come back to the user in an error message, in any encoding
    sensitive = dict(secrets or {})
    for hk, hv in (headers or {}).items():
        if hk.lower() in ("authorization", "cookie", "x-api-key", "api-key") or "token" in hk.lower() or "secret" in hk.lower():
            sensitive[f"h:{hk}"] = hv
            if hv.startswith(("Basic ", "Bearer ")):
                sensitive[f"h:{hk}:v"] = hv.split(" ", 1)[1]
                if hv.startswith("Basic "):
                    try:
                        import base64

                        dec = base64.b64decode(hv[6:]).decode()
                        sensitive[f"h:{hk}:dec"] = dec
                        sensitive[f"h:{hk}:pw"] = dec.split(":", 1)[-1]
                    except Exception:  # noqa: BLE001
                        pass
    if params:
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode(params, safe="$,()'", quote_via=urllib.parse.quote)
        check_url(url)
    data = None
    h = {"Accept": "application/json", "User-Agent": "prime-ontology/1.0", **(headers or {})}
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            raw = r.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ConnectorError("The response is larger than 30 MB.")
            return r.status, dict(r.headers), raw
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read(300).decode("utf-8", "replace")
        except Exception:
            pass
        msg = f"The system answered HTTP {e.code}" + (" (redirects are not followed)" if 300 <= e.code < 400 else "")
        raise ConnectorError(redact(f"{msg}. {detail}".strip(), sensitive)) from None
    except urllib.error.URLError as e:
        raise ConnectorError(redact(f"Cannot reach the system: {e.reason}", sensitive)) from None
    except TimeoutError:
        raise ConnectorError("The request timed out.") from None


def json_request(method: str, url: str, **kw):
    status, hdrs, raw = request(method, url, **kw)
    try:
        return json.loads(raw or b"null")
    except json.JSONDecodeError as e:
        raise ConnectorError(f"The system did not return valid JSON: {e}") from None
