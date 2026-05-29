"""
╔══════════════════════════════════════════════════════╗
║         MÓDULO DE RECOLECCIÓN DE DATOS                ║
║  Obtiene precios, derivados, y métricas de mercado    ║
╚══════════════════════════════════════════════════════╝
"""

import yfinance as yf
import ccxt
import requests
import json
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

from modules.cache import cached

logger = logging.getLogger("MarketBrief")


@cached("yfinance_daily")
def fetch_yfinance_data(symbols: dict) -> dict:
    """
    Obtiene datos de precios desde Yahoo Finance.
    Incluye: precio actual, cambio %, VWAP aproximado, volumen.
    Cacheado: 1h (velas diarias rara vez cambian intradía).
    """
    results = {}
    for name, ticker in symbols.items():
        try:
            asset = yf.Ticker(ticker)

            # Datos intraday (últimos 5 días, intervalos de 1h)
            hist_1h = asset.history(period="5d", interval="1h")

            # Datos daily (últimos 30 días)
            hist_daily = asset.history(period="30d", interval="1d")

            if hist_daily.empty:
                results[name] = {"error": "Sin datos disponibles"}
                continue

            current_price = hist_daily["Close"].iloc[-1]
            prev_close = hist_daily["Close"].iloc[-2] if len(
                hist_daily) > 1 else current_price

            # Cambio 24h
            change_24h = ((current_price - prev_close) / prev_close) * 100

            # Cambio 7d
            if len(hist_daily) >= 7:
                price_7d_ago = hist_daily["Close"].iloc[-7]
                change_7d = ((current_price - price_7d_ago) /
                             price_7d_ago) * 100
            else:
                change_7d = None

            # Volumen promedio 20d
            vol_20d_avg = hist_daily["Volume"].tail(20).mean() if len(
                hist_daily) >= 20 else hist_daily["Volume"].mean()
            vol_current = hist_daily["Volume"].iloc[-1]
            vol_ratio = vol_current / vol_20d_avg if vol_20d_avg > 0 else 0

            # VWAP diario aproximado
            if not hist_1h.empty and len(hist_1h) > 0:
                today_data = hist_1h.tail(24)
                if len(today_data) > 0 and today_data["Volume"].sum() > 0:
                    vwap = (today_data["Close"] * today_data["Volume"]
                            ).sum() / today_data["Volume"].sum()
                else:
                    vwap = current_price
            else:
                vwap = current_price

            # Niveles clave (máximos y mínimos recientes)
            high_20d = hist_daily["High"].tail(20).max()
            low_20d = hist_daily["Low"].tail(20).min()
            high_5d = hist_daily["High"].tail(5).max()
            low_5d = hist_daily["Low"].tail(5).min()

            results[name] = {
                "precio_actual": round(current_price, 4),
                "cambio_24h_pct": round(change_24h, 2),
                "cambio_7d_pct": round(change_7d, 2) if change_7d else "N/A",
                "volumen_actual": int(vol_current),
                "volumen_avg_20d": int(vol_20d_avg),
                "volumen_ratio": round(vol_ratio, 2),
                "vwap_diario": round(vwap, 4),
                "high_20d": round(high_20d, 4),
                "low_20d": round(low_20d, 4),
                "high_5d": round(high_5d, 4),
                "low_5d": round(low_5d, 4),
                "precio_sobre_vwap": current_price > vwap,
            }

        except Exception as e:
            logger.error(f"Error obteniendo datos de {name}: {e}")
            results[name] = {"error": str(e)}

    return results


