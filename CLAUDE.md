# ocrpdf — contexto do projeto

Aplicação Python que recebe um PDF composto por **imagens de páginas escaneadas** e gera um
**PDF novo, reconstruído**, com texto real (selecionável/grifável), formatação o mais próxima
possível do original e **notas ligadas por links** (chamada no texto ↔ nota, nos dois sentidos).

Documento de referência usado no desenvolvimento: ensaio "A obra de arte na era de sua
reprodutibilidade técnica" (W. Benjamin), 14 páginas Letter escaneadas a 150 dpi, uma coluna,
fonte sem serifa (Arial), muito texto em itálico, cabeçalho corrente em todas as páginas exceto a 1ª,
uma nota de rodapé (¹) no título "Primeira Versão". Está em `exemplo/sample.pdf`, com o resultado
publicado em `exemplo/resultado.pdf`.

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
| Itálico | por palavra: **inclinação medida na imagem** decide (≥ 7°); sem medida, vale o dots (`--italic-rule slant`). 2º modelo só opcional (`r1`/`r2`) | o 2º modelo quase não marca itálico; `slant` atinge precisão ≥ 90% e supera dots e referência nos 3 documentos (docs/superpowers/reports/2026-10-21-italico-avaliacao.md) |

## Uso

```bash
./scripts/install_system_deps.sh      # apt: pango/harfbuzz, fontes Liberation; instala uv
uv sync
./scripts/serve_vllm.sh               # terminal 1 (dots.mocr em :8000, served-model-name "model"; ~3 min p/ subir)
uv run pytest                         # testes
uv run ocrpdf convert in.pdf -o out.pdf -v       # OCR + montagem + PDF
uv run ocrpdf analyze in.pdf -w work/            # só OCR + document.json
uv run ocrpdf render work/document.json -o out.pdf   # re-renderiza após editar o JSON
uv run ocrpdf edit work/document.json -o out.pdf     # editor no navegador (127.0.0.1)
uv run python scripts/eval_italic.py --pdf in.pdf --work work --gold gabarito.json --pages pares
```

Opções relevantes: `--preset dots|qwen25vl|qwen3vl`, `--server`, `--model`, `--dpi 200`,
`--pages "1-3,7"`, `--workers 4`, `--font auto|serif|sans|mono|<família>`, `--lang pt-BR`,
`--force` (ignora cache), `--header/--no-header` (padrão: o gravado; sem ele, ligado).
Itálico (etapa 2): `--italic-rule slant` (padrão, só o servidor do OCR) | `r1`/`r2` (2º modelo,
servidor em :8001: `scripts/serve_pair.sh`, ou `--switch restart` para a app subir/derrubar cada
servidor; `--italic-server`, `--italic-model`, `--italic-hf-model`); `--no-italic` = itálico do OCR
sem conferir na imagem.
Formato de saída (`convert` e `render`): `convert` grava as opções em `document.output`
(`OutputFormat`: page_size, margin_cm, font_size_pt, header); `render` usa o `output` gravado e as
opções passadas valem por cima (não gravam nada); o resto do `document.json` fica no original medido.
`--page-size original|a4|<L>x<A>` (cm, aceita vírgula), `--margin 2.5` (cm; só com tamanho
diferente de `original`), `--font-size PT` (escala corpo, títulos, notas e cabeçalho). Em `--margin`
e `--font-size` o decimal é com **ponto** (decisão do usuário; está na ajuda).
`apply_format` valida o formato (`validate_output_format`: margem ≥ 0, corpo até 1000 pt, header
booleano); `output` gravado inválido sai como `ocrpdf: erro: …`. `convert`, `render` e `edit` recusam
`-o` igual ao PDF escaneado (entrada, `source_path` ou `--pdf`).

