"""
╔══════════════════════════════════════════════════════╗
║       OPTIONS FLOW — Deribit BTC options              ║
║  Put/Call ratio, Max Pain, Gamma exposure proxy.     ║
║  API pública sin key.                                 ║
╚══════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone

import requests

from modules.cache import cached

logger = logging.getLogger("MarketBrief")

DERIBIT_BASE = "https://www.deribit.com/api/v2/public"


# ═══════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════

def _parse_instrument(name: str) -> dict | None:
    """
    Parsea un nombre de instrumento Deribit estilo 'BTC-31MAY26-100000-C'.
    Devuelve dict con expiry (datetime), strike (float), kind ('C'|'P') o None.
    """
    try:
        parts = name.split("-")
        if len(parts) != 4:
            return None
        _, expiry_s, strike_s, kind = parts
        expiry = datetime.strptime(expiry_s, "%d%b%y").replace(tzinfo=timezone.utc)
        return {
            "expiry": expiry,
            "strike": float(strike_s),
            "kind": kind.upper(),
        }
    except Exception:
        return None


def _fetch_spot_price(currency: str = "BTC") -> float | None:
    """Spot index price desde Deribit."""
    try:
        r = requests.get(
            f"{DERIBIT_BASE}/get_index_price",
            params={"index_name": f"{currency.lower()}_usd"},
            timeout=8,
        )
        if r.status_code == 200:
            return float(r.json()["result"]["index_price"])
    except Exception as e:
        logger.warning(f"  Deribit spot: {e}")
    return None


# ═══════════════════════════════════════════════════════
# MAIN FETCHER
# ═══════════════════════════════════════════════════════

@cached("crypto_ohlcv")  # TTL 5min — options OI mueve lento
def fetch_btc_options_flow() -> dict:
    """
    Obtiene métricas de options BTC desde Deribit:
      - put/call OI ratio (agregado)
      - max pain del expiry más cercano
      - gamma flip proxy (strike con mayor OI cerca del spot)
    """
    try:
        r = requests.get(
            f"{DERIBIT_BASE}/get_book_summary_by_currency",
            params={"currency": "BTC", "kind": "option"},
            timeout=15,
        )
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        data = r.json().get("result", [])
        if not data:
            return {"error": "Deribit sin datos"}
    except Exception as e:
        return {"error": str(e)}

    spot = _fetch_spot_price("BTC")
    if spot is None:
        return {"error": "no se pudo obtener spot BTC"}

    # ── 1) Agregados globales (todos los expiries) ──
    total_call_oi = 0.0
    total_put_oi = 0.0
    by_expiry = defaultdict(lambda: {"strikes": defaultdict(lambda: {"C": 0.0, "P": 0.0})})

    for inst in data:
        info = _parse_instrument(inst.get("instrument_name", ""))
        if not info:
            continue
        oi = float(inst.get("open_interest") or 0.0)
        if oi <= 0:
            continue
        if info["kind"] == "C":
            total_call_oi += oi
        else:
            total_put_oi += oi
        exp_key = info["expiry"].strftime("%Y-%m-%d")
        by_expiry[exp_key]["strikes"][info["strike"]][info["kind"]] += oi

    pc_ratio = (total_put_oi / total_call_oi) if total_call_oi > 0 else None

    # ── 2) Próximo expiry mayor o igual a hoy ──
    today = datetime.now(timezone.utc).date()
    upcoming = sorted([
        (datetime.strptime(k, "%Y-%m-%d").date(), k)
        for k in by_expiry.keys()
        if datetime.strptime(k, "%Y-%m-%d").date() >= today
    ])
    if not upcoming:
        return {
            "spot": spot,
            "global_call_oi": total_call_oi,
            "global_put_oi": total_put_oi,
            "global_pc_ratio": round(pc_ratio, 2) if pc_ratio else None,
            "error_next_expiry": "no hay expiries futuros",
        }

    next_exp_date, next_exp_key = upcoming[0]
    strikes = by_expiry[next_exp_key]["strikes"]

    # ── 3) Max pain del próximo expiry ──
    # Max pain = strike donde el dolor agregado (OI ponderado por distancia) es mínimo
    # para el lado de los compradores de opciones = donde los emisores (dealers) ganan más
    strike_list = sorted(strikes.keys())
    if not strike_list:
        max_pain = None
    else:
        pain = {}
        for k in strike_list:
            total = 0.0
            for s, oi_map in strikes.items():
                if s < k:
                    # calls ITM → loss para sellers
                    total += oi_map["C"] * (k - s)
                if s > k:
                    # puts ITM
                    total += oi_map["P"] * (s - k)
            pain[k] = total
        max_pain = min(pain, key=pain.get)

    # ── 4) Top OI cerca del spot (proxy de gamma flip) ──
    # Strikes dentro de ±10% del spot, ordenados por OI total
    band = 0.10 * spot
    nearby = [
        (s, m["C"] + m["P"], m["C"], m["P"])
        for s, m in strikes.items()
        if abs(s - spot) <= band
    ]
    nearby.sort(key=lambda x: x[1], reverse=True)
    top_oi_strikes = [
        {
            "strike": s, "total_oi": round(total, 1),
            "call_oi": round(c, 1), "put_oi": round(p, 1),
            "vs_spot_pct": round((s - spot) / spot * 100, 2),
        }
        for s, total, c, p in nearby[:5]
    ]

    # ── 5) PC ratio del próximo expiry ──
    next_call_oi = sum(m["C"] for m in strikes.values())
    next_put_oi = sum(m["P"] for m in strikes.values())
    next_pc = (next_put_oi / next_call_oi) if next_call_oi > 0 else None

    # ── 6) Interpretación cualitativa ──
    interpretation = []
    if pc_ratio is not None:
        if pc_ratio > 1.0:
            interpretation.append("PC ratio > 1 (defensivo, más puts que calls)")
        elif pc_ratio < 0.6:
            interpretation.append("PC ratio bajo (calls dominantes, sentiment alcista)")
    if max_pain is not None:
        diff_pct = (max_pain - spot) / spot * 100
        if abs(diff_pct) < 2:
            interpretation.append(
                f"Spot pegado a max pain ({max_pain:.0f}) — riesgo pin day")
        elif diff_pct > 5:
            interpretation.append(
                f"Max pain {max_pain:.0f} ({diff_pct:+.1f}%) por encima del spot — gravedad alcista hacia expiry")
        elif diff_pct < -5:
            interpretation.append(
                f"Max pain {max_pain:.0f} ({diff_pct:+.1f}%) por debajo del spot — gravedad bajista hacia expiry")

    return {
        "spot": round(spot, 2),
        "global_call_oi": round(total_call_oi, 1),
        "global_put_oi": round(total_put_oi, 1),
        "global_pc_ratio": round(pc_ratio, 2) if pc_ratio else None,
        "next_expiry": next_exp_key,
        "next_expiry_call_oi": round(next_call_oi, 1),
        "next_expiry_put_oi": round(next_put_oi, 1),
        "next_expiry_pc_ratio": round(next_pc, 2) if next_pc else None,
        "max_pain": max_pain,
        "max_pain_vs_spot_pct":
            round((max_pain - spot) / spot * 100, 2) if max_pain else None,
        "top_oi_strikes_near_spot": top_oi_strikes,
        "interpretation": interpretation,
    }
