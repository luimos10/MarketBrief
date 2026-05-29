"""
╔══════════════════════════════════════════════════════╗
║         FRED — Macro liquidez + yield curve           ║
║  Series clave de la Federal Reserve (St. Louis Fed). ║
║  Free key en https://fred.stlouisfed.org/docs/api/    ║
╚══════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import requests

from modules.cache import cached

logger = logging.getLogger("MarketBrief")

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# Series clave para macro liquidity y yield curve regime
SERIES = {
    "T10Y2Y":    "spread_10y_2y",    # 10Y-2Y spread (inversion = recession signal)
    "RRPONTSYD": "rrp_balance",      # Overnight reverse repo balance (Fed drain)
    "WTREGEN":   "tga_balance",      # Treasury General Account
    "DFF":       "fed_funds_rate",   # Effective Fed funds rate
    "DGS10":     "us10y_yield",      # 10Y treasury yield (validation)
    "DGS2":      "us2y_yield",       # 2Y treasury yield (validation)
}


def _fetch_series(series_id: str, api_key: str, limit: int = 14) -> list[dict]:
    """Pide las últimas N observaciones de una serie FRED."""
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    }
    r = requests.get(FRED_BASE, params=params, timeout=15)
    r.raise_for_status()
    return r.json().get("observations", [])


def _parse_value(v) -> float | None:
    """FRED usa '.' para missing. Devuelve float o None."""
    if v in (None, "", "."):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _latest_and_delta(obs: list[dict], days_back: int = 7) -> dict:
    """De una serie FRED ordenada desc, extrae último valor + delta vs ~7 días."""
    if not obs:
        return {"latest": None, "delta_7d": None, "date": None}
    latest_val = _parse_value(obs[0].get("value"))
    latest_date = obs[0].get("date")
    delta = None
    cutoff = datetime.strptime(latest_date, "%Y-%m-%d") - timedelta(days=days_back)
    for o in obs[1:]:
        try:
            d = datetime.strptime(o.get("date", ""), "%Y-%m-%d")
        except Exception:
            continue
        if d <= cutoff:
            old_val = _parse_value(o.get("value"))
            if old_val is not None and latest_val is not None:
                delta = round(latest_val - old_val, 4)
            break
    return {"latest": latest_val, "delta_7d": delta, "date": latest_date}


@cached("macro_calendar")
def fetch_fred_macro(api_key: str = "") -> dict:
    """
    Obtiene un snapshot compacto de macro liquidity + yield curve desde FRED.

    Retorna:
      {
        "yield_curve": { "spread_10y_2y": ..., "us10y": ..., "us2y": ...,
                         "inverted": bool, "days_inverted_recent": int },
        "liquidity":   { "rrp_balance": ..., "rrp_delta_7d": ...,
                         "tga_balance": ..., "tga_delta_7d": ...,
                         "fed_funds_rate": ... },
        "interpretation": [ "...", "..." ]
      }
    """
    if not api_key:
        return {
            "info": "FRED_API_KEY no configurada — sección macro Fed se omitirá.",
            "yield_curve": {},
            "liquidity": {},
            "interpretation": [],
        }

    raw = {}
    for series_id, alias in SERIES.items():
        try:
            obs = _fetch_series(series_id, api_key)
            raw[alias] = _latest_and_delta(obs)
        except Exception as e:
            logger.warning(f"  FRED {series_id}: {e}")
            raw[alias] = {"latest": None, "delta_7d": None, "date": None}

    # Inversion detection: si T10Y2Y < 0
    spread = raw.get("spread_10y_2y", {}).get("latest")
    inverted = spread is not None and spread < 0

    # Contar cuántos días recientes consecutivos en inversión (de las 14
    # obs que pedimos)
    days_inverted = 0
    try:
        obs_t10 = _fetch_series("T10Y2Y", api_key, limit=14)
        for o in obs_t10:
            v = _parse_value(o.get("value"))
            if v is not None and v < 0:
                days_inverted += 1
            else:
                break
    except Exception:
        pass

    yield_curve = {
        "spread_10y_2y": spread,
        "spread_date": raw.get("spread_10y_2y", {}).get("date"),
        "us10y": raw.get("us10y_yield", {}).get("latest"),
        "us2y":  raw.get("us2y_yield", {}).get("latest"),
        "inverted": inverted,
        "days_inverted_recent": days_inverted,
    }

    rrp = raw.get("rrp_balance", {})
    tga = raw.get("tga_balance", {})
    liquidity = {
        "rrp_balance_billions":  round((rrp.get("latest") or 0), 1),
        "rrp_delta_7d_billions": rrp.get("delta_7d"),
        "tga_balance_billions":  round((tga.get("latest") or 0), 1),
        "tga_delta_7d_billions": tga.get("delta_7d"),
        "fed_funds_rate":        raw.get("fed_funds_rate", {}).get("latest"),
    }

    # Interpretaciones cualitativas
    interp = []
    if inverted:
        interp.append(
            f"Curva 10-2 INVERTIDA ({spread:+.2f}, ~{days_inverted}d "
            "consecutivos) — señal histórica de recesión a 12-18m."
        )
    elif spread is not None and spread < 0.25:
        interp.append(
            f"Curva 10-2 muy plana ({spread:+.2f}) — riesgo de inversión próxima."
        )
    elif spread is not None:
        interp.append(f"Curva 10-2 positiva ({spread:+.2f}) — sin señal de recesión.")

    rrp_delta = liquidity.get("rrp_delta_7d_billions")
    if rrp_delta is not None:
        if rrp_delta < -50:
            interp.append(
                f"RRP cayó {abs(rrp_delta):.0f}B en 7d — liquidez "
                "saliendo del Fed hacia mercados (bullish risk-on)."
            )
        elif rrp_delta > 50:
            interp.append(
                f"RRP subió {rrp_delta:.0f}B en 7d — drenaje de liquidez "
                "(bearish risk-on)."
            )

    tga_delta = liquidity.get("tga_delta_7d_billions")
    if tga_delta is not None:
        if tga_delta < -50:
            interp.append(
                f"TGA cayó {abs(tga_delta):.0f}B en 7d — Tesoro gastando, "
                "liquidez entrando a mercados."
            )
        elif tga_delta > 50:
            interp.append(
                f"TGA subió {tga_delta:.0f}B en 7d — Tesoro acumulando, "
                "drenaje de liquidez."
            )

    return {
        "yield_curve": yield_curve,
        "liquidity": liquidity,
        "interpretation": interp,
        "source": "FRED (St. Louis Fed)",
    }
