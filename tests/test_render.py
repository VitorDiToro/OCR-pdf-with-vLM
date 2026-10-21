from pathlib import Path

import pytest
from pypdf import PdfReader

from ocrpdf import render
from ocrpdf.model import Block, Document, Note, PageGeometry, Span

_WORDS = "texto de corpo com palavras de tamanho variado para encher linhas inteiras".split()


def _doc_with_many_notes(spread: int, long_notes: bool) -> Document:
    """Página pequena e 30 parágrafos longos, cada um com uma nota: muitas quebras em páginas
    com notas. `long_notes`: uma nota em cada três tem várias linhas e às vezes não cabe sob a
    chamada. Os parâmetros foram escolhidos para exercitar cada defeito (ver os controles)."""
    doc = Document(page=PageGeometry(300, 300, 30, 30, 30, 30), body_font_pt=10, line_height=1.2,
                   paragraph_gap_pt=4, font_family="sans")
    for i in range(1, 31):
        nid = f"n{i}"
        words = [_WORDS[(i * 7 + k) % len(_WORDS)] for k in range(20 + (i * 13) % spread)]
        doc.blocks.append(Block(kind="paragraph", spans=[
            Span(" ".join(words[:5]) + " "), Span(str(i), sup=True, note=nid), Span(" " + " ".join(words[5:]) + "."),
        ]))
        text = f"Nota {i}, " + ("comentário longo " * 25 if long_notes and i % 3 == 0 else "curta.")
        doc.notes[nid] = Note(id=nid, label=str(i), kind="footnote", page=0,
                              blocks=[Block(kind="paragraph", spans=[Span(text)])])
    return doc


# orphans/widows: o WeasyPrint adia notas já colocadas (corrigido pelo patch)
def _orphans_doc() -> Document:
    return _doc_with_many_notes(spread=60, long_notes=False)


# nota que não cabe sob a chamada (corrigido por footnote-policy: line)
def _overflow_doc() -> Document:
    return _doc_with_many_notes(spread=80, long_notes=True)


def _note_pages(pdf: Path) -> list[tuple[str, int, int]]:
    """(nota, página da chamada, página da nota) pelos destinos nomeados ref-nX e nX."""
    reader = PdfReader(pdf)
    dests = {name: reader.get_destination_page_number(d) + 1 for name, d in reader.named_destinations.items()}
    notes = sorted((k for k in dests if k[0] == "n" and k[1:].isdigit()), key=lambda k: int(k[1:]))
    return [(n, dests["ref-" + n], dests[n]) for n in notes]


def _misplaced(pdf: Path) -> list[tuple[str, int, int]]:
    return [p for p in _note_pages(pdf) if p[1] != p[2]]


def _text_positions(pdf: Path, page: int = 0) -> list[tuple[str, float, float]]:
    """(texto, x, y) em pt de cada trecho de texto da página; y cresce para cima."""
    out = []

    def visitor(text, cm, tm, font_dict, font_size):
        if text.strip():
            out.append((text, tm[4] * cm[0] + tm[5] * cm[2] + cm[4], tm[4] * cm[1] + tm[5] * cm[3] + cm[5]))

    PdfReader(pdf).pages[page].extract_text(visitor_text=visitor)
    return out


@pytest.mark.parametrize("make_doc", [_orphans_doc, _overflow_doc], ids=["orphans", "overflow"])
def test_footnotes_stay_on_the_page_of_their_call(tmp_path: Path, make_doc) -> None:
    out = tmp_path / "out.pdf"
    render.render_pdf(make_doc(), out, base_dir=tmp_path)
    assert len(_note_pages(out)) == 30
    assert _misplaced(out) == []


# Controles: garantem que cada documento de fato exercita o defeito correspondente.

def test_without_line_policy_notes_are_deferred(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """footnote-policy auto: a nota que não cabe sob a chamada vai sozinha para a página seguinte."""
    original = render.render_html

    def with_auto_policy(doc: Document) -> str:
        html = original(doc)
        assert "footnote-policy: line;" in html
        return html.replace("footnote-policy: line;", "footnote-policy: auto;")

    monkeypatch.setattr(render, "render_html", with_auto_policy)
    out = tmp_path / "out.pdf"
    render.render_pdf(_overflow_doc(), out, base_dir=tmp_path)
    assert _misplaced(out) != []


def test_without_patch_weasyprint_defers_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem o patch, o WeasyPrint adia notas já colocadas para respeitar orphans/widows."""
    from weasyprint.layout import block

    render._keep_footnotes_on_call_page()
    if not getattr(block, "_ocrpdf_patched", False):
        pytest.skip("patch não se aplica a esta versão do WeasyPrint")
    monkeypatch.setattr(block, "_linebox_layout", block._ocrpdf_original)
    monkeypatch.setattr(render, "_keep_footnotes_on_call_page", lambda: None)
    out = tmp_path / "out.pdf"
    render.render_pdf(_orphans_doc(), out, base_dir=tmp_path)
    assert _misplaced(out) != []
