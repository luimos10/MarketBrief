"""
╔══════════════════════════════════════════════════════════════╗
║        EVALUATOR v2 — Evaluación honesta del logbook          ║
║                                                                ║
║   Cambios clave vs v1:                                         ║
║   · Dirección medida con el close en call_ts + horizon, no con ║
║     el último precio disponible.                               ║
║   · Sólo se evalúan calls accionables (decision LONG/SHORT).   ║
║     El sesgo se mide aparte como `bias_hit` (informativo).     ║
║   · Fill obligatorio: sin tocar la entrada no hay trade.       ║
║   · TP/SL conservador: si stop y TP tocan la misma vela, gana  ║
║     el stop. Sin runner: el primer target cierra la posición.  ║
║   · R real calculado desde niveles + costos por activo.        ║
║   · Calibración usa `probability` (no `confidence`) y Brier.   ║
║   · Umbral direccional por activo (no 0.5% fijo).              ║
╚══════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests
import yfinance as yf

from modules.logbook import normalize_structured

logger = logging.getLogger("MarketBrief")

EVAL_VERSION = 2

# Config por activo: ticker de historia, umbral direccional (% relativo) y
# costo round-trip asumido (% del precio). Los umbrales reflejan la vol
# típica de cada activo para que un "acierto" no sea ruido.
ASSET_EVAL_CONFIG = {
    "BTC":    {"binance": "BTCUSDT", "yf_fallback": "BTC-USD",
               "threshold_pct": 1.00, "cost_pct": 0.10, "max_move_pct": 25},
    "ETH":    {"binance": "ETHUSDT", "yf_fallback": "ETH-USD",
               "threshold_pct": 1.20, "cost_pct": 0.12, "max_move_pct": 30},
    "SOL":    {"binance": "SOLUSDT", "yf_fallback": "SOL-USD",
               "threshold_pct": 1.50, "cost_pct": 0.15, "max_move_pct": 35},
    "SP500":  {"yf": "ES=F",      "threshold_pct": 0.40, "cost_pct": 0.05, "max_move_pct": 10},
    "NASDAQ": {"yf": "NQ=F",      "threshold_pct": 0.50, "cost_pct": 0.05, "max_move_pct": 12},
    "GOLD":   {"yf": "GC=F",      "threshold_pct": 0.50, "cost_pct": 0.06, "max_move_pct": 12},
    "SILVER": {"yf": "SI=F",      "threshold_pct": 0.70, "cost_pct": 0.08, "max_move_pct": 20},
    "OIL":    {"yf": "CL=F",      "threshold_pct": 0.80, "cost_pct": 0.08, "max_move_pct": 20},
    "DXY":    {"yf": "DX-Y.NYB",  "threshold_pct": 0.25, "cost_pct": 0.05, "max_move_pct": 5},
    "VIX":    {"yf": "^VIX",      "threshold_pct": 3.00, "cost_pct": 0.10, "max_move_pct": 100},
    # Yields: se miden en puntos porcentuales (pp), no en % relativo.
    "US10Y":  {"yf": "^TNX", "interval": "1d", "unit": "pp",
               "threshold_pp": 0.05, "cost_pp": 0.01, "max_pp": 0.50},
    "US02Y":  {"yf": "2YY=F", "interval": "1d", "unit": "pp",
               "threshold_pp": 0.05, "cost_pp": 0.01, "max_pp": 0.50},
}

# Features del logbook que se copian a la fila de evaluación (prefijo f_)
# para poder analizar qué condiciones (RSI, ATR, sesgo, distancia de stop…)
# tienen edge, no sólo el resultado.
_FEATURE_COPY_KEYS = (
    "rsi14", "atr14_pct", "vol_realized_30d_pct", "max_drawdown_30d_pct",
    "z_score_30d", "ema_alignment", "adx14", "trend_strength", "macd_state",
    "bb_percent", "price_vs_ema200_pct", "bias_score", "risk_pct",
    "risk_atr", "tp1_rr", "entry_vs_price_pct",
)

BINANCE_HISTORY_PAIRS = {
    alias: cfg["binance"]    for alias, cfg in ASSET_EVAL_CONFIG.items()
    if cfg.get("binance")
}
YF_HISTORY_TICKERS = {
    alias: cfg["yf"]
    for alias, cfg in ASSET_EVAL_CONFIG.items()
    if cfg.get("yf")
}

DEFAULT_THRESHOLD_PCT = 0.50
DEFAULT_COST_PCT = 0.05

BINANCE_HOSTS = (
    "https://api.binance.com",
    "https://data-api.binance.vision",
)

ACTIONABLE = ("LONG", "SHORT")

_tier_order = {"P": 0, "M": 1, "W": 2}


def _to_float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _asset_cfg(asset: str) -> dict:
    return ASSET_EVAL_CONFIG.get(asset.upper(), {})


# ═══════════════════════════════════════════════════════
# HISTORIA DE PRECIOS (para validar niveles/TP/SL)
# ═══════════════════════════════════════════════════════

def _binance_klines(asset: str, start: datetime, end: datetime) -> list[dict]:
    pair = BINANCE_HISTORY_PAIRS[asset]
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    rows = []
    cursor = start_ms

    while cursor < end_ms:
        payload = None
        for host in BINANCE_HOSTS:
            try:
                r = requests.get(
                    f"{host}/api/v3/klines",
                    params={
                        "symbol": pair,
                        "interval": "1h",
                        "startTime": cursor,
                        "endTime": end_ms,
                        "limit": 1000,
                    },
                    timeout=15,
                )
                if r.status_code == 200:
                    payload = r.json()
                    break
            except Exception:
                continue
        if not payload:
            break
        for k in payload:
            rows.append({
                "ts": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
            })
        cursor = payload[-1][0] + 1

    rows.sort(key=lambda r: r["ts"])
    return rows


def _yf_klines(ticker: str, start: datetime, end: datetime,
               interval: str = "1h") -> list[dict]:
    # yfinance trata `end` como exclusivo: sumamos un día para incluir el cierre.
    end_inclusive = (end + timedelta(days=1)).strftime("%Y-%m-%d")
    df = yf.download(
        ticker,
        interval=interval,
        start=start.strftime("%Y-%m-%d"),
        end=end_inclusive,
        progress=False,
        auto_adjust=True,
        group_by="ticker",
        threads=False,
    )
    if df is None or df.empty:
        return []

    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(-1)

    rows = []
    for idx, row in df.iterrows():
        try:
            ts = idx.to_pydatetime()
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            high = float(row["High"])
            low = float(row["Low"])
            close = float(row["Close"])
        except (TypeError, ValueError, KeyError):
            continue
        if any(v != v for v in (high, low, close)):  # NaN
            continue
        rows.append({"ts": ts, "high": high, "low": low, "close": close})

    rows.sort(key=lambda r: r["ts"])
    return rows


def fetch_history(asset: str, start: datetime, end: datetime) -> list[dict]:
    """Devuelve velas horarias (ts, high, low, close) entre start y end."""
    asset = asset.upper().strip()
    cfg = _asset_cfg(asset)

    if asset in BINANCE_HISTORY_PAIRS:
        try:
            rows = _binance_klines(asset, start, end)
            if rows:
                return rows
        except Exception as e:
            logger.warning(f"  [eval] Sin historia Binance para {asset}: {e}")
        fallback = cfg.get("yf_fallback")
        if fallback:
            try:
                return _yf_klines(fallback, start, end)
            except Exception as e:
                logger.warning(f"  [eval] Sin fallback yfinance para {asset}: {e}")
        return []

    if asset in YF_HISTORY_TICKERS:
        try:
            return _yf_klines(
                YF_HISTORY_TICKERS[asset], start, end,
                interval=cfg.get("interval", "1h"),
            )
        except Exception as e:
            logger.warning(f"  [eval] Sin historia yfinance para {asset}: {e}")
            return []

    return []


# ═══════════════════════════════════════════════════════
# NORMALIZACIÓN
# ═══════════════════════════════════════════════════════

def _bias_to_direction(bias) -> str | None:
    """Normaliza el sesgo del activo al esquema rich (numérico) o legacy (string)."""
    if bias is None or isinstance(bias, bool):
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


def normalize_decision(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip().upper().replace("_", " ").replace("-", " ")
    s = " ".join(s.split())
    if s in ("LONG", "BUY", "COMPRA", "ALCISTA"):
        return "LONG"
    if s in ("SHORT", "SELL", "VENTA", "BAJISTA"):
        return "SHORT"
    if s in ("WAIT", "ESPERAR", "NEUTRAL", ""):
        return "WAIT"
    if s in ("NO TRADE", "NO TRADING", "NO OPERAR", "SIN TRADE"):
        return "NO TRADE"
    return None


def infer_direction(decision, entry: float | None, stop: float | None) -> str | None:
    """Dirección operativa. `decision` manda; si falta, se infiere del stop."""
    d = normalize_decision(decision)
    if d in ACTIONABLE:
        return d
    if d in ("WAIT", "NO TRADE"):
        return None
    if entry is not None and stop is not None:
        if stop < entry:
            return "LONG"
        if stop > entry:
            return "SHORT"
    return None


def _levels_for(direction: str, entry: float, stop: float,
                tp1: float | None, tp2: float | None) -> tuple[bool, str]:
    """Valida que los niveles sean coherentes con la dirección."""
    if entry <= 0 or stop <= 0:
        return False, "entry/stop inválidos"
    if direction == "LONG":
        if stop >= entry:
            return False, "LONG con stop >= entry"
        if tp1 is not None and tp1 <= entry:
            return False, "LONG con tp1 <= entry"
        if tp1 is not None and tp2 is not None and tp2 < tp1:
            return False, "LONG con tp2 < tp1"
    else:
        if stop <= entry:
            return False, "SHORT con stop <= entry"
        if tp1 is not None and tp1 >= entry:
            return False, "SHORT con tp1 >= entry"
        if tp1 is not None and tp2 is not None and tp2 > tp1:
            return False, "SHORT con tp2 > tp1"
    return True, ""


def _rr(entry: float, stop: float, tp: float | None) -> float | None:
    if tp is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    reward = abs(tp - entry)
    if (tp - entry) * (entry - stop) <= 0:
        return None
    return reward / risk


# ═══════════════════════════════════════════════════════
# EVALUACIÓN DE UN CALL
# ═══════════════════════════════════════════════════════

def evaluate_asset_call(asset: str, call: dict, snapshot_price: float | None,
                        bars_window: list[dict] | None = None,
                        complete: bool = True,
                        horizon_hours: int = 24) -> dict:
    """Evalúa dirección (sesgo + decisión) y ejecución de niveles."""
    cfg = _asset_cfg(asset)
    unit = cfg.get("unit", "pct")
    if unit == "pp":
        threshold = float(cfg.get("threshold_pp", 0.05))
        cost = float(cfg.get("cost_pp", 0.01))
        max_move = float(cfg.get("max_pp", 0.50))
    else:
        threshold = float(cfg.get("threshold_pct", DEFAULT_THRESHOLD_PCT))
        cost = float(cfg.get("cost_pct", DEFAULT_COST_PCT))
        max_move = float(cfg.get("max_move_pct", 50.0))

    bias = _bias_to_direction(call.get("bias") or call.get("sesgo"))
    entry = _to_float(call.get("entry") or call.get("soporte")
                      or call.get("precio_referencia"))
    stop = _to_float(call.get("stop") or call.get("invalidacion"))
    tp1 = _to_float(call.get("tp1"))
    tp2 = _to_float(call.get("tp2"))
    decision = normalize_decision(call.get("decision"))
    direction = infer_direction(decision, entry, stop)

    result = {
        "asset": asset,
        "decision": decision or "UNKNOWN",
        "direction": direction,
        "horizon_hours": horizon_hours,
        "complete": complete,
        "unit": unit,
        "threshold": threshold,
        "cost": cost,
        "hit": None,
        "bias_hit": None,
        "reason": None,
    }

    if snapshot_price:
        result["snapshot_price"] = snapshot_price

    # ── 1) Movimiento al cierre del horizonte ──
    exit_price = None
    if bars_window:
        if complete:
            exit_price = bars_window[-1]["close"]
        else:
            result["reason"] = "ventana incompleta"
    else:
        result["reason"] = "sin velas en la ventana"

    if snapshot_price and exit_price:
        if unit == "pp":
            move = exit_price - snapshot_price
        else:
            move = (exit_price - snapshot_price) / snapshot_price * 100

        result["exit_price"] = exit_price
        if unit == "pp":
            result["move_pp"] = round(move, 3)
        else:
            result["move_pct"] = round(move, 2)

        if abs(move) > max_move:
            # Salto imposible para el activo: mezcla de instrumentos (ej. el
            # viejo US02Y=^IRX vs 2YY=F) o dato corrupto. No se puntúa.
            result["reason"] = "movimiento anómalo (posible cambio de instrumento)"
            return result

        # Sesgo: métrica informativa, independiente de la decisión.
        if bias == "alcista":
            result["bias_hit"] = move > threshold
        elif bias == "bajista":
            result["bias_hit"] = move < -threshold
        elif bias == "neutral":
            result["bias_hit"] = abs(move) <= threshold

        # Decisión: sólo LONG/SHORT son calls operables. El costo se exige
        # como colchón adicional para no contar como acierto un movimiento
        # que no cubre fricción.
        if direction == "LONG":
            result["hit"] = move > threshold + cost
        elif direction == "SHORT":
            result["hit"] = move < -(threshold + cost)
        elif complete:
            result["reason"] = "sin decisión operativa (WAIT/NO TRADE)"
    elif complete and result["reason"] is None:
        result["reason"] = "sin precio de referencia"

    # Calibración: sólo `probability` es una probabilidad declarada.
    # `confidence` mide convicción del setup, no P(acierto), y mezclarlas
    # sesga el Brier score.
    declared_p = _to_float(call.get("probability"))
    if declared_p is not None:
        result["probability_declared"] = declared_p

    # ── 2) Ejecución: entry/stop/tp1/tp2 sobre velas post-call ──
    if not bars_window or direction is None:
        return result

    if entry is None or stop is None:
        result["execution_reason"] = "sin entry/stop"
        return result

    valid, why = _levels_for(direction, entry, stop, tp1, tp2)
    result["levels_valid"] = valid
    if not valid:
        result["execution_reason"] = why
        result["level_hit"] = None
        return result

    risk = abs(entry - stop)
    rr_tp1 = _rr(entry, stop, tp1)
    rr_tp2 = _rr(entry, stop, tp2)
    if rr_tp1 is not None:
        result["rr_calc_tp1"] = round(rr_tp1, 2)
    if rr_tp2 is not None:
        result["rr_calc_tp2"] = round(rr_tp2, 2)
    declared_rr = _to_float(call.get("rr"))
    if declared_rr is not None:
        result["rr_declared"] = declared_rr

    if unit == "pp":
        cost_r = cost / risk if risk > 0 else 0.0
    else:
        cost_r = (entry * cost / 100) / risk if risk > 0 else 0.0
    result["cost_r"] = round(cost_r, 3)

    filled = False
    outcome = None
    for bar in bars_window:
        hi, lo = bar["high"], bar["low"]

        if not filled:
            # Fill sólo si el precio operó el nivel de entrada.
            if lo <= entry <= hi:
                filled = True
            else:
                continue

        # Conservador: si stop y target tocan la misma vela, gana el stop.
        if direction == "LONG":
            if lo <= stop:
                outcome = "stop"
                break
            if tp1 is not None and hi >= tp1:
                outcome = "tp1"
                break
            if tp2 is not None and hi >= tp2:
                outcome = "tp2"
                break
        else:
            if hi >= stop:
                outcome = "stop"
                break
            if tp1 is not None and lo <= tp1:
                outcome = "tp1"
                break
            if tp2 is not None and lo <= tp2:
                outcome = "tp2"
                break

    if not filled:
        result["outcome"] = "no_fill"
        result["level_hit"] = None
        result["r_realized"] = None
        return result

    result["filled"] = True
    if outcome is None:
        result["outcome"] = "pending" if not complete else "none"
        result["level_hit"] = None
        result["r_realized"] = None
        return result

    result["outcome"] = outcome
    if outcome == "stop":
        result["level_hit"] = False
        result["r_realized"] = round(-1.0 - cost_r, 3)
    else:
        result["level_hit"] = True
        base_rr = rr_tp2 if outcome == "tp2" else rr_tp1
        result["r_realized"] = (
            round(base_rr - cost_r, 3) if base_rr is not None else None
        )

    return result


def evaluate_call(entry: dict, histories: dict, horizon_hours: int = 24,
                  latest_prices: dict = None) -> dict:
    """Evalúa todas las entradas de activos de un call del logbook."""
    structured = normalize_structured(entry.get("structured"))
    assets = (structured or {}).get("assets") or {}
    snap = entry.get("snapshot") or {}
    snap_prices = snap.get("prices", {})
    ts = entry.get("timestamp", "")
    call_id = entry.get("call_id")

    try:
        call_ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if call_ts.tzinfo is None:
            call_ts = call_ts.replace(tzinfo=timezone.utc)
    except Exception:
        call_ts = datetime.now(timezone.utc)

    window_start = call_ts
    window_end = call_ts + timedelta(hours=horizon_hours)
    complete = datetime.now(timezone.utc) >= window_end

    internal_names = {
        "BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
        "SP500": "S&P 500 (ES)", "NASDAQ": "Nasdaq 100 (NQ)",
        "GOLD": "Gold (XAUUSD)", "SILVER": "Silver (XAGUSD)",
        "OIL": "Crude Oil (WTI)",
    }

    evals = []
    features_root = entry.get("features") or {}
    if not isinstance(features_root, dict):
        features_root = {}

    for alias, call in assets.items():
        if not isinstance(call, dict):
            continue

        internal = internal_names.get(alias, alias)
        sp = (snap_prices.get(alias)
              or snap_prices.get(internal)
              or call.get("price")
              or call.get("precio_referencia"))
        sp = _to_float(sp) if sp not in (None, "", 0) else None

        bars_window = None
        hist = histories.get(alias.upper())
        if hist:
            bars_window = [b for b in hist
                           if window_start <= b["ts"] < window_end]

        r = evaluate_asset_call(
            alias.upper(), call, sp, bars_window,
            complete=complete, horizon_hours=horizon_hours,
        )
        r["timestamp"] = ts
        r["call_id"] = call_id
        r["session"] = entry.get("session", "unknown")
        r["prompt_version"] = entry.get("prompt_version", "unknown")
        r["eval_version"] = EVAL_VERSION

        conf = _to_float(call.get("confidence"))
        if conf is not None:
            r["confidence_declared"] = conf
        if call.get("signal_quality"):
            r["signal_quality"] = call["signal_quality"]

        feat = features_root.get(alias) or {}
        if isinstance(feat, dict):
            for k in _FEATURE_COPY_KEYS:
                v = feat.get(k)
                if v is not None:
                    r[f"f_{k}"] = v

        evals.append(r)

    return evals


# ═══════════════════════════════════════════════════════
# AGREGACIÓN + PERF PAYLOAD
# ═══════════════════════════════════════════════════════

def _empty_stats() -> dict:
    return {"hit": 0, "miss": 0, "skip": 0}


def _add(stats: dict, value) -> None:
    if value is True:
        stats["hit"] += 1
    elif value is False:
        stats["miss"] += 1
    else:
        stats["skip"] += 1


def _rate(stats: dict) -> float | None:
    total = stats["hit"] + stats["miss"]
    return round(stats["hit"] / total * 100, 1) if total else None


def _normalize_probability(value) -> float | None:
    p = _to_float(value)
    if p is None:
        return None
    if p > 1:
        p /= 100
    if not 0 <= p <= 1:
        return None
    return p


def _bucket_probability(value) -> str:
    p = _normalize_probability(value)
    if p is None:
        return "n/d"
    if p < 0.50:
        return "<50%"
    if p < 0.60:
        return "50-60%"
    if p < 0.70:
        return "60-70%"
    return ">=70%"


def _bucket_confidence(value) -> str:
    c = _to_float(value)
    if c is None:
        return "n/d"
    if c <= 1:
        c *= 100
    if c < 50:
        return "<50"
    if c < 60:
        return "50-60"
    if c < 70:
        return "60-70"
    return ">=70"


def _bucket_risk_atr(value) -> str:
    x = _to_float(value)
    if x is None:
        return "n/d"
    if x < 0.8:
        return "<0.8 ATR"
    if x < 1.5:
        return "0.8-1.5 ATR"
    if x < 2.5:
        return "1.5-2.5 ATR"
    return ">=2.5 ATR"


def _bucket_bias(value) -> str:
    x = _to_float(value)
    if x is None:
        return "n/d"
    if x < -25:
        return "bajista (<-25)"
    if x > 25:
        return "alcista (>25)"
    return "neutral (-25..25)"


def build_perf_payload(results: list[dict], by_version: bool = True) -> dict:
    """Convierte registros de eval en el payload agregado del dashboard HTML."""
    overall = _empty_stats()
    bias_overall = _empty_stats()
    by_asset = defaultdict(_empty_stats)
    by_version = defaultdict(_empty_stats)
    by_session = defaultdict(_empty_stats)
    by_decision = defaultdict(_empty_stats)
    by_probability = defaultdict(_empty_stats)
    by_confidence = defaultdict(_empty_stats)
    by_quality = defaultdict(_empty_stats)
    by_risk_atr = defaultdict(_empty_stats)
    by_bias = defaultdict(_empty_stats)
    asset_exec = defaultdict(
        lambda: {"tp1": 0, "tp2": 0, "stop": 0,
                 "no_fill": 0, "pending": 0, "none": 0})
    asset_r = defaultdict(list)
    execution = {"tp1": 0, "tp2": 0, "stop": 0,
                 "no_fill": 0, "pending": 0, "none": 0}
    r_values = []
    probs = []
    brier_terms = []

    for r in results:
        if not isinstance(r, dict):
            continue
        asset = r.get("asset", "?")
        version = r.get("prompt_version") or "unknown"
        session = r.get("session") or "unknown"
        decision = r.get("decision") or "UNKNOWN"

        _add(overall, r.get("hit"))
        _add(bias_overall, r.get("bias_hit"))
        _add(by_asset[asset], r.get("hit"))
        _add(by_version[version], r.get("hit"))
        _add(by_session[session], r.get("hit"))
        _add(by_decision[decision], r.get("hit"))
        _add(by_probability[_bucket_probability(
            r.get("probability_declared"))], r.get("hit"))
        _add(by_confidence[_bucket_confidence(
            r.get("confidence_declared"))], r.get("hit"))
        _add(by_quality[r.get("signal_quality") or "n/d"], r.get("hit"))
        _add(by_risk_atr[_bucket_risk_atr(
            r.get("f_risk_atr"))], r.get("hit"))
        _add(by_bias[_bucket_bias(r.get("f_bias_score"))], r.get("hit"))

        outcome = r.get("outcome")
        if outcome:
            execution[outcome] = execution.get(outcome, 0) + 1
            asset_exec[asset][outcome] = asset_exec[asset].get(outcome, 0) + 1

        r_realized = _to_float(r.get("r_realized"))
        if r_realized is not None:
            r_values.append(r_realized)
            asset_r[asset].append(r_realized)

        p = _normalize_probability(r.get("probability_declared"))
        if p is not None and r.get("hit") is not None:
            probs.append(p)
            y = 1.0 if r.get("hit") is True else 0.0
            brier_terms.append((p - y) ** 2)

    calibration = None
    if probs:
        calibration = {
            "probability": round(sum(probs) / len(probs) * 100, 1),
            "actual": _rate(overall),
            "brier": round(sum(brier_terms) / len(brier_terms), 4),
            "n": len(probs),
        }
    overall["calibration"] = calibration
    overall["hit_rate"] = _rate(overall)
    bias_overall["hit_rate"] = _rate(bias_overall)

    asset_payload = {}
    for asset, stats in by_asset.items():
        stats["hit_rate"] = _rate(stats)
        ex = asset_exec.get(asset)
        if ex and any(v for v in ex.values()):
            rs = asset_r.get(asset) or []
            ex["r_mean"] = round(sum(rs) / len(rs), 3) if rs else None
            ex["n_r"] = len(rs)
            stats["execution"] = ex
        asset_payload[asset] = stats

    execution["r_sum"] = round(sum(r_values), 2) if r_values else None
    execution["r_mean"] = (
        round(sum(r_values) / len(r_values), 3) if r_values else None
    )
    execution["n_r"] = len(r_values)

    def _buckets(source):
        return {k: {**v, "hit_rate": _rate(v)} for k, v in source.items()}

    return {
        "schema_version": EVAL_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aggregated": {
            "overall": overall,
            "bias": bias_overall,
            "by_asset": asset_payload,
            "by_version": _buckets(by_version) if by_version else {},
            "by_session": _buckets(by_session),
            "by_decision": _buckets(by_decision),
            "by_probability": _buckets(by_probability),
            "by_confidence": _buckets(by_confidence),
            "by_quality": _buckets(by_quality),
            "by_risk_atr": _buckets(by_risk_atr),
            "by_bias": _buckets(by_bias),
            "execution": execution,
        },
    }


# ═══════════════════════════════════════════════════════
# PERSISTENCIA
# ═══════════════════════════════════════════════════════

def _atomic_write(path: str, content: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".jsonl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_back_calls(path: str, entries: list[dict], results: list[dict]) -> None:
    """Actualiza `evaluated` y `eval` en calls.jsonl (escritura atómica)."""
    by_key = defaultdict(list)
    for r in results:
        key = r.get("call_id") or r.get("timestamp")
        by_key[key].append(r)

    lines_out = []
    for e in entries:
        key = e.get("call_id") or e.get("timestamp")
        if key in by_key:
            e["evaluated"] = True
            e["eval"] = by_key[key]
        lines_out.append(json.dumps(e, default=str, ensure_ascii=False))

    _atomic_write(path, "\n".join(lines_out) + "\n")


def load_eval_results(path: str) -> list[dict]:
    """Carga sólo filas v2. Las legacy tenían el bug de horizonte."""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("eval_version") == EVAL_VERSION:
                rows.append(r)
    return rows


def merge_eval_results(existing: list[dict], new: list[dict]) -> list[dict]:
    """Reemplaza por (call_id|timestamp, asset) y conserva el resto."""
    merged = {}
    for r in existing + new:
        key = (r.get("call_id") or r.get("timestamp"), r.get("asset"))
        merged[key] = r
    return sorted(merged.values(), key=lambda r: (r.get("timestamp") or "", r.get("asset") or ""))


def write_eval_results(path: str, rows: list[dict]) -> None:
    payload = "\n".join(
        json.dumps(r, default=str, ensure_ascii=False) for r in rows
    )
    _atomic_write(path, payload + ("\n" if payload else ""))
