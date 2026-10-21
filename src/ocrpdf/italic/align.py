"""Casamento dos tokens do texto (OCR) com as caixas de palavra da imagem.

Programação dinâmica sobre tokens × caixas, as duas sequências na ordem de leitura, com as
operações: token ↔ caixa; token ↔ 2 caixas (palavra hifenizada: a 1ª fecha uma linha, a 2ª abre a
seguinte); 2 tokens ↔ 1 caixa (espaço apertado); caixa sobrando (sujeira, sobrescrito solto); token
sem caixa. O custo de um par é z²/2, com z o desvio entre a largura da caixa e a esperada (nº de
caracteres × escala da linha) em desvios-padrão; a variância cresce com o nº de caracteres. Um
deslocamento sai caro em todos os pares seguintes, por isso o caminho mais barato isola a palavra
que falta (ou sobra) em vez de propagar o erro. Caixas sobrando seguidas custam menos que isoladas
(linha que o OCR pulou). Precisão > cobertura: o token cuja melhor alternativa custa quase o mesmo
que a escolhida fica sem caixa.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .slant import WordBox

MATCH_MIN = 0.8   # abaixo dessa fração de tokens com caixa, o bloco fica sem evidência de inclinação

_SIGMA = 0.45      # desvio-padrão da largura de uma palavra de n letras: _SIGMA·√(n + 1) caracteres
_SKIP_TOKEN = 2.0  # custo de um token sem caixa
_SKIP_BOX = 2.0    # custo de uma caixa sem token
_SKIP_RUN = 0.5    # custo de cada caixa sem token a mais numa sequência (linha que o OCR pulou)
_SPLIT = 0.5       # custo extra de uma palavra hifenizada (token ↔ 2 caixas)
_MERGE = 1.0       # custo extra de 2 tokens numa caixa
_HYPHEN = 0.7      # largura do hífen, em caracteres
_SPACE = 0.5       # largura de um espaço apertado, em caracteres
_PART = 1.5        # largura mínima (caracteres) de cada pedaço de uma palavra hifenizada
_PRIOR = 20.0      # caracteres "emprestados" da escala global à de cada linha
_FACTORS = (0.8, 1.0, 1.25)  # escalas do bloco testadas, em relação a Σ larguras / Σ caracteres
_MARGIN = 1.0      # folga mínima de custo sobre a melhor alternativa para o token ficar com a caixa

# operações que chegam a cada célula da tabela (índices da pilha de candidatos de _forward)
_ONE, _TWO_BOXES, _TWO_TOKENS, _TOKEN = range(4)


def match_tokens(tokens: list[str], boxes: list[WordBox]) -> list[list[WordBox]]:
    """Caixas de cada token (lista vazia = sem caixa), na ordem de leitura.

    A escala (px por caractere) da 1ª passada é a do bloco (`_block_scale`); a da 2ª é a de cada
    linha, medida nos pares 1:1 da 1ª (uma linha em itálico ou noutro corpo tem outra largura por
    caractere).
    """
    if not tokens or not boxes:
        return [[] for _ in tokens]
    chars = np.array([len(t) for t in tokens], dtype=float)
    widths = np.array([b.x2 - b.x1 for b in boxes], dtype=float)
    lines = np.array([b.line for b in boxes])
    scale = np.full(len(boxes), _block_scale(chars, widths, lines))
    pairs = _align(chars, widths / scale, lines, sure=True)
    scale = _line_scale(pairs, chars, widths, lines, scale)
    out: list[list[WordBox]] = [[] for _ in tokens]
    for t, b in _align(chars, widths / scale, lines, sure=True):
        out[t].append(boxes[b])
    return out


def match_quality(matches: list[list[WordBox]]) -> float:
    """Fração dos tokens com ao menos uma caixa (0 sem tokens)."""
    return sum(1 for m in matches if m) / len(matches) if matches else 0.0


def _block_scale(chars: np.ndarray, widths: np.ndarray, lines: np.ndarray) -> float:
    """Σ larguras / Σ caracteres × 0,8, 1 ou 1,25: a que der o alinhamento mais barato. Uma linha
    que o OCR pulou, ou palavras grudadas num bloco curto, desviam a razão das somas."""
    base = widths.sum() / chars.sum()
    return min((base * f for f in _FACTORS),
               key=lambda s: _forward(*_pair_costs(chars, widths / s, lines)).cost[-1, -1])


def _line_scale(pairs: list[tuple[int, int]], chars: np.ndarray, widths: np.ndarray,
                lines: np.ndarray, scale: np.ndarray) -> np.ndarray:
    """Escala de cada caixa: Σ larguras / Σ caracteres dos pares 1:1 da sua linha, com _PRIOR
    caracteres na escala global (dos pares 1:1 do bloco) para a linha com poucos pares não oscilar."""
    tok = np.bincount([t for t, _ in pairs], minlength=len(chars))
    box = np.bincount([b for _, b in pairs], minlength=len(widths))
    one = [(t, b) for t, b in pairs if tok[t] == 1 and box[b] == 1]
    if not one:
        return scale
    t, b = np.array(one).T
    w = np.bincount(lines[b], weights=widths[b], minlength=lines.max() + 1)
    c = np.bincount(lines[b], weights=chars[t], minlength=lines.max() + 1)
    base = w.sum() / c.sum()
    return ((w + _PRIOR * base) / (c + _PRIOR))[lines]


def _align(chars: np.ndarray, size: np.ndarray, lines: np.ndarray,
           sure: bool = False) -> list[tuple[int, int]]:
    """Pares (token, caixa) do alinhamento de menor custo; `size` = largura da caixa em caracteres.

    Com `sure`, fica só o token cuja melhor alternativa (outras caixas ou nenhuma) custa ao menos
    _MARGIN a mais: onde dois caminhos quase empatam (qual de duas palavras parecidas sumiu), o
    token fica sem caixa em vez de arriscar a errada.
    """
    one, split, merge = _pair_costs(chars, size, lines)
    table = _forward(one, split, merge)
    pairs = _backtrack(table)
    if not sure:
        return pairs
    # custo do resto do caminho a partir de (i, j): a mesma tabela com as duas sequências invertidas
    back = _forward(one[::-1, ::-1], split[::-1, ::-1], merge[::-1, ::-1]).cost[::-1, ::-1]
    margin = _margins(table.cost, back, one, split, merge, pairs)
    return [(t, b) for t, b in pairs if margin[t] >= _MARGIN]


def _pair_costs(chars: np.ndarray, size: np.ndarray,
                lines: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Custos de token ↔ caixa, token ↔ 2 caixas (hífen) e 2 tokens ↔ caixa (espaço apertado)."""
    one = _cost(size[None, :], chars[:, None])                                         # (tokens, caixas)
    # hífen: caixas j e j+1 em linhas seguidas, cada pedaço com ao menos _PART caracteres
    split = _cost((size[:-1] + size[1:])[None, :], chars[:, None] + _HYPHEN) + _SPLIT  # (tokens, caixas − 1)
    split[:, (lines[1:] != lines[:-1] + 1) | (size[:-1] < _PART) | (size[1:] < _PART)] = np.inf
    # espaço apertado: tokens i e i+1 numa caixa só
    merge = _cost(size[None, :], (chars[:-1] + chars[1:] + _SPACE)[:, None]) + _MERGE  # (tokens − 1, caixas)
    return one, split, merge


