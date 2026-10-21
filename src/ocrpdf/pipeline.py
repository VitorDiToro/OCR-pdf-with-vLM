"""Orquestra as etapas: PDF → imagens → regiões (cache por página) → itálico por palavra
(etapa 2, cache próprio) → Document → PDF."""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
import pypdfium2 as pdfium
from PIL import Image

from . import probe
from .assemble import assemble
from .italic.marker import mark_italics
from .italic.stage import BlockEvidence, fuse, load_marks, needs_model, page_evidence, save_marks, tokens_key
from .model import Document
from .ocr.base import OcrEngine, PageLayout, Region
from .servers import NullSwitcher, Switcher

log = logging.getLogger(__name__)

_MEASURED = {"text", "title", "section-header", "list-item", "caption", "footnote", "page-header", "page-footer"}
_RULED = {"title", "section-header"}   # categorias em que se procuram filetes colados
_FONT_QUESTION = (
    "Look at the main body text of this page. Is its typeface serif or sans-serif? "
    "Answer with exactly one word: serif or sans."
)
_FONT_SAMPLES = 3  # blocos de texto (de páginas diferentes) consultados; vence a maioria
_TYPEFACE = re.compile(r"sans(?:[- ]serif)?|serif", re.I)
_CROP_PAD = 8      # folga (px) em torno do bbox no recorte enviado ao 2º modelo


@dataclass
class ItalicConfig:
    """Etapa 2: o 2º modelo (só marca itálico), o id dele no cache das marcas e a regra de fusão."""

    engine: OcrEngine
    model_id: str
    rule: str = "r2"
    workers: int = 4


