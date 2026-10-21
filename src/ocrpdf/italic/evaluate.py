"""Avaliação do itálico por palavra: gabarito feito à mão e conversão de referência de outra ferramenta.

Gabarito JSON: `{"document": str, "pages": {"<página 1-based do escaneado>": ["trecho em itálico", ...]}}`.
Um trecho pode ter um intervalo entre colchetes, `"afirma diante do aparelho [sua] humanidade"`: o
trecho inteiro (sem os colchetes) precisa casar com os tokens da página, mas só os de dentro dos
colchetes são itálico (útil para uma palavra comum, cuja 1ª ocorrência pode ser romana). Sem colchetes,
todos os tokens do trecho são itálico.

A referência é um PDF de texto (LaTeX, por exemplo) cujas fontes itálicas se reconhecem pelo nome. Ela
não tem a mesma paginação do escaneado (omite folha de rosto, outro papel), então é alinhada pelo
texto, não pelo número da página. As marcas são sempre por token, o mesmo de `words.tokens`.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .stage import BlockEvidence

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"',
                         "«": '"', "»": '"'})
_ITALIC_FONT = re.compile(r"Italic|Oblique|SFTI|SFSI|SFSL|CMTI|-It\b")


def normalize(token: str) -> str:
    """NFC, aspas tipográficas retas, sem pontuação nas pontas, minúsculas."""
    t = unicodedata.normalize("NFC", token).translate(_QUOTES)
    start, end = 0, len(t)
    while start < end and not t[start].isalnum():
        start += 1
    while end > start and not t[end - 1].isalnum():
        end -= 1
    return t[start:end].lower()


def is_italic_font(name: str) -> bool:
    return bool(_ITALIC_FONT.search(name))


# -- gabarito

def gold_flags(page_tokens: dict[int, list[str]],
               gold: dict[int, list[str]]) -> tuple[dict[int, list[bool]], list[str]]:
    """Marcas do gabarito por página e os trechos não encontrados (`"página: trecho"`).

    Só as páginas presentes no gabarito entram: lista vazia = anotada sem itálico (pontuada); página
    ausente = não anotada (fica fora, `score` a ignora). Os trechos de cada página devem estar na ordem
    de leitura: cada um é buscado a partir do fim do anterior (a mesma sequência pode se repetir) e,
    se não houver adiante, é dado como não encontrado.
    """
    flags: dict[int, list[bool]] = {}
    missing: list[str] = []
    for page, phrases in gold.items():
        toks = page_tokens.get(page, [])
        flags[page] = [False] * len(toks)
        norm = [normalize(t) for t in toks]
        cursor = 0
        for phrase in phrases:
            want, mask = _parse_phrase(phrase)
            at = _find(norm, want, cursor)
            if at is None:
                missing.append(f"{page}: {phrase}")
                continue
            flags[page][at:at + len(want)] = mask
            cursor = at + len(want)
    return flags, missing


def _parse_phrase(phrase: str) -> tuple[list[str], list[bool]]:
    """Tokens normalizados do trecho e quais deles são itálico (os de dentro de `[...]`, ou todos)."""
    want, mask, inside, bracketed = [], [], False, False
    for raw in phrase.split():
        if raw.startswith("["):
            inside = bracketed = True
        want.append(normalize(raw.strip("[]")))
        mask.append(inside)
        if raw.endswith("]"):
            inside = False
    return want, mask if bracketed else [True] * len(want)


def _find(norm: list[str], want: list[str], start: int) -> int | None:
    if not want:
        return None
    for i in range(start, len(norm) - len(want) + 1):
        if norm[i:i + len(want)] == want:
            return i
    return None


# -- referência

def reference_tokens(pdf: Path) -> list[tuple[str, bool]]:
    """(palavra, itálico) do PDF de referência, na ordem de leitura do PDF.

    Os trechos vêm do `visitor_text` do pypdf com a fonte de cada um. Um trecho só continua a palavra
    anterior se está na mesma linha e na mesma fonte (itálica ou não); fora disso há uma quebra.
    """
    from pypdf import PdfReader

    out: list[tuple[str, bool]] = []
    for page in PdfReader(pdf).pages:
        pieces: list[tuple[str, bool, float]] = []

        def visit(text, cm, tm, font, size):
            if text:
                name = str(font.get("/BaseFont", "")) if font else ""
                pieces.append((text, is_italic_font(name), round(float(tm[5]), 1)))

        page.extract_text(visitor_text=visit)
        out += _split(pieces)
    return out


def _split(pieces: list[tuple[str, bool, float]]) -> list[tuple[str, bool]]:
    words: list[tuple[str, bool]] = []
    cur, cur_italic, last = "", False, None
    for text, italic, y in pieces:
        if cur and (italic != cur_italic or y != last):
            words.append((cur, cur_italic))
            cur = ""
        for ch in text:
            if ch.isspace():
                if cur:
                    words.append((cur, cur_italic))
                    cur = ""
            else:
                if not cur:
                    cur_italic = italic
                cur += ch
        last = y
    if cur:
        words.append((cur, cur_italic))
    return words


def project(ref: list[tuple[str, bool]], page_tokens: dict[int, list[str]]) -> dict[int, list[bool]]:
    """Marcas da referência nos tokens do escaneado, alinhadas pelo texto normalizado.

    Só os trechos em que os dois textos coincidem recebem a marca; o resto fica sem itálico."""
    pages = sorted(page_tokens)
    flat = [(p, i) for p in pages for i in range(len(page_tokens[p]))]
    mine = [normalize(page_tokens[p][i]) for p, i in flat]
    theirs = [normalize(w) for w, _ in ref]
    out = {p: [False] * len(page_tokens[p]) for p in pages}
    matcher = difflib.SequenceMatcher(None, theirs, mine, autojunk=False)
    for a, b, size in matcher.get_matching_blocks():
        for k in range(size):
            p, i = flat[b + k]
            out[p][i] = ref[a + k][1]
    return out


# -- pontuação

@dataclass
class Score:
    precision: float
    recall: float
    tp: int
    fp: int
    fn: int


def score(pred: dict[int, list[bool]], gold: dict[int, list[bool]], pages: Iterable[int]) -> Score:
    """Precisão e cobertura por palavra nas páginas dadas que o gabarito anota (0,0 quando o
    denominador é zero)."""
    tp = fp = fn = 0
    for page in pages:
        if page not in gold:
            continue
        for p, g in zip(pred.get(page, []), gold.get(page, [])):
            tp += p and g
            fp += p and not g
            fn += g and not p
    return Score(tp / (tp + fp) if tp + fp else 0.0, tp / (tp + fn) if tp + fn else 0.0, tp, fp, fn)


# -- páginas

def parse_pages(spec: str | None, total: int) -> list[int]:
    """Páginas 1-based: "ímpares", "pares" (também sem acento), "1-3,7" ou tudo se vazio."""
    key = (spec or "").strip().lower()
    if key in ("ímpares", "impares"):
        return list(range(1, total + 1, 2))
    if key == "pares":
        return list(range(2, total + 1, 2))
    if not key:
        return list(range(1, total + 1))
    pages: set[int] = set()
    for part in key.split(","):
        a, sep, b = part.strip().partition("-")
        start = int(a) if a else 1
        end = int(b) if b else (total if sep else start)
        pages.update(range(max(1, start), min(total, end) + 1))
    return sorted(pages)


# -- evidência

def page_tokens(evidence: dict[tuple[int, int], BlockEvidence]) -> dict[int, list[str]]:
    """Tokens de cada página (1-based): os dos blocos de evidência, na ordem das regiões."""
    out: dict[int, list[str]] = {}
    for (page, _), ev in sorted(evidence.items()):
        out.setdefault(page + 1, []).extend(ev.tokens)
    return out


def page_flags(evidence: dict[tuple[int, int], BlockEvidence], pick) -> dict[int, list[bool]]:
    """Marcas por página, com `pick(ev)` dando as marcas de cada bloco (mesma ordem de `page_tokens`)."""
    out: dict[int, list[bool]] = {}
    for (page, _), ev in sorted(evidence.items()):
        out.setdefault(page + 1, []).extend(pick(ev))
    return out
