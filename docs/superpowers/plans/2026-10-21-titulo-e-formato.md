# Título (alinhamento e filete) e formato de saída — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Título/subtítulos de uma linha com o alinhamento do original, filete colado a títulos
reproduzido, e opções `--page-size`, `--margin`, `--font-size` na saída.

**Architecture:** O cache `pages/*.json` passa a guardar só o resultado do OCR; as medidas da
`probe` (métricas e filetes) são sempre recalculadas da imagem rasterizada. O `assemble` ganha
"posições à esquerda recorrentes" para decidir o alinhamento de linhas únicas e converte filetes
medidos em `Rule` relativo à área de texto. O formato de saída é aplicado na renderização sobre
uma cópia do `Document`.

**Tech Stack:** Python ≥ 3.11, numpy, Pillow, pypdfium2, WeasyPrint 70, pytest, pypdf (dev).

**Spec:** `docs/superpowers/specs/2026-10-21-titulo-italico-design.md` (seções "Item 4 — título" e
"Formato de saída").

## Global Constraints

- Comandos a partir da raiz da worktree; testes com `uv run pytest`.
- Código, comentários, mensagens e textos de ajuda em português; seguir o estilo existente
  (`from __future__ import annotations`, dataclasses, type hints, funções privadas com `_`).
- O `document.json` guarda sempre o formato **original medido**; `--page-size`, `--margin` e
  `--font-size` só agem na renderização (no `convert` e no `render`).
- `--page-size original|a4|<largura>x<altura>` em **cm**, aceita vírgula decimal; fora de
  5–100 cm → erro claro. `--margin` em cm, padrão **2,5**, só quando o tamanho não é `original`.
  `--font-size` em pt escala corpo, títulos, notas e cabeçalho proporcionalmente.
- A4 = 595,28 × 841,89 pt; 1 cm = 72/2,54 pt.
- Filete: extensão guardada **relativa à área de texto** (não em pt absolutos).
- Não quebrar o que funciona: `tests/test_render.py` (notas na página da chamada) segue passando.
- Mensagens de commit em português terminando com:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01PvrxKGQAb8Zah8oBHy1LWq
  ```

## Review Focus

1. Cache antigo (sem `rules`, com ou sem `metrics`) carregado depois da mudança: deve funcionar e
   ganhar as medidas novas sem `--force` (Task 1, teste com JSON antigo).
2. Título realmente centralizado cujo x coincide por acaso com **um único** outro bloco (ex.: outro
   título centralizado de mesma largura): deve continuar `center`, porque a posição só conta com
   ≥ 2 **outros** blocos (Task 2, teste dedicado).
3. Linha de texto comum logo abaixo de um título confundida com filete: texto não é filete
   (Task 3, teste com subtítulo colado ao título).
4. `--page-size` pequeno demais para as margens (ex.: `6x6` com 2,5 cm): erro claro em vez de PDF
   quebrado (Task 4, teste de validação).
5. `--font-size` com notas e cabeçalho: notas continuam na página da chamada (Task 4, reaproveita
   o documento sintético de `tests/test_render.py`).

---

### Task 1: Medidas sempre recalculadas da imagem

**Files:**
- Create: `tests/synth.py` (utilitários de teste), `tests/test_pipeline.py`
- Modify: `src/ocrpdf/pipeline.py` (`Pipeline.analyze`, `_analyze_page`; novo `_measure`)

**Interfaces:**
- Produces (tests/synth.py):
  - `make_pdf(path: Path, images: list[Image.Image], dpi: int = 200) -> Path` — PDF de páginas-imagem
    (`Image.save(..., save_all=True, resolution=dpi)`).
  - `class FakeEngine` (implementa `OcrEngine`): `__init__(self, responses: list[list[Region]], answers: list[str] | None = None)`;
    `analyze(image)` devolve uma cópia profunda da próxima resposta e incrementa `self.calls`;
    `ask(image, question, max_tokens=128)` devolve a próxima de `answers` (padrão `"sans"`).
  - `benjamin_like_page() -> tuple[Image.Image, list[Region]]` — página 1700×2200 px (Letter a
    200 dpi) imitando a p. 1 do exemplo: título em negrito (Liberation Sans Bold, ~47 px) em
    x = 251 com filete de 2 px de x = 254 a 1545, 2 px abaixo do texto; subtítulo em x = 251;
    "Primeira Versão" (section-header) em x = 251; dois parágrafos de 5 linhas (Liberation Sans
    ~29 px) de x = 151 a 1547 com recuo de 1ª linha até x ≈ 249. As regiões devolvidas têm os
    bboxes do dots (título `[251,157,1395,209]`, **sem** o filete) e textos fixos.
- Produces (pipeline): `Pipeline._measure(layout: PageLayout, image: Image.Image) -> PageLayout`
  — recalcula `metrics` das regiões em `_MEASURED`, recorta figuras; a Task 3 acrescenta filetes.
  O cache continua no mesmo arquivo `pages/page_NNNN.json`, agora com as medidas atuais.

- [ ] **Step 1: Escrever os testes**

```python
def test_cached_page_gets_fresh_metrics_without_new_ocr(tmp_path):
    image, regions = benjamin_like_page()
    pdf = make_pdf(tmp_path / "in.pdf", [image])
    engine = FakeEngine([regions])
    Pipeline(engine, tmp_path / "w", workers=1).analyze(pdf, font="sans")
    cache = tmp_path / "w" / "pages" / "page_0001.json"
    data = json.loads(cache.read_text())
    for r in data["regions"]:          # cache "antigo": sem medidas
        r.pop("metrics", None)
    cache.write_text(json.dumps(data))
    doc = Pipeline(engine, tmp_path / "w", workers=1).analyze(pdf, font="sans")
    assert engine.calls == 1                       # não refez o OCR
    assert all(r.metrics for r in PageLayout.load(cache).regions if r.category == "text")
    assert 9.5 <= doc.body_font_pt <= 11.0         # corpo medido de novo (~10 pt)

