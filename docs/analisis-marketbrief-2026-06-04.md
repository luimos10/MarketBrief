# Análisis MarketBrief — sesión 2026-06-04

> Conversación con Claude Code: explicación de la columna BIAS del dashboard,
> funcionamiento completo de la app y sincronización con GitHub.

---

## Pregunta inicial

Análisis del dato del **BIAS DASHBOARD** (sección 3 del brief), cómo se tabula
la columna *Bias*, funcionamiento general de la app y si está sincronizada con
el repo de GitHub.

Captura de referencia (brief 2026-06-04, tier PRINCIPALES):

| Activo | Tier | Precio | Bias | Trend | Mom. | Pos. | Sent. | EMA200 | RSI | ADX | Nivel clave |
|---|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| BTC    | P | 64138.55 | −55 | −1.00 | −1.00 | −0.56 | +0.76 | −18.22% | 18.7 | 36.7 | 61383.56 (S) |
| SP500  | P | 7544.25  | +40 | +0.87 | −0.16 | +0.00 | +0.76 | +10.03% | 63.6 | 33.4 | 7527.5 (S) |
| NASDAQ | P | 30481.25 | +51 | +1.00 | +0.00 | +0.00 | +0.76 | +18.80% | 73.1 | 37.9 | 30216.5 (S) |
| GOLD   | P | 4497.20  | +4  | +0.08 | −0.30 | +0.00 | +0.76 | +3.01%  | 44.1 | 24.3 | 4427.20 (S) |
| OIL    | P | 95.38    | +45 | +1.00 | −0.17 | +0.00 | +0.76 | +20.60% | 49.0 | 14.8 | 97.0 (R) |

---

## 1. Cómo se tabula el "BIAS" (y TREND / MOM. / POS. / SENT.)

La columna **BIAS** **no la inventa la IA** — la calcula un motor numérico propio
(`modules/bias_engine.py`) y la IA solo la transcribe. Es un score entero de
**−100 (muy bajista) a +100 (muy alcista)**.

### Fórmula

El bias es una **suma ponderada** de 4–5 sub-componentes, cada uno normalizado a
−1.00 … +1.00 (esas son las columnas TREND, MOM., POS., SENT.):

| Componente | Peso (crypto) | Peso (tradicional) | Qué mide |
|---|---|---|---|
| **Trend** | 30 | 40 | Alineación de EMAs (60%) + distancia % vs EMA200 (40%) |
| **Momentum (Mom.)** | 25 | 35 | RSI mapeado 30–70 → −1..+1 (50%) + estado MACD (50%) |
| **Volatility** | 10 | 10 | Siempre 0 — informativo, no direccional |
| **Positioning (Pos.)** | 20 | — | Funding + ratio Long/Short. **Solo crypto** |
| **Sentiment (Sent.)** | 15 | 15 | Fear & Greed (contrarian: miedo extremo = +1) |

En activos **no-crypto** (SP500, NASDAQ, GOLD, OIL) no hay funding ni L/S, así que
el peso de Positioning (20) se reparte: +10 a Trend y +10 a Momentum. Por eso la
columna **POS. sale +0.00** para todo lo que no es BTC.

```
BIAS = Σ (peso_componente × valor_componente)  →  clamp a [−100, +100]  →  redondeo
```

Etiqueta cualitativa: `≥60` Muy alcista · `≥25` Alcista · `−25..+25` Neutral ·
`≥−60` Bajista · `<−60` Muy bajista.

### Verificación con los números reales de la captura

- **BTC** (crypto): `30·(−1.00) + 25·(−1.00) + 10·0 + 20·(−0.56) + 15·(+0.76)` = −54.8 → **−55** ✅
- **NASDAQ** (trad): `40·(+1.00) + 35·(0.00) + 15·(+0.76)` = 51.4 → **+51** ✅
- **GOLD** (trad): `40·(+0.08) + 35·(−0.30) + 15·(+0.76)` = 4.1 → **+4** ✅
- **OIL** (trad): `40·(+1.00) + 35·(−0.17) + 15·(+0.76)` = 45.5 → **+45** ✅

Todos coinciden con la columna BIAS → confirma que TREND/MOM./POS./SENT. son los
componentes crudos (−1..+1) y BIAS es su combinación ponderada.

### Detalle de columnas

