import json
import os

import pytest

from ocrpdf.editor.session import ConflictError, EditError, EditSession, output_scale
from ocrpdf.model import Block, BlockStyle, Document, OutputFormat, Source, Span


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
    s.apply_page(1, s.page_text(1))
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