@dataclass
class _Table:
    cost: np.ndarray   # custo mínimo para consumir i tokens e j caixas
    base: np.ndarray   # idem, sem terminar numa sequência de caixas sobrando
    op: np.ndarray     # operação que chega a `base` em cada célula
    run: np.ndarray    # `cost` vem de uma sequência de caixas sobrando


def _forward(one: np.ndarray, split: np.ndarray, merge: np.ndarray) -> _Table:
    """Tabela da programação dinâmica, preenchida uma linha de token por vez e vetorizada sobre as
    caixas. Caixas sobrando seguidas (passo dentro da mesma linha da tabela) saem de um mínimo
    acumulado: a 1ª custa _SKIP_BOX e cada uma a mais _SKIP_RUN (linha inteira que o OCR pulou).
    """
    n_tok, n_box = one.shape
    shape = (n_tok + 1, n_box + 1)
    t = _Table(np.full(shape, np.inf), np.full(shape, np.inf), np.zeros(shape, dtype=np.int8),
               np.zeros(shape, dtype=bool))
    ramp = np.arange(n_box) * _SKIP_RUN                                  # para o início j0
    gap = np.arange(1, n_box + 1) * _SKIP_RUN + _SKIP_BOX - _SKIP_RUN    # para o fim j
    cand = np.full((4, n_box + 1), np.inf)   # candidatos a base[i], na ordem dos códigos de operação
    t.base[0, 0] = 0.0
    for i in range(n_tok + 1):
        if i:
            prev = t.cost[i - 1]
            np.add(prev[:-1], one[i - 1], out=cand[_ONE, 1:])
            np.add(prev[:-2], split[i - 1], out=cand[_TWO_BOXES, 2:])
            if i >= 2:
                np.add(t.cost[i - 2, :-1], merge[i - 2], out=cand[_TWO_TOKENS, 1:])
            np.add(prev, _SKIP_TOKEN, out=cand[_TOKEN])
            t.op[i] = cand.argmin(axis=0)
            t.base[i] = cand.min(axis=0)
        # caixas j0..j − 1 sobrando: base[i, j0] + _SKIP_BOX + (j − j0 − 1)·_SKIP_RUN
        skipped = np.minimum.accumulate(t.base[i, :-1] - ramp) + gap
        t.run[i, 1:] = skipped < t.base[i, 1:]
        t.cost[i] = t.base[i]
        np.minimum(t.cost[i, 1:], skipped, out=t.cost[i, 1:])
    return t


