"""
╔══════════════════════════════════════════════════════╗
║         LOGBOOK — Tracking de calls del brief         ║
║  Extrae el bloque JSON del brief y lo persiste       ║
║  en output/calls.jsonl (una línea por call).          ║
║  Permite medir hit rate, A/B testing de prompts.     ║
╚══════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime

logger = logging.getLogger("MarketBrief")


# ═══════════════════════════════════════════════════════
# EXTRACCIÓN DEL BLOQUE JSON
# ═══════════════════════════════════════════════════════

_JSON_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json_block(brief_text: str) -> dict | None:
    """
    Busca un bloque ```json ... ``` en el brief y lo parsea.
    Retorna None si no se encuentra o no parsea.
    """
    matches = _JSON_RE.findall(brief_text)
    if not matches:
        return None
    # Tomar el último bloque (la salida estructurada va al final)
    raw = matches[-1].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"[logbook] JSON block no parsea: {e}")
        return None


def strip_json_block_from_md(brief_text: str) -> str:
    """
    Quita el bloque ```json``` del Markdown final que se entrega al usuario.
    Lo dejamos solo en el logbook + en el JSON sidecar.
    """
    return _JSON_RE.sub("", brief_text).rstrip() + "\n"


# ═══════════════════════════════════════════════════════
# PERSISTENCIA
# ═══════════════════════════════════════════════════════

def append_call(
    output_dir: str,
    structured: dict,
    *,
    market_snapshot: dict,
    metadata: dict,
    synthetic: bool = False,
) -> str:
    """
    Agrega una entrada al logbook `output/calls.jsonl`.

    Args:
        structured: el dict parseado del bloque JSON del LLM (o sintético).
        market_snapshot: precios y bias_scores al momento del brief
            (sirve como ground truth para luego evaluar el call).
        metadata: ai_label, prompt_version, etc.
        synthetic: si True, el JSON fue sintetizado por brief.py porque
            el LLM no lo produjo (fallback). Se marca en el logbook para
            poder filtrar/comparar luego.
    """
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "calls.jsonl")

    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "ai_label": metadata.get("ai_label"),
        "prompt_version": metadata.get("prompt_version"),
        "structured": structured,
        "snapshot": market_snapshot,
        "synthetic": synthetic,
        "evaluated": False,  # se actualiza con evaluate_calls.py
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    return path


# ═══════════════════════════════════════════════════════
# FALLBACK SINTÉTICO
# ═══════════════════════════════════════════════════════

# Mapeo de claves internas (como aparecen en bias_scores) a las claves del
# esquema JSON estructurado del brief.
_ASSET_KEY_MAP = {
    "BTCUSDT":           "BTC",
    "ETHUSDT":           "ETH",
    "SOLUSDT":           "SOL",
    "S&P 500 (ES)":      "SP500",
    "Nasdaq 100 (NQ)":   "NASDAQ",
    "Gold (XAUUSD)":     "GOLD",
    "Silver (XAGUSD)":   "SILVER",
    "Crude Oil (WTI)":   "OIL",
    "DXY":               "DXY",
    "VIX":               "VIX",
    "US10Y":             "US10Y",
    "US02Y":             "US02Y",
}

# v1.4: separación principales vs macro_context (orden FIJO)
_PRINCIPAL_KEYS = [
    ("BTCUSDT",         "BTC"),
    ("S&P 500 (ES)",    "SP500"),
    ("Nasdaq 100 (NQ)", "NASDAQ"),
    ("Gold (XAUUSD)",   "GOLD"),
    ("Crude Oil (WTI)", "OIL"),
]
_MACRO_CONTEXT_KEYS = [
    ("DXY",   "DXY"),
    ("VIX",   "VIX"),
    ("US10Y", "US10Y"),
]


def _label_to_sesgo(label: str) -> str:
    """Convierte el label cualitativo del bias engine al campo `sesgo` JSON."""
    if not label:
        return "Neutral"
    lo = label.lower()
    if "alcista" in lo:
        return "Alcista"
    if "bajista" in lo:
        return "Bajista"
    return "Neutral"


def build_synthetic_structured(market_data: dict, prompt_version: str,
                               ai_model: str) -> dict:
    """
    Sintetiza un JSON mínimo cuando el LLM no produjo el bloque ```json```.
    Usa los bias_scores y precios snapshot que ya tenemos calculados.
    El logbook nunca queda vacío aunque el LLM falle el formato.

    Schema v1.4: { principales: {BTC,SP500,NASDAQ,GOLD,OIL},
                   macro_context: {DXY,VIX,US10Y} }
    """
    from datetime import date

    snapshot = build_market_snapshot(market_data)
    prices = snapshot.get("prices", {})
    bias_root = market_data.get("bias_scores", {})

    def _bias_for(internal_key: str):
        for cat in ("crypto", "traditional"):
            b = (bias_root.get(cat) or {}).get(internal_key)
            if b:
                return b
        return None

    def _price_for(internal_key: str) -> float:
        v = prices.get(internal_key)
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    # Sesgo global: promedio de las 5 principales (no contar watchlist/macro)
    principal_scores = []
    for internal_key, _ in _PRINCIPAL_KEYS:
        b = _bias_for(internal_key)
        if b and "score" in b:
            principal_scores.append(int(b["score"]))

    if principal_scores:
        avg = sum(principal_scores) / len(principal_scores)
        if avg >= 25:
            sesgo_global = "Alcista"
        elif avg <= -25:
            sesgo_global = "Bajista"
        else:
            sesgo_global = "Neutral"
    else:
        sesgo_global = "Neutral"

    # Líder: el principal con mayor |score|
    leader = None
    for internal_key, json_key in _PRINCIPAL_KEYS:
        b = _bias_for(internal_key)
        if b and "score" in b:
            if leader is None or abs(int(b["score"])) > abs(leader[1]):
                leader = (json_key, int(b["score"]))
    mercado_lider = leader[0] if leader else "Mixto"

    # Bloque principales (5 fijos)
    principales = {}
    for internal_key, json_key in _PRINCIPAL_KEYS:
        b = _bias_for(internal_key)
        precio = _price_for(internal_key)
        sesgo = _label_to_sesgo(b.get("label", "")) if b else "Neutral"
        principales[json_key] = {
            "sesgo": sesgo,
            "precio_referencia": precio,
            "soporte": 0,
            "resistencia": 0,
            "invalidacion": 0,
            "setup": "none",
            "_synthetic_source": "bias_engine",
        }

    # Bloque macro_context (3 referencias)
    macro_context = {}
    for internal_key, json_key in _MACRO_CONTEXT_KEYS:
        b = _bias_for(internal_key)
        precio = _price_for(internal_key)
        sesgo = _label_to_sesgo(b.get("label", "")) if b else "Neutral"
        macro_context[json_key] = {
            "sesgo": sesgo,
            "nivel": precio,
        }

    return {
        "fecha": date.today().isoformat(),
        "prompt_version": prompt_version,
        "ai_model": ai_model,
        "sesgo_global": sesgo_global,
        "mercado_lider": mercado_lider,
        "principales": principales,
        "macro_context": macro_context,
        "_note": "JSON sintetizado por brief.py — el LLM no produjo el bloque estructurado",
    }


def save_json_sidecar(output_dir: str, structured: dict) -> str:
    """Guarda el JSON estructurado como archivo timestamped paralelo al MD/HTML."""
    os.makedirs(output_dir, exist_ok=True)
    fname = f"brief_{datetime.now().strftime('%Y-%m-%d')}.json"
    path = os.path.join(output_dir, fname)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(structured, f, indent=2, default=str, ensure_ascii=False)
    return path


# ═══════════════════════════════════════════════════════
# SNAPSHOT EXTRACTOR
# ═══════════════════════════════════════════════════════

def build_market_snapshot(market_data: dict) -> dict:
    """
    Extrae un snapshot compacto de precios + bias scores actuales,
    para usar como ground truth en la evaluación posterior.
    """
    snap = {"timestamp": market_data.get("timestamp"), "prices": {}, "bias": {}}

    def _to_float(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    # Precios crypto
    for symbol_raw, sym_data in market_data.get("crypto_derivatives", {}).items():
        if isinstance(sym_data, dict) and "precio" in sym_data:
            snap["prices"][symbol_raw] = _to_float(sym_data.get("precio"))

    # Precios tradicionales
    for name, vals in market_data.get("traditional_markets", {}).items():
        if isinstance(vals, dict) and "precio_actual" in vals:
            snap["prices"][name] = _to_float(vals.get("precio_actual"))

    # Bias scores
    bs = market_data.get("bias_scores", {})
    for category, items in bs.items():
        for asset, b in items.items():
            if isinstance(b, dict) and "score" in b:
                snap["bias"][asset] = int(b["score"])

    return snap
