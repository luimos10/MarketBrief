"""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║              📊  MARKET BRIEF ENGINE  v1.0                   ║
║                                                              ║
║   Briefing pre-market institucional automatizado             ║
║   BTC · Gold · Silver · S&P 500 · NASDAQ · Oil              ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝

Uso:
    python brief.py              → Genera brief con método de entrega por defecto
    python brief.py --html       → Genera y abre en navegador
    python brief.py --html --no-open
                                 → Genera HTML sin abrir navegador
    python brief.py --telegram   → Genera y envía por Telegram
    python brief.py --both       → Ambos
"""

import sys
import os
import logging
import argparse
from datetime import datetime

# ═══════════════════════════════════════════════════════
# SETUP DE LOGGING
# ═══════════════════════════════════════════════════════


def setup_logging():
    """Configura logging a consola y archivo."""
    from config import LOGS_DIR
    os.makedirs(LOGS_DIR, exist_ok=True)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    log_file = os.path.join(
        LOGS_DIR,
        f"brief_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.log"
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s │ %(levelname)-7s │ %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    return logging.getLogger("MarketBrief")


# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════


def _load_perf(output_dir: str) -> dict | None:
    """Carga output/perf.json (generado por evaluate_calls.py) para el dashboard."""
    import json
    path = os.path.join(output_dir, "perf.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def main():
    # Parsear argumentos
    parser = argparse.ArgumentParser(description="📊 Market Brief Engine")
    parser.add_argument("--html", action="store_true",
                        help="Abrir brief en navegador")
    parser.add_argument("--telegram", action="store_true",
                        help="Enviar por Telegram")
    parser.add_argument("--both", action="store_true", help="HTML + Telegram")
    parser.add_argument("--no-search", action="store_true",
                        help="Desactivar web search de Gemini")
    parser.add_argument("--no-open", action="store_true",
                        help="No abrir el HTML en el navegador")
    parser.add_argument(
        "--model", choices=["gemini"], help="Modelo de IA a usar (solo gemini)")
    args = parser.parse_args()

    # Setup
    logger = setup_logging()

    logger.info("╔══════════════════════════════════════════════╗")
    logger.info("║        📊  MARKET BRIEF ENGINE  v1.0         ║")
    logger.info("╚══════════════════════════════════════════════╝")
    logger.info("")

    # Importar config
    import config

    # Determinar método de entrega
    if args.both:
        delivery_method = "both"
    elif args.telegram:
        delivery_method = "telegram"
    elif args.html:
        delivery_method = "html"
    else:
        delivery_method = config.DELIVERY_METHOD

    logger.info(f"Método de entrega: {delivery_method.upper()}")
    logger.info("")

    # ── PASO 1: Recolectar datos ──
    logger.info("━━━ PASO 1/4: Recolectando datos de mercado ━━━")
    from modules.data_fetcher import collect_all_data

    try:
        market_data = collect_all_data()
    except Exception as e:
        logger.error(f"✗ Error recolectando datos: {e}")
        logger.info("Continuando con datos parciales...")
        market_data = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "traditional_markets": {},
            "crypto_derivatives": {},
            "fear_greed_index": {"value": "N/A"},
            "correlation_data": {},
        }

    # ── PASO 2: Construir prompt (con A/B rotation opcional) ──
    logger.info("")
    logger.info("━━━ PASO 2/4: Construyendo prompt ━━━")
    from modules.prompt_builder import load_prompt_template, build_full_prompt, get_system_prompt

    ab_enabled = os.getenv("PROMPT_AB_ENABLED", "false").lower() in (
        "1", "true", "yes", "on")
    chosen_template_path = config.PROMPT_FILE
    chosen_version = getattr(config, "PROMPT_VERSION", "v1.2")

    if ab_enabled:
        v2_path = os.path.join(
            os.path.dirname(config.PROMPT_FILE), "prompt_template_v2.txt")
        if os.path.exists(v2_path):
            import random
            if random.random() < 0.5:
                chosen_template_path = v2_path
                chosen_version = "v2.0"
                logger.info("[A/B] template v2 seleccionado aleatoriamente")
            else:
                logger.info("[A/B] template v1 seleccionado aleatoriamente")
            # Pisar PROMPT_VERSION temporalmente para que el logbook lo registre
            os.environ["PROMPT_VERSION"] = chosen_version
            config.PROMPT_VERSION = chosen_version
        else:
            logger.warning(
                "PROMPT_AB_ENABLED=true pero no existe prompt_template_v2.txt — "
                "usando template default")

    logger.info(f"Template: {os.path.basename(chosen_template_path)} "
                f"(versión {chosen_version})")
    prompt_template = load_prompt_template(chosen_template_path)
    full_prompt = build_full_prompt(market_data, prompt_template, chosen_template_path)
    system_prompt = get_system_prompt()

    logger.info(f"Prompt construido ({len(full_prompt)} caracteres)")

    # ── Validar modelo de IA (solo Gemini) ──
    ai_model = os.getenv("AI_MODEL", "gemini").lower()
    if ai_model != "gemini":
        logger.error(
            f"✗ AI_MODEL inválido: {ai_model}. Solo se soporta 'gemini'.")
        sys.exit(1)

    # ── PASO 3: Generar brief (cadena de fallback solo-Gemini) ──
    logger.info("")
    primary_label = config.GEMINI_MODEL
    fallbacks_label = ", ".join(config.GEMINI_FALLBACK_MODELS) or "ninguno"
    logger.info(
        f"━━━ PASO 3/4: Generando brief (primario: gemini / "
        f"{primary_label}, fallback: {fallbacks_label}) ━━━")

    if not config.GEMINI_API_KEY:
        logger.error(
            "✗ GEMINI_API_KEY no configurada. Revisa tu .env o los secrets.")
        sys.exit(1)

    from modules.brief_generator import generate_brief_with_fallback
    brief_text, used_provider, used_model = generate_brief_with_fallback(
        system_prompt=system_prompt,
        user_prompt=full_prompt,
        gemini_api_key=config.GEMINI_API_KEY or "",
        gemini_model=config.GEMINI_MODEL,
        gemini_fallback_models=config.GEMINI_FALLBACK_MODELS,
        gemini_max_tokens=config.MAX_TOKENS,
        enable_search=not args.no_search,
    )

    if not brief_text:
        logger.error("✗ Ningún proveedor de IA pudo generar el brief.")
        sys.exit(1)

    logger.info(
        f"Brief generado ({len(brief_text)} caracteres) "
        f"con {used_provider} ({used_model})")

    # ── Reparación post-generación: JSON truncado + Sección 11 (CIERRE) ──
    # Si el LLM se quedó sin tokens (MAX_TOKENS) puede dejar el bloque
    # ```json``` sin cerrar y no escribir el cierre (sección 11).
    repaired_md = _repair_md_json_and_cierre(brief_text)
    if repaired_md != brief_text:
        logger.warning(
            "⚠ Brief truncado/JSON sin cerrar — reparado post-generación "
            "(bloque ```json``` cerrado y Sección 11 CIERRE asegurada)")
        brief_text = repaired_md

    # ── PASO 3.5: Extraer JSON estructurado + logbook (con fallback sintético) ──
    from modules.logbook import (
        extract_json_block, strip_json_block_from_md, normalize_structured,
        append_call, save_json_sidecar, build_market_snapshot,
        build_synthetic_structured,
    )

    ai_label = f"{used_provider}:{used_model}"
    structured = extract_json_block(brief_text)
    is_synthetic = False

    if structured:
        logger.info("✓ Bloque JSON estructurado extraído del brief")
        # Unificar al esquema rico (Prompt Maestro .md) para logbook/dashboard
        structured = normalize_structured(structured)
        # Limpiar el JSON crudo del MD/HTML entregado al usuario
        brief_text = strip_json_block_from_md(brief_text)
    else:
        logger.warning(
            "⚠ No se encontró bloque JSON en el brief — generando fallback "
            "sintético desde bias_scores para mantener el logbook poblado")
        # No entregar JSON crudo/partials al usuario (sección 10 va al
        # logbook/sidecar, no al informe)
        brief_text = strip_json_block_from_md(brief_text)
        structured = build_synthetic_structured(
            market_data,
            prompt_version=getattr(config, "PROMPT_VERSION", "unknown"),
            ai_model=used_provider,
        )
        is_synthetic = True

    try:
        sidecar = save_json_sidecar(config.OUTPUT_DIR, structured)
        logger.info(f"  JSON sidecar: {sidecar}")
        snapshot = build_market_snapshot(market_data)
        calls_path = append_call(
            config.OUTPUT_DIR,
            structured,
            market_snapshot=snapshot,
            metadata={
                "ai_label": ai_label,
                "prompt_version": getattr(config, "PROMPT_VERSION", ""),
            },
            synthetic=is_synthetic,
        )
        flag = " (synthetic)" if is_synthetic else ""
        logger.info(f"  Logbook{flag}: {calls_path}")
    except Exception as e:
        logger.warning(f"  ✗ Logbook falló: {e}")

    # ── PASO 4: Entregar ──
    logger.info("")
    logger.info("━━━ PASO 4/4: Entregando brief ━━━")
    from modules.delivery import deliver_brief

    is_fallback = used_model != primary_label
    if is_fallback:
        logger.warning(
            f"⚠ Modo de contingencia activo: brief generado con gemini "
            f"({used_model}) en lugar del primario ({primary_label})"
        )

    delivery_config = {
        "output_dir": config.OUTPUT_DIR,
        "telegram_token": config.TELEGRAM_BOT_TOKEN,
        "telegram_chat_id": config.TELEGRAM_CHAT_ID,
        "open_browser": not args.no_open,
        "ai_provider": used_provider,
        "ai_model": used_model,
        "is_fallback": is_fallback,
        "primary_label": f"{ai_model}:{primary_label}",
        "prompt_version": getattr(config, "PROMPT_VERSION", ""),
        "structured": structured,
        "perf": _load_perf(config.OUTPUT_DIR),
    }

    results = deliver_brief(brief_text, delivery_method, delivery_config)

    # ── Resumen final ──
    logger.info("")
    logger.info("╔══════════════════════════════════════════════╗")
    logger.info("║              ✓  BRIEF COMPLETADO             ║")
    logger.info("╚══════════════════════════════════════════════╝")
    for key, value in results.items():
        logger.info(f"  {key}: {value}")
    logger.info("")

    return 0


def _salvage_truncated_json(raw: str) -> str | None:
    """Intenta rescatar un JSON truncado (MAX_TOKENS) cerrando strings/brackets.

    Estrategia:
    1. Cerrar string abierto + eliminar coma/2-puntos colgantes + cerrar
       brackets pendientes según la pila de anidamiento.
    2. Si falla, probar recortes en los últimos `}`/`]` del texto.
    Retorna el JSON reparado (str) o None si no es posible.
    """
    import json

    s = raw.strip()
    if not s:
        return None

    # Estrategia 1: cerrar estructuras abiertas
    stack = []
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()

    candidate = s
    if in_str:
        candidate += '"'
    candidate = candidate.rstrip()
    while candidate and candidate[-1] in ",:":
        candidate = candidate[:-1]
    for ch in reversed(stack):
        candidate += "}" if ch == "{" else "]"
    try:
        json.loads(candidate)
        return candidate
    except Exception:
        pass

    # Estrategia 2: recortar en los últimos cierres de objeto/arreglo
    positions = [i for i, ch in enumerate(s) if ch in "}]"]
    for pos in reversed(positions[-80:]):
        frag = s[: pos + 1]
        if not frag.strip():
            continue
        try:
            json.loads(frag)
            return frag
        except Exception:
            continue
    return None


def _repair_md_json_and_cierre(md_text, sidecar_structured=None):
    """Repara bloque JSON sin cerrar y asegura la Sección 11 (CIERRE) en el MD.

    Protege contra truncamiento (finish_reason=MAX_TOKENS): cierra el bloque
    ```json``` con el JSON válido recuperable y añade la sección de cierre
    del Prompt Maestro si el modelo no la escribió.
    """
    import json
    import re

    if not md_text:
        return md_text

    if re.search(r"```json\s*(.*?)\s*```", md_text, re.DOTALL | re.IGNORECASE):
        text = md_text
    else:
        s = md_text.find("```json")
        if s >= 0:
            c = md_text[s + 7:]
            best = None
            try:
                dec = json.JSONDecoder()
                obj, idx = dec.raw_decode(c.strip())
                best = c[:idx]
            except Exception:
                best = _salvage_truncated_json(c)
            if best is not None:
                text = md_text[:s] + "```json\n" + best.strip() + "\n```"
            else:
                text = md_text[:s] + "```json\n{}\n```"
        else:
            text = md_text

    tl = text.lower()
    if "capital busca liquidez" not in tl and "## 11" not in tl:
        text = (
            text.rstrip()
            + '\n\n## 11. CIERRE\n\n"Capital busca liquidez en estructura antes '
            'de confirmación de breakout."\n'
        )
    return text


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠ Cancelado por el usuario")
        sys.exit(130)
    except Exception as e:
        print(f"\n✗ Error fatal: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
