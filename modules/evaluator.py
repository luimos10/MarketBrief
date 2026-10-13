"""
╔══════════════════════════════════════════════════════════════╗
║        EVALUATOR — Evaluación avanzada del logbook            ║
║                                                                ║
║   Valida sesgo (dirección), niveles (soporte/resistencia/     ║
║   invalidación), hits de TP/SL, R:R logrado y calibración     ║
║   de probabilidad/confianza declarada por el LLM.             ║
║                                                                ║
║   Soporta el esquema rico (assets/entry/stop/tp/rr/ev) del    ║
║   Prompt Maestro y, por compatibilidad, el legacy v1.4        ║
║   (principales/macro_context) y v1.3 (activos).               ║
╚══════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests
import yfinance as yf

from modules.logbook import normalize_structured

logger = logging.getLogger("MarketBrief")

# Ticker por alias para la validación histórica de niveles.
YF_HISTORY_TICKERS = {
    "SP500": "ES=F",
    "NASDAQ": "NQ=F",
    "GOLD": "GC=F",
    "OIL": "CL=F",
    "DXY": "DX-Y.NYB",
    "VIX": "^VIX",
    "SILVER": "SI=F",
    "US10Y": "^TNX",
    "US02Y": "^IRX",
}
BINANCE_HISTORY_PAIRS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}

BIAS_THRESHOLD_PCT = 0.5  # umbral de dirección del sesgo (%)

_tier_order = {"P": 0, "M": 1, "W": 2}


def _to_float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ═══════════════════════════════════════════════════════
# HISTORIA DE PRECIOS (para validar niveles/TP/SL)
# ═══════════════════════════════════════════════════════

def fetch_history(asset: str, start: datetime, end: datetime) -> list[dict]:
    """Devuelve velas horarias (ts, high, low, close) entre start y end."""
    asset = asset.upper().strip()
    if asset in BINANCE_HISTORY_PAIRS:
        try:
            start_ms = int(start.timestamp() * 1000)
            end_ms = int(end.timestamp() * 1000)
            url = "https://api.binance.com/api/v3/klines"
            rows = []
            cursor = start_ms
            while cursor < end_ms:
                r = requests.get(url, params={
                    "symbol": BINANCE_HISTORY_PAIRS[asset],
                    "interval": "1h",
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1000,
                }, timeout=15)
                if r.status_code != 200:
                    break
                data = r.json()
                if not data:
                    break
                for k in data:
                    rows.append({
                        "ts": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                    })
                cursor = data[-1][0] + 1
            rows.sort(key=lambda r: r["ts"])
            return rows
        except Exception as e:
            logger.warning(f"  [eval] Sin historia Binance para {asset}: {e}")
            return []

    if asset in YF_HISTORY_TICKERS:
        try:
            df = yf.download(
                YF_HISTORY_TICKERS[asset], interval="1h",
                start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
                progress=False, auto_adjust=True, group_by="ticker",
            )
            if df is None or df.empty:
                return []
            if isinstance(df.columns, __import__("pandas").MultiIndex):
                df = df.droplevel(0, axis=1)
            rows = []
            for idx, row in df.iterrows():
                try:
                    rows.append({
                        "ts": idx.to_pydatetime().replace(tzinfo=timezone.utc)
                        if idx.tzinfo is None else idx.to_pydatetime(),
                        "high": float(row["High"].iloc[0]) if hasattr(row["High"], "iloc") else float(row["High"]),
                        "low": float(row["Low"].iloc[0]) if hasattr(row["Low"], "iloc") else float(row["Low"]),
                        "close": float(row["Close"].iloc[0]) if hasattr(row["Close"], "iloc") else float(row["Close"]),
                    })
                except (TypeError, ValueError):
                    continue
            rows.sort(key=lambda r: r["ts"])
            return rows
        except Exception as e:
            logger.warning(f"  [eval] Sin historia yfinance para {asset}: {e}")
            return []

    return []


# ═══════════════════════════════════════════════════════
# EVALUACIÓN DE UN CALL
# ═══════════════════════════════════════════════════════

def _bias_to_direction(bias) -> str | None:
    """Normaliza el sesgo del activo al esquema rich (numérico) o legacy (string)."""
    if bias is None:
        return None
    if isinstance(bias, bool):
        return None
    if isinstance(bias, (int, float)):
        if bias > 0:
            return "alcista"
        if bias < 0:
            return "bajista"
        return "neutral"
    b = str(bias).strip().lower()
    if b in ("alcista", "bull", "long", "a"):
        return "alcista"
    if b in ("bajista", "bear", "short", "b"):
        return "bajista"
    if b in ("neutral", "mixto", "n", ""):
        return "neutral"
    return None


def evaluate_asset_call(asset: str, call: dict, snapshot_price: float | None,
                        latest_price: float | None,
                        bars_window: list[dict] = None) -> dict:
    """Evalúa un call de activo a nivel de dirección y de ejecución."""
    bias = _bias_to_direction(call.get("bias") or call.get("sesgo"))
    result = {"asset": asset, "hit": None, "reason": None}

    # ── 1) Dirección (sesgo) vs movimiento real ──
    if snapshot_price and latest_price and snapshot_price not in (0, None):
        pct_move = (latest_price - snapshot_price) / snapshot_price * 100
        result["move_pct"] = round(pct_move, 2)
        if bias == "alcista":
            result["hit"] = pct_move > BIAS_THRESHOLD_PCT
        elif bias == "bajista":
            result["hit"] = pct_move < -BIAS_THRESHOLD_PCT
        elif bias == "neutral":
            result["hit"] = abs(pct_move) <= BIAS_THRESHOLD_PCT
        else:
            result["hit"] = None
            result["reason"] = f"sesgo desconocido: {call.get('bias')}"
    else:
        result["hit"] = None
        result["reason"] = "sin precio"

    if latest_price:
        result["latest_price"] = latest_price
    if snapshot_price:
        result["snapshot_price"] = snapshot_price

    # ── 2) Ejecución: niveles entry/stop/tp1/tp2 sobre velas post-call ──
    entry = _to_float(call.get("entry") or call.get("soporte") or call.get("precio_referencia"))
    stop = _to_float(call.get("stop") or call.get("invalidacion"))
    tp1 = _to_float(call.get("tp1"))
    tp2 = _to_float(call.get("tp2"))

    if bars_window and (tp1 or tp2 or stop) and snapshot_price:
        # Dirección: si stop > snapshot/entry → setup SHORT (SL arriba).
        direction = "SHORT" if (stop and entry and stop > entry) else "LONG"
        outcome = "none"
        for bar in bars_window:
            hi, lo = bar["high"], bar["low"]
            if direction == "LONG":
                if tp1 and hi >= tp1:
                    outcome = "tp1"
                    break
                if stop and lo <= stop:
                    outcome = "stop"
                    break
                if tp2 and hi >= tp2:
                    outcome = "tp2"
            else:
                if tp1 and lo <= tp1:
                    outcome = "tp1"
                    break
                if stop and hi >= stop:
                    outcome = "stop"
                    break
                if tp2 and lo <= tp2:
                    outcome = "tp2"

        if outcome != "none":
            result["outcome"] = outcome
            rr_declared = _to_float(call.get("rr"))
            if outcome == "tp1" and rr_declared:
                result["r_realized"] = rr_declared
                result["rr_hit"] = True
            elif outcome == "tp2" and rr_declared:
                result["r_realized"] = rr_declared
                result["rr_hit"] = True
            elif outcome == "stop":
                result["r_realized"] = -1.0
                result["rr_hit"] = False
            elif outcome in ("tp1", "tp2") and not rr_declared:
                result["rr_hit"] = True
        # Tokens de posición: si tocó SL → falls el nivel.
        if outcome == "stop":
            result["level_hit"] = False
        elif outcome in ("tp1", "tp2"):
            result["level_hit"] = True
        elif tp1 or tp2:
            result["level_hit"] = None  # aún en ventana / sin tocar

    # Probabilidad declarada (para calibración)
    declared_p = _to_float(call.get("probability") or call.get("confidence"))
    if declared_p is not None:
        result["probability_declared"] = declared_p

    return result


def evaluate_call(entry: dict, histories: dict, horizon_hours: int = 24,
                  latest_prices: dict = None) -> dict:
    """Evalúa todas las entradas de activos de un call del logbook."""
    structured = normalize_structured(entry.get("structured"))
    assets = (structured or {}).get("assets") or {}
    snap = entry.get("snapshot") or {}
    snap_prices = snap.get("prices", {})
    ts = entry.get("timestamp", "")

    try:
        call_ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        call_ts = datetime.now(timezone.utc)

    window_start = call_ts
    window_end = min(call_ts + timedelta(hours=horizon_hours),
                     datetime.now(timezone.utc))
    latest_prices = latest_prices or {}

    evals = []
    for alias, call in assets.items():
        if not isinstance(call, dict):
            continue
        internal = {
            "BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
            "SP500": "S&P 500 (ES)", "NASDAQ": "Nasdaq 100 (NQ)",
            "GOLD": "Gold (XAUUSD)", "SILVER": "Silver (XAGUSD)",
            "OIL": "Crude Oil (WTI)",
        }.get(alias, alias)

        sp = (snap_prices.get(alias)
              or snap_prices.get(internal)
              or call.get("price")
              or call.get("precio_referencia"))
        sp = _to_float(sp) if sp not in (None, "", 0) else None
        lp = latest_prices.get(alias.upper())

        bars_window = None
        hist = histories.get(alias.upper())
        if hist:
            bars_window = [b for b in hist if window_start <= b["ts"] < window_end]

        r = evaluate_asset_call(alias.upper(), call, sp, lp, bars_window)
        r["timestamp"] = ts
        r["prompt_version"] = entry.get("prompt_version")
        evals.append(r)

    return evals


# ═══════════════════════════════════════════════════════
# AGREGACIÓN + PERF PAYLOAD
# ═══════════════════════════════════════════════════════

def build_perf_payload(results: list[dict], by_version: bool = True) -> dict:
    """Convierte registros de eval en el payload agregado del dashboard HTML."""
    overall = {"hit": 0, "miss": 0, "skip": 0}
    by_asset = defaultdict(lambda: {"hit": 0, "miss": 0, "skip": 0})
    by_version = defaultdict(lambda: {"hit": 0, "miss": 0, "skip": 0})

    declared_ps = []
    for r in results:
        if not isinstance(r, dict):
            continue
        asset = r.get("asset", "?")
        version = r.get("prompt_version", "unknown")
        if r.get("hit") is True:
            overall["hit"] += 1
            by_asset[asset]["hit"] += 1
            by_version[version]["hit"] += 1
        elif r.get("hit") is False:
            overall["miss"] += 1
            by_asset[asset]["miss"] += 1
            by_version[version]["miss"] += 1
        else:
            overall["skip"] += 1
            by_asset[asset]["skip"] += 1
            by_version[version]["skip"] += 1
        p = r.get("probability_declared")
        if p:
            declared_ps.append(p)

    # Calibración: acierto real vs probabilidad media declarada
    calibration = None
    if declared_ps:
        avg_p = sum(declared_ps) / len(declared_ps)
        total_eval = overall["hit"] + overall["miss"]
        actual = (overall["hit"] / total_eval * 100) if total_eval else 0
        calibration = {
            "probability": round(avg_p, 1),
            "actual": round(actual, 1),
        }
    overall["calibration"] = calibration

    return {
        "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
        "aggregated": {
            "overall": overall,
            "by_asset": dict(by_asset),
            "by_version": dict(by_version) if by_version else {},
        },
    }


# ═══════════════════════════════════════════════════════
# PERSISTENCIA
# ═══════════════════════════════════════════════════════

def write_back_calls(path: str, entries: list[dict], results: list[dict]) -> None:
    """Actualiza `evaluated` en calls.jsonl en base a los resultados."""
    by_key = defaultdict(list)
    for r in results:
        by_key[r.get("timestamp")].append(r)

    lines_out = []
    for e in entries:
        ts = e.get("timestamp")
        if ts in by_key:
            e["evaluated"] = True
            e["eval"] = by_key[ts]
        lines_out.append(json.dumps(e, default=str))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines_out) + "\n")