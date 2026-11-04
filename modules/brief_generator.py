"""
╔══════════════════════════════════════════════════════╗
║         MÓDULO DE GENERACIÓN DEL BRIEF                ║
║  Llama a Gemini con Google Search Grounding           ║
║  y fallback multi-proveedor externo con Groq          ║
╚══════════════════════════════════════════════════════╝
"""

import logging
import time
import httpx

from google import genai
from google.genai import types

logger = logging.getLogger("MarketBrief")


def generate_brief(
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    model: str = "gemini-2.5-flash",
    max_tokens: int = 8000,
    enable_search: bool = True,
    max_retries: int = 3,
    on_search_exhausted: object = None,
) -> str:
    """
    Genera el briefing usando Gemini con Google Search Grounding opcional.

    Args:
        system_prompt: Instrucciones del sistema para el modelo
        user_prompt: El prompt completo con datos + template
        api_key: API key de Google (GEMINI_API_KEY)
        model: Modelo a usar
        max_tokens: Tokens máximos de respuesta
        enable_search: Si True, habilita Google Search Grounding
        max_retries: Reintentos ante errores transitorios
        on_search_exhausted: Callback invocado si la cuota de búsqueda se agota

    Returns:
        El briefing generado en formato Markdown
    """

    client = genai.Client(api_key=api_key)

    search_desc = "con Google Search Grounding" if enable_search else "sin búsqueda web"
    logger.info(f"→ Llamando a {model} ({search_desc})...")
    logger.info(f"  Prompt size: ~{len(user_prompt)} caracteres")

    start_time = time.time()

    tools = [types.Tool(google_search=types.GoogleSearch())
             ] if enable_search else None

    # Construir config base. Para modelos con thinking desactivamos thinking
    # para que TODO el presupuesto vaya a la respuesta visible.
    generation_config_kwargs = dict(
        system_instruction=system_prompt,
        max_output_tokens=max_tokens,
        temperature=0.3,
        tools=tools,
    )

    # Modelos conocidos que rechazan thinking_budget=0 con 400 INVALID_ARGUMENT
    skip_thinking = any(bad in model.lower() for bad in ("3.5-flash-lite",))
    if not skip_thinking and hasattr(types, "ThinkingConfig") and any(
        k in model for k in ("2.5", "3.0", "3.5", "flash", "lite", "flex")
    ):
        try:
            generation_config_kwargs["thinking_config"] = (
                types.ThinkingConfig(thinking_budget=0)
            )
        except Exception as _e:
            logger.warning(f"  ThinkingConfig no aplicable: {_e}")

    for attempt in range(1, max_retries + 1):
        try:
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

            # 1. Recuperación inmediata si el modelo rechaza thinking_config (400 INVALID_ARGUMENT)
            if "thinking_config" in generation_config_kwargs and (
                "INVALID_ARGUMENT" in err_str or "400" in err_str
            ):
                logger.warning(
                    f"  Modelo {model} no soporta thinking_config ({e}). "
                    f"Reintentando inmediatamente sin thinking_config..."
                )
                generation_config_kwargs.pop("thinking_config", None)
                continue

            # 2. Si Google Search Grounding falla por cuota (429 / RESOURCE_EXHAUSTED)
            if generation_config_kwargs.get("tools") is not None and (
                "RESOURCE_EXHAUSTED" in err_str or "429" in err_str
            ):
                logger.warning(
                    f"Gemini con Google Search falló por cuota ({e}). "
                    f"Reintentando inmediatamente sin Google Search Grounding..."
                )
                tools = None
                generation_config_kwargs["tools"] = None
                if on_search_exhausted and callable(on_search_exhausted):
                    try:
                        on_search_exhausted()
                    except Exception:
                        pass
                continue

            transient = (
                any(code in err_str for code in ("429", "500", "502", "503", "504"))
                or "UNAVAILABLE" in err_str
                or "RESOURCE_EXHAUSTED" in err_str
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

            logger.error(f"Error llamando a Gemini ({model}): {e}")
            return f"ERROR: {e}"


# ═══════════════════════════════════════════════════════
# GENERACIÓN CON GROQ (OpenAI-compatible, 131k context)
# ═══════════════════════════════════════════════════════

GROQ_MUSE_MODEL = "meta-models/muse-spark-open"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
GROQ_FALLBACK_MODEL = "openai/gpt-oss-120b"
GROQ_OUTPUT_LIMITS = {
    GROQ_MUSE_MODEL: 131072,
    GROQ_DEFAULT_MODEL: 32768,
}


def generate_brief_groq(
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    model: str = GROQ_DEFAULT_MODEL,
    max_tokens: int = 8192,
    max_retries: int = 3,
) -> str:
    """
    Genera el briefing usando la API de Groq (OpenAI-compatible).

    Args:
        system_prompt: Instrucciones del sistema
        user_prompt: El prompt completo con datos + template
        api_key: API key de Groq (GROQ_API_KEY)
        model: Modelo de Groq (ej: llama-3.3-70b-versatile)
        max_tokens: Tokens máximos de salida
        max_retries: Reintentos ante errores transitorios

    Returns:
        El briefing generado en formato Markdown
    """
    logger.info(f"→ Llamando a Groq ({model})...")
    logger.info(f"  Prompt size: ~{len(user_prompt)} caracteres")

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    # Algunos modelos Groq aceptan hasta 131k de salida; el resto, 8192.
    is_muse = model == GROQ_MUSE_MODEL
    output_ceiling = GROQ_OUTPUT_LIMITS.get(model, 8192)
    capped_tokens = min(max_tokens, output_ceiling) if max_tokens else output_ceiling

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": capped_tokens,
    }
    # Muse Spark Open está optimizado para su temperatura por defecto.
    if not is_muse:
        payload["temperature"] = 0.3

    start_time = time.time()

    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(url, headers=headers, json=payload)

            elapsed = round(time.time() - start_time, 1)

            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    logger.error("Groq devolvió una respuesta sin 'choices'")
                    return "ERROR: Groq devolvió una respuesta vacía."

                choice = choices[0]
                message = choice.get("message", {})
                content = (message.get("content") or "").strip()
                finish_reason = choice.get("finish_reason", "unknown")

                usage = data.get("usage", {})
                logger.info(f"✓ Respuesta de Groq recibida en {elapsed}s")
                if usage:
                    logger.info(
                        f"  Tokens — Input: {usage.get('prompt_tokens')}, "
                        f"Output: {usage.get('completion_tokens')}, "
                        f"Total: {usage.get('total_tokens')}"
                    )
                logger.info(f"  finish_reason: {finish_reason}")

                if finish_reason == "length":
                    logger.warning(
                        "⚠ finish_reason=length — el brief en Groq se truncó. "
                        "Considera aumentar max_tokens."
                    )

                if not content:
                    logger.error("La respuesta de Groq no contenía texto")
                    return "ERROR: La respuesta de Groq estaba vacía."

                return content

            err_text = resp.text
            err_code = resp.status_code
            logger.warning(f"  Groq HTTP {err_code}: {err_text[:200]}")

            # Conmutación automática si el modelo no está disponible o no hay
            # acceso Enterprise (403/404).
            if err_code in (403, 404) and model != GROQ_FALLBACK_MODEL:
                logger.warning(
                    f"  Modelo Groq '{model}' no disponible (HTTP {err_code}). "
                    f"Conmutando automáticamente a '{GROQ_FALLBACK_MODEL}'..."
                )
                return generate_brief_groq(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    api_key=api_key,
                    model=GROQ_FALLBACK_MODEL,
                    max_tokens=max_tokens,
                    max_retries=max_retries,
                )

            # 413: el prompt supera el límite de tokens por minuto del plan.
            if err_code == 413:
                logger.error(
                    "  El prompt excede el límite TPM del plan Groq. "
                    "Reduce el prompt o actualiza el plan; en Free/Developer "
                    "gpt-oss-120b está limitado a 8K TPM."
                )

            if err_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                wait_seconds = attempt * 4
                logger.warning(
                    f"  Groq devolvió {err_code}. Reintentando en {wait_seconds}s "
                    f"({attempt}/{max_retries})..."
                )
                time.sleep(wait_seconds)
                continue

            return f"ERROR: Groq HTTP {err_code}: {err_text}"

        except Exception as e:
            err_str = str(e)
            transient = (
                "ConnectError" in err_str
                or "ReadTimeout" in err_str
                or "RemoteDisconnected" in err_str
                or "SSL" in err_str
            )
            if transient and attempt < max_retries:
                wait_seconds = attempt * 3
                logger.warning(
                    f"  Error de conexión con Groq ({e}). Reintentando en {wait_seconds}s "
                    f"({attempt}/{max_retries})..."
                )
                time.sleep(wait_seconds)
                continue

            logger.error(f"Error llamando a Groq ({model}): {e}")
            return f"ERROR: {e}"

    return "ERROR: Reintentos agotados en Groq."


