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
    """Volatilidad alta tiende a favorecer la dirección existente de forma moderada.
    Usa z-score del precio + drawdown. Peso reducido (informativo) → clamp ±0.3."""
    try:
        z = ind.get("z_score_30d")
        dd = ind.get("max_drawdown_30d_pct")
        score = 0.0
        if z is not None:
            score += max(-0.25, min(0.25, z / 2.5))  # z ±2.5 → ±0.25
        if dd is not None and dd < -3:
            score -= 0.1  # drawdown pronunciado → leve sesgo bajista
        return max(-0.3, min(0.3, score))
    except Exception:
        return 0.0


def _positioning_score(funding_regime: dict, ls_ratio) -> float | None:
    """Solo aplica a crypto. Funding extremo positivo = bearish (riesgo squeeze).
    Devuelve None si no hay ningún dato de posicionamiento (no diluye)."""
    pct = None
    if funding_regime and "error" not in funding_regime:
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

    if pct is None and ls_score is None:
        return None
    if ls_score is None:
        return funding_score
    if pct is None:
        return ls_score
    return 0.7 * funding_score + 0.3 * ls_score


def _ls_ratio_score(ls: float | None) -> float | None:
    """Long/Short ratio: > 2 = crowded longs (bearish), < 0.5 = crowded shorts (bullish).
    None = sin dato (antes devolvía 0.0 y diluía el componente)."""
    if ls is None:
        return None
    try:
        ls = float(ls)
    except (TypeError, ValueError):
        return None
    if ls > 2.5:
        return -0.6
    if ls > 2.0:
        return -0.3
    if ls < 0.5:
        return +0.6
    if ls < 0.75:
        return +0.3
    return 0.0


def _sentiment_score(fng: dict) -> float | None:
    """Fear & Greed: 0=extreme fear (contrarian bullish), 100=extreme greed (contrarian bearish).
    None = sin dato (no diluye; el F&G de crypto no aplica a tradicionales)."""
    val = fng.get("value") if fng else None
    if val == "N/A" or val is None:
        return None
    try:
        v = int(val)
    except (TypeError, ValueError):
        return None
    # Mapeo lineal: 0..100 → +1..-1 (contrarian)
    return (50 - v) / 50.0


# ═══════════════════════════════════════════════════════
# AJUSTE DE PESOS POR RÉGIMEN / DATOS DISPONIBLES
# ═══════════════════════════════════════════════════════

def _move_weight(w: dict, dst: str, amount: int, src_keys: tuple) -> None:
    """Mueve hasta 'amount' puntos hacia dst desde src_keys, sin bajar de 5."""
    moved = 0
    for k in src_keys:
        if moved >= amount or k not in w:
            continue
        take = min(amount - moved, max(0, w[k] - 5))
        w[k] -= take
        moved += take
    if moved:
        w[dst] = w.get(dst, 0) + moved


def _redistribute_weight(w: dict, amount: int, dst_keys: tuple) -> None:
    """Reparte 'amount' puntos proporcionalmente entre dst_keys presentes."""
    present = [k for k in dst_keys if k in w]
    if not present or amount <= 0:
        return
    base = sum(w[k] for k in present)
    if base <= 0:
        for i, k in enumerate(present):
            w[k] += amount // len(present) + (1 if i < amount % len(present) else 0)
        return
    remaining = amount
    for i, k in enumerate(present):
        if i == len(present) - 1:
            share = remaining
        else:
            share = round(amount * w[k] / base)
            remaining -= share
        w[k] += share


def _adapt_weights(w: dict, indicators: dict) -> None:
    """Régimen por ADX: tendencia fuerte refuerza 'trend'; rango (ADX<15)
    lo reduce a favor de osciladores (momentum/volatilidad)."""
    adx = indicators.get("adx14")
    try:
        adx = float(adx) if adx is not None else None
    except (TypeError, ValueError):
        adx = None
    if adx is None:
        return
    if adx >= 30:
        _move_weight(w, "trend", 8, ("momentum", "sentiment", "volatility"))
    elif adx >= 25:
        _move_weight(w, "trend", 4, ("momentum", "sentiment"))
    elif adx < 15:
        _move_weight(w, "momentum", 4, ("trend",))
        _move_weight(w, "volatility", 2, ("trend",))


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

    - Los pesos base (WEIGHTS) se adaptan al régimen por ADX: tendencia fuerte
      (≥30) refuerza `trend`; rango (<15) lo reduce a favor de momentum/vol.
    - Componentes sin dato (positioning sin funding/L-S, sentiment sin F&G) NO
      diluyen el score: se eliminan y su peso se redistribuye.
    - Para activos NO crypto, positioning no existe (sin funding/L-S): su peso
      va a trend/momentum. El Fear & Greed de crypto no aplica a tradicionales.
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
        # El Fear & Greed disponible es el índice de CRYPTO: no aplica a
        # tradicionales (XAUUSD/SP500/…), aunque el caller lo pase.
        "sentiment":   _sentiment_score(fng or {}) if is_crypto else None,
    }
    if is_crypto:
        components["positioning"] = _positioning_score(funding_regime or {}, ls_ratio)

    # Componentes sin dato: fuera, y su peso se reparte (evita dilución)
    for key in ("sentiment", "positioning"):
        if key in components and components[key] is None:
            components.pop(key)
            _redistribute_weight(weights, weights.pop(key, 0),
                                 ("trend", "momentum", "volatility"))

    _adapt_weights(weights, indicators)

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
