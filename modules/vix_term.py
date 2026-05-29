"""
╔══════════════════════════════════════════════════════╗
║       VIX TERM STRUCTURE — Régimen de volatilidad     ║
║  Contango (calma) vs backwardation (stress).         ║
║  Calculado desde data yfinance ya recolectada.       ║
╚══════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging

logger = logging.getLogger("MarketBrief")


def _price(yf_data: dict, key: str) -> float | None:
    """Extrae precio_actual de la sección traditional_markets de un asset."""
    asset = yf_data.get(key)
    if not isinstance(asset, dict) or "error" in asset:
        return None
    try:
        v = asset.get("precio_actual")
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def compute_vix_term_structure(traditional_markets: dict) -> dict:
    """
    Calcula la estructura de plazos del VIX a partir de los precios
    intraday ya recolectados.

      VIX9D / VIX  < 1.0 = contango corto plazo (mercado tranquilo)
      VIX9D / VIX  > 1.0 = backwardation (stress inmediato)
      VIX  / VIX3M < 1.0 = contango intermedio (calma)
      VIX  / VIX3M > 1.0 = backwardation intermedia (stress más persistente)
      VVIX > 110           = pánico sobre pánico

    Args:
        traditional_markets: dict con datos de yfinance (consolidated["traditional_markets"]).
    """
    vix    = _price(traditional_markets, "VIX")
    vix9d  = _price(traditional_markets, "VIX9D")
    vix3m  = _price(traditional_markets, "VIX3M")
    vvix   = _price(traditional_markets, "VVIX")

    if vix is None:
        return {"error": "VIX no disponible"}

    out: dict = {
        "vix": vix,
        "vix9d": vix9d,
        "vix3m": vix3m,
        "vvix": vvix,
    }

    if vix9d is not None and vix > 0:
        out["ratio_9d_vs_vix"] = round(vix9d / vix, 3)
    if vix3m is not None and vix3m > 0:
        out["ratio_vix_vs_3m"] = round(vix / vix3m, 3)

    # Etiqueta cualitativa por la curva completa
    label = "neutral"
    short_stress = bool(vix9d and vix9d > vix * 1.05)
    inter_stress = bool(vix3m and vix > vix3m * 1.05)
    short_calma = bool(vix9d and vix9d < vix * 0.93)
    inter_calma = bool(vix3m and vix < vix3m * 0.92)

    if short_stress and inter_stress:
        label = "stress_extremo"
    elif short_stress:
        label = "stress_corto_plazo"
    elif inter_stress:
        label = "stress_intermedio"
    elif short_calma and inter_calma:
        label = "calma_extrema"
    elif short_calma or inter_calma:
        label = "calma"

    if vvix is not None and vvix > 110:
        label += "+vvix_alto"

    out["label"] = label

    # Interpretación lectura corta
    interp_parts = []
    if vix < 13:
        interp_parts.append(f"VIX {vix:.1f} muy bajo (complacencia)")
    elif vix > 25:
        interp_parts.append(f"VIX {vix:.1f} elevado (cobertura activa)")
    if vvix is not None and vvix > 110:
        interp_parts.append(f"VVIX {vvix:.0f} alto: pánico sobre pánico")
    if label.startswith("stress"):
        interp_parts.append("Curva invertida — mercado espera más vol corto plazo")
    elif label.startswith("calma"):
        interp_parts.append("Contango — sin estrés inmediato esperado")
    out["interpretation"] = " · ".join(interp_parts) if interp_parts else ""

    return out
