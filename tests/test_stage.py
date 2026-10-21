"""Evidência por bloco, fusão R1/R2 e cache das marcas de itálico."""

import json

from ocrpdf.italic.marker import PROMPT_VERSION
from ocrpdf.italic.slant import ITALIC_MIN_DEG
from ocrpdf.italic.stage import (BlockEvidence, fuse, load_marks, needs_model, page_evidence, save_marks,
                                 tokens_key)
from ocrpdf.ocr.base import PageLayout, Region
from ocrpdf.probe import ink_mask
from synth import bbox_of, flat_words, sample_lines, text_image


def ev(dots, model, slant):
    return BlockEvidence(0, [f"w{i}" for i in range(len(dots))], dots, slant, model)


def test_r1_requires_model_and_slant():
    e = ev([True, False, False], [False, True, True], [9.0, 9.0, 1.0])
    assert fuse(e, "r1") == [False, True, False]


def test_r2_accepts_dots_or_model_with_slant():
    e = ev([True, False, False], [False, True, True], [9.0, 9.0, 1.0])
    assert fuse(e, "r2") == [True, True, False]


def test_short_unmeasured_word_inherits_from_neighbours():
    e = BlockEvidence(0, ["Sonho", "de", "uma"], [False] * 3, [10.0, None, 9.5], [True] * 3)
    assert fuse(e, "r1") == [True, True, True]


def test_short_unmeasured_word_needs_both_neighbours():
    e = BlockEvidence(0, ["de", "uma", "casa"], [False] * 3, [None, 9.5, 1.0], [True] * 3)
    assert fuse(e, "r1") == [False, True, False]
    e = BlockEvidence(0, ["Sonho", "de", "uma"], [False] * 3, [10.0, None, 2.0], [True] * 3)
    assert fuse(e, "r1") == [True, False, False]


def test_long_unmeasured_word_stays_normal():
    e = BlockEvidence(0, ["Sonho", "grandes", "uma"], [False] * 3, [10.0, None, 9.5], [True] * 3)
    assert fuse(e, "r1") == [True, False, True]


def test_without_model_keeps_dots():
    assert fuse(ev([True, False], None, [None, None]), "r2") == [True, False]


def test_needs_model():
    assert needs_model(ev([True, False], None, [None, None]))
    assert needs_model(ev([False, False], None, [None, 5.0]))
    assert not needs_model(ev([False, False], None, [None, 4.9]))


def test_marks_cache_invalidated_by_model_and_prompt(tmp_path):
    path = tmp_path / "italic" / "page_0001.json"
    assert load_marks(path, "m") == {}
    key = tokens_key(["a", "b"])
    save_marks(path, "m", {0: (key, [True, False]), 3: (key, None)})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"model": "m", "prompt_version": PROMPT_VERSION,
                    "blocks": {"0": {"tokens": key, "model": [True, False]},
                               "3": {"tokens": key, "model": None}}}
    assert load_marks(path, "m") == {0: (key, [True, False]), 3: (key, None)}
    assert load_marks(path, "outro") == {}
    data["prompt_version"] = PROMPT_VERSION + 1
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_marks(path, "m") == {}
    path.write_text("{lixo", encoding="utf-8")
    assert load_marks(path, "m") == {}


def test_tokens_key_depends_on_tokens():
    assert tokens_key(["a", "b"]) == tokens_key(["a", "b"]) != tokens_key(["a", "c"])


def test_page_evidence_on_synthetic_page():
    lines = sample_lines()
    # a palavra 3 ("a") é curta demais para medir; as itálicas são a 3ª e a 5ª
    lines[0] = [(w, i in (2, 4)) for i, (w, _) in enumerate(lines[0])]
    image, truth = text_image(lines)
    x1, y1, x2, y2 = bbox_of(truth)
    words = flat_words(lines)
    layout = PageLayout(0, image.width, image.height, image.width * 72 / 200, image.height * 72 / 200, [
        Region("text", (x1 - 4, y1 - 4, x2 + 4, y2 + 4), " ".join(words)),
        Region("picture", (0, 0, 10, 10)),
        Region("text", (0, 0, 10, 10), "$x^2$ e mais"),
    ])
    evs = page_evidence(layout, ink_mask(image))
    assert [e.region for e in evs] == [0]
    assert evs[0].tokens == words and evs[0].dots == [False] * len(words)
    assert needs_model(evs[0]) and [s >= ITALIC_MIN_DEG for s in (evs[0].slant[2], evs[0].slant[4])] == [True, True]
