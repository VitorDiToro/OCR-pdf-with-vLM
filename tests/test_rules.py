import json

import numpy as np
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw, ImageFont

from ocrpdf import probe
from ocrpdf.model import Block, BlockStyle, Document, PageGeometry, Rule, Span
from ocrpdf.assemble import assemble
from ocrpdf.ocr.base import LineMetrics, PageLayout, Region, RuleMetrics
from ocrpdf.pipeline import Pipeline
from ocrpdf.render import render_pdf
from synth import FakeEngine, benjamin_like_page, make_pdf
from test_render import _text_positions


def test_find_rules_below_title():
    image, regions = benjamin_like_page()
    rules = probe.find_rules(probe.ink_mask(image), regions[0].bbox)
    assert len(rules) == 1 and rules[0].position == "below"
    assert 1 <= rules[0].thickness_px <= 3 and rules[0].gap_px <= 6
    assert abs(rules[0].x1_px - 254) <= 3 and abs(rules[0].x2_px - 1545) <= 3


def test_text_line_below_title_is_not_a_rule():
    image, regions = benjamin_like_page()
    assert probe.find_rules(probe.ink_mask(image), regions[1].bbox) == []   # subtítulo


_LIBERATION = "/usr/share/fonts/truetype/liberation/Liberation{}.ttf"


def _blank_page(height: int = 600) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (1700, height), "white")      # largura de uma página Letter a 200 dpi
    return img, ImageDraw.Draw(img)


@pytest.mark.parametrize("weight", ["Regular", "Bold"])
@pytest.mark.parametrize("size", [34, 40])
@pytest.mark.parametrize("text", ["I", "II", "III", "IV"])
def test_serifs_of_short_title_are_not_rules(text, size, weight):
    img, d = _blank_page()
    font = ImageFont.truetype(_LIBERATION.format(f"Serif-{weight}"), size)
    d.text((820, 300), text, font=font, fill=0)
    assert probe.find_rules(probe.ink_mask(img), d.textbbox((820, 300), text, font=font)) == []


def test_stroke_in_another_column_is_not_a_rule():
    img, d = _blank_page()
    font = ImageFont.truetype(_LIBERATION.format("Sans-Bold"), 47)
    d.text((151, 157), "Título da seção", font=font, fill=0)
    x1, y1, x2, y2 = d.textbbox((151, 157), "Título da seção", font=font)
    assert x2 < 700
    d.rectangle((880, y2 + 3, 1548, y2 + 4), fill=0)     # traço na outra coluna, à altura do título
    assert probe.find_rules(probe.ink_mask(img), (x1, y1, x2, y2)) == []


def test_text_line_inside_search_band_is_not_a_rule():
    img, d = _blank_page()
    bold = ImageFont.truetype(_LIBERATION.format("Sans-Bold"), 47)
    reg = ImageFont.truetype(_LIBERATION.format("Sans-Regular"), 29)
    title = "A obra de arte na era de sua reprodutibilidade técnica"
    d.text((251, 157), title, font=bold, fill=0)
    bbox = d.textbbox((251, 157), title, font=bold)
    sub = "(Primeira versão, tradução de teste) — com um subtítulo comprido"
    d.text((251, bbox[3] + 12), sub, font=reg, fill=0)        # 12 px abaixo: dentro da faixa de busca
    assert probe.find_rules(probe.ink_mask(img), bbox) == []


def _longest_dark_run(pdf, page: int, y_range_pt: tuple[float, float]) -> tuple[float, float]:
    """Maior trecho escuro contínuo de uma linha de pixels: (início, fim) em pt."""
    doc = pdfium.PdfDocument(str(pdf))
    try:
        gray = np.asarray(doc[page].render(scale=2).to_pil().convert("L"))
    finally:
        doc.close()
    best = (0, 0)
    for y in range(int(y_range_pt[0] * 2), int(y_range_pt[1] * 2)):
        run = 0
        for x, dark in enumerate(gray[y] < 128):
            run = run + 1 if dark else 0
            if run > best[1] - best[0]:
                best = (x + 1 - run, x + 1)
    return best[0] / 2, best[1] / 2


def test_rule_reaches_pdf(tmp_path):
    image, regions = benjamin_like_page()
    doc = Pipeline(FakeEngine([regions]), tmp_path / "w", workers=1).analyze(
        make_pdf(tmp_path / "in.pdf", [image]), font="sans")
    rule = doc.blocks[0].style.rule_below
    assert rule is not None and rule.right_frac <= 0.01     # termina na margem direita
    out = tmp_path / "out.pdf"
    render_pdf(doc, out, base_dir=tmp_path / "w")
    # linha horizontal escura abaixo do título, ocupando ≥ 75% da área de texto
    # (a área de texto tem ~ 0,82 da largura da página)
    x1, x2 = _longest_dark_run(out, page=0, y_range_pt=(70, 110))
    assert (x2 - x1) / doc.page.width_pt >= 0.75 * 0.82
    # começa onde começava no original: 3 px (≈ 1 pt) à direita do início do título
    title_x = next(x for text, x, _ in _text_positions(out) if text.startswith("A obra"))
    assert x1 - title_x == pytest.approx(3 * 612 / 1700, abs=1.5)


