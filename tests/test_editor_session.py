import json
import os

import pytest

from ocrpdf.editor.session import SPLIT_MARK, ConflictError, EditError, EditSession, output_scale
from ocrpdf.model import Block, BlockStyle, Document, OutputFormat, Rule, Source, Span


def _block(text: str, page: int, kind: str = "paragraph", **style) -> Block:
    return Block(kind=kind, spans=[Span(text)], style=BlockStyle(**style), source=Source(page=page))


def _make(tmp_path, **kw) -> tuple[EditSession, object]:
    doc = Document(body_font_pt=kw.get("body", 10.0))
    doc.blocks = [
        _block("t0", 0, "title", **({"font_size_pt": kw["title"]} if "title" in kw else {})),
        _block("a0", 0),
        _block("a1", 1),
        _block("b1", 1),
        _block("a2", 2),
    ]
    path = tmp_path / "document.json"
    doc.save(path)
    return EditSession(path), path


def test_pages_lists_original_pages(tmp_path):
    s, _ = _make(tmp_path)
    assert s.pages() == [0, 1, 2]
    assert s.page_indices(1) == [2, 3]


def test_page_text_has_only_that_page(tmp_path):
    s, _ = _make(tmp_path)
    data = json.loads(s.page_text(1))
    assert [b["spans"][0]["text"] for b in data] == ["a1", "b1"]


def test_apply_page_changes_memory_not_disk(tmp_path):
    s, path = _make(tmp_path)
    before = path.read_bytes()
    data = json.loads(s.page_text(1))
    data[0]["spans"][0]["text"] = "mudou"
    s.apply_page(1, json.dumps(data))
    assert s.dirty
    assert path.read_bytes() == before
    s.page_text(2)
    assert "mudou" in s.page_text(1)


def test_apply_page_rejects_and_keeps_memory(tmp_path):
    s, _ = _make(tmp_path)
    before = s.doc.blocks[:]
    good = json.loads(s.page_text(1))
    with pytest.raises(EditError, match="linha"):
        s.apply_page(1, '[{"kind": "paragraph"} {"kind": "paragraph"}]')
    with pytest.raises(EditError, match="use Ocultar"):
        s.apply_page(1, json.dumps(good[:1]))
    bad = [good[0], {"kind": "paragraph", "spans": [{"texto": "x"}]}]
    with pytest.raises(EditError, match="^bloco 2:"):
        s.apply_page(1, json.dumps(bad))
    assert s.doc.blocks == before
    assert not s.dirty


def test_save_backup_atomic_and_conflict(tmp_path):
    s, path = _make(tmp_path)
    original = path.read_text(encoding="utf-8")
    data = json.loads(s.page_text(0))
    data[1]["spans"][0]["text"] = "novo"
    s.apply_page(0, json.dumps(data))
    s.save()
    orig = tmp_path / "document.orig.json"
    assert orig.read_text(encoding="utf-8") == original
    assert not s.dirty
    assert "novo" in path.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))

    data[1]["spans"][0]["text"] = "outro"
    s.apply_page(0, json.dumps(data))
    s.save()
    assert orig.read_text(encoding="utf-8") == original

    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    os.utime(path, ns=(1, path.stat().st_mtime_ns + 10**9))
    changed = path.read_bytes()
    data[1]["spans"][0]["text"] = "terceiro"
    s.apply_page(0, json.dumps(data))
    with pytest.raises(ConflictError):
        s.save()
    assert path.read_bytes() == changed


def test_justify_all_only_paragraphs(tmp_path):
    s, _ = _make(tmp_path)
    s.doc.blocks[1].style.align = "left"
    s.doc.blocks[2].style.align = "center"
    s.doc.blocks[0].style.align = "center"
    assert s.justify_all() == 2
    assert [b.style.align for b in s.doc.blocks[:3]] == ["center", "justify", "justify"]
    assert s.dirty


def test_font_pt_in_output_scale(tmp_path):
    s, _ = _make(tmp_path, body=51.5, title=127.5)
    fmt = OutputFormat(font_size_pt=12.4)
    assert output_scale(s.doc, fmt) == pytest.approx(12.4 / 51.5)
    rows = s.page_blocks(0, fmt)
    assert rows[0]["font_pt"] == pytest.approx(30.7, abs=0.05)
    assert rows[0]["n"] == 1 and rows[1]["n"] == 2 and rows[1]["index"] == 1
    assert rows[0]["kind"] == "title" and rows[0]["hidden"] is False


def test_format_roundtrip_and_validation(tmp_path):
    s, _ = _make(tmp_path)
    assert s.format() == OutputFormat()
    s.set_format(OutputFormat(page_size="A4"))
    assert s.dirty and s.format().page_size == "A4"
    with pytest.raises(EditError):
        s.set_format(OutputFormat(page_size="XYZ"))
    assert s.format().page_size == "A4"


