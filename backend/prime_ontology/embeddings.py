"""Semantic embeddings for concept/field similarity.

Providers (PRIME_ONTOLOGY_EMBEDDINGS = auto | fastembed | hash | off):
  * fastembed — a real sentence-embedding model (BAAI/bge-small-en-v1.5, ONNX, runs locally on CPU, ~130 MB, downloaded once
    to PRIME_ONTOLOGY_MODEL_DIR). Understands that "client" ~ "customer" without a synonym list.
  * hash      — dependency-free character n-gram vectors. Captures spelling/morphology similarity only. Deterministic;
    used as the automatic fallback when the model cannot be loaded (no network on first run, policy-blocked, ...).
  * off       — disabled (callers fall back to purely lexical matching).
`status()` reports which provider is really in use, so the UI/ops never claim semantic matching when it is not running.
"""
import hashlib
import math
import os
import threading
from collections import OrderedDict

from django.conf import settings

MODEL_NAME = "BAAI/bge-small-en-v1.5"
_lock = threading.Lock()
_fallback_reason = None
_cache: "OrderedDict[str, list[float]]" = OrderedDict()
CACHE_MAX = 20000


class HashEmbedder:
    name = "hash"
    dim = 384
    # raw cosine for unrelated short strings is ~0.0-0.2, for near-identical ~0.8+
    lo, hi = 0.25, 0.85

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * self.dim
            s = f" {t.lower().strip()} "
            for n in (3, 4):
                for i in range(len(s) - n + 1):
                    h = int(hashlib.md5(s[i:i + n].encode()).hexdigest()[:8], 16)
                    v[h % self.dim] += 1.0 if (h >> 31) & 1 else -1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


class FastEmbedder:
    name = "fastembed"
    dim = 384
    lo, hi = 0.60, 0.90  # bge-small gives compressed cosines: unrelated ~0.55-0.65, synonyms ~0.8+

    def __init__(self):
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from fastembed import TextEmbedding

        cache = os.environ.get("PRIME_ONTOLOGY_MODEL_DIR") or str(getattr(settings, "BASE_DIR", ".") / ".model_cache")
        self.model = TextEmbedding(MODEL_NAME, cache_dir=cache)

    def embed(self, texts):
        out = []
        for v in self.model.embed(list(texts)):
            norm = math.sqrt(sum(float(x) * float(x) for x in v)) or 1.0
            out.append([float(x) / norm for x in v])
        return out


def _mode():
    return os.environ.get("PRIME_ONTOLOGY_EMBEDDINGS", getattr(settings, "PRIME_ONTOLOGY_EMBEDDINGS", "auto")).lower()


_hash = HashEmbedder()
_fast = None
_fast_state = "idle"  # idle | loading | ready | failed
_fast_done = threading.Event()


def _load_fast():
    global _fast, _fast_state, _fallback_reason
    try:
        f = FastEmbedder()  # first run downloads the model (~130 MB); later runs read it from the local cache
        with _lock:
            _fast, _fast_state = f, "ready"
            _cache.clear()
    except Exception as e:  # missing package, offline first run, blocked DLL ...
        with _lock:
            _fallback_reason = f"{type(e).__name__}: {str(e)[:160]}"
            _fast_state = "failed"
    finally:
        _fast_done.set()


def warm_up():
    """Start loading the embedding model in the background (idempotent). Called at server start so the first user never waits."""
    global _fast_state
    with _lock:
        if _fast_state != "idle":
            return
        _fast_state = "loading"
        _fast_done.clear()
    threading.Thread(target=_load_fast, daemon=True, name="embedding-warmup").start()


def get_provider():
    """The provider to use right now. In 'auto' mode a request NEVER waits for the model: until it is loaded (or if it cannot be
    loaded) the lexical hash provider is returned; 'fastembed' mode waits (used by tests/ops that insist on the model)."""
    mode = _mode()
    if mode == "off":
        return None
    if mode == "hash":
        return _hash
    warm_up()
    if _fast_state == "loading":
        if mode == "fastembed":
            _fast_done.wait()
        else:
            return _hash
    return _fast if _fast_state == "ready" else _hash


def reset():
    global _fast, _fast_state, _fallback_reason
    with _lock:
        _fast, _fast_state, _fallback_reason = None, "idle", None
        _fast_done.clear()
        _cache.clear()


def status() -> dict:
    p = get_provider()
    return {"mode": _mode(), "provider": p.name if p else "off", "semantic": bool(p and p.name == "fastembed"),
            "model": MODEL_NAME if p and p.name == "fastembed" else None, "fallbackReason": _fallback_reason,
            "warming": _mode() in ("auto", "fastembed") and _fast_state == "loading"}


def embed(texts: list[str]) -> list[list[float]]:
    p = get_provider()
    if p is None:
        raise RuntimeError("embeddings disabled")
    key = lambda t: f"{p.name}|{t}"
    missing = [t for t in dict.fromkeys(texts) if key(t) not in _cache]
    if missing:
        for t, v in zip(missing, p.embed(missing)):
            _cache[key(t)] = v
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    return [_cache[key(t)] for t in texts]


def cosine(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def similarity(a_text: str, b_text: str) -> float:
    """Calibrated 0..1 similarity (0 = unrelated, 1 = same meaning). Returns 0.0 when embeddings are off."""
    p = get_provider()
    if p is None:
        return 0.0
    va, vb = embed([a_text, b_text])
    return max(0.0, min(1.0, (cosine(va, vb) - p.lo) / (p.hi - p.lo)))


def similarities(query: str, texts: list[str]) -> list[float]:
    p = get_provider()
    if p is None or not texts:
        return [0.0] * len(texts)
    vs = embed([query] + texts)
    q = vs[0]
    return [max(0.0, min(1.0, (cosine(q, v) - p.lo) / (p.hi - p.lo))) for v in vs[1:]]
