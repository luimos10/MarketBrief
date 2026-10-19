from datetime import datetime, timezone

from modules.evaluator import build_perf_payload, evaluate_asset_call


def _bar(close, high, low):
    return {"ts": datetime(2026, 1, 2, tzinfo=timezone.utc),
            "open": close, "high": high, "low": low, "close": close}


def _long_call():
    return {"decision": "LONG", "entry": 100.0, "stop": 95.0,
            "tp1": 110.0, "tp2": 120.0, "bias": 40, "probability": 60}


def test_long_tp1_resta_costo():
    bars = [_bar(99, 100.5, 98.5), _bar(109, 110.5, 108)]
    r = evaluate_asset_call("SP500", _long_call(), 100.0, bars, complete=True)
    assert r["hit"] is True
    assert r["bias_hit"] is True
    assert r["outcome"] == "tp1"
    assert r["cost_r"] == 0.01
    assert r["r_realized"] == 1.99


def test_empate_stop_y_tp_gana_stop():
    bars = [_bar(100, 111, 94)]
    r = evaluate_asset_call("SP500", _long_call(), 100.0, bars, complete=True)
    assert r["outcome"] == "stop"
    assert r["level_hit"] is False
    assert r["r_realized"] == -1.01


def test_sin_fill():
    bars = [_bar(101, 102, 100.5)]
    r = evaluate_asset_call("SP500", _long_call(), 100.0, bars, complete=True)
    assert r["outcome"] == "no_fill"
    assert not r.get("filled", False)
    assert r["r_realized"] is None


def test_short_tp1():
    call = {"decision": "SHORT", "entry": 100.0, "stop": 105.0,
            "tp1": 90.0, "tp2": 85.0, "bias": -40}
    bars = [_bar(99, 100.5, 89.5)]
    r = evaluate_asset_call("GOLD", call, 100.0, bars, complete=True)
    assert r["direction"] == "SHORT"
    assert r["outcome"] == "tp1"
    assert r["r_realized"] == 1.988


def test_wait_no_puntua_decision_pero_si_sesgo():
    call = {"decision": "WAIT", "bias": "alcista"}
    bars = [_bar(105, 106, 104)]
    r = evaluate_asset_call("SP500", call, 100.0, bars, complete=True)
    assert r["hit"] is None
    assert r["bias_hit"] is True
    assert "sin decisión operativa" in r["reason"]


def test_ventana_incompleta_no_puntua():
    call = _long_call()
    bars = [_bar(99, 100.5, 98.5)]
    r = evaluate_asset_call("SP500", call, 100.0, bars, complete=False)
    assert r["hit"] is None
    assert r["reason"] == "ventana incompleta"


def test_fill_sin_tocar_niveles():
    call = _long_call()
    bars = [_bar(100, 101, 99.5), _bar(102, 103, 99.8)]
    r = evaluate_asset_call("SP500", call, 100.0, bars, complete=True)
    assert r["outcome"] == "none"
    assert r["r_realized"] is None
    assert r["hit"] is True


def test_yields_en_puntos_porcentuales_y_anomalia():
    call = {"decision": "WAIT", "bias": "bajista"}
    r = evaluate_asset_call("US02Y", call, 4.05, [_bar(4.13, 4.14, 4.12)],
                            complete=True)
    assert r["unit"] == "pp"
    assert r["move_pp"] == 0.08
    assert r["bias_hit"] is False

    r2 = evaluate_asset_call("US02Y", call, 4.05, [_bar(4.75, 4.76, 4.74)],
                             complete=True)
    assert "anómalo" in r2["reason"]
    assert r2["hit"] is None


def _row(**kw):
    base = {"asset": "SP500", "hit": True, "bias_hit": True,
            "prompt_version": "v1.4", "session": "ny", "decision": "LONG",
            "outcome": "tp1", "r_realized": 1.0,
            "probability_declared": 60, "confidence_declared": 70,
            "signal_quality": "B", "f_risk_atr": 1.2}
    base.update(kw)
    return base


def test_perf_payload_buckets_y_ejecucion():
    rows = [
        _row(),
        _row(hit=False, outcome="stop", r_realized=-1.0, asset="GOLD",
             probability_declared=50, confidence_declared=55,
             signal_quality="C", f_risk_atr=2.7, decision="SHORT"),
    ]
    p = build_perf_payload(rows)["aggregated"]

    assert p["overall"]["hit"] == 1
    assert p["overall"]["miss"] == 1
    assert p["overall"]["hit_rate"] == 50.0
    assert p["by_decision"]["LONG"]["hit"] == 1
    assert p["by_decision"]["SHORT"]["miss"] == 1
    assert p["by_probability"]["60-70%"]["hit"] == 1
    assert p["by_probability"]["50-60%"]["miss"] == 1
    assert p["by_confidence"][">=70"]["hit"] == 1
    assert p["by_confidence"]["50-60"]["miss"] == 1
    assert p["by_quality"]["B"]["hit"] == 1
    assert p["by_quality"]["C"]["miss"] == 1
    assert p["by_risk_atr"]["0.8-1.5 ATR"]["hit"] == 1
    assert p["by_risk_atr"][">=2.5 ATR"]["miss"] == 1

    ex = p["by_asset"]["SP500"]["execution"]
    assert ex["tp1"] == 1
    assert ex["r_mean"] == 1.0
    assert p["execution"]["stop"] == 1
    assert p["execution"]["r_mean"] == 0.0

    cal = p["overall"]["calibration"]
    assert cal["probability"] == 55.0
    assert cal["n"] == 2
    assert cal["brier"] == 0.205
