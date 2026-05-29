"""
╔══════════════════════════════════════════════════════╗
║       SECTOR ROTATION — XLK/XLF/XLE/XLY/XLU           ║
║  Detección de risk-on vs risk-off vía performance     ║
║  relativa de sector ETFs (data ya en yfinance).      ║
╚══════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging

logger = logging.getLogger("MarketBrief")


# Aliases internos -> tickers de yfinance ya recolectados
SECTOR_KEYS = {
    "XLK_Tech":         "XLK",
    "XLF_Financials":   "XLF",
    "XLE_Energy":       "XLE",
    "XLY_Cons_Disc":    "XLY",
    "XLU_Utilities":    "XLU",
}

# Significado direccional cuando un sector outperforma vs el resto:
SECTOR_REGIME_HINT = {
    "XLK_Tech":       "risk-on (tech leadership)",
    "XLY_Cons_Disc":  "risk-on (consumer confidence)",
    "XLF_Financials": "risk-on (credit risk apetite, alza de tasas favorable)",
    "XLE_Energy":     "inflación/commodity-driven",
    "XLU_Utilities":  "risk-off (defensive rotation)",
}


def compute_sector_rotation(traditional_markets: dict) -> dict:
    """
    Calcula performance relativa de los 5 sector ETFs sobre 24h y 7d.
    Devuelve ranking + label de régimen rotacional.
    """
    perf_24h = []
    perf_7d = []

    for alias, ticker in SECTOR_KEYS.items():
        asset = traditional_markets.get(alias)
        if not isinstance(asset, dict) or "error" in asset:
            continue
        c24 = asset.get("cambio_24h_pct")
        c7d = asset.get("cambio_7d_pct")
        try:
            c24 = float(c24) if c24 is not None and c24 != "N/A" else None
        except (TypeError, ValueError):
            c24 = None
        try:
            c7d = float(c7d) if c7d is not None and c7d != "N/A" else None
        except (TypeError, ValueError):
            c7d = None
        if c24 is not None:
            perf_24h.append({"sector": alias, "ticker": ticker, "pct": c24})
        if c7d is not None:
            perf_7d.append({"sector": alias, "ticker": ticker, "pct": c7d})

    if not perf_7d:
        return {"error": "sin datos de sectores"}

    perf_24h.sort(key=lambda x: x["pct"], reverse=True)
    perf_7d.sort(key=lambda x: x["pct"], reverse=True)

    leader_7d  = perf_7d[0] if perf_7d else None
    laggard_7d = perf_7d[-1] if perf_7d else None
    leader_24h = perf_24h[0] if perf_24h else None

    # Determinar régimen — quien lidera vs quien rezaga
    regime = "neutral"
    rationale = []
    if leader_7d and laggard_7d:
        lead = leader_7d["sector"]
        lag = laggard_7d["sector"]

        # Risk-on: XLK o XLY arriba, XLU o XLP abajo
        # Risk-off: XLU arriba, XLK o XLY abajo
        # Inflación: XLE arriba destacado
        if lead in ("XLK_Tech", "XLY_Cons_Disc") and lag in ("XLU_Utilities",):
            regime = "risk-on"
            rationale.append(f"{lead} lidera y {lag} rezaga — apetito al riesgo.")
        elif lead == "XLU_Utilities":
            regime = "risk-off"
            rationale.append("Utilities lidera — rotación defensiva.")
        elif lead == "XLE_Energy":
            regime = "inflation_driven"
            rationale.append("Energy lidera — presión inflacionaria/commodity-driven.")
        elif lead == "XLF_Financials":
            regime = "rates_friendly"
            rationale.append("Financials lidera — entorno de tasas favorable.")
        else:
            regime = "mixed"
            rationale.append(
                f"{lead} lidera, {lag} rezaga — sin régimen sectorial claro.")

    return {
        "perf_24h_ranking": perf_24h,
        "perf_7d_ranking":  perf_7d,
        "leader_7d":        leader_7d,
        "laggard_7d":       laggard_7d,
        "leader_24h":       leader_24h,
        "regime":           regime,
        "interpretation":   " ".join(rationale),
        "hint":             SECTOR_REGIME_HINT.get(
                                (leader_7d or {}).get("sector", ""), ""),
    }
