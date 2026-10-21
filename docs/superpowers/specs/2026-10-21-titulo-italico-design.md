# Título e itálico — design

Data: 2026-10-21 · Branch: `worktree-titulo-italico` · Contexto do projeto: `CLAUDE.md`.

## Por quê

A primeira execução real do `ocrpdf` (exemplo do Benjamin, `exemplo/sample.pdf` →
`exemplo/resultado.pdf`) deixou dois desvios visíveis em relação ao original:

1. **Título** (p. 1): no original é alinhado à esquerda, com o mesmo recuo dos subtítulos, e tem
   um filete (linha fina) logo abaixo; no resultado sai centralizado e sem filete.
2. **Itálico**: o dots.mocr marca só parte. Marcou as frases longas da p. 1, *A idéia do cinema*,
   *sui generis*, *cella*; perdeu o subtítulo "Texto de Walter Benjamin publicado em 1955.",
   "Em busca do ouro", "Sonho de uma noite de verão", a citação de Séverin-Mars (p. 6) e
   outras.

O usuário vai converter mais documentos. Nos textos dele, o itálico marca **destaque,
paráfrase ou termo estrangeiro** — ou seja, ora frases inteiras, ora **palavras isoladas**.

## Objetivos, não-objetivos, anti-objetivos

Objetivos:
- Título/subtítulos de uma linha com o alinhamento do original (esquerda + recuo quando é o caso).
- Filete colado a título/subtítulo reproduzido (espessura, distância e extensão medidas).
- Itálico detectado **palavra a palavra**, com **precisão acima de tudo**.

Não-objetivos:
- Treinar ou ajustar modelos (tudo é inferência com modelos prontos + heurísticas).
- Negrito, sublinhado de texto, filetes soltos no meio da página (não colados a título).
- Usar uma segunda máquina/GPU (o endereço do 2º servidor é configurável, mas o alvo é uma GPU).
- Trocar o dots.mocr como motor de layout/texto.

Anti-objetivos (falha mesmo que "funcione"):
- **Itálico a mais em texto normal** — incomoda o usuário mais do que itálico faltando.
- O 2º modelo **alterar qualquer palavra** do texto do dots.
- Exigir passos manuais entre as etapas no modo padrão (com os servidores no ar, um comando só).
- Regressões no que já funciona: notas na página da chamada, fonte, cabeçalho (ver
  `tests/test_render.py`).

## Restrições e medições (máquina do usuário)

- RTX 3060 12 GB (~10,2–11 GB livres; o desktop usa 0,6–1,2 GB), 31 GB de RAM, Ubuntu, `uv`.
- vLLM **0.17.1** via `uvx` (`scripts/serve_vllm.sh`); a app fala HTTP (API OpenAI).
- dots.mocr: pesos 5,72 GiB; subir o servidor (quente) ~124 s; uma página a 200 dpi ~27–35 s.
- Modo sleep do vLLM, medido com duas instâncias do dots (20 ciclos, 40 trocas):
  dormir 1,1 s (mediana), acordar 1,27 s, resíduo de VRAM de um servidor dormindo 232 MiB
  (estável, sem vazamento), VRAM disponível ao servidor acordado ~9,9 GB.
  RAM ocupada com o par no ar: ~20 GB (≈ 4–5 GB por processo + pesos do modelo dormindo
  copiados para RAM travada, *pinned*); sobraram ~5,5 GB.
- **Defeito do vLLM 0.17.1:** após `/sleep`, uma imagem repetida derruba a requisição
  (`AssertionError: Expected a cached item for mm_hash`; o sleep limpa o cache de imagens do
  motor sem ressincronizar o da API). Contorno obrigatório: `--mm-processor-cache-gb 0`.
- `--enforce-eager` não acelera a subida (~70 s são inicialização de Python/vLLM) e deixa a
  inferência 50% mais lenta: não usar.

## Decisões do usuário

1. Itálico pela **opção 5**: filtro de inclinação na imagem + 2º modelo (VLM) que só marca.
2. Rodar **só na 3060**.
3. **Modo C (padrão):** os dois servidores ficam no ar, só um acordado por vez; a app alterna
   via `/sleep` e `/wake_up`. **Modo A** por parâmetro (`--switch restart`): a app **sobe e
   derruba** os servidores (executando `scripts/serve_vllm.sh`), sem servidor prévio.
