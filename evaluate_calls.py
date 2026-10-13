"""
╔══════════════════════════════════════════════════════════════╗
║          EVALUATE CALLS v2 — Loop de rendimiento              ║
║                                                                ║
║   Recorre output/calls.jsonl (esquema RICO o legacy), y por    ║
║   cada call valida:                                            ║
║     · dirección del sesgo vs movimiento real                   ║
║     · ejecución de niveles: entry/stop/tp1/tp2 sobre velas     ║
║       horarias posteriores (Binance / yfinance)                ║
║     · R:R logrado y calibración (probabilidad/confianza vs     ║
║       acierto real)                                            ║
║                                                                ║
║   Salidas:                                                     ║
║     · reporte en consola (hit rate por activo y prompt_version)║
║     · output/perf.json   → dashboard HTML del brief (C3)       ║
║     · output/eval_results.jsonl → detalle por call             ║
║     · con --write, actualiza calls.jsonl (evaluated=true)      ║
║                                                                ║
║   Uso:                                                         ║
║     python evaluate_calls.py                   # evaluar       ║
║     python evaluate_calls.py --write           # + write-back  ║
║     python evaluate_calls.py --last 7  --min-hours 20          ║
║     python evaluate_calls.py --horizon 48                      ║
╚══════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from modules.evaluator import (
    BINANCE_HISTORY_PAIRS,
    build_perf_payload,
    evaluate_call,
    fetch_history,
    write_back_calls,
)

# ═══════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGBOOK = os.path.join(BASE_DIR, "output", "calls.jsonl")
PERF_JSON = os.path.join(BASE_DIR, "output", "perf.json")
EVAL_RESULTS = os.path.join(BASE_DIR, "output", "eval_results.jsonl")

# Activos soportados por la validación de niveles (los que tienen historia horaria).
from modules.evaluator import YF_HISTORY_TICKERS
LEVEL_ASSETS = set(BINANCE_HISTORY_PAIRS.keys()) | set(YF_HISTORY_TICKERS.keys())


def _load_entries() -> list[dict]:
    if not os.path.exists(LOGBOOK):
        print(f"✗ Logbook no encontrado: {LOGBOOK}")
        sys.exit(1)
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
    return entries


def _fetch_latest_prices(assets: set) -> dict:
    import requests

    import modules.evaluator as ev

    latest = {}
    for alias in assets:
        u = alias.upper()
        if u in ev.BINANCE_HISTORY_PAIRS:
            try:
                r = requests.get(
                    "https://api.binance.com/api/v3/ticker/price",
                    params={"symbol": ev.BINANCE_HISTORY_PAIRS[u]}, timeout=8)
                if r.status_code == 200:
                    latest[u] = float(r.json()["price"])
                    continue
            except Exception:
                pass
        yf_t = ev.YF_HISTORY_TICKERS.get(u)
        if yf_t:
            try:
                import yfinance as yf
                hist = yf.Ticker(yf_t).history(period="1d", interval="1m")
                if not hist.empty:
                    latest[u] = float(hist["Close"].iloc[-1])
                    continue
            except Exception:
                pass
        latest[u] = None
    return latest


def main():
    parser = argparse.ArgumentParser(description="Evaluate MarketBrief calls (v2)")
    parser.add_argument("--last", type=int, default=None,
                        help="Solo los últimos N días")
    parser.add_argument("--horizon", type=int, default=24,
                        help="Ventana de validación de niveles en horas (default 24)")
    parser.add_argument("--min-hours", type=int, default=20,
                        help="Edad mínima del call para evaluarlo (default 20h)")
    parser.add_argument("--write", "-w", action="store_true",
                        help="Escribe evaluated=true + eval[] de vuelta a calls.jsonl")
    parser.add_argument("--by-version", action="store_true", default=True,
                        help="Desglose por prompt_version (default: on)")
    parser.add_argument("--no-levels", action="store_true",
                        help="Omitir validación de niveles (solo dirección)")
    args = parser.parse_args()

    entries = _load_entries()
    now = datetime.now(timezone.utc)

    # Filtrar por antigüedad
    cutoff_old = now.timestamp() - args.min_hours * 3600
    cutoff_new = None
    if args.last:
        cutoff_new = now.timestamp() - args.last * 86400

    selected = []
    for e in entries:
        ts_str = e.get("timestamp", "").replace("Z", "+00:00")
        try:
            ts = datetime.fromisoformat(ts_str).timestamp()
        except Exception:
            continue
        if ts > cutoff_old:
            continue
        if cutoff_new and ts < cutoff_new:
            continue
        selected.append(e)

    print(f"Logbook total: {len(entries)} | Evaluables (≥{args.min_hours}h): {len(selected)}")
    if not selected:
        print("Nada que evaluar.")
        return

    # Activos vistos (del esquema rico o legacy)
    def _assets_dict(e: dict) -> dict:
        s = e.get("structured") or {}
        if isinstance(s, str):
            try:
                s = json.loads(s)
            except json.JSONDecodeError:
                return {}
        return (s.get("assets") or s.get("principales") or s.get("activos") or {})

    seen_assets = set()
    for e in selected:
        for asset in _assets_dict(e):
            seen_assets.add(asset.upper())
    print(f"Activos en logbook: {sorted(seen_assets)}")

    # Precios actuales (necesarios para dirección del sesgo)
    print("Obteniendo precios actuales...")
    latest_prices = _fetch_latest_prices(seen_assets)

    # Historia horaria por activo (para validar niveles) en el rango visto
    histories = {}
    if not args.no_levels:
        min_ts = now
        max_ts = datetime.fromtimestamp(0, tz=timezone.utc)
        for e in selected:
            ts_str = e.get("timestamp", "").replace("Z", "+00:00")
            try:
                ts = datetime.fromisoformat(ts_str)
            except Exception:
                continue
            min_ts = min(min_ts, ts)
            max_ts = max(max_ts, ts)
        level_assets = {a for a in seen_assets if a in LEVEL_ASSETS}
        for asset in sorted(level_assets):
            hist = fetch_history(asset, min_ts, now)
            histories[asset] = hist
            print(f"  · {asset}: {len(hist)} velas horarias "
                  f"[{hist[0]['ts']:%m-%d %H:%M} → {hist[-1]['ts']:%m-%d %H:%M}]" if hist else f"  · {asset}: sin historia")

    # Evaluar
    overall = {"hit": 0, "miss": 0, "skip": 0}
    by_asset = defaultdict(lambda: {"hit": 0, "miss": 0, "skip": 0})
    by_version = defaultdict(lambda: {"hit": 0, "miss": 0, "skip": 0})
    all_results: list[dict] = []
    level_stats = defaultdict(lambda: {"tp1": 0, "tp2": 0, "stop": 0, "none": 0})

    for e in selected:
        results = evaluate_call(e, histories, horizon_hours=args.horizon,
                                latest_prices=latest_prices)
        ver = e.get("prompt_version", "unknown")
        for r in results:
            stat = by_asset[r.get("asset", "?")]
            r["prompt_version"] = ver
            if r.get("hit") is True:
                overall["hit"] += 1
                stat["hit"] += 1
                by_version[ver]["hit"] += 1
            elif r.get("hit") is False:
                overall["miss"] += 1
                stat["miss"] += 1
                by_version[ver]["miss"] += 1
            else:
                overall["skip"] += 1
                stat["skip"] += 1
                by_version[ver]["skip"] += 1
            if r.get("outcome"):
                level_stats[r["asset"]][r["outcome"]] += 1
            all_results.append(r)

    # ── Reporte ──
    def fmt(stats):
        total_eval = stats["hit"] + stats["miss"]
        if total_eval == 0:
            return "n/a (sin evaluables)"
        rate = stats["hit"] / total_eval * 100
        return (f"hit={stats['hit']:3d} miss={stats['miss']:3d} "
                f"skip={stats['skip']:3d}  →  {rate:.1f}%")

    print("\n" + "=" * 60)
    print("RESULTADO GLOBAL (dirección del sesgo)")
    print("=" * 60)
    print(f"  Total: {fmt(overall)}")

    print("\n" + "-" * 60)
    print("POR ACTIVO")
    print("-" * 60)
    for asset, stats in sorted(by_asset.items()):
        extra = ""
        if asset in level_stats and any(level_stats[asset].values()):
            o = level_stats[asset]
            extra = f"   [niveles] tp1={o['tp1']} tp2={o['tp2']} stop={o['stop']} sin-tocar={o['none']}"
        print(f"  {asset:8s}  {fmt(stats)}{extra}")

    if args.by_version:
        print("\n" + "-" * 60)
        print("POR PROMPT VERSION (A/B)")
        print("-" * 60)
        for ver, stats in sorted(by_version.items()):
            print(f"  {ver:10s}  {fmt(stats)}")

    # ── Persistencia ──
    payload = build_perf_payload(all_results, by_version=args.by_version)
    with open(PERF_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    with open(EVAL_RESULTS, "w", encoding="utf-8") as f:
        for r in all_results:
            f.write(json.dumps(r, default=str, ensure_ascii=False) + "\n")

    print(f"\n  · perf.json → {PERF_JSON}")
    print(f"  · eval_results.jsonl → {EVAL_RESULTS}")

    if args.write:
        write_back_calls(LOGBOOK, entries, all_results)
        print("  · calls.jsonl actualizado (evaluated=true)")


if __name__ == "__main__":
    main()