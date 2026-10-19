import json

from modules.delivery import markdown_to_html, save_html


def test_markdown_escapa_html_crudo():
    out = markdown_to_html('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>')
    assert "<script>" not in out
    assert "<img" not in out
    assert "&lt;script&gt;" in out


def test_markdown_blockquote_y_tabla():
    out = markdown_to_html("> cita con <b>html</b>\n\n| A | B |\n|---|---|\n| 1 | 2 |")
    assert "<blockquote>cita con &lt;b&gt;html&lt;/b&gt;</blockquote>" in out
    assert "<th>A</th>" in out
    assert "<td>1</td>" in out


def test_markdown_promueve_secciones_numeradas_a_h2():
    out = markdown_to_html("### 3. APEX ASSET: BTC (BIAS & EXECUTION)\n\nTexto")
    assert "<h2>3. APEX ASSET: BTC (BIAS &amp; EXECUTION)</h2>" in out
    assert "<h3>3. APEX ASSET" not in out


def test_markdown_deja_h3_no_numerados():
    out = markdown_to_html("### Key Levels\n\nTexto")
    assert "<h3>Key Levels</h3>" in out
    assert "<h2>Key Levels</h2>" not in out


def test_save_html_no_permite_breakout_de_script(tmp_path):
    structured = {"assets": {"BTC": {"nota": "</script><script>alert(1)</script>"}}}
    path = save_html(
        "# Titulo\n\nCuerpo con <script>alert(1)</script>",
        str(tmp_path),
        report_title="<b>titulo</b>",
        ai_label="gemini:test",
        open_browser=False,
        structured=structured,
    )
    html = open(path, encoding="utf-8").read()
    assert "</script><script>alert(1)" not in html
    assert "<\\/script>" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<title>&lt;b&gt;titulo&lt;/b&gt;" in html


def test_save_html_acepta_structured_normal(tmp_path):
    structured = {"assets": {"BTC": {"entry": 100.0, "stop": 98.0}}}
    path = save_html(
        "# Brief\n\nTexto normal",
        str(tmp_path),
        report_title="Sesgo Neutral | Analizado con gemini:test",
        ai_label="gemini:test",
        open_browser=False,
        structured=structured,
    )
    html = open(path, encoding="utf-8").read()
    assert 'var BRIEF_DATA = {"assets": {"BTC": {"entry": 100.0' in html
    assert "Texto normal" in html
