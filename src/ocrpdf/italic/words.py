"""Palavras (tokens) de uma lista de `Span` e aplicação de marcas de itálico por palavra."""

from __future__ import annotations

from dataclasses import replace

from ..inline import _normalize
from ..model import Span


def has_math(spans: list[Span]) -> bool:
    return any(s.math for s in spans)


def _chars(spans: list[Span]) -> list[tuple[str, int, bool]]:
    """(caractere, índice do span, é separador). Espaço dentro de span math não separa."""
    return [(c, i, c.isspace() and not s.math) for i, s in enumerate(spans) for c in s.text]


def _token_ranges(chars: list[tuple[str, int, bool]]) -> list[tuple[int, int]]:
    """Intervalos [ini, fim) de cada sequência máxima sem separador."""
    out, start = [], None
    for k, (_, _, sep) in enumerate(chars):
        if sep and start is not None:
            out.append((start, k))
            start = None
        elif not sep and start is None:
            start = k
    if start is not None:
        out.append((start, len(chars)))
    return out


def tokens(spans: list[Span]) -> list[str]:
    chars = _chars(spans)
    return ["".join(c for c, _, _ in chars[a:b]) for a, b in _token_ranges(chars)]


def italic_flags(spans: list[Span]) -> list[bool]:
    """Token é itálico se todo caractere não sobrescrito dele está em span itálico."""
    chars = _chars(spans)
    flags = []
    for a, b in _token_ranges(chars):
        body = [spans[i].italic for _, i, _ in chars[a:b] if not spans[i].sup]
        # token só de sobrescrito (ex.: "1" solto): vale o estilo dele mesmo
        body = body or [spans[i].italic for _, i, _ in chars[a:b]]
        flags.append(all(body))
    return flags


def apply_flags(spans: list[Span], flags: list[bool], start: int = 0) -> tuple[list[Span], int]:
    """Aplica `flags[start:]` aos tokens dos spans; devolve os spans e o índice do próximo token.

    O espaço entre dois tokens marcados também fica itálico. Spans math não são alterados.
    """
    chars = _chars(spans)
    ranges = _token_ranges(chars)
    end = start + len(ranges)
    if end > len(flags):
        raise ValueError(f"faltam marcas: {len(ranges)} tokens a partir de {start}, {len(flags)} marcas")
    italic = [False] * len(chars)
    for t, (a, b) in enumerate(ranges):
        for k in range(a, b):
            italic[k] = flags[start + t]
        if t and flags[start + t] and flags[start + t - 1]:
            for k in range(ranges[t - 1][1], a):
                italic[k] = True

    pieces: list[tuple[tuple[int, bool], list[str]]] = []
    for k, (c, i, _) in enumerate(chars):
        it = spans[i].italic if spans[i].math else italic[k]
        if pieces and pieces[-1][0] == (i, it):
            pieces[-1][1].append(c)
        else:
            pieces.append(((i, it), [c]))
    out = [replace(spans[i], text="".join(cs), italic=it) for (i, it), cs in pieces]
    return _normalize(out), end
