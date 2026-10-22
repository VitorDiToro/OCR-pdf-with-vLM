import io
from pathlib import Path

from pypdf import PdfReader

from ocrpdf.editor import rendering
from ocrpdf.editor.rendering import final_pages, original_image, pdf_page_images, render_full, render_page_images
from ocrpdf.model import Block, Document, OutputFormat, Source, Span
from ocrpdf.output_format import apply_format
from ocrpdf.render import render_pdf
from synth import benjamin_like_page, make_pdf


def _doc(n: int = 4) -> Document:
    doc = Document()
    doc.blocks = [Block(kind="paragraph", spans=[Span(f"Parágrafo número {i} do texto.")]) for i in range(n)]
    return doc


def _text(path):
    r = PdfReader(str(path))
    return len(r.pages), "".join(p.extract_text() for p in r.pages)


def test_render_full_is_identical_to_render_pdf(tmp_path):
    doc, fmt = _doc(), OutputFormat(page_size="a4")
    render_full(doc, fmt, tmp_path / "a.pdf", tmp_path)
    render_pdf(apply_format(doc, fmt), tmp_path / "b.pdf", tmp_path)
    assert _text(tmp_path / "a.pdf") == _text(tmp_path / "b.pdf")


def test_render_full_maps_blocks_to_pages(tmp_path):
    doc = _doc()
    doc.blocks[2].style.page_break_before = True
    assert render_full(doc, OutputFormat(), tmp_path / "a.pdf", tmp_path) == ({0: 0, 1: 0, 2: 1, 3: 1}, 2)


def test_final_pages_cover_long_blocks_until_the_next_visible_block():
    # bloco 1 vai da página 0 à 3; o bloco 2 está oculto (sem âncora); o bloco 3 começa na 3
    mapping = {0: 0, 1: 0, 3: 3, 4: 5}
    assert final_pages(mapping, [0, 1], 7) == [0, 1, 2, 3]
    assert final_pages(mapping, [2, 3], 7) == [3, 4, 5]
    assert final_pages(mapping, [4], 7) == [5, 6]          # sem próximo: até a última página
    assert final_pages(mapping, [2], 7) == []               # só blocos ocultos
    assert final_pages({}, [0], 0) == []


def test_render_page_images_are_png(tmp_path):
    imgs = render_page_images(_doc(), [1, 2], OutputFormat(), tmp_path, highlight=2)
    assert imgs and all(i.startswith(b"\x89PNG") for i in imgs)


def _orange_pixels(png: bytes) -> int:
    """Pixels do laranja do destaque (#e09b00, com antisserrilhamento)."""
    import numpy as np
    from PIL import Image

    rgb = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(int)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return int(((r > 180) & (g > 100) & (g < 200) & (b < 90) & (r - g > 40)).sum())


def test_highlight_outlines_the_cursor_block_in_the_preview(tmp_path):
    doc, fmt = _doc(), OutputFormat(page_size="a4")
    plain = render_page_images(doc, [0, 1, 2], fmt, tmp_path)
    marked = render_page_images(doc, [0, 1, 2], fmt, tmp_path, highlight=1)
    assert _orange_pixels(plain[0]) == 0
    assert _orange_pixels(marked[0]) > 500        # contorno visível em volta do bloco
    # o PDF final nunca leva o destaque
    render_full(doc, fmt, tmp_path / "final.pdf", tmp_path)
    assert _orange_pixels(pdf_page_images(tmp_path / "final.pdf", [0])[0]) == 0


def test_original_image_size(tmp_path):
    img, _ = benjamin_like_page()
    pdf = make_pdf(tmp_path / "s.pdf", [img], dpi=200)
    png, w, h = original_image(pdf, 0)
    assert png.startswith(b"\x89PNG")
    ref = PdfReader(str(pdf)).pages[0]
    assert (round(w, 1), round(h, 1)) == (round(float(ref.mediabox.width), 1), round(float(ref.mediabox.height), 1))
    assert len(pdf_page_images(pdf, [0])) == 1


def _preview_text(monkeypatch, doc: Document, indices: list[int]) -> str:
    """Texto da 1ª página do PDF da prévia (captura o PDF antes de virar imagem)."""
    pdfs = []
    monkeypatch.setattr(rendering, "pdf_page_images", lambda pdf, pages, dpi=100: pdfs.append(pdf) or [])
    render_page_images(doc, indices, OutputFormat(), Path("."))
    return PdfReader(io.BytesIO(pdfs[0])).pages[0].extract_text()


def test_preview_keeps_the_running_header_after_the_first_page(monkeypatch):
    doc = Document(running_header="CABECALHO CORRENTE", header_on_first_page=False)
    doc.blocks = [Block(kind="paragraph", spans=[Span(f"Bloco {i}.")], source=Source(page=i)) for i in range(3)]
    # a página 1 do original não é a 1ª do documento: o cabeçalho aparece na prévia
    assert "CABECALHO CORRENTE" in _preview_text(monkeypatch, doc, [1])
    # a prévia que começa no 1º bloco segue a regra do documento (sem cabeçalho na 1ª página)
    assert "CABECALHO CORRENTE" not in _preview_text(monkeypatch, doc, [0, 1])
    assert doc.header_on_first_page is False   # o documento não muda


def test_endnote_block_is_mapped_and_has_a_single_id(tmp_path):
    import re

    from ocrpdf.model import Note
    from ocrpdf.render import render_html

    doc = Document()
    doc.blocks = [
        Block(kind="paragraph", spans=[Span("Texto com chamada"), Span("1", sup=True, note="n1")]),
        Block(kind="heading", level=2, spans=[Span("Notas")]),
        Block(kind="endnote", note="n1"),
    ]
    doc.notes = {"n1": Note("n1", "1", "endnote", 0, [Block("paragraph", [Span("Conteúdo da nota.")])])}
    mapping, _ = render_full(doc, OutputFormat(), tmp_path / "a.pdf", tmp_path)
    assert 2 in mapping
    for html in (render_html(doc, block_ids=True), render_html(doc, block_ids=True, highlight=2)):
        assert not re.search(r'<[^>]*\sid="[^"]*"[^>]*\sid="', html)   # nenhuma tag com dois id
        assert 'id="b2"' in html and 'id="n1"' in html
    assert 'class="hl endnote" id="n1"' in render_html(doc, block_ids=True, highlight=2)


def test_original_size_without_rasterizing(tmp_path, monkeypatch):
    import pypdfium2 as pdfium

    img, _ = benjamin_like_page()
    pdf = make_pdf(tmp_path / "s.pdf", [img], dpi=200)
    expected = original_image(pdf, 0)[1:]

    def no_render(*a, **k):
        raise AssertionError("rasterizou a página")

    monkeypatch.setattr(pdfium.PdfPage, "render", no_render)
    assert rendering.original_size(pdf, 0) == expected