Edição do `document.json`: `ocrpdf edit <work>/document.json -o final.pdf [--pdf original.pdf]
[--port N] [--no-browser]` abre um editor local no navegador (só 127.0.0.1, sem CDN). Navega pela
página do original (`source.page`); à esquerda, o JSON compacto dos blocos da página (só texto,
marcas ligadas e `style` fora do padrão; mesmo número e `kind` de blocos ao salvar, remover =
ocultar, criar = Dividir no cursor); à direita, abas Resultado e Original (escaneado, blocos numerados). Botões: Salvar
(atômico; cria `<nome>.orig.json` uma vez; se o arquivo mudou no disco ou sumiu, recusa com 409 e a
página oferece **Recarregar do disco**: `POST /api/reload` relê o arquivo e descarta a memória, e a
faixa avisa antes quando há alterações só em memória; o texto e o formato da tela ficam, não salvos,
e o próximo Salvar os grava sobre o conteúdo novo), Renderizar esta página (aproximado na paginação;
fora da 1ª página do documento, com cabeçalho mesmo se `header_on_first_page` é falso), Renderizar
tudo (salva antes; igual ao `ocrpdf render`, inclusive o `document.html`; mostra as páginas finais do
1º bloco da página até onde começa o bloco visível seguinte), Justificar todos os
parágrafos, e por bloco negrito/itálico (estado efetivo: sobrescrito, ou título/todos os trechos
marcados), justificar, quebra de página, + linha em branco, ocultar, tamanho (pt na escala da saída),
**Dividir no cursor** (`POST /api/split`: a página envia o texto com `\ue000` no cursor; a sessão parte
o bloco de texto ali, os dois herdam tipo, `source` e estilo; quebra, linhas antes e filete de cima ficam
no 1º, filete de baixo e notas sem chamada no 2º; como o Justificar todos, fica na sessão até Salvar;
na aba Original os dois dividem a caixa, com o rótulo "2–3").
A faixa de formato edita `document.output`. O original vem de `--pdf` ou de `doc.source_path`
(gravado pelo `analyze`/`convert`; sem ele a aba Original avisa). Erros de início (documento
inexistente ou inválido, inclusive o `output` gravado, porta ocupada) saem como `ocrpdf: erro: …`, código 1.
`convert`/`analyze` remontam o JSON a partir do cache e apagam as edições (sem proteção, decisão do
usuário). Campos editáveis à mão: por bloco, `style.align` (justify|left|center|right),
`style.indent_em` (1ª linha; só left/justify), `style.block_indent_em` (bloco inteiro, do lado do
alinhamento: left/justify à esquerda, right à direita, center nenhum; em do bloco; o OCR não preenche),
`style.font_size_pt` (escala medida), `style.page_break_before`,
`style.space_before_lines`/`style.space_after_lines` (linhas do corpo, aceita fração; somam ao espaço
que o bloco já tem), `style.hidden`, `style.bold`/`style.italic`
(sobrescrevem o bloco todo; `false` tira também a marca dos trechos; `null` = padrão do tipo); por
trecho (`spans`), `text` (`\n` no texto = quebra de linha no PDF; o OCR nunca o gera), `italic`, `bold`.
NaN/Infinity são recusados (não são JSON).
A família da fonte é só do documento (`font_family`). `source.page` é 0-based.

Pasta de trabalho (padrão `<saída>_work/`): `pages/page_NNNN.json` (por página: OCR em cache +
medidas da probe regravadas a cada execução), `assets/` (recortes de figuras), `meta.json` (fonte
detectada), `italic/page_NNNN.json` (marcas do 2º modelo, só r1/r2), `document.json`, `document.html`.

## Arquitetura

