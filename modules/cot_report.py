"""
╔══════════════════════════════════════════════════════╗
║      COT REPORT — CFTC Commitments of Traders         ║
║  Posicionamiento semanal Smart Money (commercials)   ║
║  vs Speculators (non-commercials).                    ║
║  Fuente: Socrata API pública de la CFTC (no key).    ║
║  Publicación: viernes (data del martes anterior).    ║
╚══════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import requests

from modules.cache import cached

logger = logging.getLogger("MarketBrief")


# Endpoint Socrata: Legacy Futures-Only Reports (más simple, lo que el público sigue)
# Dataset id: 6dca-aqww (Legacy Futures only)
COT_BASE = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

# Mapeo de activos del brief a los nombres de mercado de la CFTC
# (busco con LIKE para tolerar variantes de capitalización)
# Patrones LIKE flexibles — algunos mercados aparecen con nombres variantes
# en la base CFTC. Probamos en orden hasta encontrar match.
COT_MARKETS = {
    "SP500":     ["E-MINI S&P 500", "S&P 500"],
    "NASDAQ":    ["NASDAQ-100", "E-MINI NASDAQ"],
    "GOLD":      ["GOLD"],
    "SILVER":    ["SILVER"],
    "OIL":       ["CRUDE OIL, LIGHT SWEET"],
    "DXY":       ["USD INDEX"],
}


def _interpret_net_change(name: str, latest: dict, prev: dict | None) -> str:
    """Genera una frase corta interpretando el cambio semanal."""
    try:
        comm_net = (int(latest.get("comm_positions_long_all", 0))
                    - int(latest.get("comm_positions_short_all", 0)))
        nc_net = (int(latest.get("noncomm_positions_long_all", 0))
                  - int(latest.get("noncomm_positions_short_all", 0)))
    except Exception:
        return ""
    line = (f"Commercials net {comm_net:+,d}  |  "
            f"Non-commercials net {nc_net:+,d}")
    if prev is not None:
        try:
            prev_comm_net = (int(prev.get("comm_positions_long_all", 0))
                             - int(prev.get("comm_positions_short_all", 0)))
            delta = comm_net - prev_comm_net
            line += f"  | Δ commercials semanal: {delta:+,d}"
        except Exception:
            pass
    return line


@cached("macro_calendar")  # TTL 6h — COT no cambia diario
def fetch_cot_report() -> dict:
    """
    Obtiene últimas 2 entradas semanales del COT por activo configurado.
    Retorna posición neta de commercials vs non-commercials + delta semanal.
    """
    out = {"source": "CFTC Legacy Futures-Only", "markets": {}}

    # Pedimos los últimos ~60 días para tener al menos 2 reportes
    since = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d")

    for alias, name_variants in COT_MARKETS.items():
        rows = []
        matched_name = None
        try:
            for variant in name_variants:
                # Para SP500/NASDAQ/GOLD evitamos los contratos MICRO (1/10 del nominal)
                # ya que el COT institucional está en el contrato grande.
                exclude_micro = alias in ("SP500", "NASDAQ", "GOLD")
                where = (
                    f"upper(market_and_exchange_names) like "
                    f"'%{variant.upper()}%' "
                    f"AND report_date_as_yyyy_mm_dd >= '{since}'"
                )
                if exclude_micro:
                    where += " AND upper(market_and_exchange_names) not like '%MICRO%'"
                params = {
                    "$where": where,
                    "$order": "report_date_as_yyyy_mm_dd DESC",
                    "$limit": 3,
                }
                r = requests.get(COT_BASE, params=params, timeout=15)
                if r.status_code != 200:
                    continue
                rows = r.json() or []
                if rows:
                    matched_name = variant
                    break
            if not rows:
                out["markets"][alias] = {"error": "sin datos"}
                continue

            latest = rows[0]
            prev = rows[1] if len(rows) > 1 else None

            out["markets"][alias] = {
                "market_name":   latest.get("market_and_exchange_names"),
                "report_date":   latest.get("report_date_as_yyyy_mm_dd"),
                "comm_long":     int(latest.get("comm_positions_long_all", 0) or 0),
                "comm_short":    int(latest.get("comm_positions_short_all", 0) or 0),
                "noncomm_long":  int(latest.get("noncomm_positions_long_all", 0) or 0),
                "noncomm_short": int(latest.get("noncomm_positions_short_all", 0) or 0),
                "open_interest": int(latest.get("open_interest_all", 0) or 0),
                "summary":       _interpret_net_change(alias, latest, prev),
            }
        except Exception as e:
            logger.warning(f"  COT {alias}: {e}")
            out["markets"][alias] = {"error": str(e)}

    return out
