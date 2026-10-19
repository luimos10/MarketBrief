# Market Brief Engine

Motor de briefing pre-market institucional para traders.
Recolecta datos multi-asset, calcula indicadores y bias scores deterministas,
genera un informe con LLM (Gemini, con cadena de fallback entre sus modelos) y entrega Markdown + HTML + Telegram.
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
  ADX) más **z-score 30d, máximo drawdown 30d y volatilidad realizada 30d**,
  calculados localmente con pandas/numpy — el LLM ya no inventa niveles.
- **Bias scores numéricos** -100..+100 por activo, con desglose por componente
  (trend, momentum, positioning, sentiment, volatility). La volatilidad pondera
  suavemente el z-score/drawdown (clamp ±0.3).
- **Matriz de correlación** (Pearson, retornos diarios de la ventana más
  reciente de 30d) entre todos los activos, inyectada en el contexto del LLM.
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
- **Salida JSON estructurada** (esquema rico: `report`, `regime`, `aggregate`,
  `assets` con entry/stop/tp1/tp2/rr/ev_r, `best_setup`, `no_trade_conditions`)
  — normalizada y persistida en `output/calls.jsonl` para evaluación posterior.
- **Dashboard de rendimiento** embebido en el HTML: hit rate histórico global
  y por activo + calibración (probabilidad declarada vs acierto real).

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
      │ │ │ │ │ │ │ │ │ │ │      (Gemini)           → output/brief_*.json
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

Loop de rendimiento (offline; en CI corre antes del brief y no lo bloquea):
   evaluate_calls.py
      → modules/evaluator.py   (decisión operativa al cierre del horizonte +
                                niveles entry/stop/tp sobre velas Binance/yfinance)
      → output/eval_results.jsonl   detalle por call (sólo filas v2)
      → output/perf.json            agregado → dashboard HTML (brief.py lo lee)
      → con --write: calls.jsonl    marca evaluated=true + eval[]
   modules/performance_feedback.py
      → resumen del rendimiento real → prompt (calibración del LLM)
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
| **Correlación cruzada** | **Yahoo (retornos diarios recientes)** | **1h cache** | **Matriz Pearson entre activos → confluencia/divergencia** |

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
   - Computa **z-score 30d, máximo drawdown 30d y volatilidad realizada 30d**.
   - Calcula bias score -100..+100 con pesos:
     `trend 30% + momentum 25% + positioning 20% + sentiment 15% + volatility 10%`.
     El componente de volatilidad pondera el z-score (±2.5σ → ±0.25) y penaliza
     drawdowns < -3% (clamp ±0.3).
   - Asigna etiqueta cualitativa (`Muy alcista | Alcista | Neutral | Bajista | Muy bajista`).

3. **Construcción del prompt** (`modules/prompt_builder.py`)
   - **Sanitiza** el contexto con `_sanitize_node`: convierte `N/A`, `ERROR`,
     `none`, `null` o cadenas vacías en `None` para que el LLM nunca lea
     marcadores de error como datos reales.
   - Formatea TODO el contexto en un texto estructurado.
   - Inyecta las secciones especiales: **INDICADORES TÉCNICOS**, **BIAS SCORES**,
     **MATRIZ DE CORRELACIÓN**, **MÉTRICAS ADICIONALES** (z-score / drawdown /
     volatilidad), **OPTIONS FLOW**, **COT REPORT**, **LIQUIDACIONES 24h**,
     **FUNDING RÉGIMEN**, **EVENTOS MACRO HOY**, **NEWS & SENTIMENT**.
   - Anexa el template editable (`Prompt Maestro` .md, `prompt_template.txt` o
     `prompt_template_v2.txt`). Si es el `Prompt Maestro` (.md) no se anexa el
     esquema JSON legado: ya lo define el propio template (esquema rico).
   - Inyecta `Versión prompt: vX.X` para tracking.

4. **Generación con LLM** (`modules/brief_generator.py`)
   - **Solo Gemini** vía `google-genai` con Google Search Grounding activable.
     Con `thinking_config.thinking_budget=0` (modelos Flash/Lite) para volcar
     todo el presupuesto a la respuesta visible; si el grounding agota cuota
     (`RESOURCE_EXHAUSTED`) se reintenta sin search.
   - **Cadena de fallback entre modelos Gemini** (misma API key):
     `GEMINI_MODEL` (primario) → `GEMINI_FALLBACK_MODELS` (en orden). Si
     **toda** la cadena falla en una pasada, espera ~60s y la repite (2
     pasadas) para dar tiempo a liberar cuota por minuto.
   - Retry 3x con exponential backoff para 429/5xx.
   - Temperatura 0.3 para consistencia.

