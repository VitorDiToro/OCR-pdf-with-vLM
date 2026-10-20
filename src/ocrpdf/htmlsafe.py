"""Sanitização do HTML de tabelas devolvido pelo modelo (só tags/atributos de tabela)."""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser

_ALLOWED = {
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
    "br", "b", "strong", "i", "em", "sup", "sub", "u", "p", "span",
}
_VOID = {"br", "col"}
_ATTRS = {"colspan", "rowspan"}


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in _ALLOWED:
            return
        kept = "".join(
            f' {k}="{escape(v)}"' for k, v in attrs if k in _ATTRS and v and v.isdigit()
        )
        self.out.append(f"<{tag}{kept}>")
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack:
                t = self.stack.pop()
                self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data):
        self.out.append(escape(data, quote=False))


def sanitize_table(html: str) -> str:
    s = _Sanitizer()
    s.feed(html)
    s.close()
    s.out.extend(f"</{t}>" for t in reversed(s.stack))
    result = "".join(s.out).strip()
    return result if "<table" in result else ""
