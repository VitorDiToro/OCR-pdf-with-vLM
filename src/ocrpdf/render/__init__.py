from __future__ import annotations

import inspect
import logging
from pathlib import Path

from ..model import Document
from .html import render_html

log = logging.getLogger(__name__)


def layout(doc: Document, base_dir: Path, block_ids: bool = False, highlight: int | None = None,
           html_out: Path | None = None):
    """Diagrama o documento (WeasyPrint) aplicando o patch das notas; ponto único de renderização."""
    from weasyprint import HTML  # import tardio: carrega Pango só quando necessário

    _keep_footnotes_on_call_page()
    html = render_html(doc, block_ids=block_ids, highlight=highlight)
    if html_out is not None:
        html_out.write_text(html, encoding="utf-8")
    return HTML(string=html, base_url=str(base_dir.resolve()) + "/").render()


def render_pdf(doc: Document, out_pdf: Path, base_dir: Path, html_out: Path | None = None) -> None:
    """Renderiza o documento; `base_dir` resolve os caminhos relativos das figuras."""
    layout(doc, base_dir, html_out=html_out).write_pdf(out_pdf)


_REPORT_LINE = "report = not context.in_column and can_break_now and not could_break_before"


def _keep_footnotes_on_call_page() -> None:
    """Impede o WeasyPrint de adiar notas para respeitar orphans/widows.

    Quando uma linha não cabe e orphans/widows impedem a quebra antes dela, o
    `_linebox_layout` do WeasyPrint (≤ 70) empurra as notas já colocadas na página para a
    seguinte, ignorando `footnote-policy`; em páginas seguidas a nota vai parar várias
    páginas depois da chamada. Com o atalho desligado, são as linhas que mudam de página.
    """
    from weasyprint.layout import block

    if getattr(block, "_ocrpdf_patched", False):
        return
    source = inspect.getsource(block._linebox_layout)
    if _REPORT_LINE not in source:
        log.warning("WeasyPrint mudou: notas de rodapé podem sair depois da página da chamada")
        return
    block._ocrpdf_original = block._linebox_layout  # para os testes compararem
    exec(compile(source.replace(_REPORT_LINE, "report = False"), block.__file__, "exec"), block.__dict__)
    block._ocrpdf_patched = True


__all__ = ["layout", "render_html", "render_pdf"]