def test_force_redoes_ocr(tmp_path):
    image, regions = benjamin_like_page()
    pdf = make_pdf(tmp_path / "in.pdf", [image])
    engine = FakeEngine([regions, regions])
    Pipeline(engine, tmp_path / "w", workers=1).analyze(pdf, font="sans")
    Pipeline(engine, tmp_path / "w", workers=1).analyze(pdf, font="sans", force=True)
    assert engine.calls == 2
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL no 1º teste (`metrics` ausentes no cache carregado).

- [ ] **Step 3: Implementar**

Em `analyze`: (1) OCR só das páginas sem cache (como hoje, rasterização em série e chamadas em
`ThreadPool`), salvando o resultado bruto; (2) para **todas** as páginas pedidas, rasterizar de
novo em série, chamar `_measure` e salvar o cache; (3) pergunta da fonte e `assemble` como hoje.
`_analyze_page` passa a só chamar o engine e montar o `PageLayout`.

- [ ] **Step 4: Rodar os testes**

Run: `uv run pytest -v`
Expected: PASS (incluindo `tests/test_render.py`).

- [ ] **Step 5: Commit** — `pipeline: medidas da probe sempre recalculadas; cache guarda só o OCR`

---

### Task 2: Alinhamento por posições à esquerda recorrentes

**Files:**
- Modify: `src/ocrpdf/assemble.py` (novo `_left_positions`; `_Builder.__init__` e `_Builder._style`)
- Create: `tests/test_assemble.py`

**Interfaces:**
- Consumes: `PageLayout`, `Region`, `LineMetrics` (sem imagem: métricas montadas à mão nos testes).
- Produces: `_left_positions(pages: list[PageLayout], content: dict[int, tuple[float, float]]) -> list[tuple[float, tuple[int, int]]]`
  — `(deslocamento_pt, (índice_da_página, índice_da_região))`; deslocamento a partir da esquerda da
  área de texto da página. Cada bloco contribui com: início do bbox se for de uma linha (`title`,
  `section-header`, `text`); início + `first_indent` se for texto de várias linhas com recuo.
  Deslocamentos < 1 em do corpo não entram (alinhado à margem já é `left`).
- Regra em `_style`, ramo de linha única: se houver **≥ 2 outros blocos** com deslocamento a até
  `tol_pt = 0,6 × body_font_pt` do deslocamento deste bloco → `align = "left"` com
  `indent_em = left / em_pt`; senão, a lógica atual.

- [ ] **Step 1: Escrever os testes** (páginas 1700×2200 px = 612×792 pt; área de texto 151–1547 px;
  parágrafos de 5 linhas em x1 = 151 com `first_indent_px = 98`)

```python
def test_title_on_recurring_left_position_is_left_aligned():
    doc = assemble([page(title([251, 157, 1395, 209]), heading([251, 334, 524, 375]),
                         para(), para(), para())])
    title = doc.blocks[0]
    assert title.kind == "title" and title.style.align == "left"
    assert title.style.indent_em > 1.5

def test_symmetric_title_without_support_stays_centered():
    doc = assemble([page(title([400, 157, 1298, 209]), heading([700, 334, 1000, 375]),
                         para(), para())])
    assert doc.blocks[0].style.align == "center"

def test_two_centered_titles_of_different_widths_stay_centered():
    doc = assemble([page(title([400, 157, 1298, 209]), heading([550, 334, 1148, 375]),
                         para(), para())])
    assert [b.style.align for b in doc.blocks[:2]] == ["center", "center"]

def test_centered_titles_sharing_x_with_only_one_other_block_stay_centered():
    doc = assemble([page(title([400, 157, 1298, 209]), heading([400, 334, 1298, 375]),
                         para(), para())])
    assert [b.style.align for b in doc.blocks[:2]] == ["center", "center"]
```

