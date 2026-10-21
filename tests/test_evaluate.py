"""Avaliação por palavra: gabarito, projeção da referência, pontuação e seleção de páginas."""

from __future__ import annotations

from ocrpdf.italic.evaluate import (Score, gold_flags, is_italic_font, normalize, page_tokens, parse_pages,
                                    project, reference_tokens, score)
from ocrpdf.italic.stage import BlockEvidence, final_flags


def test_normalize():
    assert normalize("(Rio") == "rio"
    assert normalize("“Idéia”.") == "idéia"
    assert normalize("L’art,") == "l'art"
    assert normalize("café") == "café"


def test_gold_flags_and_score():
    toks = {1: ["a", "obra", "A", "idéia", "do", "cinema", "(Rio"]}
    gold, missing = gold_flags(toks, {1: ["A idéia do cinema"]})
    assert gold == {1: [False, False, True, True, True, True, False]} and missing == []
    pred = {1: [False, True, True, True, True, False, False]}
    s = score(pred, gold, [1])
    assert (s.tp, s.fp, s.fn) == (3, 1, 1) and s.precision == 0.75
    assert isinstance(s, Score) and s.recall == 0.75


def test_gold_flags_missing_and_order():
    toks = {1: ["x", "y", "x", "y"], 2: ["z"]}
    gold, missing = gold_flags(toks, {1: ["x y", "x y", "nada disso"], 2: []})
    assert gold[1] == [True, True, True, True] and gold[2] == [False]
    assert missing == ["1: nada disso"]


def test_gold_flags_unannotated_pages_are_not_scored():
    toks = {1: ["a", "b"], 2: ["c", "d"], 3: ["e"]}
    gold, missing = gold_flags(toks, {1: ["a"], 2: []})
    assert set(gold) == {1, 2} and gold[2] == [False, False] and missing == []
    pred = {1: [True, False], 2: [True, False], 3: [True]}
    # a página 2 foi anotada sem itálico (fp conta); a 3 não foi anotada (ignorada)
    s = score(pred, gold, [1, 2, 3])
    assert (s.tp, s.fp, s.fn) == (1, 1, 0)


def test_gold_flags_repeated_phrase_listed_twice_but_present_once():
    toks = {1: ["x", "y", "z"]}
    gold, missing = gold_flags(toks, {1: ["x y", "x y"]})
    assert gold[1] == [True, True, False] and missing == ["1: x y"]


def test_gold_phrase_with_bracketed_context():
    toks = {1: ["a", "sua", "casa", "afirma", "diante", "do", "aparelho", "sua", "humanidade", "x"]}
    gold, missing = gold_flags(toks, {1: ["afirma diante do aparelho [sua] humanidade"]})
    assert gold[1] == [False] * 7 + [True, False, False] and missing == []
    gold, _ = gold_flags(toks, {1: ["aparelho [sua humanidade]"]})
    assert gold[1] == [False] * 7 + [True, True, False]
    gold, missing = gold_flags(toks, {1: ["do aparelho [sua] casa"]})
    assert not any(gold[1]) and missing == ["1: do aparelho [sua] casa"]


def test_score_without_predictions():
    s = score({1: [False]}, {1: [True]}, [1])
    assert (s.tp, s.fp, s.fn, s.precision, s.recall) == (0, 0, 1, 0.0, 0.0)


def test_is_italic_font():
    assert is_italic_font("/OMWHUL+SFTI1000") and not is_italic_font("/YEPUXV+SFRM1000")
    assert is_italic_font("/ABCDEF+LiberationSans-Italic") and is_italic_font("/X+Foo-It")
    assert not is_italic_font("/X+Item")


def test_reference_projection_from_pdf(tmp_path):
    from weasyprint import HTML
    pdf = tmp_path / "ref.pdf"
    HTML(string="<p>texto <i>em itálico</i> aqui</p>").write_pdf(pdf)
    ref = reference_tokens(pdf)
    assert [(normalize(w), it) for w, it in ref] == [
        ("texto", False), ("em", True), ("itálico", True), ("aqui", False)]
    # o escaneado tem uma palavra a mais no meio e um token de cabeçalho antes
    toks = {1: ["Cabeçalho", "texto", "em", "muito", "itálico", "aqui"]}
    assert project(ref, toks) == {1: [False, False, True, False, True, False]}


def test_parse_pages():
    assert parse_pages("ímpares", 5) == [1, 3, 5]
    assert parse_pages("impares", 5) == [1, 3, 5]
    assert parse_pages("pares", 5) == [2, 4]
    assert parse_pages("1-3,7", 8) == [1, 2, 3, 7]
    assert parse_pages(None, 3) == [1, 2, 3]


def test_page_tokens_concatenate_blocks_in_region_order():
    a = BlockEvidence(0, ["a", "b"], [True, False], [None, None])
    b = BlockEvidence(1, ["c"], [False], [None])
    toks = page_tokens({(0, 1): b, (0, 0): a, (1, 0): b})
    assert toks == {1: ["a", "b", "c"], 2: ["c"]}


def test_final_flags_mirrors_pipeline_rule():
    # sem marcas do 2º modelo, ou sem nenhuma medida: vale o dots
    sem_modelo = BlockEvidence(0, ["a", "b"], [True, False], [30.0, 30.0])
    assert final_flags(sem_modelo, "r2") == [True, False]
    sem_medida = BlockEvidence(0, ["a", "b"], [True, False], [None, None], model=[False, True])
    assert final_flags(sem_medida, "r1") == [True, False]
    # com as duas coisas: a fusão (R1 só aceita o que o modelo marcou e a imagem confirma)
    fundido = BlockEvidence(0, ["abc", "defg"], [True, False], [30.0, 30.0], model=[False, True])
    assert final_flags(fundido, "r1") == [False, True]
    assert final_flags(fundido, "r2") == [True, True]