5. **Extracción JSON + Logbook** (`modules/logbook.py`)
   - Busca el bloque ` ```json ... ``` ` y lo **normaliza al esquema rico**
     canónico (`report`, `regime`, `aggregate`, `assets`, `intermarket`,
     `catalysts`, `risks`, `best_setup`, `no_trade_conditions`), con
     backward-compat para los esquemas legacy v1.4 (`principales`+
     `macro_context`) y v1.3 (`activos`).
   - Lo guarda como `output/brief_YYYY-MM-DD_<sesión>.json` (sidecar, una
     sesión por corrida: `tokio`, `londres` o `ny`).
   - **Motor determinista** (`modules/decision_engine.py`): recalcula `rr` y
     `ev_r` desde entry/stop/tp1/tp2, `aggregate` desde los activos tier P,
     normaliza `decision` y marca `levels_valid=false` si los niveles son
     incoherentes. Las correcciones quedan en
     `structured._decision_engine.corrections`.
   - Agrega una línea a `output/calls.jsonl` con: timestamp, ai_label,
     prompt_version, structured, snapshot (precios + bias scores al momento),
     `synthetic` y `evaluated=false`.
   - **Fallback sintético**: si el LLM no emitió el bloque JSON, se sintetiza
     uno mínimo desde los bias_scores (`build_synthetic_structured`) para que
     el logbook nunca quede vacío (queda marcado `synthetic=true`).
   - Limpia el JSON crudo del MD/HTML entregado al usuario.

6. **Entrega** (`modules/delivery.py`)
   - Decora el brief: extrae sesgo, mercado líder, versión prompt, IA.
   - Reescribe el encabezado en formato canónico. El título muestra el modelo de
     IA que **realmente** generó el análisis (refleja el fallback si ocurrió).
   - El subtítulo del header HTML muestra la **hora local de cada bolsa**
     (Tokio · Londres · Nueva York) y resalta la **próxima apertura** —en vez de
     un texto fijo— calculado con `zoneinfo` (`build_header_subtitle`).
   - Genera HTML (parser propio sin dependencias) e **inyecta el dashboard**:
     tarjetas de sesgo por activo (orden por tier P/M/W) + gráfico Chart.js +
     tabla de **rendimiento histórico** leída de `output/perf.json`.
   - Envía documento HTML por Telegram si corresponde.
   - Guarda siempre Markdown como respaldo timestamped.

---

## Estructura del proyecto

