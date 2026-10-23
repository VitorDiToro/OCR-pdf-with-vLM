import json
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

    def with_auto_policy(doc: Document, **kwargs) -> str:
        html = original(doc, **kwargs)
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


def _two_parts(columns: int, break_at: int | None) -> Document:
    blocks = [Block(kind="heading", level=2, spans=[Span("Primeira parte")]),
              Block(kind="paragraph", spans=[Span("Texto curto da primeira parte.")]),
              Block(kind="heading", level=2, spans=[Span("Segunda parte")]),
              Block(kind="paragraph", spans=[Span("Texto curto da segunda parte.")])]
    for b in blocks:
        b.style.full_width = columns == 1
    if break_at is not None:
        blocks[break_at].style.page_break_before = True
    return Document(page=PageGeometry(300, 400, 30, 30, 30, 30), body_font_pt=10, line_height=1.2,
                    columns=columns, blocks=blocks)


@pytest.mark.parametrize("columns", [1, 2])
def test_page_break_before_starts_the_block_on_a_new_page(tmp_path: Path, columns: int) -> None:
    # edição manual no document.json: "page_break_before": true no style do bloco
    out = tmp_path / "out.pdf"
    render.render_pdf(_two_parts(columns, None), out, base_dir=tmp_path)
    assert len(PdfReader(out).pages) == 1                      # controle: cabe tudo numa página
    render.render_pdf(_two_parts(columns, 2), out, base_dir=tmp_path)
    first, second = (p.extract_text() for p in PdfReader(out).pages)
    assert "Primeira parte" in first and "Segunda" not in first
    assert second.lstrip().startswith("Segunda parte")


def test_page_break_before_the_first_block_adds_no_blank_page(tmp_path: Path) -> None:
    out = tmp_path / "out.pdf"
    render.render_pdf(_two_parts(1, 0), out, base_dir=tmp_path)
    assert len(PdfReader(out).pages) == 1


def test_page_break_before_survives_save_and_old_files_load_without_it(tmp_path: Path) -> None:
    path = tmp_path / "document.json"
    _two_parts(1, 2).save(path)
    assert [b.style.page_break_before for b in Document.load(path).blocks] == [False, False, True, False]
    data = json.loads(path.read_text())
    for b in data["blocks"]:
        del b["style"]["page_break_before"]                   # document.json de antes do campo
    path.write_text(json.dumps(data))
    assert not any(b.style.page_break_before for b in Document.load(path).blocks)


# --- ocultar bloco, linhas em branco, negrito/itálico do bloco ---------------

def _page_text(pdf: Path) -> str:
    return "".join(p.extract_text() for p in PdfReader(pdf).pages)


def test_hidden_block_is_not_rendered(tmp_path: Path) -> None:
    doc = _two_parts(1, None)
    doc.blocks[2].style.hidden = True
    out = tmp_path / "out.pdf"
    render.render_pdf(doc, out, base_dir=tmp_path)
    text = _page_text(out)
    assert "Segunda parte" not in text
    assert "Primeira parte" in text and "Texto curto da primeira" in text and "Texto curto da segunda" in text


def test_hidden_block_keeps_its_trailing_notes_and_drops_its_own_calls(tmp_path: Path) -> None:
    doc = _two_parts(1, None)
    doc.notes = {"n1": Note("n1", "1", "footnote", 0, [Block("paragraph", [Span("TextoDaN1")])]),
                 "n2": Note("n2", "2", "footnote", 0, [Block("paragraph", [Span("TextoDaN2")])])}
    hidden = doc.blocks[1]
    hidden.spans = [Span("Texto oculto"), Span("1", sup=True, note="n1")]
    hidden.trailing_notes = ["n2"]
    hidden.style.hidden = True
    out = tmp_path / "out.pdf"
    render.render_pdf(doc, out, base_dir=tmp_path)
    text = _page_text(out)
    assert "TextoDaN2" in text
    assert "TextoDaN1" not in text and "Texto oculto" not in text


def test_space_before_lines_adds_blank_lines(tmp_path: Path) -> None:
    def second_y(lines: int) -> float:
        doc = _two_parts(1, None)
        doc.blocks[3].style.space_before_lines = lines
        out = tmp_path / f"out{lines}.pdf"
        render.render_pdf(doc, out, base_dir=tmp_path)
        return next(y for t, _, y in _text_positions(out) if "da segunda" in t)

    shift = second_y(0) - second_y(3)
    assert shift == pytest.approx(3 * 1.2 * 10, abs=2)


def test_block_bold_and_italic_override() -> None:
    doc = _two_parts(1, None)
    doc.blocks[0].style.bold = False
    doc.blocks[1].style.italic = True
    html = render.render_html(doc)
    assert 'font-weight:normal' in html.split("<h2", 1)[1].split(">", 1)[0]
    assert 'font-style:italic' in html.split("<p", 1)[1].split(">", 1)[0]
    plain = render.render_html(_two_parts(1, None))
    assert "font-weight" not in plain.split("<body>", 1)[1]
    assert "font-style" not in plain.split("<body>", 1)[1]


def _body_html(doc: Document) -> str:
    return render.render_html(doc).split("<body>", 1)[1]


