"""Sessão de edição do document.json: núcleo do editor, sem HTTP."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ocrpdf.editor.compact import from_compact, to_compact
from ocrpdf.model import Document, OutputFormat
from ocrpdf.output_format import apply_format
from ocrpdf.render.html import block_font_pt


class EditError(Exception):
    """Edição recusada; a mensagem é mostrada ao usuário."""


class ConflictError(EditError):
    """O arquivo mudou no disco desde que foi aberto ou gravado."""


def output_scale(doc: Document, fmt: OutputFormat) -> float:
    """Fator entre a escala medida do documento e a da saída."""
    if fmt.font_size_pt and doc.body_font_pt:
        return fmt.font_size_pt / doc.body_font_pt
    return 1.0


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
        for i, b in zip(indices, new):
            self.doc.blocks[i] = b
        self.dirty = True

    def format(self) -> OutputFormat:
        return self.doc.output or OutputFormat()

    def set_format(self, fmt: OutputFormat) -> None:
        try:
            apply_format(self.doc, fmt)
        except ValueError as e:
            raise EditError(str(e)) from e
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
        if self.path.stat().st_mtime_ns != self._mtime:
            raise ConflictError(
                "o document.json mudou no disco desde que foi aberto ou gravado (outro programa ou outra "
                "janela); use Recarregar do disco e salve de novo"
            )
        backup = self.path.with_name("document.orig.json")
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
