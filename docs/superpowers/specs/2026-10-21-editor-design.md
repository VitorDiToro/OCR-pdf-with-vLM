# Editor do document.json (`ocrpdf edit`) — design

Data: 2026-10-21 · Contexto do projeto: `CLAUDE.md`.

## Por quê

Depois da conversão, o usuário quer pequenos ajustes à mão no resultado. Hoje o ciclo é abrir o
`document.json` num editor qualquer, rodar `ocrpdf render` no terminal e abrir o PDF, a cada
tentativa; e editar o JSON à mão quebra fácil (vírgulas, chaves). As edições que ele citou, vendo
os 7 PDFs de `docs_ocr`:

- forçar quebra de página (ex.: o "PREFÁCIO" do Marx, que no livro abre página nova);
- acertar o tamanho da fonte de um título (ex.: o título da obra);
- acrescentar linhas em branco entre um bloco e outro;
- remover uma imagem que não acrescenta nada (ex.: o carimbo da biblioteca na capa do Gonçalves);
- trocar o alinhamento (o resultado é fiel ao original, alinhado à esquerda, mas ele prefere ler
  justificado);
- negrito e itálico em um bloco inteiro (ex.: um título).

A referência é um editor de LaTeX: o texto-fonte de um lado, o resultado do outro.

## Objetivos, não-objetivos, anti-objetivos

Objetivos:
- Editar os blocos de **uma página do original por vez**, vendo o resultado ao lado.
- Ações de um clique para as edições acima, que escrevem no JSON pelo usuário.
- "Renderizar esta página" rápido (aproximado na paginação) e "Renderizar tudo" (o PDF final).
- O formato do PDF (página, margem, corpo, cabeçalho) gravado no documento e editável na tela.

Não-objetivos (v1):
- Negrito/itálico em **parte** de um bloco; família de fonte por bloco.
- Rodar OCR, `analyze` ou `convert` de dentro do editor.
- Editar notas de rodapé/fim no editor (continuam em `document.json` → `notes`, editáveis à mão).
- Acrescentar, apagar ou reordenar blocos pelo editor (remover = ocultar; ver abaixo).
- Uso por mais de uma pessoa ou pela rede.

Anti-objetivos (falha mesmo que "funcione"):
- Corromper ou perder o `document.json`, ou perder edições.
- O PDF de "Renderizar tudo" diferente do que `ocrpdf render` daria com o mesmo `document.json`.
- Depender de internet, de biblioteca gráfica nova ou de dependência Python nova.
- `document.json` antigos deixarem de carregar.

## Restrições

- Ubuntu, `uv`, Python ≥ 3.11; dependências já existentes (WeasyPrint, pypdfium2, Pillow).
- Uso local: servidor só em `127.0.0.1`; um documento por servidor.
- Tempo de render completo: segundos a ~1 min nos livros maiores (Horkheimer: 46 págs.).
- O WeasyPrint não diagrama uma página isolada como ela sai no documento (a quebra depende de tudo
  o que vem antes): por isso há dois botões de renderizar.

## Decisões do usuário

1. **Layout C**: à esquerda, o JSON dos blocos da página atual; à direita, abas **Resultado** e
   **Original** (a página escaneada).
2. Navegação pela **página do original** (`source.page` dos blocos); o JSON mostra só os blocos
   dela.
3. Barra do topo: seletor de página (◀ N de M ▶), **Salvar**, **Renderizar esta página**,
   **Renderizar tudo**, **Justificar todos os parágrafos**.
4. Barra do bloco onde está o cursor: **Negrito**, **Itálico** (no bloco inteiro, liga/desliga),
   **Justificar parágrafo**, **Quebra de página antes**, **+ linha em branco antes**,
   **Ocultar/Mostrar bloco**.
5. Remover = **ocultar** (`"hidden": true` no `style`): reversível; o render pula o bloco; o
   arquivo da figura fica em `assets/`; a numeração dos blocos continua batendo com o original.
6. Negrito e itálico valem para o bloco inteiro.
7. **Formato do PDF** numa faixa da tela (página `original|a4|LxA` em cm, margem em cm, corpo em pt,
   cabeçalho corrente sim/não), **gravado no `document.json`**. Os dois botões de renderizar usam o
   que estiver na faixa. `ocrpdf render` usa o formato gravado quando não recebe opções; opção
   passada no comando vale por cima. `convert` grava no `document.json` as opções que receber.
