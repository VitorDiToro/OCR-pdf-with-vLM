from __future__ import annotations

from pathlib import Path

from ..model import Document
from .html import render_html


def render_pdf(doc: Document, out_pdf: Path, base_dir: Path, html_out: Path | None = None) -> None:
    """Renderiza o documento; `base_dir` resolve os caminhos relativos das figuras."""
    from weasyprint import HTML  # import tardio: carrega Pango só quando necessário

    html = render_html(doc)
    if html_out is not None:
        html_out.write_text(html, encoding="utf-8")
    HTML(string=html, base_url=str(base_dir.resolve()) + "/").write_pdf(out_pdf)


__all__ = ["render_html", "render_pdf"]