(`page`, `title`, `heading`, `para` são construtores locais do arquivo de teste que montam
`Region` com `LineMetrics` coerentes.)

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/test_assemble.py -v` → FAIL no 1º teste
  (`center`).
- [ ] **Step 3: Implementar** `_left_stops` e a regra em `_style`.
- [ ] **Step 4: Rodar** — `uv run pytest -v` → PASS.
- [ ] **Step 5: Commit** — `assemble: linha única numa posição recorrente é alinhada à esquerda`

---

### Task 3: Filete colado a títulos

**Files:**
- Modify: `src/ocrpdf/ocr/base.py` (`RuleMetrics`; `Region.rules`; `PageLayout.load`),
  `src/ocrpdf/probe.py` (`find_rules`), `src/ocrpdf/pipeline.py` (`_measure`),
  `src/ocrpdf/model.py` (`Rule`; `BlockStyle.rule_above`/`rule_below`; desserialização),
  `src/ocrpdf/assemble.py` (conversão em `_style`), `src/ocrpdf/render/html.py` (`_style_attr`)
- Test: `tests/test_rules.py`

**Interfaces:**
- Produces:
  - `@dataclass RuleMetrics: position: str ("above" | "below"), x1_px: float, x2_px: float, y_px: float, thickness_px: float, gap_px: float`
    (`gap_px`: distância entre o filete e a tinta do texto do bloco).
  - `Region.rules: list[RuleMetrics] = field(default_factory=list)`; `PageLayout.load` aceita JSON
    sem `rules`.
  - `probe.find_rules(mask: np.ndarray, bbox: tuple[float, float, float, float]) -> list[RuleMetrics]`
    — procura em `[y1 − band, y2 + band]`, `band = max(10, round(0,6 × (y2 − y1)))`, grupos de linhas
    consecutivas com altura ≤ 4 px cujo trecho contínuo de tinta mais longo tem ≥ 50% da largura do
    bbox e preenchimento ≥ 90% entre suas pontas. Chamado em `_measure` para `title` e
    `section-header`.
  - `@dataclass Rule: thickness_pt: float, gap_pt: float, left_em: float, right_frac: float` —
    `left_em` = (início do filete − esquerda da área de texto) / tamanho da fonte do bloco;
    `right_frac` = (direita da área de texto − fim do filete) / largura da área de texto, entre 0 e 1.
  - `BlockStyle.rule_above: Rule | None = None`, `BlockStyle.rule_below: Rule | None = None`.
  - Render de título com filete: `margin-left:{left_em}em; margin-right:{right_frac*100}%;`
    `border-bottom:{thickness_pt}pt solid; padding-bottom:{gap_pt}pt` (análogo com `top` para
    `rule_above`); o `text-indent` passa a ser `indent_em − left_em` (≥ 0).

- [ ] **Step 1: Escrever os testes**

```python
def test_find_rules_below_title():
    image, regions = benjamin_like_page()
    rules = probe.find_rules(probe.ink_mask(image), regions[0].bbox)
    assert len(rules) == 1 and rules[0].position == "below"
    assert 1 <= rules[0].thickness_px <= 3 and rules[0].gap_px <= 6
    assert abs(rules[0].x1_px - 254) <= 3 and abs(rules[0].x2_px - 1545) <= 3

def test_text_line_below_title_is_not_a_rule():
    image, regions = benjamin_like_page()
    assert probe.find_rules(probe.ink_mask(image), regions[1].bbox) == []   # subtítulo

def test_rule_reaches_pdf(tmp_path):
    image, regions = benjamin_like_page()
    doc = Pipeline(FakeEngine([regions]), tmp_path / "w", workers=1).analyze(
        make_pdf(tmp_path / "in.pdf", [image]), font="sans")
    rule = doc.blocks[0].style.rule_below
    assert rule is not None and 0.02 <= rule.right_frac <= 0.08     # termina perto da margem
    out = tmp_path / "out.pdf"
    render_pdf(doc, out, base_dir=tmp_path / "w")
    # linha horizontal escura abaixo do título, ocupando ≥ 75% da área de texto
    assert _longest_dark_row_fraction(out, page=0, y_range_pt=(70, 110)) >= 0.75

def test_old_document_json_without_rules_loads(tmp_path): ...  # Document.load de JSON sem rule_*
```

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/test_rules.py -v` → FAIL (`find_rules` inexistente).
- [ ] **Step 3: Implementar** `RuleMetrics`, `find_rules`, chamada em `_measure`, `Rule`/`BlockStyle`
  (com desserialização tolerante em `_block_from_dict`), conversão no `assemble` e CSS no render.