8. Plataforma: "o mais simples de implementar, com menor chance de erro" (delegado ao Claude; ver
   abaixo).

Esboços aprovados: `.superpowers/brainstorm/1889215-1792632357/content/layout-c-formato.html`
(fora do git).

## Decisões do Claude

- **Página local no navegador**, servida pelo próprio ocrpdf: `ThreadingHTTPServer` da biblioteca
  padrão em `127.0.0.1`, porta livre, abre o navegador (`webbrowser`) e imprime a URL; Ctrl+C
  encerra. Uma página HTML + JS puro, **sem bibliotecas externas nem CDN** (funciona offline).
  O editor de JSON é um `<textarea>` com numeração de linhas.
- **Comando**: `ocrpdf edit <work>/document.json -o <final>.pdf [--pdf original.pdf]`.
  - `-o` obrigatório, como no `render`.
  - Original: `--pdf`; sem ele, `doc.source_path` (caminho absoluto do escaneado, campo novo
    gravado pelo `analyze`/`convert`); sem ele (documentos antigos), procura o arquivo de nome
    `doc.source` na pasta-pai, avó e bisavó do `document.json`, **pulando o arquivo de `-o` e PDFs
    gerados pelo WeasyPrint** (metadado Producer). Em `docs_ocr`, a avó tem o PDF reconstruído de
    mesmo nome e o escaneado está na bisavó. Não achou: a aba Original mostra o aviso e o resto
    funciona.
  - O formato vem do `document.json` (ou os padrões de hoje: `original`, 2,5 cm, corpo medido,
    cabeçalho ligado).
- **O que o JSON da esquerda contém**: um array com os blocos daquela página, na ordem do
  documento, em **forma compacta** (como no esboço aprovado): `kind`, os `spans` com `text` e só as
  marcas ligadas (`"italic": true`…), e no `style` só os campos diferentes do padrão. Campos técnicos
  (`source`, `note`, `trailing_notes`, `html`, `latex`, `image`, `level`) não aparecem e são
  preservados do bloco original; numa página cheia, isso reduz de ~500–1.500 linhas para dezenas.
  Ao salvar, cada bloco é reconstruído a partir do original + o que está na tela (campo ausente =
  padrão). O array precisa ter **o mesmo número de blocos, na mesma ordem de `kind`**; senão, nada é
  gravado e a mensagem explica (remover = Ocultar). Isso evita mapear inserções/remoções.
- **Salvar**: valida (JSON e estrutura; erro de sintaxe mostra a linha); na primeira gravação cria
  `document.orig.json` se não existir (nunca sobrescreve); grava de forma atômica (arquivo temporário
  + rename). Se o `document.json` mudou no disco desde que foi aberto, recusa e pede para recarregar.
- **Trocar de página com edições não salvas**: pergunta se salva, descarta ou fica.
- **Renderizar esta página**: usa o estado na tela (sem salvar). Compõe um documento temporário só
  com os blocos visíveis da página (e as notas deles), com a mesma geometria, formato e cabeçalho,
  renderiza com o mesmo `render_pdf` num diretório temporário e mostra as páginas como imagens
  (pypdfium2). A página começa no topo; a quebra pode diferir do PDF final.
- **Renderizar tudo**: **salva antes** (se houver erro, não renderiza) e gera o PDF em `-o` com o
  formato da faixa, exatamente como `ocrpdf render` faria com o `document.json` salvo. Depois, a aba
  Resultado mostra as páginas finais onde caíram os blocos da página atual (sugestão: `id` por bloco
  no HTML e as âncoras das páginas do WeasyPrint).
- Renderizações rodam numa thread; a tela mostra "renderizando…" e desabilita os botões; uma por vez.
- **Aba Original**: a página escaneada rasterizada (~100 dpi) com cada bloco numerado sobre ela pelo
  `source.bbox` (pt, origem no topo); blocos ocultos tracejados e riscados; o bloco do cursor em
  destaque. Na aba Resultado, o destaque do bloco do cursor só no "Renderizar esta página".
