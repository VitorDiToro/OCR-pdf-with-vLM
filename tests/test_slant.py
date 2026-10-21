"""Caixas de palavra e inclinação em texto sintético (Liberation a 29 px ≈ 10 pt a 200 dpi)."""

import pytest

from ocrpdf.italic.slant import SUSPECT_DEG, baseline_deg, slant_deg, word_boxes
from ocrpdf.probe import ink_mask
from synth import bbox_of, sample_lines, text_image


def _ink_bbox(mask):
    """Extensão da tinta: no texto girado, a tinta sai do bbox das caixas de antes da rotação."""
    ys, xs = mask.nonzero()
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def _overlap(a, b) -> int:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0, w) * max(0, h)


def measure_all(image, truth, lines):
    """[(itálico?, ângulo)] de cada caixa achada, com o itálico da palavra real que ela mais cobre;
    caixas que não cobrem palavra nenhuma ficam de fora."""
    mask = ink_mask(image)
    boxes = word_boxes(mask, _ink_bbox(mask))
    base = {i: baseline_deg([b for b in boxes if b.line == i]) for i in {b.line for b in boxes}}
    words = [(it, box) for line, row in zip(lines, truth) for (_, it), box in zip(line, row)]
    out = []
    for b in boxes:
        cover = [_overlap((b.x1, b.y1, b.x2, b.y2), box) for _, box in words]
        k = max(range(len(words)), key=cover.__getitem__)
        if cover[k]:
            out.append((words[k][0], slant_deg(mask, b, base[b.line])))
    return out


def share(values, pred) -> float:
    return sum(map(pred, values)) / len(values) if values else 0.0


@pytest.mark.parametrize("italic_every", [0, 2])
@pytest.mark.parametrize("serif", [False, True])
def test_word_boxes_match_rendered_words(serif, italic_every):
    image, truth = text_image(sample_lines(italic_every), serif=serif)
    boxes = word_boxes(ink_mask(image), bbox_of(truth))
    assert [sum(b.line == i for b in boxes) for i in range(len(truth))] == [len(l) for l in truth]
    # mesma extensão horizontal da tinta (a binarização come no máximo 1 px da borda)
    got = [x for b in boxes for x in (b.x1, b.x2)]
    assert got == pytest.approx([x for row in truth for t in row for x in (t[0], t[2])], abs=1)


@pytest.mark.parametrize("serif", [False, True])
def test_attached_punctuation_stays_with_the_word(serif):
    # pontuação e aspas ocupam poucas linhas: pela distância linha a linha, ficariam soltas
    words = ["“prestasse”", "série;", "visto.", "(Rio", "“Em", "ouro”,", "1936).", "Versão¹"]
    lines = [[(w, k % 2 == 1) for k, w in enumerate(words)]]
    image, truth = text_image(lines, serif=serif)
    boxes = word_boxes(ink_mask(image), bbox_of(truth))
    got = [x for b in boxes for x in (b.x1, b.x2)]
    assert got == pytest.approx([x for t in truth[0] for x in (t[0], t[2])], abs=1)


@pytest.mark.parametrize("serif,min_deg", [(False, 8.0), (True, 7.0)])
def test_slant_separates_italic(serif, min_deg):
    lines = sample_lines(italic_every=2)
    measured = measure_all(*text_image(lines, serif=serif), lines)
    regular = [a for it, a in measured if not it and a is not None]
    italic = [a for it, a in measured if it and a is not None]
    assert min(len(regular), len(italic)) >= 15   # a maioria das 24 + 24 palavras é medida
    assert share(regular, lambda a: abs(a) < 3.0) >= 0.95
    assert share(italic, lambda a: a >= min_deg) >= 0.90


@pytest.mark.parametrize("angle", [1.5, -1.5])
def test_skewed_page_is_not_italic(angle):
    lines = sample_lines(italic_every=0)
    measured = measure_all(*text_image(lines, angle_deg=angle), lines)
    assert share([a for _, a in measured if a is not None], lambda a: a < SUSPECT_DEG) >= 0.95


@pytest.mark.parametrize("angle", [1.5, -1.5])
def test_baseline_follows_rotation(angle):
    # o PIL gira no sentido anti-horário: com ângulo positivo a linha sobe para a direita e, com o
    # y crescendo para baixo, a base dá −ângulo
    image, truth = text_image(sample_lines(), angle_deg=angle)
    mask = ink_mask(image)
    boxes = word_boxes(mask, _ink_bbox(mask))
    assert [sum(b.line == i for b in boxes) for i in range(len(truth))] == [len(l) for l in truth]
    for i in range(len(truth)):
        assert baseline_deg([b for b in boxes if b.line == i]) == pytest.approx(-angle, abs=0.3)


def test_slant_is_relative_to_baseline():
    image, truth = text_image([[("reprodução", True)]])
    mask = ink_mask(image)
    [box] = word_boxes(mask, bbox_of(truth))
    assert slant_deg(mask, box, baseline=-1.5) == slant_deg(mask, box) + 1.5


@pytest.mark.parametrize("serif", [False, True])
def test_short_or_stemless_words_are_not_measured(serif):
    # "a": mais estreita que a altura da linha; "vez": sem hastes, o perfil não tem pico claro
    lines = [[("a", False), ("vez", False), ("obra", False), ("vez", True), ("obra", True)]]
    image, truth = text_image(lines, serif=serif)
    mask = ink_mask(image)
    boxes = word_boxes(mask, bbox_of(truth))
    assert [slant_deg(mask, b) is None for b in boxes] == [True, True, False, True, False]
