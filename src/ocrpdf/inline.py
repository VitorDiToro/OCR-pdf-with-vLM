"""Converte o Markdown devolvido pelo modelo em parágrafos de `Span`."""

from __future__ import annotations

import re
from dataclasses import replace

from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin

from .model import Span

_md = MarkdownIt("commonmark", {"html": True}).use(dollarmath_plugin, allow_space=True, double_inline=True)
_md.disable(["code", "fence", "table"])  # indentação/crases em texto OCR não são código

_HEADING = re.compile(r"^\s*(#{1,6})\s+")
_LIST_MARK = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+")
_HTML_TAG = re.compile(r"^<\s*(/?)\s*([a-z0-9]+)[^>]*>$", re.I)
# ^{1}, ^1, ^{*} dentro de $...$  →  sobrescrito simples (chamada de nota)
_SIMPLE_SUP = re.compile(r"^\s*\{?\s*\^\s*\{?\s*([0-9a-zA-Z*†‡]{1,4})\s*\}?\s*\}?\s*$")
_SIMPLE_SUB = re.compile(r"^\s*_\s*\{?\s*([0-9a-zA-Z]{1,4})\s*\}?\s*$")
_UNI_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_UNI_SUP_RUN = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+")


def heading_level(markdown: str) -> int:
    m = _HEADING.match(markdown)
    return len(m.group(1)) if m else 0


def parse(markdown: str, *, strip_list_marker: bool = False) -> list[list[Span]]:
    """Devolve uma lista de parágrafos; cada parágrafo é uma lista de spans."""
    text = _HEADING.sub("", markdown.strip())
    if strip_list_marker:
        text = _LIST_MARK.sub("", text)
    else:
        # "1. Texto" seria lido como lista e perderia o número; o rótulo é conteúdo aqui.
        text = re.sub(r"^(\s*\d{1,3})([.)])(\s)", r"\1\\\2\3", text, flags=re.M)
        text = re.sub(r"^(\s*)([-+*])(\s)", r"\1\\\2\3", text, flags=re.M)
    paragraphs = []
    for tok in _md.parse(text):
        if tok.type == "inline":
            spans = _inline(tok.children or [])
        elif tok.type in ("math_block", "math_block_label"):
            spans = [Span(tok.content.strip(), math=True)]
        elif tok.type == "html_block":
            spans = _inline(_md.parseInline(tok.content)[0].children or [])
        else:
            continue
        spans = _normalize(spans)
        if spans:
            paragraphs.append(spans)
    return paragraphs


def parse_flat(markdown: str, **kw) -> list[Span]:
    """Como `parse`, mas une os parágrafos com espaço (blocos que devem ser um só)."""
    out: list[Span] = []
    for par in parse(markdown, **kw):
        if out:
            out.append(Span(" "))
        out.extend(par)
    return _normalize(out)


def _inline(children) -> list[Span]:
    spans: list[Span] = []
    state = {"italic": 0, "bold": 0, "sup": 0, "sub": 0}

    def emit(text: str, **extra) -> None:
        if text:
            spans.append(Span(
                text,
                italic=state["italic"] > 0,
                bold=state["bold"] > 0,
                sup=state["sup"] > 0 or extra.get("sup", False),
                sub=state["sub"] > 0 or extra.get("sub", False),
                math=extra.get("math", False),
            ))

    html_map = {"i": "italic", "em": "italic", "b": "bold", "strong": "bold", "sup": "sup", "sub": "sub"}
    for t in children:
        kind = t.type
        if kind in ("text", "code_inline"):
            emit(t.content)
        elif kind in ("softbreak", "hardbreak"):
            emit(" ")
        elif kind in ("em_open", "em_close"):
            state["italic"] += 1 if kind.endswith("open") else -1
        elif kind in ("strong_open", "strong_close"):
            state["bold"] += 1 if kind.endswith("open") else -1
        elif kind in ("math_inline", "math_inline_double"):
            if m := _SIMPLE_SUP.match(t.content):
                emit(m.group(1), sup=True)
            elif m := _SIMPLE_SUB.match(t.content):
                emit(m.group(1), sub=True)
            else:
                emit(t.content.strip(), math=True)
        elif kind == "html_inline":
            m = _HTML_TAG.match(t.content.strip())
            if m and m.group(2).lower() in html_map:
                key = html_map[m.group(2).lower()]
                state[key] = max(0, state[key] + (-1 if m.group(1) else 1))
            elif m and m.group(2).lower() == "br":
                emit(" ")
        elif kind == "image":
            emit(t.content)
        # link_open/close e demais: ignorados, o texto interno vem como "text"
    return spans


def _normalize(spans: list[Span]) -> list[Span]:
    """Separa sobrescritos Unicode, junta trechos de mesmo estilo e apara as pontas."""
    split: list[Span] = []
    for s in spans:
        if s.math or not _UNI_SUP_RUN.search(s.text):
            split.append(s)
            continue
        pos = 0
        for m in _UNI_SUP_RUN.finditer(s.text):
            if m.start() > pos:
                split.append(replace(s, text=s.text[pos:m.start()]))
            split.append(replace(s, text=m.group().translate(_UNI_SUP), sup=True))
            pos = m.end()
        if pos < len(s.text):
            split.append(replace(s, text=s.text[pos:]))

    merged: list[Span] = []
    for s in split:
        s = replace(s, text=re.sub(r"\s+", " ", s.text) if not s.math else s.text)
        if merged and _same_style(merged[-1], s) and not s.math:
            merged[-1] = replace(merged[-1], text=merged[-1].text + s.text)
        elif s.text:
            merged.append(s)
    if merged:
        merged[0] = replace(merged[0], text=merged[0].text.lstrip())
        merged[-1] = replace(merged[-1], text=merged[-1].text.rstrip())
    return [s for s in merged if s.text]


def _same_style(a: Span, b: Span) -> bool:
    return (a.italic, a.bold, a.sup, a.sub, a.math, a.note) == (b.italic, b.bold, b.sup, b.sub, b.math, b.note)
