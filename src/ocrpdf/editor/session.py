"""Sessão de edição do document.json: núcleo do editor, sem HTTP."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from ocrpdf.editor.compact import from_compact, to_compact
from ocrpdf.model import Block, Document, OutputFormat, Span
from ocrpdf.output_format import apply_format, validate_output_format
from ocrpdf.render.html import block_font_pt

SPLIT_MARK = ""   # ponto de divisão no texto da página (uso privado do Unicode: o OCR não o gera)
_SPLITTABLE = ("title", "heading", "paragraph", "list_item", "caption")


class EditError(Exception):
    """Edição recusada; a mensagem é mostrada ao usuário."""


class ConflictError(EditError):
    """O arquivo mudou no disco desde que foi aberto ou gravado."""


def output_scale(doc: Document, fmt: OutputFormat) -> float:
    """Fator entre a escala medida do documento e a da saída."""
    if fmt.font_size_pt and doc.body_font_pt:
        return fmt.font_size_pt / doc.body_font_pt
    return 1.0


def _strip_edge(spans: list[Span], at_end: bool) -> list[Span]:
    """Tira os espaços e quebras da borda da divisão, e os trechos que ficam vazios."""
    spans = list(spans)
    while spans:
        k = -1 if at_end else 0
        text = spans[k].text.rstrip() if at_end else spans[k].text.lstrip()
        if text:
            spans[k] = replace(spans[k], text=text)
            break
        spans.pop(k)
    return spans


def _no_constant(name: str) -> None:
    """json.loads aceita NaN/Infinity, que não são JSON e corromperiam o document.json."""
    raise EditError(f"{name} não é um número válido em JSON")


class EditSession:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.reload()

    def reload(self) -> None:
        """Lê o arquivo do disco e descarta as alterações em memória; se falhar, nada muda."""
        try:
            mtime = self.path.stat().st_mtime_ns   # antes de ler: se mudar no meio, o salvar recusa
            doc = Document.load(self.path)
        except FileNotFoundError:
            raise EditError(f"documento não encontrado: {self.path}") from None
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as e:
            raise EditError(f"não foi possível ler {self.path}: {e}") from e
        try:   # output editado à mão: erro claro aqui, não 500 em todas as páginas
            if doc.output is not None:
                validate_output_format(doc.output)
        except ValueError as e:
            raise EditError(f"formato gravado (output) inválido em {self.path}: {e}") from None
        self.doc, self._mtime, self.dirty = doc, mtime, False

    # --- consulta ---------------------------------------------------------

    def pages(self) -> list[int]:
        return sorted({b.source.page for b in self.doc.blocks})

    def page_indices(self, page: int) -> list[int]:
        return [i for i, b in enumerate(self.doc.blocks) if b.source.page == page]

    def page_text(self, page: int) -> str:
        data = [to_compact(self.doc.blocks[i]) for i in self.page_indices(page)]
        return json.dumps(data, ensure_ascii=False, indent=2)

    def page_blocks(self, page: int, fmt: OutputFormat) -> list[dict]:
        scale = output_scale(self.doc, fmt)
        rows = []
        for n, i in enumerate(self.page_indices(page), start=1):
            b = self.doc.blocks[i]
            rows.append({
                "n": n, "index": i, "kind": b.kind, "hidden": b.style.hidden,
                "font_pt": block_font_pt(self.doc, b) * scale,
            })
        return rows

    # --- edição (só em memória) -------------------------------------------

    def apply_page(self, page: int, text: str) -> None:
        self._commit(self._parse_page(page, text))

    def _commit(self, parsed: list[tuple[int, Block]]) -> None:
        for i, b in parsed:
            if b != self.doc.blocks[i]:   # o mesmo texto de volta (Descartar) não suja a sessão
                self.doc.blocks[i] = b
                self.dirty = True

    def _parse_page(self, page: int, text: str) -> list[tuple[int, Block]]:
        """(índice no documento, bloco novo) de cada bloco da página; não muda a sessão."""
        indices = self.page_indices(page)
        try:
            data = json.loads(text, parse_constant=_no_constant)
        except json.JSONDecodeError as e:
            raise EditError(f"JSON inválido na linha {e.lineno}, coluna {e.colno}: {e.msg}") from e
        if not isinstance(data, list):
            raise EditError("o texto da página deve ser uma lista JSON de blocos")
        if len(data) != len(indices):
            raise EditError(
                f"a página tem {len(indices)} blocos e o texto tem {len(data)}; "
                "não é possível remover nem criar blocos: use Ocultar para remover um bloco"
            )
        new = []
        for n, (i, item) in enumerate(zip(indices, data), start=1):
            try:
                new.append(from_compact(self.doc.blocks[i], item))
            except ValueError as e:
                raise EditError(f"bloco {n}: {e}") from e
        return list(zip(indices, new))

    def split_block(self, page: int, text: str) -> int:
        """Aplica o texto da página e divide em dois o bloco onde está SPLIT_MARK (o cursor da tela);
        devolve a posição na página (0-based) do bloco novo. Se recusar, a sessão não muda."""
        parsed = self._parse_page(page, text)
        hits = [(k, j) for k, (_, b) in enumerate(parsed) for j, s in enumerate(b.spans) if SPLIT_MARK in s.text]
        marks = sum(s.text.count(SPLIT_MARK) for _, b in parsed for s in b.spans)
        if not marks:
            raise EditError('ponha o cursor dentro do texto ("text") do bloco, onde ele deve ser dividido')
        if marks > 1:
            raise EditError("a marca de divisão deve aparecer uma vez só")
        k, j = hits[0]
        i, block = parsed[k]
        if block.kind not in _SPLITTABLE:
            raise EditError("só blocos de texto podem ser divididos")
        span = block.spans[j]
        before, after = span.text.split(SPLIT_MARK)
        if span.math or (span.note and before and after):
            raise EditError("não é possível dividir dentro de uma fórmula ou de uma chamada de nota")
        first = _strip_edge(block.spans[:j] + [replace(span, text=before)], at_end=True)
        second = _strip_edge([replace(span, text=after)] + block.spans[j + 1:], at_end=False)
        if not first or not second:
            raise EditError("o cursor está no começo ou no fim do texto do bloco: não há o que dividir")
        # o que é "antes" do bloco fica no 1º, o que é "depois" no 2º; o resto do estilo vale para os dois
        new = deepcopy(block)
        new.spans = second
        new.style = replace(new.style, page_break_before=False, space_before_lines=0, rule_above=None)
        parsed[k] = (i, replace(block, spans=first, trailing_notes=[], style=replace(block.style, rule_below=None)))
        self._commit(parsed)
        self.doc.blocks.insert(i + 1, new)
        self.dirty = True
        return k + 1

    def format(self) -> OutputFormat:
        return self.doc.output or OutputFormat()

    def set_format(self, fmt: OutputFormat) -> None:
        try:
            apply_format(self.doc, fmt)
        except ValueError as e:
            raise EditError(str(e)) from e
        if fmt != self.format():
            self.doc.output = fmt
            self.dirty = True

    def justify_all(self) -> int:
        changed = 0
        for b in self.doc.blocks:
            if b.kind == "paragraph" and b.style.align != "justify":
                b.style.align = "justify"
                changed += 1
        if changed:
            self.dirty = True
        return changed

    # --- gravação ---------------------------------------------------------

    def save(self) -> None:
        try:
            mtime = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            raise ConflictError(
                f"o documento não está mais em {self.path} (apagado ou renomeado); restaure o arquivo e "
                "use Recarregar do disco"
            ) from None
        if mtime != self._mtime:
            raise ConflictError(
                "o document.json mudou no disco desde que foi aberto ou gravado (outro programa ou outra "
                "janela); use Recarregar do disco e salve de novo"
            )
        backup = self.path.with_name(self.path.stem + ".orig.json")   # document.json → document.orig.json
        if not backup.exists():
            backup.write_bytes(self.path.read_bytes())
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.doc.save(tmp)
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)
        self._mtime = self.path.stat().st_mtime_ns
        self.dirty = False
