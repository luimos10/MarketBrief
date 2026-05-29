"""
╔══════════════════════════════════════════════════════════════╗
║          EVALUATE CALLS — Hit rate del logbook                ║
║                                                                ║
║   Recorre output/calls.jsonl, compara los calls vs el precio  ║
║   actual de cada activo, y reporta hit rate por:              ║
║     - sesgo agregado del brief                                ║
║     - sesgo por activo                                        ║
║     - prompt_version (para A/B testing)                       ║
║                                                                ║
║   Uso:                                                         ║
║     python evaluate_calls.py             # todos los calls    ║
║     python evaluate_calls.py --last 7    # últimos 7 días     ║
║     python evaluate_calls.py --min-hours 20   # solo calls    ║
║                                                con ≥20h       ║
╚══════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yfinance as yf
import requests


# ═══════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGBOOK = os.path.join(BASE_DIR, "output", "calls.jsonl")

# Tickers para obtener precio actual de cada activo del JSON
TICKERS_YF = {
    "SP500":   "ES=F",
    "NASDAQ":  "NQ=F",
    "GOLD":    "GC=F",
    "OIL":     "CL=F",
    "DXY":     "DX-Y.NYB",
    "VIX":     "^VIX",
}
BINANCE_PAIRS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}


# ═══════════════════════════════════════════════════════
# OBTENER PRECIO ACTUAL
# ═══════════════════════════════════════════════════════

def current_price(asset: str) -> float | None:
    if asset.upper() in BINANCE_PAIRS:
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": BINANCE_PAIRS[asset.upper()]}, timeout=8)
            if r.status_code == 200:
                return float(r.json()["price"])
        except Exception:
            return None
        return None

    if asset.upper() in TICKERS_YF:
        try:
            t = yf.Ticker(TICKERS_YF[asset.upper()])
            h = t.history(period="1d", interval="1m")
            if not h.empty:
                return float(h["Close"].iloc[-1])
        except Exception:
            return None
    return None


# ═══════════════════════════════════════════════════════
# EVALUACIÓN DE UN CALL
# ═══════════════════════════════════════════════════════

def evaluate_asset_call(asset: str, call: dict, snapshot_price: float | None,
                        latest_price: float | None) -> dict:
    """
    Compara el sesgo del call contra el movimiento real.
    Hit = sesgo Alcista y precio subió, o Bajista y bajó.
    Neutral cuenta como hit si el move fue < 0.5% (asset normal) o < 0.3% (índices).
    """
    bias = (call.get("sesgo") or "").lower()
    if snapshot_price is None or latest_price is None or snapshot_price == 0:
        return {"asset": asset, "hit": None, "reason": "sin precio"}

    pct_move = (latest_price - snapshot_price) / snapshot_price * 100
    threshold = 0.5  # %

    if bias == "alcista":
        hit = pct_move > threshold
    elif bias == "bajista":
        hit = pct_move < -threshold
    elif bias == "neutral":
        hit = abs(pct_move) <= threshold
    else:
        return {"asset": asset, "hit": None, "reason": f"sesgo desconocido: {bias}"}

    return {
        "asset": asset,
        "bias": bias,
        "snapshot_price": snapshot_price,
        "latest_price": latest_price,
        "move_pct": round(pct_move, 2),
        "hit": hit,
    }


# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Evaluate MarketBrief calls")
    parser.add_argument("--last", type=int, default=None,
                        help="Solo los últimos N días")
    parser.add_argument("--min-hours", type=int, default=20,
                        help="Edad mínima del call para evaluarlo (default 20h)")
    parser.add_argument("--by-version", action="store_true",
                        help="Desglose por prompt_version")
    args = parser.parse_args()

    if not os.path.exists(LOGBOOK):
        print(f"✗ Logbook no encontrado: {LOGBOOK}")
        sys.exit(1)

    cutoff_old = datetime.now(timezone.utc) - timedelta(hours=args.min_hours)
    cutoff_new = None
    if args.last:
        cutoff_new = datetime.now(timezone.utc) - timedelta(days=args.last)

    entries = []
    with open(LOGBOOK, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Filtrar por antigüedad
    selected = []
    for e in entries:
        ts_str = e.get("timestamp", "").replace("Z", "+00:00")
        try:
            ts = datetime.fromisoformat(ts_str)
        except Exception:
            continue
        if ts > cutoff_old:
            continue  # demasiado reciente
        if cutoff_new and ts < cutoff_new:
            continue
        selected.append((ts, e))

    print(f"Logbook total: {len(entries)} | Evaluables (≥{args.min_hours}h): {len(selected)}")
    if not selected:
        print("Nada que evaluar.")
        return

    # Recoger precios actuales para todos los activos vistos
    def _assets_dict(structured: dict) -> dict:
        """v1.4 usa 'principales'; v1.3 y anteriores usan 'activos'.
        Soporta ambos para backward-compat."""
        return (structured.get("principales")
                or structured.get("activos")
                or {})

    seen_assets = set()
    for _, e in selected:
        s = e.get("structured") or {}
        for asset in _assets_dict(s):
            seen_assets.add(asset.upper())

    print(f"Activos en logbook: {sorted(seen_assets)}")
    print("Obteniendo precios actuales...")
    latest_prices = {a: current_price(a) for a in seen_assets}

    # Estructuras de resultado
    overall = {"hit": 0, "miss": 0, "skip": 0}
    by_asset = defaultdict(lambda: {"hit": 0, "miss": 0, "skip": 0})
    by_version = defaultdict(lambda: {"hit": 0, "miss": 0, "skip": 0})

    # Evaluar global call
    for ts, e in selected:
        s = e.get("structured") or {}
        snap = e.get("snapshot") or {}
        snap_prices = snap.get("prices", {})
        ver = e.get("prompt_version", "unknown")

        # Por activo
        for asset, asset_call in _assets_dict(s).items():
            asset_u = asset.upper()
            # Buscar snapshot price del activo: probar varias variantes
            sp = (snap_prices.get(asset_u)
                  or snap_prices.get(asset)
                  or asset_call.get("precio_referencia"))
            try:
                sp = float(sp) if sp not in (None, "", 0) else None
            except (TypeError, ValueError):
                sp = None

            lp = latest_prices.get(asset_u)
            r = evaluate_asset_call(asset_u, asset_call, sp, lp)
            if r["hit"] is True:
                overall["hit"] += 1
                by_asset[asset_u]["hit"] += 1
                by_version[ver]["hit"] += 1
            elif r["hit"] is False:
                overall["miss"] += 1
                by_asset[asset_u]["miss"] += 1
                by_version[ver]["miss"] += 1
            else:
                overall["skip"] += 1
                by_asset[asset_u]["skip"] += 1
                by_version[ver]["skip"] += 1

    # Reporte
    def fmt(stats):
        total_eval = stats["hit"] + stats["miss"]
        if total_eval == 0:
            return "n/a (sin evaluables)"
        rate = stats["hit"] / total_eval * 100
        return (f"hit={stats['hit']:3d} miss={stats['miss']:3d} "
                f"skip={stats['skip']:3d}  →  {rate:.1f}%")

    print("\n" + "=" * 60)
    print("RESULTADO GLOBAL")
    print("=" * 60)
    print(f"  Total: {fmt(overall)}")

    print("\n" + "-" * 60)
    print("POR ACTIVO")
    print("-" * 60)
    for asset, stats in sorted(by_asset.items()):
        print(f"  {asset:8s}  {fmt(stats)}")

    if args.by_version:
        print("\n" + "-" * 60)
        print("POR PROMPT VERSION (A/B)")
        print("-" * 60)
        for ver, stats in sorted(by_version.items()):
            print(f"  {ver:10s}  {fmt(stats)}")


if __name__ == "__main__":
    main()
