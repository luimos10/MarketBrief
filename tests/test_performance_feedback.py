import json
from datetime import datetime, timedelta, timezone

from modules.performance_feedback import build_feedback_block


def _rows(n=60):
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(hours=2)).isoformat()
    rows = []
    for i in range(30):
        rows.append({"eval_version": 2, "timestamp": ts, "asset": "SP500",
                     "decision": "LONG", "hit": False, "bias_hit": False,
                     "outcome": "stop", "r_realized": -1.01,
                     "probability_declared": 60.0})
    for i in range(10):
        rows.append({"eval_version": 2, "timestamp": ts, "asset": "SP500",
                     "decision": "LONG", "hit": True, "bias_hit": True,
                     "outcome": "tp1", "r_realized": 1.5,
                     "probability_declared": 60.0})
    for i in range(15):
        rows.append({"eval_version": 2, "timestamp": ts, "asset": "GOLD",
                     "decision": "SHORT", "hit": None, "bias_hit": None,
                     "outcome": "no_fill", "r_realized": None,
                     "probability_declared": None})
    for i in range(5):
        rows.append({"eval_version": 2, "timestamp": ts, "asset": "OIL",
                     "decision": "WAIT", "hit": None, "bias_hit": True,
                     "outcome": None, "r_realized": None})
    return rows


def test_bloque_con_reglas(tmp_path):
    path = tmp_path / "eval_results.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r in _rows():
            f.write(json.dumps(r) + "\n")

    block = build_feedback_block(str(tmp_path))
    assert "RENDIMIENTO MEDIDO" in block
    assert "Decisiones:" in block
    assert "Ejecución de niveles:" in block
    assert "REGLAS DE CALIBRACIÓN" in block
    assert "stop" in block
    assert "SOBRECONFIANZA" in block


def test_sin_historial_devuelve_vacio(tmp_path):
    assert build_feedback_block(str(tmp_path)) == ""


def test_inyecta_feedback_en_prompt():
    from modules.prompt_builder import build_full_prompt

    data = {"timestamp": "2026-01-01"}
    prompt = build_full_prompt(data, "TEMPLATE", "x.txt", feedback="FB_TEST")
    assert "FB_TEST" in prompt
    assert "FEEDBACK MEDIDO DEL SISTEMA" in prompt

    prompt_sin = build_full_prompt(data, "TEMPLATE", "x.txt")
    assert "FEEDBACK MEDIDO DEL SISTEMA" not in prompt_sin
