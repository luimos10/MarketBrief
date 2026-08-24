"""
╔══════════════════════════════════════════════════════╗
║         MÓDULO DE ENTREGA DEL BRIEF                   ║
║  Envía por Telegram y/o genera HTML local              ║
╚══════════════════════════════════════════════════════╝
"""

import json
import os
import re
import logging
import webbrowser
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger("MarketBrief")


# ═══════════════════════════════════════════════════════
# SUBTÍTULO: RELOJES DE CADA BOLSA + PRÓXIMA APERTURA
# ═══════════════════════════════════════════════════════

# Meses en español (el runner de la nube no tiene locale 'es', así que
# strftime("%B") devolvería "June" en inglés).
SPANISH_MONTHS = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

# (nombre, zona horaria, hora_apertura, minuto_apertura)
EXCHANGES = [
    ("Tokio",      "Asia/Tokyo",        9, 0),
    ("Londres",    "Europe/London",     8, 0),
    ("Nueva York", "America/New_York",  9, 30),
]


def build_header_subtitle(now_utc: datetime | None = None) -> str:
    """Construye el subtítulo HTML del header: fecha, próxima bolsa en abrir
    y la hora local actual de cada bolsa.

    Ej:
        01 de junio 2026
        🔔 Próxima apertura: NUEVA YORK
        Tokio 23:30 · Londres 14:30 · Nueva York 09:00
    """
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    clocks = []          # (nombre, "HH:MM")
    next_open = None     # (delta, nombre, fecha_local)

    for name, tz, oh, om in EXCHANGES:
        local = now.astimezone(ZoneInfo(tz))
        clocks.append((name, local.strftime("%H:%M")))

        # Siguiente apertura: hoy si aún no pasó, si no el próximo día hábil.
        candidate = local.replace(hour=oh, minute=om, second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=1)
        while candidate.weekday() >= 5:   # 5=sáb, 6=dom
            candidate += timedelta(days=1)

        delta = (candidate - local).total_seconds()
        if next_open is None or delta < next_open[0]:
            next_open = (delta, name, candidate)

    # Fecha en español, tomada de la bolsa que está por abrir (la más relevante).
    ref_date = next_open[2]
    date_line = f"{ref_date.day:02d} de {SPANISH_MONTHS[ref_date.month]} {ref_date.year}"
    session_name = next_open[1].upper()
    clocks_line = " · ".join(f"{name} {hhmm}" for name, hhmm in clocks)

    return (
        f'<span class="header-day">{date_line}</span>'
        f'<span class="header-session">🔔 Próxima apertura: {session_name}</span>'
        f'<span class="header-clocks">{clocks_line}</span>'
    )