@cached("crypto_realtime")
def fetch_crypto_data(symbol_pair: str = "BTC/USDT",
                      symbol_raw: str = "BTCUSDT") -> dict:
    """
    Obtiene datos de derivados crypto desde Binance para UN símbolo.
    Incluye: OI, funding rate, precios de futuros, L/S ratios, OHLCV.
    Cacheado: 60s (ticker realtime).
    """
    result = {}
    try:
        exchange = ccxt.binance({
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })

        ticker = exchange.fetch_ticker(symbol_pair)
        result.update({
            "precio": ticker.get("last"),
            "high_24h": ticker.get("high"),
            "low_24h": ticker.get("low"),
            "volumen_24h_usd": ticker.get("quoteVolume"),
            "cambio_24h_pct": ticker.get("percentage"),
        })

        # Funding Rate
        try:
            funding = exchange.fetch_funding_rate(symbol_pair)
            result["funding_rate"] = funding.get("fundingRate")
            result["next_funding_time"] = str(funding.get("fundingDatetime", ""))
        except Exception as e:
            logger.warning(f"[{symbol_raw}] funding rate: {e}")
            result["funding_rate"] = "N/A"

        # Open Interest
        try:
            oi = exchange.fetch_open_interest(symbol_pair)
            result["open_interest"] = oi.get("openInterestAmount")
            result["open_interest_value"] = oi.get("openInterestValue")
        except Exception as e:
            logger.warning(f"[{symbol_raw}] OI: {e}")
            result["open_interest"] = "N/A"

        # Order Book
        try:
            ob = exchange.fetch_order_book(symbol_pair, limit=20)
            bid_depth = sum([b[1] for b in ob["bids"][:10]])
            ask_depth = sum([a[1] for a in ob["asks"][:10]])
            result["bid_depth_top10"] = round(bid_depth, 4)
            result["ask_depth_top10"] = round(ask_depth, 4)
            result["bid_ask_ratio"] = round(bid_depth / ask_depth, 2) \
                if ask_depth > 0 else "N/A"
        except Exception as e:
            logger.warning(f"[{symbol_raw}] order book: {e}")

        # OHLCV — Guardamos LISTA COMPLETA para indicadores (no solo last 5)
        try:
            candles_4h = exchange.fetch_ohlcv(symbol_pair, "4h", limit=200)
            candles_1d = exchange.fetch_ohlcv(symbol_pair, "1d", limit=200)
            result["_ohlcv_4h_full"] = candles_4h
            result["_ohlcv_1d_full"] = candles_1d
            result["candles_4h_last5"] = [
                {"open": c[1], "high": c[2], "low": c[3],
                 "close": c[4], "volume": c[5]}
                for c in candles_4h[-5:]
            ]
            result["candles_1d_last5"] = [
                {"open": c[1], "high": c[2], "low": c[3],
                 "close": c[4], "volume": c[5]}
                for c in candles_1d[-5:]
            ]
        except Exception as e:
            logger.warning(f"[{symbol_raw}] OHLCV: {e}")

        # Long/Short Ratio
        try:
            resp = requests.get(
                "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                params={"symbol": symbol_raw, "period": "1h", "limit": 5},
                timeout=10,
            )
            if resp.status_code == 200:
                ls_data = resp.json()
                latest = ls_data[0] if ls_data else {}
                result["long_short_ratio"] = latest.get("longShortRatio")
                result["long_account_pct"] = latest.get("longAccount")
                result["short_account_pct"] = latest.get("shortAccount")
                result["long_short_trend_5h"] = [
                    {"ratio": d.get("longShortRatio"),
                     "long_pct": d.get("longAccount"),
                     "short_pct": d.get("shortAccount")}
                    for d in ls_data
                ]
        except Exception as e:
            logger.warning(f"[{symbol_raw}] L/S ratio: {e}")
            result["long_short_ratio"] = "N/A"

        # Taker Buy/Sell Volume
        try:
            resp = requests.get(
                "https://fapi.binance.com/futures/data/takerlongshortRatio",
                params={"symbol": symbol_raw, "period": "1h", "limit": 5},
                timeout=10,
            )
            if resp.status_code == 200:
                taker_data = resp.json()
                latest = taker_data[0] if taker_data else {}
                result["taker_buy_sell_ratio"] = latest.get("buySellRatio")
                result["taker_buy_vol"] = latest.get("buyVol")
                result["taker_sell_vol"] = latest.get("sellVol")
        except Exception as e:
            logger.warning(f"[{symbol_raw}] taker volume: {e}")
            result["taker_buy_sell_ratio"] = "N/A"

        # Top Trader Sentiment
        try:
            resp_acc = requests.get(
                "https://fapi.binance.com/futures/data/topLongShortAccountRatio",
                params={"symbol": symbol_raw, "period": "1h", "limit": 5},
                timeout=10,
            )
            resp_pos = requests.get(
                "https://fapi.binance.com/futures/data/topLongShortPositionRatio",
                params={"symbol": symbol_raw, "period": "1h", "limit": 5},
                timeout=10,
            )
            if resp_acc.status_code == 200:
                acc_data = resp_acc.json()
                latest_acc = acc_data[0] if acc_data else {}
                result["top_trader_long_short_account"] = latest_acc.get("longShortRatio")
                result["top_trader_long_account_pct"] = latest_acc.get("longAccount")
                result["top_trader_short_account_pct"] = latest_acc.get("shortAccount")
            if resp_pos.status_code == 200:
                pos_data = resp_pos.json()
                latest_pos = pos_data[0] if pos_data else {}
                result["top_trader_long_short_position"] = latest_pos.get("longShortRatio")
                result["top_trader_long_position_pct"] = latest_pos.get("longAccount")
                result["top_trader_short_position_pct"] = latest_pos.get("shortAccount")
        except Exception as e:
            logger.warning(f"[{symbol_raw}] top trader sentiment: {e}")
            result["top_trader_long_short_account"] = "N/A"

    except Exception as e:
        # Binance bloquea IPs de datacenter (HTTP 451 desde GitHub Actions).
        # Fallback no geobloqueado para recuperar precio + OHLCV (indicadores/bias).
        logger.warning(
            f"[{symbol_raw}] Binance no disponible ({e}); "
            f"usando fallback data-api.binance.vision (spot)")
        fb = _fetch_crypto_fallback(symbol_raw)
        if "error" in fb:
            logger.error(
                f"Error en datos crypto {symbol_raw}: fallback falló ({fb['error']})")
            return {"error": fb["error"]}
        return fb

    return result


