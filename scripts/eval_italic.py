#!/usr/bin/env python
"""Avalia o itálico por palavra contra o gabarito: dots, R1, R2 e (opcional) o PDF de referência.

Não chama servidor nenhum: refaz a evidência a partir do cache da pasta de trabalho (OCR em
`pages/`, marcas do 2º modelo em `italic/`).

    uv run python scripts/eval_italic.py --pdf livro.pdf --work livro_work --gold gabarito.json \
        --italic-model <id usado nas marcas> [--reference ref.pdf] [--pages pares] [--rule r1|r2|ambas]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pypdfium2 as pdfium

from ocrpdf.italic.evaluate import (gold_flags, page_flags, page_tokens, parse_pages, project, reference_tokens,
                                    score)
from ocrpdf.italic.stage import final_flags
from ocrpdf.pipeline import ItalicConfig, Pipeline


class _NoEngine:
    """Engine que nunca é chamado: só o cache é lido."""

    def analyze(self, image):
        raise RuntimeError("a avaliação não chama o OCR")

    def ask(self, image, question, max_tokens=128):
        raise RuntimeError("a avaliação não chama o 2º modelo")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pdf", type=Path, required=True, help="PDF escaneado")
    ap.add_argument("--work", type=Path, required=True, help="pasta de trabalho com o cache")
    ap.add_argument("--gold", type=Path, required=True, help="gabarito JSON")
    ap.add_argument("--italic-model", required=True, help="id do 2º modelo nas marcas em cache")
    ap.add_argument("--reference", type=Path, help="PDF de referência (outra ferramenta)")
    ap.add_argument("--pages", default="", help="ímpares | pares | 1-3,7 (páginas do escaneado, 1-based)")
    ap.add_argument("--rule", choices=["r1", "r2", "ambas"], default="ambas")
    ap.add_argument("--dpi", type=int, default=200)
    args = ap.parse_args(argv)

    doc = pdfium.PdfDocument(str(args.pdf))
    total = len(doc)
    doc.close()
    raw = json.loads(args.gold.read_text(encoding="utf-8"))
    gold_pages = {int(p): v for p, v in raw["pages"].items()}
    # só as páginas anotadas no gabarito são avaliadas (página fora dele não é "sem itálico")
    pages = [p for p in parse_pages(args.pages, total) if p in gold_pages]

    pipe = Pipeline(_NoEngine(), args.work, dpi=args.dpi, italic=ItalicConfig(_NoEngine(), args.italic_model))
    evidence = pipe.collect_evidence(args.pdf)
    toks = page_tokens(evidence)

    gold, missing = gold_flags(toks, gold_pages)

    rows = {"dots": page_flags(evidence, lambda ev: ev.dots)}
    for rule in ("r1", "r2") if args.rule == "ambas" else (args.rule,):
        rows[rule.upper()] = page_flags(evidence, lambda ev, r=rule: final_flags(ev, r))
    if args.reference:
        rows["referência"] = project(reference_tokens(args.reference), toks)

    print(f"{'fonte':<12}{'precisão':>10}{'cobertura':>11}{'tp':>7}{'fp':>7}{'fn':>7}")
    for name, pred in rows.items():
        s = score(pred, gold, pages)
        print(f"{name:<12}{s.precision:>10.1%}{s.recall:>11.1%}{s.tp:>7}{s.fp:>7}{s.fn:>7}")
    print(f"\nPáginas avaliadas: {', '.join(map(str, pages))}")
    shown = [m for m in missing if int(m.split(":", 1)[0]) in pages]
    print(f"Trechos do gabarito não encontrados: {len(shown)}")
    for m in shown:
        print(f"  {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
