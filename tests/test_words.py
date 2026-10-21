import pytest

from ocrpdf import inline
from ocrpdf.italic.words import apply_flags, has_math, italic_flags, tokens
from ocrpdf.model import Span


def test_tokens_keep_attached_superscript():
    spans = [Span("Primeira Versão"), Span("1", sup=True)]
    assert tokens(spans) == ["Primeira", "Versão1"]


def test_italic_flags():
    spans = inline.parse_flat("publicado em *A idéia do cinema* (Rio")
    assert italic_flags(spans) == [False, False, True, True, True, True, False]


def test_italic_flags_ignore_superscript():
    spans = [Span("Versão", italic=True), Span("1", sup=True)]
    assert italic_flags(spans) == [True]


def test_apply_flags_roundtrip_and_cursor():
    a, b = inline.parse_flat("um dois três"), inline.parse_flat("quatro cinco")
    flags = [False, True, True, True, False]
    sa, n = apply_flags(a, flags)
    sb, m = apply_flags(b, flags, n)
    assert (n, m) == (3, 5)
    assert italic_flags(sa) == [False, True, True] and italic_flags(sb) == [True, False]
    assert any(s.italic and s.text == "dois três" for s in sa)   # espaço interno itálico


def test_apply_flags_preserves_other_attributes():
    spans = [Span("Texto ", bold=True), Span("1", sup=True, note="n1"), Span(" fim")]
    out, n = apply_flags(spans, [True, True, True])
    assert n == 3
    assert any(s.sup and s.note == "n1" and s.text == "1" for s in out)
    assert all(s.bold for s in out if s.text.startswith("Texto"))
    assert italic_flags(out) == [True, True, True]


def test_apply_flags_never_italicizes_math():
    spans = [Span("a "), Span("x^2 + y", math=True), Span(" b")]
    assert tokens(spans) == ["a", "x^2 + y", "b"]
    out, n = apply_flags(spans, [True, True, True])
    assert n == 3
    assert [s.italic for s in out if s.math] == [False]


def test_apply_flags_missing_flags():
    with pytest.raises(ValueError):
        apply_flags(inline.parse_flat("um dois"), [True])


def test_has_math():
    assert has_math([Span("a"), Span("x", math=True)])
    assert not has_math([Span("a")])


def test_italic_flags_ignore_trailing_punctuation():
    assert italic_flags(inline.parse_flat("*Por isso*, precisavam")) == [True, True, False]
    assert italic_flags(inline.parse_flat("*publi*cado")) == [False]
