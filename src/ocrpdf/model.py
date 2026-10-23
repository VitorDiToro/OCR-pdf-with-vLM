"""Modelo de documento intermediário (serializado como document.json).

É a fronteira entre o OCR e a renderização: tudo que o renderizador precisa está
aqui, e o arquivo pode ser corrigido à mão e re-renderizado (`ocrpdf render`).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


@dataclass
class Span:
    """Trecho de texto com estilo uniforme."""

    text: str
    italic: bool = False
    bold: bool = False
    sup: bool = False
    sub: bool = False
    math: bool = False        # `text` é LaTeX inline
    note: str | None = None   # id da nota quando o trecho é uma chamada de nota


@dataclass
class Rule:
    """Filete colado a um bloco, relativo à área de texto (independe do tamanho da página)."""

    thickness_pt: float
    gap_pt: float          # folga entre o texto e o filete
    left_em: float         # início do filete − esquerda da área de texto, em em do corpo
    right_frac: float      # (direita da área − fim do filete) / largura da área, 0–1


@dataclass
class BlockStyle:
    align: str = "justify"            # justify | left | center | right
    indent_em: float = 0.0            # recuo da primeira linha
    block_indent_em: float = 0.0      # recuo do bloco inteiro, do lado do alinhamento (center: nenhum)
    font_size_pt: float | None = None
    full_width: bool = True           # False: bloco pertence a uma coluna
    rule_above: Rule | None = None    # filete colado acima do bloco
    rule_below: Rule | None = None    # filete colado abaixo do bloco
    page_break_before: bool = False   # força quebra de página antes do bloco (edição à mão)
    hidden: bool = False              # não renderiza o bloco (as notas sem chamada dele ficam)
    space_before_lines: float = 0     # linhas em branco antes do bloco (aceita fração)
    space_after_lines: float = 0      # linhas em branco depois do bloco, antes do próximo visível
    table_borders: str = "all"        # tabela: all (grade) | outer (só a borda de fora) | none
    bold: bool | None = None          # None: o padrão do tipo de bloco
    italic: bool | None = None


@dataclass
class Source:
    page: int = 0                                       # índice 0-based no PDF original
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)  # em pt, origem no topo


@dataclass
class Block:
    """Bloco de conteúdo.

    kind: title | heading | paragraph | list_item | caption | table | formula |
          figure | endnote
    """

    kind: str
    spans: list[Span] = field(default_factory=list)
    level: int = 0           # nível do título (1 = título do documento)
    html: str = ""           # tabela (HTML sanitizado)
    latex: str = ""          # fórmula em destaque
    image: str = ""          # figura: caminho relativo ao document.json
    note: str | None = None  # endnote: id da nota que ocupa esta posição
    trailing_notes: list[str] = field(default_factory=list)  # notas sem chamada ancoradas aqui
    style: BlockStyle = field(default_factory=BlockStyle)
    source: Source = field(default_factory=Source)

    def plain_text(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass
class Note:
    id: str
    label: str               # rótulo como aparece no original ("1", "*", "a"...)
    kind: str                # footnote | endnote
    page: int
    blocks: list[Block] = field(default_factory=list)


@dataclass
class PageGeometry:
    width_pt: float = 612.0
    height_pt: float = 792.0
    margin_top_pt: float = 72.0
    margin_right_pt: float = 72.0
    margin_bottom_pt: float = 72.0
    margin_left_pt: float = 72.0


@dataclass
class OutputFormat:
    """Formato de saída escolhido no editor (o original fica em `Document.page` etc.)."""

    page_size: str = "original"       # original | a4 | <largura>x<altura> em cm (output_format.parse_page_size)
    margin_cm: float = 2.5
    font_size_pt: float | None = None
    header: bool = True


@dataclass
class Document:
    source: str = ""
    page: PageGeometry = field(default_factory=PageGeometry)
    font_family: str = "serif"
    body_font_pt: float = 11.0
    line_height: float = 1.2
    paragraph_gap_pt: float = 6.0
    columns: int = 1
    running_header: str = ""
    running_header_align: str = "center"
    running_header_font_pt: float | None = None   # None: 0,85 × corpo
    header_on_first_page: bool = True
    lang: str = ""
    page_numbers: bool = False
    blocks: list[Block] = field(default_factory=list)
    notes: dict[str, Note] = field(default_factory=dict)
    output: OutputFormat | None = None
    source_path: str = ""             # PDF escaneado de origem (gravado pelo analyze/convert, ou à mão); o editor lê
    schema_version: int = SCHEMA_VERSION

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Document:
        return _document_from_dict(json.loads(path.read_text(encoding="utf-8")))


# --- desserialização -------------------------------------------------------

def _build(cls: type, data: dict[str, Any]) -> Any:
    """Instancia um dataclass ignorando chaves desconhecidas (tolerante a edições)."""
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


def _block_from_dict(d: dict[str, Any]) -> Block:
    block = _build(Block, d)
    block.spans = [_build(Span, s) for s in d.get("spans", [])]
    style = d.get("style", {})
    block.style = _build(BlockStyle, style)
    block.style.rule_above = _build(Rule, style["rule_above"]) if style.get("rule_above") else None
    block.style.rule_below = _build(Rule, style["rule_below"]) if style.get("rule_below") else None
    src = d.get("source", {})
    block.source = Source(page=src.get("page", 0), bbox=tuple(src.get("bbox", (0, 0, 0, 0))))
    return block


def _document_from_dict(d: dict[str, Any]) -> Document:
    doc = _build(Document, d)
    doc.page = _build(PageGeometry, d.get("page", {}))
    doc.blocks = [_block_from_dict(b) for b in d.get("blocks", [])]
    doc.output = _build(OutputFormat, d["output"]) if d.get("output") else None
    doc.notes = {}
    for nid, n in d.get("notes", {}).items():
        note = _build(Note, n)
        note.blocks = [_block_from_dict(b) for b in n.get("blocks", [])]
        doc.notes[nid] = note
    return doc
