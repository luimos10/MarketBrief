"""
╔══════════════════════════════════════════════════════════════╗
║          EVALUATE CALLS v3 — Loop de rendimiento              ║
║                                                                ║
║   Evalúa cada call del logbook contra velas reales:            ║
║     · dirección operativa (decision LONG/SHORT) al close de    ║
║       call_ts + horizon                                       ║
║     · sesgo declarado (informativo, no cuenta como trade)      ║
║     · fill obligatorio + entry/stop/tp1/tp2                    ║
║     · R real con costos; stop gana empates en la misma vela    ║
║     · calibración con probability (Brier)                      ║
║                                                                ║
║   Salidas:                                                     ║
║     · output/perf.json          → dashboard del brief          ║
║     · output/eval_results.jsonl → detalle acumulado            ║
║     · con --write, calls.jsonl (evaluated=true + eval[])       ║
║                                                                ║
║   Uso:                                                         ║
║     python evaluate_calls.py                   # evaluar       ║
║     python evaluate_calls.py --write           # + write-back  ║
║     python evaluate_calls.py --last 7 --horizon 24             ║
╚══════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from modules.evaluator import (
    ASSET_EVAL_CONFIG,
    build_perf_payload,
    evaluate_call,
    fetch_history,
    load_eval_results,
    merge_eval_results,
    write_eval_results,
    write_back_calls,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGBOOK = os.path.join(BASE_DIR, "output", "calls.jsonl")
PERF_JSON = os.path.join(BASE_DIR, "output", "perf.json")
EVAL_RESULTS = os.path.join(BASE_DIR, "output", "eval_results.jsonl")

LEVEL_ASSETS = set(ASSET_EVAL_CONFIG)


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


def _parse_ts(value: str) -> datetime | None:
    try:
        ts = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _assets_dict(e: dict) -> dict:
    s = e.get("structured") or {}
    if isinstance(s, str):
        try:
            s = json.loads(s)
        except json.JSONDecodeError:
            return {}
    return s.get("assets") or s.get("principales") or s.get("activos") or {}


def _needs_eval(e: dict, horizon: int, now: datetime) -> bool:
    ts = _parse_ts(e.get("timestamp", ""))
    if ts is None:
        return False
    if now < ts + timedelta(hours=horizon):
        return False
    existing = e.get("eval") or []
    if not e.get("evaluated") or not existing:
        return True
    return not all(r.get("eval_version") == 2 for r in existing if isinstance(r, dict))


def main():
    parser = argparse.ArgumentParser(description="Evaluate MarketBrief calls (v3)")
    parser.add_argument("--last", type=int, default=None,
                        help="Solo los últimos N días")
    parser.add_argument("--horizon", type=int, default=24,
                        help="Horizonte fijo de evaluación en horas (default 24)")
    parser.add_argument("--min-hours", type=int, default=None,
                        help="Edad mínima del call (default = horizon)")
    parser.add_argument("--write", "-w", action="store_true",
                        help="Escribe evaluated=true + eval[] en calls.jsonl")
    parser.add_argument("--by-version", action="store_true", default=True,
                        help="Desglose por prompt_version (default: on)")
    parser.add_argument("--no-levels", action="store_true",
                        help="Omitir validación de niveles (solo dirección)")
    parser.add_argument("--sessions", default=None,
                        help="Filtrar sesiones, ej: tokio,londres,ny")
    parser.add_argument("--all", action="store_true",
                        help="Reevaluar todo el logbook, no solo pendientes")
    args = parser.parse_args()

    min_hours = args.min_hours if args.min_hours is not None else args.horizon
    if min_hours < args.horizon:
        print(f"⚠ --min-hours ({min_hours}) < --horizon ({args.horizon}): "
              f"se usa {args.horizon} para evitar ventanas incompletas.")
        min_hours = args.horizon

    entries = _load_entries()
    now = datetime.now(timezone.utc)

    cutoff_old = now - timedelta(hours=min_hours)
    cutoff_new = now - timedelta(days=args.last) if args.last else None
    sessions = (
        {s.strip().lower() for s in args.sessions.split(",") if s.strip()}
        if args.sessions else None
    )

    selected = []
    for e in entries:
        ts = _parse_ts(e.get("timestamp", ""))
        if ts is None or ts > cutoff_old:
            continue
        if cutoff_new and ts < cutoff_new:
            continue
        if sessions and (e.get("session") or "unknown").lower() not in sessions:
            continue
        if args.all or _needs_eval(e, args.horizon, now):
            selected.append((e, ts))

    pending = len(selected)
    print(f"Logbook total: {len(entries)} | A evaluar: {pending} "
          f"(horizonte {args.horizon}h)")
    if not selected:
        print("Nada nuevo que evaluar. Recalculando dashboard con lo existente.")
        existing = load_eval_results(EVAL_RESULTS)
        payload = build_perf_payload(existing, by_version=args.by_version)
        with open(PERF_JSON, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"  · perf.json → {PERF_JSON} ({len(existing)} filas)")
        return

    seen_assets = set()
    for e, _ in selected:
        for asset in _assets_dict(e):
            seen_assets.add(asset.upper())
    print(f"Activos en lote: {sorted(seen_assets)}")

    # Historia horaria: un solo fetch por activo que cubra desde el call más
    # antiguo hasta el horizonte del más nuevo.
    histories = {}
    if not args.no_levels:
        level_assets = {a for a in seen_assets if a in LEVEL_ASSETS}
        max_ts = max(ts for _, ts in selected)
        min_ts = min(ts for _, ts in selected)
        fetch_end = min(max_ts + timedelta(hours=args.horizon), now)
        for asset in sorted(level_assets):
            hist = fetch_history(asset, min_ts, fetch_end)
            histories[asset] = hist
            if hist:
                print(f"  · {asset}: {len(hist)} velas "
                      f"[{hist[0]['ts']:%m-%d %H:%M} → {hist[-1]['ts']:%m-%d %H:%M}]")
            else:
                print(f"  · {asset}: sin historia")

    all_results: list[dict] = []
    for e, _ in selected:
        all_results.extend(
            evaluate_call(e, histories, horizon_hours=args.horizon)
        )

    existing = load_eval_results(EVAL_RESULTS)
    merged = merge_eval_results(existing, all_results)

    # ── Reporte del lote ──
    def fmt(stats):
        total_eval = stats["hit"] + stats["miss"]
        if total_eval == 0:
            return "n/a (sin evaluables)"
        rate = stats["hit"] / total_eval * 100
        return (f"hit={stats['hit']:3d} miss={stats['miss']:3d} "
                f"skip={stats['skip']:3d}  →  {rate:.1f}%")

    payload = build_perf_payload(merged, by_version=args.by_version)
    agg = payload["aggregated"]
    print("\n" + "=" * 64)
    print("RESULTADO ACUMULADO (decisión operativa al cierre del horizonte)")
    print("=" * 64)
    print(f"  Total: {fmt(agg['overall'])}")
    if agg["overall"].get("calibration"):
        cal = agg["overall"]["calibration"]
        print(f"  Calibración: {cal['probability']:.1f}% declarada vs "
              f"{cal['actual']}% real · Brier={cal['brier']} (n={cal['n']})")
    print(f"  Sesgo (informativo): {fmt(agg['bias'])}")
    ex = agg["execution"]
    print(f"  Ejecución: tp1={ex['tp1']} tp2={ex['tp2']} stop={ex['stop']} "
          f"no_fill={ex['no_fill']} pending={ex['pending']} none={ex['none']}")
    if ex.get("r_mean") is not None:
        print(f"  R medio: {ex['r_mean']:+.3f} (n={ex['n_r']}, suma={ex['r_sum']:+.2f})")

    print("\n" + "-" * 64)
    print("POR ACTIVO")
    print("-" * 64)
    for asset, stats in sorted(agg["by_asset"].items()):
        print(f"  {asset:8s}  {fmt(stats)}")

    if args.by_version and agg["by_version"]:
        print("\n" + "-" * 64)
        print("POR PROMPT VERSION")
        print("-" * 64)
        for ver, stats in sorted(agg["by_version"].items()):
            print(f"  {ver:12s}  {fmt(stats)}")

    if agg["by_session"]:
        print("\n" + "-" * 64)
        print("POR SESIÓN")
        print("-" * 64)
        for ses, stats in sorted(agg["by_session"].items()):
            print(f"  {ses:12s}  {fmt(stats)}")

    with open(PERF_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    write_eval_results(EVAL_RESULTS, merged)

    print(f"\n  · perf.json → {PERF_JSON}")
    print(f"  · eval_results.jsonl → {EVAL_RESULTS} ({len(merged)} filas v2)")

    if args.write:
        write_back_calls(LOGBOOK, entries, all_results)
        print("  · calls.jsonl actualizado (evaluated=true)")


if __name__ == "__main__":
    main()
