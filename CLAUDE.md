# ocrpdf — contexto do projeto

Aplicação Python que recebe um PDF composto por **imagens de páginas escaneadas** e gera um
**PDF novo, reconstruído**, com texto real (selecionável/grifável), formatação o mais próxima
possível do original e **notas ligadas por links** (chamada no texto ↔ nota, nos dois sentidos).

Documento de referência usado no desenvolvimento: ensaio "A obra de arte na era de sua
reprodutibilidade técnica" (W. Benjamin), 14 páginas Letter escaneadas a 150 dpi, uma coluna,
fonte sem serifa (Arial), muito texto em itálico, cabeçalho corrente em todas as páginas exceto a 1ª,
uma nota de rodapé (¹) no título "Primeira Versão". **Não está no repositório**; peça ao usuário.

## Preferências do usuário

- Responder em português, de forma sucinta, mas sem deixar nada sem explicação.
- Usuário experiente (C++17/20, Python, Linux Debian/Ubuntu). Valoriza SOLID, Clean Code,
  Clean Architecture e design patterns, mas **prefere soluções simples e diretas**; só propor
  arquiteturas mais complexas quando ele pedir.
- Ambiente alvo: **Ubuntu + uv**, **GPU NVIDIA ≥ 8 GB**.

## Decisões já tomadas (não reabrir sem motivo)

| Tema | Decisão | Por quê |
|---|---|---|
| Saída | PDF **reconstruído** (não "sanduíche" imagem + texto invisível) | escolha do usuário |
| OCR | VLM local via **vLLM** (API OpenAI-compatível), padrão **dots.mocr** (~3B) | layout + categoria + ordem de leitura numa passada; cabe em 8 GB |
| Intermediário | **`document.json`** (modelo próprio) → HTML/CSS → **WeasyPrint** | editável à mão, re-renderizável; WeasyPrint tem `float: footnote` e links internos |
| Conteúdo | texto, títulos, notas, **múltiplas colunas, tabelas, figuras, fórmulas** | o usuário marcou todos |
| Fórmulas | LaTeX → SVG com **ziamath** (WeasyPrint não roda JS/MathJax nem renderiza MathML) | puro Python |
| vLLM | ambiente **separado** via `uvx` (scripts/serve_vllm.sh), não dependência do projeto | torch/CUDA pesados; a app só faz HTTP |

## Uso

```bash
./scripts/install_system_deps.sh      # apt: pango/harfbuzz, fontes Liberation; instala uv
uv sync
./scripts/serve_vllm.sh               # terminal 1 (dots.mocr em :8000, served-model-name "model")
uv run ocrpdf convert in.pdf -o out.pdf -v       # OCR + montagem + PDF
uv run ocrpdf analyze in.pdf -w work/            # só OCR + document.json
uv run ocrpdf render work/document.json -o out.pdf   # re-renderiza após editar o JSON
```

Opções relevantes: `--preset dots|qwen25vl|qwen3vl`, `--server`, `--model`, `--dpi 200`,
`--pages "1-3,7"`, `--workers 4`, `--font auto|serif|sans|mono|<família>`, `--lang pt-BR`,
`--force` (ignora cache), `--no-header`.

Pasta de trabalho (padrão `<saída>_work/`): `pages/page_NNNN.json` (cache bruto por página),
`assets/` (recortes de figuras), `meta.json` (fonte detectada), `document.json`, `document.html`.

## Arquitetura

```
src/ocrpdf/
  cli.py          argparse: convert | analyze | render
  pipeline.py     Pipeline: rasteriza (pypdfium2, em série, lotes de workers*2) → engine.analyze
                  em ThreadPool → probe/recorte → cache por página → assemble → document.json.
                  Também pergunta ao VLM se a fonte é serif/sans (--font auto).
  ocr/base.py     Region(category, bbox px, text, metrics, image), LineMetrics, PageLayout
                  (save/load JSON), Protocol OcrEngine { analyze(image), ask(image, q) }
  ocr/vlm.py      VlmEngine (httpx → /chat/completions), PRESETS, smart_resize, parse_layout
                  (tira cercas ```json, recupera objetos de JSON truncado), reescala bbox.
  probe.py        heurísticas numpy sobre a imagem do bloco: Otsu, perfil de projeção →
                  nº de linhas, entrelinha, ascent (topo→linha de base), recuo 1ª linha, alinhamento.
  inline.py       Markdown inline → list[Span] (markdown-it-py + dollarmath): *it*, **b**, <sup>,
                  <sub>, $^{1}$ → sup, $…$ → math, ¹²³ → sup; escapa "1." e "-" iniciais para
                  não virarem lista.
  notes.py        split_label (rótulo no início da nota), link() (marca chamadas e ancora notas
                  sem chamada), ENDNOTE_HEADINGS (Notas, Referências, Bibliografia...).
  assemble.py     PageLayout[] → Document: margens, tipografia, nº de colunas, cabeçalho corrente,
                  blocos, junção de parágrafos entre páginas/colunas, notas, níveis de título.
  htmlsafe.py     sanitiza HTML de tabelas (só tags de tabela + colspan/rowspan).
  model.py        Document/Block/Span/Note/BlockStyle/Source/PageGeometry + save/load tolerante.
  render/html.py  Document → HTML+CSS; render/style.css (base); render/math.py (ziamath);
  render/__init__  render_pdf() com WeasyPrint (import tardio).
