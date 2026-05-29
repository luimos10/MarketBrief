"""
╔══════════════════════════════════════════════════════╗
║         MÓDULO DE GENERACIÓN DEL BRIEF                ║
║  Llama a Gemini con Google Search Grounding           ║
╚══════════════════════════════════════════════════════╝
"""

import requests
import logging
import time

from google import genai
from google.genai import types

logger = logging.getLogger("MarketBrief")


def generate_brief(
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    model: str = "gemini-2.0-flash",
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
                # Si el SDK soporta ThinkingConfig y el modelo es 2.5+,
                # apagar el thinking interno (libera ~3K tokens útiles).
                if "2.5" in model and hasattr(types, "ThinkingConfig"):
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
# GROQ: Generación de brief usando Llama 3 (Groq API)
# ═══════════════════════════════════════════════════════


def generate_brief_groq(
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    model: str = "llama3-70b-8192",
    max_tokens: int = 8000,
    max_retries: int = 3,
) -> str:
    """
    Genera el briefing usando Groq (Llama 3 u otro modelo soportado).

    Args:
        system_prompt: Instrucciones del sistema para el modelo
        user_prompt: El prompt completo con datos + template
        api_key: API key de Groq
        model: Modelo a usar (por defecto llama3-70b-8192)
        max_tokens: Tokens máximos de respuesta
        max_retries: Reintentos ante errores transitorios

    Returns:
        El briefing generado en formato Markdown
    """
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, headers=headers,
                                     json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            transient = status_code in {429, 500, 502, 503, 504}
            if transient and attempt < max_retries:
                wait_seconds = attempt * 3
                logger.warning(
                    f"Groq devolvió un error transitorio ({e}). "
                    f"Reintentando en {wait_seconds}s "
                    f"({attempt}/{max_retries})..."
                )
                time.sleep(wait_seconds)
                continue

            logger.error(f"Error llamando a Groq: {e}")
            return f"ERROR: {e}"
