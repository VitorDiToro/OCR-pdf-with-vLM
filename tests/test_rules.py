import json

import numpy as np
import pypdfium2 as pdfium

from ocrpdf import probe
from ocrpdf.model import Document
from ocrpdf.ocr.base import PageLayout
from ocrpdf.pipeline import Pipeline
from ocrpdf.render import render_pdf
from synth import FakeEngine, benjamin_like_page, make_pdf


def test_find_rules_below_title():
    image, regions = benjamin_like_page()
    rules = probe.find_rules(probe.ink_mask(image), regions[0].bbox)
    assert len(rules) == 1 and rules[0].position == "below"
    assert 1 <= rules[0].thickness_px <= 3 and rules[0].gap_px <= 6
    assert abs(rules[0].x1_px - 254) <= 3 and abs(rules[0].x2_px - 1545) <= 3


def test_text_line_below_title_is_not_a_rule():
    image, regions = benjamin_like_page()
    assert probe.find_rules(probe.ink_mask(image), regions[1].bbox) == []   # subtítulo


def _longest_dark_row_fraction(pdf, page: int, y_range_pt: tuple[float, float]) -> float:
    """Maior trecho escuro contínuo de uma linha de pixels, como fração da largura da página."""
    doc = pdfium.PdfDocument(str(pdf))
    try:
        gray = np.asarray(doc[page].render(scale=2).to_pil().convert("L"))
    finally:
        doc.close()
    best = 0
    for y in range(int(y_range_pt[0] * 2), int(y_range_pt[1] * 2)):
        run = longest = 0
        for dark in gray[y] < 128:
            run = run + 1 if dark else 0
            longest = max(longest, run)
        best = max(best, longest)
    return best / gray.shape[1]


def test_rule_reaches_pdf(tmp_path):
    image, regions = benjamin_like_page()
    doc = Pipeline(FakeEngine([regions]), tmp_path / "w", workers=1).analyze(
        make_pdf(tmp_path / "in.pdf", [image]), font="sans")
    rule = doc.blocks[0].style.rule_below
    assert rule is not None and rule.right_frac <= 0.01     # termina na margem direita
    out = tmp_path / "out.pdf"
    render_pdf(doc, out, base_dir=tmp_path / "w")
    # linha horizontal escura abaixo do título, ocupando ≥ 75% da área de texto
    # (a área de texto tem ~ 0,82 da largura da página)
    assert _longest_dark_row_fraction(out, page=0, y_range_pt=(70, 110)) >= 0.75 * 0.82


def test_old_files_without_rules_load(tmp_path):
    image, regions = benjamin_like_page()
    doc = Pipeline(FakeEngine([regions]), tmp_path / "w", workers=1).analyze(
        make_pdf(tmp_path / "in.pdf", [image]), font="sans")
    # page cache without "rules"
    page = tmp_path / "w" / "pages" / "page_0001.json"
    d = json.loads(page.read_text())
    for r in d["regions"]:
        r.pop("rules")
    page.write_text(json.dumps(d))
    assert all(r.rules == [] for r in PageLayout.load(page).regions)
    # document.json without rule_*
    path = tmp_path / "doc.json"
    doc.save(path)
    d = json.loads(path.read_text())
    for b in d["blocks"]:
        b["style"].pop("rule_above"), b["style"].pop("rule_below")
    path.write_text(json.dumps(d))
    loaded = Document.load(path)
    assert loaded.blocks[0].style.rule_below is None
