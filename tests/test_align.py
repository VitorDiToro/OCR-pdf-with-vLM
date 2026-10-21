"""Casamento dos tokens do texto com as caixas de palavra da imagem (texto sintético)."""

import pytest
from PIL import ImageDraw

from ocrpdf.italic.align import match_quality, match_tokens
from ocrpdf.italic.slant import WordBox, word_boxes
from ocrpdf.probe import ink_mask
from synth import bbox_of, flat_words, sample_lines, text_image


def boxcenter(box):
    return ((box.x1 + box.x2) / 2, (box.y1 + box.y2) / 2)


def approx_centers(truth):
    """Centros das palavras desenhadas, na ordem de leitura. Folga de 6 px: na vertical a caixa
    tem a altura da linha, e a tinta da palavra não."""
    return [pytest.approx(((x1 + x2) / 2, (y1 + y2) / 2), abs=6) for row in truth for x1, y1, x2, y2 in row]


def boxes_of(image, truth):
    return word_boxes(ink_mask(image), bbox_of(truth))


@pytest.mark.parametrize("italic_every", [0, 2])
@pytest.mark.parametrize("serif", [False, True])
def test_clean_paragraph_matches_one_to_one(serif, italic_every):
    lines = sample_lines(italic_every)
    image, truth = text_image(lines, serif=serif)
    matches = match_tokens(flat_words(lines), boxes_of(image, truth))
    assert all(len(m) == 1 for m in matches) and match_quality(matches) == 1.0
    assert [boxcenter(m[0]) for m in matches] == approx_centers(truth)


def test_hyphenated_word_takes_two_boxes():
    lines = [[("a", False), ("obra", False), ("reprodu-", False)], [("tível", False), ("hoje", False)]]
    image, truth = text_image(lines)
    matches = match_tokens(["a", "obra", "reprodutível", "hoje"], boxes_of(image, truth))
    assert [len(m) for m in matches] == [1, 1, 2, 1]
    assert [b.line for b in matches[2]] == [0, 1]
    assert [boxcenter(b) for m in matches for b in m] == approx_centers(truth)


@pytest.mark.parametrize("serif", [False, True])
def test_attached_punctuation_and_note_markers(serif):
    # o texto traz a chamada de nota como "1" colado; na imagem ela é o "¹" sobrescrito
    toks = ["como", "“Em", "busca", "do", "ouro”,", "o", "que", "Versão1"]
    drawn = [[(t.replace("1", "¹"), False) for t in toks]]
    image, truth = text_image(drawn, serif=serif)
    matches = match_tokens(toks, boxes_of(image, truth))
    assert all(len(m) == 1 for m in matches)
    assert [boxcenter(m[0]) for m in matches] == approx_centers(truth)


@pytest.mark.parametrize("serif", [False, True])
def test_missing_word_does_not_shift_the_rest(serif):
    # a imagem não tem a 3ª palavra do texto
    lines = sample_lines()
    drawn = [line[:] for line in lines]
    del drawn[0][2]
    image, truth = text_image(drawn, serif=serif)
    matches = match_tokens(flat_words(lines), boxes_of(image, truth))
    assert matches[2] == []
    assert [boxcenter(m[0]) for m in matches[:2] + matches[3:]] == approx_centers(truth)


@pytest.mark.parametrize("serif", [False, True])
def test_skipped_line_does_not_shift_the_rest(serif):
    # o texto não tem a 3ª linha da imagem (o OCR a pulou): 9 caixas seguidas sem token. Na fronteira,
    # palavras de largura parecida com as da linha pulada podem ficar sem caixa (até 10%; cobrando
    # cada caixa sobrando como isolada, ficariam 18%); com a caixa errada, nenhuma
    lines = sample_lines()
    image, truth = text_image(lines, serif=serif)
    matches = match_tokens(flat_words(lines[:2] + lines[3:]), boxes_of(image, truth))
    assert match_quality(matches) >= 0.9 and all(len(m) <= 1 for m in matches)
    expected = approx_centers(truth[:2] + truth[3:])
    assert [boxcenter(m[0]) for m in matches if m] == [c for m, c in zip(matches, expected) if m]


def test_ambiguous_token_gets_no_box():
    # falta "de" ou "da" na imagem? As duas têm a mesma largura: nenhuma fica com a caixa
    image, truth = text_image([[("obra", False), ("da", False), ("arte", False)]])
    matches = match_tokens(["obra", "de", "da", "arte"], boxes_of(image, truth))
    assert matches[1] == matches[2] == []
    assert [boxcenter(m[0]) for m in (matches[0], matches[3])] == approx_centers([[truth[0][0], truth[0][2]]])


def test_dirt_is_left_over():
    # mancha depois do fim da linha mais curta: caixa sem token
    lines = sample_lines()
    image, truth = text_image(lines)
    _, y1, end, y2 = min((row[-1] for row in truth), key=lambda b: b[2])
    x, y = end + 40, (y1 + y2) // 2
    ImageDraw.Draw(image).ellipse((x, y - 4, x + 8, y + 4), fill="black")
    boxes = boxes_of(image, truth)
    assert len(boxes) == len(flat_words(lines)) + 1
    matches = match_tokens(flat_words(lines), boxes)
    assert all(len(m) == 1 for m in matches)
    assert [boxcenter(m[0]) for m in matches] == approx_centers(truth)


def test_loose_superscript_is_left_over():
    # chamada de nota separada da palavra na imagem; no texto, colada
    drawn = [[("Primeira", False), ("Versão", False), ("¹", False), ("da", False), ("obra", False)]]
    image, truth = text_image(drawn)
    boxes = boxes_of(image, truth)
    assert len(boxes) == 5
    matches = match_tokens(["Primeira", "Versão1", "da", "obra"], boxes)
    assert all(len(m) == 1 for m in matches)
    assert [boxcenter(m[0]) for m in matches] == approx_centers([truth[0][:2] + truth[0][3:]])


def test_two_tokens_share_one_box():
    # espaço apertado: "de arte" virou uma caixa só
    image, truth = text_image([[("obra", False), ("dearte", False), ("sempre", False)]])
    matches = match_tokens(["obra", "de", "arte", "sempre"], boxes_of(image, truth))
    assert [len(m) for m in matches] == [1, 1, 1, 1]
    assert matches[1] == matches[2]
    assert [boxcenter(m[0]) for m in matches[:2] + matches[3:]] == approx_centers(truth)


def test_empty_inputs():
    box = WordBox(0, 0, 40, 30, 0)
    assert match_tokens([], [box]) == []
    assert match_tokens(["obra", "de"], []) == [[], []]


def test_match_quality_is_share_of_tokens_with_boxes():
    box = WordBox(0, 0, 40, 30, 0)
    assert match_quality([[box], [], [box, box], [box]]) == 0.75
    assert match_quality([]) == 0.0