```
MarketBrief/
├── brief.py                       # Orquestador principal (CLI entry)
├── config.py                      # Variables, símbolos, paths, timezone
├── evaluate_calls.py              # CLI del evaluador v3 (decisión + niveles + R)
├── prompt_template.txt            # Template v1 (A/B)
├── prompt_template_v2.txt         # Template v2 (A/B testing)
├── Prompt Maestro — … .md         # Template por defecto (esquema rico)
├── requirements.txt
├── install_scheduled_task.ps1     # Tarea programada Windows
├── remove_scheduled_task.ps1
├── run_brief.bat                  # Launcher HTML
├── run_brief_telegram.bat         # Launcher Telegram
│
├── modules/
│   ├── cache.py                   # diskcache TTL backend + decorador @cached
│   ├── data_fetcher.py            # 11 fetchers + ThreadPool collect_all_data
│   ├── indicators.py              # EMA, RSI, MACD, ATR, BB%, ADX, z/dd/vol
│   ├── bias_engine.py             # Score -100..+100 con desglose por componente
│   ├── options_flow.py            # Deribit BTC: PC ratio, max pain, gamma proxy
│   ├── news_sentiment.py          # CryptoPanic + Finnhub aggregator
│   ├── cot_report.py              # CFTC Legacy Futures-Only vía Socrata
│   ├── prompt_builder.py          # Contexto sanitizado + esquema JSON
│   ├── brief_generator.py         # Gemini + cadena de fallback (modelos Gemini)
│   ├── delivery.py                # MD parser, HTML + dashboard, Telegram
│   ├── evaluator.py               # Eval v3: decisión al horizonte, niveles, R, calibración
│   ├── decision_engine.py         # Motor determinista: rr/ev_r/aggregate/decision
│   ├── performance_feedback.py    # Feedback de rendimiento real → prompt
│   ├── session.py                 # Sesiones Tokio/Londres/NY + nombres con fecha
│   └── logbook.py                 # JSON rico normalizado + sidecar + calls.jsonl
│
├── tests/                         # pytest (lógica sin red)
├── conftest.py                    # Raíz en sys.path para pytest
│
├── output/                        # Briefs generados + logbook
│   ├── brief_YYYY-MM-DD_<sesión>.md
│   ├── brief_YYYY-MM-DD_<sesión>.html   # Incluye dashboard de rendimiento
│   ├── brief_YYYY-MM-DD_<sesión>.json   # Sidecar JSON estructurado
│   ├── calls.jsonl                # Append-only logbook (evaluated/write-back)
│   ├── eval_results.jsonl         # Detalle de la última evaluación
│   └── perf.json                  # Agregado de rendimiento (dashboard)
│
├── logs/                          # Logs por corrida
│   └── brief_YYYY-MM-DD_HHMMSS.log
│
├── docs/                          # Notas y análisis puntuales
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

### IA

| Variable | Default | Descripción |
|----------|---------|-------------|
| `GEMINI_API_KEY` | — | **Obligatoria** — única API key de IA |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Modelo primario de Gemini |
| `GEMINI_FALLBACK_MODELS` | `gemini-3.5-flash-lite` | Fallbacks en orden, separados por coma |
| `MAX_TOKENS` | `32768` | Tokens de salida máximos |

> **Cadena de fallback solo-Gemini.** Si el primario falla tras sus reintentos internos, el brief cae al siguiente modelo de `GEMINI_FALLBACK_MODELS` (misma API key). Si **toda** la cadena falla en una pasada, espera ~60s y la repite (2 pasadas) para dar tiempo a liberar cuota por minuto. El título, el footer y un banner de contingencia en el HTML muestran el modelo que **realmente** lo generó. Lógica en `generate_brief_with_fallback` (`modules/brief_generator.py`).

> **Límite de tokens.** `MAX_TOKENS` fija el techo de salida. Los modelos Flash/Lite se invocan con `thinking_config.thinking_budget=0` (todo el presupuesto va a la respuesta visible).

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

# Gemini sin web search
.\venv\Scripts\python.exe brief.py --no-search
```

### Argumentos CLI

| Argumento | Efecto |
|-----------|--------|
| `--html` | Genera HTML local |
| `--telegram` | Envía por Telegram (también genera HTML) |
| `--both` | HTML + Telegram |
| `--model {gemini}` | Fuerza el proveedor (solo gemini disponible) |
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
| `output/brief_YYYY-MM-DD_<sesión>.md` | Brief en Markdown (siempre, respaldo) |
| `output/brief_YYYY-MM-DD_<sesión>.html` | Brief en HTML con dashboard de sesgo + rendimiento (si `--html`, `--telegram` o `--both`) |
| `output/brief_YYYY-MM-DD_<sesión>.json` | Sidecar JSON estructurado (esquema rico, normalizado) |
| `output/calls.jsonl` | Append-only logbook acumulado (con `evaluated` / `eval`) |
| `output/eval_results.jsonl` | Detalle de la última corrida de `evaluate_calls.py` |
| `output/perf.json` | Agregado de rendimiento (dashboard del HTML) |
| `logs/brief_YYYY-MM-DD_HHMMSS.log` | Log detallado de la corrida |

> `eval_results.jsonl` y `perf.json` los genera `evaluate_calls.py` (no cada
> brief). El HTML los reutiliza: `brief.py` lee `perf.json` y lo inyecta en el
> dashboard de rendimiento.

### Esquema del JSON estructurado (rico)

```json
{
  "report":  { "date": "2026-05-25", "market_bias": "Alcista|Bajista|Neutral",
               "market_leader": "BTC|Nasdaq|...", "model": "gemini",
               "prompt_version": "v1.4" },
  "regime":  { "primary": "...", "secondary": "...", "confidence": null },
  "aggregate": { "main_assets_average_bias": 0, "decision": "..." },
  "assets": {
    "BTC":    { "tier": "P", "price": 0, "bias": 0, "confidence": 0,
                "signal_quality": "A+", "decision": "LONG|SHORT|WAIT|NO TRADE",
                "entry": 0, "stop": 0, "tp1": 0, "tp2": 0,
                "rr": 0, "probability": 0, "ev_r": 0 },
    "SP500":  { "tier": "P", "...": "..." },
    "NASDAQ": { "tier": "P" }, "GOLD": { "tier": "P" }, "OIL": { "tier": "P" },
    "DXY":    { "tier": "M" }, "VIX": { "tier": "M" }, "US10Y": { "tier": "M" },
    "ETH":    { "tier": "W" }, "SOL": { "tier": "W" },
    "SILVER": { "tier": "W" }, "US02Y": { "tier": "W" }
  },
  "intermarket": { "btc_vs_nasdaq": "", "dxy_vs_gold_btc": "",
                   "vix_signal": "", "watchlist_signal": "" },
  "catalysts": [], "risks": [], "best_setup": { "...": "..." },
  "no_trade_conditions": []
}
```

