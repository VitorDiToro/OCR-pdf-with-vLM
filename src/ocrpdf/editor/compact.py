"""Forma compacta de um bloco para o editor: só o texto e o que foge do padrão."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from ocrpdf.model import Block, BlockStyle, Span

EDITABLE_STYLE = (
    "align", "indent_em", "font_size_pt", "page_break_before",
    "space_before_lines", "hidden", "bold", "italic",
)
_MARKS = ("italic", "bold", "sup", "sub", "math")
_ALIGNS = ("justify", "left", "center", "right")
_DEFAULTS = BlockStyle()


def to_compact(b: Block) -> dict:
    out: dict = {"kind": b.kind}
    spans = []
    for s in b.spans:
        d: dict = {"text": s.text}
        for m in _MARKS:
            if getattr(s, m):
                d[m] = True
        if s.note is not None:
            d["note"] = s.note
        spans.append(d)
    if spans:
        out["spans"] = spans
    style = {
        f: getattr(b.style, f) for f in EDITABLE_STYLE
        if getattr(b.style, f) != getattr(_DEFAULTS, f)
    }
    if style:
        out["style"] = style
    return out


def from_compact(original: Block, data: object) -> Block:
    """Reconstrói o bloco completo a partir da forma compacta; ValueError se inválida."""
    if not isinstance(data, dict):
        raise ValueError("o bloco deve ser um objeto JSON")
    _only(data, ("kind", "spans", "style"))
    if data.get("kind") != original.kind:
        raise ValueError(f"não é permitido mudar o tipo do bloco ({original.kind!r})")
    spans = [_span(s) for s in _list(data.get("spans", []), "spans")]
    style = _style(original.style, data.get("style", {}))
    new = deepcopy(original)
    return replace(new, spans=spans, style=style)


def _only(d: dict, allowed: tuple[str, ...]) -> None:
    for k in d:
        if k not in allowed:
            raise ValueError(f"campo desconhecido: {k}")


def _list(v: object, name: str) -> list:
    if not isinstance(v, list):
        raise ValueError(f"{name} deve ser uma lista")
    return v


def _span(d: object) -> Span:
    if not isinstance(d, dict):
        raise ValueError("cada span deve ser um objeto")
    _only(d, ("text", *_MARKS, "note"))
    if not isinstance(d.get("text"), str):
        raise ValueError("span sem text (texto)")
    for m in _MARKS:
        if not isinstance(d.get(m, False), bool):
            raise ValueError(f"{m} deve ser true ou false")
    note = d.get("note")
    if note is not None and not isinstance(note, str):
        raise ValueError("note deve ser texto")
    return Span(d["text"], note=note, **{m: d.get(m, False) for m in _MARKS})


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _style(base: BlockStyle, d: object) -> BlockStyle:
    if not isinstance(d, dict):
        raise ValueError("style deve ser um objeto")
    _only(d, EDITABLE_STYLE)
    v = {f: d.get(f, getattr(_DEFAULTS, f)) for f in EDITABLE_STYLE}
    if v["align"] not in _ALIGNS:
        raise ValueError(f"align deve ser um de {', '.join(_ALIGNS)}")
    if not _is_num(v["indent_em"]):
        raise ValueError("indent_em deve ser número")
    v["indent_em"] = float(v["indent_em"])
    fs = v["font_size_pt"]
    if fs is not None:
        if not _is_num(fs) or fs <= 0:
            raise ValueError("font_size_pt deve ser um número positivo")
        v["font_size_pt"] = float(fs)
    n = v["space_before_lines"]
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise ValueError("space_before_lines deve ser um inteiro não negativo")
    for f in ("page_break_before", "hidden"):
        if not isinstance(v[f], bool):
            raise ValueError(f"{f} deve ser true ou false")
    for f in ("bold", "italic"):
        if v[f] is not None and not isinstance(v[f], bool):
            raise ValueError(f"{f} deve ser true, false ou null")
    return replace(base, **v)
