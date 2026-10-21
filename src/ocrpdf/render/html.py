"""Document → HTML/CSS pronto para o WeasyPrint."""

from __future__ import annotations

import statistics
from html import escape
from importlib.resources import files

from ..model import Block, Document, Note, Span
from .math import math_img

FONT_STACKS = {
    "serif": '"Liberation Serif", "Times New Roman", "DejaVu Serif", serif',
    "sans": '"Liberation Sans", Arial, Helvetica, "DejaVu Sans", sans-serif',
    "mono": '"Liberation Mono", "Courier New", "DejaVu Sans Mono", monospace',
}


def render_html(doc: Document) -> str:
    return _HtmlRenderer(doc).render()


class _HtmlRenderer:
    def __init__(self, doc: Document) -> None:
        self.doc = doc
        self.emitted: set[str] = set()     # notas de rodapé já posicionadas
        self.ref_count: dict[str, int] = {}
        self.referenced = {s.note for b in doc.blocks for s in b.spans if s.note} | {
            s.note for n in doc.notes.values() for b in n.blocks for s in b.spans if s.note
        }

    def render(self) -> str:
        body: list[str] = []
        group: list[str] = []
        for block in self.doc.blocks:
            html = self._block(block)
            if self.doc.columns > 1 and not block.style.full_width:
                group.append(html)
                continue
            if group:
                body.append(self._columns(group))
                group = []
            body.append(html)
        if group:
            body.append(self._columns(group))

        title = next((b.plain_text() for b in self.doc.blocks if b.kind in ("title", "heading")), "")
        lang = f' lang="{escape(self.doc.lang)}"' if self.doc.lang else ""
        return (
            f"<!doctype html>\n<html{lang}><head><meta charset=\"utf-8\">"
            f"<title>{escape(title)}</title><style>\n{self._css()}\n</style></head>\n"
            f"<body>\n" + "\n".join(body) + "\n</body></html>\n"
        )

    # -- blocos

    def _columns(self, items: list[str]) -> str:
        return f'<div class="cols" style="column-count:{self.doc.columns}">' + "\n".join(items) + "</div>"

    def _block(self, b: Block) -> str:
        style = self._style_attr(b)
        size = b.style.font_size_pt or self.doc.body_font_pt
        if b.kind in ("title", "heading"):
            tag = "h1" if b.kind == "title" else f"h{min(6, max(2, b.level))}"
            inner = f"<{tag}{style}>{self._spans(b.spans, size)}</{tag}>"
        elif b.kind == "paragraph":
            inner = f"<p{style}>{self._spans(b.spans, size)}{self._trailing(b)}</p>"
            return inner
        elif b.kind == "list_item":
            inner = f'<p class="li"{style}>{self._spans(b.spans, size)}{self._trailing(b)}</p>'
            return inner
        elif b.kind == "caption":
            inner = f'<p class="caption"{style}>{self._spans(b.spans, size)}</p>'
        elif b.kind == "table":
            inner = f'<div class="table">{b.html}</div>'
        elif b.kind == "formula":
            inner = f'<div class="formula">{math_img(b.latex, round(self.doc.body_font_pt * 1.1, 1), False)}</div>'
        elif b.kind == "figure":
            x1, _, x2, _ = b.source.bbox
            inner = f'<figure><img src="{escape(b.image)}" style="width:{x2 - x1:.1f}pt"></figure>'
        elif b.kind == "endnote":
            inner = self._endnote(self.doc.notes[b.note]) if b.note in self.doc.notes else ""
        else:
            inner = f"<p{style}>{self._spans(b.spans, size)}</p>"
        trailing = self._trailing(b)
        return inner + (f'<div class="fn-anchor">{trailing}</div>' if trailing else "")

    def _style_attr(self, b: Block) -> str:
        css = []
        st = b.style
        if b.kind in ("paragraph", "list_item", "caption", "title", "heading") and st.align != "justify":
            css.append(f"text-align:{st.align}")
        indent = st.indent_em
        for side, rule in (("top", st.rule_above), ("bottom", st.rule_below)):
            if rule is None:
                continue
            # o filete é a borda do bloco; o texto mantém a posição original via text-indent
            css.append(f"margin-left:{rule.left_em:g}em;margin-right:{rule.right_frac * 100:.1f}%;"
                       f"border-{side}:{rule.thickness_pt:g}pt solid;padding-{side}:{rule.gap_pt:g}pt")
            indent = max(0.0, indent - rule.left_em)
        if indent and st.align in ("justify", "left"):
            css.append(f"text-indent:{indent:g}em")
        if st.font_size_pt:
            css.append(f"font-size:{st.font_size_pt:g}pt")
        return f' style="{";".join(css)}"' if css else ""

    def _trailing(self, b: Block) -> str:
        return "".join(self._footnote(self.doc.notes[n]) for n in b.trailing_notes
                       if n in self.doc.notes and n not in self.emitted)

    # -- trechos e notas

    def _spans(self, spans: list[Span], size_pt: float, allow_float: bool = True) -> str:
        out = []
        for s in spans:
            if s.math:
                out.append(math_img(s.text, size_pt, True))
                continue
            text = escape(s.text, quote=False)
            if s.italic:
                text = f"<i>{text}</i>"
            if s.bold:
                text = f"<b>{text}</b>"
            if s.sup:
                text = f"<sup>{text}</sup>"
            if s.sub:
                text = f"<sub>{text}</sub>"
            if s.note and s.note in self.doc.notes:
                out.append(self._ref(s.note, text, allow_float))
            else:
                out.append(text)
        return "".join(out)

    def _ref(self, nid: str, label_html: str, allow_float: bool) -> str:
        k = self.ref_count.get(nid, 0)
        self.ref_count[nid] = k + 1
        ref_id = f"ref-{nid}" if k == 0 else f"ref-{nid}-{k}"
        html = f'<a class="noteref" id="{ref_id}" href="#{nid}">{label_html}</a>'
        note = self.doc.notes[nid]
        if allow_float and note.kind == "footnote" and nid not in self.emitted:
            html += self._footnote(note)
        return html

    def _note_label(self, note: Note) -> str:
        if not note.label:
            return ""
        label = escape(note.label)
        if note.id in self.referenced:
            return f'<a class="note-label" href="#ref-{note.id}">{label}</a> '
        return f'<span class="note-label">{label}</span> '

    def _note_body(self, note: Note, par_tag: str) -> str:
        size = self._footnote_size() if note.kind == "footnote" else self.doc.body_font_pt
        pars = []
        for i, b in enumerate(note.blocks):
            label = self._note_label(note) if i == 0 else ""
            pars.append(f'<{par_tag} class="note-par">{label}{self._spans(b.spans, size, allow_float=False)}</{par_tag}>')
        return "".join(pars)

    def _footnote(self, note: Note) -> str:
        self.emitted.add(note.id)
        return f'<span class="footnote" id="{note.id}">{self._note_body(note, "span")}</span>'

    def _endnote(self, note: Note) -> str:
        return f'<div class="endnote" id="{note.id}">{self._note_body(note, "p")}</div>'

    def _footnote_size(self) -> float:
        sizes = [b.style.font_size_pt for n in self.doc.notes.values() if n.kind == "footnote"
                 for b in n.blocks if b.style.font_size_pt]
        return statistics.median(sizes) if sizes else round(self.doc.body_font_pt * 0.82 * 2) / 2

    # -- CSS

    def _css(self) -> str:
        d = self.doc
        g = d.page
        family = FONT_STACKS.get(d.font_family, f'"{d.font_family}", serif')
        # margin boxes herdam do elemento raiz, não do body: a família vai no html
        small = round(d.body_font_pt * 0.85, 1)
        header = ""
        if d.running_header:
            content = '"' + d.running_header.replace("\\", "\\\\").replace('"', '\\"') + '"'
            box = {"left": "top-left", "right": "top-right"}.get(d.running_header_align, "top-center")
            header = f"@{box} {{ content: {content}; font-size: {d.running_header_font_pt or small}pt; }}"
        numbers = f"@bottom-center {{ content: counter(page); font-size: {small}pt; }}" if d.page_numbers else ""
        first = "" if d.header_on_first_page or not header else (
            "@page :first { @top-left { content: none } @top-center { content: none } @top-right { content: none } }"
        )
        base = files("ocrpdf.render").joinpath("style.css").read_text(encoding="utf-8")
        return (
            f"@page {{ size: {g.width_pt}pt {g.height_pt}pt; "
            f"margin: {g.margin_top_pt}pt {g.margin_right_pt}pt {g.margin_bottom_pt}pt {g.margin_left_pt}pt; "
            f"{header} {numbers} }}\n{first}\n"
            f":root {{ --body-size: {d.body_font_pt}pt; --line-height: {d.line_height}; "
            f"--par-gap: {d.paragraph_gap_pt}pt; --note-size: {self._footnote_size()}pt; }}\n"
            f"html {{ font-family: {family}; }}\n" + base
        )
