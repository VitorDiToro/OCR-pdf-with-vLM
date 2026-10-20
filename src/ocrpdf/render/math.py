"""LaTeX → SVG (ziamath), embutido como <img> com o LaTeX original no alt."""

from __future__ import annotations

import base64
import re
from functools import lru_cache
from html import escape

import ziamath

_VIEWBOX = re.compile(r'viewBox="([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+)"')


@lru_cache(maxsize=512)
def math_img(latex: str, size_pt: float, inline: bool) -> str:
    try:
        svg = ziamath.Latex(latex, size=size_pt, inline=inline).svg()
    except Exception:  # LaTeX que o ziamath não entende: mostra o código-fonte
        return f'<code class="math-src">{escape(latex)}</code>'
    m = _VIEWBOX.search(svg)
    if not m:
        return f'<code class="math-src">{escape(latex)}</code>'
    _, y0, w, h = (float(v) for v in m.groups())
    descent = h + y0  # a linha de base do SVG é y = 0
    data = base64.b64encode(svg.encode()).decode()
    css = f"width:{w:.2f}pt;height:{h:.2f}pt"
    if inline:
        css += f";vertical-align:{-descent:.2f}pt"
    cls = "math-inline" if inline else "math-display"
    return f'<img class="{cls}" style="{css}" alt="{escape(latex)}" src="data:image/svg+xml;base64,{data}">'