def _fetch_crypto_fallback(symbol_raw: str) -> dict:
    """
    Fallback cuando Binance bloquea la IP (HTTP 451 en runners de datacenter).
    Usa el mirror público de datos de Binance (data-api.binance.vision, solo
    spot, sin geobloqueo). Recupera precio + OHLCV para que indicadores y bias
    se calculen. Los datos de futuros (funding, OI, L/S) quedan como "N/A".
    """
    base = "https://data-api.binance.vision"
    out: dict = {"_fallback_source": "data-api.binance.vision (spot)"}
    try:
        t = requests.get(f"{base}/api/v3/ticker/24hr",
                         params={"symbol": symbol_raw}, timeout=10)
        if t.status_code != 200:
            return {"error": f"fallback ticker HTTP {t.status_code}"}
        td = t.json()
        out.update({
            "precio": float(td["lastPrice"]),
            "high_24h": float(td["highPrice"]),
            "low_24h": float(td["lowPrice"]),
            "volumen_24h_usd": float(td["quoteVolume"]),
            "cambio_24h_pct": float(td["priceChangePercent"]),
        })

        for tf, full_key, last5_key in (
            ("4h", "_ohlcv_4h_full", "candles_4h_last5"),
            ("1d", "_ohlcv_1d_full", "candles_1d_last5"),
        ):
            k = requests.get(f"{base}/api/v3/klines",
                             params={"symbol": symbol_raw, "interval": tf,
                                     "limit": 200}, timeout=10)
            if k.status_code != 200:
                continue
            # Mismo formato que ccxt OHLCV: [ts, open, high, low, close, volume]
            ohlcv = [[c[0], float(c[1]), float(c[2]), float(c[3]),
                      float(c[4]), float(c[5])] for c in k.json()]
            out[full_key] = ohlcv
            out[last5_key] = [
                {"open": c[1], "high": c[2], "low": c[3],
                 "close": c[4], "volume": c[5]}
                for c in ohlcv[-5:]
            ]

        # Datos de futuros: Binance fapi esta geobloqueado -> usamos OKX.
        out.update(_fetch_okx_futures(symbol_raw))
        out.setdefault("taker_buy_sell_ratio", "N/A")
        return out
    except Exception as e:
        return {"error": f"fallback: {e}"}


# ═══════════════════════════════════════════════════════
# OKX — Fuente de futuros cuando Binance geobloquea (HTTP 451)
# ═══════════════════════════════════════════════════════

OKX_BASE = "https://www.okx.com"


def _okx_inst(symbol_raw: str):
    """BTCUSDT -> ('BTC-USDT-SWAP', 'BTC')."""
    base = symbol_raw[:-4] if symbol_raw.endswith("USDT") else symbol_raw
    return f"{base}-USDT-SWAP", base


def _fetch_okx_futures(symbol_raw: str) -> dict:
    """Funding actual + Open Interest + Long/Short ratio desde OKX."""
    inst, ccy = _okx_inst(symbol_raw)
    out: dict = {"_futures_source": "okx"}

    try:
        r = requests.get(f"{OKX_BASE}/api/v5/public/funding-rate",
                         params={"instId": inst}, timeout=10)
        d = (r.json().get("data") or [{}])[0]
        if d.get("fundingRate") not in (None, ""):
            out["funding_rate"] = float(d["fundingRate"])
            out["next_funding_time"] = d.get("nextFundingTime", "")
    except Exception as e:
        logger.warning(f"[{symbol_raw}] OKX funding: {e}")

    try:
        r = requests.get(f"{OKX_BASE}/api/v5/public/open-interest",
                         params={"instType": "SWAP", "instId": inst}, timeout=10)
        d = (r.json().get("data") or [{}])[0]
        if d.get("oiCcy"):
            out["open_interest"] = float(d["oiCcy"])
            out["open_interest_value"] = (
                float(d["oiUsd"]) if d.get("oiUsd") else "N/A")
    except Exception as e:
        logger.warning(f"[{symbol_raw}] OKX OI: {e}")

    try:
        r = requests.get(
            f"{OKX_BASE}/api/v5/rubik/stat/contracts/long-short-account-ratio",
            params={"ccy": ccy, "period": "1H"}, timeout=10)
        data = r.json().get("data") or []   # [[ts, ratio], ...] newest-first
        if data:
            out["long_short_ratio"] = float(data[0][1])
            out["long_short_trend_5h"] = [{"ratio": float(x[1])} for x in data[:5]]
    except Exception as e:
        logger.warning(f"[{symbol_raw}] OKX L/S: {e}")

    out.setdefault("funding_rate", "N/A")
    out.setdefault("open_interest", "N/A")
    out.setdefault("long_short_ratio", "N/A")
    return out