def _area(r: Region) -> float:
    return (r.bbox[2] - r.bbox[0]) * (r.bbox[3] - r.bbox[1])


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
    def __init__(self, engine: OcrEngine, work_dir: Path, dpi: int = 200, workers: int = 4,
                 italic: ItalicConfig | None = None, switcher: Switcher | None = None) -> None:
        self.engine = engine
        self.work = work_dir
        self.dpi = dpi
        self.workers = workers
        self.italic = italic
        self.switcher = switcher or NullSwitcher()
        (self.work / "pages").mkdir(parents=True, exist_ok=True)
        (self.work / "assets").mkdir(exist_ok=True)

    def analyze(self, pdf_path: Path, pages: str | None = None, font: str = "auto",
                lang: str = "", force: bool = False) -> Document:
        try:
            indices = self._indices(pdf_path, pages)
            todo = [i for i in indices if force or not self._cache(i).exists()]
            ask_font = font == "auto" and (force or "font_family" not in self._meta())
            # cada servidor só é acordado (ou iniciado) se houver trabalho para ele
            if todo or ask_font:
                self.switcher.use("ocr")
            self._ocr(pdf_path, todo)
            family = font if font != "auto" else self._font_family(pdf_path, indices, ask_font)
            italic = None
            if self.italic:
                evidence = self.collect_evidence(pdf_path, pages)
                self._request_marks(pdf_path, evidence, force)
                # só blocos com marcas do 2º modelo; os demais ficam com os spans do OCR, intactos
                # (itálico parcial de palavra, como "*Aura*s", não cabe nas marcas por token)
                italic = {key: fuse(ev, self.italic.rule) for key, ev in evidence.items() if ev.model is not None}
            else:
                self._measure_pages(pdf_path, indices)
        finally:
            # modo restart: derruba o servidor no ar, também se a execução for interrompida
            self.switcher.close()

        layouts = [PageLayout.load(self._cache(i)) for i in indices]
        doc = assemble(layouts, source=pdf_path.name, font_family=family, italic=italic)
        doc.lang = lang
        doc.save(self.work / "document.json")
        return doc

    def collect_evidence(self, pdf_path: Path, pages: str | None = None) -> dict[tuple[int, int], BlockEvidence]:
        """Mede as páginas (o OCR precisa estar em cache) e devolve a evidência de itálico de cada
        bloco elegível, com chave `(página, região)`. Os blocos suspeitos recebem as marcas do 2º
        modelo que estiverem em cache; nenhum servidor é chamado."""
        evidence = self._measure_pages(pdf_path, self._indices(pdf_path, pages), evidence=True)
        if self.italic:
            for page, evs in _by_page(evidence).items():
                cached = load_marks(self._marks(page), self.italic.model_id)
                for ev in evs:
                    if needs_model(ev) and _usable(cached.get(ev.region), ev):
                        ev.model = cached[ev.region][1]
        return evidence

    # -- etapas

    def _cache(self, index: int) -> Path:
        return self.work / "pages" / f"page_{index + 1:04d}.json"

    def _marks(self, index: int) -> Path:
        return self.work / "italic" / f"page_{index + 1:04d}.json"

    def _meta(self) -> dict:
        path = self.work / "meta.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def _indices(self, pdf_path: Path, pages: str | None) -> list[int]:
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            return parse_page_range(pages, len(pdf))
        finally:
            pdf.close()

    def _ocr(self, pdf_path: Path, todo: list[int]) -> None:
        if not todo:
            return
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
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

    def _rasterize(self, pdf: pdfium.PdfDocument, index: int) -> tuple[Image.Image, float, float]:
        page = pdf[index]
        w_pt, h_pt = page.get_size()
        image = page.render(scale=self.dpi / 72).to_pil().convert("RGB")
        page.close()
        return image, w_pt, h_pt

    def _page_image(self, pdf: pdfium.PdfDocument, layout: PageLayout) -> Image.Image:
        """A página no espaço de pixels do OCR em cache (que pode ter vindo de outro --dpi)."""
        image, _, _ = self._rasterize(pdf, layout.index)
        if image.size != (layout.width_px, layout.height_px):
            image = image.resize((layout.width_px, layout.height_px), Image.LANCZOS)
        return image

    def _analyze_page(self, index: int, image: Image.Image, w_pt: float, h_pt: float) -> PageLayout:
        layout = PageLayout(index=index, width_px=image.width, height_px=image.height,
                            width_pt=w_pt, height_pt=h_pt, regions=self.engine.analyze(image))
        layout.save(self._cache(index))
        return layout

    def _measure_pages(self, pdf_path: Path, indices: list[int],
                       evidence: bool = False) -> dict[tuple[int, int], BlockEvidence]:
        """Passe de medidas sobre as páginas em cache; com `evidence`, devolve também a evidência
        de itálico. As medidas vêm sempre da imagem (o cache guarda o OCR; as medidas são
        regravadas), assim ajustes nas heurísticas valem sem refazer o OCR."""
        out: dict[tuple[int, int], BlockEvidence] = {}
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            for i in indices:
                layout = PageLayout.load(self._cache(i))
                image = self._page_image(pdf, layout)
                mask = probe.ink_mask(image)
                self._measure(layout, image, mask).save(self._cache(i))
                if evidence:
                    out.update(((i, ev.region), ev) for ev in page_evidence(layout, mask))
        finally:
            pdf.close()
        return out

    def _measure(self, layout: PageLayout, image: Image.Image, mask: np.ndarray) -> PageLayout:
        """Recalcula as medidas de cada região a partir da imagem e recorta as figuras."""
        for n, r in enumerate(layout.regions):
            if r.category in _MEASURED:
                r.metrics = probe.measure(mask, r.bbox)
            # zera também as demais categorias: o cache pode trazer filetes de outra versão
            r.rules = probe.find_rules(mask, r.bbox) if r.category in _RULED else []
            if r.category == "picture":
                name = f"assets/p{layout.index + 1:04d}_fig{n:02d}.png"
                image.crop(tuple(int(v) for v in r.bbox)).save(self.work / name)
                r.image = name
        return layout

    def _request_marks(self, pdf_path: Path, evidence: dict[tuple[int, int], BlockEvidence],
                       force: bool) -> None:
        """Pede ao 2º modelo as marcas dos blocos suspeitos que faltam no cache e as grava.

        Resposta rejeitada (texto alterado) vai para o cache como None e não é pedida de novo.
        Falha de conexão não vai: as marcas já obtidas são gravadas e a execução para com erro.
        """
        cfg = self.italic
        cached: dict[int, dict[int, tuple[str, list[bool] | None]]] = {}
        missing: dict[int, list[BlockEvidence]] = {}
        for page, evs in _by_page(evidence).items():
            cached[page] = {} if force else load_marks(self._marks(page), cfg.model_id)
            todo = [ev for ev in evs if needs_model(ev) and not _usable(cached[page].get(ev.region), ev)]
            if todo:
                missing[page] = todo
        if not missing:
            return
        log.info("itálico: %d blocos para o 2º modelo", sum(len(v) for v in missing.values()))
        self.switcher.use("italic")
        pages = sorted(missing)
        batch = max(1, cfg.workers * 2)
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
                for start in range(0, len(pages), batch):
                    chunk = pages[start:start + batch]
                    jobs = []
                    for page in chunk:
                        layout = PageLayout.load(self._cache(page))
                        image = self._page_image(pdf, layout)
                        jobs += [(page, ev, _crop(image, layout.regions[ev.region].bbox)) for ev in missing[page]]
                    errors = []
                    for (page, ev, _), (marks, error) in zip(jobs, pool.map(lambda job: self._mark(*job[1:]), jobs)):
                        if error is not None:
                            errors.append(error)
                            continue
                        ev.model = marks
                        cached[page][ev.region] = (tokens_key(ev.tokens), marks)
                    for page in chunk:
                        save_marks(self._marks(page), cfg.model_id, cached[page])
                    if errors:
                        raise RuntimeError(
                            f"o 2º modelo não respondeu em {len(errors)} bloco(s) ({errors[0]}); as marcas "
                            "obtidas foram gravadas e a próxima execução pede só as que faltam. Confira o "
                            "servidor do itálico (modo sleep: suba o par com scripts/serve_pair.sh)")
        finally:
            pdf.close()

    def _mark(self, ev: BlockEvidence, crop: Image.Image) -> tuple[list[bool] | None, Exception | None]:
        engine = _Watched(self.italic.engine)
        return mark_italics(engine, crop, ev.tokens), engine.error

    def _font_family(self, pdf_path: Path, indices: list[int], ask: bool) -> str:
        meta = self._meta()
        if ask:
            meta["font_family"] = self._ask_font(pdf_path, [PageLayout.load(self._cache(i)) for i in indices])
            (self.work / "meta.json").write_text(json.dumps(meta))
        return meta["font_family"]

    def _ask_font(self, pdf_path: Path, layouts: list[PageLayout]) -> str:
        # Recortes em resolução cheia do maior bloco de texto das páginas com mais texto:
        # com a página inteira reduzida o modelo não enxerga as serifas.
        samples = []
        for p in layouts:
            texts = [r for r in p.regions if r.category == "text"]
            if texts:
                samples.append((max(texts, key=_area), p))
        samples.sort(key=lambda s: _area(s[0]), reverse=True)

        votes: Counter[str] = Counter()
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            for region, layout in samples[:_FONT_SAMPLES]:
                image, _, _ = self._rasterize(pdf, layout.index)
                k = image.width / layout.width_px  # o cache pode ter vindo de outro --dpi
                x1, y1, x2, y2 = (v * k for v in region.bbox)
                crop = image.crop((int(x1), int(y1), int(x2), int(min(y2, y1 + 0.2 * image.height))))
                try:
                    answer = self.engine.ask(crop, _FONT_QUESTION)
                except Exception as exc:  # pergunta auxiliar: não deve derrubar a conversão
                    log.warning("não foi possível classificar a fonte (%s)", exc)
                    continue
                if found := _TYPEFACE.findall(answer):
                    # vale a última classificação citada ("...is sans-serif. \boxed{sans}")
                    votes["sans" if found[-1].lower().startswith("sans") else "serif"] += 1
        finally:
            pdf.close()
        family = "sans" if votes["sans"] > votes["serif"] else "serif"
        log.info("fonte do corpo: %s (votos: %s)", family, dict(votes) or "nenhum; padrão serif")
        return family


