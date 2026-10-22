import json
from collections import Counter
from pathlib import Path

import httpx
import pytest
from pypdf import PdfReader

from ocrpdf import cli
from ocrpdf.cli import main
from ocrpdf.model import Block, Document, OutputFormat, Rule, Span
from ocrpdf.output_format import apply_format, apply_output_format, parse_page_size, validate_output_format
from ocrpdf.render import render_pdf
from ocrpdf.servers import SleepSwitcher
from synth import FakeEngine, benjamin_like_page, make_pdf
from test_render import _doc_with_many_notes, _misplaced, _text_positions


@pytest.mark.parametrize("spec,expected", [("original", None), ("a4", (595.28, 841.89)),
                                           ("17.6x25", (498.9, 708.66)), ("17,6x25", (498.9, 708.66))])
def test_parse_page_size(spec, expected):
    assert parse_page_size(spec) == expected


@pytest.mark.parametrize("spec", ["4x10", "17.6", "x25", "a3x", "101x20"])
def test_parse_page_size_rejects(spec):
    with pytest.raises(ValueError):
        parse_page_size(spec)


def test_margins_too_large_for_page():
    with pytest.raises(ValueError):
        apply_output_format(Document(), (170.08, 170.08), 2.5)  # 6x6 cm


def test_apply_scales_fonts_without_mutating_input():
    doc = Document(body_font_pt=10, paragraph_gap_pt=5, running_header_font_pt=8)
    rule = Rule(thickness_pt=1, gap_pt=4, left_em=0, right_frac=0.5)
    doc.blocks.append(Block(kind="title", spans=[Span("t")]))
    doc.blocks[0].style.font_size_pt = 20
    doc.blocks[0].style.rule_below = rule
    out = apply_output_format(doc, None, font_size_pt=12)
    assert (out.body_font_pt, out.paragraph_gap_pt, out.running_header_font_pt) == pytest.approx((12, 6, 9.6))
    assert out.blocks[0].style.font_size_pt == pytest.approx(24)
    assert out.blocks[0].style.rule_below.gap_pt == pytest.approx(4.8)
    assert out.page == doc.page                        # sem page_size, a página não muda
    assert (doc.body_font_pt, doc.blocks[0].style.font_size_pt, rule.gap_pt) == (10, 20, 4)


def _body_font_size(pdf: Path) -> float:
    """Tamanho (pt) mais frequente do texto: o fluxo do WeasyPrint está em px (1 px = 0,75 pt)."""
    sizes: Counter[float] = Counter()

    def visitor(text, cm, tm, font_dict, font_size):
        if text.strip():
            sizes[round(font_size * tm[0] * 0.75, 1)] += len(text)

    for page in PdfReader(pdf).pages:
        page.extract_text(visitor_text=visitor)
    return sizes.most_common(1)[0][0]


def test_a4_and_font_size_in_pdf(tmp_path):
    doc = apply_output_format(_doc_with_many_notes(spread=80, long_notes=True), (595.28, 841.89), 2.5, 12)
    out = tmp_path / "a4.pdf"
    render_pdf(doc, out, base_dir=tmp_path)
    box = PdfReader(out).pages[0].mediabox
    assert (round(float(box.width), 1), round(float(box.height), 1)) == (595.3, 841.9)
    assert _body_font_size(out) == pytest.approx(12, abs=0.2)
    assert _misplaced(out) == []                       # notas na página da chamada


def test_a4_margin_in_pdf(tmp_path):
    doc = _doc_with_many_notes(spread=60, long_notes=False)
    doc.notes.clear()                                  # só texto do corpo (as notas vão no @footnote)
    out = tmp_path / "a4.pdf"
    render_pdf(apply_output_format(doc, (595.28, 841.89), 2.5), out, base_dir=tmp_path)
    left_cm = min(x for _, x, _ in _text_positions(out)) * 2.54 / 72
    assert left_cm == pytest.approx(2.5, abs=0.2)


def test_cli_render_with_page_size(tmp_path):
    src = tmp_path / "document.json"
    _doc_with_many_notes(spread=60, long_notes=False).save(src)
    out = tmp_path / "x.pdf"
    assert main(["render", str(src), "-o", str(out), "--page-size", "17.6x25"]) == 0
    box = PdfReader(out).pages[0].mediabox
    assert (round(float(box.width), 1), round(float(box.height), 1)) == (498.9, 708.7)