def _okx_funding_history(inst: str, pages: int = 3) -> list:
    """Histórico de funding (paginado, ~100 por página), newest-first."""
    url = f"{OKX_BASE}/api/v5/public/funding-rate-history"
    collected, after = [], None
    for _ in range(pages):
        params = {"instId": inst, "limit": 100}
        if after:
            params["after"] = after
        r = requests.get(url, params=params, timeout=10)
        data = r.json().get("data") or []
        if not data:
            break
        collected.extend(data)
        after = data[-1]["fundingTime"]
    return collected


def _compute_funding_regime(rates: list, symbol: str) -> dict:
    """Régimen de funding a partir de tasas ascendentes (oldest→newest)."""
    if not rates:
        return {"error": "Sin datos"}

    last_7d = rates[-21:] if len(rates) >= 21 else rates
    avg_7d = sum(last_7d) / len(last_7d)
    max_7d = max(last_7d)
    min_7d = min(last_7d)

    latest = rates[-1]
    sorted_rates = sorted(rates)
    rank = sum(1 for r in sorted_rates if r <= latest)
    percentile = round((rank / len(sorted_rates)) * 100, 1)

    consec = 0
    sign = 1 if last_7d[-1] > 0 else -1
    for r in reversed(last_7d):
        if (r > 0 and sign > 0) or (r < 0 and sign < 0):
            consec += 1
        else:
            break

    if percentile > 90:
        regimen = "EXTREMO POSITIVO — riesgo long squeeze"
    elif percentile < 10:
        regimen = "EXTREMO NEGATIVO — riesgo short squeeze"
    elif percentile > 75:
        regimen = "Elevado positivo — posicionamiento alcista"
    elif percentile < 25:
        regimen = "Elevado negativo — posicionamiento bajista"
    else:
        regimen = "Neutral"

    return {
        "symbol": symbol,
        "funding_actual": latest,
        "funding_actual_pct": round(latest * 100, 4),
        "promedio_7d_pct": round(avg_7d * 100, 4),
        "max_7d_pct": round(max_7d * 100, 4),
        "min_7d_pct": round(min_7d * 100, 4),
        "percentil_90d": percentile,
        "dias_consecutivos_mismo_signo": consec // 3,  # 3 events = 1 día
        "regimen": regimen,
    }


def _fetch_funding_regime_okx(symbol_raw: str) -> dict:
    inst, _ = _okx_inst(symbol_raw)
    try:
        hist = _okx_funding_history(inst, pages=3)             # newest-first
        rates = [float(e["fundingRate"]) for e in reversed(hist)]  # ascending
        regime = _compute_funding_regime(rates, symbol_raw)
        if "error" not in regime:
            regime["_source"] = "okx"
        return regime
    except Exception as e:
        return {"error": f"OKX funding regime: {e}"}


def _liquidation_from_ohlcv(rows: list, symbol: str, hours: int) -> dict:
    """rows: [[ts_ms, open, high, low, close, volume], ...] (cualquier orden)."""
    wicks = []
    for c in rows:
        open_p, high, low, close, volume = (
            float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5]))
        upper_wick = high - max(open_p, close)
        lower_wick = min(open_p, close) - low
        total_range = high - low
        if total_range == 0:
            continue
        wicks.append({
            "timestamp": datetime.fromtimestamp(int(c[0]) / 1000, tz=timezone.utc)
                                   .strftime("%Y-%m-%d %H:%M UTC"),
            "high": high, "low": low, "close": close,
            "upper_wick_score": (upper_wick / total_range) * volume,
            "lower_wick_score": (lower_wick / total_range) * volume,
            "upper_wick_pct": round((upper_wick / total_range) * 100, 1),
            "lower_wick_pct": round((lower_wick / total_range) * 100, 1),
            "volume": volume,
        })
    top_upper = sorted(wicks, key=lambda x: x["upper_wick_score"], reverse=True)[:3]
    top_lower = sorted(wicks, key=lambda x: x["lower_wick_score"], reverse=True)[:3]
    return {
        "symbol": symbol,
        "lookback_hours": hours,
        "proxy_metodo": "wicks 1h ponderados por volumen",
        "top_short_squeeze_zones": [
            {"precio": round(z["high"], 2), "wick_pct": z["upper_wick_pct"],
             "timestamp": z["timestamp"]}
            for z in top_upper if z["upper_wick_pct"] > 30
        ],
        "top_long_liquidation_zones": [
            {"precio": round(z["low"], 2), "wick_pct": z["lower_wick_pct"],
             "timestamp": z["timestamp"]}
            for z in top_lower if z["lower_wick_pct"] > 30
        ],
    }


def _fetch_liquidation_okx(symbol_raw: str, hours: int) -> dict:
    inst, _ = _okx_inst(symbol_raw)
    try:
        r = requests.get(f"{OKX_BASE}/api/v5/market/candles",
                         params={"instId": inst, "bar": "1H",
                                 "limit": min(hours + 1, 300)}, timeout=10)
        data = r.json().get("data") or []   # [ts,o,h,l,c,vol,...] newest-first
        if not data:
            return {"error": "OKX sin velas"}
        rows = [[x[0], x[1], x[2], x[3], x[4], x[5]] for x in data]
        return _liquidation_from_ohlcv(rows, symbol_raw, hours)
    except Exception as e:
        return {"error": f"OKX liquidation: {e}"}