4. Confirmação do itálico **por palavra** (versão refinada). Se o casamento palavra da
   imagem ↔ palavra do texto não se mostrar confiável, **voltar ao usuário com os números**
   antes de cair para a confirmação por bloco.
5. **Precisão > cobertura**.
6. Gabarito anotado à mão: o implementador faz a primeira marcação, o usuário revisa.
7. Item 4 como descrito abaixo.
8. Além do Benjamin, há **dois documentos de validação** em `/home/vitor/Amostras`, cada um com o
   PDF escaneado e uma conversão feita por **outra ferramenta** (uma diferente para cada). As
   conversões são satisfatórias, embora não idênticas ao original. **Meta: igual ou melhor que
   essas ferramentas.**
9. **Tempo não é problema:** escolher o 2º modelo mais preciso.
10. Regra de fusão do itálico: **implementar as duas variantes** (Item 5, passo d) e decidir
    pelos números.

## Item 4 — título

### Alinhamento
Hoje (`assemble._Builder._style`), bloco de uma linha quase simétrico na área útil vira
`center`. O título do exemplo começa em x ≈ 251 px e termina em 1395 (área útil 151–1547), daí
o erro.

Regra nova: coletar as **posições à esquerda recorrentes** do documento — início dos
subtítulos/títulos e posição da 1ª linha dos parágrafos (margem + recuo de 1ª linha). Bloco de
uma linha cujo início cai numa dessas posições (com tolerância) é `left` + recuo; só é `center`
se for simétrico **e** não cair numa delas. No exemplo, recuo de parágrafo ≈ 249 px e
subtítulos ≈ 251 px.

### Filete
Medido na p. 1 (200 dpi): linha de 2 px (~0,7 pt), 2 px abaixo do texto do título, de x = 254 a
1545 (até a margem direita; o texto termina em 1395). O dots não tem categoria para linhas e às
vezes inclui o filete no bbox do título, às vezes não (nesta execução não incluiu).
`probe._line_spans` já descarta filetes (altura ≤ 4 px e > 50% da largura) para não estragar a
medida da fonte.

Regra nova: para `title` e `section-header`, procurar numa faixa estreita **acima e abaixo** do
bbox (e dentro dele) uma linha fina e longa; registrar espessura, distância ao texto e
extensão (x inicial/final). O modelo (`BlockStyle` ou similar) ganha esses dados e o render
desenha borda superior/inferior com essas medidas.

## Item 5 — itálico (etapa 2)

### Fluxo por documento
1. **Etapa 1 (dots)** — como hoje: layout + texto de todas as páginas (`pages/*.json`).
2. **Etapa 2 (itálico)** — para cada bloco de texto (`text`, `list-item`, `caption`,
   `footnote`, títulos):
   a. **Filtro de inclinação** (sem modelo): segmenta linhas (a `probe` já faz) e palavras
      (lacunas horizontais) e estima a inclinação de cada palavra. O bloco segue para o 2º
      modelo se tiver ao menos uma palavra suspeita, ou se o dots já marcou itálico nele. O
      filtro deve ser **sensível** (não é ele que garante a precisão).
   b. **2º modelo (só marca):** recebe o recorte do bloco + o texto do dots e devolve o
      **mesmo texto** com `*…*` nos trechos em itálico. Aceito só se, sem os asteriscos, o
      texto for idêntico ao do dots (ignorando diferenças de espaço); senão, mantém o do dots e
      registra aviso.
   c. **Confirmação por palavra:** casar as palavras do texto com as caixas de palavra da
      imagem e medir a inclinação de cada uma. Palavras curtas demais para medir ("a", "de")
      herdam do trecho em volta. Sem evidência suficiente → fica normal.
   d. **Fusão** — implementar as duas regras e escolher pelos números (decisão 10):
      - **R1:** palavra é itálica se **o 2º modelo marcou** e a inclinação confirma.
      - **R2:** palavra é itálica se **o dots ou o 2º modelo marcou** e a inclinação confirma
        (a imagem segue como garantia de precisão; a cobertura não cai abaixo da do dots).
      Nos blocos que o filtro não mandou ao 2º modelo, mantém o itálico do dots.
