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
            align="center", indent_em=1.5, font_size_pt=12.0, full_width=False,
            rule_below=Rule(0.5, 3.0, 1.0, 0.25), page_break_before=True,
            space_before_lines=2, hidden=True, bold=True, italic=False,
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
    ({"kind": "paragraph", "style": {"space_before_lines": -1}}, "space_before_lines"),
    ({"kind": "paragraph", "style": {"hidden": "sim"}}, "hidden"),
    ({"kind": "paragraph", "spans": [{"italic": True}]}, "text"),
    ([1, 2], "objeto"),
])
def test_rejects(data, msg):
    with pytest.raises(ValueError, match=msg):
        from_compact(_para(), data)
