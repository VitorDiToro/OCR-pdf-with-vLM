"""assemble com páginas sintéticas (sem imagens): métricas montadas à mão."""

from __future__ import annotations

from ocrpdf.assemble import assemble
from ocrpdf.ocr.base import LineMetrics, PageLayout, Region


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
