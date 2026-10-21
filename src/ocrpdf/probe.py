"""Heurísticas sobre a imagem de um bloco de texto: nº de linhas, entrelinha, recuo e alinhamento.

O VLM devolve o texto e o bbox, mas não a geometria interna do bloco; isso é medido
aqui com perfis de projeção da tinta, o que é barato e suficiente para texto impresso.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from .ocr.base import LineMetrics, RuleMetrics


def ink_mask(page: Image.Image) -> np.ndarray:
    """Binariza a página inteira (Otsu) uma vez; True = tinta."""
    gray = np.asarray(page.convert("L"), dtype=np.uint8)
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = gray.size
    cum = np.cumsum(hist)
    cum_mean = np.cumsum(hist * np.arange(256))
    w0 = cum / total
    w1 = 1 - w0
    with np.errstate(divide="ignore", invalid="ignore"):
        m0 = cum_mean / cum
        m1 = (cum_mean[-1] - cum_mean) / (total - cum)
        between = w0 * w1 * (m0 - m1) ** 2
    threshold = int(np.nanargmax(between))
    return gray <= threshold


def measure(mask: np.ndarray, bbox: tuple[float, float, float, float]) -> LineMetrics | None:
    x1, y1, x2, y2 = (int(round(v)) for v in bbox)
    region = mask[max(y1, 0):y2, max(x1, 0):x2]
    if region.size == 0 or region.shape[1] < 8:
        return None

    lines = line_spans(region)
    if not lines:
        return None
    width = region.shape[1]
    lefts, rights, ascents = [], [], []
    for top, bottom in lines:
        band = region[top:bottom]
        cols = np.flatnonzero(band.any(axis=0))
        lefts.append(cols[0])
        rights.append(cols[-1])
        ascents.append(_ascent(band.sum(axis=1)))

    starts = [t for t, _ in lines]
    pitch = float(np.median(np.diff(starts))) if len(lines) > 1 else 0.0
    indent = float(lefts[0] - np.median(lefts[1:])) if len(lines) > 1 else 0.0
    return LineMetrics(
        lines=len(lines),
        line_pitch_px=pitch,
        ascent_px=float(np.median(ascents)),
        first_indent_px=max(0.0, indent),
        align=_alignment(lefts, rights, width),
    )


def find_rules(mask: np.ndarray, bbox: tuple[float, float, float, float]) -> list[RuleMetrics]:
    """Filetes horizontais colados ao bloco (acima ou abaixo), que o VLM não reporta.

    Procura em [y1 − banda, y2 + banda] grupos de linhas consecutivas (altura ≤ 4 px) com um
    trecho contínuo de tinta que cubra ≥ metade da largura do bbox e meça ≥ 10% da página e
    ≥ 3× a altura da linha de texto do bloco. O filete pode passar do bbox (vai até a margem),
    por isso a linha é lida na largura inteira da página, mas vale o trecho de maior
    sobreposição com o bbox (um traço noutra coluna não conta). O grupo também precisa estar
    separado da tinta do texto por ≥ 1 linha em branco: serifas encostam na haste da letra.
    """
    x1, y1, x2, y2 = (int(round(v)) for v in bbox)
    h, w = mask.shape
    x1, x2, y1, y2 = max(x1, 0), min(x2, w), max(y1, 0), min(y2, h)
    if x2 - x1 < 8 or y2 <= y1:
        return []
    block = mask[y1:y2, x1:x2]
    lines = line_spans(block)
    if not lines:
        return []
    min_len = max(0.1 * w, 3 * float(np.median([b - a for a, b in lines])))
    band = max(10, round(0.6 * (y2 - y1)))
    top, bottom = max(0, y1 - band), min(h, y2 + band)

    spans: list[tuple[int, int, int]] = []   # (y, início, fim) por linha qualificada
    for y in range(top, bottom):
        span = _stroke_over(mask[y], x1, x2)
        if span and span[1] - span[0] >= min_len:
            spans.append((y, *span))
    groups: list[list[tuple[int, int, int]]] = []
    for s in spans:
        if groups and s[0] == groups[-1][-1][0] + 1:
            groups[-1].append(s)
        else:
            groups.append([s])

    ink_rows = np.flatnonzero(block.any(axis=1)) + y1
    rules = []
    for g in groups:
        if len(g) > 4:
            continue
        g_top, g_bottom = g[0][0], g[-1][0] + 1
        # tinta do texto do bloco (sem o filete), para medir a folga
        rows = ink_rows[(ink_rows < g_top) | (ink_rows >= g_bottom)]
        below = (g_top + g_bottom) / 2 >= (y1 + y2) / 2
        near = rows[rows < g_top] if below else rows[rows >= g_bottom]
        if not near.size:
            continue   # nenhuma tinta de texto do lado do bloco
        gap = g_top - (near.max() + 1) if below else near.min() - g_bottom
        if gap < 1:
            continue   # encostado na tinta: serifa ou traço de letra
        rules.append(RuleMetrics(
            position="below" if below else "above",
            x1_px=float(min(s[1] for s in g)), x2_px=float(max(s[2] for s in g)),
            y_px=(g_top + g_bottom) / 2, thickness_px=float(g_bottom - g_top), gap_px=float(gap),
        ))
    return rules


def _stroke_over(row: np.ndarray, x1: int, x2: int) -> tuple[int, int] | None:
    """Trecho de tinta da linha com maior sobreposição com [x1, x2), unindo falhas de até 2 px.

    Exige sobreposição ≥ metade de x2 − x1 e ≥ 90% de preenchimento do trecho.
    """
    cols = np.flatnonzero(row)
    if cols.size == 0:
        return None
    breaks = np.flatnonzero(np.diff(cols) > 3)
    starts = cols[np.concatenate(([0], breaks + 1))]
    ends = cols[np.concatenate((breaks, [cols.size - 1]))] + 1
    overlap = np.minimum(ends, x2) - np.maximum(starts, x1)
    k = int(np.argmax(overlap))
    a, b = int(starts[k]), int(ends[k])
    if overlap[k] < 0.5 * (x2 - x1) or row[a:b].sum() < 0.9 * (b - a):
        return None
    return a, b


def _ascent(profile: np.ndarray) -> int:
    """Altura do topo da tinta até a linha de base.

    A faixa da altura-x é onde o perfil horizontal é mais denso; a linha de base é a
    última linha dessa faixa. Diferente da altura total, não depende de a linha ter
    descendentes (g, p, q), o que importa em títulos curtos.
    """
    dense = np.flatnonzero(profile >= profile.max() * 0.4)
    return int(dense[-1]) + 1 if dense.size else len(profile)


def line_spans(region: np.ndarray) -> list[tuple[int, int]]:
    """Faixas horizontais com tinta; une lacunas pequenas (acentos, pontos do i)."""
    rows = region.sum(axis=1) > max(1, region.shape[1] * 0.002)
    spans, start = [], None
    for y, on in enumerate(rows):
        if on and start is None:
            start = y
        elif not on and start is not None:
            spans.append([start, y])
            start = None
    if start is not None:
        spans.append([start, len(rows)])
    # filetes (sublinhado de título, linha acima das notas) não são linhas de texto
    width = region.shape[1]
    spans = [s for s in spans if not (s[1] - s[0] <= 4 and region[s[0]:s[1]].sum(axis=1).max() > width * 0.5)]
    if not spans:
        return []

    heights = sorted(b - a for a, b in spans)
    typical = heights[len(heights) // 2]
    merged = [spans[0]]
    for a, b in spans[1:]:
        prev = merged[-1]
        gap = a - prev[1]
        # acento separado da letra, ou fragmento muito baixo colado à linha
        if gap <= max(2, typical * 0.25) and min(b - a, prev[1] - prev[0]) < typical * 0.6:
            prev[1] = b
        else:
            merged.append([a, b])
    min_h = max(2, typical * 0.3)
    return [(a, b) for a, b in merged if b - a >= min_h]


def _alignment(lefts: list[int], rights: list[int], width: int) -> str:
    tol = max(3, width * 0.015)
    if len(lefts) == 1:
        left_gap, right_gap = lefts[0], width - 1 - rights[0]
        if abs(left_gap - right_gap) <= tol and left_gap > tol:
            return "center"
        return "right" if left_gap > right_gap + tol else "left"

    body_l = np.array(lefts[1:])
    body_r = np.array(rights[:-1])  # última linha costuma ser curta
    left_flush = np.ptp(body_l) <= tol
    right_flush = len(body_r) > 0 and np.ptp(body_r) <= tol
    if left_flush and right_flush:
        return "justify"
    if left_flush:
        return "left"
    if right_flush:
        return "right"
    centers = (np.array(lefts) + np.array(rights)) / 2
    return "center" if np.ptp(centers) <= tol * 2 else "left"