> **Compatibilidad.** `modules/logbook.normalize_structured` unifica cualquier
> esquema al rico. Acepta también el legacy v1.4 (`principales` +
> `macro_context`, con `sesgo`/`soporte`/`invalidacion`/`setup`) y v1.3
> (`activos`). Los campos `tier` se derivan por activo (P/M/W).

---

## Logbook y evaluación de calls

Cada brief genera una entrada en `output/calls.jsonl` con:

- Timestamp ISO 8601 UTC
- `ai_label` (proveedor + modelo)
- `prompt_version`
- `structured` (JSON normalizado al esquema rico)
- `snapshot` (precios + bias scores al momento del brief — ground truth)
- `synthetic` (si el JSON fue sintetizado por el fallback)
- `evaluated` (lo marca el evaluador; `false` hasta entonces)

### Evaluador v3 (`evaluate_calls.py` + `modules/evaluator.py`)

Mide la **decisión operativa** (`LONG`/`SHORT`) al cierre de un horizonte fijo
(`call_ts + horizon`), no el movimiento "alguna vez después". El sesgo
(`bias`) se evalúa aparte como métrica informativa.

- `complete=false` (ventana aún abierta) → no se puntúa.
- `WAIT`/`NO TRADE` no son calls operables → skip (no inflan el hit rate).
- Ejecución de niveles con fill obligatorio (`low ≤ entry ≤ high`), TP antes
  que SL salvo empate en la misma vela (gana el stop, criterio conservador) y
  primer TP cierra la posición (sin runner).
- Costos por activo (spread/slippage) restados del R: `r_realized`.
- Yields (`US10Y`/`US02Y`) se miden en puntos porcentuales, no en % relativo,
  y usan velas diarias (`2YY=F` para el 2Y).
- Guard de anomalías: un movimiento imposible para el activo (p.ej. mezclar
  `^IRX` con `2YY=F`) se marca como `movimiento anómalo` y no se puntúa.
- Calibración sólo con `probability` declarada (Brier); `confidence` no se usa.
- Salidas: reporte en consola, `output/perf.json`, `output/eval_results.jsonl`
  y, con `--write`, el write-back a `calls.jsonl`.

```powershell
# Evaluar calls cerradas (>=24h) y recalcular perf.json + eval_results.jsonl
.\venv\Scripts\python.exe evaluate_calls.py

# Write-back: marca evaluated=true y adjunta eval[] en calls.jsonl
.\venv\Scripts\python.exe evaluate_calls.py --write

# Solo los últimos 7 días
.\venv\Scripts\python.exe evaluate_calls.py --last 7

# Ventana de evaluación en horas (default 24)
.\venv\Scripts\python.exe evaluate_calls.py --horizon 48

# Edad mínima requerida (default = horizon, evita ventanas incompletas)
.\venv\Scripts\python.exe evaluate_calls.py --min-hours 12

# Solo dirección (omite la validación de niveles)
.\venv\Scripts\python.exe evaluate_calls.py --no-levels

# Filtrar por sesión (tokio|londres|ny)
.\venv\Scripts\python.exe evaluate_calls.py --sessions ny
```

Output (consola):

```
RESULTADO ACUMULADO (decisión operativa al cierre del horizonte)
  Total: hit=  6 miss= 12 skip= 26  →  33.3%
  Calibración: 56.7% declarada vs 33.3% real · Brier=0.255 (n=3)
  Sesgo (informativo): hit= 10 miss= 32 skip=  2  →  23.8%
  Ejecución: tp1=1 tp2=0 stop=2 no_fill=10 pending=0 none=3
  R medio: -0.511 (n=3, suma=-1.53)

POR ACTIVO / POR PROMPT VERSION / POR SESIÓN
  ...
```

