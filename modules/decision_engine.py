"""
╔══════════════════════════════════════════════════════╗
║   MARKET BRIEF — MOTOR DE DECISIÓN DETERMINISTA      ║
╚══════════════════════════════════════════════════════╝

El LLM redacta; la aritmética no se delega. Este módulo valida y
recalcula los campos cuantitativos del JSON estructurado antes de
guardarlo en el logbook/sidecar/dashboard:

  - `rr` y `ev_r` a partir de entry/stop/tp1/tp2 (no del LLM).
  - `aggregate.main_assets_average_bias` y `aggregate.decision` desde
    los sesgos/decisión de los activos tier P.
  - `report.date` (ISO) y `report.model` (modelo real usado).
  - Marca `levels_valid=False` cuando los niveles son incoherentes
    (p.ej. TP de un LONG por debajo de la entrada).

Devuelve la lista de correcciones aplicadas para trazabilidad.
"""

from __future__ import annotations

from datetime import datetime, timezone

MAIN_ASSETS = ("BTC", "SP500", "NASDAQ", "GOLD", "OIL")
RR_TOL = 0.05

_DECISION_ALIASES = {
    "LONG": "LONG", "BUY": "LONG", "COMPRA": "LONG",
    "SHORT": "SHORT", "SELL": "SHORT", "VENTA": "SHORT",
    "WAIT": "WAIT", "ESPERAR": "WAIT",
    "NO TRADE": "NO TRADE", "NO_TRADE": "NO TRADE", "NOTRADE": "NO TRADE",
}


