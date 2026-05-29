"""
╔══════════════════════════════════════════════════════╗
║       INDICADORES TÉCNICOS (numpy/pandas puro)        ║
║  Evita pandas-ta para compatibilidad con numpy 2.x   ║
╚══════════════════════════════════════════════════════╝

Todas las funciones reciben pd.Series / pd.DataFrame y devuelven
pd.Series. Funciones de conveniencia al final devuelven dicts con
el último valor calculado (lo que el LLM consumirá).
"""

from __future__ import annotations

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("MarketBrief")


# ═══════════════════════════════════════════════════════
# INDICADORES BASE
# ═══════════════════════════════════════════════════════

def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder)."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    # Wilder smoothing = EMA con alpha = 1/period
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> dict[str, pd.Series]:
    """MACD: line, signal, histogram."""
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "hist": hist}


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range. df debe tener columns 'high', 'low', 'close'."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def bbands_percent(series: pd.Series, period: int = 20,
                   num_std: float = 2.0) -> pd.Series:
    """Bollinger %B: posición del precio dentro de las bandas (0=lower, 1=upper)."""
    mid = sma(series, period)
    std = series.rolling(window=period, min_periods=period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return (series - lower) / (upper - lower)


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index — fuerza de tendencia (no dirección)."""
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = ((up_move > down_move) & (up_move > 0)) * up_move
    minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1 / period, adjust=False).mean()

    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def vwap_session(df: pd.DataFrame) -> float:
    """VWAP intradía simple (toda la sesión)."""
    if df.empty:
        return float("nan")
    pv = (df["close"] * df["volume"]).sum()
    v = df["volume"].sum()
    return pv / v if v > 0 else float(df["close"].iloc[-1])


# ═══════════════════════════════════════════════════════
# WRAPPER: compute_indicators_from_ohlcv
# ═══════════════════════════════════════════════════════

def _ohlcv_to_df(ohlcv) -> pd.DataFrame:
    """Convierte lista ccxt OHLCV [[ts,o,h,l,c,v], ...] o lista de dicts
    o pd.DataFrame yfinance a DataFrame estándar."""
    if isinstance(ohlcv, pd.DataFrame):
        cols_lower = {c.lower(): c for c in ohlcv.columns}
        df = pd.DataFrame({
            "open":   ohlcv[cols_lower.get("open", "Open")].astype(float),
            "high":   ohlcv[cols_lower.get("high", "High")].astype(float),
            "low":    ohlcv[cols_lower.get("low", "Low")].astype(float),
            "close":  ohlcv[cols_lower.get("close", "Close")].astype(float),
            "volume": ohlcv[cols_lower.get("volume", "Volume")].astype(float),
        })
        return df.reset_index(drop=True)

    if not ohlcv:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    first = ohlcv[0]
    if isinstance(first, dict):
        df = pd.DataFrame(ohlcv)
        df = df.rename(columns={c: c.lower() for c in df.columns})
    else:
        # ccxt format: [ts, o, h, l, c, v]
        df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low",
                                          "close", "volume"])
    return df[["open", "high", "low", "close", "volume"]].astype(float).reset_index(drop=True)


def compute_indicators(ohlcv, *, label: str = "") -> dict:
    """
    Computa los indicadores clave sobre una serie OHLCV y devuelve
    un dict con valores escalares listos para inyectar en el prompt.

    Compatible con:
      - listas ccxt: [[ts,o,h,l,c,v], ...]
      - DataFrames yfinance (con columnas Open/High/Low/Close/Volume)
      - listas de dicts {open,high,low,close,volume}
    """
    df = _ohlcv_to_df(ohlcv)
    if len(df) < 30:
        return {"error": f"datos insuficientes ({len(df)} velas)"}

    close = df["close"]
    last = close.iloc[-1]

    out = {"label": label, "last_close": round(float(last), 4)}

    # EMAs
    for p in (20, 55, 200):
        if len(df) >= p:
            v = ema(close, p).iloc[-1]
            out[f"ema{p}"] = round(float(v), 4)
            out[f"price_vs_ema{p}_pct"] = round(((last - v) / v) * 100, 2)
        else:
            out[f"ema{p}"] = None

    # Alineación: alcista si EMA20>EMA55>EMA200, bajista si invertido
    e20, e55, e200 = out.get("ema20"), out.get("ema55"), out.get("ema200")
    if e20 and e55 and e200:
        if e20 > e55 > e200:
            out["ema_alignment"] = "alcista"
        elif e20 < e55 < e200:
            out["ema_alignment"] = "bajista"
        else:
            out["ema_alignment"] = "mixto"

    # RSI
    rsi_val = rsi(close, 14).iloc[-1]
    out["rsi14"] = round(float(rsi_val), 1) if not np.isnan(rsi_val) else None

    # MACD
    m = macd(close)
    macd_v = m["macd"].iloc[-1]
    signal_v = m["signal"].iloc[-1]
    hist_v = m["hist"].iloc[-1]
    out["macd"] = round(float(macd_v), 4) if not np.isnan(macd_v) else None
    out["macd_signal"] = round(float(signal_v), 4) if not np.isnan(signal_v) else None
    out["macd_hist"] = round(float(hist_v), 4) if not np.isnan(hist_v) else None
    if not np.isnan(hist_v):
        out["macd_state"] = (
            "alcista expandiendo" if macd_v > signal_v and hist_v > 0 and hist_v > m["hist"].iloc[-2]
            else "alcista contrayendo" if macd_v > signal_v
            else "bajista expandiendo" if hist_v < 0 and hist_v < m["hist"].iloc[-2]
            else "bajista contrayendo"
        )

    # ATR (volatilidad)
    atr_val = atr(df, 14).iloc[-1]
    out["atr14"] = round(float(atr_val), 4) if not np.isnan(atr_val) else None
    if out["atr14"]:
        out["atr14_pct"] = round((out["atr14"] / float(last)) * 100, 2)

    # Bollinger %B
    bb_val = bbands_percent(close, 20, 2.0).iloc[-1]
    out["bb_percent"] = round(float(bb_val), 2) if not np.isnan(bb_val) else None

    # ADX (fuerza de tendencia)
    adx_val = adx(df, 14).iloc[-1]
    out["adx14"] = round(float(adx_val), 1) if not np.isnan(adx_val) else None
    if out["adx14"] is not None:
        out["trend_strength"] = (
            "fuerte" if out["adx14"] > 25
            else "débil" if out["adx14"] < 20
            else "moderada"
        )

    return out
