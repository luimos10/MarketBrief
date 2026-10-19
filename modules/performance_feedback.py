"""
╔══════════════════════════════════════════════════════╗
║  MARKET BRIEF — FEEDBACK DE RENDIMIENTO AL PROMPT    ║
╚══════════════════════════════════════════════════════╝

Resume el rendimiento real medido (eval_results.jsonl) en un bloque
compacto que se inyecta al prompt del LLM, para que calibre sus
probabilidades y deje de repetir patrones perdedores.

La ventana por defecto son los últimos 14 días; si hay pocos datos en
la ventana se usa todo el historial disponible (indicándolo).
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

MAX_BLOCK_CHARS = 2200


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except (ValueError, TypeError):
        return None


def load_results(output_dir: str) -> list[dict]:
    """Lee las filas de eval (solo v2) de eval_results.jsonl."""
    path = os.path.join(output_dir, "eval_results.jsonl")
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(r, dict) and r.get("eval_version") == 2:
                    rows.append(r)
    except FileNotFoundError:
        return []
    return rows


def _rate(hit: int, miss: int) -> float | None:
    total = hit + miss
    return round(hit / total * 100, 1) if total else None


def build_feedback_rows(results: list[dict], days: int = 14) -> tuple[list[dict], str]:
    """Filtra por ventana; si hay pocos datos usa todo el historial."""
    if not results:
        return [], ""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = [r for r in results if (_parse_ts(r.get("timestamp")) or cutoff) >= cutoff]
    if len(recent) >= 50:
        return recent, f"últimos {days} días"
    return results, "historial completo (pocos datos recientes)"


def build_feedback_block(output_dir: str, days: int = 14,
                         min_asset_n: int = 10) -> str:
    """Genera el bloque de feedback. Devuelve "" si no hay historial."""
    results, window_label = build_feedback_rows(load_results(output_dir), days)
    if not results:
        return ""

    hit = miss = skip = 0
    closed = {"tp1": 0, "tp2": 0, "stop": 0}
    no_fill = none_filled = 0
    r_values = []
    probs = []
    brier_terms = []
    by_asset = defaultdict(lambda: {"hit": 0, "miss": 0})
    by_decision = defaultdict(lambda: {"hit": 0, "miss": 0})

    for r in results:
        h = r.get("hit")
        if h is True:
            hit += 1
        elif h is False:
            miss += 1
        else:
            skip += 1

        asset = r.get("asset") or "?"
        decision = r.get("decision") or "UNKNOWN"
        if h is True or h is False:
            key = "hit" if h is True else "miss"
            by_asset[asset][key] += 1
            by_decision[decision][key] += 1

        outcome = r.get("outcome")
        if outcome in closed:
            closed[outcome] += 1
        elif outcome == "no_fill":
            no_fill += 1
        elif outcome == "none":
            none_filled += 1

        rv = r.get("r_realized")
        try:
            rv = float(rv) if rv is not None else None
        except (TypeError, ValueError):
            rv = None
        if rv is not None:
            r_values.append(rv)

        p = r.get("probability_declared")
        try:
            p = float(p) if p is not None else None
        except (TypeError, ValueError):
            p = None
        if p is not None and h is not None:
            if p > 1:
                p /= 100
            if 0 <= p <= 1:
                probs.append(p)
                brier_terms.append((p - (1.0 if h else 0.0)) ** 2)

    n_eval = hit + miss
    global_rate = _rate(hit, miss)
    lines = [f"RENDIMIENTO MEDIDO — {window_label} "
             f"({n_eval} evaluables, {skip} sin decisión operativa):"]

    if global_rate is not None:
        dec_parts = []
        for dec in ("LONG", "SHORT"):
            st = by_decision.get(dec)
            if st and (st["hit"] + st["miss"]):
                dec_parts.append(
                    f"{dec} {_rate(st['hit'], st['miss'])}% (n={st['hit'] + st['miss']})")
        extra = f" | {' · '.join(dec_parts)}" if dec_parts else ""
        lines.append(f"- Decisiones: {global_rate}% de acierto "
                     f"(hit={hit}, miss={miss}){extra}")

    closed_n = sum(closed.values())
    if closed_n:
        r_mean = round(sum(r_values) / len(r_values), 2) if r_values else None
        exec_line = (f"- Ejecución de niveles: TP1 {closed['tp1']} · "
                     f"TP2 {closed['tp2']} · Stop {closed['stop']} · "
                     f"no-fill {no_fill} · cerró sin tocar {none_filled}")
        if r_mean is not None:
            exec_line += f" · R medio {r_mean}R (n={len(r_values)})"
        lines.append(exec_line)

    asset_parts = []
    for asset, st in sorted(by_asset.items(),
                            key=lambda kv: -(kv[1]["hit"] + kv[1]["miss"])):
        n = st["hit"] + st["miss"]
        if n >= min_asset_n:
            asset_parts.append(f"{asset} {_rate(st['hit'], st['miss'])}% (n={n})")
    if asset_parts:
        lines.append("- Por activo: " + " · ".join(asset_parts[:8]))

    if probs:
        avg_p = round(sum(probs) / len(probs) * 100, 1)
        brier = round(sum(brier_terms) / len(brier_terms), 3)
        gap = round(avg_p - (global_rate or 0), 1)
        cal = (f"- Calibración: probability media {avg_p}% vs acierto real "
               f"{global_rate}% (gap {gap:+.1f}pp, Brier {brier}, n={len(probs)})")
        if gap > 10:
            cal += " → SOBRECONFIANZA"
        lines.append(cal)

    # ── Reglas de calibración basadas en los datos ──
    rules = []
    if closed_n >= 20:
        stop_share = closed["stop"] / closed_n * 100
        if stop_share >= 70:
            rules.append(
                f"El {stop_share:.0f}% de los trades cerrados murió en el stop: "
                f"no propongas stops a menos de 1.5×ATR del entry y exige "
                f"confirmación de momentum antes de operar")
    attempts = no_fill + none_filled + closed_n
    if attempts >= 30:
        nofill_share = no_fill / attempts * 100
        if nofill_share >= 50:
            rules.append(
                f"El {nofill_share:.0f}% de los niveles nunca se llenó: evita "
                f"planes que dependen de retroceso exacto al entry; si el setup "
                f"es válido, entrada a mercado con stop estructural")
    if n_eval >= 50 and global_rate is not None and global_rate < 45:
        rules.append(
            f"Hit rate de decisiones {global_rate}% (<45%): reduce el número de "
            f"calls operables (solo calidad A/B) y declara probabilidades acordes "
            f"al rendimiento real, no al ideal")
    if probs and global_rate is not None and (sum(probs) / len(probs) * 100) - global_rate > 10:
        rules.append(
            "Declara `probability` calibrada: parte del hit rate real y sube como "
            "máximo +10pp hasta que la calibración converja")

    if rules:
        lines.append("REGLAS DE CALIBRACIÓN (obligatorias para este brief):")
        lines.extend(f"- {rule}." for rule in rules)

    block = "\n".join(lines)
    if len(block) > MAX_BLOCK_CHARS:
        block = block[:MAX_BLOCK_CHARS].rsplit("\n", 1)[0] + "\n(recortado)"
    return block
