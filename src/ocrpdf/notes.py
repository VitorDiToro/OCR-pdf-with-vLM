"""Detecção de rótulos de notas e ligação chamada ↔ nota.

Chamadas reconhecidas no corpo do texto (só são ligadas se existir uma nota com o
mesmo rótulo, o que evita falsos positivos como "[sic]" ou anos entre colchetes):
  - sobrescritos: <sup>1</sup>, $^{1}$, ¹, <sup>*</sup>
  - colchetes:    [1], [12]
  - símbolos colados à palavra: palavra*, palavra**, palavra†
"""

from __future__ import annotations

import re
from dataclasses import replace

from .model import Block, Document, Note, Span

# Rótulo no início de uma nota: "1 ", "1. ", "1) ", "[1] ", "(1) ", "* ", "** ", "† ", "a) "
_NOTE_START = re.compile(
    r"^\s*(?:\[(\d{1,3}|[a-z])\]|\((\d{1,3})\)|(\d{1,3})[.)]?(?=\s)|(\*{1,3}|†{1,2}|‡)|([a-z])\))\s*"
)
_BRACKET_REF = re.compile(r"\[(\d{1,3})\]")
_SYMBOL_REF = re.compile(r"(?<=[\w.,;:!?”\"')\]])(\*{1,3}|†{1,2}|‡)(?=[\s.,;:!?)\]”\"']|$)")
_SUP_LABEL = re.compile(r"^\s*\(?\[?([0-9]{1,3}|[a-z]|\*{1,3}|†{1,2}|‡)\]?\)?\s*$")

ENDNOTE_HEADINGS = re.compile(
    r"^\s*(notas?|notes?|notas de fim|notas finais|endnotes|refer[eê]ncias(?: bibliogr[aá]ficas)?|references|bibliografia)\s*:?\s*$",
    re.I,
)


def split_label(spans: list[Span]) -> tuple[str | None, list[Span]]:
    """Separa o rótulo inicial de uma nota (ex.: "¹ O texto..." → ("1", spans sem o rótulo))."""
    if not spans:
        return None, spans
    first = spans[0]
    if first.sup and (m := _SUP_LABEL.match(first.text)):
        rest = list(spans[1:])
        if rest:
            rest[0] = replace(rest[0], text=rest[0].text.lstrip(" .)"))
        return m.group(1), [s for s in rest if s.text]
    if first.math:
        return None, spans
    m = _NOTE_START.match(first.text)
    if not m:
        return None, spans
    label = next(g for g in m.groups() if g)
    rest = [replace(first, text=first.text[m.end():])] + list(spans[1:])
    return label, [s for s in rest if s.text]


def link(doc: Document) -> None:
    """Marca as chamadas de nota nos spans do corpo e ancora notas sem chamada."""
    index = _NoteIndex(doc.notes.values())
    for block in doc.blocks:
        if block.kind in ("table", "formula", "figure", "endnote"):
            continue
        block.spans = _mark_refs(block.spans, block.source.page, index)
    for note in doc.notes.values():
        for nb in note.blocks:
            # notas que citam outras notas (comum em edições críticas)
            nb.spans = _mark_refs(nb.spans, note.page, index, exclude=note.id)

    # Notas de rodapé sem chamada encontrada: ancoradas no último bloco da sua página,
    # para continuarem aparecendo no pé da página correspondente.
    last_on_page: dict[int, Block] = {}
    for block in doc.blocks:
        last_on_page[block.source.page] = block
    for note in doc.notes.values():
        if note.kind == "footnote" and note.id not in index.used:
            anchor = last_on_page.get(note.page) or _nearest(last_on_page, note.page)
            if anchor is not None:
                anchor.trailing_notes.append(note.id)


class _NoteIndex:
    def __init__(self, notes) -> None:
        self.footnotes: dict[tuple[int, str], list[Note]] = {}
        self.endnotes: dict[str, list[Note]] = {}
        for n in notes:
            key = _norm(n.label)
            if n.kind == "footnote":
                self.footnotes.setdefault((n.page, key), []).append(n)
            else:
                self.endnotes.setdefault(key, []).append(n)
        self.used: set[str] = set()
        self.labels = {k for _, k in self.footnotes} | set(self.endnotes)

    def find(self, label: str, page: int, exclude: str | None = None) -> Note | None:
        key = _norm(label)
        # mesma página primeiro; depois vizinhas (nota que começa na página seguinte)
        for p in (page, page + 1, page - 1):
            if hit := self._pick(self.footnotes.get((p, key), []), exclude):
                return hit
        return self._pick(self.endnotes.get(key, []), exclude)

    def _pick(self, candidates: list[Note], exclude: str | None) -> Note | None:
        candidates = [c for c in candidates if c.id != exclude]
        if not candidates:
            return None
        # numeração que reinicia por capítulo: usa a primeira ainda não referenciada
        for c in candidates:
            if c.id not in self.used:
                return c
        return candidates[0]


def _norm(label: str) -> str:
    return label.strip().strip("[]()").lower()


def _mark_refs(spans: list[Span], page: int, index: _NoteIndex, exclude: str | None = None) -> list[Span]:
    out: list[Span] = []
    for s in spans:
        if s.math or s.note:
            out.append(s)
            continue
        if s.sup:
            m = _SUP_LABEL.match(s.text)
            note = index.find(m.group(1), page, exclude) if m else None
            if note:
                index.used.add(note.id)
                out.append(replace(s, text=note.label, note=note.id))
            else:
                out.append(s)
            continue
        out.extend(_split_inline_refs(s, page, index, exclude))
    return out


def _split_inline_refs(s: Span, page: int, index: _NoteIndex, exclude: str | None) -> list[Span]:
    hits = []
    for rx in (_BRACKET_REF, _SYMBOL_REF):
        for m in rx.finditer(s.text):
            if _norm(m.group(1)) in index.labels:
                hits.append(m)
    if not hits:
        return [s]
    hits.sort(key=lambda m: m.start())
    out, pos = [], 0
    for m in hits:
        if m.start() < pos:
            continue
        note = index.find(m.group(1), page, exclude)
        if not note:
            continue
        index.used.add(note.id)
        if m.start() > pos:
            out.append(replace(s, text=s.text[pos:m.start()]))
        # mantém a aparência original: [1] continua entre colchetes, * continua na linha
        out.append(replace(s, text=m.group(0), note=note.id))
        pos = m.end()
    if pos < len(s.text):
        out.append(replace(s, text=s.text[pos:]))
    return out


def _nearest(blocks_by_page: dict[int, Block], page: int) -> Block | None:
    before = [p for p in blocks_by_page if p <= page]
    return blocks_by_page[max(before)] if before else None
