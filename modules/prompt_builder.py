"""
╔══════════════════════════════════════════════════════╗
║         MÓDULO DE CONSTRUCCIÓN DEL PROMPT             ║
║  Combina el template con los datos en tiempo real      ║
╚══════════════════════════════════════════════════════╝
"""

import json
import os
import logging
from datetime import datetime

logger = logging.getLogger("MarketBrief")


def load_prompt_template(filepath: str) -> str:
    """Carga el template del prompt desde archivo."""
    if not os.path.exists(filepath):
        logger.error(f"No se encontró el archivo de prompt: {filepath}")
        raise FileNotFoundError(f"Prompt template no encontrado: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


# Orden FIJO de los 5 activos principales del brief (v1.4)
# Las claves son las "claves internas" como aparecen en bias_scores/indicators.
PRINCIPAL_ASSETS_ORDER = [
    ("BTCUSDT",          "BTC",    "crypto"),
    ("S&P 500 (ES)",     "SP500",  "traditional"),
    ("Nasdaq 100 (NQ)",  "NASDAQ", "traditional"),
    ("Gold (XAUUSD)",    "GOLD",   "traditional"),
    ("Crude Oil (WTI)",  "OIL",    "traditional"),
]

# Activos macro_context — referencias, no análisis profundo
MACRO_CONTEXT_ORDER = [
    ("DXY",     "DXY",    "traditional"),
    ("VIX",     "VIX",    "traditional"),
    ("US10Y",   "US10Y",  "traditional"),
]

# Activos watchlist — bias dashboard, sin análisis profundo
WATCHLIST_ORDER = [
    ("ETHUSDT",         "ETH",     "crypto"),
    ("SOLUSDT",         "SOL",     "crypto"),
    ("Silver (XAGUSD)", "SILVER",  "traditional"),
    ("US02Y",           "US02Y",   "traditional"),
]


def _compute_principal_assets_summary(data: dict) -> list[dict]:
    """
    Devuelve los 5 activos principales (FIJOS) con sus bias scores
    precalculados. El LLM consume este orden para la sección "Top 5".
    """
    bias_scores = data.get("bias_scores", {})
    summary = []
    for internal_key, brief_key, category in PRINCIPAL_ASSETS_ORDER:
        b = (bias_scores.get(category) or {}).get(internal_key)
        if isinstance(b, dict) and "score" in b:
            summary.append({
                "asset_internal": internal_key,
                "asset_brief": brief_key,
                "score": int(b["score"]),
                "label": b.get("label", ""),
                "category": category,
                "components": b.get("components", {}),
            })
        else:
            summary.append({
                "asset_internal": internal_key,
                "asset_brief": brief_key,
                "score": None,
                "label": "N/A",
                "category": category,
            })
    return summary


def _append_indicator_lines(sections: list, vals: dict) -> None:
    """Helper para formatear bloque de indicadores de forma compacta."""
    keys_compact = [
        ("last_close", "precio"),
        ("ema20", "EMA20"),
        ("ema55", "EMA55"),
        ("ema200", "EMA200"),
        ("price_vs_ema200_pct", "vs EMA200 %"),
        ("ema_alignment", "alineación"),
        ("rsi14", "RSI14"),
        ("macd_state", "MACD"),
        ("atr14_pct", "ATR %"),
        ("bb_percent", "BB%"),
        ("adx14", "ADX"),
        ("trend_strength", "fuerza"),
    ]
    parts = []
    for key, label in keys_compact:
        if key in vals and vals[key] is not None:
            parts.append(f"{label}={vals[key]}")
    # Pinta en 2 líneas si es largo
    chunked = [parts[i:i+6] for i in range(0, len(parts), 6)]
    for chunk in chunked:
        sections.append("    " + "  ".join(chunk))


def format_data_context(data: dict) -> str:
    """
    Formatea los datos recolectados en un bloque de contexto
    legible para Claude.
    """
    sections = []

    # ── Header ──
    sections.append(f"📊 DATOS DE MERCADO RECOLECTADOS — {data['timestamp']}")
    sections.append("=" * 60)

    # ── Mercados Tradicionales ──
    sections.append("\n▸ MERCADOS TRADICIONALES (Yahoo Finance)")
    sections.append("-" * 40)
    for name, values in data.get("traditional_markets", {}).items():
        if "error" in values:
            sections.append(f"  {name}: ERROR — {values['error']}")
        else:
            lines = [f"  {name}:"]
            for key, val in values.items():
                lines.append(f"    • {key}: {val}")
            sections.append("\n".join(lines))

    # ── Crypto Derivados ──
    sections.append("\n▸ DERIVADOS CRYPTO (Binance Futures)")
    sections.append("-" * 40)
    for name, values in data.get("crypto_derivatives", {}).items():
        if "error" in values:
            sections.append(f"  {name}: ERROR — {values['error']}")
        else:
            lines = [f"  {name}:"]
            for key, val in values.items():
                if key in ("candles_4h_last5", "candles_1d_last5"):
                    lines.append(f"    • {key}: [últimas 5 velas incluidas]")
                    for i, candle in enumerate(val):
                        lines.append(
                            f"      Vela {i+1}: O={candle['open']} H={candle['high']} L={candle['low']} C={candle['close']} V={candle['volume']}")
                else:
                    lines.append(f"    • {key}: {val}")
            sections.append("\n".join(lines))

    # ── Fear & Greed ──
    sections.append("\n▸ FEAR & GREED INDEX")
    sections.append("-" * 40)
    fng = data.get("fear_greed_index", {})
    sections.append(
        f"  Valor: {fng.get('value', 'N/A')} — {fng.get('classification', 'N/A')}")

    # ── CoinMarketCap ──
    cmc = data.get("coinmarketcap", {})
    if cmc and "error" not in cmc:
        sections.append("\n▸ COINMARKETCAP (Métricas globales + por cripto)")
        sections.append("-" * 40)

        gm = cmc.get("global_metrics", {})
        if gm and "error" not in gm:
            sections.append("  Mercado Crypto Global:")
            for key, val in gm.items():
                sections.append(f"    • {key}: {val}")

        coins = cmc.get("coins", {})
        if coins and "error" not in coins:
            for symbol, coin_data in coins.items():
                lines = [f"  {symbol} (CoinMarketCap):"]
                for key, val in coin_data.items():
                    lines.append(f"    • {key}: {val}")
                sections.append("\n".join(lines))

    # ── Datos de Correlación ──
    sections.append("\n▸ DATOS PARA CORRELACIONES (retornos diarios 30d)")
    sections.append("-" * 40)
    for name, values in data.get("correlation_data", {}).items():
        if "error" in values:
            sections.append(f"  {name}: ERROR — {values['error']}")
        else:
            retornos = values.get("retornos_pct", [])
            sections.append(
                f"  {name}: {len(retornos)} retornos diarios disponibles")
            if retornos:
                sections.append(f"    Retornos: {retornos}")

    # ── Zonas de Liquidación (proxy) ──
    liq = data.get("liquidation_zones", {})
    if liq and "error" not in liq:
        sections.append("\n▸ LIQUIDACIONES 24h (proxy: wicks 1h ponderados por volumen)")
        sections.append("-" * 40)
        sections.append(f"  Símbolo: {liq.get('symbol', 'BTCUSDT')}  "
                        f"Método: {liq.get('proxy_metodo', '')}")
        short_zones = liq.get("top_short_squeeze_zones", [])
        long_zones = liq.get("top_long_liquidation_zones", [])
        if short_zones:
            sections.append("  Top zonas SHORT SQUEEZE (mecha superior dominante):")
            for z in short_zones:
                sections.append(
                    f"    • {z['precio']} ({z['wick_pct']}% wick) — {z['timestamp']}")
        if long_zones:
            sections.append("  Top zonas LONG LIQUIDATION (mecha inferior dominante):")
            for z in long_zones:
                sections.append(
                    f"    • {z['precio']} ({z['wick_pct']}% wick) — {z['timestamp']}")
        if not short_zones and not long_zones:
            sections.append("  Sin wicks anómalos detectados en 24h.")

    # ── Régimen de Funding ──
    fr = data.get("funding_regime", {})
    if fr and "error" not in fr:
        sections.append("\n▸ FUNDING REGIMEN (perpetuo BTCUSDT)")
        sections.append("-" * 40)
        sections.append(f"  Funding actual: {fr.get('funding_actual_pct')}%")
        sections.append(f"  Promedio 7d:    {fr.get('promedio_7d_pct')}%  "
                        f"(max {fr.get('max_7d_pct')}% / min {fr.get('min_7d_pct')}%)")
        sections.append(f"  Percentil 90d:  {fr.get('percentil_90d')}")
        sections.append(f"  Días consecutivos mismo signo: "
                        f"{fr.get('dias_consecutivos_mismo_signo')}")
        sections.append(f"  Régimen: {fr.get('regimen')}")

    # ── Indicadores Técnicos pre-calculados ──
    ind = data.get("indicators", {})
    if ind:
        sections.append("\n▸ INDICADORES TÉCNICOS PRE-CALCULADOS (verificados, NO inventar)")
        sections.append("-" * 40)
        sections.append("  ⚠ Usa estos valores como fuente primaria. Si citas un")
        sections.append("    nivel técnico, debe coincidir ±0.5% con estos datos.")

        crypto_ind = ind.get("crypto", {})
        if crypto_ind:
            sections.append("\n  -- CRYPTO --")
            for label, vals in crypto_ind.items():
                if "error" in vals:
                    sections.append(f"  {label}: ERROR — {vals['error']}")
                    continue
                sections.append(f"  {label}:")
                _append_indicator_lines(sections, vals)

        trad_ind = ind.get("traditional", {})
        if trad_ind:
            sections.append("\n  -- TRADICIONALES (1d) --")
            for label, vals in trad_ind.items():
                if "error" in vals:
                    sections.append(f"  {label}: ERROR — {vals['error']}")
                    continue
                sections.append(f"  {label}:")
                _append_indicator_lines(sections, vals)

    # ── Bias Scores ──
    bias_scores = data.get("bias_scores", {})
    if bias_scores:
        sections.append("\n▸ BIAS SCORES (motor numérico interno, -100 muy bajista / +100 muy alcista)")
        sections.append("-" * 40)
        sections.append("  Usa estos scores como ancla del sesgo. Justifícalos con los datos.")

        for category, scores in bias_scores.items():
            if not scores:
                continue
            sections.append(f"\n  -- {category.upper()} --")
            for asset, b in scores.items():
                if "error" in b:
                    continue
                comps = b.get("components", {})
                comp_str = ", ".join(f"{k}={v:+.2f}" for k, v in comps.items())
                sections.append(
                    f"  {asset}: {b['score']:+d} ({b['label']})  | {comp_str}")

    # ── TOP 5 PRINCIPALES (orden FIJO — usar para sección 5 del brief) ──
    principales = _compute_principal_assets_summary(data)
    sections.append("\n▸ TOP 5 PRINCIPALES (orden FIJO — análisis profundo en sección 5 del brief)")
    sections.append("-" * 40)
    for i, p in enumerate(principales, 1):
        score_str = f"{p['score']:+d}" if p['score'] is not None else "N/A"
        sections.append(
            f"  #{i}: {p['asset_brief']:6s}  score={score_str}  "
            f"({p['label']}, internal: {p['asset_internal']})")

    # ── MACRO CONTEXT ASSETS (referencias, no análisis profundo) ──
    bias_scores_all = data.get("bias_scores", {})
    sections.append("\n▸ MACRO CONTEXT (referencias para sección 4 del brief)")
    sections.append("-" * 40)
    for internal_key, brief_key, category in MACRO_CONTEXT_ORDER:
        b = (bias_scores_all.get(category) or {}).get(internal_key)
        if isinstance(b, dict) and "score" in b:
            sections.append(
                f"  {brief_key:6s}  score={b['score']:+d}  ({b.get('label', '')})")

    # ── WATCHLIST (solo Bias Dashboard, sin análisis profundo) ──
    sections.append("\n▸ WATCHLIST (incluir en Bias Dashboard, NO en Top 5)")
    sections.append("-" * 40)
    for internal_key, brief_key, category in WATCHLIST_ORDER:
        b = (bias_scores_all.get(category) or {}).get(internal_key)
        if isinstance(b, dict) and "score" in b:
            sections.append(
                f"  {brief_key:6s}  score={b['score']:+d}  ({b.get('label', '')})")

    # ── CONTEXTO MACRO: FRED + VIX TS + Sectores ──
    fred = data.get("fred_macro", {})
    if fred:
        sections.append("\n▸ CONTEXTO MACRO — FRED (curva yield + liquidez Fed)")
        sections.append("-" * 40)
        if "info" in fred:
            sections.append(f"  {fred['info']}")
        else:
            yc = fred.get("yield_curve", {})
            lq = fred.get("liquidity", {})
            if yc.get("spread_10y_2y") is not None:
                sections.append(
                    f"  Spread 10Y-2Y: {yc['spread_10y_2y']:+.2f}  "
                    f"(US10Y={yc.get('us10y')} / US2Y={yc.get('us2y')})")
                sections.append(
                    f"  Curva invertida: {yc.get('inverted')}  "
                    f"(días consecutivos en inversión: {yc.get('days_inverted_recent', 0)})")
            if lq.get("rrp_balance_billions") is not None:
                sections.append(
                    f"  RRP balance: ${lq['rrp_balance_billions']:.0f}B  "
                    f"(Δ 7d: {lq.get('rrp_delta_7d_billions')})")
            if lq.get("tga_balance_billions") is not None:
                sections.append(
                    f"  TGA balance: ${lq['tga_balance_billions']:.0f}B  "
                    f"(Δ 7d: {lq.get('tga_delta_7d_billions')})")
            if lq.get("fed_funds_rate") is not None:
                sections.append(f"  Fed Funds rate: {lq['fed_funds_rate']:.2f}%")
            for line in fred.get("interpretation", []):
                sections.append(f"  ⓘ {line}")

    # VIX Term Structure
    vt = data.get("vix_term", {})
    if vt and "error" not in vt:
        sections.append("\n▸ CONTEXTO MACRO — VIX TERM STRUCTURE")
        sections.append("-" * 40)
        sections.append(
            f"  VIX9D: {vt.get('vix9d')} | VIX: {vt.get('vix')} | "
            f"VIX3M: {vt.get('vix3m')} | VVIX: {vt.get('vvix')}")
        if vt.get("ratio_9d_vs_vix") is not None:
            sections.append(f"  Ratio VIX9D/VIX: {vt['ratio_9d_vs_vix']}")
        if vt.get("ratio_vix_vs_3m") is not None:
            sections.append(f"  Ratio VIX/VIX3M:  {vt['ratio_vix_vs_3m']}")
        sections.append(f"  Label: {vt.get('label', 'N/A')}")
        if vt.get("interpretation"):
            sections.append(f"  ⓘ {vt['interpretation']}")

    # Sector Rotation
    sr = data.get("sector_rotation", {})
    if sr and "error" not in sr:
        sections.append("\n▸ CONTEXTO MACRO — SECTOR ROTATION (USA)")
        sections.append("-" * 40)
        for entry in sr.get("perf_7d_ranking", []):
            sections.append(
                f"  {entry['sector']:22s}  7d: {entry['pct']:+.2f}%")
        sections.append(f"  Régimen: {sr.get('regime', 'N/A')}")
        if sr.get("interpretation"):
            sections.append(f"  ⓘ {sr['interpretation']}")

    # ── Options Flow BTC (Deribit) ──
    of = data.get("options_flow", {})
    if of and "error" not in of:
        sections.append("\n▸ OPTIONS FLOW BTC (Deribit)")
        sections.append("-" * 40)
        sections.append(f"  Spot: {of.get('spot')}")
        sections.append(f"  PC ratio global: {of.get('global_pc_ratio')}  "
                        f"(call OI {of.get('global_call_oi')}, "
                        f"put OI {of.get('global_put_oi')})")
        next_exp = of.get("next_expiry")
        if next_exp:
            sections.append(
                f"  Próximo expiry: {next_exp}  "
                f"(PC {of.get('next_expiry_pc_ratio')}, "
                f"calls {of.get('next_expiry_call_oi')}, "
                f"puts {of.get('next_expiry_put_oi')})")
        mp = of.get("max_pain")
        if mp is not None:
            sections.append(
                f"  Max pain: {mp:.0f}  ({of.get('max_pain_vs_spot_pct'):+.2f}% vs spot)")
        top = of.get("top_oi_strikes_near_spot", [])
        if top:
            sections.append("  Top 5 strikes con OI cerca del spot:")
            for s in top:
                sections.append(
                    f"    • {s['strike']:.0f} ({s['vs_spot_pct']:+.2f}%): "
                    f"total OI {s['total_oi']} (C {s['call_oi']} / P {s['put_oi']})")
        for line in of.get("interpretation", []):
            sections.append(f"  ⓘ {line}")

    # ── News & Sentiment ──
    news = data.get("news", {})
    if news:
        sections.append("\n▸ NEWS & SENTIMENT (últimas 24h)")
        sections.append("-" * 40)
        cp = news.get("crypto", {}) or {}
        if cp.get("items"):
            sections.append("  -- CRYPTO (CryptoPanic, top score) --")
            for it in cp["items"][:5]:
                sections.append(
                    f"    [{it['score']:+d}] {it['published']} "
                    f"({','.join(it.get('currencies') or [])}): "
                    f"{it['title']}")
        elif cp.get("info"):
            sections.append(f"  -- CRYPTO -- {cp['info']}")
        elif "error" in cp:
            sections.append(f"  -- CRYPTO -- ERROR: {cp['error']}")

        mc = news.get("macro", {}) or {}
        if mc.get("items"):
            sections.append("  -- MACRO/EQUITY (Finnhub, más recientes) --")
            for it in mc["items"][:5]:
                sections.append(
                    f"    {it['published']} [{it.get('source')}]: "
                    f"{it['headline']}")
        elif mc.get("info"):
            sections.append(f"  -- MACRO -- {mc['info']}")
        elif "error" in mc:
            sections.append(f"  -- MACRO -- ERROR: {mc['error']}")

    # ── COT Report ──
    cot = data.get("cot_report", {})
    if cot and "markets" in cot:
        sections.append("\n▸ COT REPORT (CFTC Legacy Futures-Only — smart money semanal)")
        sections.append("-" * 40)
        sections.append("  Convención: 'Commercials' = hedgers (suelen contrarian al precio).")
        for alias, d in cot["markets"].items():
            if "error" in d:
                sections.append(f"  {alias}: ERROR — {d['error']}")
                continue
            sections.append(
                f"  {alias} ({d.get('report_date', '')[:10]}): {d.get('summary', '')}")

    # ── Calendario Macro ──
    mc = data.get("macro_calendar", {})
    if mc and "error" not in mc:
        sections.append("\n▸ EVENTOS MACRO HOY (alto impacto)")
        sections.append("-" * 40)
        events = mc.get("events_today_high_impact", [])
        if events:
            for ev in events:
                sections.append(
                    f"  • {ev.get('hora', 'N/A')} [{ev.get('pais', '')}] "
                    f"{ev.get('evento', '')} — "
                    f"prev: {ev.get('previo', 'N/A')}  est: {ev.get('estimado', 'N/A')}")
        else:
            info = mc.get("info", "Sin eventos high-impact registrados.")
            sections.append(f"  {info}")

    return "\n".join(sections)


def build_full_prompt(data: dict, prompt_template: str) -> str:
    """
    Construye el prompt final combinando:
    1. Instrucción del sistema
    2. Datos recolectados como contexto
    3. Template del briefing
    """

    data_context = format_data_context(data)

    today = datetime.now().strftime("%A, %d de %B de %Y")

    try:
        from config import PROMPT_VERSION
    except Exception:
        PROMPT_VERSION = "unknown"

    full_prompt = f"""Fecha: {today}
Versión prompt: {PROMPT_VERSION}

## DATOS DE MERCADO (base — no inventes precios)

{data_context}

## INSTRUCCIONES DEL BRIEFING

{prompt_template}

═══════════════════════════════════════════════════════════════════════
## ESQUEMA JSON OBLIGATORIO (sección 9 del brief)
═══════════════════════════════════════════════════════════════════════

REGLA CRÍTICA: el bloque ```json``` debe aparecer SIEMPRE, ANTES del cierre
narrativo (sección 10). Si te estás quedando sin presupuesto de tokens en
cualquier sección anterior, RECORTA esa sección — NUNCA omitas este bloque.
El logbook que mide tu hit rate depende de él.

Estructura exacta (sin texto adicional dentro del bloque, sin coma final):

```json
{{
  "fecha": "YYYY-MM-DD",
  "prompt_version": "{PROMPT_VERSION}",
  "ai_model": "gemini|groq",
  "sesgo_global": "Alcista|Bajista|Neutral",
  "mercado_lider": "BTC|Nasdaq|SP500|Gold|Oil|DXY|VIX|Mixto",
  "principales": {{
    "BTC":    {{ "sesgo": "Alcista|Bajista|Neutral", "precio_referencia": 0, "soporte": 0, "resistencia": 0, "invalidacion": 0, "setup": "continuacion|reversion|sweep|none" }},
    "SP500":  {{ "sesgo": "...", "precio_referencia": 0, "soporte": 0, "resistencia": 0, "invalidacion": 0, "setup": "..." }},
    "NASDAQ": {{ "sesgo": "...", "precio_referencia": 0, "soporte": 0, "resistencia": 0, "invalidacion": 0, "setup": "..." }},
    "GOLD":   {{ "sesgo": "...", "precio_referencia": 0, "soporte": 0, "resistencia": 0, "invalidacion": 0, "setup": "..." }},
    "OIL":    {{ "sesgo": "...", "precio_referencia": 0, "soporte": 0, "resistencia": 0, "invalidacion": 0, "setup": "..." }}
  }},
  "macro_context": {{
    "DXY":    {{ "sesgo": "...", "nivel": 0 }},
    "VIX":    {{ "sesgo": "...", "nivel": 0 }},
    "US10Y":  {{ "sesgo": "...", "nivel": 0 }}
  }}
}}
```

Reglas:
- Precios coinciden con datos pre-calculados (±0.5%).
- Si un activo no tiene visibilidad clara, `"sesgo": "Neutral"` + `"setup": "none"`.
- `principales` SIEMPRE incluye las 5 claves (BTC, SP500, NASDAQ, GOLD, OIL).
- `macro_context.nivel` es el precio/nivel actual del activo (DXY nominal, VIX nominal, US10Y yield %).
- DESPUÉS del JSON ve la sección 11 (cierre, 1 frase) y termina.

═══════════════════════════════════════════════════════════════════════
## PRESUPUESTO DE TOKENS
═══════════════════════════════════════════════════════════════════════

Output objetivo total: 5500-7500 tokens (~22-30K caracteres). Distribución:

| Sección              | Tokens objetivo |
|----------------------|-----------------|
| 1. Header            | 30              |
| 2. Resumen Ejecutivo | 250             |
| 3. Bias Dashboard    | 700 (12 filas)  |
| 4. Contexto Macro    | 400 (FRED + VIX TS + Sectores + DXY + curva) |
| 5. Top 5 Principales | 2500 (5 × 500 c/u FIJOS: BTC, SP500, NASDAQ, GOLD, OIL) |
| 6. Intermarket Flash | 200             |
| 7. Catalizadores 24h | 500             |
| 8. Plan Operativo    | 700 (5-6 setups con R:R/tamaño/probabilidad) |
| 9. Riesgos / NO-trade| 350 (5-6 bullets) |
| 10. JSON estructurado| 400             |
| 11. Cierre           | 30              |

Si en cualquier punto sientes que te aproximas al límite de output,
recorta secciones 6-7 y 9 antes que TOP 5 (5) o JSON (10).

IMPORTANTE: incluye `**Versión prompt:** {PROMPT_VERSION}` en el header
justo después de `**IA de analisis:**`.

Web search: usar SOLO si la sección "EVENTOS MACRO HOY" está vacía o para
confirmar máx 1-2 titulares relevantes. NO buscar análisis técnico (ya lo
tienes pre-calculado).

Español, Markdown limpio.
"""

    return full_prompt


def get_system_prompt() -> str:
    """System prompt para el LLM — estilo desk institucional, denso."""
    return """Eres Head of Trading + Quant Analyst de un hedge fund global.
Tu output es un INFORME PRE-MARKET para un trader profesional que ya
conoce los conceptos: NO expliques qué es RSI ni qué significa risk-on.

REGLAS:
- Datos pre-calculados son fuente primaria. NO inventes EMAs ni niveles.
- Niveles citados deben coincidir ±0.5% con los datos pre-calculados.
- Pensamiento probabilístico, no certezas. Probabilidades explícitas en
  los escenarios.
- Lenguaje denso y operativo. Cada frase debe agregar edge o acción.
- Si la data es ambigua o el bias_score está entre -25 y +25, declara
  "no hay edge claro" para ese activo. No fuerces sesgo.
- Cobertura: BTC, ETH, SOL, SP500, Nasdaq, Gold, Oil, DXY, VIX.
- Respeta el presupuesto de tokens por sección del template.
- El bloque ```json``` (sección 9) es OBLIGATORIO siempre, antes del cierre.
- Si te falta un dato, omite la línea — NO escribas "[DATA NO DISPONIBLE]"
  como relleno.
- Markdown limpio, español. Emojis SOLO 🟢🔴🟡⚠ y solo cuando aportan info."""