def test_cli_render_rejects_bad_page_size(tmp_path, capsys):
    src = tmp_path / "document.json"
    Document().save(src)
    with pytest.raises(SystemExit) as e:
        main(["render", str(src), "-o", str(tmp_path / "x.pdf"), "--page-size", "4x10"])
    assert e.value.code == 2
    assert "tamanho de página fora do intervalo" in capsys.readouterr().err


def _exit_code(argv: list[str]) -> int:
    """Código de saída, venha do retorno de main ou do argparse (SystemExit)."""
    try:
        return main(argv)
    except SystemExit as e:
        return e.code


@pytest.mark.parametrize("opts,message", [
    (["--font-size", "0"], "o tamanho da fonte deve ser positivo"),
    (["--font-size", "nan"], "o tamanho da fonte deve ser positivo"),
    (["--page-size", "a4", "--margin", "nan"], "a margem deve ser um número finito"),
    (["--font-size", "doze"], "tamanho de fonte inválido: doze"),
    (["--page-size", "6x6", "--margin", "2.5"], "margem de 2.5 cm deixa menos de 3 cm de área de texto"),
])
def test_cli_convert_rejects_bad_format_before_ocr(tmp_path, monkeypatch, capsys, opts, message):
    image, regions = benjamin_like_page()
    engine = FakeEngine([regions])
    monkeypatch.setattr(cli, "VlmEngine", lambda **kw: engine)
    pdf = make_pdf(tmp_path / "in.pdf", [image])
    assert _exit_code(["convert", str(pdf), "-o", str(tmp_path / "out.pdf"), *opts]) == 2
    assert engine.calls == 0                       # nem chegou ao OCR
    assert message in capsys.readouterr().err


def test_apply_format_matches_apply_output_format():
    doc = _doc_with_many_notes(spread=60, long_notes=False)
    out = apply_format(doc, OutputFormat("a4", 2.0, 12.0, True))
    ref = apply_output_format(doc, parse_page_size("a4"), 2.0, 12.0)
    assert out.page == ref.page and out.body_font_pt == ref.body_font_pt
    doc.running_header = "Cabeçalho"
    assert apply_format(doc, OutputFormat(header=False)).running_header == ""
    assert apply_format(doc, OutputFormat()).running_header == "Cabeçalho"
    with pytest.raises(ValueError):
        apply_format(doc, OutputFormat("grande"))


def _saved_doc(tmp_path, fmt):
    src = tmp_path / "document.json"
    doc = _doc_with_many_notes(spread=60, long_notes=False)
    doc.output = fmt
    doc.save(src)
    return src


def test_cli_render_uses_saved_format(tmp_path):
    src = _saved_doc(tmp_path, OutputFormat("10x15", 1.0, None, True))
    out = tmp_path / "x.pdf"
    assert main(["render", str(src), "-o", str(out)]) == 0
    box = PdfReader(out).pages[0].mediabox
    assert float(box.width) == pytest.approx(283.46, abs=1) and float(box.height) == pytest.approx(425.2, abs=1)


def test_cli_render_options_override_saved_format(tmp_path):
    src = _saved_doc(tmp_path, OutputFormat("10x15", 1.0, None, True))
    before = src.read_text()
    out = tmp_path / "x.pdf"
    assert main(["render", str(src), "-o", str(out), "--page-size", "a4"]) == 0
    box = PdfReader(out).pages[0].mediabox
    assert float(box.width) == pytest.approx(595.28, abs=1) and float(box.height) == pytest.approx(841.89, abs=1)
    assert src.read_text() == before


def test_cli_convert_saves_format_options(tmp_path, monkeypatch):
    image, regions = benjamin_like_page()
    monkeypatch.setattr(cli, "VlmEngine", lambda **kw: FakeEngine([regions]))
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(404)))   # só o dots, simples
    monkeypatch.setattr(cli, "SleepSwitcher", lambda roots: SleepSwitcher(roots, client=client))
    pdf = make_pdf(tmp_path / "in.pdf", [image])
    work = tmp_path / "w"
    assert main(["convert", str(pdf), "-o", str(tmp_path / "out.pdf"), "-w", str(work), "--no-italic",
                 "--workers", "1", "--font", "sans", "--page-size", "a4", "--margin", "2", "--font-size", "11",
                 "--no-header"]) == 0
    assert Document.load(work / "document.json").output == OutputFormat("a4", 2.0, 11.0, False)


