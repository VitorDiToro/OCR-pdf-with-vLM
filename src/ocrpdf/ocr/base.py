"""Resultado bruto da análise de uma página (cacheado em <work>/pages/page_NNNN.json)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from PIL import Image

# Categorias normalizadas (as mesmas do dots.ocr, em minúsculas).
CATEGORIES = {
    "caption", "footnote", "formula", "list-item", "page-footer", "page-header",
    "picture", "section-header", "table", "text", "title",
}


@dataclass
class LineMetrics:
    """Medidas extraídas da imagem do bloco (ver probe.py), em pixels da página."""

    lines: int
    line_pitch_px: float     # distância entre linhas consecutivas (0 se 1 linha)
    ascent_px: float         # topo da tinta → linha de base (mediana por linha)
    first_indent_px: float   # recuo da 1ª linha em relação às demais
    align: str               # justify | left | center | right


@dataclass
class RuleMetrics:
    """Filete horizontal colado a um bloco (sublinhado de título), em pixels da página."""

    position: str            # above | below
    x1_px: float
    x2_px: float
    y_px: float              # centro do filete
    thickness_px: float
    gap_px: float            # distância entre o filete e a tinta do texto do bloco


@dataclass
class Region:
    category: str
    bbox: tuple[float, float, float, float]   # pixels da imagem da página
    text: str = ""
    metrics: LineMetrics | None = None
    image: str = ""                            # recorte salvo (figuras)
    rules: list[RuleMetrics] = field(default_factory=list)   # filetes colados (títulos)


@dataclass
class PageLayout:
    index: int
    width_px: int
    height_px: int
    width_pt: float
    height_pt: float
    regions: list[Region] = field(default_factory=list)

    @property
    def px_to_pt(self) -> float:
        return self.width_pt / self.width_px

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> PageLayout:
        d = json.loads(path.read_text(encoding="utf-8"))
        regions = []
        for r in d.pop("regions"):
            m = r.pop("metrics", None)
            rules = [RuleMetrics(**x) for x in r.pop("rules", [])]
            regions.append(Region(**r, metrics=LineMetrics(**m) if m else None, rules=rules))
        return cls(**d, regions=regions)


class OcrEngine(Protocol):
    def analyze(self, image: Image.Image) -> list[Region]:
        """Devolve as regiões da página em ordem de leitura, com bbox em pixels de `image`."""
        ...

    def ask(self, image: Image.Image, question: str, max_tokens: int = 128) -> str:
        """Pergunta livre sobre a imagem (usada para classificar a fonte)."""
        ...