- **TREND −1.00** (BTC): EMAs bajistas + precio bajo EMA200 (−18.22%) → trend bajista máximo.
- **MOM. −1.00** (BTC): RSI 18.7 (sobreventa extrema) + MACD bajista.
- **POS. −0.56** (BTC): funding / ratio L/S con longs aglomerados → edge bajista (riesgo squeeze).
- **SENT. +0.76**: igual para todos (Fear & Greed es global). +0.76 ⇒ F&G ≈ 12 (miedo extremo) → contrarian alcista.
- **TIER**: P = Principal, M = Macro context, W = Watchlist. Solo los "P" llevan análisis profundo.
- **EMA200 / RSI / ADX / NIVEL CLAVE**: indicadores pre-calculados que la IA copia tal cual. NIVEL CLAVE: (S)=soporte, (R)=resistencia.

> Los componentes en la tabla están redondeados a 2 decimales, pero el cálculo
> interno usa valores sin redondear — puede haber ±1 de diferencia al rehacer la
> cuenta a mano.

---

## 2. Cómo funciona toda la app (MarketBrief)

Generador automatizado de **briefings pre-market institucionales**. Flujo
(`brief.py`, 4 pasos):

**PASO 1 — Recolectar datos** (`modules/data_fetcher.py` + módulos):
- Mercados tradicionales (Yahoo Finance), derivados crypto (Binance Futures)
- Fear & Greed, CoinMarketCap, correlaciones 30d
- Funding regime, zonas de liquidación (proxy de mechas)
- FRED (curva de tipos, liquidez Fed: RRP/TGA), VIX term structure, rotación sectorial
- Options flow BTC (Deribit), noticias (CryptoPanic + Finnhub), COT report (CFTC), calendario macro

**PASO 1.5 — Motor de bias** (`bias_engine.py`): convierte indicadores en los
scores numéricos. Filosofía: *"el LLM ya no inventa el sesgo, lo justifica"* — el
número es trazable y determinista.

**PASO 2 — Construir el prompt** (`prompt_builder.py`): empaqueta datos + bias
scores en contexto y los inyecta en `prompt_template.txt` (versión **v1.4**).
Sistema A/B opcional con `prompt_template_v2.txt`. System prompt: rol de
"Head of Trading + Quant Analyst".

**PASO 3 — Generar con IA**: **Gemini con fallback automático a Groq**. Produce el
markdown del brief, incluido un bloque JSON estructurado obligatorio (sección 9).

**PASO 4 — Entregar** (`modules/delivery.py`): HTML (navegador) y/o Telegram.
Guarda salidas en `output/brief_YYYY-MM-DD.{md,json}`.

**Extras**: `evaluate_calls.py` + `modules/logbook.py` registran cada "call" en
`output/calls.jsonl` para medir hit rate del modelo en el tiempo.

**Disparo**: el brief lo lanza **cron-job.org** (no el cron de GitHub Actions)
varias veces al día → commits automáticos `brief: <timestamp> [skip ci]`.

---

## 3. Sincronización con GitHub

- Remoto: `https://github.com/luimos10/MarketBrief.git` ✅ (repo correcto)
- Al inicio de la sesión, el local estaba **7 commits por detrás** de `origin/main`
  (`HEAD 70f7cd4` vs `origin/main 39c591e`).
- Esos 7 commits eran **briefs automáticos** de cron-job.org (días 2, 3 y 4 de junio).
- Se ejecutó `git pull --ff-only` → **fast-forward** sin conflictos.

Resultado del pull:

```
Updating 70f7cd4..39c591e
Fast-forward
 output/brief_2026-06-02.json |  63 ++++++++++
 output/brief_2026-06-02.md   | 152 ++++++++++++++
 output/brief_2026-06-03.json |  63 ++++++++++
 output/brief_2026-06-03.md   | 151 ++++++++++++++
 output/brief_2026-06-04.json |  63 ++++++++++
 output/brief_2026-06-04.md   | 152 ++++++++++++++
 output/calls.jsonl           |   7 ++
 7 files changed, 651 insertions(+)
```

Local y `origin/main` apuntan ahora al mismo commit. El brief del **2026-06-04**
(el de la captura, BTC −55) está en `output/brief_2026-06-04.md`.

---

_Generado por Claude Code · 2026-06-04_
