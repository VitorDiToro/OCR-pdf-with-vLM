"""Utilitários de teste: PDF de páginas-imagem, engine falso e uma página sintética estilo Benjamin."""

from __future__ import annotations

import copy
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ocrpdf.ocr.base import Region

_FONTS = "/usr/share/fonts/truetype/liberation/LiberationSans-{}.ttf"
_LOREM = ("A reprodução técnica da obra de arte é um processo que se intensificou lentamente na história, "
          "por saltos separados por longos intervalos, mas com intensidade crescente e penetrantes "
          "consequências para a percepção coletiva ").split()


def make_pdf(path: Path, images: list[Image.Image], dpi: int = 200) -> Path:
    """PDF só com páginas-imagem."""
    images[0].save(path, save_all=True, append_images=images[1:], resolution=dpi)
    return path


class FakeEngine:
    """OcrEngine de mentira: devolve respostas pré-definidas e conta as chamadas a `analyze`."""

    def __init__(self, responses: list[list[Region]], answers: list[str] | None = None) -> None:
        self.responses = responses
        self.answers = answers or []
        self.calls = 0
        self._asked = 0

    def analyze(self, image: Image.Image) -> list[Region]:
        out = copy.deepcopy(self.responses[min(self.calls, len(self.responses) - 1)])
        self.calls += 1
        return out

    def ask(self, image: Image.Image, question: str, max_tokens: int = 128) -> str:
        answer = self.answers[self._asked] if self._asked < len(self.answers) else "sans"
        self._asked += 1
        return answer


def _justified_line(draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont, words: list[str],
                    x1: int, x2: int, y: int) -> None:
    """Desenha `words` esticando os espaços para ocupar x1..x2."""
    widths = [draw.textlength(w, font=font) for w in words]
    gap = (x2 - x1 - sum(widths)) / max(1, len(words) - 1)
    x = float(x1)
    for w, wd in zip(words, widths):
        draw.text((x, y), w, font=font, fill=0)
        x += wd + gap


def _paragraph(draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont, top: int, seed: int,
               lines: int = 5, pitch: int = 31, left: int = 151, right: int = 1547, indent: int = 98) -> int:
    """Parágrafo justificado de `lines` linhas com recuo na 1ª; devolve o fim (y) do bloco."""
    k = seed
    for n in range(lines):
        start = left + (indent if n == 0 else 0)
        words: list[str] = []
        while True:
            cand = words + [_LOREM[k % len(_LOREM)]]
            if draw.textlength(" ".join(cand), font=font) > right - start:
                break
            words, k = cand, k + 1
        y = top + n * pitch
        if n == lines - 1:
            draw.text((start, y), " ".join(words[: max(2, len(words) // 2)]), font=font, fill=0)
        else:
            _justified_line(draw, font, words, start, right, y)
    return top + lines * pitch - 1


def benjamin_like_page() -> tuple[Image.Image, list[Region]]:
    """Página 1700×2200 (Letter a 200 dpi) imitando a p. 1 do exemplo; bboxes como os do dots."""
    img = Image.new("RGB", (1700, 2200), "white")
    d = ImageDraw.Draw(img)
    bold, reg = ImageFont.truetype(_FONTS.format("Bold"), 47), ImageFont.truetype(_FONTS.format("Regular"), 29)
    title = "A obra de arte na era de sua reprodutibilidade técnica"
    d.text((251, 157), title, font=bold, fill=0)
    d.rectangle((254, 211, 1545, 212), fill=0)  # filete de 2 px sob o título (não entra na região)
    sub = "(Primeira versão, tradução de teste)"
    d.text((251, 257), sub, font=reg, fill=0)
    d.text((251, 342), "Primeira Versão", font=reg, fill=0)
    end1 = _paragraph(d, reg, 398, seed=0)
    end2 = _paragraph(d, reg, 574, seed=7)
    regions = [
        Region("title", (251, 157, 1395, 209), title),
        Region("text", (251, 257, 938, 288), sub),
        Region("section-header", (251, 342, 517, 368), "Primeira Versão"),
        Region("text", (151, 398, 1547, end1), "Primeiro parágrafo do ensaio, com cinco linhas de texto."),
        Region("text", (151, 574, 1547, end2), "Segundo parágrafo do ensaio, com cinco linhas de texto."),
    ]
    return img, regions