```

### Modelo intermediário (`model.py`)
- `Document`: page (tamanho + margens em pt), font_family, body_font_pt, line_height,
  paragraph_gap_pt, columns, running_header(+_align), header_on_first_page, page_numbers, lang,
  `blocks: list[Block]`, `notes: dict[id, Note]`.
- `Block.kind`: title | heading | paragraph | list_item | caption | table | formula | figure | endnote.
  `endnote` é um marcador de posição (`note=id`); o conteúdo fica em `doc.notes`.
  `trailing_notes`: notas de rodapé sem chamada, ancoradas no último bloco da página de origem.
- `Span`: text + italic/bold/sup/sub/math + `note` (id quando é chamada de nota).
- `Note`: id ("n1"...), label ("1", "*", "a"...), kind footnote|endnote, page, blocks.

### Motor OCR (dots.mocr)
- Saída: lista JSON `{"bbox": [x1,y1,x2,y2], "category": ..., "text": ...}`, bbox em pixels da
  **imagem enviada**; categorias: Caption, Footnote, Formula, List-item, Page-footer,
  Page-header, Picture, Section-header, Table (HTML), Text (Markdown), Title.
- O texto do usuário precisa do prefixo `<|img|><|imgpad|><|endofimg|>` (senão o vLLM v1 insere
  "\n" e piora a saída). Por isso o preset `dots` define `text_prefix`.
- A imagem é redimensionada por `smart_resize` (múltiplos de 28, máx. 11.289.600 px) **antes** do
  envio, para sabermos o espaço de coordenadas. Qwen3-VL usa bbox normalizado 0–1000 e fator 32.
- vLLM recomendado pelo dots.mocr: **0.17.1**. Página Letter a 200 dpi ≈ 4–5k tokens de imagem.
- Prompt original: repositório rednote-hilab/dots.mocr, `dots_mocr/utils/prompts.py`
  (`prompt_layout_all_en`), copiado em `ocr/vlm.py`. Os presets genéricos acrescentam regras
  pedindo itálico/negrito/`<sup>`.

### Heurísticas importantes
- **Tamanho de fonte** = ascent_px / 0,72 (altura de maiúsculas/ascendentes por em). Medir pela
  altura total da tinta subestimava títulos sem descendentes ("Primeira Versão"). Filetes
  (altura ≤ 4 px e > 50% da largura) são descartados antes.
- Corpo = mediana ponderada por linhas dos blocos `text` com ≥ 3 linhas; entrelinha = pitch/font.
- Espaço entre parágrafos = mediana das lacunas − entrelinha extra.
- Margens: mediana das extremidades do conteúdo; a inferior usa o 1º quartil (páginas cheias).
- Colunas: mediana de largura dos blocos de texto / largura útil < 0,6 → 2 ou 3 colunas;
  blocos com largura > 70% são `full_width` (fora do `div.cols`).
- Alinhamento: multilinhas → probe; linha única → posição relativa à área útil da página
  (centro/direita/esquerda + recuo em em).
- Títulos: níveis por faixas de tamanho (diferenças < 1 pt = mesmo nível); title = h1.
- Parágrafo continua da página/coluna anterior se o anterior não termina em pontuação final
  **ou** o novo começa com minúscula, e o novo não tem recuo de 1ª linha; desfaz hifenização.
- Notas: uma região Footnote pode ter várias notas (divide por linhas que começam com rótulo).
  Bloco sem rótulo = continuação da nota anterior (junta se minúscula/sem pontuação final,
  senão vira novo parágrafo da nota).
- Ligação: chamadas sobrescritas, `[n]` e símbolos (`*`, `†`, `‡`) colados à palavra; **só liga
  se existir nota com o rótulo** (evita falsos positivos). Busca: rodapé da mesma página →
  página +1 → página −1 → notas de fim. Numeração que reinicia por capítulo: escolhe a primeira
  nota ainda não referenciada.
- Render: nota de rodapé vira `<span class="footnote" id=nX>` (float: footnote) logo após a
  chamada `<a class="noteref" id="ref-nX" href="#nX">`; `::footnote-call`/`::footnote-marker`
  com `content: none` para manter o rótulo original; o rótulo dentro da nota linka de volta para
  `#ref-nX`. Isso foi **validado** no WeasyPrint 70 (links /Link com destinos nomeados corretos).
  Filete curto acima das notas via `linear-gradient` no `@footnote`.