def test_reload_reads_disk_and_discards_memory(tmp_path):
    s, path = _make(tmp_path)
    data = json.loads(s.page_text(1))
    data[0]["spans"][0]["text"] = "em memória"
    s.apply_page(1, json.dumps(data))
    other = Document.load(path)
    other.blocks[4].spans[0].text = "de fora"
    other.save(path)
    os.utime(path, ns=(1, path.stat().st_mtime_ns + 10**9))
    with pytest.raises(ConflictError):
        s.save()
    s.reload()
    assert not s.dirty
    assert "de fora" in s.page_text(2) and "em memória" not in s.page_text(1)
    s.apply_page(1, json.dumps(data))
    s.save()   # sem conflito: a base agora é o arquivo novo
    back = Document.load(path)
    assert back.blocks[4].spans[0].text == "de fora" and back.blocks[2].spans[0].text == "em memória"


def test_reload_failure_keeps_the_session(tmp_path):
    s, path = _make(tmp_path)
    s.apply_page(1, s.page_text(1).replace('"a1"', '"mudou"'))
    before = s.doc
    path.write_text("{ quebrado", encoding="utf-8")
    with pytest.raises(EditError, match="document.json"):
        s.reload()
    assert s.doc is before and s.dirty


def test_open_missing_or_broken_document_is_edit_error(tmp_path):
    with pytest.raises(EditError, match="não encontrado"):
        EditSession(tmp_path / "nada.json")
    (tmp_path / "ruim.json").write_text("[1, 2", encoding="utf-8")
    with pytest.raises(EditError, match="ruim.json"):
        EditSession(tmp_path / "ruim.json")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
@pytest.mark.parametrize("field", ["indent_em", "font_size_pt"])
def test_nan_and_infinity_in_blocks_are_refused(tmp_path, field, value):
    s, path = _make(tmp_path)
    text = s.page_text(1).replace('"kind": "paragraph"', f'"kind": "paragraph", "style": {{"{field}": {value}}}', 1)
    with pytest.raises(EditError):
        s.apply_page(1, text)
    assert not s.dirty and "style" not in s.page_text(1)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["margin_cm", "font_size_pt"])
def test_nan_and_infinity_in_format_are_refused(tmp_path, field, value):
    s, path = _make(tmp_path)
    with pytest.raises(EditError):
        s.set_format(OutputFormat(page_size="a4", **{field: value}))
    assert s.format() == OutputFormat() and not s.dirty


def test_save_of_a_deleted_document_is_a_conflict(tmp_path):
    s, path = _make(tmp_path)
    path.unlink()
    with pytest.raises(ConflictError, match="apagado ou renomeado"):
        s.save()
    assert not path.exists()


def test_backup_takes_the_name_of_the_document(tmp_path):
    doc = Document(blocks=[_block("x", 0)])
    path = tmp_path / "a.json"
    doc.save(path)
    original = path.read_bytes()
    s = EditSession(path)
    s.save()
    assert (tmp_path / "a.orig.json").read_bytes() == original
    assert not (tmp_path / "document.orig.json").exists()


def test_applying_the_same_text_or_format_does_not_dirty(tmp_path):
    s, _ = _make(tmp_path)
    s.apply_page(1, s.page_text(1))
    s.set_format(s.format())
    assert not s.dirty
    s.set_format(OutputFormat(page_size="a4"))
    assert s.dirty


@pytest.mark.parametrize("output", [{"font_size_pt": "12"}, {"margin_cm": None}, {"page_size": 5}])
def test_invalid_saved_format_is_refused_on_open_and_reload(tmp_path, output):
    s, path = _make(tmp_path)
    data = json.loads(path.read_text())
    data["output"] = {**{"page_size": "a4", "margin_cm": 2, "font_size_pt": None, "header": True}, **output}
    path.write_text(json.dumps(data))
    with pytest.raises(EditError, match=r"formato gravado \(output\) inválido"):
        s.reload()
    assert s.doc.output is None                    # a sessão anterior fica
    with pytest.raises(EditError, match=r"formato gravado \(output\) inválido"):
        EditSession(path)


# --- dividir bloco ----------------------------------------------------------------------------

def _cover(tmp_path) -> tuple[EditSession, object]:
    """Capa: um título com filetes, quebra, espaço e nota sem chamada; e um parágrafo."""
    rule = Rule(1.0, 2.0, 0.0, 1.0)
    title = Block(
        kind="title", level=1,
        spans=[Span("CONTRIBUIÇÃO À CRÍTICA DA ECONOMIA POLÍTICA "), Span("Tradução", italic=True)],
        style=BlockStyle(align="center", font_size_pt=20.0, page_break_before=True, space_before_lines=2,
                         rule_above=rule, rule_below=rule),
        trailing_notes=["n1"], source=Source(page=0, bbox=(1, 2, 3, 4)),
    )
    doc = Document(blocks=[title, _block("texto", 0), _block("outra página", 1)])
    path = tmp_path / "document.json"
    doc.save(path)
    return EditSession(path), path


