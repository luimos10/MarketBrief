"""
Probe de fuentes de derivados desde el runner de GitHub Actions.
Objetivo: ver cuales NO estan geobloqueadas para recuperar funding/OI/L-S.
No modifica nada del brief; solo imprime el estado de cada endpoint.
"""
import requests

results = {}


def probe(key: str, name: str, url: str, **params):
    try:
        r = requests.get(url, params=params or None, timeout=15)
        ok = r.status_code == 200
        results[key] = (r.status_code, ok)
        mark = "OK " if ok else "!! "
        print(f"[{mark}] {name}: HTTP {r.status_code}")
        print(f"        {r.text[:280].replace(chr(10), ' ')}")
    except Exception as e:
        results[key] = (None, False)
        print(f"[!!!] {name}: EXCEPTION {e}")
    print()


print("=" * 72)
print("BASELINE — Binance fapi (esperado: 451 = bloqueado en el runner)")
print("=" * 72)
probe("binance_funding", "Binance fapi funding (premiumIndex)",
      "https://fapi.binance.com/fapi/v1/premiumIndex", symbol="BTCUSDT")
probe("binance_oi", "Binance fapi openInterest",
      "https://fapi.binance.com/fapi/v1/openInterest", symbol="BTCUSDT")

print("=" * 72)
print("OPCION A1 — Bybit v5 (funding + OI + L/S, sin key)")
print("=" * 72)
probe("bybit_tickers", "Bybit tickers (precio+funding+OI)",
      "https://api.bybit.com/v5/market/tickers", category="linear", symbol="BTCUSDT")
probe("bybit_funding_hist", "Bybit funding history",
      "https://api.bybit.com/v5/market/funding/history",
      category="linear", symbol="BTCUSDT", limit=5)
probe("bybit_ls", "Bybit long/short account ratio",
      "https://api.bybit.com/v5/market/account-ratio",
      category="linear", symbol="BTCUSDT", period="1h", limit=5)

print("=" * 72)
print("OPCION A2 — OKX (funding + OI + L/S, sin key)")
print("=" * 72)
probe("okx_funding", "OKX funding-rate",
      "https://www.okx.com/api/v5/public/funding-rate", instId="BTC-USDT-SWAP")
probe("okx_oi", "OKX open-interest",
      "https://www.okx.com/api/v5/public/open-interest",
      instType="SWAP", instId="BTC-USDT-SWAP")
probe("okx_ls", "OKX long/short ratio",
      "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio",
      ccy="BTC", period="1H")

print("=" * 72)
print("OPCION B1 — CoinGecko /derivatives (agregado multi-exchange, sin key)")
print("=" * 72)
probe("coingecko_deriv", "CoinGecko derivatives",
      "https://api.coingecko.com/api/v3/derivatives")

print("=" * 72)
print("OPCION B2 — Coinalyze (401 sin key = ALCANZABLE, no bloqueado)")
print("=" * 72)
probe("coinalyze", "Coinalyze /exchanges (sin key)",
      "https://api.coinalyze.net/v1/exchanges")

print("=" * 72)
print("VEREDICTO")
print("=" * 72)


def verdict(label, key, reachable_codes=(200,)):
    code, ok = results.get(key, (None, False))
    status = "ALCANZABLE" if (code in reachable_codes) else f"NO (HTTP {code})"
    print(f"  {label:<34} {status}")


verdict("Binance fapi (baseline)", "binance_funding")
verdict("Bybit (funding+OI)", "bybit_tickers")
verdict("Bybit L/S ratio", "bybit_ls")
verdict("OKX (funding)", "okx_funding")
verdict("OKX (OI)", "okx_oi")
verdict("OKX L/S ratio", "okx_ls")
verdict("CoinGecko derivatives", "coingecko_deriv")
# Para Coinalyze, 401 tambien cuenta como alcanzable (falta key, no bloqueo)
verdict("Coinalyze (reachability)", "coinalyze", reachable_codes=(200, 401, 403))
