"""Monta o `Document` a partir das páginas analisadas (sem I/O; testável com fixtures)."""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import replace

from . import inline, notes
from .htmlsafe import sanitize_table
from .model import Block, BlockStyle, Document, Note, PageGeometry, Rule, Source, Span
from .ocr.base import PageLayout, Region

_ASCENT_PER_EM = 0.72  # altura das maiúsculas/ascendentes por em (Arial 0,72; Times 0,68)
_TERMINAL = re.compile(r"[.!?:;…\"”»)\]]\s*$")
_PAGE_NUMBER = re.compile(r"^\s*(?:p[áa]g(?:ina)?\.?\s*)?(\d{1,4}|[ivxlcdm]{1,7})\s*$", re.I)
_EDGE_NUMBER = re.compile(r"^\s*\d{1,4}\s+|\s+\d{1,4}\s*$")
# início de nota no Markdown bruto: <sup>1</sup>, $^{1}$, ¹, [1], 1., 1), *
_NOTE_LINE = re.compile(
    r"^\s*(?:<sup>\s*[\w*†]{1,3}\s*</sup>|\$\s*\^\s*\{?[\w*†]{1,3}\}?\s*\$|[⁰¹²³⁴⁵⁶⁷⁸⁹]+|\[\d{1,3}\]|\d{1,3}[.)]?\s|\*{1,3}\s|†)"
)


def assemble(pages: list[PageLayout], source: str = "", font_family: str = "serif") -> Document:
    pages = sorted(pages, key=lambda p: p.index)
    geom, content = _page_geometry(pages)
    doc = Document(source=source, page=geom, font_family=font_family)
    _measure_typography(doc, pages)
    doc.columns = _column_count(pages, content)
    _running_header(doc, pages)
    _Builder(doc, content).run(pages)
    _assign_heading_levels(doc)
    notes.link(doc)
    return doc


# --- geometria e tipografia ------------------------------------------------

_BODY = {"text", "title", "section-header", "list-item", "caption", "table", "formula", "picture", "footnote"}


def _bbox_pt(r: Region, page: PageLayout) -> tuple[float, float, float, float]:
    k = page.px_to_pt
    return tuple(v * k for v in r.bbox)  # type: ignore[return-value]


