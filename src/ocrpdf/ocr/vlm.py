"""Motor de OCR baseado em VLM servido por uma API compatível com OpenAI (vLLM, Ollama...)."""

from __future__ import annotations

import base64
import io
import json
import math
import re
import time
from dataclasses import dataclass

import httpx
from PIL import Image

from .base import CATEGORIES, Region

_DOTS_PROMPT = """Please output the layout information from the PDF image, including each layout element's bbox, its category, and the corresponding text content within the bbox.

1. Bbox format: [x1, y1, x2, y2]

2. Layout Categories: The possible categories are ['Caption', 'Footnote', 'Formula', 'List-item', 'Page-footer', 'Page-header', 'Picture', 'Section-header', 'Table', 'Text', 'Title'].

3. Text Extraction & Formatting Rules:
    - Picture: For the 'Picture' category, the text field should be omitted.
    - Formula: Format its text as LaTeX.
    - Table: Format its text as HTML.
    - All Others (Text, Title, etc.): Format their text as Markdown.

4. Constraints:
    - The output text must be the original text from the image, with no translation.
    - All layout elements must be sorted according to human reading order.

5. Final Output: The entire output must be a single JSON object.
"""

# Para VLMs genéricos: mesmo esquema de saída, com regras explícitas de formatação inline.
_GENERIC_PROMPT = _DOTS_PROMPT.replace(
    "    - All Others (Text, Title, etc.): Format their text as Markdown.",
    "    - All Others (Text, Title, etc.): Format their text as Markdown. Preserve inline styles exactly:\n"
    "      *italic*, **bold**, and note/footnote markers as <sup>n</sup> (both in the body text and\n"
    "      at the start of the footnote itself). Join hyphenated line breaks; keep one paragraph per element.",
).replace(
    "5. Final Output: The entire output must be a single JSON object.",
    '5. Final Output: a JSON array of objects {"bbox": [...], "category": "...", "text": "..."}, nothing else.',
)


@dataclass(frozen=True)
class Preset:
    prompt: str
    bbox_space: str           # "pixels" (da imagem enviada) | "norm1000"
    text_prefix: str = ""
    factor: int = 28          # dimensões da imagem enviada múltiplas deste valor
    min_pixels: int = 3136
    max_pixels: int = 11_289_600
    temperature: float = 0.1
    top_p: float = 1.0


PRESETS: dict[str, Preset] = {
    # dots.ocr / dots.mocr: sem o prefixo de imagem o vLLM v1 insere um "\n" que piora a saída.
    "dots": Preset(_DOTS_PROMPT, "pixels", text_prefix="<|img|><|imgpad|><|endofimg|>"),
    "qwen25vl": Preset(_GENERIC_PROMPT, "pixels", max_pixels=2_560_000),
    "qwen3vl": Preset(_GENERIC_PROMPT, "norm1000", factor=32, max_pixels=2_560_000),
}


def smart_resize(height: int, width: int, factor: int, min_pixels: int, max_pixels: int) -> tuple[int, int]:
    """Mesma regra do processador Qwen-VL: lados múltiplos de `factor`, área em [min, max]."""
    h = max(factor, round(height / factor) * factor)
    w = max(factor, round(width / factor) * factor)
    if h * w > max_pixels:
        beta = math.sqrt(height * width / max_pixels)
        h = max(factor, math.floor(height / beta / factor) * factor)
        w = max(factor, math.floor(width / beta / factor) * factor)
    elif h * w < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h = math.ceil(height * beta / factor) * factor
        w = math.ceil(width * beta / factor) * factor
    return h, w


class VlmEngine:
    def __init__(
        self,
        base_url: str = "http://localhost:8000/v1",
        model: str = "model",
        preset: str = "dots",
        max_tokens: int = 12_000,
        timeout: float = 600.0,
        retries: int = 2,
        client: httpx.Client | None = None,
    ) -> None:
        self._preset = PRESETS[preset]
        self._model = model
        self._max_tokens = max_tokens
        self._retries = retries
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def analyze(self, image: Image.Image) -> list[Region]:
        p = self._preset
        h, w = smart_resize(image.height, image.width, p.factor, p.min_pixels, p.max_pixels)
        sent = image if (w, h) == image.size else image.resize((w, h), Image.LANCZOS)
        raw = self._chat(sent, p.prompt, max_tokens=self._max_tokens)
        if p.bbox_space == "norm1000":
            sx, sy = image.width / 1000, image.height / 1000
        else:
            sx, sy = image.width / w, image.height / h
        return [_scale(r, sx, sy, image.size) for r in parse_layout(raw)]

    def ask(self, image: Image.Image, question: str) -> str:
        thumb = image.copy()
        thumb.thumbnail((1024, 1024))
        h, w = smart_resize(thumb.height, thumb.width, self._preset.factor, self._preset.min_pixels, 1_048_576)
        return self._chat(thumb.resize((w, h)), question, max_tokens=16).strip()

    def _chat(self, image: Image.Image, prompt: str, max_tokens: int) -> str:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG")
        url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        body = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": self._preset.temperature,
            "top_p": self._preset.top_p,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": url}},
                    {"type": "text", "text": self._preset.text_prefix + prompt},
                ],
            }],
        }
        for attempt in range(self._retries + 1):
            try:
                resp = self._client.post("/chat/completions", json=body)
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"] or ""
            except (httpx.TransportError, httpx.HTTPStatusError):
                if attempt == self._retries:
                    raise
                time.sleep(2 ** attempt)
        raise AssertionError("unreachable")


# --- parse da resposta -----------------------------------------------------

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def parse_layout(raw: str) -> list[Region]:
    """Interpreta a lista JSON de regiões; recupera os itens válidos se a saída vier truncada."""
    text = _FENCE.sub("", raw.strip())
    try:
        data = json.loads(text)
        items = data if isinstance(data, list) else data.get("layout", data.get("elements", [data]))
    except json.JSONDecodeError:
        items = _salvage_objects(text)
    regions = []
    for item in items:
        if not isinstance(item, dict) or "bbox" not in item:
            continue
        category = str(item.get("category", "text")).strip().lower().replace("_", "-").replace(" ", "-")
        if category not in CATEGORIES:
            category = "text"
        try:
            x1, y1, x2, y2 = (float(v) for v in item["bbox"])
        except (TypeError, ValueError):
            continue
        regions.append(Region(category=category, bbox=(x1, y1, x2, y2), text=str(item.get("text", "") or "")))
    return regions


def _salvage_objects(text: str) -> list[dict]:
    decoder = json.JSONDecoder()
    found, pos = [], 0
    while (pos := text.find("{", pos)) != -1:
        try:
            obj, end = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            pos += 1
            continue
        found.append(obj)
        pos = end
    return found


def _scale(r: Region, sx: float, sy: float, size: tuple[int, int]) -> Region:
    x1, y1, x2, y2 = r.bbox
    w, h = size
    r.bbox = (
        max(0.0, min(x1, x2) * sx), max(0.0, min(y1, y2) * sy),
        min(float(w), max(x1, x2) * sx), min(float(h), max(y1, y2) * sy),
    )
    return r