# ═══════════════════════════════════════════════════════
# FALLBACK MULTI-MODELO Y MULTI-PROVEEDOR
# ═══════════════════════════════════════════════════════

# Espera entre pasadas completas de la cadena Gemini (libera cuota por minuto)
_CHAIN_PASS_WAIT_S = 60


def generate_brief_with_fallback(
    system_prompt: str,
    user_prompt: str,
    *,
    gemini_api_key: str = "",
    gemini_model: str = "gemini-2.5-flash",
    gemini_fallback_models: list[str] | None = None,
    gemini_max_tokens: int = 32768,
    enable_search: bool = True,
    max_chain_passes: int = 2,
    groq_api_key: str = "",
    groq_model: str = GROQ_DEFAULT_MODEL,
    forced_provider: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """
    Genera el brief con arquitectura híbrida multi-proveedor:
    1. Si se solicita --model groq: ejecuta directamente con Groq.
    2. En flujo normal: intenta la cadena de modelos Gemini (primario + fallbacks).
    3. Si TODA la cadena Gemini falla: recurre a Groq (llama-3.3-70b-versatile) como paracaídas externo.

    Returns:
        (brief_text, proveedor_usado, modelo_usado) si algún modelo responde;
        (None, None, None) si todos fallan.
    """
    # ── Caso 1: Proveedor Groq forzado explícitamente ──
    if forced_provider == "groq":
        if not groq_api_key:
            logger.error("✗ GROQ_API_KEY no configurada pero se solicitó proveedor 'groq'.")
            return None, None, None
        logger.info(f"→ Ejecutando directamente con Groq ({groq_model}) por solicitud explícita...")
        text = generate_brief_groq(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            api_key=groq_api_key,
            model=groq_model,
            max_tokens=gemini_max_tokens,
        )
        if text and not text.startswith("ERROR"):
            logger.info(f"✓ Brief generado con groq ({groq_model})")
            return text, "groq", groq_model
        logger.error(f"✗ Groq falló: {text}")
        return None, None, None

    # ── Caso 2: Cadena Gemini (flujo principal) ──
    if gemini_api_key:
        fallbacks = gemini_fallback_models or []
        candidates: list[str] = []
        for m in [gemini_model, *fallbacks]:
            if m and m not in candidates:
                candidates.append(m)

        search_available = enable_search

        def _mark_search_exhausted():
            nonlocal search_available
            if search_available:
                logger.info("  Desactivando Google Search para el resto de la cadena en esta corrida.")
                search_available = False

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
                    enable_search=search_available,
                    on_search_exhausted=_mark_search_exhausted,
                )

                if text and not text.startswith("ERROR"):
                    logger.info(f"✓ Brief generado con gemini ({model})")
                    return text, "gemini", model

                logger.warning(
                    f"✗ gemini ({model}) falló: {text}. Probando siguiente modelo..."
                )

        logger.error("✗ Todos los modelos Gemini fallaron en todas las pasadas.")
    else:
        logger.warning("⚠ GEMINI_API_KEY no configurada. Saltando directo a proveedores externos.")

    # ── Caso 3: Fallback Externo Multi-Proveedor (Groq) ──
    if groq_api_key and (forced_provider != "gemini"):
        logger.warning(
            f"⚠ Activando fallback multi-proveedor externo: Groq ({groq_model})..."
        )
        text = generate_brief_groq(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            api_key=groq_api_key,
            model=groq_model,
            max_tokens=gemini_max_tokens,
        )

        if text and not text.startswith("ERROR"):
            logger.info(f"✓ Brief generado con fallback groq ({groq_model})")
            return text, "groq", groq_model

        logger.error(f"✗ Fallback Groq también falló: {text}")

    logger.error("✗ Ningún proveedor de IA pudo generar el brief.")
    return None, None, None
