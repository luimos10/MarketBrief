import pandas as pd
import pytest

from modules.data_fetcher import _compute_all_indicators_and_bias


def _hist(n=80, base=100.0):
    close = [base + i * 0.5 for i in range(n)]
    return pd.DataFrame({
        "Open": close,
        "High": [c + 1 for c in close],
        "Low": [c - 1 for c in close],
        "Close": close,
        "Volume": [1000] * n,
    })


@pytest.fixture()
def solo_sp500(monkeypatch):
    import config

    monkeypatch.setattr(config, "YFINANCE_SYMBOLS", {"SP500": "^GSPC"})


def test_reutiliza_historial_sin_red(solo_sp500):
    consolidated = {
        "fear_greed_index": {},
        "funding_regime": {},
        "crypto_derivatives": {},
        "traditional_markets": {
            "SP500": {"precio_actual": 139.5, "_hist_daily_full": _hist()},
        },
    }
    out = _compute_all_indicators_and_bias(consolidated)

    assert "SP500" in out["indicators"]["traditional"]
    assert out["indicators"]["traditional"]["SP500"]["last_close"] == 139.5
    assert "SP500" in out["bias_scores"]["traditional"]
    assert "_hist_daily_full" not in out["traditional_markets"]["SP500"]


def test_sin_historial_no_crashea(solo_sp500, monkeypatch):
    class _Ticker:
        def __init__(self, *_):
            pass

        def history(self, **_):
            return pd.DataFrame()

    import yfinance

    monkeypatch.setattr(yfinance, "Ticker", _Ticker)

    consolidated = {
        "fear_greed_index": {},
        "funding_regime": {},
        "crypto_derivatives": {},
        "traditional_markets": {"SP500": {"_hist_daily_full": "degradado-por-cache"}},
    }
    out = _compute_all_indicators_and_bias(consolidated)

    assert out["indicators"]["traditional"] == {}
    assert "_hist_daily_full" not in out["traditional_markets"]["SP500"]
