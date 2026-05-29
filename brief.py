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
        "--model", choices=["gemini", "groq"], help="Modelo de AI a usar")
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
    full_prompt = build_full_prompt(market_data, prompt_template)
    system_prompt = get_system_prompt()

    logger.info(f"Prompt construido ({len(full_prompt)} caracteres)")

    # ── Elegir modelo de AI ──
    ai_model = os.getenv("AI_MODEL", "gemini").lower()
    if hasattr(config, "AI_MODEL"):
        ai_model = config.AI_MODEL.lower()
    if hasattr(args, "model") and getattr(args, "model", None):
        ai_model = args.model.lower()

    if ai_model not in {"gemini", "groq"}:
        logger.error(
            f"✗ AI_MODEL inválido: {ai_model}. Usa 'gemini' o 'groq'.")
        sys.exit(1)

    # ── PASO 3: Generar brief con el modelo elegido ──
    logger.info("")
    if ai_model == "groq":
        groq_max_tokens = min(config.MAX_TOKENS, config.GROQ_MAX_TOKENS)
        if groq_max_tokens < config.MAX_TOKENS:
            logger.info(
                f"Ajustando max_tokens para Groq: {config.MAX_TOKENS} -> {groq_max_tokens}"
            )
        logger.info(
            f"━━━ PASO 3/4: Generando brief con {config.GROQ_MODEL} (Groq) ━━━")
        from modules.brief_generator import generate_brief_groq
        if not config.GROQ_API_KEY:
            logger.error(
                "✗ GROQ_API_KEY no configurada. Revisa tu archivo .env")
            sys.exit(1)
        brief_text = generate_brief_groq(
            system_prompt=system_prompt,
            user_prompt=full_prompt,
            api_key=config.GROQ_API_KEY,
            model=config.GROQ_MODEL,
            max_tokens=groq_max_tokens,
        )
    else:
        logger.info(
            f"━━━ PASO 3/4: Generando brief con {config.GEMINI_MODEL} (Gemini) ━━━")
        from modules.brief_generator import generate_brief
        if not config.GEMINI_API_KEY:
            logger.error(
                "✗ GEMINI_API_KEY no configurada. Revisa tu archivo .env")
            sys.exit(1)
        brief_text = generate_brief(
            system_prompt=system_prompt,
            user_prompt=full_prompt,
            api_key=config.GEMINI_API_KEY,
            model=config.GEMINI_MODEL,
            max_tokens=config.MAX_TOKENS,
            enable_search=not args.no_search,
        )

    if brief_text.startswith("ERROR"):
        logger.error(f"✗ {brief_text}")
        sys.exit(1)

    logger.info(f"Brief generado ({len(brief_text)} caracteres)")

    # ── PASO 3.5: Extraer JSON estructurado + logbook (con fallback sintético) ──
    from modules.logbook import (
        extract_json_block, strip_json_block_from_md,
        append_call, save_json_sidecar, build_market_snapshot,
        build_synthetic_structured,
    )

    ai_label = (f"{ai_model}:{config.GROQ_MODEL}" if ai_model == "groq"
                else f"{ai_model}:{config.GEMINI_MODEL}")
    structured = extract_json_block(brief_text)
    is_synthetic = False

    if structured:
        logger.info("✓ Bloque JSON estructurado extraído del brief")
        # Limpiar el JSON crudo del MD/HTML entregado al usuario
        brief_text = strip_json_block_from_md(brief_text)
    else:
        logger.warning(
            "⚠ No se encontró bloque JSON en el brief — generando fallback "
            "sintético desde bias_scores para mantener el logbook poblado")
        structured = build_synthetic_structured(
            market_data,
            prompt_version=getattr(config, "PROMPT_VERSION", "unknown"),
            ai_model=ai_model,
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

    delivery_config = {
        "output_dir": config.OUTPUT_DIR,
        "telegram_token": config.TELEGRAM_BOT_TOKEN,
        "telegram_chat_id": config.TELEGRAM_CHAT_ID,
        "open_browser": not args.no_open,
        "ai_provider": ai_model,
        "ai_model": config.GROQ_MODEL if ai_model == "groq" else config.GEMINI_MODEL,
        "prompt_version": getattr(config, "PROMPT_VERSION", ""),
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