```
src/ocrpdf/
  cli.py          argparse: convert | analyze | render | edit (+ --page-size/--margin/--font-size,
                  validados antes do OCR)
  pipeline.py     Pipeline: rasteriza (pypdfium2, em série, lotes de workers*2) → engine.analyze
                  em ThreadPool (só páginas sem cache) → probe/filetes/recortes de todas as páginas
                  (medidas sempre recalculadas da imagem) → assemble → document.json.
                  Também pergunta ao VLM se a fonte é serif/sans (--font auto).
  ocr/base.py     Region(category, bbox px, text, metrics, image, rules), LineMetrics,
                  RuleMetrics (filete em px), PageLayout (save/load JSON),
                  Protocol OcrEngine { analyze(image), ask(image, q) }
  ocr/vlm.py      VlmEngine (httpx → /chat/completions), PRESETS, smart_resize, parse_layout
                  (tira cercas ```json, recupera objetos de JSON truncado), reescala bbox.
  probe.py        heurísticas numpy sobre a imagem do bloco: Otsu, bloco endireitado (straighten,
                  skew_deg), perfil de projeção → nº de linhas, entrelinha, ascent (topo→linha de
                  base), recuo 1ª linha, alinhamento; find_rules: filetes colados acima/abaixo de
                  títulos (o VLM não os reporta).
  inline.py       Markdown inline → list[Span] (markdown-it-py + dollarmath): *it*, **b**, <sup>,
                  <sub>, $^{1}$ → sup, $…$ → math, ¹²³ → sup; escapa "1." e "-" iniciais para
                  não virarem lista.
  notes.py        split_label (rótulo no início da nota), link() (marca chamadas e ancora notas
                  sem chamada), ENDNOTE_HEADINGS (Notas, Referências, Bibliografia...).
  assemble.py     PageLayout[] → Document: margens, tipografia, nº de colunas, cabeçalho corrente,
                  blocos, junção de parágrafos entre páginas/colunas, notas, níveis de título.
  htmlsafe.py     sanitiza HTML de tabelas (só tags de tabela + colspan/rowspan).
  model.py        Document/Block/Span/Note/BlockStyle/Rule/Source/PageGeometry + save/load tolerante.
  editor/         `ocrpdf edit`: compact (bloco ↔ forma compacta), session (EditSession: páginas,
                  salvar com validação/backup/conflito de mtime, sem HTTP), rendering (página
                  isolada e tudo, imagens via pypdfium2), server (ThreadingHTTPServer em 127.0.0.1,
                  API JSON; recusa Host ≠ 127.0.0.1/localhost:PORTA (DNS rebinding) e POST sem
                  application/json), static/ (index.html + app.js + style.css, JS puro).
  output_format.py  --page-size/--margin/--font-size: cópia ajustada do Document só para renderizar
                  (o document.json guarda o formato original medido; `apply_format` recebe o `Document.output`).
  render/html.py  Document → HTML+CSS; render/style.css (base); render/math.py (ziamath);
  render/__init__  render_pdf() com WeasyPrint (import tardio).
  italic/         etapa 2, itálico por palavra: words (tokens dos Spans), slant (caixas de palavra
                  e inclinação, com o bloco endireitado), align (tokens ↔ caixas), stage (evidência
                  por bloco e regras slant/r1/r2), marker (2º modelo), evaluate (gabarito/referência).
  servers.py      só r1/r2: SleepSwitcher (par no ar, /sleep e /wake_up) e RestartSwitcher (a app
                  sobe cada servidor com serve_vllm.sh); ITALIC_HF_MODEL, ITALIC_SERVER_ENV.
scripts/          serve_vllm.sh (um servidor; GPU_MEM pela VRAM livre; MAX_IMAGE_PX p/ Qwen3-VL),
                  serve_pair.sh (par dots + 2º modelo), eval_italic.py (precisão/cobertura).