def test_block_override_false_also_removes_the_marks_of_the_spans() -> None:
    doc = _two_parts(1, None)
    doc.blocks[0].spans = [Span("Primeira "), Span("parte", bold=True, italic=True)]
    doc.blocks[1].spans = [Span("Texto "), Span("todo em itálico", italic=True), Span("1", sup=True, italic=True)]
    plain = _body_html(doc)
    assert "<b><i>parte</i></b>" in plain and "<i>todo em itálico</i>" in plain   # padrão: as marcas valem

    doc.blocks[1].style.italic = False
    doc.blocks[0].style.bold = False
    html = _body_html(doc)
    p = html.split("<p", 1)[1].split("</p>", 1)[0]
    assert "<i>" not in p and "font-style:normal" in p
    h2 = html.split("<h2", 1)[1].split("</h2>", 1)[0]
    assert "<b>" not in h2 and "font-weight:normal" in h2
    assert "<i>parte</i>" in h2           # só a marca desligada sai; o itálico do trecho fica

    doc.blocks[1].style.italic = True     # ligado: as marcas dos trechos não atrapalham
    assert "<i>todo em itálico</i>" in _body_html(doc)


def test_block_override_false_spares_the_notes_of_the_block() -> None:
    doc = _two_parts(1, None)
    doc.notes = {"n1": Note("n1", "1", "footnote", 0, [Block("paragraph", [Span("Obra", italic=True)])])}
    doc.blocks[1].spans = [Span("Texto", italic=True), Span("1", sup=True, note="n1")]
    doc.blocks[1].style.italic = False
    html = _body_html(doc)
    assert "<i>Texto</i>" not in html and "<i>Obra</i>" in html


def test_new_fields_survive_save_and_old_files_load(tmp_path: Path) -> None:
    from ocrpdf.model import OutputFormat

    doc = _two_parts(1, None)
    st = doc.blocks[1].style
    st.hidden, st.space_before_lines, st.bold, st.italic = True, 2, True, False
    doc.output = OutputFormat(page_size="A4", margin_cm=2.0, font_size_pt=12.0, header=False)
    doc.source_path = "/tmp/x.pdf"
    path = tmp_path / "document.json"
    doc.save(path)
    back = Document.load(path)
    st = back.blocks[1].style
    assert (st.hidden, st.space_before_lines, st.bold, st.italic) == (True, 2, True, False)
    assert back.output == doc.output and back.source_path == "/tmp/x.pdf"

    data = json.loads(path.read_text())
    for key in ("output", "source_path"):
        del data[key]
    for b in data["blocks"]:
        for key in ("hidden", "space_before_lines", "bold", "italic"):
            del b["style"][key]
    path.write_text(json.dumps(data))
    old = Document.load(path)
    st = old.blocks[1].style
    assert (st.hidden, st.space_before_lines, st.bold, st.italic) == (False, 0, None, None)
    assert old.output is None and old.source_path == ""


def test_hidden_block_passes_its_page_break_to_the_next_visible_block(tmp_path: Path) -> None:
    # ocultar o título que abre a 2ª parte não pode levar junto a quebra de página
    doc = _two_parts(1, 2)
    doc.blocks[2].style.hidden = True
    doc.blocks[2].style.space_before_lines = 3              # o espaço do oculto vai com ele
    out = tmp_path / "out.pdf"
    render.render_pdf(doc, out, base_dir=tmp_path)
    first, second = (p.extract_text() for p in PdfReader(out).pages)
    assert "Segunda parte" not in first + second and "segunda" not in first
    assert second.lstrip().startswith("Texto curto da segunda")
    assert 'class="space"' not in _body_html(doc)
    # oculto com quebra antes do 1º bloco visível (e com nota sem chamada): nada antes do 1º bloco
    doc = _two_parts(1, 0)
    doc.notes = {"n1": Note("n1", "1", "footnote", 0, [Block("paragraph", [Span("TextoDaN1")])])}
    doc.blocks[0].style.hidden = True
    doc.blocks[0].trailing_notes = ["n1"]
    assert "page-break" not in _body_html(doc)


def test_space_before_lines_in_a_column_stays_inside_the_columns() -> None:
    doc = Document(columns=2, blocks=[Block(kind="paragraph", spans=[Span(f"Parágrafo {i}.")]) for i in range(4)])
    for b in doc.blocks:
        b.style.full_width = False
    doc.blocks[2].style.space_before_lines = 2
    html = _body_html(doc)
    assert html.count('class="cols"') == 1
    cols = html.split('class="cols"', 1)[1]
    assert cols.index("Parágrafo 1.") < cols.index('class="space"') < cols.index("Parágrafo 2.")


def test_space_before_the_first_column_block_stays_outside_the_columns() -> None:
    # dentro do div.cols, o espaço no topo empurraria só a 1ª coluna
    doc = Document(columns=2, blocks=[Block(kind="title", spans=[Span("Título")])]
                   + [Block(kind="paragraph", spans=[Span(f"Parágrafo {i}.")]) for i in range(4)])
    for b in doc.blocks[1:]:
        b.style.full_width = False
    doc.blocks[1].style.space_before_lines = 1
    html = _body_html(doc)
    assert html.count('class="cols"') == 1
    assert html.index("Título") < html.index('class="space"') < html.index('class="cols"')


def test_newline_in_a_span_breaks_the_line() -> None:
    # o OCR nunca deixa "\n" nos trechos (inline.parse troca quebras por espaço): só o usuário o escreve
    doc = _two_parts(1, None)
    doc.blocks[1].spans = [Span("CONTRIBUIÇÃO À CRÍTICA\nDA ECONOMIA POLÍTICA\n"),
                           Span("Tradução e\nIntrodução", italic=True)]
    html = _body_html(doc)
    assert "CONTRIBUIÇÃO À CRÍTICA<br>DA ECONOMIA POLÍTICA<br><i>Tradução e<br>Introdução</i>" in html
