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
import uuid
from datetime import datetime, timezone

from modules.session import session_slug

logger = logging.getLogger("MarketBrief")

SCHEMA_VERSION = 2


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
    features: dict | None = None,
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
        features: vector compacto por activo al momento del call
            (indicadores + geometría de niveles) para análisis posterior.
    """
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "calls.jsonl")

    now = datetime.now(timezone.utc)
    entry = {
        "call_id": uuid.uuid4().hex[:16],
        "schema_version": SCHEMA_VERSION,
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "session": session_slug(),
        "ai_label": metadata.get("ai_label"),
        "prompt_version": metadata.get("prompt_version"),
        "structured": structured,
        "snapshot": market_snapshot,
        "features": features or {},
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


# ═══════════════════════════════════════════════════════
# NORMALIZACIÓN AL ESQUEMA RICO (Prompt Maestro .md)
# ═══════════════════════════════════════════════════════

# Claves JSON (alias) por tier. Tier P = principales, M = macro context,
# W = watchlist. Se usan para ordenar/etiquetar en el dashboard/eval.
_ASSET_TIER = {
    "BTC": "P", "SP500": "P", "NASDAQ": "P", "GOLD": "P", "OIL": "P",
    "DXY": "M", "VIX": "M", "US10Y": "M", "US02Y": "M",
    "ETH": "W", "SOL": "W", "SILVER": "W",
}

# Alias legacy -> nombre de ticker interno (snapshot) para evaluación.
_LEGACY_TO_INTERNAL = {
    "BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
    "SP500": "S&P 500 (ES)", "NASDAQ": "Nasdaq 100 (NQ)",
    "GOLD": "Gold (XAUUSD)", "SILVER": "Silver (XAGUSD)",
    "OIL": "Crude Oil (WTI)", "DXY": "DXY", "VIX": "VIX",
    "US10Y": "US10Y", "US02Y": "US02Y",
}


def _decision_from(setup: str | None, bias: str | None = None) -> str:
    """Deriva la decisión operativa desde el setup legacy y el sesgo.

    `continuacion` opera a favor del sesgo; `reversion`, en contra. Sin sesgo
    direccional no hay trade: devolvemos WAIT en vez de asumir LONG.
    """
    s = (setup or "").strip().lower()
    if not s or s in ("none", "wait", "no trade", "no_trade"):
        return "WAIT"

    direction = _bias_to_sign(bias)
    if direction == 0:
        return "WAIT"

    if s in ("continuacion", "continuation", "breakout", "sweep"):
        return "LONG" if direction > 0 else "SHORT"
    if s in ("reversion", "reversión", "reversal"):
        return "SHORT" if direction > 0 else "LONG"
    return "WAIT"


def _bias_to_sign(bias) -> int:
    """+1 alcista, -1 bajista, 0 neutral/desconocido (acepta rich y legacy)."""
    if bias is None or isinstance(bias, bool):
        return 0
    if isinstance(bias, (int, float)):
        return 1 if bias > 0 else (-1 if bias < 0 else 0)
    b = str(bias).strip().lower()
    if b in ("alcista", "bull", "long", "a", "buy"):
        return 1
    if b in ("bajista", "bear", "short", "b", "sell"):
        return -1
    return 0


def normalize_structured(structured: dict | None) -> dict | None:
    """
    Convierte cualquier esquema del logbook al esquema RICO canónico
    (el del Prompt Maestro .md): {report, regime, aggregate, assets, ...}.

    Acepta:
      - Ricoh (nuevo): structured["assets"]  → se devuelve enriquecido.
      - Legacy v1.4: structured["principales"] + ["macro_context"], o
        v1.3: structured["activos"].
    Retorna None si no hay nada que normalizar (structured vacío).
    """
    if not structured:
        return None

    if structured.get("assets"):
        rich = dict(structured)
        if not rich.get("report"):
            rich["report"] = {
                "date": structured.get("fecha", ""),
                "market_bias": structured.get("sesgo_global", ""),
                "market_leader": structured.get("mercado_lider", ""),
                "model": structured.get("ai_model", ""),
                "prompt_version": structured.get("prompt_version", ""),
            }
        rich["_schema"] = "rich"
        return rich

    source = (structured.get("principales") or structured.get("activos") or {})
    if not source:
        return None

    assets = {}
    for alias, d in source.items():
        if not isinstance(d, dict):
            continue
        tier = _ASSET_TIER.get(alias, "W")
        entry = {
            "tier": tier,
            "price": d.get("precio_referencia"),
            "bias": d.get("sesgo"),
            "confidence": d.get("confidence"),
            "signal_quality": d.get("signal_quality", ""),
            "decision": _decision_from(d.get("setup"), d.get("sesgo")),
            "entry": d.get("soporte") or d.get("entry"),
            "stop": d.get("invalidacion") or d.get("stop"),
            "tp1": d.get("tp1"),
            "tp2": d.get("tp2"),
            "rr": d.get("rr"),
            "probability": d.get("probability"),
            "ev_r": d.get("ev_r"),
        }
        # Preservar campos legacy por compatibilidad con el dashboard.
        for legacy_key in ("soporte", "resistencia", "invalidacion", "setup",
                           "_synthetic_source", "precio_referencia"):
            if legacy_key in d:
                entry[legacy_key] = d[legacy_key]
        assets[alias] = entry

    # Macro context legacy suelta -> pasa como assets tier M.
    for alias, d in (structured.get("macro_context") or {}).items():
        if not isinstance(d, dict) or alias in assets:
            continue
        assets[alias] = {
            "tier": _ASSET_TIER.get(alias, "M"),
            "price": d.get("nivel") or d.get("price"),
            "bias": d.get("sesgo"),
            "decision": d.get("decision", "WAIT"),
            "entry": d.get("entry"), "stop": d.get("stop"),
            "tp1": d.get("tp1"), "tp2": d.get("tp2"),
            "rr": d.get("rr"), "probability": d.get("probability"),
            "ev_r": d.get("ev_r"),
        }

    return {
        "report": {
            "date": structured.get("fecha", ""),
            "market_bias": structured.get("sesgo_global", ""),
            "market_leader": structured.get("mercado_lider", ""),
            "model": structured.get("ai_model", ""),
            "prompt_version": structured.get("prompt_version", ""),
        },
        "regime": structured.get("regime") if structured.get("regime") else None,
        "aggregate": structured.get("aggregate"),
        "assets": assets,
        "intermarket": structured.get("intermarket"),
        "catalysts": structured.get("catalysts", []),
        "risks": structured.get("risks", []),
        "best_setup": structured.get("best_setup"),
        "no_trade_conditions": structured.get("no_trade_conditions", []),
        "_schema": "rich",
        "_note": structured.get("_note", ""),
    }


def _bias_to_entry(b: dict | None, precio: float) -> dict:
    """Crea una entrada del esquema rico desde un bias engine."""
    sesgo = _label_to_sesgo(b.get("label", "")) if b else "Neutral"
    return {
        "tier": "P", "price": precio, "bias": sesgo,
        "confidence": None, "signal_quality": "", "decision": "WAIT",
        "entry": None, "stop": None, "tp1": None, "tp2": None,
        "rr": None, "probability": None, "ev_r": None,
        "_synthetic_source": "bias_engine",
    }


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

    # Todos los activos con bias calculado (principales + macro + watchlist)
    assets = {}
    asset_aliases = list(_PRINCIPAL_KEYS) + list(_MACRO_CONTEXT_KEYS)
    for internal_key, json_key in [
        ("ETHUSDT", "ETH"), ("SOLUSDT", "SOL"),
        ("Silver (XAGUSD)", "SILVER"),
    ]:
        if (bias_root.get("crypto") or bias_root.get("traditional") or {}).get(internal_key):
            asset_aliases.append((internal_key, json_key))

    for internal_key, json_key in asset_aliases:
        b = _bias_for(internal_key)
        precio = _price_for(internal_key)
        entry = _bias_to_entry(b, precio)
        entry["tier"] = _ASSET_TIER.get(json_key, entry["tier"])
        assets[json_key] = entry

    return {
        "report": {
            "date": date.today().isoformat(),
            "market_bias": sesgo_global,
            "market_leader": mercado_lider,
            "model": ai_model,
            "prompt_version": prompt_version,
        },
        "regime": None,
        "aggregate": {"main_assets_average_bias": None, "decision": ""},
        "assets": assets,
        "intermarket": None,
        "catalysts": [],
        "risks": [],
        "best_setup": None,
        "no_trade_conditions": [],
        "_schema": "rich",
        "_note": "JSON sintetizado por brief.py — el LLM no produjo el bloque estructurado",
    }


def save_json_sidecar(output_dir: str, structured: dict,
                      session: str | None = None) -> str:
    """Guarda el JSON estructurado con fecha y sesión (no se sobrescribe)."""
    from modules.session import dated_name

    os.makedirs(output_dir, exist_ok=True)
    fname = dated_name("brief", "json")
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


# ═══════════════════════════════════════════════════════
# FEATURES POR CALL (aprendizaje posterior)
# ═══════════════════════════════════════════════════════

# alias del esquema rico -> (categoría, clave interna en indicators/bias_scores)
_FEATURE_INTERNAL = {
    "BTC":    ("crypto",      "BTCUSDT"),
    "ETH":    ("crypto",      "ETHUSDT"),
    "SOL":    ("crypto",      "SOLUSDT"),
    "SP500":  ("traditional", "S&P 500 (ES)"),
    "NASDAQ": ("traditional", "Nasdaq 100 (NQ)"),
    "GOLD":   ("traditional", "Gold (XAUUSD)"),
    "SILVER": ("traditional", "Silver (XAGUSD)"),
    "OIL":    ("traditional", "Crude Oil (WTI)"),
    "DXY":    ("traditional", "DXY"),
    "VIX":    ("traditional", "VIX"),
    "US10Y":  ("traditional", "US10Y"),
    "US02Y":  ("traditional", "US02Y"),
}

_FEATURE_IND_KEYS = (
    "rsi14", "atr14_pct", "vol_realized_30d_pct", "max_drawdown_30d_pct",
    "z_score_30d", "ema_alignment", "adx14", "trend_strength",
    "macd_state", "bb_percent", "price_vs_ema200_pct",
)


def build_feature_snapshot(market_data: dict, structured: dict | None) -> dict:
    """Vector compacto por activo para aprendizaje: indicadores pre-calculados
    al momento del call + geometría de los niveles propuestos.

    Se guarda en la entrada del logbook (`features`) y el evaluador lo copia
    a las filas de resultado para analizar qué condiciones tienen edge.
    """
    ind = market_data.get("indicators") or {}
    ind_crypto = ind.get("crypto") or {}
    ind_trad = ind.get("traditional") or {}
    bias_root = market_data.get("bias_scores") or {}
    assets = (structured or {}).get("assets") or {}

    def _flt(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    features: dict[str, dict] = {}
    for alias, d in assets.items():
        if not isinstance(d, dict):
            continue
        cat, internal = _FEATURE_INTERNAL.get(alias, (None, None))
        f: dict = {}

        ind_dict = None
        if cat == "crypto":
            ind_dict = ind_crypto.get(f"{internal}_1d")
        elif cat == "traditional":
            ind_dict = ind_trad.get(internal)
        if isinstance(ind_dict, dict) and "error" not in ind_dict:
            for k in _FEATURE_IND_KEYS:
                if ind_dict.get(k) is not None:
                    f[k] = ind_dict[k]

        b = (bias_root.get(cat) or {}).get(internal) if cat else None
        if isinstance(b, dict) and "score" in b:
            f["bias_score"] = int(b["score"])

        entry = _flt(d.get("entry"))
        stop = _flt(d.get("stop"))
        tp1 = _flt(d.get("tp1"))
        price = _flt(d.get("price"))
        if entry and stop and entry != stop:
            risk_pct = abs(entry - stop) / entry * 100
            f["risk_pct"] = round(risk_pct, 2)
            atr_pct = _flt(f.get("atr14_pct"))
            if atr_pct and atr_pct > 0:
                f["risk_atr"] = round(risk_pct / atr_pct, 2)
            if tp1:
                f["tp1_rr"] = round(abs(tp1 - entry) / abs(entry - stop), 2)
        if entry and price:
            f["entry_vs_price_pct"] = round((entry - price) / price * 100, 2)
        if d.get("decision"):
            f["decision"] = d["decision"]
        if _flt(d.get("probability")) is not None:
            f["probability"] = _flt(d.get("probability"))

        if f:
            features[alias] = f

    return features