### Criterio de hit/miss (decisión)

- `LONG` → hit si `move > threshold + cost` del activo.
- `SHORT` → hit si `move < -(threshold + cost)`.
- `WAIT`/`NO TRADE`/`UNKNOWN` → skip ("sin decisión operativa").
- Umbrales/costos por activo en `ASSET_EVAL_CONFIG` (`modules/evaluator.py`).

### Criterio de ejecución de niveles

- La dirección se toma de `decision` (legacy: inferida de entry/stop, o del
  setup+sesgo al normalizar).
- Fill obligatorio: la vela debe operar el `entry`; si nunca lo toca →
  `no_fill` (no cuenta como acierto direccional del trade).
- Conservador: si stop y TP tocan la misma vela, gana el stop.
- `r_realized` = R:R al primer TP alcanzado (sin runner) − `cost_r`.
- Activos con historia: BTC/ETH/SOL (Binance) + SP500/NASDAQ/GOLD/OIL/DXY/VIX/
  SILVER (yfinance 1h) y US10Y/US02Y (yfinance diario).

### Calibración

`perf.json` incluye `calibration`: la `probability` media declarada por el LLM
vs el `actual` (hit rate real) y el Brier score. Una `probability` alta con
`actual` bajo indica sobreconfianza.

### Features por call (aprendizaje)

Cada entrada nueva del logbook guarda `features` por activo (`modules/logbook.py`
→ `build_feature_snapshot`): RSI14, ATR%, EMA alignment, ADX, z-score, bias
score, `risk_pct`/`risk_atr` (distancia entry→stop en ATR), `tp1_rr` y
`entry_vs_price_pct`. El evaluador copia ese vector a las filas de resultado
(prefijo `f_`), y `perf.json` agrega buckets por `probability`, `confidence`,
`signal_quality`, `risk_atr` y `bias_score` para ver qué condiciones tienen edge
(además de la ejecución por activo: TP1/Stop/R medio).

### Feedback al prompt (loop de calibración)