3. Montagem (`assemble`) e render, como hoje.

### Cache
A etapa 2 tem cache próprio por página (ao lado de `pages/`), invalidado quando muda o modelo
ou o prompt do 2º passo. `--force` refaz as duas etapas.

### Escolha do 2º modelo
Por medição contra os gabaritos, **priorizando precisão; tempo não pesa** (decisão 9). Incluir
entre os candidatos o maior que caiba sozinho na 3060 (ex.: Qwen3-VL-8B em FP8 ou 4 bits;
Qwen3-VL-4B como referência menor; disponibilidade a verificar). O modo C limita os pesos a
~6–8 GB pela RAM. **Se o mais preciso só funcionar bem no modo A nesta 3060, avisar o usuário
com os números, sem descartar nada**: o modelo mais preciso e os dois modos continuam no
projeto, porque o usuário vai validar depois em outra máquina.

## Formato de saída (página e fonte)

Hoje o PDF gerado já usa o tamanho da página escaneada, com margens e fontes medidas (Benjamin:
Letter; Horkheimer: 481×679 pt; Althusser: 389×575 pt). Novas opções, no `convert` e no
`render` (aplicadas na renderização; o `document.json` guarda sempre o formato original medido,
de modo que as variantes saem do mesmo JSON sem refazer o OCR):

- `--page-size original|a4|<largura>x<altura>` — padrão `original`; dimensões em **cm**
  (ex.: `17.6x25` para B5; aceita vírgula decimal). Fora de 5–100 cm → erro claro.
- `--margin <cm>` — margens quando o tamanho não é `original`; padrão 2,5 cm. *(Decisão do
  Claude.)*
- `--font-size <pt>` — tamanho do corpo; escala proporcionalmente títulos, notas e cabeçalho.
  Padrão: o medido. (As referências do Horkheimer/Althusser usam 12 pt em A4.)

Consequências: o filete do título (Item 4) guarda a extensão **relativa à área de texto**, não
em pt absolutos; figuras ficam limitadas à largura útil. Recuos (em `em`), notas e cabeçalho
já se adaptam.

Fora deste design: casar a **paginação** com o livro (p. 124 do livro = p. 124 do PDF). Mesmo em
`original`, as quebras de página são do WeasyPrint.

## Execução na GPU

- **Modo C (padrão):** um script sobe o par uma vez por sessão (dots primeiro, faz dormir, depois
  o 2º modelo), ambos com `--enable-sleep-mode`, `VLLM_SERVER_DEV_MODE=1`,
  `--mm-processor-cache-gb 0`, `--host 127.0.0.1`, cada um numa porta. Toda chamada da app a
  um servidor passa por um "trocador" que garante aquele servidor acordado e o outro dormindo
  (`/is_sleeping`, `/sleep?level=1`, `/wake_up`). Se o par não estiver no ar, a app falha com
  mensagem dizendo como subir. Os servidores sobrevivem entre execuções (é daí que vem a
  vantagem de C).
- **Modo A (`--switch restart`):** a app executa `scripts/serve_vllm.sh` para subir o dots, roda
  a etapa 1 (e a pergunta da fonte), derruba, sobe o 2º modelo, roda a etapa 2, derruba.
- `--host 127.0.0.1` passa a valer também no modo normal do `serve_vllm.sh` (hoje ele escuta em
  `0.0.0.0` sem autenticação).
- `GPU_MEM` automático como hoje (memória livre − 512 MiB), que no teste já descontou o resíduo
  do servidor dormindo.

## Gabarito e avaliação

Documentos (em `/home/vitor/Amostras`, fora do repositório):

| Documento | Escaneado | Conversão de referência | Interesse |
|---|---|---|---|
| Benjamin (`exemplo/sample.pdf`) | 14 págs, Arial | — | itálico em frases longas, sans |
| Horkheimer, *Teoria tradicional e teoria crítica* | 49 págs (copiadora Canon), serifada, levemente torta | 45 págs, pdfTeX | itálico em palavras isoladas (*lógos*, *práxis*), notas |
| Althusser, *Ler O Capital* | 10 págs, serifada | 5 págs, pdfTeX | muito itálico em palavras/expressões soltas, citação em bloco |

