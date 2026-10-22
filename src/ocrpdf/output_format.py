"""Formato de saída aplicado na renderização: tamanho de página, margens e tamanho da fonte.

O `document.json` guarda sempre o formato original medido; aqui se produz uma cópia ajustada.
"""

from __future__ import annotations

import copy
import re

from .model import Block, Document, OutputFormat, PageGeometry

_PT_PER_CM = 72 / 2.54
_A4_PT = (595.28, 841.89)
_MIN_CM, _MAX_CM = 5.0, 100.0
_MIN_TEXT_CM = 3.0     # área de texto mínima em cada direção
_SIZE_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*x\s*(\d+(?:[.,]\d+)?)$", re.IGNORECASE)


def parse_page_size(spec: str) -> tuple[float, float] | None:
    """"original" → None; "a4" → A4; "<largura>x<altura>" em cm → pt (aceita vírgula decimal)."""
    s = spec.strip().lower()
    if s == "original":
        return None
    if s == "a4":
        return _A4_PT
    m = _SIZE_RE.match(s)
    if not m:
        raise ValueError(f'tamanho de página inválido: "{spec}" (use original, a4 ou <largura>x<altura> em cm)')
    w_cm, h_cm = (float(g.replace(",", ".")) for g in m.groups())
    for v in (w_cm, h_cm):
        if not _MIN_CM <= v <= _MAX_CM:
            raise ValueError(f"tamanho de página fora do intervalo de {_MIN_CM:g} a {_MAX_CM:g} cm: {spec}")
    return round(w_cm * _PT_PER_CM, 2), round(h_cm * _PT_PER_CM, 2)


def apply_output_format(doc: Document, page_size: tuple[float, float] | None, margin_cm: float = 2.5,
                        font_size_pt: float | None = None) -> Document:
    """Devolve uma cópia de `doc` com a página e/ou as fontes ajustadas (o original não muda)."""
    out = copy.deepcopy(doc)
    if page_size is not None:
        w, h = page_size
        m = margin_cm * _PT_PER_CM
        if margin_cm < 0 or min(w - 2 * m, h - 2 * m) < _MIN_TEXT_CM * _PT_PER_CM:
            raise ValueError(f"margem de {margin_cm:g} cm deixa menos de {_MIN_TEXT_CM:g} cm de área de texto "
                             f"na página de {w / _PT_PER_CM:.1f} x {h / _PT_PER_CM:.1f} cm")
        out.page = PageGeometry(w, h, m, m, m, m)
    if font_size_pt is not None:
        if font_size_pt <= 0:
            raise ValueError("o tamanho da fonte deve ser positivo")
        _scale_fonts(out, font_size_pt / doc.body_font_pt)
    return out


def apply_format(doc: Document, fmt: OutputFormat) -> Document:
    """Como `apply_output_format`, a partir do formato gravado; `header=False` tira o cabeçalho corrente."""
    out = apply_output_format(doc, parse_page_size(fmt.page_size), fmt.margin_cm, fmt.font_size_pt)
    if not fmt.header:
        out.running_header = ""
    return out


def _scale_fonts(doc: Document, k: float) -> None:
    doc.body_font_pt *= k
    doc.paragraph_gap_pt *= k
    if doc.running_header_font_pt is not None:
        doc.running_header_font_pt *= k
    blocks: list[Block] = list(doc.blocks)
    for note in doc.notes.values():
        blocks.extend(note.blocks)
    for b in blocks:
        if b.style.font_size_pt is not None:
            b.style.font_size_pt *= k
        for rule in (b.style.rule_above, b.style.rule_below):
            if rule is not None:
                rule.gap_pt *= k
