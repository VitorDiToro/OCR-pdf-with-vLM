"""Marcação de itálico por um 2º modelo: recebe o recorte do bloco e a transcrição exata e devolve
o MESMO texto com <i>…</i>. A resposta só vale se, sem as marcas, os tokens forem idênticos."""

from __future__ import annotations

import logging
import re

from PIL import Image

from ..ocr.base import OcrEngine

log = logging.getLogger(__name__)

PROMPT_VERSION = 1

PROMPT = """The image shows a block of printed text. Below is its exact transcription.
Return the SAME transcription, character for character, wrapping every word or phrase
that is printed in italics in <i></i> tags. Do not change, add, remove or reorder any
character, do not fix spelling, do not translate. Output only the text.

Transcription:
{text}"""

_FENCE = re.compile(r"^\s*```[A-Za-z]*\s*\n(.*?)\n?\s*```\s*$", re.S)
_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
_ITALIC = {"i", "em"}


def _strip_fence(answer: str) -> str:
    m = _FENCE.match(answer)
    return m.group(1) if m else answer


def _flag_chars(answer: str) -> list[tuple[str, bool]] | None:
    """(caractere, dentro de marca de itálico); None se houver outra tag ou marcas desbalanceadas."""
    out: list[tuple[str, bool]] = []
    depth, pos = 0, 0
    for m in _TAG.finditer(answer):
        out += [(c, depth > 0) for c in answer[pos:m.start()]]
        pos = m.end()
        name = m.group().strip("<>/ ").split()[0].lower()
        if name not in _ITALIC:
            return None
        if m.group().startswith("</"):
            depth -= 1
            if depth < 0:
                return None
        else:
            depth += 1
    out += [(c, depth > 0) for c in answer[pos:]]
    return out if depth == 0 else None


def mark_italics(engine: OcrEngine, crop: Image.Image, tokens: list[str]) -> list[bool] | None:
    """Itálico por token, ou None se a resposta alterar o texto (ou o modelo falhar)."""
    if not tokens:
        return None
    text = " ".join(tokens)
    try:
        answer = engine.ask(crop, PROMPT.format(text=text), max_tokens=len(text) // 2 + 128)
    except Exception as exc:  # rede, servidor, resposta inválida
        log.warning("2º modelo falhou na marcação de itálico: %s", exc)
        return None
    chars = _flag_chars(_strip_fence(answer))
    if chars is None:
        return None
    words: list[list[tuple[str, bool]]] = [[]]
    for c, italic in chars:
        if c.isspace():
            if words[-1]:
                words.append([])
        else:
            words[-1].append((c, italic))
    if not words[-1]:
        words.pop()
    if ["".join(c for c, _ in w) for w in words] != tokens:
        return None
    return [all(italic for _, italic in w) for w in words]