def _f(v) -> float | None:
    """Convierte a float; None/bool/strings no numéricos → None."""
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalize_decision(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip().upper().replace("_", " ")
    return _DECISION_ALIASES.get(s)


def _bias_num(v) -> float | None:
    """Bias numérico o etiqueta cualitativa → -1/0/+1 escala."""
    n = _f(v)
    if n is not None:
        return n
    if isinstance(v, str):
        b = v.strip().lower()
        if "alcista" in b or b in ("bull", "bullish"):
            return 1.0
        if "bajista" in b or b in ("bear", "bearish"):
            return -1.0
        if "neutral" in b:
            return 0.0
    return None


def _rr(entry: float, stop: float, tp: float | None) -> float | None:
    risk = abs(entry - stop)
    if risk <= 0 or tp is None:
        return None
    return abs(tp - entry) / risk


def levels_valid(direction: str, entry: float | None, stop: float | None,
                 tp1: float | None, tp2: float | None) -> bool:
    """Coherencia geométrica de los niveles según la dirección."""
    if direction not in ("LONG", "SHORT"):
        return False
    if entry is None or stop is None or entry == stop:
        return False
    if direction == "LONG":
        if stop >= entry:
            return False
        if tp1 is not None and tp1 <= entry:
            return False
        if tp2 is not None and tp1 is not None and tp2 <= tp1:
            return False
    else:
        if stop <= entry:
            return False
        if tp1 is not None and tp1 >= entry:
            return False
        if tp2 is not None and tp1 is not None and tp2 >= tp1:
            return False
    return True


def _prob_fraction(v) -> float | None:
    p = _f(v)
    if p is None:
        return None
    if p > 1:
        p /= 100.0
    return min(max(p, 0.0), 1.0)


def _ev_r(p: float | None, rr: float | None) -> float | None:
    if p is None or rr is None:
        return None
    return round(p * rr - (1.0 - p), 2)


def _fix_levels(d: dict, direction: str, label: str,
                corrections: list[str]) -> None:
    """Recalcula rr/ev_r de un bloque con entry/stop/tp1/tp2 (o sl)."""
    entry = _f(d.get("entry"))
    stop = _f(d.get("stop") if d.get("stop") is not None else d.get("sl"))
    tp1 = _f(d.get("tp1"))
    tp2 = _f(d.get("tp2"))
    if direction not in ("LONG", "SHORT"):
        return

    if not levels_valid(direction, entry, stop, tp1, tp2):
        if d.get("levels_valid") is not False:
            corrections.append(
                f"{label}: niveles inválidos para {direction} "
                f"(entry={entry}, stop={stop}, tp1={tp1}) — rr/ev_r anulados")
        d["levels_valid"] = False
        d["rr"] = None
        d["ev_r"] = None
        return

    d["levels_valid"] = True
    rr1 = _rr(entry, stop, tp1)
    rr2 = _rr(entry, stop, tp2)
    rr_eff = rr1 if rr1 is not None else rr2
    declared = _f(d.get("rr"))
    d["rr_tp1"] = round(rr1, 2) if rr1 is not None else None
    d["rr_tp2"] = round(rr2, 2) if rr2 is not None else None
    if rr_eff is not None:
        if declared is None or abs(declared - rr_eff) > RR_TOL:
            corrections.append(
                f"{label}: rr {declared} → {round(rr_eff, 2)} "
                f"(calculado de entry/stop/tp)")
        d["rr"] = round(rr_eff, 2)
    p = _prob_fraction(d.get("probability"))
    d["ev_r"] = _ev_r(p, d["rr"])


def _aggregate(assets: dict, corrections: list[str]) -> dict:
    biases = []
    longs = shorts = 0
    for name, d in assets.items():
        if not isinstance(d, dict):
            continue
        tier = d.get("tier") or ("P" if name in MAIN_ASSETS else "W")
        if tier != "P":
            continue
        b = _bias_num(d.get("bias"))
        if b is not None:
            biases.append(b)
        dec = normalize_decision(d.get("decision"))
        if dec == "LONG":
            longs += 1
        elif dec == "SHORT":
            shorts += 1

    avg = round(sum(biases) / len(biases), 1) if biases else None
    if longs >= 2 and longs > shorts:
        decision = "LONG"
    elif shorts >= 2 and shorts > longs:
        decision = "SHORT"
    else:
        decision = "WAIT"
    return {"main_assets_average_bias": avg, "decision": decision,
            "_source": "decision_engine"}


def apply_decision_engine(structured: dict | None, ai_label: str | None = None,
                          prompt_version: str | None = None,
                          now: datetime | None = None) -> tuple[dict | None, list[str]]:
    """Aplica el motor determinista in-place. Devuelve (structured, correcciones)."""
    if not structured or not isinstance(structured, dict):
        return structured, []
    assets = structured.get("assets")
    if not isinstance(assets, dict):
        return structured, []

    corrections: list[str] = []

    # ── report: fecha ISO y modelo real ──
    report = structured.get("report")
    if not isinstance(report, dict):
        report = {}
        structured["report"] = report
    report_date = str(report.get("date") or "")
    try:
        datetime.strptime(report_date[:10], "%Y-%m-%d")
        iso_ok = True
    except ValueError:
        iso_ok = False
    if not iso_ok:
        today = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        corrections.append(f"report.date '{report_date}' → '{today}' (ISO)")
        report["date"] = today
    if ai_label and report.get("model") != ai_label:
        corrections.append(
            f"report.model '{report.get('model')}' → '{ai_label}' (modelo real)")
        report["model"] = ai_label
    if prompt_version:
        report["prompt_version"] = prompt_version

    # ── assets: decisión canónica + rr/ev_r deterministas ──
    for name, d in assets.items():
        if not isinstance(d, dict):
            continue
        raw_dec = d.get("decision")
        dec = normalize_decision(raw_dec)
        if raw_dec is not None and dec != str(raw_dec).strip().upper():
            corrections.append(f"{name}: decision '{raw_dec}' → '{dec}'")
        if dec is not None:
            d["decision"] = dec
        _fix_levels(d, dec or "", name, corrections)

    # ── aggregate determinista ──
    new_agg = _aggregate(assets, corrections)
    old_agg = structured.get("aggregate") if isinstance(
        structured.get("aggregate"), dict) else {}
    old_avg = _f(old_agg.get("main_assets_average_bias"))
    old_dec = normalize_decision(old_agg.get("decision"))
    if (old_avg != new_agg["main_assets_average_bias"]
            or old_dec != new_agg["decision"]):
        corrections.append(
            f"aggregate: bias {old_avg}→{new_agg['main_assets_average_bias']}, "
            f"decision {old_dec}→{new_agg['decision']}")
    merged = dict(old_agg)
    merged.update(new_agg)
    structured["aggregate"] = merged

    # ── best_setup: mismos checks ──
    best = structured.get("best_setup")
    if isinstance(best, dict):
        raw_dec = best.get("direction")
        dec = normalize_decision(raw_dec)
        if dec is not None:
            best["direction"] = dec
        _fix_levels(best, dec or "", "best_setup", corrections)

    structured["_decision_engine"] = {
        "corrections": corrections,
        "applied_at": (now or datetime.now(timezone.utc)).isoformat(),
    }
    return structured, corrections