```

### Modelo intermediário (`model.py`)
- `Document`: page (tamanho + margens em pt), font_family, body_font_pt, line_height,
  paragraph_gap_pt, columns, running_header(+_align), header_on_first_page, page_numbers, lang,
  `blocks: list[Block]`, `notes: dict[id, Note]`, `source_path` (PDF escaneado, absoluto),
  `output: OutputFormat | None` (formato gravado: page_size, margin_cm, font_size_pt, header).
- `Block.kind`: title | heading | paragraph | list_item | caption | table | formula | figure | endnote.
  `endnote` é um marcador de posição (`note=id`); o conteúdo fica em `doc.notes`.
  `trailing_notes`: notas de rodapé sem chamada, ancoradas no último bloco da página de origem.
- `BlockStyle`: align, indent_em, block_indent_em, font_size_pt, full_width, rule_above/rule_below (`Rule`:
  thickness_pt, gap_pt, left_em no em do corpo, right_frac relativo à área de texto),
  hidden (o render pula o bloco; a quebra de página dele passa para o próximo bloco visível),
  space_before_lines/space_after_lines (linhas do corpo, com fração; o "depois" soma ao "antes" do próximo
  bloco visível num `div.space` só, e some se uma quebra de página ou o fim do documento vem antes; o
  espaço de um bloco de coluna no meio do grupo fica dentro do `div.cols`), bold/italic
  (`bool | None`, sobrescrevem o bloco inteiro), page_break_before (o render põe um `div.page-break` fora do `div.cols`, e
  nada antes do 1º bloco visível).
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
- **Página torta** (~1° é comum): `probe.measure` endireita o bloco antes de separar as linhas
  (cisalhamento vertical pelo ângulo que mais concentra a projeção horizontal) e desconta o
  deslocamento horizontal da margem linha a linha. Sem isso, as linhas de um bloco largo se
  encostavam e o bloco virava uma "linha" de ~1000 px (fonte de 95–376 pt; Horkheimer com 1613
  páginas).
- **bbox sobreposto** (o dots às vezes sobe/desce ~15 px na linha do bloco vizinho): o pipeline
  passa à probe quantos px do topo/base também estão no bbox de um vizinho da mesma coluna
  (`pipeline._overlaps`); a 1ª/última linha cuja tinta, no recorte original, fica toda nessa faixa
  (menor que uma linha) é a do vizinho e não conta. Medir no recorte original, não no endireitado,
  é o que funciona em página torta; e sem vizinho sobreposto nada é descartado ("anos." fica).
- **Fonte impossível** (logotipo de capa com haste de letra atravessando as linhas, carimbo): se a
  fonte medida passa de 1,8× a maior em que o texto caberia no bbox (palavra mais longa na largura,
  texto todo na área, 0,5 em por caractere), vale essa estimativa (`assemble._fitting_font_px`).
  Nos 8 documentos reais: capas/carimbos 2,0–2,85×, texto ≤ 1,76×. Antes, capas com 115–376 pt
  quebravam uma palavra por linha e saíam da página.
- Corpo = mediana ponderada por linhas dos blocos `text` com ≥ 3 linhas; entrelinha = pitch/font.
- Espaço entre parágrafos = mediana das lacunas − entrelinha extra.
- Margens: mediana das extremidades do conteúdo; a inferior usa o 1º quartil (páginas cheias).
- Colunas: mediana de largura dos blocos de texto / largura útil < 0,6 → 2 ou 3 colunas;
  blocos com largura > 70% são `full_width` (fora do `div.cols`).
- Alinhamento: multilinhas → probe; linha única → posição relativa à área útil da página
  (centro/direita/esquerda + recuo em em). Linha única que começa numa **posição recorrente**
  (≥ 2 outros blocos: recuo de 1ª linha de parágrafos, subtítulos) é `left` mesmo se parecer
  simétrica; blocos simétricos não contam como apoio ("I", "II", "III" centralizados).
  Títulos gravam sempre `font_size_pt` (o recuo fica no em do próprio título).
- Filetes (`probe.find_rules`, só title/section-header): grupo de ≤ 4 linhas de pixels numa faixa
  acima/abaixo do bbox, com o trecho de maior sobreposição com o bbox (≥ 50% da largura),
  comprimento ≥ 10% da página e ≥ 3× a altura da linha, e ≥ 1 linha em branco até a tinta
  (exclui serifas). `Rule` guarda extensão relativa à área de texto (`left_em` no em do corpo,
  `right_frac`); o render desenha um `div.rule` próprio antes/depois do título. Filete achado por
  dois títulos vizinhos (abaixo de um, acima do outro) fica com o mais perto
  (`pipeline._drop_shared_rules`). Bloco de coluna (`full_width=False`) não leva filete: a geometria
  é da área da página e ele é desenhado dentro de `div.cols` (TODO: geometria por coluna).
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
  Filete curto acima das notas: SVG em data-URI como `background` do `@footnote`.
- **Peculiaridades do WeasyPrint 70** (conferidas em 2026-10-20):
  - Para respeitar `orphans`/`widows`, ele adia notas já colocadas para a página seguinte
    (ignora `footnote-policy`; em cascata a nota vai várias páginas adiante). `render/__init__.py`
    recompila `_linebox_layout` trocando a linha `report = …` por `report = False` (se a linha
    mudar numa versão futura, só avisa). Aconteceu no exemplo com fonte serif (nota 1 → p. 3).
  - Nota que não cabe sob a chamada: com `footnote-policy: auto` só a nota vai para a página
    seguinte; `style.css` usa `line` (a linha da chamada vai junto). Aconteceu na 2ª execução do
    exemplo (chamada ² na última linha da p. 5). O patch e a política são ambos necessários;
    `tests/test_render.py` tem um documento sintético para cada caso e controles sem cada correção.
  - Seletores de margin box separados por vírgula (`@top-left, @top-right {…}`) invalidam a regra.
  - Margin boxes herdam do elemento raiz, não do `body`: `font-family` vai no `html`.
  - O `body` tem margem padrão de 8px (6pt) somada às margens da `@page`: `style.css` zera.
  - `background-size` de gradiente é ignorado no `@footnote` (pinta a área toda de preto).
- Fonte (`--font auto`): pergunta ao VLM sobre recortes em resolução cheia do maior bloco de
  texto de 5 páginas e vence a maioria. A pergunta descreve as serifas ("small strokes at the
  ends…, like Times New Roman, or plain like Arial"): a original errava em livros serifados.
  Com a página reduzida o dots erra; ele raciocina antes de responder (termina em `\boxed{sans}`),
  por isso `ask` usa 128 tokens e vale a última menção.
- Itálico (`slant`): caixas de palavra pelas lacunas entre tintas, casadas com os tokens do OCR
  (bloco sem casamento ≥ `MATCH_MIN` fica sem medida); inclinação = cisalhamento que endireita as
  hastes, menos a da linha de base. Só os blocos em que a regra muda alguma palavra recebem as
  flags; os demais mantêm os spans do OCR (itálico parcial "*Aura*s" preservado).
- Cabeçalho corrente: alinhamento pela folga até as margens (o centro engana com texto longo);
  tamanho medido pela probe (`page-header`/`page-footer` estão em `_MEASURED`).

### Medições de referência (página 1 do Benjamin, 200 dpi → 1700×2200 px)
Blocos (px): título [254,165,1546,213] (inclui sublinhado); subtítulo itálico [262,257,938,288];
"Primeira Versão¹" [258,342,517,368]; par. 1 [158,398,1541,552]; par. 2 [157,574,1542,951];
"Reprodutibilidade técnica" [258,1021,662,1055]; par. 3 [158,1078,1542,1391];
par. 4 [158,1414,1542,1919]; filete [157,1943,557,1945]; nota [158,1959,1525,2043].
Páginas 2–14: cabeçalho corrente em [380,104,1540,129] (alinhado à direita).
Resultado da probe: corpo ≈ 10,3 pt, entrelinha ≈ 1,11, recuo ≈ 3,5 em, justificado;
títulos ≈ 13–13,5 pt; título principal ≈ 17 pt; nota ≈ 8 pt.

## Estado atual

Repositório: github.com/VitorDiToro/OCR-pdf-with-vLM (público; `*.pdf` fora do git, exceto
`exemplo/*.pdf` e `tests/**/*.pdf`).

Feito e conferido:
- **Execução real** (2026-10-20): dots.mocr no vLLM 0.17.1, RTX 3060 12 GB (o desktop ocupa
  ~1,2 GB; `serve_vllm.sh` calcula `GPU_MEM` pela VRAM livre). 14 páginas em ~4 min com
  `--workers 4`; sobram só ~0,46 GiB de KV cache (~17k tokens, ≈ 2 páginas em paralelo).
- Saída do dots confere com o esperado (bbox em pixels da imagem enviada, prefixo `<|img|>…`).
  Texto fiel: os erros na citação em francês da p. 6 ("exprinient", "peiisée") estão no original.
- Resultado comparado com o original: 14 páginas, quebras quase iguais, notas na página da
  chamada, fonte sans, cabeçalho à direita.
- `tests/test_render.py` (pytest): notas na página da chamada + controle que reproduz o defeito
  do WeasyPrint sem o patch. Também há testes de alinhamento/filete do título (`test_assemble.py`,
  `test_rules.py`), cache e medidas (`test_pipeline.py`) e formato de saída (`test_output_format.py`);
  `tests/synth.py` gera a página sintética estilo Benjamin e o `FakeEngine`.
- `inline.parse` e `probe.measure` conferidos manualmente.
- **Itálico por palavra** (2026-10-21): avaliado contra gabaritos à mão em 3 documentos (Benjamin;
  Althusser e Horkheimer em `/home/vitor/Amostras`, com conversões de referência de outras
  ferramentas; gabaritos e caches deles em `amostras/`, fora do git). Páginas pares, precisão /
  cobertura da regra `slant`: Benjamin 99,4/89,2 (dots 100/46,1); Althusser 97,6/100 (dots
  100/97,5; referência 92,5/92,5); Horkheimer 98,6/92,2 (dots 99,3/90,8; referência 98,2/71,2).
  O 2º modelo (Qwen3-VL 8B-AWQ e 4B-FP8) quase não marca: R1 0,5–60% de cobertura.
- **Livros reais** (2026-10-21, Althusser e Horkheimer, páginas ~1° tortas): depois da correção da
  probe, Althusser (`--pages 5-10`) sai com 6 páginas para 6 escaneadas, mesmas quebras de linha
  quase todas; Horkheimer (`--pages 5-49`) com 44 para 45. Fonte serifada detectada nos dois.
  Comparações lado a lado em `amostras/comparacao/` (fora do git).
- **VRAM e desktop**: com dois servidores no ar (dots dormindo + 2º modelo), sobram ~0,5 GiB para a
  interface; numa execução assim o gnome-shell caiu e encerrou a sessão (servidores e Claude Code
  juntos). Só com um servidor, sem problema.

- **Editor** (2026-10-22): `ocrpdf edit` implementado e coberto por testes de servidor/sessão/render
  (sem navegador; o JS foi conferido à mão). Os 7 `document.json` de `docs_ocr` ganharam
  `source_path`; o do Marx também `output` (14.4x21.6, margem 2, corpo 12.4, cabeçalho ligado):
  `render` sem opções dá 11 páginas de 408,19 × 612,28 pt.

Ainda não exercitado: ziamath inline, colunas, tabelas, figuras, presets `qwen25vl`/`qwen3vl`.

## Próximos passos

1. Pendências da verificação de ponta a ponta (detalhes em
   docs/superpowers/reports/2026-10-21-italico-avaliacao.md):
   - `meta.json` sem versão da pergunta da fonte: mudar a pergunta não invalida o cache.
   - `probe.find_rules` não endireita o bloco (filete em página torta). A inclinação segue por
     bloco, não por página (decidido na revisão do PR #1: acompanha a curvatura perto da lombada).
   - Limiares da inclinação em pixels (`slant_deg` exige ≥ 30 px de tinta; tudo medido a 200 dpi:
     em 100–150 dpi há menos medidas) e calibração em só 3 documentos em português. Testar um 4º
     documento (corpo ≤ 9 pt, ou itálico oblíquo sintético) antes de confiar na regra fora disso.
   - Herança de palavra curta na regra `slant` (revisão do PR #1): Benjamin pares 89,2 → 98,4 de
     cobertura, mas a precisão do Horkheimer cai para 96,6 (< 98,2 da referência). Não adotada.
2. Editor: v1 sem negrito/itálico parcial, sem editar notas, sem juntar/reordenar blocos (criar só
   dividindo um bloco).
   Pendências conhecidas (PR #2): num bloco com marca parcial, dois cliques em Negrito/Itálico não voltam
   ao original (apagar o campo volta); a prévia mostra cabeçalho se todos os blocos da 1ª página do
   original estão ocultos; "Renderizar tudo" pode mostrar uma página a mais quando o bloco seguinte
   começa no topo dela; o JS das correções da revisão (Recarregar, Justificar todos, atalho do tamanho)
   não foi exercitado num navegador.
3. **Testes** restantes: `test_inline.py`, `test_notes.py` (split_label, link por página,
   endnotes, `[n]`, `*`), `test_vlm.py` com `httpx.MockTransport` (cerca, JSON truncado, escala
   pixels/norm1000), `test_assemble.py` com PageLayouts sintéticos (cabeçalho corrente, junção
   "Por mais" | "penetrantes que fossem", nota ligada ao título).
4. Escrever o `README.md` (está vazio).
5. Pendências menores conhecidas:
   - `inline.parse(strip_list_marker=...)` não é usado; remover ou usar em list-items.
   - `font_size_pt` por nota é ignorado em parte (o render usa a mediana das notas).
   - Notas longas que quebram de página: avaliar `footnote-policy` no CSS.
   - Chamadas `(1)` não são reconhecidas de propósito (ambíguas com enumerações).
   - Com `-v`, os logs INFO do WeasyPrint ("Step 5 - Creating layout…") e do httpx poluem a saída.