# --- revisão do PR #2: formato gravado inválido, cabeçalho, saída sobre o original ---------

@pytest.mark.parametrize("field,value,message", [
    ("margin_cm", None, "a margem deve ser um número finito e não negativo, em cm (recebido: null)"),
    ("margin_cm", "3", 'a margem deve ser um número finito e não negativo, em cm (recebido: "3")'),
    ("margin_cm", -5, "a margem deve ser um número finito e não negativo"),
    ("page_size", 5, "tamanho de página inválido: 5"),
    ("font_size_pt", 1e308, "o tamanho da fonte deve ser um número positivo de até 1000 pt"),
    ("font_size_pt", True, "o tamanho da fonte deve ser um número positivo"),
    ("header", "sim", "o cabeçalho (header) deve ser true ou false"),
])
def test_cli_render_rejects_bad_saved_format(tmp_path, capsys, field, value, message):
    src = _saved_doc(tmp_path, OutputFormat())
    data = json.loads(src.read_text())
    data["output"][field] = value                     # document.json editado à mão
    src.write_text(json.dumps(data))
    assert main(["render", str(src), "-o", str(tmp_path / "x.pdf")]) == 2
    err = capsys.readouterr().err
    assert err.startswith("ocrpdf: erro:") and message in err and "Traceback" not in err
    assert not (tmp_path / "x.pdf").exists()


def test_validate_output_format():
    validate_output_format(OutputFormat("17,6x25", 0, 1000, False))
    for bad in (OutputFormat("xyz"), OutputFormat(margin_cm=float("nan")), OutputFormat(font_size_pt=0),
                OutputFormat(font_size_pt=1000.5), OutputFormat(margin_cm=False)):
        with pytest.raises(ValueError):
            validate_output_format(bad)
    with pytest.raises(ValueError, match="até 1000 pt"):
        apply_output_format(Document(), None, font_size_pt=1001)


def test_cli_render_header_option_overrides_the_saved_one(tmp_path):
    doc = _doc_with_many_notes(spread=60, long_notes=False)
    doc.running_header = "CABECALHO CORRENTE"
    doc.output = OutputFormat(header=False)
    src = tmp_path / "document.json"
    doc.save(src)
    out = tmp_path / "x.pdf"
    assert main(["render", str(src), "-o", str(out)]) == 0
    assert "CABECALHO CORRENTE" not in _page_texts(out)
    assert main(["render", str(src), "-o", str(out), "--header"]) == 0
    assert "CABECALHO CORRENTE" in _page_texts(out)
    doc.output = None
    doc.save(src)
    assert main(["render", str(src), "-o", str(out), "--no-header"]) == 0
    assert "CABECALHO CORRENTE" not in _page_texts(out)


def _page_texts(pdf: Path) -> str:
    return "".join(p.extract_text() for p in PdfReader(pdf).pages)


def test_cli_render_refuses_to_overwrite_the_source_pdf(tmp_path, capsys):
    scanned = tmp_path / "in.pdf"
    scanned.write_bytes(b"%PDF-1.4 original")
    src = tmp_path / "w" / "document.json"
    src.parent.mkdir()
    for source_path in (str(scanned), "../in.pdf"):  # absoluto, ou relativo à pasta do document.json
        Document(source_path=source_path).save(src)
        assert main(["render", str(src), "-o", str(scanned)]) == 2
        err = capsys.readouterr().err
        assert err.startswith("ocrpdf: erro:") and "source_path" in err
    assert scanned.read_bytes() == b"%PDF-1.4 original"


def test_cli_convert_refuses_to_overwrite_the_input_before_ocr(tmp_path, monkeypatch, capsys):
    image, regions = benjamin_like_page()
    engine = FakeEngine([regions])
    monkeypatch.setattr(cli, "VlmEngine", lambda **kw: engine)
    pdf = make_pdf(tmp_path / "in.pdf", [image])
    before = pdf.read_bytes()
    assert _exit_code(["convert", str(pdf), "-o", str(tmp_path / "." / "in.pdf")]) == 2
    assert engine.calls == 0 and pdf.read_bytes() == before
    assert "é o próprio PDF escaneado" in capsys.readouterr().err
