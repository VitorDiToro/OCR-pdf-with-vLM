"""Imagens para o editor: páginas do PDF reconstruído, do documento inteiro e do original."""

from __future__ import annotations

import copy
import io
from pathlib import Path

from ..model import Document, OutputFormat
from ..output_format import apply_format
from ..render import layout, render_html


def _png(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def pdf_page_images(pdf: Path | bytes, pages: list[int], dpi: int = 100) -> list[bytes]:
    """PNG das páginas (0-based) de um PDF."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf if isinstance(pdf, bytes) else str(pdf))
    try:
        return [_png(doc[p].render(scale=dpi / 72).to_pil()) for p in pages]
    finally:
        doc.close()


def render_page_images(doc: Document, indices: list[int], fmt: OutputFormat, base_dir: Path,
                       highlight: int | None = None, dpi: int = 100) -> list[bytes]:
    """Renderiza só os blocos `indices` (com todas as notas, geometria e cabeçalho); PNG por página.

    `highlight` é um índice em `doc.blocks`.
    """
    tmp = copy.copy(doc)
    tmp.blocks = [doc.blocks[i] for i in indices]
    if 0 not in indices:
        tmp.header_on_first_page = True   # a prévia não começa na 1ª página do documento
    pos = indices.index(highlight) if highlight in indices else None
    rendered = layout(apply_format(tmp, fmt), base_dir, block_ids=pos is not None, highlight=pos)
    pdf = rendered.write_pdf()
    return pdf_page_images(pdf, list(range(len(rendered.pages))), dpi)


def render_full(doc: Document, fmt: OutputFormat, out_pdf: Path, base_dir: Path,
                html_out: Path | None = None) -> tuple[dict[int, int], int]:
    """Grava o PDF do documento inteiro (e, com `html_out`, o HTML como o `ocrpdf render`, sem os ids
    dos blocos); devolve bloco → página final (0-based) dos blocos visíveis e o número de páginas."""
    formatted = apply_format(doc, fmt)
    rendered = layout(formatted, base_dir, block_ids=True)
    rendered.write_pdf(out_pdf)
    if html_out is not None:
        html_out.write_text(render_html(formatted), encoding="utf-8")
    mapping: dict[int, int] = {}
    for n, page in enumerate(rendered.pages):
        for name in page.anchors:
            if name.startswith("b") and name[1:].isdigit():
                mapping.setdefault(int(name[1:]), n)
    return mapping, len(rendered.pages)


def final_pages(mapping: dict[int, int], indices: list[int], page_count: int) -> list[int]:
    """Páginas do PDF final (0-based) com os blocos `indices` de `doc.blocks`.

    Vão da página do 1º bloco visível até a página onde começa o próximo bloco visível depois do
    último deles, inclusive (ele pode começar no meio dela; um bloco longo cobre as do meio); sem
    próximo, até a última página. Vazio se nenhum dos blocos é visível.
    """
    shown = [mapping[i] for i in indices if i in mapping]
    if not shown:
        return []
    nxt = min((i for i in mapping if i > max(indices)), default=None)
    end = mapping[nxt] if nxt is not None else page_count - 1
    return list(range(min(shown), max(end, max(shown)) + 1))


def original_size(pdf: Path, page: int) -> tuple[float, float]:
    """Tamanho em pt (largura, altura) da página do PDF escaneado, sem rasterizá-la."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        w, h = doc[page].get_size()
        return float(w), float(h)
    finally:
        doc.close()


def original_image(pdf: Path, page: int, dpi: int = 100) -> tuple[bytes, float, float]:
    """PNG da página do PDF escaneado e seu tamanho em pt (largura, altura)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        p = doc[page]
        w, h = p.get_size()
        return _png(p.render(scale=dpi / 72).to_pil()), float(w), float(h)
    finally:
        doc.close()
