"""Caixas de palavra e inclinação de cada palavra na imagem (evidência de itálico por palavra).

O bloco é endireitado (página torta) antes de procurar as linhas com `probe.line_spans`; as
palavras saem das lacunas horizontais entre tintas vizinhas. A inclinação de uma palavra é o
cisalhamento que deixa as suas hastes na vertical, descontada a inclinação da linha de base.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..probe import line_spans, projection_scores, straighten

# Calibrados com os gabaritos (docs/superpowers/reports/2026-10-21-italico-avaliacao.md): a regra
# padrão fica estável com o limiar entre 4° e 9°; itálico medido 9,5–16,5° (p5–p95), redondo ~0°.
SUSPECT_DEG = 5.0      # a partir daqui a palavra é suspeita: o bloco vai ao 2º modelo (r1/r2)
ITALIC_MIN_DEG = 7.0   # a partir daqui a inclinação confirma o itálico
MIN_CONTRAST = 0.15    # perfil mais achatado que isso (1 − mín/máx): palavra sem hastes, sem medida

_ANGLES = np.arange(-15.0, 25.0 + 0.25, 0.5)   # cisalhamentos testados (graus; > 0 = topo à direita)
_GAP = 0.22           # lacuna mínima entre palavras, em alturas de linha
_VTOL = 0.08          # tinta até esta fração da altura acima/abaixo ainda conta como vizinha
_MARK = 0.4           # pedaço com até esta largura (em alturas de linha) é pontuação/sinal


@dataclass
class WordBox:
    """Caixa de uma palavra em px da página: na horizontal, a tinta da palavra; na vertical, a
    faixa da linha (a altura da caixa é a altura da linha). `line` é o índice da linha no bloco."""

    x1: int
    y1: int
    x2: int
    y2: int
    line: int


def word_boxes(mask: np.ndarray, bbox: tuple[float, float, float, float]) -> list[WordBox]:
    """Caixas das palavras do bloco `bbox`, na ordem de leitura.

    As linhas são procuradas no bloco endireitado (cada coluna sobe ou desce conforme a inclinação
    das linhas); a faixa de cada linha volta para a página na coluna central de cada palavra.
    """
    x1, y1, x2, y2 = (int(round(v)) for v in bbox)
    h, w = mask.shape
    x1, x2, y1, y2 = max(x1, 0), min(x2, w), max(y1, 0), min(y2, h)
    region = mask[y1:y2, x1:x2]
    if not region.any():
        return []

    straight, shift, offset, _ = straighten(region)
    lines = line_spans(straight)
    if not lines:
        return []
    height = float(np.median([b - a for a, b in lines]))
    boxes = []
    for i, (top, bottom) in enumerate(lines):
        for a, b in _words(straight[top:bottom], height):
            dy = y1 + int(shift[(a + b - 1) // 2]) - offset
            boxes.append(WordBox(x1 + a, top + dy, x1 + b, bottom + dy, i))
    return boxes


def baseline_deg(boxes: list[WordBox]) -> float:
    """Ângulo (graus) da reta ajustada às bases das caixas de uma linha (mínimos quadrados).

    O y cresce para baixo: > 0 é a linha descendo para a direita (página girada no sentido horário).
    """
    if len(boxes) < 2:
        return 0.0
    x = np.array([(b.x1 + b.x2) / 2 for b in boxes])
    y = np.array([b.y2 for b in boxes], dtype=float)
    dx = x - x.mean()
    den = float((dx ** 2).sum())
    return math.degrees(math.atan(float((dx * (y - y.mean())).sum()) / den)) if den else 0.0


def slant_deg(mask: np.ndarray, box: WordBox, baseline: float = 0.0) -> float | None:
    """Inclinação da palavra (graus; > 0 = topo à direita, como no itálico) em relação à base.

    Busca, de −15° a +25° (passo 0,5°), o cisalhamento que mais concentra a projeção vertical da
    tinta (maior Σ p²): o que deixa as hastes na vertical. Numa página girada as hastes giram com a
    linha de base, por isso `baseline` é descontado. `None` se a caixa for pequena demais para medir
    (largura < altura da linha ou < 30 px de tinta) ou se o perfil for achatado demais para ter um
    pico (palavra sem hastes, como "vez": as diagonais do v e do z imitam o itálico).
    """
    if box.x2 - box.x1 < box.y2 - box.y1:
        return None
    ys, xs = np.nonzero(mask[max(box.y1, 0):box.y2, max(box.x1, 0):box.x2])
    if ys.size < 30:
        return None
    scores = projection_scores(xs, ys, _ANGLES)
    if 1 - scores.min() / scores.max() < MIN_CONTRAST:
        return None
    return float(_ANGLES[np.argmax(scores)]) - baseline


def _words(band: np.ndarray, height: float) -> list[tuple[int, int]]:
    """Intervalos [x1, x2) das palavras numa faixa de linha (colunas da faixa).

    Separa nas lacunas entre colunas de tinta cuja distância linha a linha (`_row_gaps`) é
    ≥ _GAP × altura, a não ser que um dos lados seja um pedaço estreito (≤ _MARK × altura: a vírgula
    depois de aspas, o "1" de "1936") e a lacuna na projeção vertical fique abaixo do limiar.
    """
    cols = np.flatnonzero(band.any(axis=0))
    if cols.size == 0:
        return []
    jumps = np.flatnonzero(np.diff(cols) > 1)
    a, b = cols[jumps], cols[jumps + 1]   # última coluna com tinta antes e primeira depois da lacuna
    cut = np.flatnonzero(_row_gaps(band, a, b, height) >= _GAP * height)
    widths = np.append(a[cut] + 1, cols[-1] + 1) - np.insert(b[cut], 0, cols[0])
    narrow = widths <= _MARK * height
    glued = (narrow[:-1] | narrow[1:]) & (b[cut] - a[cut] - 1 < _GAP * height)
    jumps = jumps[cut[~glued]]
    starts = np.insert(cols[jumps + 1], 0, cols[0])
    ends = np.append(cols[jumps] + 1, cols[-1] + 1)
    return list(zip(starts.tolist(), ends.tolist()))


def _row_gaps(band: np.ndarray, a: np.ndarray, b: np.ndarray, height: float) -> np.ndarray:
    """Para cada lacuna [a+1, b−1], a menor distância horizontal entre a tinta da esquerda e a da
    direita na mesma linha de pixels (com tolerância de ±_VTOL × altura).

    Pela projeção vertical, o pé inclinado do itálico ("um *processo*") encolheria a lacuna até a
    de duas letras; linha a linha isso não acontece. A tolerância deixa pontuação e aspas, que
    ocupam poucas linhas, verem a letra ao lado ("série;"). Só conta tinta a até uma altura de
    linha da lacuna; sem nenhuma, vale a lacuna da projeção.
    """
    h, w = band.shape
    idx = np.arange(w)
    last = np.maximum.accumulate(np.where(band, idx, -w), axis=1)[:, a]   # (linhas, lacunas)
    first = np.minimum.accumulate(np.where(band, idx, 2 * w)[:, ::-1], axis=1)[:, ::-1][:, b]
    k = int(round(_VTOL * height))
    if k:
        padded = np.pad(last, ((k, k), (0, 0)), constant_values=-w)
        last = np.max([padded[i:i + h] for i in range(2 * k + 1)], axis=0)
    near = (last >= a - height) & (first <= b + height)
    dist = np.where(near, first - last - 1, w).min(axis=0)
    return np.where(near.any(axis=0), dist, b - a - 1)
