"""Value normalisers for identity matching (deterministic entity resolution)."""
import re
import unicodedata

_PUNCT = re.compile(r"[^\w\s]", re.U)
_WS = re.compile(r"\s+")
COMPANY_SUFFIX = re.compile(r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|gmbh|ag|sa|plc|pvt|private|pte|bv|nv|oy|ab|llp|lp)\b\.?", re.I)


def _s(v) -> str:
    return "" if v is None else str(v)


def n_text(v) -> str:
    s = unicodedata.normalize("NFKD", _s(v)).encode("ascii", "ignore").decode()
    return _WS.sub(" ", _PUNCT.sub(" ", s.casefold())).strip()


def n_email(v) -> str:
    s = _s(v).strip().casefold()
    return s if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s) else ""


def n_digits(v) -> str:
    return re.sub(r"\D", "", _s(v))


def n_phone(v) -> str:
    d = n_digits(v)
    d = d.lstrip("0")
    return d[-10:] if len(d) > 10 else d if len(d) >= 7 else ""


def n_identifier(v) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", _s(v)).upper()


def n_company(v) -> str:
    return _WS.sub(" ", COMPANY_SUFFIX.sub(" ", n_text(v))).strip()


NORMALIZERS = {"text": n_text, "email": n_email, "digits": n_digits, "phone": n_phone, "identifier": n_identifier, "company": n_company,
               "none": lambda v: _s(v).strip()}


def normalize(kind: str, value) -> str:
    return NORMALIZERS.get(kind or "text", n_text)(value)