class _Watched:
    """Engine do 2º modelo que guarda a falha de conexão de uma chamada: `mark_italics` devolve
    None tanto para resposta rejeitada quanto para falha, e só a rejeitada pode ir para o cache."""

    def __init__(self, engine: OcrEngine) -> None:
        self.engine = engine
        self.error: httpx.TransportError | None = None

    def ask(self, image: Image.Image, question: str, max_tokens: int = 128) -> str:
        try:
            return self.engine.ask(image, question, max_tokens=max_tokens)
        except httpx.TransportError as exc:
            self.error = exc
            raise


def _by_page(evidence: dict[tuple[int, int], BlockEvidence]) -> dict[int, list[BlockEvidence]]:
    out: dict[int, list[BlockEvidence]] = {}
    for (page, _), ev in evidence.items():
        out.setdefault(page, []).append(ev)
    return out


def _usable(entry: tuple[str, list[bool] | None] | None, ev: BlockEvidence) -> bool:
    """A marca do cache vale se foi dada para estes tokens (None = resposta rejeitada pelo marker)."""
    if entry is None or entry[0] != tokens_key(ev.tokens):
        return False
    return entry[1] is None or len(entry[1]) == len(ev.tokens)


def _crop(image: Image.Image, bbox: tuple[float, float, float, float]) -> Image.Image:
    x1, y1, x2, y2 = (int(v) for v in bbox)
    return image.crop((max(0, x1 - _CROP_PAD), max(0, y1 - _CROP_PAD),
                       min(image.width, x2 + _CROP_PAD), min(image.height, y2 + _CROP_PAD)))
