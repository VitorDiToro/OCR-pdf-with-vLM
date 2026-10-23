"""Document → HTML/CSS pronto para o WeasyPrint."""

from __future__ import annotations

import re
import statistics
from html import escape
from importlib.resources import files

from ..model import Block, Document, Note, Rule, Span
from .math import math_img

# tamanho e margens dos títulos em style.css (em do próprio título); o filete colado a um
# título leva a margem dele daquele lado, para o espaço até o bloco vizinho não mudar
_HEADING_EM = {1: 1.6, 2: 1.25, 3: 1.1}
_HEADING_MARGIN_EM = {"top": 1.1, "bottom": 0.45}

_TEXT_KINDS = ("paragraph", "list_item", "caption", "title", "heading")

FONT_STACKS = {
    "serif": '"Liberation Serif", "Times New Roman", "DejaVu Serif", serif',
    "sans": '"Liberation Sans", Arial, Helvetica, "DejaVu Sans", sans-serif',
    "mono": '"Liberation Mono", "Courier New", "DejaVu Sans Mono", monospace',
}


def render_html(doc: Document, block_ids: bool = False, highlight: int | None = None) -> str:
    """`block_ids`: o elemento principal do bloco i recebe id="b{i}"; `highlight`: contorna o bloco."""
    return _HtmlRenderer(doc, block_ids, highlight).render()


def block_font_pt(doc: Document, b: Block) -> float:
    """Tamanho efetivo do bloco: o próprio, o do título (corpo × fator do nível) ou o corpo."""
    if b.style.font_size_pt:
        return b.style.font_size_pt
    if b.kind in ("title", "heading"):
        level = 1 if b.kind == "title" else min(6, max(2, b.level))
        return doc.body_font_pt * _HEADING_EM.get(level, 1.0)
    return doc.body_font_pt


