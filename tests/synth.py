"""Utilitários de teste: PDF de páginas-imagem, engine falso, uma página sintética estilo Benjamin e
texto sintético palavra a palavra (regular/itálico) para a detecção de itálico."""

from __future__ import annotations

import copy
import threading
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ocrpdf.ocr.base import Region

_LIBERATION = "/usr/share/fonts/truetype/liberation/Liberation{}-{}.ttf"
_FONTS = _LIBERATION.format("Sans", "{}")
_LOREM = ("A reprodução técnica da obra de arte é um processo que se intensificou lentamente na história, "
          "por saltos separados por longos intervalos, mas com intensidade crescente e penetrantes "
          "consequências para a percepção coletiva ").split()


def make_pdf(path: Path, images: list[Image.Image], dpi: int = 200) -> Path:
    """PDF só com páginas-imagem."""
    images[0].save(path, save_all=True, append_images=images[1:], resolution=dpi)
    return path


class FakeEngine:
    """OcrEngine de mentira: devolve respostas pré-definidas e conta as chamadas a `analyze`.

    As respostas são servidas na ordem das chamadas (a última se repete), não por página: o
    engine não recebe o índice da página. Com várias páginas, use `workers=1` no Pipeline.
    """

    def __init__(self, responses: list[list[Region]], answers: list[str] | None = None) -> None:
        self.responses = responses
        self.answers = answers or []
        self.calls = 0
        self._asked = 0
        self._lock = threading.Lock()   # o Pipeline chama `analyze` de várias threads

    def analyze(self, image: Image.Image) -> list[Region]:
        with self._lock:
            k = self.calls
            self.calls += 1
        return copy.deepcopy(self.responses[min(k, len(self.responses) - 1)])

    def ask(self, image: Image.Image, question: str, max_tokens: int = 128) -> str:
        with self._lock:
            k = self._asked
            self._asked += 1
        return self.answers[k] if k < len(self.answers) else "sans"


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
               lines: int = 5, pitch: int = 31, left: int = 151, right: int = 1547,
               indent: int = 98) -> tuple[int, str]:
    """Parágrafo justificado de `lines` linhas com recuo na 1ª; devolve o fim (y) do bloco e o
    texto desenhado (a última linha, curta, leva só metade das palavras que caberiam)."""
    k = seed
    drawn: list[str] = []
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
            words = words[: max(2, len(words) // 2)]
            draw.text((start, y), " ".join(words), font=font, fill=0)
        else:
            _justified_line(draw, font, words, start, right, y)
        drawn.extend(words)
    return top + lines * pitch - 1, " ".join(drawn)


_SAMPLE = (
    "Em sua essência, a obra de arte sempre foi reprodutível.",
    "O que os homens faziam podia ser imitado por outros",
    "Essa imitação era praticada por discípulos, em seus exercícios",
    "pelos mestres, para a difusão das obras e por terceiros",
    "Em contraste, a reprodução técnica representa um processo novo",
)


def sample_lines(italic_every: int = 0) -> list[list[tuple[str, bool]]]:
    """5 linhas de 9–10 palavras `(texto, itálico)`; com k > 0, cada k-ésima palavra do texto
    (contando de 1, através das linhas) é itálica."""
    out, n = [], 0
    for line in _SAMPLE:
        row = []
        for word in line.split():
            n += 1
            row.append((word, italic_every > 0 and n % italic_every == 0))
        out.append(row)
    return out


def flat_words(lines: list[list[tuple[str, bool]]]) -> list[str]:
    """Texto das palavras na ordem de leitura."""
    return [w for line in lines for w, _ in line]


def bbox_of(truth: list[list[tuple[int, int, int, int]]]) -> tuple[int, int, int, int]:
    """União das caixas das palavras: o bbox do bloco, como o dots o daria."""
    boxes = [b for row in truth for b in row]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _ink_box(font: ImageFont.FreeTypeFont, word: str, x: float, y: int) -> tuple[int, int, int, int]:
    """Caixa da tinta de `word` desenhada em (x, y), posicionada como `ImageDraw.text` a desenha."""
    mask, (ox, oy) = font.getmask2(word, "L", start=(x % 1, 0))
    left, top, right, bottom = mask.getbbox()
    return int(x) + ox + left, y + oy + top, int(x) + ox + right, y + oy + bottom


def text_image(lines: list[list[tuple[str, bool]]], serif: bool = False, size_px: int = 29,
               origin: tuple[int, int] = (151, 150), page: tuple[int, int] = (1700, 800),
               angle_deg: float = 0.0) -> tuple[Image.Image, list[list[tuple[int, int, int, int]]]]:
    """Desenha cada palavra `(texto, itálico)` em Liberation Sans/Serif Regular/Italic, separadas
    pelo espaço da fonte, com entrelinha de 1,1 × `size_px`; devolve a imagem e as caixas da tinta
    das palavras por linha. `angle_deg` gira a imagem em torno do centro do bloco (anti-horário,
    como o PIL); as caixas são as de antes da rotação."""
    family = "Serif" if serif else "Sans"
    fonts = {it: ImageFont.truetype(_LIBERATION.format(family, "Italic" if it else "Regular"), size_px)
             for it in (False, True)}
    img = Image.new("RGB", page, "white")
    d = ImageDraw.Draw(img)
    space, pitch = fonts[False].getlength(" "), round(1.1 * size_px)
    truth = []
    for n, line in enumerate(lines):
        x, y = float(origin[0]), origin[1] + n * pitch
        row = []
        for word, italic in line:
            d.text((x, y), word, font=fonts[italic], fill=0)
            row.append(_ink_box(fonts[italic], word, x, y))
            x += fonts[italic].getlength(word) + space
        truth.append(row)
    if angle_deg:
        x1, y1, x2, y2 = bbox_of(truth)
        img = img.rotate(angle_deg, resample=Image.Resampling.BICUBIC,
                         center=((x1 + x2) / 2, (y1 + y2) / 2), fillcolor="white")
    return img, truth


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
    end1, text1 = _paragraph(d, reg, 398, seed=0)
    end2, text2 = _paragraph(d, reg, 574, seed=7)
    regions = [
        Region("title", (251, 157, 1395, 209), title),
        Region("text", (251, 257, 938, 288), sub),
        Region("section-header", (251, 342, 517, 368), "Primeira Versão"),
        Region("text", (151, 398, 1547, end1), text1),
        Region("text", (151, 574, 1547, end2), text2),
    ]
    return img, regions