- **Bloco do cursor**: descoberto varrendo os objetos de primeiro nível do array no texto (respeitando
  strings); a barra do bloco mostra "bloco N (tipo)".
- **Ações** (escrevem no JSON da tela; gravam só no Salvar):
  - Negrito / Itálico: alternam `style.bold` / `style.italic` (campos novos, `null` = como hoje).
    Títulos já saem em negrito pelo CSS (h1–h6) e seus spans têm `bold=false`; por isso o botão age
    num **sobrescrito do bloco**, não nos spans: `true` = bloco todo em negrito, `false` = bloco todo
    sem negrito (tira o do título), `null` = padrão. O botão mostra o estado efetivo e alterna para o
    oposto; apagar o campo volta ao padrão.
  - Tamanho (pt): campo numérico na barra do bloco, **na escala da saída** (o que sai no PDF com o
    formato da faixa). Os `font_size_pt` do `document.json` estão na escala medida e o render os
    multiplica por corpo da faixa ÷ `body_font_pt` (no Marx, 12,4 ÷ 51,5); o campo converte ao
    gravar no JSON. No JSON da esquerda o valor continua na escala medida.
  - Justificar parágrafo: `style.align = "justify"`.
  - Quebra de página antes: liga/desliga `style.page_break_before`.
  - + / − linha em branco antes: `style.space_before_lines` ± 1 (mínimo 0).
  - Ocultar/Mostrar: liga/desliga `style.hidden`.
  - Justificar todos os parágrafos (topo): `align = "justify"` em todos os blocos `paragraph` do
    documento (todas as páginas); marca o documento como alterado.
- **Modelo** (campos novos, todos com padrão; arquivos antigos continuam carregando):
  - `BlockStyle.hidden: bool = False`
  - `BlockStyle.space_before_lines: int = 0` — espaço extra antes do bloco, em linhas do corpo
    (`N × line_height × body_font_pt`).
  - `BlockStyle.bold: bool | None = None`, `BlockStyle.italic: bool | None = None` — sobrescritos do
    bloco inteiro (o render aplica `font-weight`/`font-style` no elemento do bloco).
  - `Document.source_path: str = ""` — caminho absoluto do escaneado, gravado pelo `analyze`.
  - `Document.edited: bool = False` — o editor liga ao salvar.
  - `Document.output: OutputFormat | None = None`, com `page_size: str = "original"`,
    `margin_cm: float = 2.5`, `font_size_pt: float | None = None`, `header: bool = True`.
- **Render**: pula blocos ocultos. Notas chamadas só por blocos ocultos somem com eles;
  `trailing_notes` ancoradas num bloco oculto continuam sendo emitidas no lugar dele.
- **CLI**: `render` monta o formato a partir de `doc.output` e sobrepõe as opções passadas (não grava
  nada); `convert` grava em `doc.output` as opções de formato que recebeu. `--no-header` passa a ser
  `header = False`.
- **Edições protegidas de um novo `analyze`/`convert`**: o `analyze` remonta e sobrescreve o
  `document.json` mesmo com o OCR em cache. Se o `document.json` existente tem `edited = true`, ele
  **para com erro** explicando; com `--reassemble`, guarda o existente como
  `document.edited-<data-hora>.json` e segue. Sem mesclagem de edições (fora da v1).
- Depois de implementado: gravar no `document.json` do Marx o formato usado nele (`14.4x21.6`,
  margem 2, corpo 12.4).
- **Testes** (pytest, sem navegador): API do servidor (carregar página, salvar com validação,
  recusa de mudança estrutural, backup, conflito de mtime, renderizar página e tudo), render de
  `hidden`/`space_before_lines`, `output` (gravar/carregar, precedência no `render`, `convert`
  gravando). O JS é conferido à mão no navegador.

## Deixado ao implementador

- Estrutura dos arquivos (ex.: `src/ocrpdf/editor/` com servidor e `static/`), rotas e formato das
  respostas da API.
- Detalhes visuais da página, numeração de linhas e atalhos de teclado.
- Resolução das imagens das páginas renderizadas e do original; cache delas.
- Como mapear blocos → páginas finais depois do "Renderizar tudo".
