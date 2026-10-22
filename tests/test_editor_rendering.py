import io

from pypdf import PdfReader

from ocrpdf.editor.rendering import original_image, pdf_page_images, render_full, render_page_images
from ocrpdf.model import Block, Document, OutputFormat, Span
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
    assert render_full(doc, OutputFormat(), tmp_path / "a.pdf", tmp_path) == {0: 0, 1: 0, 2: 1, 3: 1}


def test_render_page_images_are_png(tmp_path):
    imgs = render_page_images(_doc(), [1, 2], OutputFormat(), tmp_path, highlight=2)
    assert imgs and all(i.startswith(b"\x89PNG") for i in imgs)


def test_original_image_size(tmp_path):
    img, _ = benjamin_like_page()
    pdf = make_pdf(tmp_path / "s.pdf", [img], dpi=200)
    png, w, h = original_image(pdf, 0)
    assert png.startswith(b"\x89PNG")
    ref = PdfReader(str(pdf)).pages[0]
    assert (round(w, 1), round(h, 1)) == (round(float(ref.mediabox.width), 1), round(float(ref.mediabox.height), 1))
    assert len(pdf_page_images(pdf, [0])) == 1
