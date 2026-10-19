from modules.bias_engine import compute_bias


def _ind(**over):
    base = {
        "ema_alignment": "alcista",
        "price_vs_ema200_pct": 5.0,
        "rsi14": 60.0,
        "macd_state": "alcista expandiendo",
        "z_score_30d": 0.5,
        "max_drawdown_30d_pct": -2.0,
        "adx14": 20.0,
    }
    base.update(over)
    return base


def test_pesos_siempre_suman_100():
    for ind in (_ind(), _ind(adx14=None), _ind(adx14=35), _ind(adx14=10)):
        for kwargs in (
            {"is_crypto": True, "fng": {"value": 40}},
            {"is_crypto": True},
            {"is_crypto": False},
        ):
            b = compute_bias(ind, **kwargs)
            assert sum(b["weights"].values()) == 100, (kwargs, b["weights"])


def test_tradicional_sin_fng_no_tiene_sentiment():
    b = compute_bias(_ind(), fng={"value": 10}, is_crypto=False)
    assert "sentiment" not in b["components"]
    assert "sentiment" not in b["weights"]
    assert "positioning" not in b["weights"]


def test_crypto_sin_datos_de_posicionamiento_no_diluye():
    b = compute_bias(_ind(), funding_regime={"error": "sin datos"},
                     ls_ratio="N/A", fng={"value": 50}, is_crypto=True)
    assert "positioning" not in b["components"]
    assert sum(b["weights"].values()) == 100


def test_crypto_con_posicionamiento_crowded_longs():
    b = compute_bias(_ind(), funding_regime={"percentil_90d": 95}, ls_ratio=2.6)
    assert b["components"]["positioning"] < 0
    assert sum(b["weights"].values()) == 100


def test_adx_alto_refuerza_trend_y_rango_lo_reduce():
    base = compute_bias(_ind(adx14=20), is_crypto=False)
    trend = compute_bias(_ind(adx14=35), is_crypto=False)
    rango = compute_bias(_ind(adx14=10), is_crypto=False)
    assert trend["weights"]["trend"] > base["weights"]["trend"]
    assert rango["weights"]["trend"] < base["weights"]["trend"]
    assert rango["weights"]["momentum"] > base["weights"]["momentum"]


def test_score_direccional():
    bull = compute_bias(_ind(adx14=30), fng={"value": 40}, is_crypto=False)
    bear = compute_bias(
        _ind(ema_alignment="bajista", price_vs_ema200_pct=-5.0, rsi14=35.0,
             macd_state="bajista expandiendo", adx14=30),
        fng={"value": 40}, is_crypto=False)
    assert bull["score"] > 0 > bear["score"]
