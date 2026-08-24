# Market Brief Engine

Motor de briefing pre-market institucional para traders.
Recolecta datos multi-asset, calcula indicadores y bias scores deterministas,
genera un informe con LLM (Gemini o Groq) y entrega Markdown + HTML + Telegram.
Además, persiste cada call en un logbook estructurado para medir hit rate
real en el tiempo.

**Corre solo en la nube con GitHub Actions** — de lunes a viernes, 30 minutos
antes de la apertura de Tokio, Londres y Nueva York (3 veces al día), sin
depender de tu PC. El disparo a la hora exacta lo hace un scheduler externo
(cron-job.org) vía la API de GitHub, así que no dependemos del cron de Actions.
Ver [Ejecución automática](#ejecución-automática-en-la-nube-github-actions).

---

## Tabla de contenidos

1. [Qué hace](#qué-hace)
2. [Arquitectura](#arquitectura)
3. [Fuentes de datos y edge informacional](#fuentes-de-datos-y-edge-informacional)
4. [Pipeline completo](#pipeline-completo)
5. [Estructura del proyecto](#estructura-del-proyecto)
6. [Instalación](#instalación)
7. [Variables de entorno](#variables-de-entorno)
8. [Uso](#uso)
9. [Outputs generados](#outputs-generados)
10. [Logbook y evaluación de calls](#logbook-y-evaluación-de-calls)
11. [A/B testing de prompts](#ab-testing-de-prompts)
12. [Caché y rendimiento](#caché-y-rendimiento)
13. [Ejecución automática en la nube (GitHub Actions)](#ejecución-automática-en-la-nube-github-actions)
14. [Troubleshooting](#troubleshooting)
15. [Skills de diseño para OpenCode](#skills-de-diseño-para-opencode)
16. [Roadmap](#roadmap)

---

## Qué hace

Cada corrida (manual o programada) produce un informe institucional con:

**Universo de activos por tiers (v1.4):**
- **Principales** (análisis profundo, 5 fijos): **BTC, SP500, Nasdaq, Gold, Oil**.
- **Macro context** (referencias para sección 4): DXY, VIX, US10Y.
- **Watchlist** (solo Bias Dashboard, sin análisis profundo): ETH, SOL,
  Silver, US02Y.

**Contenido del brief:**
- **Régimen macro** desde FRED (curva yield 10Y-2Y + RRP/TGA liquidity),
  VIX term structure (contango/backwardation) y sector rotation (XLK/XLF/
  XLE/XLY/XLU).
- **Análisis profundo** de los 5 activos principales: régimen técnico,
  niveles exactos, trigger, escenarios probabilísticos (base + alternativo)
  con R:R, catalizador específico y confluencia macro.
- **Indicadores técnicos verificados** (EMA20/55/200, RSI14, MACD, ATR, BB%,
  ADX) calculados localmente con pandas/numpy — el LLM ya no inventa niveles.
- **Bias scores numéricos** -100..+100 por activo, con desglose por componente
  (trend, momentum, positioning, sentiment, volatility).
- **Options flow BTC** (Deribit): PC ratio, max pain, strikes magnéticos cerca
  del spot, detección de pin day risk.
- **COT report semanal** (CFTC): posicionamiento smart money vs speculators
  en SP500, Nasdaq, Gold, Silver, Oil y DXY.
- **Liquidaciones 24h** (proxy via wicks 1h ponderados por volumen).
- **Funding régimen** (percentil 90d, días consecutivos del mismo signo,
  riesgo de squeeze).
- **Calendario macro high-impact** del día (FMP API).
- **News & sentiment** (CryptoPanic + Finnhub, opcional con keys).
- **Plan operativo** con 5-6 setups concretos (entrada / SL / TP1 / TP2 / R:R
  / tamaño / probabilidad).
- **Salida JSON estructurada** con `principales` + `macro_context` —
  persistida en `output/calls.jsonl` para evaluación posterior.

---

## Arquitectura

```
                  ┌──────────────────────────────────────────────┐
                  │              brief.py (orquestador)          │
                  └──────────────────────┬───────────────────────┘
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              │                          │                          │
   ┌──────────▼────────────┐  ┌──────────▼─────────┐  ┌─────────────▼───────────┐
   │   collect_all_data    │  │  build_full_prompt │  │   deliver_brief         │
   │   (ThreadPool x8)     │  │  + indicadores +   │  │   MD + HTML + Telegram  │
   │                       │  │  bias + JSON       │  │                         │
   └─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┬─┘  │  schema           │  └─┬───────────────────────┘
     │ │ │ │ │ │ │ │ │ │ │    └────────┬───────────┘    │
     │ │ │ │ │ │ │ │ │ │ │             │                │
     │ │ │ │ │ │ │ │ │ │ │             ▼                ▼
     │ │ │ │ │ │ │ │ │ │ │      generate_brief    extract_json_block
     │ │ │ │ │ │ │ │ │ │ │      (Gemini/Groq)     → output/brief_*.json
     │ │ │ │ │ │ │ │ │ │ │             │            + output/calls.jsonl
     │ │ │ │ │ │ │ │ │ │ │             ▼
     │ │ │ │ │ │ │ │ │ │ │       indicators.py + bias_engine.py
     │ │ │ │ │ │ │ │ │ │ │       (post-process: EMA/RSI/MACD/ADX + score)
     │ │ │ │ │ │ │ │ │ │ │
     │ │ │ │ │ │ │ │ │ │ └─ cot_report.py        ← CFTC Socrata
     │ │ │ │ │ │ │ │ │ └─── news_sentiment.py    ← CryptoPanic + Finnhub
     │ │ │ │ │ │ │ │ └───── options_flow.py      ← Deribit
     │ │ │ │ │ │ │ └─────── macro_calendar       ← Financial Modeling Prep
     │ │ │ │ │ │ └───────── funding_regime       ← Binance histórico funding
     │ │ │ │ │ └─────────── liquidation_zones    ← Binance klines wicks proxy
     │ │ │ │ └───────────── correlation_data     ← yfinance retornos 30d
     │ │ │ └─────────────── coinmarketcap        ← CMC Pro API
     │ │ └───────────────── fear_greed_index     ← alternative.me
     │ └─────────────────── crypto_derivatives   ← Binance Futures (ccxt) x3
     └───────────────────── traditional_markets  ← yfinance x9

Cache layer (diskcache): TTL por categoría
   yfinance_daily=1h | crypto_realtime=60s | funding_history=30min | macro=6h
```

---

## Fuentes de datos y edge informacional

| Capa | Fuente | Frecuencia | Edge |
|------|--------|------------|------|
| Precios tradicionales | Yahoo Finance | 1h cache | Niveles, volumen, VWAP intradía |
| Crypto derivados | Binance Futures (ccxt) | 60s cache | OI, funding actual, L/S ratio, taker buy/sell |
| Funding histórico | Binance fapi | 30min cache | Percentil 90d → detección de extremos crowded |
| Liquidaciones 24h | Binance klines (wick proxy) | 5min cache | Zonas de stops barridos, smart money sweeps |
| Sentimiento crypto | alternative.me Fear&Greed | 4h cache | Contrarian indicator |
| CMC Pro | CoinMarketCap | 5min cache | Dominance BTC/ETH, volumen global, derivados volume |
| Options BTC | Deribit (público) | 5min cache | PC ratio, max pain, gamma proxy (pin day risk) |
| Smart money semanal | CFTC Socrata API | 6h cache | Commercials vs Non-commercials net positioning |
| Calendario macro | Financial Modeling Prep | 6h cache | Eventos high-impact del día con hora local |
| News crypto | CryptoPanic | 5min cache | Titulares scored últimas 24h |
| News macro | Finnhub | 5min cache | News generales últimas 24h |
| **Curva yield + liquidez Fed** | **FRED (St. Louis Fed)** | **6h cache** | **Spread 10Y-2Y inversion (85% recession indicator), RRP/TGA delta semanal (Fed drain vs liquidez)** |
| **VIX term structure** | **Yahoo (^VIX9D, ^VIX3M, ^VVIX)** | **1h cache** | **Contango (calma) vs backwardation (stress) → regime shift 2-4 semanas** |
| **Sector rotation** | **Yahoo (XLK, XLF, XLE, XLY, XLU)** | **1h cache** | **Risk-on (Tech lead) vs risk-off (Utilities lead) vs inflación (Energy lead)** |

Todas las fuentes corren en paralelo con `ThreadPoolExecutor(max_workers=8)`.
Tiempo total típico: **10-15s en cold cache, <1s en warm cache.**

> **Fallbacks en la nube (geobloqueo de Binance).** Las IPs de datacenter de
> GitHub Actions reciben `HTTP 451` de Binance. El proyecto lo resuelve solo:
> precio + OHLCV caen a `data-api.binance.vision` (mirror spot, sin geobloqueo)
> y los datos de futuros (funding, OI, L/S, liquidaciones) a **OKX**. En local
> sigue usando Binance directamente. Transparente, sin configuración.

---

## Pipeline completo

1. **Recolección paralela** (`modules/data_fetcher.collect_all_data`)
   Lanza 11 tareas concurrentes contra todas las fuentes. Cada fetcher está
   decorado con `@cached("categoria")` para reusar resultados dentro de su TTL.

2. **Post-procesamiento numérico** (`modules/indicators.py` + `modules/bias_engine.py`)
   Para cada activo (crypto 1d+4h y tradicionales 1d):
   - Computa EMA20/55/200, RSI14, MACD, ATR, Bollinger %B, ADX.
   - Calcula bias score -100..+100 con pesos:
     `trend 30% + momentum 25% + positioning 20% + sentiment 15% + volatility 10%`.
   - Asigna etiqueta cualitativa (`Muy alcista | Alcista | Neutral | Bajista | Muy bajista`).

3. **Construcción del prompt** (`modules/prompt_builder.py`)
   - Formatea TODO el contexto en un texto estructurado.
   - Inyecta las secciones especiales: **INDICADORES TÉCNICOS**, **BIAS SCORES**,
     **OPTIONS FLOW**, **COT REPORT**, **LIQUIDACIONES 24h**, **FUNDING RÉGIMEN**,
     **EVENTOS MACRO HOY**, **NEWS & SENTIMENT**.
   - Anexa el template editable (`prompt_template.txt` o `prompt_template_v2.txt`).
   - Anexa el esquema JSON de salida estructurada obligatoria.
   - Inyecta `Versión prompt: vX.X` para tracking.

4. **Generación con LLM** (`modules/brief_generator.py`)
   - **Gemini** vía `google-genai` con Google Search Grounding activable.
   - **Groq** vía API OpenAI-compatible (Llama 4 Scout 17B por default).
   - Retry 3x con exponential backoff para 429/5xx.
   - Temperatura 0.3 para consistencia.

5. **Extracción JSON + Logbook** (`modules/logbook.py`)
   - Busca el bloque ` ```json ... ``` ` al final del brief.
   - Lo guarda como `output/brief_YYYY-MM-DD.json` (sidecar).
   - Agrega una línea a `output/calls.jsonl` con:
     timestamp, ai_label, prompt_version, structured (JSON parseado),
     snapshot (precios + bias scores al momento del brief).
   - Limpia el JSON crudo del MD/HTML entregado al usuario.

6. **Entrega** (`modules/delivery.py`)
   - Decora el brief: extrae sesgo, mercado líder, versión prompt, IA.
   - Reescribe el encabezado en formato canónico. El título muestra el modelo de
     IA que **realmente** generó el análisis (refleja el fallback si ocurrió).
   - El subtítulo del header HTML muestra la **hora local de cada bolsa**
     (Tokio · Londres · Nueva York) y resalta la **próxima apertura** —en vez de
     un texto fijo— calculado con `zoneinfo` (`build_header_subtitle`).
   - Genera HTML (parser propio sin dependencias).
   - Envía documento HTML por Telegram si corresponde.
   - Guarda siempre Markdown como respaldo timestamped.

---

## Estructura del proyecto

```
MarketBrief/
├── brief.py                       # Orquestador principal (CLI entry)
├── config.py                      # Variables, símbolos, paths, timezone
├── evaluate_calls.py              # Script standalone para medir hit rate
├── prompt_template.txt            # Template v1 (default)
├── prompt_template_v2.txt         # Template v2 (para A/B testing)
├── requirements.txt
├── install_scheduled_task.ps1     # Tarea programada Windows
├── remove_scheduled_task.ps1
├── run_brief.bat                  # Launcher HTML
├── run_brief_telegram.bat         # Launcher Telegram
│
├── modules/
│   ├── cache.py                   # diskcache TTL backend + decorador @cached
│   ├── data_fetcher.py            # 11 fetchers + ThreadPool collect_all_data
│   ├── indicators.py              # EMA, RSI, MACD, ATR, BB%, ADX (pandas puro)
│   ├── bias_engine.py             # Score -100..+100 con desglose por componente
│   ├── options_flow.py            # Deribit BTC: PC ratio, max pain, gamma proxy
│   ├── news_sentiment.py          # CryptoPanic + Finnhub aggregator
│   ├── cot_report.py              # CFTC Legacy Futures-Only vía Socrata
│   ├── prompt_builder.py          # Formateo del contexto + esquema JSON
│   ├── brief_generator.py         # Cliente Gemini + Groq con retry
│   ├── delivery.py                # MD parser, HTML, Telegram, decorate_brief
│   └── logbook.py                 # Extract JSON + sidecar + calls.jsonl
│
├── output/                        # Briefs generados + logbook
│   ├── brief_YYYY-MM-DD.md
│   ├── brief_YYYY-MM-DD.html
│   ├── brief_YYYY-MM-DD.json      # Sidecar JSON estructurado
│   └── calls.jsonl                # Append-only logbook para hit rate
│
├── logs/                          # Logs por corrida
│   └── brief_YYYY-MM-DD_HHMMSS.log
│
├── .cache/                        # diskcache local (gitignored)
├── .env                           # API keys (no commit)
└── .env.example
```

---

## Instalación

### 1. Crear entorno virtual

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 2. Instalar dependencias

```powershell
pip install -r requirements.txt
```

Dependencias clave:
- `google-genai` — Gemini API
- `yfinance` — Yahoo Finance
- `ccxt` — Binance Futures unified API
- `requests`, `python-dotenv`
- `diskcache` — caché TTL local
- `pandas`, `numpy` — indicadores técnicos

### 3. Configurar `.env`

```powershell
Copy-Item .env.example .env
```

Edita `.env` con tus API keys (ver sección siguiente).

---

## Variables de entorno

Todas configuradas en `.env`. Las marcadas como `(opcional)` se omiten gracefully.

### IA (obligatoria al menos una)

| Variable | Default | Descripción |
|----------|---------|-------------|
| `AI_MODEL` | `gemini` | Proveedor **primario**: `gemini` o `groq` |
| `GEMINI_API_KEY` | — | Required si AI_MODEL=gemini |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite` | |
| `GROQ_API_KEY` | — | Required si AI_MODEL=groq; también habilita el fallback |
| `GROQ_MODEL` | `groq/compound` | Modelo principal de Groq (1er fallback) |
| `GROQ_FALLBACK_MODEL` | `qwen/qwen3.6-27b` | Modelo secundario de Groq (2do fallback) |
| `MAX_TOKENS` | `12000` | Tokens de salida máximos |
| `GROQ_MAX_TOKENS` | `8192` | Techo Groq (clamp automático) |

> **Cadena de fallback automático entre modelos.** Si Gemini (`gemini-2.5-flash`) falla tras sus reintentos, el brief intenta con Groq usando `groq/compound` y, si este falla, utiliza `qwen/qwen3.6-27b`. El título y el footer del informe muestran el modelo que realmente lo generó. Lógica en `generate_brief_with_fallback` (`modules/brief_generator.py`).

### Datos de mercado

| Variable | Default | Descripción |
|----------|---------|-------------|
| `CMC_API_KEY` | — | CoinMarketCap (recomendado, free tier) |
| `FMP_API_KEY` | — | Financial Modeling Prep (calendario macro, opcional) |
| `CRYPTOPANIC_API_KEY` | — | News crypto scored (opcional) |
| `FINNHUB_API_KEY` | — | News macro/equity (opcional, 60 req/min free) |
| `FRED_API_KEY` | — | Federal Reserve Economic Data (recomendado, free key) |

### Entrega

| Variable | Default | Descripción |
|----------|---------|-------------|
| `DELIVERY_METHOD` | `html` | `html`, `telegram` o `both` |
| `TELEGRAM_BOT_TOKEN` | — | Required si Telegram |
| `TELEGRAM_CHAT_ID` | — | Required si Telegram |

### Versionado y A/B testing

| Variable | Default | Descripción |
|----------|---------|-------------|
| `PROMPT_VERSION` | `v1.4` | Tag inyectado en cada brief + logbook |
| `PROMPT_AB_ENABLED` | `false` | Si `true`, alterna 50/50 entre `prompt_template.txt` y `prompt_template_v2.txt` |

---

## Uso

### Comandos básicos

```powershell
# Default (según DELIVERY_METHOD en .env)
.\venv\Scripts\python.exe brief.py

# Forzar HTML local
.\venv\Scripts\python.exe brief.py --html

# HTML sin abrir navegador
.\venv\Scripts\python.exe brief.py --html --no-open

# Enviar por Telegram
.\venv\Scripts\python.exe brief.py --telegram

# Ambos
.\venv\Scripts\python.exe brief.py --both

# Forzar Groq
.\venv\Scripts\python.exe brief.py --model groq --html

# Gemini sin web search
.\venv\Scripts\python.exe brief.py --model gemini --no-search
```

### Argumentos CLI

| Argumento | Efecto |
|-----------|--------|
| `--html` | Genera HTML local |
| `--telegram` | Envía por Telegram (también genera HTML) |
| `--both` | HTML + Telegram |
| `--model {gemini,groq}` | Fuerza el proveedor |
| `--no-search` | Desactiva Google Search Grounding en Gemini |
| `--no-open` | No abre navegador tras generar HTML |

### Launchers `.bat`

```bat
run_brief.bat              :: HTML
run_brief_telegram.bat     :: Telegram
```

---

## Outputs generados

Cada corrida produce:

| Archivo | Contenido |
|---------|-----------|
| `output/brief_YYYY-MM-DD.md` | Brief en Markdown (siempre, respaldo) |
| `output/brief_YYYY-MM-DD.html` | Brief en HTML (si `--html`, `--telegram` o `--both`) |
| `output/brief_YYYY-MM-DD.json` | Sidecar JSON estructurado (sesgo + niveles por activo) |
| `output/calls.jsonl` | Append-only logbook acumulado para evaluación |
| `logs/brief_YYYY-MM-DD_HHMMSS.log` | Log detallado de la corrida |

### Esquema del JSON estructurado (v1.4)

```json
{
  "fecha": "2026-05-25",
  "prompt_version": "v1.4",
  "ai_model": "gemini",
  "sesgo_global": "Alcista|Bajista|Neutral",
  "mercado_lider": "BTC|Nasdaq|SP500|Gold|Oil|DXY|VIX|Mixto",
  "principales": {
    "BTC":    { "sesgo": "...", "precio_referencia": 0, "soporte": 0,
                "resistencia": 0, "invalidacion": 0,
                "setup": "continuacion|reversion|sweep|none" },
    "SP500":  { ... },
    "NASDAQ": { ... },
    "GOLD":   { ... },
    "OIL":    { ... }
  },
  "macro_context": {
    "DXY":    { "sesgo": "...", "nivel": 0 },
    "VIX":    { "sesgo": "...", "nivel": 0 },
    "US10Y":  { "sesgo": "...", "nivel": 0 }
  }
}
```

> Nota: `evaluate_calls.py` mantiene backward-compat con el schema v1.3
> que usaba `activos` en lugar de `principales`.

---

## Logbook y evaluación de calls

Cada brief genera una entrada en `output/calls.jsonl` con:

- Timestamp ISO 8601 UTC
- `ai_label` (proveedor + modelo)
- `prompt_version`
- `structured` (el JSON parseado del LLM)
- `snapshot` (precios + bias scores al momento del brief — ground truth)

### Evaluar hit rate

```powershell
# Evaluar todos los calls con al menos 20h de antigüedad
.\venv\Scripts\python.exe evaluate_calls.py

# Solo los últimos 7 días
.\venv\Scripts\python.exe evaluate_calls.py --last 7

# Desglosado por versión de prompt (A/B)
.\venv\Scripts\python.exe evaluate_calls.py --by-version

# Cambiar la edad mínima requerida (default 20h)
.\venv\Scripts\python.exe evaluate_calls.py --min-hours 24
```

Output:

```
RESULTADO GLOBAL
  Total: hit= 18 miss= 10 skip=  2  →  64.3%

POR ACTIVO
  BTC       hit=  6 miss=  2 skip=  0  →  75.0%
  ETH       hit=  4 miss=  3 skip=  0  →  57.1%
  NASDAQ    hit=  5 miss=  1 skip=  1  →  83.3%
  ...

POR PROMPT VERSION (A/B)
  v1.2      hit=  9 miss=  6 skip=  1  →  60.0%
  v2.0      hit=  9 miss=  4 skip=  1  →  69.2%
```

### Criterio de hit/miss

- Sesgo **Alcista** → hit si precio subió >0.5% desde snapshot.
- Sesgo **Bajista** → hit si precio bajó >0.5%.
- Sesgo **Neutral** → hit si movimiento absoluto ≤0.5%.

---

## A/B testing de prompts

1. Edita `prompt_template_v2.txt` con tu variante.
2. Pon `PROMPT_AB_ENABLED=true` en `.env`.
3. Cada corrida rota aleatoriamente entre v1 y v2 (50/50) y registra la
   versión en el logbook.
4. Tras 14-30 días, ejecuta:

```powershell
.\venv\Scripts\python.exe evaluate_calls.py --by-version --last 30
```

5. Adopta el ganador como `prompt_template.txt` y desactiva A/B.

---

## Caché y rendimiento

Backend: `diskcache` con TTL por categoría definidos en `modules/cache.py`:

| Categoría | TTL | Justificación |
|-----------|-----|---------------|
| `yfinance_daily` | 1h | Velas diarias cambian poco intradía |
| `crypto_realtime` | 60s | Ticker, funding actual, OI |
| `crypto_ohlcv` | 5min | OHLCV crypto |
| `cmc_global` | 5min | Dominance, volumen global |
| `fear_greed` | 4h | F&G publica diario |
| `liquidations` | 5min | Proxy de wicks |
| `funding_history` | 30min | Histórico funding 90d |
| `macro_calendar` | 6h | Eventos del día |

Cualquier corrida adicional dentro del TTL es **virtually free** (<1s).
La caché se guarda en `.cache/` (gitignored).

### Limpiar la caché manualmente

```powershell
Remove-Item -Recurse -Force .\.cache
```

---

## Skills de diseño para OpenCode

El proyecto incluye tres skills locales para que OpenCode aplique criterios
consistentes al trabajar sobre la interfaz de los briefs. Están en
`.opencode/skills/` y solo afectan a este repositorio. Tras crear o modificar
un skill, reinicia OpenCode para que lo vuelva a cargar.

| Skill | Cuándo invocarlo | Qué hace |
|-------|------------------|----------|
| `frontend-design` | Crear o modificar HTML, CSS, layout, tarjetas, tablas, gráficos o comportamiento responsive | Implementa cambios visuales en el generador, preservando la jerarquía de datos financieros, accesibilidad y compatibilidad móvil/escritorio. |
| `design-system` | Unificar colores, tipografías, espaciados, badges, tablas o componentes compartidos | Mantiene y extiende los tokens CSS y patrones reutilizables de la plantilla HTML. |
| `frontend-review` | Revisar UI, UX, responsive, accesibilidad o regresiones antes de publicar | Audita la interfaz sin editar por defecto y entrega hallazgos priorizados con rutas, líneas y correcciones recomendadas. |

Ejemplos de invocación:

```text
Usa frontend-design para mejorar la visualización móvil de las tarjetas de activos.
Usa design-system para unificar los estados bullish, bearish y neutral en el HTML.
Usa frontend-review para revisar el brief HTML antes de desplegarlo.
```

La fuente de verdad para cambios visuales es `modules/delivery.py`; los
archivos `output/brief_*.html` son resultados generados y no deben editarse
directamente.

---

## Ejecución automática en la nube (GitHub Actions)

El brief corre solo en GitHub Actions — **no necesitas la PC encendida**.
Workflow: `.github/workflows/market-brief.yml`.

### Programación

Se ejecuta **de lunes a viernes, 30 minutos antes de la apertura de cada
sesión**:

| Sesión | Apertura | Brief (30 min antes) | Zona |
|--------|----------|----------------------|------|
| Asia (Tokio) | 9:00 JST | **8:30 JST** | Asia/Tokyo |
| Londres | 8:00 UK | **7:30 UK** | Europe/London |
| Nueva York | 9:30 ET | **9:00 ET** | America/New_York |

El **timing lo controla un scheduler externo (cron-job.org)**, no el cron de
GitHub Actions (que es poco puntual y retrasaba/descartaba disparos). Hay 3 jobs
en cron-job.org, uno por sesión, cada uno programado en la **zona horaria de su
bolsa** — así el horario de verano (DST) se ajusta solo, sin pares de cron ni
*guard*. Cada job hace un `POST` a la API de GitHub para disparar el workflow
(`workflow_dispatch`):

```
POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/market-brief.yml/dispatches
Headers: Authorization: Bearer <PAT fine-grained, Actions: write>
         Accept: application/vnd.github+json
         X-GitHub-Api-Version: 2022-11-28
Body:    {"ref":"main"}
```

El workflow ya no tiene `schedule:` ni guard: solo `workflow_dispatch`, así que
cada disparo genera y entrega un brief. Si algo falla (Gemini 503 persistente,
geobloqueo, etc.), un step `if: failure()` avisa por Telegram con el enlace al
run. También puedes dispararlo a mano desde la pestaña **Actions** de GitHub.

### Secrets

En GitHub → **Settings → Secrets and variables → Actions**:

| Secret | Requerido |
|--------|-----------|
| `GEMINI_API_KEY` | Sí (modelo por defecto) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Sí (entrega) |
| `CMC_API_KEY`, `FRED_API_KEY` | Recomendados |
| `FMP_API_KEY`, `CRYPTOPANIC_API_KEY`, `FINNHUB_API_KEY`, `GROQ_API_KEY` | Opcionales |

`config.py` lee las variables del entorno, así que no hace falta `.env` en la nube.

### Continuidad del logbook

Como el runner es efímero, cada corrida hace `commit` de `output/`
(`calls.jsonl` + sidecars JSON) de vuelta al repo, para que `evaluate_calls.py`
acumule el historial entre ejecuciones. La entrega usa
`python brief.py --telegram --no-open`.

### Ejecución local (opcional, Windows Task Scheduler)

Si prefieres correrlo en tu máquina en vez de (o además de) la nube:

```powershell
# Instalar tarea diaria a 09:05 (default)
powershell -ExecutionPolicy Bypass -File .\install_scheduled_task.ps1 -Telegram

# Variantes: -Both  -Groq  -NoOpen  |  Cambiar nombre/hora: -TaskName "..." -RunAt "09:05"

# Eliminar
powershell -ExecutionPolicy Bypass -File .\remove_scheduled_task.ps1
```

La tarea se crea con `StartWhenAvailable`, por lo que si el equipo estaba
apagado a las 09:05 Windows la ejecutará en cuanto vuelva.

---

## Troubleshooting

### `ModuleNotFoundError`

Activa el venv o usa el Python directamente:

```powershell
.\venv\Scripts\python.exe brief.py --html
```

### Falta una API key

Revisa `.env`. Solo `GEMINI_API_KEY` (o `GROQ_API_KEY` si usas Groq) es estrictamente
obligatoria. Las otras keys (CMC, FMP, CryptoPanic, Finnhub, Telegram) son opcionales
y la sección correspondiente se omite gracefully.

### Gemini devuelve `503 UNAVAILABLE`

Saturación temporal del proveedor. El cliente reintenta 3x con backoff exponencial.
Si tras los reintentos sigue fallando, hay **fallback automático a Groq**: si
`GROQ_API_KEY` está configurada, el brief se genera con Groq sin intervención y el
título/footer del informe muestran el modelo que **realmente** lo produjo (ej.
"Analizado con Groq (…)"). Solo si **todos** los proveedores fallan se aborta la
corrida (en la nube, eso dispara el aviso de fallo por Telegram).

Para forzar Groq manualmente desde el inicio:

```powershell
.\venv\Scripts\python.exe brief.py --model groq --html
```

### El brief no incluye el bloque JSON estructurado

Verifica que el LLM no haya cortado por límite de tokens. Aumenta `MAX_TOKENS`
o revisa los logs. Si falta el JSON, `calls.jsonl` no se actualiza pero el
Markdown y HTML se entregan igual.

### `evaluate_calls.py` reporta `sin precio`

Algunos tickers (ej. DXY) pueden tardar en yfinance. Reintenta la evaluación;
los precios actuales se obtienen al vuelo en cada corrida.

### COT no encuentra un mercado

La CFTC publica viernes (data del martes). Si corres en lunes-jueves, los datos
son de la semana anterior. Algunos contratos (E-MINI vs MICRO) son filtrados
explícitamente en `modules/cot_report.py`.

### Crypto sin precio/indicadores en la nube (Binance `451`)

GitHub Actions corre en IPs de datacenter que Binance bloquea
(`HTTP 451 restricted location`). Está resuelto automáticamente: precio + OHLCV
caen a `data-api.binance.vision` (mirror spot) y los datos de futuros (funding,
OI, L/S, liquidaciones) a **OKX**. En local sigue usando Binance. No requiere
configuración; en el log verás `usando fallback ...` cuando aplique.

### Limpiar cache local

```powershell
Remove-Item -Recurse -Force .\.cache
```

---

## Roadmap

| Estado | Feature |
|--------|---------|
| Hecho | Fase 1: paralelización, caché TTL, liquidaciones, funding régimen, calendario macro, versionado de prompt |
| Hecho | Fase 2: indicadores pandas, bias engine numérico, JSON estructurado, logbook, `evaluate_calls.py` |
| Hecho | Fase 3: options flow Deribit, news sentiment, COT report semanal, A/B prompt testing |
| Hecho | Fase 4: tiers de activos (5 principales fijos + 3 macro + 4 watchlist), FRED (curva + liquidez), VIX term structure, sector rotation, frontend dark minimalista |
| Hecho | Fase 5: despliegue en la nube (GitHub Actions) — 3 corridas/día (Tokio/Londres/NY) disparadas por cron-job.org (DST por zona horaria) + fallbacks de geobloqueo (spot mirror + OKX) |
| Pendiente | Alertas intradía (re-runs cada 30 min con bias score delta > threshold) |
| Pendiente | Dashboard Streamlit para visualizar hit rate por activo y versión |

---

## Limitaciones conocidas

- `--no-search` afecta sólo a Gemini; Groq no usa grounding web.
- Si `MAX_TOKENS` excede el techo de Groq, se reduce automáticamente.
- El conversor Markdown→HTML es propio (sin dependencias). Soporta lo esencial
  (headers, bold, italic, listas, tablas, code blocks, blockquotes).
- Las liquidaciones son **proxy** (wicks 1h) — ningún exchange expone
  liquidaciones agregadas vía REST público gratuito. En local usa velas de
  Binance; en la nube, de OKX. Para data real se requeriría WebSocket
  `@forceOrder` o un proveedor pago tipo Coinglass.
- En la nube los datos de derivados (funding/OI/L-S) provienen de **OKX**, porque
  Binance geobloquea las IPs de GitHub Actions. Son ≈ equivalentes en BTC/ETH/SOL;
  el L/S de OKX es un ratio de cuentas agregado, sin el desglose top-trader
  account/position que da Binance (el bias engine no lo usa).
- COT se publica solo viernes. En lunes-jueves los datos son de la semana anterior.
- News sentiment requiere keys de CryptoPanic y Finnhub para habilitarse;
  sin ellas, esa sección queda vacía y el LLM cae al web grounding de Gemini.