### Medições de referência (página 1 do Benjamin, 200 dpi → 1700×2200 px)
Blocos (px): título [254,165,1546,213] (inclui sublinhado); subtítulo itálico [262,257,938,288];
"Primeira Versão¹" [258,342,517,368]; par. 1 [158,398,1541,552]; par. 2 [157,574,1542,951];
"Reprodutibilidade técnica" [258,1021,662,1055]; par. 3 [158,1078,1542,1391];
par. 4 [158,1414,1542,1919]; filete [157,1943,557,1945]; nota [158,1959,1525,2043].
Páginas 2–14: cabeçalho corrente em [380,104,1540,129] (alinhado à direita).
Resultado da probe: corpo ≈ 10,3 pt, entrelinha ≈ 1,11, recuo ≈ 3,5 em, justificado;
títulos ≈ 13–13,5 pt; título principal ≈ 17 pt; nota ≈ 8 pt.

## Estado atual

Feito e conferido:
- Todos os módulos importam; `uv run ocrpdf --help` funciona; `uv.lock` gerado.
- `inline.parse` testado manualmente (itálico, negrito, sup, $^{2}$, ¹², math inline/bloco).
- `probe.measure` conferido em blocos reais da página 1 (números acima).
- Mecanismo de notas/links do WeasyPrint validado num HTML isolado.

**Nunca executado** (fazer primeiro):
- Pipeline de ponta a ponta; `assemble` com dados reais; render de um documento completo.
- `VlmEngine` contra um servidor real (o ambiente anterior não tinha GPU e o Hugging Face estava
  bloqueado). Formato da saída, prefixo e escala de bbox vêm da documentação/código do dots.
- ziamath inline (alinhamento vertical pela viewBox), agrupamento em colunas, tabelas, figuras.

## Próximos passos

1. **Testes** (`tests/`, pytest; `pypdf` já está no grupo dev):
   - `test_inline.py`, `test_notes.py` (split_label, link por página, endnotes, `[n]`, `*`).
   - `test_vlm.py` com `httpx.MockTransport`: JSON com cerca, JSON truncado, escala de bbox
     (pixels e norm1000).
   - `test_assemble.py` com PageLayouts sintéticos: cabeçalho corrente, junção de parágrafo
     entre páginas ("Por mais" | "penetrantes que fossem"), nota de rodapé ligada ao título.
   - `test_render.py`: gera PDF e verifica com pypdf texto extraível + anotações /Link com
     destinos `nX` e `ref-nX`.
2. **Demo com o PDF de referência**: um engine falso que devolve regiões transcritas das
   páginas 1–3 (bboxes acima) para comparar visualmente o resultado com o original
   (`pdftoppm -r 60`), sem depender da GPU.
3. **Primeira execução real com GPU**: rodar `convert` no PDF de referência, inspecionar
   `pages/*.json`. Verificar principalmente **se o dots.mocr marca itálico**; se não marcar,
   comparar com `--preset qwen3vl` (Qwen3-VL-8B-Instruct-FP8, ~12 GB) e considerar um modo
   híbrido (layout do dots + re-reconhecimento do texto dos blocos por um VLM genérico).
4. Escrever o `README.md` (está vazio).
5. Pendências menores conhecidas:
   - `inline.parse(strip_list_marker=...)` não é usado; remover ou usar em list-items.
   - `font_size_pt` por nota é ignorado em parte (o render usa a mediana das notas).
   - `_ask_font` rasteriza a página de novo; o dots pode responder mal a perguntas livres
     (há fallback para serif).
   - Notas longas que quebram de página: avaliar `footnote-policy` no CSS.
   - Chamadas `(1)` não são reconhecidas de propósito (ambíguas com enumerações).
6. Git: o projeto ainda não é um repositório; `git init` + `.gitignore` (`.venv/`, `*_work/`,
   `__pycache__/`).
