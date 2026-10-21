from __future__ import annotations

from PIL import Image

from ocrpdf.italic.marker import PROMPT_VERSION, mark_italics
from synth import FakeEngine

CROP = Image.new("RGB", (200, 60), "white")
TOKS = ["publicado", "em", "A", "idéia", "do", "cinema", "(Rio"]
OK = [False, False, True, True, True, True, False]


class _Recorder(FakeEngine):
    def ask(self, image, question, max_tokens=128):
        self.last = (question, max_tokens)
        return super().ask(image, question, max_tokens)


class _Broken:
    def ask(self, image, question, max_tokens=128):
        raise ConnectionError("sem servidor")


def _run(answer, toks=TOKS):
    return mark_italics(FakeEngine([], answers=[answer]), CROP, toks)


def test_marks_become_flags():
    assert _run("publicado em <i>A idéia do cinema</i> (Rio") == OK


def test_code_fence_is_tolerated():
    assert _run("```\npublicado em <i>A idéia do cinema</i> (Rio\n```") == OK
    assert _run("```html\npublicado em <i>A idéia do cinema</i> (Rio\n```") == OK


def test_em_tags_and_case_are_accepted():
    assert _run("publicado em <EM>A idéia do cinema</Em> (Rio") == OK


def test_changed_word_is_rejected():
    assert _run("publicado em <i>A ideia do cinema</i> (Rio") is None


def test_normalized_quotes_are_rejected():
    assert _run('"Em casa', ["“Em", "casa"]) is None


def test_partial_token_is_not_italic():
    assert _run("<i>publi</i>cado em A idéia do cinema (Rio") == [False] * 7


def test_unbalanced_tags_are_rejected():
    assert _run("publicado em <i>A idéia do cinema (Rio") is None
    assert _run("publicado em A</i> idéia do cinema (Rio") is None


def test_other_tags_are_rejected():
    assert _run("publicado em <b>A</b> idéia do cinema (Rio") is None


def test_whitespace_is_normalized():
    assert _run("publicado  em\n<i>A idéia do cinema</i>\t(Rio") == OK


def test_engine_error_returns_none():
    assert mark_italics(_Broken(), CROP, TOKS) is None


def test_question_and_max_tokens():
    eng = _Recorder([], answers=["a b"])
    mark_italics(eng, CROP, ["a", "b"])
    question, max_tokens = eng.last
    assert PROMPT_VERSION == 1
    assert question.endswith("Transcription:\na b")
    assert max_tokens == len("a b") // 2 + 128