def _ruled_title(rules: bool, indent_em: float, right_frac: float, text: str) -> Document:
    doc = Document(page=PageGeometry(612, 792, 72, 72, 72, 72), body_font_pt=10, font_family="sans")
    style = BlockStyle(align="left", indent_em=indent_em, font_size_pt=17)
    if rules:
        style.rule_above = Rule(thickness_pt=0.75, gap_pt=2, left_em=1.0, right_frac=right_frac)
        style.rule_below = Rule(thickness_pt=0.75, gap_pt=2, left_em=1.0, right_frac=right_frac)
    doc.blocks.append(Block(kind="title", level=1, spans=[Span(text)], style=style))
    return doc


def test_underlined_title_stays_on_one_line(tmp_path):
    # filete mais curto que o título (termina a 60% da área da direita): não estreita o título
    doc = _ruled_title(True, 0.0, 0.6, "Um título sublinhado bem comprido na página")
    out = tmp_path / "out.pdf"
    render_pdf(doc, out, base_dir=tmp_path)
    assert len({round(y, 1) for _, _, y in _text_positions(out)}) == 1


def test_rules_do_not_change_title_indent(tmp_path):
    xs = []
    for rules in (False, True):
        out = tmp_path / f"out_{rules}.pdf"
        render_pdf(_ruled_title(rules, 3.0, 0.2, "Título com filetes"), out, base_dir=tmp_path)
        xs.append(_text_positions(out)[0][1])
    assert xs[1] == pytest.approx(xs[0], abs=0.5)


def test_old_files_without_rules_load(tmp_path):
    image, regions = benjamin_like_page()
    doc = Pipeline(FakeEngine([regions]), tmp_path / "w", workers=1).analyze(
        make_pdf(tmp_path / "in.pdf", [image]), font="sans")
    # page cache without "rules"
    page = tmp_path / "w" / "pages" / "page_0001.json"
    d = json.loads(page.read_text())
    for r in d["regions"]:
        r.pop("rules")
    page.write_text(json.dumps(d))
    assert all(r.rules == [] for r in PageLayout.load(page).regions)
    # document.json without rule_*
    path = tmp_path / "doc.json"
    doc.save(path)
    d = json.loads(path.read_text())
    for b in d["blocks"]:
        b["style"].pop("rule_above"), b["style"].pop("rule_below")
    path.write_text(json.dumps(d))
    loaded = Document.load(path)
    assert loaded.blocks[0].style.rule_below is None


def test_rule_between_two_titles_goes_to_one_of_them(tmp_path):
    # título / filete / subtítulo: o filete fica abaixo de um e acima do outro, mas é um só
    img, d = _blank_page()
    font = ImageFont.truetype(_LIBERATION.format("Sans-Bold"), 40)
    d.text((151, 100), "Primeira Parte", font=font, fill=0)
    a = d.textbbox((151, 100), "Primeira Parte", font=font)
    d.rectangle((151, a[3] + 10, 1500, a[3] + 11), fill=0)
    d.text((151, a[3] + 14), "Capitulo Um", font=font, fill=0)
    b = d.textbbox((151, a[3] + 14), "Capitulo Um", font=font)
    mask = probe.ink_mask(img)
    # controle: cada título, sozinho, acha o mesmo filete
    assert [r.position for r in probe.find_rules(mask, a)] == ["below"]
    assert [r.position for r in probe.find_rules(mask, b)] == ["above"]
    regions = [Region("section-header", a, "Primeira Parte"), Region("section-header", b, "Capitulo Um")]
    pdf = make_pdf(tmp_path / "in.pdf", [img])
    Pipeline(FakeEngine([regions]), tmp_path / "w", workers=1).analyze(pdf, font="sans")
    found = [rm for r in PageLayout.load(tmp_path / "w" / "pages" / "page_0001.json").regions for rm in r.rules]
    assert len(found) == 1


def test_rule_of_a_title_inside_a_column_is_not_drawn():
    # TODO: geometria do filete por coluna. Hoje left_em/right_frac são da área de texto da página e
    # um título de coluna é desenhado dentro de div.cols: o filete sairia deslocado. Só o de largura total.
    body = LineMetrics(lines=8, line_pitch_px=32.0, ascent_px=21.0, first_indent_px=0.0, align="justify")
    one = LineMetrics(lines=1, line_pitch_px=0.0, ascent_px=27.0, first_indent_px=0.0, align="left")
    regions = [Region("title", (151, 150, 1547, 200), "Título", one,
                      rules=[RuleMetrics("below", 151, 1547, 206, 2, 4)]),
               Region("section-header", (880, 260, 1300, 300), "Seção", one,
                      rules=[RuleMetrics("below", 880, 1547, 306, 2, 4)])]
    for col in (151, 880):                 # duas colunas de texto
        regions += [Region("text", (col, y, col + 667, y + 260), "texto", body) for y in (330, 620, 910, 1200)]
    doc = assemble([PageLayout(0, 1700, 2200, 612.0, 792.0, regions)])
    assert doc.columns == 2
    title, header = doc.blocks[0], next(b for b in doc.blocks if b.kind == "heading")
    assert title.style.full_width and title.style.rule_below is not None
    assert not header.style.full_width and header.style.rule_below is None
