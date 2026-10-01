"""Encryption of connector credentials at rest (Fernet = AES-128-CBC + HMAC-SHA256, authenticated).

Key: PRIME_ONTOLOGY_SECRET_KEY (env/settings) or, failing that, Django's SECRET_KEY, hashed to 32 bytes. Changing the key makes stored
credentials undecryptable (re-enter them) — rotate deliberately. Credentials are NEVER returned by the API or written to logs/audit.
"""
import base64
import hashlib
import json
import os

from django.conf import settings


class SecretError(ValueError):
    pass


def _fernet():
    from cryptography.fernet import Fernet

    raw = os.environ.get("PRIME_ONTOLOGY_SECRET_KEY") or getattr(settings, "PRIME_ONTOLOGY_SECRET_KEY", None) or settings.SECRET_KEY
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def encrypt_json(data: dict) -> str:
    if not data:
        return ""
    return _fernet().encrypt(json.dumps(data, sort_keys=True).encode()).decode()


def decrypt_json(token: str) -> dict:
    if not token:
        return {}
    from cryptography.fernet import InvalidToken

    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as e:
        raise SecretError("Stored credentials cannot be decrypted (the server secret key changed). Re-enter the credentials.") from e


def redact(text: str, secrets: dict) -> str:
    """Remove any credential value from a string (error messages must never leak secrets)."""
    out = str(text)
    for v in (secrets or {}).values():
        if isinstance(v, str) and len(v) >= 4:
            out = out.replace(v, "***")
    return out