def _page_geometry(pages: list[PageLayout]) -> tuple[PageGeometry, dict[int, tuple[float, float]]]:
    """Margens = mediana das extremidades do conteúdo; devolve também a faixa horizontal por página."""
    first = pages[0]
    lefts, rights, tops, bottoms = [], [], [], []
    content: dict[int, tuple[float, float]] = {}
    for p in pages:
        boxes = [_bbox_pt(r, p) for r in p.regions if r.category in _BODY]
        if not boxes:
            continue
        x1 = min(b[0] for b in boxes)
        x2 = max(b[2] for b in boxes)
        content[p.index] = (x1, x2)
        lefts.append(x1)
        rights.append(p.width_pt - x2)
        tops.append(min(b[1] for b in boxes))
        bottoms.append(p.height_pt - max(b[3] for b in boxes))
    if not lefts:
        return PageGeometry(first.width_pt, first.height_pt), content

    def clamp(v: float) -> float:
        return round(max(18.0, v), 1)

    bottoms.sort()
    geom = PageGeometry(
        width_pt=round(first.width_pt, 2),
        height_pt=round(first.height_pt, 2),
        margin_top_pt=clamp(statistics.median(tops)),
        margin_right_pt=clamp(statistics.median(rights)),
        # páginas cheias definem a margem inferior; a última página costuma ser curta
        margin_bottom_pt=clamp(bottoms[len(bottoms) // 4]),
        margin_left_pt=clamp(statistics.median(lefts)),
    )
    return geom, content


def _font_px(r: Region) -> float | None:
    m = r.metrics
    if not m or m.ascent_px <= 0:
        return None
    return m.ascent_px / _ASCENT_PER_EM


def _measure_typography(doc: Document, pages: list[PageLayout]) -> None:
    sizes, ratios, gaps = [], [], []
    for p in pages:
        prev = None
        for r in p.regions:
            if r.category != "text":
                prev = None
                continue
            fpx = _font_px(r)
            if fpx and r.metrics.lines >= 3:
                sizes.extend([fpx * p.px_to_pt] * r.metrics.lines)
                if r.metrics.line_pitch_px > 0:
                    ratios.append(r.metrics.line_pitch_px / fpx)
            if prev is not None and r.bbox[1] > prev.bbox[3]:
                gaps.append((r.bbox[1] - prev.bbox[3]) * p.px_to_pt)
            prev = r
    if sizes:
        doc.body_font_pt = round(statistics.median(sizes) * 2) / 2
    if ratios:
        doc.line_height = round(min(2.0, max(1.0, statistics.median(ratios))), 2)
    if gaps:
        leading = (doc.line_height - 1) * doc.body_font_pt
        doc.paragraph_gap_pt = round(min(2 * doc.body_font_pt, max(0.0, statistics.median(gaps) - leading)), 1)


def _column_count(pages: list[PageLayout], content: dict[int, tuple[float, float]]) -> int:
    ratios = []
    for p in pages:
        if p.index not in content:
            continue
        x1, x2 = content[p.index]
        width = max(1.0, x2 - x1)
        for r in p.regions:
            if r.category == "text" and r.metrics and r.metrics.lines >= 3:
                b = _bbox_pt(r, p)
                ratios.append((b[2] - b[0]) / width)
    if len(ratios) < 3:
        return 1
    ratio = statistics.median(ratios)
    return 1 if ratio > 0.6 else min(3, max(2, round(1 / ratio)))


def _left_positions(
    pages: list[PageLayout],
    content: dict[int, tuple[float, float]],
    body_font_pt: float,
) -> list[tuple[float, tuple[int, int]]]:
    """Onde cada bloco começa à esquerda: `(deslocamento_pt, (página, região))`.

    O deslocamento é contado a partir da esquerda da área de texto da página. Linha única
    (título, subtítulo, texto) conta pelo início do bbox; texto de várias linhas conta pelo
    início + recuo da 1ª linha. Quem começa a menos de 1 em da margem já é `left` e não entra,
    nem a linha única simétrica na área de texto: títulos centralizados de larguras parecidas
    ("I", "II", "III") começam quase no mesmo x e se apoiariam uns nos outros.
    """
    stops: list[tuple[float, tuple[int, int]]] = []
    for p in pages:
        if p.index not in content:
            continue
        c_left, c_right = content[p.index]
        for i, r in enumerate(p.regions):
            m = r.metrics
            if r.category not in ("title", "section-header", "text") or not m:
                continue
            offset = r.bbox[0] * p.px_to_pt - c_left
            if m.lines > 1:
                if m.first_indent_px <= 0:
                    continue
                offset += m.first_indent_px * p.px_to_pt
            elif _symmetric(offset, c_right - r.bbox[2] * p.px_to_pt, c_right - c_left):
                continue
            if offset >= body_font_pt:
                stops.append((offset, (p.index, i)))
    return stops


def _symmetric(left: float, right: float, width: float) -> bool:
    """Linha única centralizada: folgas parecidas (± 4% da área de texto) até as duas margens."""
    tol = width * 0.04
    return left > tol and right > tol and abs(left - right) < tol


def _running_header(doc: Document, pages: list[PageLayout]) -> None:
    texts: Counter[str] = Counter()
    align: dict[str, str] = {}
    sizes: dict[str, list[float]] = {}
    for p in pages:
        for r in p.regions:
            if r.category == "page-footer" and _PAGE_NUMBER.match(inline_text(r.text)):
                doc.page_numbers = True
            if r.category != "page-header":
                continue
            t = inline_text(r.text)
            if _PAGE_NUMBER.match(t):
                doc.page_numbers = True
                continue
            stripped = _EDGE_NUMBER.sub("", t).strip()
            if stripped != t:
                doc.page_numbers = True
            if not stripped:
                continue
            texts[stripped] += 1
            align[stripped] = _header_align(doc.page, _bbox_pt(r, p))
            if fpx := _font_px(r):
                sizes.setdefault(stripped, []).append(fpx * p.px_to_pt)
    if not texts:
        return
    text, count = texts.most_common(1)[0]
    if count >= max(2, len(pages) * 0.3):
        doc.running_header = text
        doc.running_header_align = align[text]
        doc.header_on_first_page = any(r.category == "page-header" for r in pages[0].regions)
        if text in sizes:
            doc.running_header_font_pt = round(statistics.median(sizes[text]) * 2) / 2


def _header_align(g: PageGeometry, bbox: tuple[float, float, float, float]) -> str:
    """Pela folga até cada margem: o centro do bloco engana quando o texto é longo."""
    left = bbox[0] - g.margin_left_pt
    right = g.width_pt - g.margin_right_pt - bbox[2]
    if abs(left - right) < 0.04 * (g.width_pt - g.margin_left_pt - g.margin_right_pt):
        return "center"
    return "right" if right < left else "left"


def inline_text(markdown: str) -> str:
    return "".join(s.text for s in inline.parse_flat(markdown))


# --- blocos ----------------------------------------------------------------

class _Builder:
    def __init__(self, doc: Document, content: dict[int, tuple[float, float]]) -> None:
        self.doc = doc
        self.content = content
        self.in_endnotes = False
        self.last_note: Note | None = None
        self.prev_body_region: tuple[PageLayout, Region] | None = None
        self.left_stops: list[tuple[float, tuple[int, int]]] = []

    def run(self, pages: list[PageLayout]) -> None:
        self.left_stops = _left_positions(pages, self.content, self.doc.body_font_pt)
        for page in pages:
            for region in page.regions:
                self._region(page, region)

    def _region(self, page: PageLayout, r: Region) -> None:
        cat = r.category
        if cat in ("page-header", "page-footer"):
            return
        src = Source(page=page.index, bbox=tuple(round(v, 1) for v in _bbox_pt(r, page)))
        style = self._style(page, r)

        if cat == "footnote":
            self._notes(r.text, "footnote", page.index, src, style)
            return
        if cat in ("title", "section-header"):
            text = r.text
            spans = inline.parse_flat(text)
            if not spans:
                return
            self.in_endnotes = bool(notes.ENDNOTE_HEADINGS.match("".join(s.text for s in spans)))
            kind = "title" if cat == "title" else "heading"
            block = Block(kind=kind, spans=spans, level=1 if kind == "title" else inline.heading_level(text),
                          style=style, source=src)
            block.style.align = style.align if style.align != "justify" else "left"
            self._append(block)
            return
        if cat in ("text", "list-item") and self.in_endnotes and self._notes(r.text, "endnote", page.index, src, style):
            return
        if cat == "text":
            for i, spans in enumerate(inline.parse(r.text)):
                block = Block(kind="paragraph", spans=spans, style=replace(style) if i else style, source=src)
                if i == 0 and self._continues_previous(page, r, block):
                    continue
                self._append(block)
            self.prev_body_region = (page, r)
            return
        if cat == "list-item":
            spans = inline.parse_flat(r.text)
            if spans:
                self._append(Block(kind="list_item", spans=spans, style=style, source=src))
        elif cat == "caption":
            spans = inline.parse_flat(r.text)
            if spans:
                style.align = "center" if style.align == "justify" and r.metrics and r.metrics.lines == 1 else style.align
                self._append(Block(kind="caption", spans=spans, style=style, source=src))
        elif cat == "table":
            html = sanitize_table(r.text)
            if html:
                self._append(Block(kind="table", html=html, style=style, source=src))
        elif cat == "formula":
            latex = _strip_math_delims(r.text)
            if latex:
                self._append(Block(kind="formula", latex=latex, style=style, source=src))
        elif cat == "picture" and r.image:
            self._append(Block(kind="figure", image=r.image, style=style, source=src))
        self.prev_body_region = None

    def _append(self, block: Block) -> None:
        self.doc.blocks.append(block)
        if block.kind not in ("paragraph",):
            self.prev_body_region = None

    # -- estilo

    def _style(self, page: PageLayout, r: Region) -> BlockStyle:
        doc = self.doc
        k = page.px_to_pt
        x1, _, x2, _ = _bbox_pt(r, page)
        c_left, c_right = self.content.get(page.index, (doc.page.margin_left_pt, doc.page.width_pt - doc.page.margin_right_pt))
        c_width = max(1.0, c_right - c_left)
        style = BlockStyle()
        style.full_width = doc.columns == 1 or (x2 - x1) / c_width > 0.7

        fpx = _font_px(r)
        if fpx:
            size = fpx * k
            # títulos sempre: sem tamanho próprio, o h2/h3 do CSS (1,25em…) deslocaria o recuo
            if r.category in ("title", "section-header") or abs(size - doc.body_font_pt) / doc.body_font_pt > 0.12:
                style.font_size_pt = round(size * 2) / 2
        em_pt = style.font_size_pt or doc.body_font_pt

        m = r.metrics
        if m and m.lines > 1:
            style.align = m.align
            style.indent_em = round(m.first_indent_px * k / em_pt, 2) if m.first_indent_px * k > 0.5 * em_pt else 0.0
        elif style.full_width:
            # linha única: alinhamento relativo à área de texto da página
            left, right = x1 - c_left, c_right - x2
            tol = c_width * 0.04
            if self._on_recurring_left(page, r, left):
                # mesma posição de outros blocos (recuo de parágrafo, subtítulos): é `left`
                style.align = "left"
                style.indent_em = round(left / em_pt, 2)
            elif _symmetric(left, right, c_width):
                style.align = "center"
            elif left > right + tol and right < tol:
                style.align = "right"
            else:
                style.align = "left"
                style.indent_em = round(left / em_pt, 2) if left > 0.5 * em_pt else 0.0

        for rm in r.rules:
            rule = Rule(
                thickness_pt=round(max(0.25, rm.thickness_px * k), 2),
                gap_pt=round(rm.gap_px * k, 2),
                left_em=round(max(0.0, (rm.x1_px * k - c_left) / doc.body_font_pt), 2),   # em do corpo
                right_frac=round(min(1.0, max(0.0, (c_right - rm.x2_px * k) / c_width)), 3),
            )
            if rm.position == "below":
                style.rule_below = rule
            else:
                style.rule_above = rule
        return style

    def _on_recurring_left(self, page: PageLayout, r: Region, left: float) -> bool:
        """Há ≥ 2 outros blocos começando (quase) no mesmo x? Um bloco não apoia a si mesmo."""
        me = next((i for i, o in enumerate(page.regions) if o is r), -1)
        tol = 0.6 * self.doc.body_font_pt
        support = sum(1 for off, key in self.left_stops if key != (page.index, me) and abs(off - left) <= tol)
        return support >= 2

    # -- parágrafos que atravessam páginas/colunas

    def _continues_previous(self, page: PageLayout, r: Region, block: Block) -> bool:
        if self.prev_body_region is None or not self.doc.blocks:
            return False
        prev_page, prev_r = self.prev_body_region
        prev = self.doc.blocks[-1]
        if prev.kind != "paragraph":
            return False
        new_page = prev_page.index != page.index
        new_column = not new_page and r.bbox[1] < prev_r.bbox[1] and r.bbox[0] > prev_r.bbox[2] - 1
        if not (new_page or new_column):
            return False
        text = block.plain_text()
        starts_lower = bool(text) and text[0].islower()
        indented = bool(r.metrics and r.metrics.lines > 1 and r.metrics.first_indent_px > 0)
        if not starts_lower and (_TERMINAL.search(prev.plain_text()) or indented):
            return False
        _join(prev.spans, block.spans)
        return True

    # -- notas

    def _notes(self, markdown: str, kind: str, page: int, src: Source, style: BlockStyle) -> bool:
        """Cria notas a partir de uma região. Devolve False se nada foi tratado como nota."""
        handled = False
        for chunk in _split_note_lines(markdown):
            spans = inline.parse_flat(chunk)
            if not spans:
                continue
            label, rest = notes.split_label(spans)
            nstyle = replace(style, indent_em=0.0, align="justify")
            if label is not None:
                nid = f"n{len(self.doc.notes) + 1}"
                note = Note(id=nid, label=label, kind=kind, page=page,
                            blocks=[Block(kind="paragraph", spans=rest, style=nstyle, source=src)])
                self.doc.notes[nid] = note
                if kind == "endnote":
                    self.doc.blocks.append(Block(kind="endnote", note=nid, style=nstyle, source=src))
                self.last_note = note
                handled = True
            elif self.last_note is not None and self.last_note.kind == kind:
                last = self.last_note.blocks[-1]
                text = "".join(s.text for s in rest)
                if (text[:1].islower()) or not _TERMINAL.search(last.plain_text()):
                    _join(last.spans, rest)
                else:
                    self.last_note.blocks.append(Block(kind="paragraph", spans=rest, style=nstyle, source=src))
                handled = True
            elif kind == "footnote":
                # nota sem rótulo e sem nota anterior: mantém como nota não numerada
                nid = f"n{len(self.doc.notes) + 1}"
                self.last_note = self.doc.notes[nid] = Note(
                    id=nid, label="", kind=kind, page=page,
                    blocks=[Block(kind="paragraph", spans=rest, style=nstyle, source=src)])
                handled = True
        return handled


def _split_note_lines(markdown: str) -> list[str]:
    chunks: list[list[str]] = []
    for line in markdown.splitlines():
        if not line.strip():
            if chunks and chunks[-1]:
                chunks.append([])
            continue
        if _NOTE_LINE.match(line) or not chunks:
            chunks.append([line])
        else:
            chunks[-1].append(line)
    return ["\n".join(c) for c in chunks if c]


def _join(dst: list[Span], src: list[Span]) -> None:
    """Concatena `src` em `dst`, desfazendo a hifenização de fim de linha/página."""
    if not src:
        return
    if dst and re.search(r"\w-$", dst[-1].text) and src[0].text[:1].islower():
        dst[-1] = replace(dst[-1], text=dst[-1].text[:-1])
    elif dst:
        dst.append(Span(" "))
    dst.extend(src)


def _strip_math_delims(text: str) -> str:
    t = text.strip()
    for a, b in (("$$", "$$"), ("\\[", "\\]"), ("$", "$"), ("\\(", "\\)")):
        if t.startswith(a) and t.endswith(b) and len(t) > len(a) + len(b):
            return t[len(a):-len(b)].strip()
    return t


def _assign_heading_levels(doc: Document) -> None:
    """Níveis pelos tamanhos de fonte medidos (maior = nível 2); senão, o '#' do Markdown."""
    headings = [b for b in doc.blocks if b.kind == "heading"]
    if not any(b.style.font_size_pt for b in headings):
        for b in headings:
            b.level = min(6, max(2, b.level or 2))
        return
    # agrupa tamanhos próximos (ruído de medição < 1 pt) num mesmo nível
    tiers: list[float] = []
    for size in sorted({b.style.font_size_pt or doc.body_font_pt for b in headings}, reverse=True):
        if not tiers or tiers[-1] - size >= 1.0:
            tiers.append(size)
    for b in headings:
        size = b.style.font_size_pt or doc.body_font_pt
        b.level = min(6, 2 + next(i for i, t in enumerate(tiers) if t - size < 1.0))
