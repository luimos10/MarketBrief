"""
╔══════════════════════════════════════════════════════╗
║           MARKET BRIEF — CONFIGURACIÓN               ║
╚══════════════════════════════════════════════════════╝
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ═══════════════════════════════════════════════════════
# API KEYS
# ═══════════════════════════════════════════════════════
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CMC_API_KEY = os.getenv("CMC_API_KEY")

# Calendario macro (financialmodelingprep.com — opcional, free tier disponible)
FMP_API_KEY = os.getenv("FMP_API_KEY", "")

# News & sentiment (Fase 3)
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# ═══════════════════════════════════════════════════════
# VERSIONADO DEL PROMPT (para tracking de alpha)
# ═══════════════════════════════════════════════════════
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "v1.4")

# FRED (St. Louis Fed) - macro liquidez + yield curve. Free en
# https://fred.stlouisfed.org/docs/api/api_key.html (registro 1 min).
FRED_API_KEY = os.getenv("FRED_API_KEY", "")

# ═══════════════════════════════════════════════════════
# MODELO DE GEMINI
# ═══════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════
# MODELOS DE AI
# ═══════════════════════════════════════════════════════
AI_MODEL = os.getenv("AI_MODEL", "gemini")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GROQ_MODEL = os.getenv(
    "GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"
)
# MAX_TOKENS: techo de output del LLM. Gemini 2.5 Flash soporta hasta ~65K,
# pero con thinking interno activado consume tokens invisibles. Subimos a 16K
# y deshabilitamos thinking en brief_generator.py para que todo el budget
# vaya a la respuesta. Groq mantiene su techo en 8192 (limite del modelo).
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "16000"))
GROQ_MAX_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "8192"))

# ═══════════════════════════════════════════════════════
# ACTIVOS A MONITOREAR (estructura por tiers)
# ═══════════════════════════════════════════════════════
# Tier 1 — Principales: análisis profundo en sección "Top 5" del brief.
CRYPTO_PRINCIPAL = {
    "BTC/USDT": "BTCUSDT",
}
YFINANCE_PRINCIPAL = {
    "S&P 500 (ES)":     "ES=F",
    "Nasdaq 100 (NQ)":  "NQ=F",
    "Gold (XAUUSD)":    "GC=F",
    "Crude Oil (WTI)":  "CL=F",
}

# Tier 2 — Macro context: aparecen en sección "Contexto Macro" del brief.
YFINANCE_MACRO = {
    "DXY":     "DX-Y.NYB",
    "VIX":     "^VIX",
    "VIX9D":   "^VIX9D",
    "VIX3M":   "^VIX3M",
    "VVIX":    "^VVIX",
    "US02Y":   "^IRX",
    "US10Y":   "^TNX",
    "US30Y":   "^TYX",
}

# Tier 3 — Watchlist: bias score + bias dashboard, sin análisis profundo.
CRYPTO_WATCHLIST = {
    "ETH/USDT": "ETHUSDT",
    "SOL/USDT": "SOLUSDT",
}
YFINANCE_WATCHLIST = {
    "Silver (XAGUSD)":  "SI=F",
}

# Sector ETFs USA — para detección de rotation risk-on/risk-off.
SECTOR_ETFS = {
    "XLK_Tech":         "XLK",
    "XLF_Financials":   "XLF",
    "XLE_Energy":       "XLE",
    "XLY_Cons_Disc":    "XLY",
    "XLU_Utilities":    "XLU",
}

# Dicts compuestos (lo que data_fetcher consume — preserva compatibilidad)
CRYPTO_SYMBOLS = {**CRYPTO_PRINCIPAL, **CRYPTO_WATCHLIST}
YFINANCE_SYMBOLS = {
    **YFINANCE_PRINCIPAL,
    **YFINANCE_MACRO,
    **YFINANCE_WATCHLIST,
    **SECTOR_ETFS,
}

# Slugs de CoinMarketCap para datos adicionales
CMC_SLUGS = ["bitcoin", "ethereum", "solana"]
CMC_CONVERT = "USD"

# ═══════════════════════════════════════════════════════
# DELIVERY
# ═══════════════════════════════════════════════════════
# "telegram", "html", "both"
DELIVERY_METHOD = os.getenv("DELIVERY_METHOD", "html")

# ═══════════════════════════════════════════════════════
# RUTAS
# ═══════════════════════════════════════════════════════
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
PROMPT_FILE = os.path.join(BASE_DIR, "prompt_template.txt")

# ═══════════════════════════════════════════════════════
# TIMEZONE
# ═══════════════════════════════════════════════════════
TIMEZONE = "America/Santiago"
