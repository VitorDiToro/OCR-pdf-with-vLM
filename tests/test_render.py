from pathlib import Path

import pytest
from pypdf import PdfReader

from ocrpdf import render
from ocrpdf.model import Block, Document, Note, PageGeometry, Span

_WORDS = "texto de corpo com palavras de tamanho variado para encher linhas inteiras".split()


def _doc_with_many_notes(n: int = 30) -> Document:
    """Página pequena e parágrafos longos, cada um com uma nota: muitas quebras em páginas com notas."""
    doc = Document(page=PageGeometry(300, 300, 30, 30, 30, 30), body_font_pt=10, line_height=1.2,
                   paragraph_gap_pt=4, font_family="sans")
    for i in range(1, n + 1):
        nid = f"n{i}"
        words = [_WORDS[(i * 7 + k) % len(_WORDS)] for k in range(20 + (i * 13) % 80)]
        doc.blocks.append(Block(kind="paragraph", spans=[
            Span(" ".join(words[:5]) + " "), Span(str(i), sup=True, note=nid), Span(" " + " ".join(words[5:]) + "."),
        ]))
        doc.notes[nid] = Note(id=nid, label=str(i), kind="footnote", page=0,
                              blocks=[Block(kind="paragraph", spans=[Span(f"Nota {i}, curta.")])])
    return doc


def _note_pages(pdf: Path) -> list[tuple[str, int, int]]:
    """(nota, página da chamada, página da nota) pelos destinos nomeados ref-nX e nX."""
    reader = PdfReader(pdf)
    dests = {name: reader.get_destination_page_number(d) + 1 for name, d in reader.named_destinations.items()}
    notes = sorted((k for k in dests if k[0] == "n" and k[1:].isdigit()), key=lambda k: int(k[1:]))
    return [(n, dests["ref-" + n], dests[n]) for n in notes]


def test_footnotes_stay_on_the_page_of_their_call(tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    render.render_pdf(_doc_with_many_notes(), out, base_dir=tmp_path)
    pages = _note_pages(out)
    assert len(pages) == 30
    assert [p for p in pages if p[1] != p[2]] == []


def test_without_patch_weasyprint_defers_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Garante que o documento acima de fato exercita o defeito que o patch corrige."""
    from weasyprint.layout import block

    render._keep_footnotes_on_call_page()
    if not getattr(block, "_ocrpdf_patched", False):
        pytest.skip("patch não se aplica a esta versão do WeasyPrint")
    monkeypatch.setattr(block, "_linebox_layout", block._ocrpdf_original)
    monkeypatch.setattr(render, "_keep_footnotes_on_call_page", lambda: None)
    out = tmp_path / "out.pdf"
    render.render_pdf(_doc_with_many_notes(), out, base_dir=tmp_path)
    assert [p for p in _note_pages(out) if p[1] != p[2]] != []
