# Itálico por palavra — avaliação e escolha da regra (Task 10)

Data: 2026-10-21. Máquina: RTX 3060 12 GB (desktop GNOME ocupando ~0,25–1,3 GB), vLLM 0.17.1.

## Resumo

- O 2º modelo **quase nunca marca itálico**: devolve o bloco sem marcas na maior parte das vezes.
  Com ele, R1 cobre de 0,5% a 60% do itálico, e R2 fica igual ou abaixo do dots sozinho.
- A **inclinação medida na imagem** separa itálico de redondo quase sem erro (itálico 9,5–16,5°
  entre os percentis 5 e 95; o redondo fica em 0° até o p95, e só 0,13% dele passa de 4°).
- Regra adotada (padrão, `--italic-rule slant`; "R3u" nas tabelas): **onde a inclinação foi medida,
  ela decide (≥ 7°); onde não foi, vale o dots**. Não usa 2º modelo, então basta o servidor do OCR.
  É a única regra que atinge todos os critérios nos três documentos.
- O 2º modelo continua disponível (`--italic-rule r1|r2`), com padrão
  `cyankiwi/Qwen3-VL-8B-Instruct-AWQ-4bit`, o que mais marcou entre os que cabem na 3060.
- Decisão do usuário em 2026-10-21: regra `slant` como padrão, 2º modelo opcional (opção 1).

## Documentos e gabaritos

| documento | páginas avaliadas | itálicos no gabarito | referência (outra ferramenta) |
|---|---|---|---|
| Benjamin (`exemplo/sample.pdf`) | 1–14 | 783 palavras | — |
| Althusser, *Para ler O Capital* | 5–10 | 117 | PDF de texto |
| Horkheimer, *Teoria tradicional e teoria crítica* | 5–49 | 340 | PDF de texto (LaTeX) |

Gabaritos anotados à mão a partir das imagens. O do Benjamin está em `exemplo/gabarito_italico.json`;
os das amostras ficam só no disco (`amostras/`, fora do git). Calibração nas páginas **ímpares** e
medição nas **pares**.

Critérios (plano, Task 10, passo 6), nas páginas pares de cada documento: precisão ≥ 90%;
cobertura maior que a do dots sozinho; no Althusser e no Horkheimer, precisão e cobertura ≥ às da
referência.

## Resultados — páginas pares (precisão / cobertura, %)

| regra | Benjamin | Althusser | Horkheimer |
|---|---|---|---|
| dots sozinho | 100 / 46,1 | 100 / 97,5 | 99,3 / 90,8 |
| referência | — | 92,5 / 92,5 | 98,2 / 71,2 |
| 8B-AWQ, R1 | 100 / 3,8 | 100 / 60,0 | 100 / 54,9 |
| 8B-AWQ, R2 | 100 / 43,6 | 100 / 95,0 | 99,3 / 89,5 |
| 4B-FP8, R1 | 100 / 0,5 | 100 / 27,5 | 100 / 24,2 |
| 4B-FP8, R2 | 100 / 42,5 | 100 / 95,0 | 99,3 / 87,6 |
| R3 (só inclinação; curta sem medida herda dos vizinhos) | 99,4 / 94,9 | 94,7 / 90,0 | 95,8 / 75,2 |
| R3d (R3 só em blocos com alguma medida) | 99,4 / 94,9 | 95,0 / 95,0 | 96,5 / 89,5 |
| **R3u = `slant`** (inclinação; sem medida, o dots) | **99,4 / 89,2** | **97,6 / 100** | **98,6 / 92,2** |

R3 é a melhor no Benjamin, onde o dots erra parágrafos inteiros, mas no Horkheimer cobre menos que o
dots. R3u herda os erros do dots nas palavras sem medida (Benjamin), mas atinge os critérios nos três.

Todas as páginas (P/C): R3u Benjamin 97,7 / 93,7; Althusser 98,3 / 96,6; Horkheimer 98,7 / 90,9.

### Sensibilidade ao limiar (R3u, ímpares | pares)