def fetch_all_crypto_data(symbols_map: dict) -> dict:
    """Llama fetch_crypto_data para cada símbolo en paralelo."""
    out = {}
    with ThreadPoolExecutor(max_workers=min(len(symbols_map), 4)) as pool:
        futures = {
            pool.submit(fetch_crypto_data, pair, raw): raw
            for pair, raw in symbols_map.items()
        }
        for fut in as_completed(futures):
            raw = futures[fut]
            try:
                out[raw] = fut.result()
            except Exception as e:
                out[raw] = {"error": str(e)}
    return out


@cached("fear_greed")
def fetch_fear_greed_index() -> dict:
    """Obtiene el Fear & Greed Index de crypto. Cacheado: 4h."""
    try:
        resp = requests.get(
            "https://api.alternative.me/fng/?limit=1", timeout=10)
        data = resp.json()
        if data.get("data"):
            entry = data["data"][0]
            return {
                "value": int(entry["value"]),
                "classification": entry["value_classification"],
                "timestamp": entry["timestamp"],
            }
    except Exception as e:
        logger.warning(f"No se pudo obtener Fear & Greed: {e}")
    return {"value": "N/A", "classification": "N/A"}


@cached("yfinance_daily")
def fetch_correlation_data() -> dict:
    """
    Obtiene precios históricos de los 6 activos para calcular correlaciones.
    Retorna los datos crudos; Claude calculará/estimará las correlaciones.
    """
    symbols = {
        "BTC": "BTC-USD",
        "GOLD": "GC=F",
        "SILVER": "SI=F",
        "SPX": "^GSPC",
        "NDX": "^NDX",
        "OIL": "CL=F",
    }

    results = {}
    for name, ticker in symbols.items():
        try:
            data = yf.Ticker(ticker).history(period="35d", interval="1d")
            if not data.empty:
                closes = data["Close"].tail(30).tolist()
                results[name] = {
                    "closes_30d": [round(c, 2) for c in closes],
                    "retornos_pct": [
                        round(
                            ((closes[i] - closes[i-1]) / closes[i-1]) * 100, 4)
                        for i in range(1, len(closes))
                    ]
                }
        except Exception as e:
            logger.warning(f"Error en correlación de {name}: {e}")
            results[name] = {"error": str(e)}

    return results


# ═══════════════════════════════════════════════════════
# COINMARKETCAP
# ═══════════════════════════════════════════════════════

