"""
╔══════════════════════════════════════════════════════╗
║         MÓDULO DE GENERACIÓN DEL BRIEF                ║
║  Llama a Gemini con Google Search Grounding           ║
╚══════════════════════════════════════════════════════╝
"""

import logging
import time

from google import genai
from google.genai import types

logger = logging.getLogger("MarketBrief")


def generate_brief(
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    model: str = "gemini-3.8-flash",
    max_tokens: int = 8000,
    enable_search: bool = True,
    max_retries: int = 3,
) -> str:
    """
    Genera el briefing usando Gemini con Google Search Grounding.

    Args:
        system_prompt: Instrucciones del sistema para el modelo
        user_prompt: El prompt completo con datos + template
        api_key: API key de Google (GEMINI_API_KEY)
        model: Modelo a usar
        max_tokens: Tokens máximos de respuesta
        enable_search: Si True, habilita Google Search Grounding
        max_retries: Reintentos ante errores transitorios

    Returns:
        El briefing generado en formato Markdown
    """

    client = genai.Client(api_key=api_key)

    logger.info(f"→ Llamando a {model} con Google Search Grounding...")
    logger.info(f"  Prompt size: ~{len(user_prompt)} caracteres")

    start_time = time.time()

    tools = [types.Tool(google_search=types.GoogleSearch())
             ] if enable_search else None

    for attempt in range(1, max_retries + 1):
        try:
            # Construir config. Para gemini-2.5-* desactivamos thinking
            # para que TODO el presupuesto vaya a la respuesta visible.
            generation_config_kwargs = dict(
                system_instruction=system_prompt,
                max_output_tokens=max_tokens,
                temperature=0.3,
                tools=tools,
            )
            try:
                # Si el SDK soporta ThinkingConfig, apagar el thinking interno
                # (libera ~3K tokens útiles) para cualquier modelo Flash/Lite/Flex.
                if hasattr(types, "ThinkingConfig") and any(
                    k in model for k in ("2.5", "3.0", "3.5", "flash", "lite", "flex")
                ):
                    generation_config_kwargs["thinking_config"] = (
                        types.ThinkingConfig(thinking_budget=0)
                    )
            except Exception as _e:
                logger.warning(f"  ThinkingConfig no aplicable: {_e}")

            response = client.models.generate_content(
                model=model,
                contents=user_prompt,
                config=types.GenerateContentConfig(**generation_config_kwargs),
            )

            elapsed = round(time.time() - start_time, 1)
            logger.info(f"✓ Respuesta recibida en {elapsed}s")

            usage = getattr(response, "usage_metadata", None)
            if usage:
                logger.info(
                    f"  Tokens — Input: {usage.prompt_token_count}, "
                    f"Output: {usage.candidates_token_count}, "
                    f"Total: {usage.total_token_count}"
                )

            # finish_reason: STOP (ok) | MAX_TOKENS (truncado) | SAFETY (bloqueado) etc
            try:
                cand = response.candidates[0] if response.candidates else None
                fr = getattr(cand, "finish_reason", None) if cand else None
                if fr is not None:
                    fr_str = str(fr).split(".")[-1]
                    if fr_str.upper() == "MAX_TOKENS":
                        logger.warning(
                            f"⚠ finish_reason=MAX_TOKENS — el brief se TRUNCÓ. "
                            f"Considera aumentar MAX_TOKENS o reducir el template."
                        )
                    else:
                        logger.info(f"  finish_reason: {fr_str}")
            except Exception:
                pass

            brief_text = (response.text or "").strip()

            if not brief_text:
                logger.error("La respuesta de Gemini está vacía")
                return "ERROR: La respuesta de Gemini no contenía texto."

            return brief_text

        except Exception as e:
            err_str = str(e)
            transient = (
                any(code in err_str for code in ("429", "500", "502", "503", "504"))
                or "SSL" in err_str
                or "UNEXPECTED_EOF" in err_str
                or "ConnectionError" in err_str
                or "ReadTimeout" in err_str
                or "RemoteDisconnected" in err_str
            )
            if tools is not None and "RESOURCE_EXHAUSTED" in err_str:
                logger.warning(
                    f"Gemini con Google Search falló por cuota ({e}). "
                    f"Reintentando inmediatamente sin Google Search Grounding..."
                )
                tools = None
                continue

            if transient and attempt < max_retries:
                wait_seconds = attempt * 3
                logger.warning(
                    f"Gemini devolvió un error transitorio ({e}). "
                    f"Reintentando en {wait_seconds}s "
                    f"({attempt}/{max_retries})..."
                )
                time.sleep(wait_seconds)
                continue

            logger.error(f"Error llamando a Gemini: {e}")
            return f"ERROR: {e}"


# ═══════════════════════════════════════════════════════
# FALLBACK ENTRE MODELOS GEMINI
# ═══════════════════════════════════════════════════════

# Espera entre pasadas completas de la cadena (si la cuota por minuto
# agota todos los modelos, la segunda pasada da tiempo a que se libere).
_CHAIN_PASS_WAIT_S = 60


def generate_brief_with_fallback(
    system_prompt: str,
    user_prompt: str,
    *,
    gemini_api_key: str = "",
    gemini_model: str = "gemini-3.8-flash",
    gemini_fallback_models: list[str] | None = None,
    gemini_max_tokens: int = 32768,
    enable_search: bool = True,
    max_chain_passes: int = 2,
) -> tuple[str | None, str | None, str | None]:
    """
    Genera el brief con fallback SOLO entre modelos Gemini (misma API key).

    Cadena por defecto:
    1. gemini-3.8-flash (primario)
    2. gemini-3.5-flash-lite (fallback)

    Condiciones para saltar al siguiente modelo (o a la siguiente pasada):
    - Errores transitorios (429/5xx) tras agotar los reintentos internos
    - 429 con Google Search Grounding (reintenta sin search; si vuelve a
      fallar, se pasa al siguiente modelo)
    - Respuesta vacía o "ERROR: ..." (safety block, 4xx, parseo)

    Si TODA la cadena falla en una pasada, espera ~60s y la repite
    (max_chain_passes veces) para dar tiempo a liberar cuota por minuto.

    Returns:
        (brief_text, "gemini", modelo_usado) si algún modelo responde;
        (None, None, None) si todos fallan.
    """
    fallbacks = gemini_fallback_models or []
    candidates: list[str] = []
    for m in [gemini_model, *fallbacks]:
        if m and m not in candidates:
            candidates.append(m)

    if not gemini_api_key:
        logger.error("✗ GEMINI_API_KEY no configurada — no hay cadena de IA.")
        return None, None, None

    passes = max(1, max_chain_passes)
    for p in range(1, passes + 1):
        if p > 1:
            logger.warning(
                f"⚠ Toda la cadena Gemini falló en la pasada {p - 1}. "
                f"Esperando {_CHAIN_PASS_WAIT_S}s antes de reintentar "
                f"(pasada {p}/{passes})..."
            )
            time.sleep(_CHAIN_PASS_WAIT_S)

        for model in candidates:
            logger.info(f"→ Intentando con Gemini ({model}) [pasada {p}/{passes}]...")
            text = generate_brief(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                api_key=gemini_api_key,
                model=model,
                max_tokens=gemini_max_tokens,
                enable_search=enable_search,
            )

            if text and not text.startswith("ERROR"):
                logger.info(f"✓ Brief generado con gemini ({model})")
                return text, "gemini", model

            logger.warning(
                f"✗ gemini ({model}) falló: {text}. Probando siguiente modelo..."
            )

    logger.error("✗ Todos los modelos Gemini fallaron en todas las pasadas.")
    return None, None, None
