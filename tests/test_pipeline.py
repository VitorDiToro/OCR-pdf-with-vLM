import json

from synth import FakeEngine, benjamin_like_page, make_pdf

from ocrpdf.ocr.base import PageLayout
from ocrpdf.pipeline import Pipeline


def test_cached_page_gets_fresh_metrics_without_new_ocr(tmp_path):
    image, regions = benjamin_like_page()
    pdf = make_pdf(tmp_path / "in.pdf", [image])
    engine = FakeEngine([regions])
    Pipeline(engine, tmp_path / "w", workers=1).analyze(pdf, font="sans")
    cache = tmp_path / "w" / "pages" / "page_0001.json"
    data = json.loads(cache.read_text())
    for r in data["regions"]:          # cache "antigo": sem medidas
        r.pop("metrics", None)
    cache.write_text(json.dumps(data))
    doc = Pipeline(engine, tmp_path / "w", workers=1).analyze(pdf, font="sans")
    assert engine.calls == 1                       # não refez o OCR
    assert all(r.metrics for r in PageLayout.load(cache).regions if r.category == "text")
    assert 9.5 <= doc.body_font_pt <= 11.0         # corpo medido de novo (~10 pt)


def test_force_redoes_ocr(tmp_path):
    image, regions = benjamin_like_page()
    pdf = make_pdf(tmp_path / "in.pdf", [image])
    engine = FakeEngine([regions, regions])
    Pipeline(engine, tmp_path / "w", workers=1).analyze(pdf, font="sans")
    Pipeline(engine, tmp_path / "w", workers=1).analyze(pdf, font="sans", force=True)
    assert engine.calls == 2