def _marked(s: EditSession, page: int, before: str) -> str:
    """Texto da página com a marca de divisão antes da 1ª ocorrência de `before`."""
    text = s.page_text(page)
    return text.replace(before, SPLIT_MARK + before, 1)


def test_split_block_at_the_mark(tmp_path):
    s, path = _cover(tmp_path)
    disk = path.read_bytes()
    assert s.split_block(0, _marked(s, 0, "DA ECONOMIA")) == 1
    a, b = s.doc.blocks[0], s.doc.blocks[1]
    assert a.spans == [Span("CONTRIBUIÇÃO À CRÍTICA")]
    assert b.spans == [Span("DA ECONOMIA POLÍTICA "), Span("Tradução", italic=True)]
    assert (a.kind, b.kind, a.level, b.level) == ("title", "title", 1, 1)
    assert a.source == b.source == Source(page=0, bbox=(1, 2, 3, 4))
    # o que vem "antes" fica no 1º; o que vem "depois", no 2º; o resto do estilo vale para os dois
    assert (a.style.page_break_before, a.style.space_before_lines) == (True, 2)
    assert (b.style.page_break_before, b.style.space_before_lines) == (False, 0)
    assert a.style.rule_above and not a.style.rule_below
    assert b.style.rule_below and not b.style.rule_above
    assert a.style.font_size_pt == b.style.font_size_pt == 20.0 and b.style.align == "center"
    assert (a.trailing_notes, b.trailing_notes) == ([], ["n1"])
    assert len(s.doc.blocks) == 4 and s.page_indices(0) == [0, 1, 2]
    assert s.dirty and path.read_bytes() == disk


def test_split_at_a_span_boundary_and_with_other_edits(tmp_path):
    s, _ = _cover(tmp_path)
    text = _marked(s, 0, "Tradução").replace('"texto"', '"texto editado"')
    assert s.split_block(0, text) == 1
    assert s.doc.blocks[0].spans == [Span("CONTRIBUIÇÃO À CRÍTICA DA ECONOMIA POLÍTICA")]
    assert s.doc.blocks[1].spans == [Span("Tradução", italic=True)]
    assert s.doc.blocks[2].spans == [Span("texto editado")]   # o resto da página também é aplicado


def test_split_then_save_and_reopen(tmp_path):
    s, path = _cover(tmp_path)
    s.split_block(0, _marked(s, 0, "DA ECONOMIA"))
    s.save()
    again = EditSession(path)
    assert [b.plain_text() for b in again.doc.blocks][:2] == ["CONTRIBUIÇÃO À CRÍTICA", "DA ECONOMIA POLÍTICA Tradução"]
    assert len(json.loads(again.page_text(0))) == 3


@pytest.mark.parametrize("make_text, message", [
    (lambda t: t, "cursor"),                                                     # sem marca
    (lambda t: t.replace("DA", SPLIT_MARK + "DA").replace("texto", SPLIT_MARK + "texto"), "uma vez"),
    (lambda t: t.replace("CONTRIBUIÇÃO", SPLIT_MARK + "CONTRIBUIÇÃO"), "começo ou no fim"),
    (lambda t: t.replace("Tradução", "Tradução" + SPLIT_MARK), "começo ou no fim"),
    (lambda t: t.replace("[", "[" + SPLIT_MARK, 1), "JSON"),                     # fora de uma string
])
def test_split_refused_leaves_the_session_as_it_was(tmp_path, make_text, message):
    s, _ = _cover(tmp_path)
    before = [b.plain_text() for b in s.doc.blocks]
    with pytest.raises(EditError, match=message):
        s.split_block(0, make_text(s.page_text(0)))
    assert [b.plain_text() for b in s.doc.blocks] == before and not s.dirty


def test_split_refuses_inside_a_formula_or_a_note_call(tmp_path):
    s, _ = _cover(tmp_path)
    s.doc.blocks[1].spans = [Span("antes "), Span("x+y", math=True), Span("12", sup=True, note="n1"), Span(" depois")]
    for target in ("+y", '2"'):
        with pytest.raises(EditError, match="fórmula|chamada"):
            s.split_block(0, _marked(s, 0, target))
    assert len(s.doc.blocks) == 3


def test_split_only_text_blocks(tmp_path):
    s, _ = _cover(tmp_path)
    s.doc.blocks[1] = Block(kind="table", spans=[Span("célula")], source=Source(page=0))
    with pytest.raises(EditError, match="texto"):
        s.split_block(0, _marked(s, 0, "lula"))