# ═══════════════════════════════════════════════════════
# HTML LOCAL
# ═══════════════════════════════════════════════════════

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{report_title} — {date}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        :root {{
            /* Dark graphite scale */
            --bg:           #0b0f14;
            --surface:      #121821;
            --surface-2:    #18212c;
            --border:       #263241;
            --border-soft:  #1b2633;

            /* Text scale */
            --text:         #e6edf3;
            --text-muted:   #9ba8b7;
            --text-dim:     #64748b;

            /* Functional accent and market states */
            --accent:       #38bdf8;
            --bull:         #34d399;
            --bear:         #fb7185;
            --neutral:      #fbbf24;

            --font-sans: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            --font-mono: ui-monospace, "Cascadia Code", "SFMono-Regular", Consolas, monospace;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: var(--font-sans);
            background: var(--bg);
            color: var(--text);
            line-height: 1.65;
            font-size: 15px;
            -webkit-font-smoothing: antialiased;
        }}

        /* ─── HEADER ─── */
        .site-header {{
            background: var(--surface);
            border-bottom: 1px solid var(--border);
            padding: 1.75rem 2rem;
            text-align: center;
        }}
        .header-badge {{
            display: inline-block;
            background: var(--surface-2);
            border: 1px solid var(--border);
            color: var(--text-muted);
            font-size: 0.65rem;
            font-weight: 600;
            letter-spacing: 2.5px;
            text-transform: uppercase;
            padding: 0.2rem 0.7rem;
            border-radius: 4px;
            margin-bottom: 0.75rem;
        }}
        .header-title {{
            font-size: 1.35rem;
            font-weight: 700;
            color: var(--text);
            letter-spacing: -0.01em;
            margin-bottom: 0.4rem;
        }}
        .header-date {{
            color: var(--text-muted);
            font-size: 0.82rem;
        }}
        .header-day {{ display: block; }}
        .header-session {{
            display: block;
            color: var(--text);
            font-weight: 600;
            margin-top: 0.15rem;
        }}
        .header-clocks {{
            display: block;
            color: var(--text-dim);
            font-size: 0.76rem;
            font-family: var(--font-mono);
            margin-top: 0.1rem;
        }}

        /* ─── LAYOUT ─── */
        .main-container {{
            max-width: 1080px;
            margin: 0 auto;
            padding: 2rem;
        }}

        /* ─── DASHBOARD ─── */
        .dashboard-section {{ margin-bottom: 2.5rem; }}
        .dashboard-title {{
            font-size: 0.7rem;
            font-weight: 600;
            letter-spacing: 1.8px;
            text-transform: uppercase;
            color: var(--text-muted);
            margin-bottom: 1rem;
            padding-bottom: 0.5rem;
            border-bottom: 1px solid var(--border);
        }}
        .asset-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(155px, 1fr));
            gap: 0.6rem;
            margin-bottom: 1.25rem;
        }}
        .asset-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 0.85rem 0.95rem;
            transition: border-color 0.15s;
            cursor: default;
        }}
        .asset-card:hover {{
            border-color: var(--text-dim);
        }}
        .asset-name {{
            font-size: 0.95rem;
            font-weight: 700;
            letter-spacing: 0.5px;
            margin-bottom: 0.3rem;
            color: var(--text);
        }}
        .asset-sesgo {{
            font-size: 0.68rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            margin-bottom: 0.5rem;
        }}
        .asset-setup {{
            font-size: 0.65rem;
            color: var(--text-muted);
            background: var(--surface-2);
            padding: 0.1rem 0.45rem;
            border-radius: 3px;
            display: inline-block;
            margin-bottom: 0.6rem;
            text-transform: capitalize;
        }}
        .asset-levels {{ font-size: 0.7rem; }}
        .level-row {{
            display: flex;
            justify-content: space-between;
            padding: 0.12rem 0;
            border-bottom: 1px solid var(--border-soft);
        }}
        .level-row:last-child {{ border-bottom: none; }}
        .level-row span:first-child {{ color: var(--text-dim); }}
        .level-val {{ font-family: var(--font-mono); font-weight: 500; color: var(--text); }}
        .level-val.green {{ color: var(--bull); }}
        .level-val.red {{ color: var(--bear); }}
        .level-val.muted {{ color: var(--text-muted); }}

        .chart-wrapper {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 1.1rem 1.25rem;
        }}
        .chart-label {{
            font-size: 0.68rem;
            font-weight: 600;
            letter-spacing: 1.8px;
            text-transform: uppercase;
            color: var(--text-muted);
            margin-bottom: 0.75rem;
        }}

        /* ─── CONTENT ─── */
        h2 {{
            color: var(--text);
            font-size: 1.05rem;
            font-weight: 700;
            margin: 2.5rem 0 0.85rem;
            padding-bottom: 0.45rem;
            border-bottom: 1px solid var(--border);
            letter-spacing: -0.01em;
        }}
        h3 {{
            color: var(--text);
            font-size: 0.92rem;
            font-weight: 600;
            margin: 1.5rem 0 0.45rem;
            letter-spacing: -0.005em;
        }}
        h4 {{
            color: var(--text-muted);
            font-size: 0.85rem;
            font-weight: 600;
            margin: 0.9rem 0 0.35rem;
        }}
        p {{ margin: 0.55rem 0; color: var(--text); }}
        ul, ol {{ padding-left: 1.4rem; margin: 0.45rem 0; }}
        li {{ margin: 0.25rem 0; }}
        strong {{ color: var(--text); font-weight: 600; }}
        em {{ color: var(--text-muted); }}
        a {{ color: var(--accent); text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        code {{
            background: var(--surface-2);
            padding: 0.1rem 0.35rem;
            border-radius: 3px;
            font-family: var(--font-mono);
            font-size: 0.82rem;
            color: var(--text);
            border: 1px solid var(--border);
        }}

        /* ─── TABLES ─── */
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 1rem 0 1.25rem;
            font-size: 0.82rem;
            border: 1px solid var(--border);
            border-radius: 4px;
            overflow: hidden;
        }}
        thead tr {{
            background: var(--surface-2);
        }}
        th {{
            padding: 0.55rem 0.7rem;
            color: var(--text-muted);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.68rem;
            letter-spacing: 0.8px;
            border-bottom: 1px solid var(--border);
            text-align: center;
        }}
        td {{
            padding: 0.5rem 0.7rem;
            border-bottom: 1px solid var(--border-soft);
            text-align: center;
            color: var(--text);
        }}
        tr:hover td {{ background: rgba(56, 189, 248, 0.05); }}
        tr:last-child td {{ border-bottom: none; }}

        blockquote {{
            border-left: 2px solid var(--accent);
            padding: 0.65rem 1.1rem;
            margin: 1.15rem 0;
            background: var(--surface);
            color: var(--text);
        }}
        hr {{
            border: none;
            border-top: 1px solid var(--border);
            margin: 2rem 0;
        }}

        /* ─── FOOTER ─── */
        .site-footer {{
            text-align: center;
            padding: 1.5rem 2rem;
            color: var(--text-dim);
            font-size: 0.75rem;
            border-top: 1px solid var(--border);
            margin-top: 3rem;
        }}

        @media (max-width: 640px) {{
            .main-container {{ padding: 1rem; }}
            .asset-grid {{ grid-template-columns: repeat(2, 1fr); }}
            .header-title {{ font-size: 1.1rem; }}
        }}
        @media print {{
            :root {{ --accent: #0369a1; }}
            body {{ background: #fff; color: #1a1a1e; }}
            .site-header {{ background: #f5f5f7; border-color: #d2d2d7; }}
            .header-title {{ color: #1a1a1e; }}
            .header-badge {{ background: #fff; border-color: #d2d2d7; color: #6e6e73; }}
            h2, h3, h4 {{ color: #1a1a1e; border-color: #d2d2d7; }}
            th {{ background: #f5f5f7; color: #6e6e73; border-color: #d2d2d7; }}
            td {{ border-color: #e5e5ea; color: #1a1a1e; }}
            blockquote {{ background: #f5f5f7; border-left-color: var(--accent); color: #1a1a1e; }}
            code {{ background: #f5f5f7; color: #1a1a1e; border-color: #d2d2d7; }}
            .dashboard-section, .site-footer {{ display: none; }}
        }}
    </style>
</head>
<body>
    <header class="site-header">
        <div class="header-badge">Pre-Market Brief</div>
        <h1 class="header-title">{report_title}</h1>
        <div class="header-date">{header_subtitle}</div>
    </header>

    <div class="main-container">
        <div class="dashboard-section" id="dashboard"></div>
        <div class="content">{content}</div>
    </div>

    <footer class="site-footer">
        Analizado con {ai_label} · MarketBrief Engine · No constituye asesoría financiera
    </footer>

    <script>
    var BRIEF_DATA = {brief_json};

    /* Paleta dark (debe matchear las CSS vars) */
    var COLOR_BULL = '#34d399';
    var COLOR_BEAR = '#fb7185';
    var COLOR_NEUTRAL = '#fbbf24';

    function fmtPrice(v) {{
        if (!v || v === 0) return '—';
        return Number(v).toLocaleString('en-US', {{minimumFractionDigits: 0, maximumFractionDigits: 2}});
    }}
    function sesgColor(s) {{
        s = (s || '').toLowerCase();
        if (s.indexOf('alcista') !== -1 || s.indexOf('bullish') !== -1) return COLOR_BULL;
        if (s.indexOf('bajista') !== -1 || s.indexOf('bearish') !== -1) return COLOR_BEAR;
        return COLOR_NEUTRAL;
    }}
    function sesgLabel(s) {{
        s = (s || '').toLowerCase();
        if (s.indexOf('alcista') !== -1 || s.indexOf('bullish') !== -1) return 'ALCISTA';
        if (s.indexOf('bajista') !== -1 || s.indexOf('bearish') !== -1) return 'BAJISTA';
        return 'NEUTRAL';
    }}
    function sesgValue(s) {{
        s = (s || '').toLowerCase();
        if (s.indexOf('alcista') !== -1 || s.indexOf('bullish') !== -1) return 75;
        if (s.indexOf('bajista') !== -1 || s.indexOf('bearish') !== -1) return -75;
        return 0;
    }}

    document.addEventListener('DOMContentLoaded', function() {{
        // Colorize sesgo keywords in table cells (solo donde aporta)
        var cells = document.querySelectorAll('td');
        for (var i = 0; i < cells.length; i++) {{
            var t = cells[i].textContent.trim().toLowerCase();
            if (t === 'alcista' || t === 'bullish') {{
                cells[i].style.color = COLOR_BULL; cells[i].style.fontWeight = '600';
            }} else if (t === 'bajista' || t === 'bearish') {{
                cells[i].style.color = COLOR_BEAR; cells[i].style.fontWeight = '600';
            }} else if (t === 'neutral') {{
                cells[i].style.color = COLOR_NEUTRAL; cells[i].style.fontWeight = '600';
            }}
        }}

        if (!BRIEF_DATA) return;
        // v1.4: el schema cambió a {{principales, macro_context}}.
        // Backward compat: si existe `activos` (v1.3), usarlo.
        var assetsObj = BRIEF_DATA.principales || BRIEF_DATA.activos;
        if (!assetsObj) return;

        var entries = Object.entries(assetsObj);
        var dashboard = document.getElementById('dashboard');

        // Build asset cards (sin barra de acento — borde uniforme)
        var cardsHTML = '';
        for (var j = 0; j < entries.length; j++) {{
            var name = entries[j][0];
            var d = entries[j][1];
            var color = sesgColor(d.sesgo);
            var label = sesgLabel(d.sesgo);
            cardsHTML +=
                '<div class="asset-card">' +
                '<div class="asset-name">' + name + '</div>' +
                '<div class="asset-sesgo" style="color:' + color + '">' + label + '</div>' +
                '<div class="asset-setup">' + (d.setup || 'N/A') + '</div>' +
                '<div class="asset-levels">' +
                '<div class="level-row"><span>Precio</span><span class="level-val">' + fmtPrice(d.precio_referencia) + '</span></div>' +
                '<div class="level-row"><span>Soporte</span><span class="level-val green">' + fmtPrice(d.soporte) + '</span></div>' +
                '<div class="level-row"><span>Resist.</span><span class="level-val red">' + fmtPrice(d.resistencia) + '</span></div>' +
                '<div class="level-row"><span>Inval.</span><span class="level-val muted">' + fmtPrice(d.invalidacion) + '</span></div>' +
                '</div></div>';
        }}

        dashboard.innerHTML =
            '<div class="dashboard-title">Resumen de Activos Principales</div>' +
            '<div class="asset-grid">' + cardsHTML + '</div>' +
            '<div class="chart-wrapper">' +
            '<div class="chart-label">Sesgo por Activo</div>' +
            '<canvas id="biasChart" height="160"></canvas>' +
            '</div>';

        if (!window.Chart) return;

        var labels = entries.map(function(e) {{ return e[0]; }});
        var values = entries.map(function(e) {{ return sesgValue(e[1].sesgo); }});
        var colors = entries.map(function(e) {{ return sesgColor(e[1].sesgo); }});
        // Alpha 15% en background (más sutil)
        var bgColors = colors.map(function(c) {{ return c + '26'; }});

        new Chart(document.getElementById('biasChart'), {{
            type: 'bar',
            data: {{
                labels: labels,
                datasets: [{{
                    data: values,
                    backgroundColor: bgColors,
                    borderColor: colors,
                    borderWidth: 1.5,
                    borderRadius: 3
                }}]
            }},
            options: {{
                indexAxis: 'y',
                responsive: true,
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{
                        callbacks: {{
                            label: function(ctx) {{
                                var v = ctx.raw;
                                if (v > 0) return ' Alcista';
                                if (v < 0) return ' Bajista';
                                return ' Neutral';
                            }}
                        }}
                    }}
                }},
                scales: {{
                    x: {{
                        min: -100, max: 100,
                        ticks: {{ color: '#64748b', font: {{ size: 10.5 }} }},
                        grid: {{ color: 'rgba(38,50,65,0.7)' }},
                        border: {{ color: '#263241' }}
                    }},
                    y: {{
                        ticks: {{ color: '#e6edf3', font: {{ weight: '600', size: 11.5 }} }},
                        grid: {{ color: 'rgba(38,50,65,0.4)' }},
                        border: {{ color: '#263241' }}
                    }}
                }}
            }}
        }});
    }});
    </script>
</body>
</html>"""


def _extract_brief_json(brief_text: str) -> str:
    """Extrae el bloque ```json del brief. Retorna 'null' si no encuentra o no es parseable."""
    match = re.search(r"```json\s*(\{.*?\})\s*```", brief_text, re.DOTALL)
    if not match:
        return "null"
    try:
        parsed = json.loads(match.group(1))
        return json.dumps(parsed)
    except Exception:
        return "null"


def normalize_ai_label(ai_provider: str, ai_model: str) -> str:
    """Normaliza una etiqueta legible de proveedor/modelo."""
    provider_map = {
        "gemini": "Gemini",
        "groq": "Groq",
    }
    provider_label = provider_map.get((ai_provider or "").lower(),
                                      ai_provider or "IA")
    if ai_model:
        return f"{provider_label} ({ai_model})"
    return provider_label


def infer_market_bias(brief_text: str) -> str:
    """Intenta extraer el sesgo dominante del brief."""
    fixed_field_match = re.search(
        r"(?im)^\s*\*\*Sesgo del mercado:\*\*\s*(alcista|bajista|neutral)\s*$",
        brief_text,
    )
    if fixed_field_match:
        value = fixed_field_match.group(1).strip().lower()
        return {
            "alcista": "Sesgo Alcista",
            "bajista": "Sesgo Bajista",
            "neutral": "Sesgo Neutral",
        }[value]

    patterns = [
        (r"(?im)\bbias:\s*(long|alcista)\b", "Sesgo Alcista"),
        (r"(?im)\bbias:\s*(short|bajista)\b", "Sesgo Bajista"),
        (r"(?im)\bbias:\s*(neutral)\b", "Sesgo Neutral"),
        (r"(?im)\bsesgo(?: agregado)?(?: del sistema)?\s*:\s*(alcista)\b", "Sesgo Alcista"),
        (r"(?im)\bsesgo(?: agregado)?(?: del sistema)?\s*:\s*(bajista)\b", "Sesgo Bajista"),
        (r"(?im)\bsesgo(?: agregado)?(?: del sistema)?\s*:\s*(neutral)\b", "Sesgo Neutral"),
        (r"(?im)\brisk-on\b", "Sesgo Alcista"),
        (r"(?im)\brisk-off\b", "Sesgo Bajista"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, brief_text):
            return label

    lowered = brief_text.lower()
    bullish_terms = [" alcista", " bullish", " long", " risk-on", " compra", " continuacion alcista"]
    bearish_terms = [" bajista", " bearish", " short", " risk-off", " venta", " continuacion bajista"]
    bullish_score = sum(lowered.count(term) for term in bullish_terms)
    bearish_score = sum(lowered.count(term) for term in bearish_terms)

    if bullish_score >= bearish_score + 2:
        return "Sesgo Alcista"
    if bearish_score >= bullish_score + 2:
        return "Sesgo Bajista"
    return "Sesgo Neutral"


def extract_fixed_field(brief_text: str, field_name: str) -> str | None:
    """Extrae un campo fijo Markdown del tipo **Campo:** valor."""
    pattern = rf"(?im)^\s*\*\*{re.escape(field_name)}:\*\*\s*(.+?)\s*$"
    match = re.search(pattern, brief_text)
    return match.group(1).strip() if match else None


def infer_market_leader(brief_text: str) -> str:
    """Extrae el mercado líder desde campo fijo o heurística básica."""
    fixed_value = extract_fixed_field(brief_text, "Mercado lider")
    if fixed_value:
        return fixed_value

    patterns = [
        (r"(?im)\bmercado lider\s*:\s*(btc)\b", "BTC"),
        (r"(?im)\bmercado lider\s*:\s*(nasdaq)\b", "Nasdaq"),
        (r"(?im)\bmercado lider\s*:\s*(sp500|s&p 500)\b", "SP500"),
        (r"(?im)\bmercado lider\s*:\s*(gold|oro)\b", "Gold"),
        (r"(?im)\bmercado lider\s*:\s*(oil|crude oil|wti)\b", "Oil"),
        (r"(?im)\bmercado lider\s*:\s*(dxy)\b", "DXY"),
        (r"(?im)\bmercado lider\s*:\s*(vix)\b", "VIX"),
        (r"(?im)\bmercado lider\s*:\s*(mixto)\b", "Mixto"),
        (r"(?im)\b(indices tecnologicos|nasdaq).+lider", "Nasdaq"),
        (r"(?im)\bbtc.+lider", "BTC"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, brief_text):
            return label

    return "Mixto"


def extract_original_title(brief_text: str) -> str:
    """Extrae el primer heading del brief si existe."""
    for line in brief_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return "Informe Institucional de Mercado"


def decorate_brief(brief_text: str, ai_label: str,
                   prompt_version: str = "") -> tuple[str, dict]:
    """Inyecta un encabezado corto con sesgo, IA y versión del prompt
    sin perder el título original."""
    bias_title = infer_market_bias(brief_text)
    bias_value = bias_title.replace("Sesgo ", "")
    market_leader = infer_market_leader(brief_text)
    original_title = extract_original_title(brief_text)
    report_title = f"{bias_title} | Analizado con {ai_label}"

    lines = brief_text.splitlines()

    fields_to_remove = {"Informe base", "Sesgo del mercado",
                        "Mercado lider", "IA de analisis",
                        "Versión prompt", "Version prompt"}
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        should_skip = False
        for field_name in fields_to_remove:
            if re.match(rf"(?im)^\s*\*\*{re.escape(field_name)}:\*\*\s*.+$", stripped):
                should_skip = True
                break
        if not should_skip:
            cleaned_lines.append(line)
    lines = cleaned_lines

    title_index = None
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            title_index = i
            break

    if title_index is not None:
        original_heading = lines[title_index]
        heading_prefix = original_heading.split(" ")[0]
        lines[title_index] = f"{heading_prefix} {report_title}"
        insert_at = title_index + 1
        while insert_at < len(lines) and not lines[insert_at].strip():
            insert_at += 1
        header_lines = [
            f"**Informe base:** {original_title}",
            f"**Sesgo del mercado:** {bias_value}",
            f"**Mercado lider:** {market_leader}",
            f"**IA de analisis:** {ai_label}",
        ]
        if prompt_version:
            header_lines.append(f"**Versión prompt:** {prompt_version}")
        header_lines.append("")
        lines[insert_at:insert_at] = header_lines
    else:
        base = [
            f"## {report_title}",
            f"**Informe base:** {original_title}",
            f"**Sesgo del mercado:** {bias_value}",
            f"**Mercado lider:** {market_leader}",
            f"**IA de analisis:** {ai_label}",
        ]
        if prompt_version:
            base.append(f"**Versión prompt:** {prompt_version}")
        base.append("")
        lines = base + lines

    decorated_text = "\n".join(lines)
    metadata = {
        "bias_title": bias_title,
        "bias_value": bias_value,
        "market_leader": market_leader,
        "original_title": original_title,
        "report_title": report_title,
        "ai_label": ai_label,
        "prompt_version": prompt_version,
    }
    return decorated_text, metadata


def markdown_to_html(md_text: str) -> str:
    """Conversión básica de Markdown a HTML sin dependencias extra."""
    html = md_text

    # Escapar HTML existente (excepto emojis)
    html = html.replace("&", "&amp;")
    html = html.replace("<", "&lt;")
    html = html.replace(">", "&gt;")

    # Headers
    html = re.sub(r"^#### (.+)$", r"<h4>\1</h4>", html, flags=re.MULTILINE)
    html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
    html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
    html = re.sub(r"^# (.+)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)

    # Bold y italic
    html = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", html)
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html)

    # Inline code
    html = re.sub(r"`([^`]+)`", r"<code>\1</code>", html)

    # Horizontal rules
    html = re.sub(r"^-{3,}$", "<hr>", html, flags=re.MULTILINE)
    html = re.sub(r"^={3,}$", "<hr>", html, flags=re.MULTILINE)

    # Blockquotes
    html = re.sub(r"^&gt; (.+)$", r"<blockquote>\1</blockquote>",
                  html, flags=re.MULTILINE)

    # Tables (básico)
    lines = html.split("\n")
    in_table = False
    table_lines = []
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if "|" in stripped and stripped.startswith("|"):
            if re.match(r"^\|[\s\-:|]+\|$", stripped):
                continue  # separator row
            if not in_table:
                in_table = True
                table_lines = []
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if not table_lines:
                row = "<tr>" + \
                    "".join(f"<th>{c}</th>" for c in cells) + "</tr>"
            else:
                row = "<tr>" + \
                    "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
            table_lines.append(row)
        else:
            if in_table:
                new_lines.append("<table><thead>" + table_lines[0] + "</thead><tbody>" +
                                 "".join(table_lines[1:]) + "</tbody></table>")
                in_table = False
                table_lines = []
            # Bullet points
            bullet_match = re.match(r"^(\s*)[-•] (.+)$", stripped)
            if bullet_match:
                new_lines.append(f"<li>{bullet_match.group(2)}</li>")
            elif stripped:
                new_lines.append(f"<p>{stripped}</p>")
            else:
                new_lines.append("")

    if in_table:
        new_lines.append("<table><thead>" + table_lines[0] + "</thead><tbody>" +
                         "".join(table_lines[1:]) + "</tbody></table>")

    html = "\n".join(new_lines)

    # Agrupar <li> consecutivos en <ul>
    html = re.sub(r"((?:<li>.+?</li>\n?)+)", r"<ul>\1</ul>", html)

    return html


def save_html(
    brief_text: str,
    output_dir: str,
    report_title: str,
    ai_label: str,
    open_browser: bool = True,
) -> str:
    """
    Guarda el brief como HTML y opcionalmente lo abre en el navegador.

    Returns:
        Ruta del archivo generado
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    header_subtitle = build_header_subtitle()
    # Fecha plana solo para el <title> de la pestaña del navegador.
    now_local = datetime.now()
    tab_date = f"{now_local.day:02d} de {SPANISH_MONTHS[now_local.month]} {now_local.year}"

    filename = f"brief_{date_str}.html"
    filepath = os.path.join(output_dir, filename)

    brief_json = _extract_brief_json(brief_text)
    html_content = markdown_to_html(brief_text)
    full_html = HTML_TEMPLATE.format(
        date=tab_date,
        header_subtitle=header_subtitle,
        content=html_content,
        report_title=report_title,
        ai_label=ai_label,
        brief_json=brief_json,
    )

    os.makedirs(output_dir, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(full_html)

    logger.info(f"✓ HTML guardado: {filepath}")

    if open_browser:
        webbrowser.open(f"file:///{filepath.replace(os.sep, '/')}")
        logger.info("✓ Brief abierto en navegador")

    return filepath


def save_markdown(brief_text: str, output_dir: str) -> str:
    """Guarda el brief como archivo Markdown."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"brief_{date_str}.md"
    filepath = os.path.join(output_dir, filename)

    os.makedirs(output_dir, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(brief_text)

    logger.info(f"✓ Markdown guardado: {filepath}")
    return filepath


# ═══════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════

def send_telegram_html(html_path: str, caption: str, bot_token: str, chat_id: str) -> bool:
    """
    Envía el HTML por Telegram como documento con caption corto.
    """
    if not bot_token or not chat_id:
        logger.warning("⚠ Telegram no configurado (falta BOT_TOKEN o CHAT_ID)")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    safe_caption = caption[:1024]

    try:
        with open(html_path, "rb") as f:
            files = {"document": (os.path.basename(html_path), f, "text/html")}
            data = {
                "chat_id": chat_id,
                "caption": safe_caption,
                "disable_content_type_detection": False,
            }
            resp = requests.post(url, data=data, files=files, timeout=60)

        if resp.status_code == 200:
            logger.info("✓ HTML enviado por Telegram")
            return True

        logger.error(f"✗ Telegram error: {resp.status_code} — {resp.text}")
        return False
    except Exception as e:
        logger.error(f"✗ Error enviando HTML a Telegram: {e}")
        return False


# ═══════════════════════════════════════════════════════
# DISPATCHER
# ═══════════════════════════════════════════════════════

def deliver_brief(brief_text: str, method: str, config: dict) -> dict:
    """
    Entrega el brief según el método configurado.

    Args:
        brief_text: El briefing en Markdown
        method: "html", "telegram", o "both"
        config: Diccionario con las configuraciones necesarias

    Returns:
        Dict con resultados de cada delivery
    """
    results = {}
    ai_label = normalize_ai_label(
        config.get("ai_provider", ""),
        config.get("ai_model", ""),
    )
    final_brief, metadata = decorate_brief(
        brief_text, ai_label,
        prompt_version=config.get("prompt_version", ""),
    )
    results["report_title"] = metadata["report_title"]

    # Siempre guardar el Markdown como respaldo
    md_path = save_markdown(final_brief, config.get("output_dir", "output"))
    results["markdown"] = md_path

    should_generate_html = method in ("html", "telegram", "both")
    open_browser = config.get("open_browser", True) or method in ("telegram", "both")

    html_path = None
    if should_generate_html:
        html_path = save_html(
            final_brief,
            config.get("output_dir", "output"),
            report_title=metadata["report_title"],
            ai_label=ai_label,
            open_browser=open_browser,
        )
        results["html"] = html_path

    if method in ("telegram", "both"):
        caption = metadata["report_title"]
        tg_ok = send_telegram_html(
            html_path,
            caption,
            config.get("telegram_token", ""),
            config.get("telegram_chat_id", ""),
        )
        results["telegram"] = "enviado" if tg_ok else "error"

    return results
