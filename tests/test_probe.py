"""probe.measure num parágrafo sintético: página reta e página torta no scanner."""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw, ImageFont

from ocrpdf import probe
from synth import _FONTS, _paragraph, benjamin_like_page

_LEFT, _RIGHT, _TOP = 151, 1547, 100


def _block(angle: float):
    """Parágrafo justificado de 20 linhas com recuo de 98 px na 1ª, girado `angle` graus (PIL:
    anti-horário) em torno do centro; devolve a máscara e o bbox com folga para as pontas giradas."""
    img = Image.new("RGB", (1700, 900), "white")
    end, _ = _paragraph(ImageDraw.Draw(img), ImageFont.truetype(_FONTS.format("Regular"), 29), _TOP, seed=3,
                        lines=20, left=_LEFT, right=_RIGHT)
    img = img.rotate(angle, resample=Image.Resampling.BICUBIC, center=((_LEFT + _RIGHT) / 2, (_TOP + end) / 2),
                     fillcolor="white")
    pad = 30
    return probe.ink_mask(img), (_LEFT - pad, _TOP - pad, _RIGHT + pad, end + pad)


@pytest.mark.parametrize("angle", [1.0, -1.0, 2.0, -2.0])
def test_skewed_paragraph_keeps_lines_indent_and_alignment(angle):
    level = probe.measure(*_block(0.0))
    assert (level.lines, level.align) == (20, "justify") and level.first_indent_px == pytest.approx(98, abs=4)
    mask, (x1, y1, x2, y2) = _block(angle)
    # controle: sem endireitar, as linhas se encostam na projeção horizontal
    assert len(probe.line_spans(mask[y1:y2, x1:x2])) < 10
    m = probe.measure(mask, (x1, y1, x2, y2))
    assert m.lines == 20
    assert m.line_pitch_px == pytest.approx(level.line_pitch_px, abs=1.5)
    assert m.ascent_px == pytest.approx(level.ascent_px, rel=0.1)
    # a margem de uma página girada também anda na horizontal (~22 px em 20 linhas a 2°)
    assert m.align == "justify"
    assert m.first_indent_px == pytest.approx(level.first_indent_px, abs=4)


@pytest.mark.parametrize("cut", ["top", "bottom"])
def test_bbox_overlapping_a_neighbour_line_ignores_the_cut_piece(cut):
    # o bbox do dots às vezes invade a linha do bloco vizinho; o pedaço cortado na borda não é uma linha
    image, regions = benjamin_like_page()
    (x1, y1, x2, y2), (_, y3, _, y4) = regions[3].bbox, regions[4].bbox   # os dois parágrafos
    mask = probe.ink_mask(image)
    if cut == "top":      # o 2º parágrafo, com o bbox subindo até a última linha do 1º
        level, bbox = probe.measure(mask, (x1, y3, x2, y4)), (x1, y2 - 12, x2, y4)
    else:                 # o 1º parágrafo, com o bbox descendo até a 1ª linha do 2º
        level, bbox = probe.measure(mask, (x1, y1, x2, y2)), (x1, y1, x2, y3 + 20)
    # controle: o pedaço cortado aparece como uma faixa a mais
    region = mask[bbox[1]:bbox[3], bbox[0]:bbox[2]]
    assert len(probe.line_spans(probe.straighten(region)[0])) == 6
    m = probe.measure(mask, bbox)
    assert (m.lines, m.align) == (level.lines, level.align) == (5, "justify")
    assert m.first_indent_px == pytest.approx(level.first_indent_px, abs=2)


def test_straighten_keeps_a_level_block():
    mask, (x1, y1, x2, y2) = _block(0.0)
    region = mask[y1:y2, x1:x2]
    straight, shift, offset, tan = probe.straighten(region)
    assert abs(tan) < 1e-3 and abs(int(shift[-1])) <= 1 and straight.sum() == region.sum()