- As conversões de referência **omitem de propósito** folha de rosto, índice e o que não é o
  texto propriamente dito (as ferramentas permitiam escolher as páginas); por isso têm menos
  páginas, e em outro formato (A4). A comparação usa só o conteúdo presente na referência,
  casado com o escaneado **pelo texto**, não pelo número da página; a conversão pelo `ocrpdf`
  usa `--pages` com as páginas de texto correspondentes.
- Gabaritos: Benjamin marcado a partir das imagens; Horkheimer e Althusser com rascunho
  extraído **da união** do itálico da conversão de referência (fontes `SFTI…` = itálico) com a
  marcação a partir das imagens, para não herdar só os erros da referência. O usuário revisa
  os três. *(Decisão do Claude.)*
- Os gabaritos e arquivos de Horkheimer e Althusser **não entram no git** (direitos autorais);
  ficam em pasta local ignorada e o script de avaliação recebe os caminhos. O do Benjamin pode
  ficar no repositório, junto do exemplo já publicado. *(Decisão do Claude.)*
- Métrica **por palavra** sobre o texto do dots: precisão (itálicos marcados que estão certos) e
  cobertura (itálicos reais encontrados). Medir também o dots sozinho como referência.
- Um script de avaliação reproduzível (roda sobre o cache, sem refazer a etapa 1).
- *Decisão do Claude:* calibrar limiares (filtro, confirmação) num subconjunto das páginas e
  reportar precisão/cobertura **só nas demais**, para os números não saírem otimistas.

## Critérios de aceite

- Itálico, em cada documento: **precisão ≥ 90% por palavra** (definido pelo usuário); cobertura
  a maior possível dentro disso e maior que a do dots sozinho; nenhuma palavra alterada.
- "Igual ou melhor que as outras ferramentas" (decisão 8), *interpretado pelo Claude* como: no
  Horkheimer e no Althusser, precisão e cobertura de itálico **≥ às da conversão de referência**,
  medidas no mesmo gabarito. Comparação visual lado a lado do resultado completo como relatório
  (não como critério de aceite deste design).
- Título do exemplo: alinhado à esquerda com o recuo dos subtítulos e com o filete até a margem
  direita; subtítulos do exemplo inalterados; título realmente centralizado continua centralizado
  (teste sintético).
- Modo C: um `ocrpdf convert` com o par no ar roda as duas etapas sem intervenção; modo A roda
  sem servidor prévio.
- `--page-size`/`--margin`/`--font-size`: o PDF sai com o tamanho e as margens pedidos (conferido
  pelo `mediabox` com pypdf) e o corpo no tamanho pedido; notas continuam na página da chamada.
- Testes existentes passam; testes novos para alinhamento, filete, validação "texto idêntico",
  trocador (com transporte HTTP falso), formato de saída e avaliação.

## Deixado ao implementador

- Algoritmos de segmentação de palavras, estimativa de inclinação e casamento palavra↔texto
  (o critério é o de aceite; o usuário quer a versão por palavra).
- Formato do cache da etapa 2, do gabarito e do script de avaliação.
- Nomes de módulos, de opções da CLI (exceto `--switch restart`) e do script do par.
- Texto do prompt do 2º modelo.
- Resolução do recorte enviado ao 2º modelo e paralelismo da etapa 2.

## Riscos

- Casamento palavra↔texto pode falhar com hifenização, pontuação e linhas muito justificadas —
  se falhar, volta ao usuário (decisão 4).
- Fontes serifadas têm itálico "verdadeiro" e às vezes pouca inclinação: o filtro precisa ser
  conservador na confirmação e sensível na triagem (Horkheimer e Althusser validam isso).
- Página torta (Horkheimer): a inclinação das palavras deve ser medida em relação à linha de
  base local, não ao eixo da imagem.
- RAM no modo C: um 2º modelo maior que ~8 GB de pesos deixa a máquina perto do swap.
- Os endpoints de sleep são de modo "dev" do vLLM e podem mudar entre versões (vLLM fixado em
  0.17.1).
