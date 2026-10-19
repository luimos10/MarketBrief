from datetime import datetime, timezone

from modules.decision_engine import apply_decision_engine


def _structured():
    return {
        "report": {"date": "14 Ene 2026",
                   "model": "Institutional Quant Engine v1.4",
                   "market_bias": "", "market_leader": "",
                   "prompt_version": ""},
        "aggregate": {"main_assets_average_bias": 14.2, "decision": "SHORT"},
        "assets": {
            "BTC": {"tier": "P", "bias": 20, "decision": "WAIT", "entry": None,
                    "stop": None, "tp1": None, "tp2": None, "rr": None,
                    "probability": 55, "ev_r": None},
            "SP500": {"tier": "P", "bias": 46, "decision": "LONG",
                      "entry": 7800, "stop": 7750, "tp1": 7856, "tp2": 7900,
                      "rr": 2.12, "probability": 60, "ev_r": 9.9},
            "NASDAQ": {"tier": "P", "bias": -10, "decision": "WAIT"},
            "GOLD": {"tier": "P", "bias": -20, "decision": "short",
                     "entry": 4242, "stop": 4262, "tp1": 4200, "tp2": 4150,
                     "rr": 0.9, "probability": 55},
            "OIL": {"tier": "P", "bias": 25, "decision": "LONG", "entry": 92.2,
                    "stop": 90.8, "tp1": 93.35, "tp2": 94.5, "rr": 0.82,
                    "probability": 0.55},
            "DXY": {"tier": "M", "bias": 15, "decision": "Buy", "entry": 99.0,
                    "stop": 98.5, "tp1": 100.0, "tp2": None, "rr": 2.0},
            "VIX": {"tier": "M", "bias": -30, "decision": None},
            "SZ": {"tier": "P", "bias": "Neutral", "decision": "LONG",
                   "entry": 100, "stop": 105, "tp1": 95, "tp2": None,
                   "rr": 3.0},
        },
        "best_setup": {"asset": "SP500", "direction": "long", "entry": 7800,
                       "sl": 7750, "tp1": 7856, "tp2": 7900, "rr": 2.12,
                       "probability": 60, "ev_r": 9.9},
    }


def test_recalcula_rr_ev_y_normaliza():
    out, corrections = apply_decision_engine(
        _structured(), ai_label="gemini:test", prompt_version="v1.4",
        now=datetime(2026, 10, 19, tzinfo=timezone.utc))

    assert out["report"]["date"] == "2026-10-19"
    assert out["report"]["model"] == "gemini:test"

    sp = out["assets"]["SP500"]
    assert sp["rr"] == 1.12
    assert sp["ev_r"] == 0.27
    assert sp["levels_valid"] is True

    assert out["assets"]["GOLD"]["decision"] == "SHORT"
    assert out["assets"]["GOLD"]["rr"] == 2.1
    assert out["assets"]["OIL"]["rr"] == 0.82
    assert out["assets"]["OIL"]["ev_r"] == 0.0
    assert out["assets"]["DXY"]["decision"] == "LONG"

    sz = out["assets"]["SZ"]
    assert sz["levels_valid"] is False
    assert sz["rr"] is None and sz["ev_r"] is None

    assert out["aggregate"]["decision"] == "LONG"
    assert out["aggregate"]["main_assets_average_bias"] is not None
    assert out["best_setup"]["rr"] == 1.12
    assert len(corrections) > 0
    assert out["_decision_engine"]["corrections"] == corrections


def test_no_modifica_sin_assets():
    payload = {"report": {"date": ""}}
    out, corrections = apply_decision_engine(payload)
    assert out == payload
    assert corrections == []
