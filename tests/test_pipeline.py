import json

from synth import FakeEngine, benjamin_like_page, make_pdf

from ocrpdf.ocr.base import PageLayout, Region
from ocrpdf.pipeline import Pipeline, _overlaps


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


def test_rules_recomputed_and_cleared_outside_titles(tmp_path):
    image, regions = benjamin_like_page()
    pdf = make_pdf(tmp_path / "in.pdf", [image])
    engine = FakeEngine([regions])
    Pipeline(engine, tmp_path / "w", workers=1).analyze(pdf, font="sans")
    cache = tmp_path / "w" / "pages" / "page_0001.json"
    data = json.loads(cache.read_text())
    stale = {"position": "above", "x1_px": 1.0, "x2_px": 2.0, "y_px": 3.0, "thickness_px": 1.0, "gap_px": 1.0}
    for r in data["regions"]:          # filetes gravados por outra versão da heurística
        r["rules"] = [stale]
    cache.write_text(json.dumps(data))
    Pipeline(engine, tmp_path / "w", workers=1).analyze(pdf, font="sans")
    regions = PageLayout.load(cache).regions
    assert [len(r.rules) for r in regions] == [1, 0, 0, 0, 0]
    assert regions[0].rules[0].position == "below"


def test_bbox_invading_the_neighbour_is_measured_without_the_cut_line(tmp_path):
    # o dots deu ao 2º parágrafo um bbox que sobe 12 px na última linha do 1º (visto no Horkheimer)
    from test_probe import _two_paragraphs
    image, _, ((ax1, ay1, ax2, ay2), (bx1, by1, bx2, by2)) = _two_paragraphs(1.0)
    regions = [Region("text", (ax1, ay1, ax2, ay2), "a"), Region("text", (bx1, ay2 - 12, bx2, by2), "b")]
    pdf = make_pdf(tmp_path / "in.pdf", [image])
    Pipeline(FakeEngine([regions]), tmp_path / "w", workers=1).analyze(pdf, font="sans")
    first, second = (r.metrics for r in PageLayout.load(tmp_path / "w" / "pages" / "page_0001.json").regions)
    assert (first.lines, first.align) == (5, "justify")        # o vizinho de baixo não lhe tira nada
    assert (second.lines, second.align) == (5, "justify") and second.first_indent_px > 80


def test_overlaps_only_between_neighbours_of_the_same_column():
    a, b = (151, 398, 1547, 556), (151, 544, 1547, 730)
    assert _overlaps(b, [a]) == (12, 0) and _overlaps(a, [b]) == (0, 12)
    assert _overlaps(b, [(900, 500, 1547, 556)]) == (12, 0)    # vizinho estreito na mesma coluna
    assert _overlaps(b, [(1600, 398, 1690, 556)]) == (0, 0)    # outra coluna
    assert _overlaps(b, [(151, 600, 1547, 650)]) == (0, 0)     # dentro do bloco: não é borda


def test_analyze_saves_source_path(tmp_path):
    from ocrpdf.model import Document
    image, regions = benjamin_like_page()
    pdf = make_pdf(tmp_path / "in.pdf", [image])
    Pipeline(FakeEngine([regions]), tmp_path / "w", workers=1).analyze(pdf, font="sans")
    assert Document.load(tmp_path / "w" / "document.json").source_path == str(pdf.resolve())
