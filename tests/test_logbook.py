from modules.logbook import (append_call, build_feature_snapshot,
                             normalize_structured)


def test_decision_desde_setup_y_sesgo():
    legacy = {
        "fecha": "2026-10-13", "sesgo_global": "Alcista",
        "principales": {
            "BTC": {"sesgo": "Alcista", "precio_referencia": 100,
                    "soporte": 99, "invalidacion": 98, "setup": "continuacion"},
            "SP500": {"sesgo": "Alcista", "precio_referencia": 100,
                      "soporte": 99, "invalidacion": 98, "setup": "reversion"},
            "NASDAQ": {"sesgo": "Bajista", "precio_referencia": 100,
                       "soporte": 101, "invalidacion": 102, "setup": "continuacion"},
            "GOLD": {"sesgo": "Neutral", "precio_referencia": 100,
                     "soporte": 99, "invalidacion": 98, "setup": "continuacion"},
            "OIL": {"sesgo": "Alcista", "precio_referencia": 100,
                    "soporte": 99, "invalidacion": 98, "setup": "none"},
        },
    }
    rich = normalize_structured(legacy)
    assets = rich["assets"]
    assert assets["BTC"]["decision"] == "LONG"
    assert assets["SP500"]["decision"] == "SHORT"
    assert assets["NASDAQ"]["decision"] == "SHORT"
    assert assets["GOLD"]["decision"] == "WAIT"
    assert assets["OIL"]["decision"] == "WAIT"


def test_features_geometria_y_indicadores():
    market_data = {
        "indicators": {
            "crypto": {
                "BTCUSDT_1d": {"rsi14": 55.0, "atr14_pct": 2.0,
                               "ema_alignment": "alcista"},
            },
            "traditional": {},
        },
        "bias_scores": {"crypto": {"BTCUSDT": {"score": 35}}},
    }
    structured = {"assets": {
        "BTC": {"decision": "LONG", "entry": 100.0, "stop": 98.0,
                "tp1": 103.0, "tp2": 105.0, "price": 102.0,
                "probability": 60},
    }}
    features = build_feature_snapshot(market_data, structured)
    f = features["BTC"]
    assert f["rsi14"] == 55.0
    assert f["bias_score"] == 35
    assert f["risk_pct"] == 2.0
    assert f["risk_atr"] == 1.0
    assert f["tp1_rr"] == 1.5
    assert f["entry_vs_price_pct"] == -1.96


def test_append_call_guarda_features_y_sesion(tmp_path):
    import json

    path = append_call(
        str(tmp_path),
        {"assets": {}},
        market_snapshot={"prices": {}},
        metadata={"ai_label": "gemini:test", "prompt_version": "v1.4"},
        features={"BTC": {"rsi14": 50.0}},
    )
    with open(path, encoding="utf-8") as fh:
        row = json.loads(fh.readline())
    assert row["features"]["BTC"]["rsi14"] == 50.0
    assert row["call_id"]
    assert row["session"]
    assert row["schema_version"] == 2
    assert row["evaluated"] is False