- [ ] **Step 4: Rodar** — `uv run pytest -v` → PASS.
- [ ] **Step 5: Conferir no exemplo real** — `uv run ocrpdf convert exemplo/sample.pdf -o /tmp/claude-1001/-home-vitor-git-OCR-vLM/9ec11da8-6e64-499c-a52c-4224c8a25501/scratchpad/t3.pdf -w resultado_work`
  (usa o cache; não precisa de servidor) e `pdftoppm -r 70 -f 1 -l 1 -png` do resultado: título à
  esquerda com o recuo dos subtítulos e filete até perto da margem direita.
- [ ] **Step 6: Commit** — `título: detecta e desenha filete colado a títulos`

---

### Task 4: `--page-size`, `--margin`, `--font-size`

**Files:**
- Create: `src/ocrpdf/output_format.py`, `tests/test_output_format.py`
- Modify: `src/ocrpdf/cli.py` (opções no `convert` e no `render`), `CLAUDE.md` (seção "Uso")

**Interfaces:**
- Produces:
  - `parse_page_size(spec: str) -> tuple[float, float] | None` — `"original"` → `None`; `"a4"` →
    `(595.28, 841.89)`; `"17.6x25"`/`"17,6x25"` → `(498.9, 708.66)` (pt, 2 casas); fora de 5–100 cm
    ou formato inválido → `ValueError` com mensagem em português.
  - `apply_output_format(doc: Document, page_size: tuple[float, float] | None, margin_cm: float = 2.5, font_size_pt: float | None = None) -> Document`
    — devolve **cópia**; com `page_size`, troca `doc.page` por `PageGeometry` com as 4 margens
    iguais a `margin_cm`; valida que sobram ≥ 3 cm de área de texto em cada direção (senão
    `ValueError`); com `font_size_pt`, fator `k = font_size_pt / doc.body_font_pt` aplicado a
    `body_font_pt`, `paragraph_gap_pt`, `running_header_font_pt`, `style.font_size_pt` de blocos e
    notas e `Rule.gap_pt`.
  - CLI: `--page-size` (padrão `original`), `--margin` (float, padrão 2.5), `--font-size` (float,
    padrão: medido) em `convert` e `render`; erros de `parse_page_size`/`apply_output_format` viram
    erro do argparse/saída com código 2.

- [ ] **Step 1: Escrever os testes**

```python
@pytest.mark.parametrize("spec,expected", [("original", None), ("a4", (595.28, 841.89)),
                                           ("17.6x25", (498.9, 708.66)), ("17,6x25", (498.9, 708.66))])
def test_parse_page_size(spec, expected): assert parse_page_size(spec) == expected

@pytest.mark.parametrize("spec", ["4x10", "17.6", "x25", "a3x", "101x20"])
def test_parse_page_size_rejects(spec): with pytest.raises(ValueError): parse_page_size(spec)

def test_margins_too_large_for_page():
    with pytest.raises(ValueError): apply_output_format(Document(), (170.08, 170.08), 2.5)  # 6x6 cm

def test_a4_and_font_size_in_pdf(tmp_path):
    doc = apply_output_format(_doc_with_many_notes(spread=80, long_notes=True), (595.28, 841.89), 2.5, 12)
    out = tmp_path / "a4.pdf"; render_pdf(doc, out, base_dir=tmp_path)
    box = PdfReader(out).pages[0].mediabox
    assert (round(float(box.width), 1), round(float(box.height), 1)) == (595.3, 841.9)
    assert _body_font_size(out) == pytest.approx(12, abs=0.2)
    assert _misplaced(out) == []                       # notas na página da chamada

def test_cli_render_with_page_size(tmp_path):  # ocrpdf render doc.json -o x.pdf --page-size 17.6x25
    ...  # mediabox ≈ (498.9, 708.7)
```

(`_doc_with_many_notes`, `_misplaced` vêm de `tests/test_render.py`; `_body_font_size` mede o
tamanho mais frequente via `visitor_text` do pypdf, como na validação manual.)

- [ ] **Step 2: Rodar e ver falhar** — `uv run pytest tests/test_output_format.py -v` → FAIL (módulo inexistente).
- [ ] **Step 3: Implementar** `output_format.py` e as opções da CLI; atualizar "Uso" no `CLAUDE.md`.
- [ ] **Step 4: Rodar** — `uv run pytest -v` → PASS.
- [ ] **Step 5: Conferir no exemplo** — `uv run ocrpdf render resultado_work/document.json -o <scratchpad>/a4.pdf --page-size a4 --font-size 12`
  e `pdfinfo` (595 × 842 pt).
- [ ] **Step 6: Commit** — `saída: --page-size (original, A4 ou cm), --margin e --font-size`
