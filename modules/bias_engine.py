"""
╔══════════════════════════════════════════════════════╗
║         BIAS ENGINE — Score numérico de sesgo         ║
║  Convierte indicadores + posicionamiento en un       ║
║  score -100 (muy bajista) .. +100 (muy alcista)      ║
╚══════════════════════════════════════════════════════╝

Filosofía: el LLM ya no inventa el sesgo, lo justifica.
Cada componente tiene peso explícito y trazable.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("MarketBrief")


# ═══════════════════════════════════════════════════════
# PESOS (suma = 100)
# ═══════════════════════════════════════════════════════
WEIGHTS = {
    "trend":       30,   # EMA alignment + posición vs EMA200
    "momentum":    25,   # RSI + MACD
    "volatility": 10,    # ATR pct (informativo, no direccional)
    "positioning": 20,   # Funding regime + L/S ratio (solo crypto)
    "sentiment":   15,   # Fear & Greed
}


# ═══════════════════════════════════════════════════════
# SUB-COMPONENTES (cada uno devuelve -1..+1)
# ═══════════════════════════════════════════════════════

def _trend_score(ind: dict) -> float:
    align = ind.get("ema_alignment", "mixto")
    pct_vs_ema200 = ind.get("price_vs_ema200_pct")

    align_score = {"alcista": 1.0, "bajista": -1.0, "mixto": 0.0}.get(align, 0.0)

    if pct_vs_ema200 is None:
        dist_score = 0.0
    else:
        # Clamp en ±15% (un activo 15% sobre EMA200 = trend muy fuerte)
        dist_score = max(-1.0, min(1.0, pct_vs_ema200 / 15.0))

    return 0.6 * align_score + 0.4 * dist_score


def _momentum_score(ind: dict) -> float:
    rsi_v = ind.get("rsi14")
    macd_state = ind.get("macd_state", "")

    # RSI: 50 = neutral, 70 = sobrecompra, 30 = sobreventa
    if rsi_v is None:
        rsi_score = 0.0
    else:
        # Mapear 30..70 → -1..+1, clamp fuera
        rsi_score = max(-1.0, min(1.0, (rsi_v - 50) / 20.0))

    macd_score = {
        "alcista expandiendo": 1.0,
        "alcista contrayendo": 0.3,
        "bajista contrayendo": -0.3,
        "bajista expandiendo": -1.0,
    }.get(macd_state, 0.0)

    return 0.5 * rsi_score + 0.5 * macd_score


def _volatility_score(ind: dict) -> float:
    """Volatilidad alta no es alcista/bajista — devuelve 0 (componente informativo)."""
    return 0.0


def _positioning_score(funding_regime: dict, ls_ratio: float | None) -> float:
    """Solo aplica a crypto. Funding extremo positivo = bearish (riesgo squeeze)."""
    if not funding_regime or "error" in funding_regime:
        ls_only = _ls_ratio_score(ls_ratio)
        return ls_only

    pct = funding_regime.get("percentil_90d")
    funding_score = 0.0
    if pct is not None:
        if pct > 90:
            funding_score = -0.8  # crowded longs → bearish edge
        elif pct < 10:
            funding_score = +0.8  # crowded shorts → bullish edge
        elif pct > 75:
            funding_score = -0.4
        elif pct < 25:
            funding_score = +0.4

    ls_score = _ls_ratio_score(ls_ratio)
    return 0.7 * funding_score + 0.3 * ls_score


def _ls_ratio_score(ls: float | None) -> float:
    """Long/Short ratio: > 2 = crowded longs (bearish), < 0.5 = crowded shorts (bullish)."""
    if ls is None:
        return 0.0
    try:
        ls = float(ls)
    except (TypeError, ValueError):
        return 0.0
    if ls > 2.5:
        return -0.6
    if ls > 2.0:
        return -0.3
    if ls < 0.5:
        return +0.6
    if ls < 0.75:
        return +0.3
    return 0.0


def _sentiment_score(fng: dict) -> float:
    """Fear & Greed: 0=extreme fear (contrarian bullish), 100=extreme greed (contrarian bearish)."""
    val = fng.get("value") if fng else None
    if val == "N/A" or val is None:
        return 0.0
    try:
        v = int(val)
    except (TypeError, ValueError):
        return 0.0
    # Mapeo lineal: 0..100 → +1..-1 (contrarian)
    return (50 - v) / 50.0


# ═══════════════════════════════════════════════════════
# API PÚBLICA
# ═══════════════════════════════════════════════════════

def compute_bias(indicators: dict,
                 funding_regime: dict | None = None,
                 ls_ratio: float | None = None,
                 fng: dict | None = None,
                 is_crypto: bool = True) -> dict:
    """
    Devuelve un score -100..+100 con el desglose por componente.

    Para activos NO crypto, el peso de positioning se redistribuye en
    trend (10) y momentum (10).
    """
    if "error" in indicators:
        return {"score": 0, "error": indicators["error"]}

    weights = dict(WEIGHTS)
    if not is_crypto:
        # No tenemos funding ni L/S para tradicionales — redistribuir peso
        extra = weights.pop("positioning", 0)
        weights["trend"] += extra // 2
        weights["momentum"] += extra - (extra // 2)

    components = {
        "trend":       _trend_score(indicators),
        "momentum":    _momentum_score(indicators),
        "volatility":  _volatility_score(indicators),
        "sentiment":   _sentiment_score(fng or {}),
    }
    if is_crypto:
        components["positioning"] = _positioning_score(funding_regime or {}, ls_ratio)

    score = sum(weights[k] * components[k] for k in weights if k in components)
    score = max(-100, min(100, round(score)))

    # Label cualitativo
    if score >= 60:
        label = "Muy alcista"
    elif score >= 25:
        label = "Alcista"
    elif score >= -25:
        label = "Neutral"
    elif score >= -60:
        label = "Bajista"
    else:
        label = "Muy bajista"

    return {
        "score": int(score),
        "label": label,
        "components": {k: round(v, 2) for k, v in components.items()},
        "weights": weights,
    }
