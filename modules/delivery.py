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
        f'<span class="header-sep">·</span>'
        f'<span class="header-session">🔔 Próxima apertura: {session_name}</span>'
        f'<span class="header-sep">·</span>'
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
            --surface-3:    #1e2a38;
            --border:       #263241;
            --border-soft:  #1b2633;
            --border-focus: #38bdf8;

            /* Text scale */
            --text:         #e6edf3;
            --text-muted:   #9ba8b7;
            --text-dim:     #64748b;

            /* Functional accent and market states */
            --accent:       #38bdf8;
            --bull:         #34d399;
            --bear:         #fb7185;
            --neutral:      #fbbf24;

            /* Spacing scale */
            --space-xs:     0.25rem;
            --space-sm:     0.5rem;
            --space-md:     1rem;
            --space-lg:     1.5rem;
            --space-xl:     2rem;
            --space-2xl:    3rem;

            --header-height:  auto;

            /* Radius scale */
            --radius-sm:    4px;
            --radius-md:    8px;
            --radius-lg:    12px;

            /* Shadow scale */
            --shadow-sm:    0 1px 2px rgba(0,0,0,0.3);
            --shadow-md:    0 4px 12px rgba(0,0,0,0.35);
            --shadow-lg:    0 8px 24px rgba(0,0,0,0.4);

            --font-sans: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            --font-mono: ui-monospace, "Cascadia Code", "SFMono-Regular", Consolas, monospace;
        }}

        @media (prefers-reduced-motion: reduce) {{
            *, *::before, *::after {{
                animation-duration: 0.01ms !important;
                transition-duration: 0.01ms !important;
            }}
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        html {{
            font-size: clamp(14px, 1.2vw, 16px);
            scroll-behavior: smooth;
        }}

        body {{
            font-family: var(--font-sans);
            background: var(--bg);
            color: var(--text);
            line-height: 1.7;
            -webkit-font-smoothing: antialiased;
            text-rendering: optimizeLegibility;
        }}

        /* ─── LAYOUT ─── */
        .main-container {{
            max-width: 1080px;
            margin: 0 auto;
            width: 100%;
            padding: var(--space-xl) var(--space-lg);
        }}

        /* ─── SECTION CARDS — separación clara entre 1 / 2 / 3 ─── */
        .section-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-left: 4px solid var(--accent);
            border-radius: var(--radius-lg);
            padding: var(--space-xl);
            margin-bottom: 3.5rem;
            box-shadow: var(--shadow-sm);
            transition: border-color 0.15s, box-shadow 0.15s;
            scroll-margin-top: calc(var(--space-xl) + 60px);
        }}
        .section-card:last-child {{ margin-bottom: 0; }}
        .section-card:hover {{
            border-color: var(--border-focus);
            box-shadow: var(--shadow-md);
        }}

        .section-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: var(--space-md);
            margin-bottom: var(--space-lg);
            padding-bottom: var(--space-md);
            border-bottom: 2px solid var(--border-soft);
            flex-wrap: wrap;
        }}

        .section-title {{
            font-size: clamp(1.2rem, 1.8vw, 1.45rem);
            font-weight: 700;
            color: var(--text);
            letter-spacing: -0.015em;
            margin: 0;
        }}

        .section-badge {{
            font-size: 0.65rem;
            font-weight: 700;
            letter-spacing: 1.5px;
            text-transform: uppercase;
            color: var(--accent);
            background: rgba(56, 189, 248, 0.12);
            border: 1px solid rgba(56, 189, 248, 0.25);
            padding: 0.2rem 0.6rem;
            border-radius: var(--radius-sm);
            white-space: nowrap;
        }}

        .section-content {{
            font-size: clamp(0.85rem, 0.95vw, 0.92rem);
            line-height: 1.7;
            text-align: left;
        }}

        /* ─── HEADER ─── */
        .site-header {{
            background: var(--surface);
            border-bottom: 1px solid var(--border);
            padding: var(--space-lg) var(--space-xl);
            text-align: center;
            position: sticky;
            top: 0;
            z-index: 100;
            box-shadow: var(--shadow-sm);
        }}
        .header-badge {{
            display: inline-block;
            background: var(--surface-2);
            border: 1px solid var(--border);
            color: var(--text-muted);
            font-size: 0.6rem;
            font-weight: 600;
            letter-spacing: 2px;
            text-transform: uppercase;
            padding: 0.15rem 0.6rem;
            border-radius: var(--radius-sm);
            margin-bottom: var(--space-xs);
        }}
        .header-title {{
            font-size: clamp(1.1rem, 1.8vw, 1.3rem);
            font-weight: 700;
            color: var(--text);
            letter-spacing: -0.02em;
            margin-bottom: var(--space-xs);
        }}
        .header-date {{
            color: var(--text-muted);
            font-size: clamp(0.72rem, 0.85vw, 0.8rem);
            line-height: 1.5;
        }}
        .header-day {{ display: inline; }}
        .header-session {{
            display: inline;
            color: var(--text);
            font-weight: 600;
        }}
        .header-clocks {{
            display: inline;
            color: var(--text-dim);
            font-size: clamp(0.65rem, 0.8vw, 0.72rem);
            font-family: var(--font-mono);
        }}
        .header-sep {{
            color: var(--text-dim);
            margin: 0 0.35em;
        }}

        /* ─── DASHBOARD ─── */
        .dashboard-section {{ margin-bottom: 3.5rem; }}
        .dashboard-title {{
            font-size: 0.65rem;
            font-weight: 600;
            letter-spacing: 1.5px;
            text-transform: uppercase;
            color: var(--text-muted);
            margin-bottom: var(--space-md);
            padding-bottom: var(--space-sm);
            border-bottom: 1px solid var(--border);
        }}
        .asset-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
            gap: var(--space-sm);
            margin-bottom: var(--space-md);
        }}

        /* ─── ASSET CARDS ─── */
        .asset-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            padding: var(--space-md);
            transition: border-color 0.15s, box-shadow 0.15s;
            cursor: default;
            display: flex;
            flex-direction: column;
            gap: var(--space-sm);
        }}
        .asset-card:hover {{
            border-color: var(--border-focus);
            box-shadow: var(--shadow-sm);
        }}
        .asset-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: var(--space-sm);
        }}
        .asset-name {{
            font-size: 0.95rem;
            font-weight: 700;
            letter-spacing: 0.5px;
            color: var(--text);
        }}
        .asset-sesgo {{
            font-size: 0.62rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            white-space: nowrap;
        }}
        .asset-setup {{
            font-size: 0.6rem;
            font-weight: 600;
            letter-spacing: 0.5px;
            text-transform: uppercase;
            padding: 0.15rem 0.5rem;
            border-radius: var(--radius-sm);
            display: inline-flex;
            align-items: center;
            gap: 0.3rem;
        }}
        .setup-long {{ background: rgba(52, 211, 153, 0.15); color: var(--bull); }}
        .setup-short {{ background: rgba(251, 113, 133, 0.15); color: var(--bear); }}
        .setup-wait {{ background: rgba(251, 191, 36, 0.15); color: var(--neutral); }}
        .asset-levels-toggle {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: var(--space-xs);
            padding: var(--space-xs) 0;
            margin-top: var(--space-xs);
            color: var(--text-muted);
            font-size: 0.7rem;
            cursor: pointer;
            border-top: 1px solid var(--border-soft);
            transition: color 0.15s;
        }}
        .asset-levels-toggle:hover {{ color: var(--text); }}
        .asset-levels-toggle .chevron {{
            flex-shrink: 0;
            transition: transform 0.2s ease;
            stroke: var(--text-dim);
        }}
        .asset-card.expanded .asset-levels-toggle .chevron {{
            transform: rotate(180deg);
        }}
        .asset-levels {{
            display: none;
            flex-direction: column;
            gap: 0;
            padding-top: var(--space-xs);
            border-top: 1px solid var(--border-soft);
            margin-top: var(--space-xs);
        }}
        .asset-card.expanded .asset-levels {{
            display: flex;
            animation: slideDown 0.2s ease;
        }}
        @keyframes slideDown {{
            from {{ opacity: 0; transform: translateY(-4px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        .level-row {{
            display: flex;
            justify-content: space-between;
            padding: var(--space-xs) 0;
            border-bottom: 1px solid var(--border-soft);
            font-size: 0.72rem;
        }}
        .level-row:last-child {{ border-bottom: none; }}
        .level-row span:first-child {{ color: var(--text-dim); }}
        .level-val {{ font-family: var(--font-mono); font-weight: 500; color: var(--text); }}
        .level-bull {{ color: var(--bull); }}
        .level-bear {{ color: var(--bear); }}
        .level-muted {{ color: var(--text-muted); }}

        /* ─── TYPOGRAPHY INSIDE SECTIONS ─── */
        .section-content h2,
        .section-content h3,
        .section-content h4 {{
            margin: var(--space-xl) 0 var(--space-md);
            color: var(--text);
            text-align: left;
        }}

        .section-content .sub-section-title {{
            font-size: clamp(1.05rem, 1.4vw, 1.25rem);
            font-weight: 700;
            color: var(--accent);
            margin: var(--space-2xl) 0 var(--space-md) 0;
            padding-bottom: var(--space-xs);
            border-bottom: 1px solid var(--border);
            text-align: left;
            display: flex;
            align-items: center;
            gap: var(--space-xs);
        }}

        .section-content h2 {{
            font-size: clamp(1.1rem, 1.6vw, 1.35rem);
            font-weight: 700;
            padding-bottom: var(--space-sm);
            border-bottom: 2px solid var(--accent);
            text-align: left;
        }}

        .section-content h3 {{
            font-size: clamp(0.95rem, 1.2vw, 1.08rem);
            font-weight: 600;
            color: var(--text);
            text-align: left;
        }}

        .section-content h4 {{
            font-size: clamp(0.82rem, 0.98vw, 0.9rem);
            font-weight: 600;
            color: var(--accent);
            text-transform: uppercase;
            letter-spacing: 0.8px;
            margin: var(--space-md) 0 var(--space-xs) 0;
            text-align: left;
        }}

        .section-content p {{ margin: var(--space-md) 0; color: var(--text); font-size: clamp(0.85rem, 0.95vw, 0.92rem); line-height: 1.7; text-align: left; }}
        .section-content ul, .section-content ol {{ padding-left: var(--space-xl); margin: var(--space-md) 0; font-size: clamp(0.85rem, 0.95vw, 0.92rem); line-height: 1.7; text-align: left; }}
        .section-content li {{ margin: var(--space-sm) 0; }}
        .section-content strong {{ color: var(--text); font-weight: 600; }}
        .section-content em {{ color: var(--text-muted); }}
        .section-content a {{ color: var(--accent); text-decoration: none; }}
        .section-content a:hover {{ text-decoration: underline; }}
        .section-content code {{
            background: var(--surface-2);
            padding: 0.1rem 0.35rem;
            border-radius: var(--radius-sm);
            font-family: var(--font-mono);
            font-size: 0.8em;
            color: var(--text);
            border: 1px solid var(--border);
        }}

        .section-content blockquote {{
            border-left: 3px solid var(--accent);
            padding: var(--space-md);
            margin: var(--space-lg) 0;
            background: var(--surface-2);
            color: var(--text);
            border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
            text-align: left;
        }}

        .section-content hr {{
            border: none;
            border-top: 1px solid var(--border-soft);
            margin: var(--space-xl) 0;
        }}

        /* ─── TABLES ─── */
        .table-wrapper {{
            width: 100%;
            overflow-x: auto;
            margin: var(--space-lg) 0;
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            background: var(--surface);
            box-shadow: var(--shadow-sm);
        }}
        .section-content table {{
            width: 100%;
            border-collapse: collapse;
            margin: 0;
            font-size: clamp(0.72rem, 0.88vw, 0.8rem);
            background: var(--surface);
        }}
        .section-content thead {{
            position: sticky;
            top: 0;
            z-index: 2;
        }}
        .section-content thead tr {{
            background: linear-gradient(135deg, var(--surface-2), var(--surface-3));
            border-bottom: 2px solid var(--accent);
        }}
        .section-content th {{
            padding: var(--space-md) var(--space-md);
            color: var(--text);
            font-weight: 700;
            text-transform: uppercase;
            font-size: 0.65rem;
            letter-spacing: 1px;
            border-bottom: 2px solid var(--accent);
            text-align: center;
            background: var(--surface-2);
            white-space: nowrap;
        }}
        .section-content td {{
            padding: var(--space-md) var(--space-md);
            border-bottom: 1px solid var(--border-soft);
            text-align: center;
            color: var(--text);
            font-weight: 500;
            white-space: nowrap;
        }}
        .section-content td:first-child {{
            text-align: left;
            font-weight: 700;
            color: var(--accent);
        }}
        .section-content tbody tr {{
            border-bottom: 1px solid var(--border-soft);
        }}
        .section-content tbody tr:nth-child(even) {{
            background: rgba(56, 189, 248, 0.03);
        }}
        .section-content tbody tr:hover {{
            background: rgba(56, 189, 248, 0.08);
            transition: background 0.15s ease;
        }}
        .section-content tr:last-child td {{
            border-bottom: none;
        }}

        /* ─── CHART WRAPPER ─── */
        .chart-wrapper {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            padding: var(--space-md) var(--space-lg);
        }}
        .chart-label {{
            font-size: 0.62rem;
            font-weight: 600;
            letter-spacing: 1.5px;
            text-transform: uppercase;
            color: var(--text-muted);
            margin-bottom: var(--space-md);
        }}

        /* ─── FOOTER ─── */
        .site-footer {{
            text-align: center;
            padding: var(--space-lg) var(--space-lg);
            color: var(--text-dim);
            font-size: clamp(0.7rem, 0.85vw, 0.78rem);
            border-top: 1px solid var(--border);
            margin-top: var(--space-2xl);
        }}

        /* ─── PAGE LAYOUT (sin sidebar) ─── */
        .page-wrapper {{
            min-height: 100vh;
        }}

        .main-content-wrapper {{
            min-width: 0;
        }}

        /* Scroll progress indicator in header */
        .scroll-progress {{
            position: absolute;
            bottom: 0;
            left: 0;
            height: 2px;
            background: linear-gradient(90deg, var(--bull), var(--accent));
            border-radius: 0 0 var(--radius-sm) var(--radius-sm);
            transform-origin: left;
            transform: scaleX(0);
            transition: transform 0.1s linear;
        }}

        /* ─── RESPONSIVE ─── */
        @media (max-width: 768px) {{
            .main-container {{ padding: var(--space-lg) var(--space-md); }}
            .asset-grid {{ grid-template-columns: repeat(2, 1fr); }}
            .section-card {{ padding: var(--space-md); }}
            .site-header {{ padding: var(--space-md) var(--space-md); }}
            .header-title {{ font-size: clamp(1rem, 4vw, 1.15rem); }}
        }}

        @media (max-width: 480px) {{
            .asset-grid {{ grid-template-columns: 1fr; }}
            .section-header {{ flex-direction: column; align-items: flex-start; }}
            .header-date {{ font-size: 0.7rem; }}
            .header-clocks {{ font-size: 0.6rem; }}
        }}

        @media print {{
            :root {{
                --accent: #0369a1;
                --bg: #fff;
                --surface: #fff;
                --surface-2: #f5f5f7;
                --surface-3: #eee;
                --text: #1a1a1e;
                --text-muted: #6e6e73;
                --text-dim: #8e8e93;
                --border: #d2d2d7;
                --border-soft: #e5e5ea;
                --bull: #0a8f3f;
                --bear: #c71e1e;
                --neutral: #b87a00;
            }}
            .page-wrapper {{ display: block; }}
            .site-footer, .dashboard-section, .chart-wrapper,
            .scroll-progress {{ display: none !important; }}
            .main-content-wrapper {{ max-width: none; }}
            .main-container {{ max-width: none; padding: 0; }}
            .site-header {{
                background: #fff; border-color: #d2d2d7; box-shadow: none;
                position: static; padding: var(--space-lg) var(--space-lg);
                border-bottom: 2px solid #1a1a1e;
                margin-bottom: var(--space-lg);
            }}
            .header-title {{ color: #1a1a1e; font-size: 1.3rem; }}
            .header-badge {{ background: #1a1a1e; border-color: #1a1a1e; color: #fff; }}
            .header-date {{ color: #6e6e73; }}
            .section-card {{
                background: #fff; border: 1px solid #d2d2d7; border-left: 3px solid var(--accent);
                box-shadow: none; border-radius: 0; margin-bottom: var(--space-lg);
                page-break-inside: avoid;
            }}
            .section-card:hover {{ border-color: #d2d2d7; box-shadow: none; }}
            .section-header {{ border-bottom-color: #d2d2d7; }}
            .section-title {{ color: #1a1a1e; }}
            .section-badge {{ background: #1a1a1e; border-color: #1a1a1e; color: #fff; }}
            .section-content h2, .section-content h3, .section-content h4 {{ color: #1a1a1e; border-color: #d2d2d7; }}
            .section-content h2 {{ border-bottom: 2px solid var(--accent); }}
            .section-content th {{ background: #f5f5f7; color: #6e6e73; border-color: #d2d2d7; }}
            .section-content td {{ border-color: #e5e5ea; color: #1a1a1e; }}
            .section-content tbody tr:nth-child(even) {{ background: #f9f9fb; }}
            .section-content blockquote {{ background: #f5f5f7; border-left-color: var(--accent); color: #1a1a1e; }}
            .section-content code {{ background: #f5f5f7; color: #1a1a1e; border-color: #d2d2d7; }}
            .asset-card {{ background: #fff; border-color: #d2d2d7; }}
            .asset-setup {{ background: #f5f5f7; }}
            .asset-levels {{ border-top-color: #d2d2d7; }}
            .level-row {{ border-bottom-color: #e5e5ea; }}
            .level-bull {{ color: #0a8f3f !important; }}
            .level-bear {{ color: #c71e1e !important; }}
            .level-muted {{ color: #8e8e93 !important; }}
            .setup-long {{ background: #e8f8f0 !important; color: #0a8f3f !important; }}
            .setup-short {{ background: #fdeaea !important; color: #c71e1e !important; }}
            .setup-wait {{ background: #fef9e7 !important; color: #b87a00 !important; }}
            @page {{
                margin: 2cm 1.5cm;
                @top-center {{
                    content: "MarketBrief — Pre-Market Intelligence";
                    font-size: 0.75rem; color: #999;
                }}
                @bottom-center {{
                    content: counter(page) " / " counter(pages);
                    font-size: 0.75rem; color: #999;
                }}
                @bottom-right {{
                    content: "Generado: " attr(data-date);
                    font-size: 0.7rem; color: #999;
                }}
            }}
            .section-card {{ break-inside: avoid; }}
            h2, h3, h4 {{ break-after: avoid; }}
            table {{ break-inside: avoid; }}
        }}
    </style>
</head>
<body>
    <div class="page-wrapper" data-date="{date}">
        <!-- Main Content -->
        <main class="main-content-wrapper">
            <header class="site-header">
                <div class="header-badge">Pre-Market Brief</div>
                <h1 class="header-title">{report_title}</h1>
                <div class="header-date">{header_subtitle}</div>
                <div class="scroll-progress" id="scrollProgress"></div>
            </header>

            <div class="main-container">
                <div class="dashboard-section" id="dashboard"></div>
                <div class="content" id="main-content">{content}</div>
            </div>

            <footer class="site-footer">
                Analizado con {ai_label} · MarketBrief Engine · No constituye asesoría financiera
            </footer>
        </main>
    </div>

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
        // Dividir secciones en cards SIEMPRE (aunque BRIEF_DATA falle) — garantiza separación 1-2-3
        try {{ wrapSectionsInCards(); }} catch(e) {{ console.error('wrapSectionsInCards', e); }}
        try {{ initScrollProgress(); }} catch(e) {{}}

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

        // Build asset cards - interactive with expandible levels
        var cardsHTML = '';
        for (var j = 0; j < entries.length; j++) {{
            var name = entries[j][0];
            var d = entries[j][1];
            var color = sesgColor(d.sesgo);
            var label = sesgLabel(d.sesgo);
            var setup = (d.setup || 'WAIT').toUpperCase();
            var setupClass = setup === 'LONG' ? 'setup-long' : (setup === 'SHORT' ? 'setup-short' : 'setup-wait');

            // Build levels HTML
            var levelsHTML = '';
            if (d.precio_referencia) levelsHTML += '<div class="level-row"><span>Precio</span><span class="level-val">' + fmtPrice(d.precio_referencia) + '</span></div>';
            if (d.soporte) levelsHTML += '<div class="level-row"><span>Soporte</span><span class="level-val level-bull">' + fmtPrice(d.soporte) + '</span></div>';
            if (d.resistencia) levelsHTML += '<div class="level-row"><span>Resistencia</span><span class="level-val level-bear">' + fmtPrice(d.resistencia) + '</span></div>';
            if (d.invalidacion) levelsHTML += '<div class="level-row"><span>Invalidación</span><span class="level-val level-muted">' + fmtPrice(d.invalidacion) + '</span></div>';
            if (d.stop) levelsHTML += '<div class="level-row"><span>SL</span><span class="level-val level-bear">' + fmtPrice(d.stop) + '</span></div>';
            if (d.tp1) levelsHTML += '<div class="level-row"><span>TP1</span><span class="level-val level-bull">' + fmtPrice(d.tp1) + '</span></div>';
            if (d.tp2) levelsHTML += '<div class="level-row"><span>TP2</span><span class="level-val level-bull">' + fmtPrice(d.tp2) + '</span></div>';
            if (d.rr) levelsHTML += '<div class="level-row"><span>R:R</span><span class="level-val">' + d.rr + '</span></div>';
            if (d.probability) levelsHTML += '<div class="level-row"><span>Prob.</span><span class="level-val">' + d.probability + '%</span></div>';

            cardsHTML +=
                '<div class="asset-card" data-asset="' + name + '">' +
                '<div class="asset-header">' +
                '<div class="asset-name">' + name + '</div>' +
                '<div class="asset-sesgo" style="color:' + color + '">' + label + '</div>' +
                '</div>' +
                '<div class="asset-setup ' + setupClass + '">' + setup + '</div>' +
                '<div class="asset-levels-toggle" onclick="toggleAssetLevels(this)">' +
                '<span>Ver niveles</span>' +
                '<svg class="chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>' +
                '</div>' +
                '<div class="asset-levels">' + levelsHTML + '</div>' +
                '</div>';
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

    // Section card wrapper - finds major H2s and wraps them + following content into section-cards
    function wrapSectionsInCards() {{
        var content = document.getElementById('main-content');
        if (!content) return;

        var allHeadings = Array.from(content.querySelectorAll('h2'));
        if (allHeadings.length === 0) return;

        // Demote sub-H2s to H3s so they stay inside their parent section card
        allHeadings.forEach(function(h2, idx) {{
            var text = h2.textContent.trim();
            var isMain = idx === 0 || /^\\d+[.)]/.test(text) || /^Sesgo/i.test(text) || /^Informe/i.test(text);
            if (!isMain) {{
                var h3 = document.createElement('h3');
                h3.className = 'sub-section-title';
                h3.innerHTML = h2.innerHTML;
                if (h2.id) h3.id = h2.id;
                h2.parentNode.replaceChild(h3, h2);
            }}
        }});

        // Now wrap remaining main H2s into section cards
        var mainHeadings = Array.from(content.querySelectorAll('h2'));
        mainHeadings.forEach(function(h2) {{
            var card = document.createElement('div');
            card.className = 'section-card';

            var header = document.createElement('div');
            header.className = 'section-header';

            var title = document.createElement('h2');
            title.className = 'section-title';

            var text = h2.textContent.trim();
            var badgeText = 'SECCIÓN';
            var match = text.match(/^(\\d+)[.)]\\s*(.+)/);
            if (match) {{
                badgeText = 'SECCIÓN ' + match[1];
                title.textContent = match[2];
            }} else {{
                title.textContent = text;
            }}

            title.id = h2.id || text.toLowerCase().replace(/[^a-z0-9]+/g, '-');

            var badge = document.createElement('span');
            badge.className = 'section-badge';
            badge.textContent = badgeText;

            header.appendChild(title);
            header.appendChild(badge);
            card.appendChild(header);

            var sectionContent = document.createElement('div');
            sectionContent.className = 'section-content';

            var node = h2.nextSibling;
            while (node && !(node.nodeType === 1 && node.tagName === 'H2')) {{
                var next = node.nextSibling;
                sectionContent.appendChild(node);
                node = next;
            }}

            card.appendChild(sectionContent);
            h2.parentNode.insertBefore(card, h2);
            h2.remove();
        }});
    }}

    // Toggle asset levels expand/collapse
    function toggleAssetLevels(btn) {{
        var card = btn.closest('.asset-card');
        if (card) {{
            card.classList.toggle('expanded');
        }}
    }}

    // Scroll progress indicator
    function initScrollProgress() {{
        var progressBar = document.getElementById('scrollProgress');
        if (!progressBar) return;

        window.addEventListener('scroll', function() {{
            var scrollTop = window.scrollY;
            var docHeight = document.documentElement.scrollHeight - window.innerHeight;
            var progress = docHeight > 0 ? scrollTop / docHeight : 0;
            progressBar.style.transform = 'scaleX(' + Math.min(1, Math.max(0, progress)) + ')';
        }}, {{ passive: true }});
    }}

    // Keyboard shortcuts
    function initKeyboardShortcuts() {{
        var cards = Array.from(document.querySelectorAll('.section-card'));
        var currentIndex = -1;

        document.addEventListener('keydown', function(e) {{
            // Ignore if typing in input
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

            switch (e.key.toLowerCase()) {{
                case 'n': // Next section
                    e.preventDefault();
                    if (currentIndex < cards.length - 1) {{
                        currentIndex++;
                        cards[currentIndex].scrollIntoView({{ behavior: 'smooth', block: 'start' }});
                    }}
                    break;
                case 'p': // Previous section
                    e.preventDefault();
                    if (currentIndex > 0) {{
                        currentIndex--;
                        cards[currentIndex].scrollIntoView({{ behavior: 'smooth', block: 'start' }});
                    }}
                    break;
                case 't': // Top
                    e.preventDefault();
                    window.scrollTo({{ top: 0, behavior: 'smooth' }});
                    currentIndex = -1;
                    break;
                case 'c': // Collapse/expand all asset cards
                    e.preventDefault();
                    var assetCards = document.querySelectorAll('.asset-card');
                    var anyExpanded = Array.from(assetCards).some(function(c) {{ return c.classList.contains('expanded'); }});
                    assetCards.forEach(function(card) {{
                        card.classList.toggle('expanded', !anyExpanded);
                    }});
                    break;
            }}
        }});
    }}

    // Initialize all on DOMContentLoaded (sin sidebar)
    document.addEventListener('DOMContentLoaded', function() {{
        try {{ initScrollProgress(); }} catch(e) {{}}
        try {{ initKeyboardShortcuts(); }} catch(e) {{}}
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
    html = html.replace("&", "&")
    html = html.replace("<", "<")
    html = html.replace(">", ">")

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
    html = re.sub(r"^> (.+)$", r"<blockquote>\1</blockquote>",
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
                new_lines.append('<div class="table-wrapper"><table><thead>' + table_lines[0] + "</thead><tbody>" +
                                 "".join(table_lines[1:]) + "</tbody></table></div>")
                in_table = False
                table_lines = []
            # Bullet points
            bullet_match = re.match(r"^(\s*)[-•] (.+)$", stripped)
            if bullet_match:
                new_lines.append(f"<li>{bullet_match.group(2)}</li>")
            elif stripped:
                # No envolver headers, blockquotes, tables, hr en <p>
                if not (stripped.startswith("<h") or
                        stripped.startswith("<blockquote") or
                        stripped.startswith("<table") or
                        stripped.startswith("<hr") or
                        stripped.startswith("<ul") or
                        stripped.startswith("<ol")):
                    new_lines.append(f"<p>{stripped}</p>")
                else:
                    new_lines.append(stripped)
            else:
                new_lines.append("")

    if in_table:
        new_lines.append('<div class="table-wrapper"><table><thead>' + table_lines[0] + "</thead><tbody>" +
                         "".join(table_lines[1:]) + "</tbody></table></div>")

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
