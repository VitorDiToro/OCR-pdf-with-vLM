"""Orquestra as etapas: PDF → imagens → regiões (cache por página) → Document → PDF."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from . import probe
from .assemble import assemble
from .model import Document
from .ocr.base import OcrEngine, PageLayout

log = logging.getLogger(__name__)

_MEASURED = {"text", "title", "section-header", "list-item", "caption", "footnote"}
_FONT_QUESTION = (
    "Look at the main body text of this page. Is its typeface serif or sans-serif? "
    "Answer with exactly one word: serif or sans."
)


def parse_page_range(spec: str | None, total: int) -> list[int]:
    """"1-3,7" → [0, 1, 2, 6] (índices 0-based)."""
    if not spec:
        return list(range(total))
    pages: set[int] = set()
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        start = int(a) if a else 1
        end = int(b) if b else (total if _ else start)
        pages.update(range(max(1, start) - 1, min(total, end)))
    return sorted(pages)


class Pipeline:
    def __init__(self, engine: OcrEngine, work_dir: Path, dpi: int = 200, workers: int = 4) -> None:
        self.engine = engine
        self.work = work_dir
        self.dpi = dpi
        self.workers = workers
        (self.work / "pages").mkdir(parents=True, exist_ok=True)
        (self.work / "assets").mkdir(exist_ok=True)

    def analyze(self, pdf_path: Path, pages: str | None = None, font: str = "auto",
                lang: str = "", force: bool = False) -> Document:
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            indices = parse_page_range(pages, len(pdf))
            todo = [i for i in indices if force or not self._cache(i).exists()]
            # pdfium não é thread-safe: rasteriza em série, em lotes pequenos (memória),
            # e paraleliza só as chamadas ao modelo
            batch = max(1, self.workers * 2)
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                for start in range(0, len(todo), batch):
                    jobs = [(i, *self._rasterize(pdf, i)) for i in todo[start:start + batch]]
                    for layout in pool.map(lambda job: self._analyze_page(*job), jobs):
                        log.info("página %d: %d regiões", layout.index + 1, len(layout.regions))
        finally:
            pdf.close()

        layouts = [PageLayout.load(self._cache(i)) for i in indices]
        family = self._font_family(pdf_path, layouts, font)
        doc = assemble(layouts, source=pdf_path.name, font_family=family)
        doc.lang = lang
        doc.save(self.work / "document.json")
        return doc

    # -- etapas

    def _cache(self, index: int) -> Path:
        return self.work / "pages" / f"page_{index + 1:04d}.json"

    def _rasterize(self, pdf: pdfium.PdfDocument, index: int) -> tuple[Image.Image, float, float]:
        page = pdf[index]
        w_pt, h_pt = page.get_size()
        image = page.render(scale=self.dpi / 72).to_pil().convert("RGB")
        page.close()
        return image, w_pt, h_pt

    def _analyze_page(self, index: int, image: Image.Image, w_pt: float, h_pt: float) -> PageLayout:
        regions = self.engine.analyze(image)
        mask = probe.ink_mask(image)
        for n, r in enumerate(regions):
            if r.category in _MEASURED:
                r.metrics = probe.measure(mask, r.bbox)
            elif r.category == "picture":
                name = f"assets/p{index + 1:04d}_fig{n:02d}.png"
                image.crop(tuple(int(v) for v in r.bbox)).save(self.work / name)
                r.image = name
        layout = PageLayout(index=index, width_px=image.width, height_px=image.height,
                            width_pt=w_pt, height_pt=h_pt, regions=regions)
        layout.save(self._cache(index))
        return layout

    def _font_family(self, pdf_path: Path, layouts: list[PageLayout], font: str) -> str:
        if font != "auto":
            return font
        meta_path = self.work / "meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        if "font_family" not in meta:
            meta["font_family"] = self._ask_font(pdf_path, layouts)
            meta_path.write_text(json.dumps(meta))
        return meta["font_family"]

    def _ask_font(self, pdf_path: Path, layouts: list[PageLayout]) -> str:
        # página com mais texto corrido, para a pergunta ter o que olhar
        best = max(layouts, key=lambda p: sum(len(r.text) for r in p.regions if r.category == "text"))
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            image, _, _ = self._rasterize(pdf, best.index)
        finally:
            pdf.close()
        try:
            answer = self.engine.ask(image, _FONT_QUESTION).lower()
        except Exception as exc:  # pergunta auxiliar: não deve derrubar a conversão
            log.warning("não foi possível classificar a fonte (%s); usando serif", exc)
            return "serif"
        return "sans" if "sans" in answer else "serif"
