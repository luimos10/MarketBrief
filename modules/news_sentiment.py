"""
╔══════════════════════════════════════════════════════╗
║       NEWS & SENTIMENT — CryptoPanic + Finnhub        ║
║  Agrega titulares con score básico (votes / pondera-  ║
║  ción heurística) últimas 12-24h.                     ║
╚══════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

from modules.cache import cached

logger = logging.getLogger("MarketBrief")


# ═══════════════════════════════════════════════════════
# CRYPTOPANIC
# ═══════════════════════════════════════════════════════

@cached("cmc_global")  # TTL 5min — news refresh moderado
def fetch_cryptopanic(api_key: str = "",
                      currencies: str = "BTC,ETH,SOL",
                      hours: int = 24) -> dict:
    """
    News scored de CryptoPanic. Endpoint free: 'Public Posts'.
    Sin key, intenta endpoint público sin auth (limitado).
    """
    out = {"source": "cryptopanic", "items": [], "info": ""}
    if not api_key:
        out["info"] = "CRYPTOPANIC_API_KEY no configurada — saltando"
        return out

    try:
        url = "https://cryptopanic.com/api/free/v1/posts/"
        params = {
            "auth_token": api_key,
            "currencies": currencies,
            "filter": "hot",
            "public": "true",
        }
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}", "source": "cryptopanic"}
        data = r.json().get("results", []) or []
    except Exception as e:
        return {"error": str(e), "source": "cryptopanic"}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    items = []
    for post in data:
        try:
            ts = datetime.fromisoformat(post["published_at"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ts < cutoff:
            continue
        votes = post.get("votes") or {}
        score = ((votes.get("positive") or 0)
                 - (votes.get("negative") or 0)
                 + (votes.get("important") or 0) * 2
                 + (votes.get("liked") or 0)
                 - (votes.get("disliked") or 0))
        items.append({
            "title": post.get("title", "")[:200],
            "url": post.get("url"),
            "domain": post.get("domain"),
            "published": ts.strftime("%Y-%m-%d %H:%M UTC"),
            "currencies": [c.get("code") for c in post.get("currencies", [])],
            "score": score,
            "kind": post.get("kind"),  # news|media
        })

    # Top 10 por score absoluto + recencia
    items.sort(key=lambda x: (abs(x["score"]), x["published"]), reverse=True)
    out["items"] = items[:10]
    out["total_recent"] = len(items)
    return out


# ═══════════════════════════════════════════════════════
# FINNHUB
# ═══════════════════════════════════════════════════════

@cached("cmc_global")
def fetch_finnhub_news(api_key: str = "", category: str = "general",
                       hours: int = 24) -> dict:
    """
    News macro/equity desde Finnhub. Free tier: 60 calls/min.
    """
    out = {"source": "finnhub", "items": [], "info": ""}
    if not api_key:
        out["info"] = "FINNHUB_API_KEY no configurada — saltando"
        return out

    try:
        r = requests.get(
            "https://finnhub.io/api/v1/news",
            params={"category": category, "token": api_key},
            timeout=10,
        )
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}", "source": "finnhub"}
        data = r.json() or []
    except Exception as e:
        return {"error": str(e), "source": "finnhub"}

    cutoff_ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
    items = []
    for art in data:
        ts = art.get("datetime")
        if not ts or ts < cutoff_ts:
            continue
        items.append({
            "headline": (art.get("headline") or "")[:200],
            "source": art.get("source"),
            "url": art.get("url"),
            "category": art.get("category"),
            "published": datetime.fromtimestamp(ts, tz=timezone.utc)
                                  .strftime("%Y-%m-%d %H:%M UTC"),
        })

    items.sort(key=lambda x: x["published"], reverse=True)
    out["items"] = items[:10]
    out["total_recent"] = len(items)
    return out


# ═══════════════════════════════════════════════════════
# AGGREGATE (lo que collect_all_data llama)
# ═══════════════════════════════════════════════════════

def fetch_news_aggregate(cryptopanic_key: str = "",
                         finnhub_key: str = "") -> dict:
    """Llama ambas fuentes y devuelve un dict combinado."""
    return {
        "crypto": fetch_cryptopanic(cryptopanic_key, hours=24),
        "macro":  fetch_finnhub_news(finnhub_key, category="general", hours=24),
    }
