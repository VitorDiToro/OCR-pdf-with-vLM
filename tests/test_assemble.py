"""assemble com páginas sintéticas (sem imagens): métricas montadas à mão."""

from __future__ import annotations

import pytest

from ocrpdf.assemble import assemble
from ocrpdf.ocr.base import LineMetrics, PageLayout, Region
from ocrpdf.render import render_pdf
from test_render import _text_positions


def page(*regions: Region) -> PageLayout:
    # parágrafos empilhados abaixo dos títulos, sem sobreposição
    y = 500.0
    placed = []
    for r in regions:
        if r.category == "text":
            r = Region(r.category, (r.bbox[0], y, r.bbox[2], y + 160), r.text, r.metrics)
            y += 200
        placed.append(r)
    regions = tuple(placed)
    return PageLayout(index=0, width_px=1700, height_px=2200, width_pt=612.0, height_pt=792.0, regions=list(regions))


def _one_line(category: str, bbox: list[float], ascent: float) -> Region:
    m = LineMetrics(lines=1, line_pitch_px=0.0, ascent_px=ascent, first_indent_px=0.0, align="left")
    return Region(category, tuple(bbox), "Texto", m)


def title(bbox: list[float]) -> Region:
    return _one_line("title", bbox, 34.0)


def heading(bbox: list[float]) -> Region:
    return _one_line("section-header", bbox, 27.0)


def para() -> Region:
    m = LineMetrics(lines=5, line_pitch_px=32.0, ascent_px=21.0, first_indent_px=98.0, align="justify")
    return Region("text", (151, 0, 1547, 160), "Um parágrafo de texto corrido.", m)


def test_title_on_recurring_left_position_is_left_aligned():
    doc = assemble([page(title([251, 157, 1395, 209]), heading([251, 334, 524, 375]),
                         para(), para(), para())])
    title_block = doc.blocks[0]
    assert title_block.kind == "title" and title_block.style.align == "left"
    assert title_block.style.indent_em > 1.5


def test_symmetric_title_without_support_stays_centered():
    doc = assemble([page(title([400, 157, 1298, 209]), heading([700, 334, 1000, 375]),
                         para(), para())])
    assert doc.blocks[0].style.align == "center"


def test_two_centered_titles_of_different_widths_stay_centered():
    doc = assemble([page(title([400, 157, 1298, 209]), heading([550, 334, 1148, 375]),
                         para(), para())])
    assert [b.style.align for b in doc.blocks[:2]] == ["center", "center"]


def test_centered_titles_sharing_x_with_only_one_other_block_stay_centered():
    doc = assemble([page(title([400, 157, 1298, 209]), heading([400, 334, 1298, 375]),
                         para(), para())])
    assert [b.style.align for b in doc.blocks[:2]] == ["center", "center"]


def test_repeated_centered_titles_do_not_support_each_other():
    # "I", "II", "III" centralizados, de larguras parecidas: começam quase no mesmo x
    doc = assemble([page(heading([841, 157, 857, 197]), heading([833, 257, 865, 297]),
                         heading([825, 357, 873, 397]), para(), para())])
    assert [b.style.align for b in doc.blocks[:3]] == ["center", "center", "center"]


def test_heading_of_body_size_keeps_paragraph_indent_in_pdf(tmp_path):
    # título do tamanho do corpo (ascent 21 como o parágrafo) no recuo dos parágrafos (3,36 em)
    m = LineMetrics(lines=1, line_pitch_px=0.0, ascent_px=21.0, first_indent_px=0.0, align="left")
    head = Region("section-header", (249, 157, 700, 187), "Seção em negrito", m)
    doc = assemble([page(head, para(), para())], font_family="sans")
    assert doc.blocks[0].style.font_size_pt is not None      # mede sempre o tamanho do título
    out = tmp_path / "out.pdf"
    render_pdf(doc, out, base_dir=tmp_path)
    xs = {text.split()[0]: x for text, x, _ in _text_positions(out)}
    assert xs["Seção"] == pytest.approx(xs["Um"], abs=1.0)