class _HtmlRenderer:
    def __init__(self, doc: Document, block_ids: bool = False, highlight: int | None = None) -> None:
        self.doc = doc
        self.block_ids = block_ids
        self.highlight = highlight
        self.index = 0                     # índice do bloco de doc.blocks em renderização
        self.emitted: set[str] = set()     # notas de rodapé já posicionadas
        self.ref_count: dict[str, int] = {}
        self.referenced = {s.note for b in doc.blocks for s in b.spans if s.note} | {
            s.note for n in doc.notes.values() for b in n.blocks for s in b.spans if s.note
        }

    def render(self) -> str:
        body: list[str] = []
        group: list[str] = []
        shown = False          # já saiu algum bloco visível
        page_break = False     # quebra de página de um bloco oculto, à espera do próximo visível
        for self.index, block in enumerate(self.doc.blocks):
            cols = self.doc.columns > 1 and not block.style.full_width
            if block.style.hidden:
                anchor = self._trailing_anchor(block)       # as notas sem chamada continuam
                if anchor:
                    (group if cols else body).append(anchor)
                page_break = page_break or block.style.page_break_before
                continue
            html = self._block(block)
            if (block.style.page_break_before or page_break) and shown:
                # fora do div.cols (dentro dele seria quebra de coluna); antes do 1º bloco, nada
                if group:
                    body.append(self._columns(group))
                    group = []
                body.append('<div class="page-break"></div>')
            page_break, shown = False, True
            if block.style.space_before_lines > 0:
                # bloco de coluna no meio do grupo: o espaço fica dentro do div.cols (fechá-lo rebalancearia
                # as colunas); no início do grupo, fora dele (dentro, empurraria só a 1ª coluna)
                if group and not cols:
                    body.append(self._columns(group))
                    group = []
                height = block.style.space_before_lines * self.doc.line_height * self.doc.body_font_pt
                (group if cols and group else body).append(f'<div class="space" style="height:{height:g}pt"></div>')
            if cols:
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
        html = self._block_html(b)
        return self._mark(html) if self.block_ids or self.highlight == self.index else html

    def _mark(self, html: str) -> str:
        """id="b{i}" e class="hl" no elemento principal (o filete, se houver, vem antes dele). Se ele já
        tem id (div.endnote), o id do bloco vai num div em volta: um 2º id na tag seria ignorado."""
        wrap = False

        def tag(m: re.Match[str]) -> str:
            nonlocal wrap
            t = m.group(0)
            if self.block_ids:
                if re.search(r'\sid="', t):
                    wrap = True
                else:
                    t = t[:-1] + f' id="b{self.index}">'
            if self.highlight == self.index:
                t = re.sub(r'class="', 'class="hl ', t, 1) if 'class="' in t else t[:-1] + ' class="hl">'
            return t

        # o primeiro elemento que não é o div de filete
        out = re.sub(r'<(?!div class="rule )[a-z0-9]+[^>]*>', tag, html, count=1)
        return f'<div id="b{self.index}">{out}</div>' if wrap else out

    def _block_html(self, b: Block) -> str:
        style = self._style_attr(b)
        size = b.style.font_size_pt or self.doc.body_font_pt
        if b.kind in ("title", "heading"):
            tag = "h1" if b.kind == "title" else f"h{min(6, max(2, b.level))}"
            ruled = " ".join(c for c, r in (("rule-above", b.style.rule_above), ("rule-below", b.style.rule_below)) if r)
            cls = f' class="{ruled}"' if ruled else ""
            inner = f"<{tag}{cls}{style}>{self._text(b, size)}</{tag}>"
        elif b.kind == "paragraph":
            return self._ruled(b, f"<p{style}>{self._text(b, size)}{self._trailing(b)}</p>")
        elif b.kind == "list_item":
            return self._ruled(b, f'<p class="li"{style}>{self._text(b, size)}{self._trailing(b)}</p>')
        elif b.kind == "caption":
            inner = f'<p class="caption"{style}>{self._text(b, size)}</p>'
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
        return self._ruled(b, inner) + self._trailing_anchor(b)

    def _trailing_anchor(self, b: Block) -> str:
        trailing = self._trailing(b)
        return f'<div class="fn-anchor">{trailing}</div>' if trailing else ""

    def _ruled(self, b: Block, html: str) -> str:
        """Filetes como elementos próprios antes/depois do bloco: a caixa do bloco não muda."""
        above = self._rule(b, b.style.rule_above, "above") if b.style.rule_above else ""
        below = self._rule(b, b.style.rule_below, "below") if b.style.rule_below else ""
        return above + html + below

    def _rule(self, b: Block, rule: Rule, position: str) -> str:
        # o div tem o em do corpo (left_em é medido nele); a folga medida é a margem do lado do
        # bloco e, num título (margem zerada pela classe rule-*), a margem dele passa para o outro lado
        near, far = ("top", "bottom") if position == "below" else ("bottom", "top")
        css = (f"margin-left:{rule.left_em:g}em;margin-right:{rule.right_frac * 100:.1f}%;"
               f"border-top:{rule.thickness_pt:g}pt solid;margin-{near}:{rule.gap_pt:g}pt")
        if b.kind in ("title", "heading"):
            css += f";margin-{far}:{self._heading_margin_pt(b, far):.2f}pt"
        return f'<div class="rule {position}" style="{css}"></div>'

    def _heading_margin_pt(self, b: Block, side: str) -> float:
        level = 1 if b.kind == "title" else min(6, max(2, b.level))
        if side == "top" and level == 1:
            return 0.0                                  # h1 { margin-top: 0 }
        size = b.style.font_size_pt or self.doc.body_font_pt * _HEADING_EM.get(level, 1.0)
        return _HEADING_MARGIN_EM[side] * size

    def _style_attr(self, b: Block) -> str:
        css = []
        st = b.style
        if b.kind in ("paragraph", "list_item", "caption", "title", "heading") and st.align != "justify":
            css.append(f"text-align:{st.align}")
        if st.indent_em and st.align in ("justify", "left"):
            css.append(f"text-indent:{st.indent_em:g}em")
        if st.font_size_pt:
            css.append(f"font-size:{st.font_size_pt:g}pt")
        if b.kind in _TEXT_KINDS:
            if st.bold is not None:
                css.append(f"font-weight:{'bold' if st.bold else 'normal'}")
            if st.italic is not None:
                css.append(f"font-style:{'italic' if st.italic else 'normal'}")
        return f' style="{";".join(css)}"' if css else ""

    def _trailing(self, b: Block) -> str:
        return "".join(self._footnote(self.doc.notes[n]) for n in b.trailing_notes
                       if n in self.doc.notes and n not in self.emitted)

    # -- trechos e notas

    def _text(self, b: Block, size_pt: float) -> str:
        """Trechos de um bloco de texto. O sobrescrito `False` do bloco (sem negrito / sem itálico) vale
        também para as marcas dos trechos: o <b>/<i> deles reaplicaria o que o bloco tirou."""
        return self._spans(b.spans, size_pt, bold=b.style.bold is not False, italic=b.style.italic is not False)

    def _spans(self, spans: list[Span], size_pt: float, allow_float: bool = True,
               bold: bool = True, italic: bool = True) -> str:
        """`bold`/`italic` False: ignora essas marcas dos trechos."""
        out = []
        for s in spans:
            if s.math:
                out.append(math_img(s.text, size_pt, True))
                continue
            text = escape(s.text, quote=False).replace("\n", "<br>")   # "\n" só vem do usuário
            if s.italic and italic:
                text = f"<i>{text}</i>"
            if s.bold and bold:
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
