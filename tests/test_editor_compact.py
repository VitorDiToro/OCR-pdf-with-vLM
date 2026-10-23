import json

import pytest

from ocrpdf.editor.compact import from_compact, to_compact
from ocrpdf.model import Block, BlockStyle, Rule, Source, Span


def _para(**style) -> Block:
    return Block(kind="paragraph", spans=[Span("texto")], style=BlockStyle(**style))


def test_roundtrip_keeps_everything():
    b = Block(
        kind="paragraph",
        spans=[
            Span("Disse: “aspas” e acentuação\nnova linha ", italic=True),
            Span("1", sup=True, note="n1"),
        ],
        level=2,
        html="<table></table>",
        latex="x^2",
        image="assets/a.png",
        note="n9",
        trailing_notes=["n2", "n3"],
        style=BlockStyle(
            align="center", indent_em=1.5, block_indent_em=2.0, font_size_pt=12.0, line_height=0.95,
            full_width=False,
            rule_below=Rule(0.5, 3.0, 1.0, 0.25), page_break_before=True,
            space_before_lines=2, space_after_lines=0.5, hidden=True, bold=True, italic=False,
        ),
        source=Source(page=3, bbox=(1.0, 2.0, 3.0, 4.0)),
    )
    data = json.loads(json.dumps(to_compact(b), ensure_ascii=False))
    assert from_compact(b, data) == b


def test_compact_shows_only_what_differs():
    assert to_compact(_para()) == {"kind": "paragraph", "spans": [{"text": "texto"}]}
    c = to_compact(_para(align="left", hidden=True))
    assert c["style"] == {"align": "left", "hidden": True}


def test_missing_field_means_default():
    b = _para(align="left")
    c = to_compact(b)
    del c["style"]["align"]
    assert from_compact(b, c).style.align == "justify"


@pytest.mark.parametrize("data,msg", [
    ({"kind": "title"}, "tipo"),
    ({"kind": "paragraph", "style": {"algin": "left"}}, "campo desconhecido: algin"),
    ({"kind": "paragraph", "style": {"align": "middle"}}, "align"),
    ({"kind": "paragraph", "style": {"font_size_pt": -3}}, "font_size_pt"),
    ({"kind": "paragraph", "style": {"font_size_pt": float("inf")}}, "font_size_pt"),
    ({"kind": "paragraph", "style": {"indent_em": float("nan")}}, "indent_em"),
    ({"kind": "paragraph", "style": {"block_indent_em": -1}}, "block_indent_em"),
    ({"kind": "paragraph", "style": {"line_height": 0}}, "line_height"),
    ({"kind": "paragraph", "style": {"line_height": -1}}, "line_height"),
    ({"kind": "paragraph", "style": {"line_height": "1"}}, "line_height"),
    ({"kind": "paragraph", "style": {"line_height": True}}, "line_height"),
    ({"kind": "paragraph", "style": {"block_indent_em": "2"}}, "block_indent_em"),
    ({"kind": "paragraph", "style": {"block_indent_em": True}}, "block_indent_em"),
    ({"kind": "paragraph", "style": {"space_before_lines": "um"}}, "space_before_lines"),
    ({"kind": "paragraph", "style": {"hidden": "sim"}}, "hidden"),
    ({"kind": "paragraph", "spans": [{"italic": True}]}, "text"),
    ([1, 2], "objeto"),
])
def test_rejects(data, msg):
    with pytest.raises(ValueError, match=msg):
        from_compact(_para(), data)


def test_integer_float_space_before_lines_roundtrips_as_int():
    b = _para(space_before_lines=1.0)                 # document.json editado à mão
    data = json.loads(json.dumps(to_compact(b)))
    back = from_compact(b, data)
    assert back.style.space_before_lines == 1 and type(back.style.space_before_lines) is int
    for field in ("space_before_lines", "space_after_lines"):
        for ok in (1.5, -0.5, -2):                         # negativo aproxima os blocos
            assert getattr(from_compact(b, {"kind": "paragraph", "style": {field: ok}}).style, field) == ok
        for bad in (True, "1", None):
            with pytest.raises(ValueError, match=field):
                from_compact(b, {"kind": "paragraph", "style": {field: bad}})



def _table(**style) -> Block:
    return Block(kind="table", html="<table><tr><td>M392c</td><td>Marx, Karl</td></tr></table>",
                 style=BlockStyle(**style))


def test_table_html_is_editable_and_sanitized():
    b = _table()
    c = to_compact(b)
    assert c == {"kind": "table", "html": b.html}
    assert from_compact(b, c) == b
    c["html"] = '<table><tr><td>M392c</td><td align="right">Marx,<br>Karl<script>x</script></td></tr></table>'
    assert from_compact(b, c).html == '<table><tr><td>M392c</td><td align="right">Marx,<br>Karlx</td></tr></table>'
    for bad in ("só texto", 3, None):
        with pytest.raises(ValueError, match="html"):
            from_compact(b, {"kind": "table", "html": bad})
    with pytest.raises(ValueError, match="campo desconhecido: html"):
        from_compact(_para(), {"kind": "paragraph", "html": "<table></table>"})


def test_table_borders():
    b = _table(table_borders="outer")
    assert to_compact(b)["style"] == {"table_borders": "outer"}
    assert from_compact(b, to_compact(b)) == b
    with pytest.raises(ValueError, match="table_borders"):
        from_compact(b, {"kind": "table", "html": b.html, "style": {"table_borders": "dupla"}})
