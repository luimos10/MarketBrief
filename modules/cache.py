"""
╔══════════════════════════════════════════════════════╗
║              MÓDULO DE CACHÉ TTL                      ║
║  Cache en disco con expiración por categoría.         ║
║  Backend: diskcache si está disponible, fallback a    ║
║  un cache local en memoria + JSON.                    ║
╚══════════════════════════════════════════════════════╝
"""

import hashlib
import json
import logging
import os
import time
from functools import wraps

logger = logging.getLogger("MarketBrief")

# ═══════════════════════════════════════════════════════
# CONFIGURACIÓN DE TTL POR CATEGORÍA (segundos)
# ═══════════════════════════════════════════════════════
TTL = {
    "yfinance_daily":     60 * 60,        # 1h — velas diarias
    "crypto_realtime":    60,             # 1 min — ticker/funding/OI
    "crypto_ohlcv":       60 * 5,         # 5 min — velas crypto
    "cmc_global":         60 * 5,         # 5 min
    "fear_greed":         60 * 60 * 4,    # 4h
    "liquidations":       60 * 5,         # 5 min
    "funding_history":    60 * 30,        # 30 min
    "macro_calendar":     60 * 60 * 6,    # 6h
}

# ═══════════════════════════════════════════════════════
# BACKEND
# ═══════════════════════════════════════════════════════
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_DIR = os.path.join(_BASE_DIR, ".cache")
os.makedirs(_CACHE_DIR, exist_ok=True)

try:
    import diskcache  # type: ignore
    _cache = diskcache.Cache(_CACHE_DIR)
    _BACKEND = "diskcache"
except Exception:
    _cache = None
    _BACKEND = "json-fallback"


def _fallback_path(key: str) -> str:
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return os.path.join(_CACHE_DIR, f"{h}.json")


def _get(key: str):
    """Get value if not expired. Returns (hit, value)."""
    if _cache is not None:
        try:
            value = _cache.get(key, default=None)
            if value is not None:
                return True, value
        except Exception as e:
            logger.warning(f"[cache] backend get error: {e}")
        return False, None

    path = _fallback_path(key)
    if not os.path.exists(path):
        return False, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            entry = json.load(f)
        if entry.get("expires_at", 0) < time.time():
            return False, None
        return True, entry.get("value")
    except Exception:
        return False, None


def _set(key: str, value, ttl: int):
    """Store value with TTL (seconds)."""
    if _cache is not None:
        try:
            _cache.set(key, value, expire=ttl)
            return
        except Exception as e:
            logger.warning(f"[cache] backend set error: {e}")

    path = _fallback_path(key)
    try:
        entry = {"expires_at": time.time() + ttl, "value": value}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f, default=str)
    except Exception as e:
        logger.warning(f"[cache] fallback set error: {e}")


def _make_key(prefix: str, args: tuple, kwargs: dict) -> str:
    """Build a stable string key from function args."""
    try:
        payload = json.dumps(
            {"a": list(args), "k": kwargs},
            sort_keys=True,
            default=str,
        )
    except Exception:
        payload = repr((args, kwargs))
    return f"{prefix}::{payload}"


def cached(category: str, prefix: str = None):
    """
    Decorador con TTL por categoría.
    Uso:
        @cached("crypto_realtime")
        def fetch_btc_ticker(...): ...
    """
    ttl = TTL.get(category, 300)

    def decorator(fn):
        nonlocal prefix
        key_prefix = prefix or f"{fn.__module__}.{fn.__name__}"

        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = _make_key(key_prefix, args, kwargs)
            hit, value = _get(key)
            if hit:
                logger.info(
                    f"  [cache HIT] {key_prefix} (category={category})")
                return value
            value = fn(*args, **kwargs)
            try:
                _set(key, value, ttl)
            except Exception as e:
                logger.warning(f"[cache] no se pudo guardar {key_prefix}: {e}")
            return value

        return wrapper

    return decorator


def cache_info() -> dict:
    """Diagnóstico del backend de caché."""
    return {
        "backend": _BACKEND,
        "dir": _CACHE_DIR,
        "ttl_config": TTL,
    }
