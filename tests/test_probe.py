"""probe.measure num parágrafo sintético: página reta e página torta no scanner."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from ocrpdf import probe
from synth import _FONTS, _paragraph, bbox_of, sample_lines, text_image

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


def _two_paragraphs(angle: float):
    """Os dois parágrafos de 5 linhas do synth (tops 398 e 574), girados `angle` graus em torno do
    meio deles; devolve a imagem, a máscara e o bbox da tinta de cada um, como o dots os daria."""
    font = ImageFont.truetype(_FONTS.format("Regular"), 29)

    def render(*paragraphs):
        img = Image.new("RGB", (1700, 1000), "white")
        for top, seed in paragraphs:
            _paragraph(ImageDraw.Draw(img), font, top, seed=seed, left=_LEFT, right=_RIGHT)
        return img.rotate(angle, resample=Image.Resampling.BICUBIC, center=((_LEFT + _RIGHT) / 2, 563),
                          fillcolor="white")

    boxes = []
    for paragraph in ((398, 0), (574, 7)):
        ys, xs = np.nonzero(probe.ink_mask(render(paragraph)))
        boxes.append((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    image = render((398, 0), (574, 7))
    return image, probe.ink_mask(image), boxes


@pytest.mark.parametrize("angle", [0.0, 1.0, -1.0])
def test_neighbour_line_cut_by_the_bbox_is_not_a_line(angle):
    # o bbox do dots às vezes invade a linha do bloco vizinho; o pipeline informa a faixa sobreposta
    _, mask, ((ax1, ay1, ax2, ay2), (bx1, by1, bx2, by2)) = _two_paragraphs(angle)
    first, second = probe.measure(mask, (ax1, ay1, ax2, ay2)), probe.measure(mask, (bx1, by1, bx2, by2))
    assert (first.lines, first.align, second.lines, second.align) == (5, "justify", 5, "justify")

    up = (bx1, ay2 - 12, bx2, by2)          # o 2º sobe 12 px na última linha do 1º
    assert probe.measure(mask, up).lines == 6                 # controle: sem a faixa, o pedaço conta
    m = probe.measure(mask, up, overlap=(12, 0))
    assert (m.lines, m.align) == (5, "justify")
    assert m.first_indent_px == pytest.approx(second.first_indent_px, abs=2)

    down = (ax1, ay1, ax2, by1 + 20)        # o 1º desce 20 px na 1ª linha do 2º
    assert probe.measure(mask, down).lines == 6
    m = probe.measure(mask, down, overlap=(0, 20))
    assert (m.lines, m.align) == (5, "justify")


def test_line_of_its_own_crossing_the_overlap_is_kept():
    # vizinho com o bbox folgado: a faixa sobreposta pega o topo da 1ª linha DESTE bloco, que continua
    # abaixo dela e não pode ser descartada
    _, mask, (_, (bx1, by1, bx2, by2)) = _two_paragraphs(0.0)
    level = probe.measure(mask, (bx1, by1, bx2, by2))
    m = probe.measure(mask, (bx1, by1, bx2, by2), overlap=(15, 0))
    assert (m.lines, m.first_indent_px) == (level.lines, level.first_indent_px)


def test_short_last_line_without_neighbour_is_kept():
    # última linha só com letras baixas ("anos.") num bbox justo: sem vizinho sobreposto, é uma linha
    lines = sample_lines()[:4] + [[("anos.", False)]]
    image, truth = text_image(lines)
    assert probe.measure(probe.ink_mask(image), bbox_of(truth)).lines == 5


def test_straighten_keeps_a_level_block():
    mask, (x1, y1, x2, y2) = _block(0.0)
    region = mask[y1:y2, x1:x2]
    straight, shift, offset, tan = probe.straighten(region)
    assert abs(tan) < 1e-3 and abs(int(shift[-1])) <= 1 and straight.sum() == region.sum()