def _backtrack(t: _Table) -> list[tuple[int, int]]:
    i, j = t.op.shape[0] - 1, t.op.shape[1] - 1
    ramp = np.arange(j + 1) * _SKIP_RUN
    pairs = []
    while i or j:
        if t.run[i, j]:   # volta ao início da sequência de caixas sobrando
            j = int(np.argmin(t.base[i, :j] - ramp[:j]))
            if not (i or j):
                break
        o = t.op[i, j]
        if o == _ONE:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif o == _TWO_BOXES:
            pairs += [(i - 1, j - 1), (i - 1, j - 2)]
            i, j = i - 1, j - 2
        elif o == _TWO_TOKENS:
            pairs += [(i - 1, j - 1), (i - 2, j - 1)]
            i, j = i - 2, j - 1
        else:   # _TOKEN
            i -= 1
    return pairs[::-1]


def _margins(fwd: np.ndarray, back: np.ndarray, one: np.ndarray, split: np.ndarray,
             merge: np.ndarray, pairs: list[tuple[int, int]]) -> np.ndarray:
    """Para cada token, quanto custa a mais o melhor caminho que lhe dá outras caixas (ou nenhuma).

    O custo do melhor caminho que passa por uma operação é fwd (até ela) + a operação + back (dela
    até o fim); as alternativas de um token são as operações que o consomem.
    """
    n_tok, _ = one.shape
    single = fwd[:-1, :-1] + one + back[1:, 1:]                     # token → caixa j
    both = fwd[:-2, :-1] + merge + back[2:, 1:]                     # tokens t, t + 1 → caixa j
    single[:-1] = np.minimum(single[:-1], both)
    single[1:] = np.minimum(single[1:], both)
    double = fwd[:-1, :-2] + split + back[1:, 2:]                   # token → caixas j, j + 1
    none = (fwd[:-1] + back[1:]).min(axis=1) + _SKIP_TOKEN          # token sem caixa

    chosen: list[list[int]] = [[] for _ in range(n_tok)]
    for t, b in pairs:
        chosen[t].append(b)
    for t, bs in enumerate(chosen):   # tira a escolha feita; sobram as alternativas
        if len(bs) == 1:
            single[t, bs[0]] = np.inf
        elif len(bs) == 2:
            double[t, bs[0]] = np.inf
        else:
            none[t] = np.inf
    alt = np.minimum.reduce([single.min(axis=1, initial=np.inf), double.min(axis=1, initial=np.inf), none])
    return alt - fwd[-1, -1]


def _cost(size: np.ndarray, chars: np.ndarray) -> np.ndarray:
    """z²/2 do desvio entre a largura medida e a esperada (em caracteres): a largura de uma palavra
    é a soma das larguras das letras, por isso a variância cresce com o nº de caracteres."""
    return (size - chars) ** 2 / (2 * _SIGMA ** 2 * (chars + 1))