@cached("cmc_global")
def fetch_cmc_data(api_key: str, slugs: list = None, convert: str = "USD") -> dict:
    """
    Obtiene datos enriquecidos de CoinMarketCap.
    Incluye: market cap, dominancia, supply, volumen global,
    métricas de mercado global y datos por cripto.
    """
    if not api_key:
        logger.warning("⚠ CMC_API_KEY no configurada — saltando CoinMarketCap")
        return {"error": "API key no configurada"}

    headers = {
        "Accepts": "application/json",
        "X-CMC_PRO_API_KEY": api_key,
    }
    base_url = "https://pro-api.coinmarketcap.com"
    results = {}

    # ── 1) Métricas globales del mercado crypto ──
    try:
        resp = requests.get(
            f"{base_url}/v1/global-metrics/quotes/latest",
            headers=headers,
            params={"convert": convert},
            timeout=15,
        )
        if resp.status_code == 200:
            gdata = resp.json().get("data", {})
            quote = gdata.get("quote", {}).get(convert, {})
            results["global_metrics"] = {
                "total_market_cap": round(quote.get("total_market_cap", 0), 0),
                "total_volume_24h": round(quote.get("total_volume_24h", 0), 0),
                "total_volume_24h_change": round(quote.get("total_volume_24h_yesterday_percentage_change", 0), 2),
                "btc_dominance": round(gdata.get("btc_dominance", 0), 2),
                "eth_dominance": round(gdata.get("eth_dominance", 0), 2),
                "active_cryptos": gdata.get("active_cryptocurrencies", 0),
                "defi_volume_24h": round(gdata.get("defi_volume_24h", 0), 0),
                "defi_market_cap": round(gdata.get("defi_market_cap", 0), 0),
                "stablecoin_volume_24h": round(gdata.get("stablecoin_volume_24h", 0), 0),
                "derivatives_volume_24h": round(gdata.get("derivatives_volume_24h", 0), 0),
            }
            logger.info("  ✓ CMC métricas globales obtenidas")
        else:
            logger.warning(f"  CMC global-metrics error: {resp.status_code}")
            results["global_metrics"] = {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        logger.warning(f"  CMC global-metrics excepción: {e}")
        results["global_metrics"] = {"error": str(e)}

    # ── 2) Datos por criptomoneda (quotes) ──
    if slugs is None:
        slugs = ["bitcoin", "ethereum"]

    try:
        resp = requests.get(
            f"{base_url}/v1/cryptocurrency/quotes/latest",
            headers=headers,
            params={"slug": ",".join(slugs), "convert": convert},
            timeout=15,
        )
        if resp.status_code == 200:
            coins = resp.json().get("data", {})
            results["coins"] = {}
            for coin_id, coin in coins.items():
                q = coin.get("quote", {}).get(convert, {})
                symbol = coin.get("symbol", "?")
                results["coins"][symbol] = {
                    "nombre": coin.get("name"),
                    "precio": round(q.get("price", 0), 2),
                    "market_cap": round(q.get("market_cap", 0), 0),
                    "market_cap_dominance": round(q.get("market_cap_dominance", 0), 2),
                    "volumen_24h": round(q.get("volume_24h", 0), 0),
                    "volume_change_24h": round(q.get("volume_change_24h", 0), 2),
                    "cambio_1h_pct": round(q.get("percent_change_1h", 0), 2),
                    "cambio_24h_pct": round(q.get("percent_change_24h", 0), 2),
                    "cambio_7d_pct": round(q.get("percent_change_7d", 0), 2),
                    "cambio_30d_pct": round(q.get("percent_change_30d", 0), 2),
                    "circulating_supply": coin.get("circulating_supply"),
                    "total_supply": coin.get("total_supply"),
                    "max_supply": coin.get("max_supply"),
                    "cmc_rank": coin.get("cmc_rank"),
                    "fully_diluted_market_cap": round(q.get("fully_diluted_market_cap", 0), 0),
                }
            logger.info(
                f"  ✓ CMC datos de {len(results['coins'])} criptos obtenidos")
        else:
            logger.warning(f"  CMC quotes error: {resp.status_code}")
            results["coins"] = {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        logger.warning(f"  CMC quotes excepción: {e}")
        results["coins"] = {"error": str(e)}

    # ── 3) Fear & Greed Index (CMC versión) ──
    try:
        resp = requests.get(
            f"{base_url}/v1/global-metrics/quotes/latest",
            headers=headers,
            params={"convert": convert},
            timeout=15,
        )
        # CMC no tiene endpoint directo de F&G en basic plan,
        # pero extraemos el dato de sentimiento si está disponible
    except Exception:
        pass  # Se usa el de alternative.me como fallback

    return results


# ═══════════════════════════════════════════════════════
# NUEVOS FETCHERS — Edge informacional (Fase 1)
# ═══════════════════════════════════════════════════════

@cached("liquidations")
def fetch_liquidation_zones(symbol: str = "BTCUSDT", hours: int = 24) -> dict:
    """
    Proxy de zonas de liquidación: detecta wicks anómalos en velas 1h
    de las últimas 24h. Una mecha grande comparada con el cuerpo es
    señal típica de cascadas de liquidación (sweep de stops).

    Nota: Binance no expone liquidaciones agregadas en REST público
    (sólo WS @forceOrder o /allForceOrders firmado). Este proxy es
    una aproximación honesta basada en estructura de vela.
    Binance fapi -> si geobloquea (451), fallback a velas de OKX.
    """
    try:
        url = "https://fapi.binance.com/fapi/v1/klines"
        params = {"symbol": symbol, "interval": "1h", "limit": hours + 1}
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            rows = [[c[0], c[1], c[2], c[3], c[4], c[5]] for c in resp.json()]
            return _liquidation_from_ohlcv(rows, symbol, hours)
        logger.warning(
            f"  Liquidation Binance HTTP {resp.status_code}; usando OKX")
    except Exception as e:
        logger.warning(f"  Liquidation Binance excepción ({e}); usando OKX")
    return _fetch_liquidation_okx(symbol, hours)


@cached("funding_history")
def fetch_funding_regime(symbol: str = "BTCUSDT") -> dict:
    """
    Régimen de funding rate: histórico 7d y 90d para detectar
    posicionamiento extremo (riesgo de squeeze).
    Binance fapi -> si geobloquea (451), fallback a OKX.
    """
    try:
        url = "https://fapi.binance.com/fapi/v1/fundingRate"
        # 90 días = ~270 funding events (cada 8h)
        resp = requests.get(
            url, params={"symbol": symbol, "limit": 270}, timeout=10)
        if resp.status_code == 200:
            rates = [float(e["fundingRate"]) for e in resp.json()]
            regime = _compute_funding_regime(rates, symbol)
            if "error" not in regime:
                return regime
        else:
            logger.warning(
                f"  Funding Binance HTTP {resp.status_code}; usando OKX")
    except Exception as e:
        logger.warning(f"  Funding Binance excepción ({e}); usando OKX")
    return _fetch_funding_regime_okx(symbol)


@cached("macro_calendar")
def fetch_macro_calendar(api_key: str = "", country: str = "US") -> dict:
    """
    Calendario macro de eventos high-impact del día.
    Provider primario: financialmodelingprep.com (requiere FMP_API_KEY).
    Si no hay key, retorna placeholder y delega al web grounding del LLM.
    """
    if not api_key:
        return {
            "info": "FMP_API_KEY no configurada. "
                    "El LLM usará web grounding (Gemini) para calendar.",
            "events_today_high_impact": [],
        }

    try:
        today = datetime.now().strftime("%Y-%m-%d")
        url = "https://financialmodelingprep.com/api/v3/economic_calendar"
        resp = requests.get(
            url,
            params={"from": today, "to": today, "apikey": api_key},
            timeout=10,
        )
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}"}

        events = resp.json() or []
        high_impact = []
        for ev in events:
            impact = (ev.get("impact") or "").lower()
            ev_country = (ev.get("country") or "").upper()
            if impact in {"high"} and (country == "" or ev_country == country):
                high_impact.append({
                    "hora": ev.get("date"),
                    "evento": ev.get("event"),
                    "pais": ev_country,
                    "previo": ev.get("previous"),
                    "estimado": ev.get("estimate"),
                })

        return {
            "fecha": today,
            "events_today_high_impact": high_impact[:10],
            "total_high_impact": len(high_impact),
        }
    except Exception as e:
        logger.warning(f"  Macro calendar excepción: {e}")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════
# COLECTOR PRINCIPAL (paralelo)
# ═══════════════════════════════════════════════════════

def _compute_all_indicators_and_bias(consolidated: dict) -> dict:
    """
    Post-procesado tras los fetches: calcula indicadores técnicos
    y bias scores para crypto y tradicionales.
    """
    try:
        from modules.indicators import compute_indicators
        from modules.bias_engine import compute_bias
    except Exception as e:
        logger.warning(f"No se pudieron importar indicadores/bias: {e}")
        return consolidated

    fng = consolidated.get("fear_greed_index", {})
    funding_regime = consolidated.get("funding_regime", {})

    # ── CRYPTO ──
    indicators_crypto = {}
    bias_crypto = {}
    for symbol_raw, sym_data in consolidated.get("crypto_derivatives", {}).items():
        if not isinstance(sym_data, dict) or "error" in sym_data:
            continue
        ohlcv_1d = sym_data.get("_ohlcv_1d_full")
        ohlcv_4h = sym_data.get("_ohlcv_4h_full")
        if ohlcv_1d:
            ind_1d = compute_indicators(ohlcv_1d, label=f"{symbol_raw}_1d")
            indicators_crypto[f"{symbol_raw}_1d"] = ind_1d
            ls_ratio = sym_data.get("long_short_ratio")
            bias = compute_bias(
                ind_1d,
                funding_regime=funding_regime if symbol_raw == "BTCUSDT" else None,
                ls_ratio=ls_ratio,
                fng=fng,
                is_crypto=True,
            )
            bias_crypto[symbol_raw] = bias
        if ohlcv_4h:
            ind_4h = compute_indicators(ohlcv_4h, label=f"{symbol_raw}_4h")
            indicators_crypto[f"{symbol_raw}_4h"] = ind_4h

        # Limpiar OHLCV crudo del output final (muy verbose)
        sym_data.pop("_ohlcv_1d_full", None)
        sym_data.pop("_ohlcv_4h_full", None)

    # ── TRADICIONALES (yfinance) ──
    # Re-fetcheamos historiales diarios y computamos indicadores
    # (yfinance ya fue llamado, pero solo guardó scalars. Re-bajamos
    # historiales completos para los tradicionales clave.)
    indicators_trad = {}
    bias_trad = {}
    try:
        import yfinance as yf
        from config import YFINANCE_SYMBOLS
        for name, ticker in YFINANCE_SYMBOLS.items():
            try:
                hist = yf.Ticker(ticker).history(period="2y", interval="1d")
                if hist.empty or len(hist) < 30:
                    continue
                ind = compute_indicators(hist, label=name)
                indicators_trad[name] = ind
                bias = compute_bias(
                    ind, funding_regime=None, ls_ratio=None,
                    fng=fng, is_crypto=False,
                )
                bias_trad[name] = bias
            except Exception as e:
                logger.warning(f"Indicadores {name}: {e}")
    except Exception as e:
        logger.warning(f"Bloque tradicionales: {e}")

    consolidated["indicators"] = {
        "crypto": indicators_crypto,
        "traditional": indicators_trad,
    }
    consolidated["bias_scores"] = {
        "crypto": bias_crypto,
        "traditional": bias_trad,
    }
    return consolidated


def collect_all_data() -> dict:
    """
    Recolecta TODOS los datos en paralelo usando ThreadPoolExecutor.
    Tras la recolección, computa indicadores técnicos y bias scores.
    """
    logger.info("═══ Iniciando recolección de datos (paralelo) ═══")

    from config import (
        YFINANCE_SYMBOLS, CMC_API_KEY, CMC_SLUGS, CMC_CONVERT, CRYPTO_SYMBOLS
    )
    try:
        from config import FMP_API_KEY  # opcional
    except ImportError:
        FMP_API_KEY = ""
    try:
        from config import CRYPTOPANIC_API_KEY, FINNHUB_API_KEY  # opcional, Fase 3
    except ImportError:
        CRYPTOPANIC_API_KEY = ""
        FINNHUB_API_KEY = ""

    # Fase 3: options flow, news, COT
    from modules.options_flow import fetch_btc_options_flow
    from modules.news_sentiment import fetch_news_aggregate
    from modules.cot_report import fetch_cot_report
    # v1.4: FRED macro
    from modules.fred import fetch_fred_macro
    try:
        from config import FRED_API_KEY
    except ImportError:
        FRED_API_KEY = ""

    start_ts = datetime.now()

    tasks = {
        "traditional_markets": (fetch_yfinance_data, (YFINANCE_SYMBOLS,), {}),
        "crypto_derivatives":  (fetch_all_crypto_data, (CRYPTO_SYMBOLS,), {}),
        "fear_greed_index":    (fetch_fear_greed_index, (), {}),
        "coinmarketcap":       (fetch_cmc_data,
                                (CMC_API_KEY, CMC_SLUGS, CMC_CONVERT), {}),
        "correlation_data":    (fetch_correlation_data, (), {}),
        "liquidation_zones":   (fetch_liquidation_zones, ("BTCUSDT", 24), {}),
        "funding_regime":      (fetch_funding_regime, ("BTCUSDT",), {}),
        "macro_calendar":      (fetch_macro_calendar, (FMP_API_KEY, "US"), {}),
        "options_flow":        (fetch_btc_options_flow, (), {}),
        "news":                (fetch_news_aggregate,
                                (CRYPTOPANIC_API_KEY, FINNHUB_API_KEY), {}),
        "cot_report":          (fetch_cot_report, (), {}),
        "fred_macro":          (fetch_fred_macro, (FRED_API_KEY,), {}),
    }

    results = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        future_to_key = {
            pool.submit(fn, *args, **kwargs): key
            for key, (fn, args, kwargs) in tasks.items()
        }
        for fut in as_completed(future_to_key):
            key = future_to_key[fut]
            try:
                results[key] = fut.result()
                logger.info(f"  ✓ {key}")
            except Exception as e:
                logger.error(f"  ✗ {key}: {e}")
                results[key] = {"error": str(e)}

    elapsed = (datetime.now() - start_ts).total_seconds()
    logger.info(f"═══ Recolección completada en {elapsed:.1f}s ═══")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    consolidated = {
        "timestamp": timestamp,
        "traditional_markets": results.get("traditional_markets", {}),
        "crypto_derivatives": results.get("crypto_derivatives", {}),
        "coinmarketcap": results.get("coinmarketcap", {}),
        "fear_greed_index": results.get("fear_greed_index", {}),
        "correlation_data": results.get("correlation_data", {}),
        "liquidation_zones": results.get("liquidation_zones", {}),
        "funding_regime": results.get("funding_regime", {}),
        "macro_calendar": results.get("macro_calendar", {}),
        "options_flow": results.get("options_flow", {}),
        "news": results.get("news", {}),
        "cot_report": results.get("cot_report", {}),
        "fred_macro": results.get("fred_macro", {}),
    }

    # ── Indicadores + Bias Scores (post-procesado) ──
    logger.info("→ Computando indicadores técnicos y bias scores...")
    t0 = datetime.now()
    consolidated = _compute_all_indicators_and_bias(consolidated)
    logger.info(f"  ✓ Indicadores/bias listos ({(datetime.now()-t0).total_seconds():.1f}s)")

    # ── Macro overlays: VIX term + sector rotation ──
    consolidated = _compute_macro_overlays(consolidated)

    return consolidated


def _compute_macro_overlays(consolidated: dict) -> dict:
    """
    Tras la recolección, calcula:
      - vix_term: estructura de plazos del VIX (contango/backwardation)
      - sector_rotation: ranking de sector ETFs (risk-on/off regime)
    Ambos usan data ya recolectada por yfinance.
    """
    try:
        from modules.vix_term import compute_vix_term_structure
        from modules.sectors import compute_sector_rotation
    except Exception as e:
        logger.warning(f"Macro overlays: import error: {e}")
        return consolidated

    trad = consolidated.get("traditional_markets", {})
    try:
        consolidated["vix_term"] = compute_vix_term_structure(trad)
    except Exception as e:
        logger.warning(f"VIX term structure: {e}")
        consolidated["vix_term"] = {"error": str(e)}

    try:
        consolidated["sector_rotation"] = compute_sector_rotation(trad)
    except Exception as e:
        logger.warning(f"Sector rotation: {e}")
        consolidated["sector_rotation"] = {"error": str(e)}

    return consolidated