Antes de generar el brief, `modules/performance_feedback.py` resume el
rendimiento real reciente (`eval_results.jsonl`) y lo inyecta al final del
prompt: hit rate por decisión, ejecución de niveles, R medio, calibración y
reglas obligatorias (p.ej. "el 66% de los niveles nunca se llenó: evita
entradas que requieren retroceso exacto"). El LLM debe calibrar sus
`probability` declaradas y no repetir patrones perdedores.

- `PERF_FEEDBACK_ENABLED=true|false` (default `true`)
- `PERF_FEEDBACK_DAYS=14` (si hay <50 filas en la ventana usa todo el historial)

### Tests

```powershell
.\venv\Scripts\python.exe -m pytest -q
```

Cubren evaluador (horizonte/fill/stop conservador/costos/yields/buckets),
motor determinista, sesiones, logbook/features y feedback. No requieren red.
El workflow de GitHub Actions los corre antes de evaluar y generar el brief.

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
| `FMP_API_KEY`, `CRYPTOPANIC_API_KEY`, `FINNHUB_API_KEY` | Opcionales |

`config.py` lee las variables del entorno, así que no hace falta `.env` en la nube.

### Continuidad del logbook

Como el runner es efímero, cada corrida hace `commit` de `output/`
(`calls.jsonl` + sidecars JSON) de vuelta al repo, para que `evaluate_calls.py`
acumule el historial entre ejecuciones. El workflow evalúa primero
(`python evaluate_calls.py --write`, con `continue-on-error` para no bloquear
la entrega) y después genera el brief (`python brief.py --telegram --no-open`),
de modo que el dashboard ya usa las métricas al día. El commit corre con
`if: always()` (aunque el brief falle, eval/logbook se preservan) y hace
`git pull --rebase --autostash` antes del push.

### Ejecución local (opcional, Windows Task Scheduler)

Si prefieres correrlo en tu máquina en vez de (o además de) la nube:

```powershell
# Instalar tarea diaria a 09:05 (default)
powershell -ExecutionPolicy Bypass -File .\install_scheduled_task.ps1 -Telegram

# Variantes: -Both  -NoOpen  |  Cambiar nombre/hora: -TaskName "..." -RunAt "09:05"

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

Revisa `.env`. Solo `GEMINI_API_KEY` es estrictamente
obligatoria. Las otras keys (CMC, FMP, CryptoPanic, Finnhub, Telegram) son opcionales
y la sección correspondiente se omite gracefully.

### Gemini devuelve `503 UNAVAILABLE`

Saturación temporal del proveedor. El cliente reintenta 3x con backoff exponencial.
Si el modelo primario (`GEMINI_MODEL`) sigue fallando, se activa la **cadena de
fallback solo-Gemini**: pasa al siguiente modelo de `GEMINI_FALLBACK_MODELS`
(por defecto `gemini-3.5-flash-lite`). Si **toda** la cadena falla en una pasada,
espera ~60s y la repite (2 pasadas). El título, el footer y un banner de
contingencia en el HTML muestran el modelo que **realmente** lo produjo
(ej. "Analizado con Gemini (gemini-3.5-flash-lite)"). Solo si **todos** los
modelos fallan en todas las pasadas se aborta la corrida (en la nube, eso
dispara el aviso de fallo por Telegram).

> Si Gemini falla por cuota (`RESOURCE_EXHAUSTED`) con Google Search activo, el
> cliente reintenta inmediatamente **sin** grounding antes de saltar al modelo
> siguiente de la cadena.

### El brief no incluye el bloque JSON estructurado

Verifica que el LLM no haya cortado por límite de tokens. Aumenta `MAX_TOKENS`
o revisa los logs. Si falta el JSON, `brief.py` genera un **fallback sintético**
desde los bias_scores (`build_synthetic_structured`) y lo registra en
`calls.jsonl` con `synthetic=true`; el Markdown y HTML se entregan igual.

### El dashboard de rendimiento no aparece en el HTML

El HTML lee `output/perf.json`, que genera `evaluate_calls.py`. Corre al menos
una vez el evaluador para poblarlo; si el archivo no existe o está corrupto,
el bloque se omite sin romper el resto del informe.

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
| Hecho | Fase 6: evaluador v2 (niveles/TP/SL/RR + calibración + write-back), `perf.json`/`eval_results.jsonl`, dashboard de rendimiento en HTML, matriz de correlación, z-score/drawdown/volatilidad, sanitización de contexto, `thinking_config` |
| Hecho | Fase 7: cadena de IA **solo-Gemini** (fallback entre modelos + 2 pasadas con espera por cuota); eliminación total de Groq (código, config, workflow) |
| Hecho | Fase 8 (P0): medición confiable — evaluador v3 (horizonte fijo, decisión operativa, fill obligatorio, stop conservador, costos, yields en pp), motor determinista, US02Y `2YY=F`, nombres por sesión, evaluación en CI, tests + pytest |
| Hecho | Fase 9 (P1): loop de aprendizaje — features por call, buckets (probability/confidence/quality/risk-ATR/sesgo), ejecución por activo en dashboard, feedback medido al prompt, `call_id`/`session` en el logbook |
| Hecho | Fase 10 (P2 parcial): HTML del brief escapa markup crudo de la IA (anti-XSS + `</script>` en payloads), correlación con ventana reciente (no la más vieja), reuso del historial diario 2y de yfinance (sin doble descarga) |
| Pendiente | Alertas intradía (re-runs cada 30 min con bias score delta > threshold) |
| Pendiente | Análisis de cohortes por modelo IA (hit rate por proveedor/modelo en el dashboard) |

---

## Limitaciones conocidas

- `--no-search` afecta a Gemini (desactiva Google Search Grounding).
- `thinking_config.thinking_budget=0` sólo aplica a modelos Gemini Flash/Lite/Flex
  soportados por el SDK; si el SDK no expone `ThinkingConfig`, se continúa sin él.
- La validación de niveles del evaluador requiere historia: BTC/ETH/SOL
  (Binance) y SP500/NASDAQ/GOLD/OIL/DXY/VIX/SILVER (yfinance 1h); US10Y/US02Y
  usan velas diarias (`2YY=F`). Para el resto sólo evalúa dirección. yfinance
  puede limitar el intradía en rangos muy antiguos (`--horizon` acota la
  ventana).
- La calibración del dashboard usa la `probability` declarada por el LLM; si el
  modelo no la emite, no se calcula (ya no usa `confidence`).
- El conversor Markdown→HTML es propio (sin dependencias). Soporta lo esencial
  (headers, bold, italic, listas, tablas, code blocks, blockquotes). Cualquier
  HTML crudo que emita la IA se **escapa** (se muestra literal): solo el
  Markdown se convierte a markup, y los payloads JSON del dashboard neutralizan
  `</script>` (anti-XSS).
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
