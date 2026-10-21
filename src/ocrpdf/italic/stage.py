"""Evidência de itálico por bloco (marcas do dots, inclinação na imagem, marcas do 2º modelo) e fusão.

Precisão > cobertura: uma palavra só vira itálico se alguma fonte candidata a marcou **e** a imagem
confirma. Só as marcas do 2º modelo vão para o cache (custam uma chamada); os tokens e a inclinação
são recalculados a cada execução.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..inline import parse_flat
from ..ocr.base import PageLayout
from .align import MATCH_MIN, match_quality, match_tokens
from .marker import PROMPT_VERSION
from .slant import ITALIC_MIN_DEG, SUSPECT_DEG, baseline_deg, slant_deg, word_boxes
from .words import has_math, italic_flags, tokens

ELIGIBLE = {"text", "list-item", "caption", "footnote", "title", "section-header"}

_SHORT = 3       # palavra com até tantas letras não tem haste para medir: herda dos vizinhos
_NEIGHBOURS = 2  # posições vasculhadas de cada lado atrás de uma palavra medida


@dataclass
class BlockEvidence:
    region: int                 # índice da região em `layout.regions`
    tokens: list[str]
    dots: list[bool]            # itálico segundo o dots
    slant: list[float | None]   # inclinação medida na imagem (graus); None = sem medida
    model: list[bool] | None = None   # marcas do 2º modelo (None = não consultado ou resposta rejeitada)


def page_evidence(layout: PageLayout, mask: np.ndarray) -> list[BlockEvidence]:
    """Evidência dos blocos elegíveis da página (sem fórmula e com ao menos uma palavra)."""
    out = []
    for i, region in enumerate(layout.regions):
        if region.category not in ELIGIBLE:
            continue
        spans = parse_flat(region.text)
        toks = tokens(spans)
        if not toks or has_math(spans):
            continue
        out.append(BlockEvidence(i, toks, italic_flags(spans), _slants(toks, mask, region.bbox)))
    return out


def _slants(toks: list[str], mask: np.ndarray, bbox: tuple[float, float, float, float]) -> list[float | None]:
    """Inclinação de cada token: mediana da de suas caixas, descontada a linha de base da linha."""
    boxes = word_boxes(mask, bbox)
    matches = match_tokens(toks, boxes)
    if match_quality(matches) < MATCH_MIN:
        return [None] * len(toks)
    by_line: dict[int, list] = {}
    for b in boxes:
        by_line.setdefault(b.line, []).append(b)
    base = {line: baseline_deg(bs) for line, bs in by_line.items()}
    out: list[float | None] = []
    for match in matches:
        values = [v for b in match if (v := slant_deg(mask, b, base[b.line])) is not None]
        out.append(statistics.median(values) if values else None)
    return out


def needs_model(ev: BlockEvidence) -> bool:
    """O bloco é suspeito: o dots marcou algo ou alguma palavra tem inclinação de itálico."""
    return any(ev.dots) or any(s is not None and s >= SUSPECT_DEG for s in ev.slant)


def fuse(ev: BlockEvidence, rule: str) -> list[bool]:
    """Flags finais. R1: candidato = marca do 2º modelo; R2: marca do dots ou do 2º modelo."""
    if ev.model is None:
        return list(ev.dots)
    if rule == "r1":
        cand = list(ev.model)
    elif rule == "r2":
        cand = [d or m for d, m in zip(ev.dots, ev.model)]
    else:
        raise ValueError(f"regra desconhecida: {rule!r}")
    n = len(cand)
    sure = [c and s is not None and s >= ITALIC_MIN_DEG for c, s in zip(cand, ev.slant)]
    out = list(sure)
    for i in range(n):
        if cand[i] and ev.slant[i] is None and _letters(ev.tokens[i]) <= _SHORT:
            before = _nearest(ev.slant, i, -1)
            after = _nearest(ev.slant, i, +1)
            out[i] = before is not None and after is not None and sure[before] and sure[after]
    return out


def _letters(token: str) -> int:
    return sum(c.isalpha() for c in token)


def _nearest(slant: list[float | None], i: int, step: int) -> int | None:
    """Posição do token medido mais próximo de `i` na direção `step`, até _NEIGHBOURS posições."""
    for k in range(1, _NEIGHBOURS + 1):
        j = i + step * k
        if 0 <= j < len(slant) and slant[j] is not None:
            return j
    return None


def tokens_key(toks: list[str]) -> str:
    return hashlib.sha1("\n".join(toks).encode("utf-8")).hexdigest()


def load_marks(path: Path, model_id: str) -> dict[int, tuple[str, list[bool] | None]]:
    """Marcas do 2º modelo cacheadas: {região: (tokens_key, marcas)}. Vazio se o arquivo faltar,
    estiver ilegível ou foi gerado por outro modelo/versão do prompt."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["model"] != model_id or data["prompt_version"] != PROMPT_VERSION:
            return {}
        return {int(k): (v["tokens"], v["model"]) for k, v in data["blocks"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def save_marks(path: Path, model_id: str, marks: dict[int, tuple[str, list[bool] | None]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"model": model_id, "prompt_version": PROMPT_VERSION,
            "blocks": {str(k): {"tokens": key, "model": m} for k, (key, m) in marks.items()}}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