| limiar | Benjamin | Althusser | Horkheimer |
|---|---|---|---|
| 4° | 96,4/97,8 \| 99,4/89,4 | 98,6/94,8 \| 97,6/100 | 98,8/89,8 \| 98,6/92,2 |
| 7° | 96,4/97,8 \| 99,4/89,2 | 98,6/94,8 \| 97,6/100 | 98,8/89,8 \| 98,6/92,2 |
| 9° | 96,3/94,2 \| 99,4/88,1 | 98,6/94,8 \| 97,6/100 | 98,8/89,3 \| 98,6/92,2 |
| 11° | 95,5/76,1 \| 99,3/72,4 | 100/93,5 \| 97,6/100 | 98,8/88,2 \| 98,6/91,5 |

Patamar estável entre 4° e 9° nas ímpares; o `ITALIC_MIN_DEG = 7°` do design fica no meio dele e
não foi alterado. `SUSPECT_DEG` e `MATCH_MIN` não afetam a regra `slant` e ficaram como estavam.

## Execução do 2º modelo

| modelo | pesos | como coube na 3060 | Benjamin | Althusser | Horkheimer |
|---|---|---|---|---|---|
| `cyankiwi/Qwen3-VL-8B-Instruct-AWQ-4bit` | 7,0 GiB | par (dots dormindo), MAX_LEN 8192, imagem ≤ 1,6 MP, sem vídeo, 2 req. | 2 min 05 s (33 blocos) | 30 s (40) | ~6 min 36 s (113) |
| `Qwen/Qwen3-VL-4B-Instruct-FP8` | 5,6 GiB | idem; refeito sozinho no Horkheimer (GPU_MEM 0,72) | 2 min 27 s (33) | 35 s (40) | 4 min 49 s (113) |
| `Qwen/Qwen3-VL-8B-Instruct-FP8` | 9,9 GiB | não cabe com o desktop; não medido | — | — | — |

- Sem limitar a imagem, o vLLM reserva memória para a imagem máxima do Qwen3-VL (16,7 MP) e o 8B
  não cabe. Os recortes de bloco têm ~0,4 MP. Daí `MAX_IMAGE_PX` em `scripts/serve_vllm.sh` e
  `ITALIC_SERVER_ENV` em `src/ocrpdf/servers.py`.
- Com 4 requisições simultâneas, o encoder do 8B estourou a VRAM em recortes grandes do Horkheimer;
  com 2, não.
- RAM livre não foi medida (a máquina tem 31 GB; não houve falta).
- A outra máquina disponível (RTX 4070) também tem 12 GB: o 8B-FP8 (9,9 GiB de pesos) deve caber
  só sem desktop, no modo `restart`, com contexto curto. Não testado.

## Incidente: queda da sessão gráfica

Com o par no ar (dots dormindo + 4B com `GPU_MEM` 0,83), sobravam ~0,5 GiB de VRAM para o desktop.
O gnome-shell caiu (segfault em `libmutter`), o GNOME encerrou a sessão e levou junto os servidores
e o Claude Code. O vLLM estava ocioso e não registrou falta de memória; a falta de VRAM para o
compositor é a causa provável, não comprovada. Repetido só com o 4B (`GPU_MEM` 0,72, ~2,6 GiB livres),
não houve falha. Com a regra `slant` o par não é mais necessário.

## Pendências encontradas

- **Páginas inclinadas quebram a tipografia** (fora do itálico): `probe.measure` separa linhas pela
  projeção horizontal e exige uma linha de pixels sem tinta entre elas. Com ~1° de inclinação,
  blocos largos viram uma "linha" só, e a fonte medida chega a 95–376 pt. Resultado: o Horkheimer
  sai com 1613 páginas, e o Althusser com título de 375 pt e parágrafos de 18–25 pt. Endireitando o
  recorte em −1°, o mesmo bloco dá 27 linhas e entrelinha de 35 px. Por isso a comparação visual
  com as referências (passo 7 do plano) ficou para depois da correção.
