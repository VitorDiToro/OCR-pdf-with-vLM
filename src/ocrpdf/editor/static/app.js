// ocrpdf edit — página do editor (JS puro, sem bibliotecas). Consome só a API do servidor local.
'use strict';

// ===== Funções puras (sem DOM) ==========================================================

const KIND_NAMES = {
  title: 'título', heading: 'intertítulo', paragraph: 'parágrafo', list_item: 'item de lista',
  caption: 'legenda', table: 'tabela', formula: 'fórmula', figure: 'figura', endnote: 'nota de fim',
};
// tipos em que o render aplica alinhamento e negrito/itálico do bloco (render/html.py)
const TEXT_KINDS = ['paragraph', 'list_item', 'caption', 'title', 'heading'];
const SPLIT_MARK = '\ue000';   // ponto de divisão enviado ao servidor (a mesma marca da sessão)

/**
 * Intervalos [início, fim) de cada objeto de 1º nível do array JSON do texto.
 * Conta { } e [ ] fora de strings (respeitando escapes). null se a estrutura não fecha.
 */
function scanBlocks(text) {
  const ranges = [];
  const stack = [];
  let inStr = false, esc = false, start = -1, root = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inStr) {
      if (esc) esc = false;
      else if (c === '\\') esc = true;
      else if (c === '"') inStr = false;
      continue;
    }
    if (c === '"') {
      inStr = true;
    } else if (c === '{' || c === '[') {
      if (stack.length === 0) {
        if (c !== '[' || root) return null;   // o texto é um único array
        root = true;
      } else if (stack.length === 1 && c === '{') {
        start = i;
      }
      stack.push(c);
    } else if (c === '}' || c === ']') {
      const open = stack.pop();
      if (open === undefined || (open === '{') !== (c === '}')) return null;
      if (stack.length === 1 && c === '}') ranges.push([start, i + 1]);
    }
  }
  return inStr || stack.length || !root ? null : ranges;
}

/** Bloco (0-based) que contém a posição `pos`; vale a linha inteira do `{` e do `}`. -1 se nenhum. */
function blockAt(text, ranges, pos) {
  if (!ranges) return -1;
  for (let k = 0; k < ranges.length; k++) {
    if (pos >= ranges[k][0] && pos <= ranges[k][1]) return k;
  }
  for (let k = 0; k < ranges.length; k++) {
    const from = text.lastIndexOf('\n', ranges[k][0] - 1) + 1;
    let to = text.indexOf('\n', ranges[k][1]);
    if (to < 0) to = text.length;
    if (pos >= from && pos <= to) return k;
  }
  return -1;
}

/** Linha e coluna (1-based) de um offset. */
function lineCol(text, offset) {
  const before = text.slice(0, Math.max(0, Math.min(offset, text.length)));
  const nl = before.lastIndexOf('\n');
  return { line: before.split('\n').length, col: before.length - nl };
}

/**
 * Onde o JSON é inválido: {pos, msg} do primeiro erro, ou null se for válido. Validador mínimo
 * (RFC 8259) só para achar a linha: as mensagens do JSON.parse variam e o Chrome nem sempre dá a posição.
 */
function jsonError(text) {
  let i = 0;
  const fail = (msg, at = i) => {
    throw { pos: at, msg: at >= text.length ? 'o texto acabou antes de fechar (falta "]" ou "}")' : msg };
  };
  const ws = () => { while (i < text.length && ' \t\n\r'.includes(text[i])) i++; };
  const num = /-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/y;
  function string() {
    const start = i++;
    while (i < text.length) {
      const c = text[i];
      if (c === '"') { i++; return; }
      if (c === '\\') {
        const e = text[i + 1];
        if (e === 'u') {
          if (!/^[0-9a-fA-F]{4}$/.test(text.slice(i + 2, i + 6))) fail('escape \\u inválido');
          i += 6;
        } else if (e !== undefined && '"\\/bfnrt'.includes(e)) {
          i += 2;
        } else {
          fail('barra invertida inválida (use \\\\ para uma barra)');
        }
        continue;
      }
      if (c < ' ') fail('quebra de linha ou caractere de controle dentro de um texto (use \\n)');
      i++;
    }
    fail('texto sem as aspas de fechamento', start);
  }
  function value() {
    ws();
    const c = text[i];
    if (c === '{') {
      i++; ws();
      if (text[i] === '}') { i++; return; }
      for (;;) {
        ws();
        if (text[i] !== '"') fail('esperava o nome de um campo entre aspas');
        string(); ws();
        if (text[i] !== ':') fail('esperava ":"');
        i++; value(); ws();
        if (text[i] === ',') { i++; continue; }
        if (text[i] === '}') { i++; return; }
        fail('esperava "," ou "}"');
      }
    }
    if (c === '[') {
      i++; ws();
      if (text[i] === ']') { i++; return; }
      for (;;) {
        value(); ws();
        if (text[i] === ',') { i++; continue; }
        if (text[i] === ']') { i++; return; }
        fail('esperava "," ou "]"');
      }
    }
    if (c === '"') return string();
    num.lastIndex = i;
    const m = num.exec(text);
    if (m) { i += m[0].length; return; }
    for (const lit of ['true', 'false', 'null']) {
      if (text.startsWith(lit, i)) { i += lit.length; return; }
    }
    fail(`valor inesperado: ${c}`);
  }
  try {
    value(); ws();
    if (i < text.length) fail('sobra texto depois do fim da lista');
    return null;
  } catch (e) {
    if (e && typeof e.pos === 'number') return e;
    throw e;
  }
}

/** Analisa o texto do editor: dados (se for uma lista JSON de objetos), intervalos dos blocos e erro. */
function analyzeText(text) {
  const ranges = scanBlocks(text);
  let data = null, error = null;
  try {
    data = JSON.parse(text);
  } catch (e) {
    const where = jsonError(text);
    const at = where ? lineCol(text, where.pos) : null;
    error = { message: where ? where.msg : String(e.message), line: at ? at.line : null, col: at ? at.col : null };
  }
  if (!error && !Array.isArray(data)) {
    error = { message: 'o texto deve ser uma lista JSON de blocos: [ {…}, {…} ]', line: 1, col: 1 };
  } else if (!error && data.some((b) => !b || typeof b !== 'object' || Array.isArray(b))) {
    error = { message: 'cada item da lista deve ser um bloco {…}', line: null, col: null };
  }
  if (error) data = null;
  return { data, ranges, error };
}

/** Forma canônica para comparar textos (ignora formatação: 3.0 == 3, espaços). */
function canonical(text, data) {
  return data ? JSON.stringify(data) : text;
}

function styleOf(b) {
  return b.style && typeof b.style === 'object' && !Array.isArray(b.style) ? b.style : {};
}

/** Grava (ou apaga, com undefined) um campo de `style`; `style` vazio some do bloco. */
function setStyle(b, key, value) {
  const st = styleOf(b);
  if (value === undefined) delete st[key];
  else st[key] = value;
  if (Object.keys(st).length) b.style = st;
  else delete b.style;
}

/** Trechos que decidem se o bloco "está" em negrito/itálico: com texto visível, fora chamadas de nota e fórmulas. */
function markSpans(b) {
  const spans = Array.isArray(b.spans) ? b.spans : [];
  return spans.filter((s) => s && typeof s === 'object' && typeof s.text === 'string'
    && s.text.trim() !== '' && !s.note && !s.math);
}
function allMarked(b, mark) {
  const spans = markSpans(b);
  return spans.length > 0 && spans.every((s) => s[mark] === true);
}
/**
 * Negrito efetivo do bloco: o sobrescrito; sem ele, títulos (o CSS põe negrito) ou todos os trechos em
 * negrito. Com `false` o render tira também o negrito dos trechos (render/html.py).
 */
function isBold(b) {
  const v = styleOf(b).bold;
  if (typeof v === 'boolean') return v;
  return b.kind === 'title' || b.kind === 'heading' || allMarked(b, 'bold');
}
/** Itálico efetivo do bloco: o sobrescrito, ou todos os trechos em itálico. */
function isItalic(b) {
  const v = styleOf(b).italic;
  return typeof v === 'boolean' ? v : allMarked(b, 'italic');
}
function isJustified(b) { const a = styleOf(b).align; return a === undefined || a === 'justify'; }
function isHidden(b) { return styleOf(b).hidden === true; }
function hasBreak(b) { return styleOf(b).page_break_before === true; }
function linesBefore(b) {
  const n = styleOf(b).space_before_lines;
  return typeof n === 'number' && Number.isFinite(n) ? n : 0;   // fração (0.5) ou negativo (aproxima)
}

/** Aplica uma ação da barra do bloco ao objeto `b` (forma compacta), no lugar. */
function applyAction(b, action, value) {
  switch (action) {
    case 'bold': return setStyle(b, 'bold', !isBold(b));
    case 'italic': return setStyle(b, 'italic', !isItalic(b));
    case 'justify': return setStyle(b, 'align', undefined);   // justify é o padrão: o campo some
    case 'break': return setStyle(b, 'page_break_before', hasBreak(b) ? undefined : true);
    case 'more': return setStyle(b, 'space_before_lines', linesBefore(b) + 1);
    case 'less': return setStyle(b, 'space_before_lines', Math.max(0, linesBefore(b) - 1) || undefined);
    case 'hide': return setStyle(b, 'hidden', isHidden(b) ? undefined : true);
    case 'size': return setStyle(b, 'font_size_pt', value === null ? undefined : value);
    default: throw new Error('ação desconhecida: ' + action);
  }
}

function round1(x) { return Math.round(x * 10) / 10; }

/** Fator entre a escala medida do documento e a da saída (corpo da faixa ÷ body_font_pt; 1 sem corpo). */
function outputScale(bodyPt, measuredBodyPt) {
  return bodyPt && measuredBodyPt ? bodyPt / measuredBodyPt : 1;
}

/** Número digitado (aceita vírgula); null se vazio; NaN se inválido. */
function parseNum(s) {
  const t = String(s).trim().replace(',', '.');
  return t === '' ? null : Number(t);
}

const PAGE_SIZE_RE = /^(original|a4|\d+(?:[.,]\d+)?\s*x\s*\d+(?:[.,]\d+)?)$/i;

/** Valida os campos da faixa de formato; devolve {format} ou {error, field}. */
function readFormat(raw) {
  const page = String(raw.page).trim();
  if (!PAGE_SIZE_RE.test(page)) {
    return { error: 'página inválida: use original, a4 ou LxA em cm (ex.: 14.4x21.6)', field: 'page' };
  }
  const margin = parseNum(raw.margin);
  if (margin === null || !Number.isFinite(margin) || margin < 0) {
    return { error: 'margem inválida: um número em cm', field: 'margin' };
  }
  const body = parseNum(raw.body);
  if (body !== null && !(Number.isFinite(body) && body > 0)) {
    return { error: 'corpo inválido: um número em pt, ou vazio para o medido', field: 'body' };
  }
  return { format: { page_size: page, margin_cm: margin, font_size_pt: body, header: Boolean(raw.header) } };
}

function sameFormat(a, b) {
  return Boolean(a && b)
    && String(a.page_size).trim().toLowerCase() === String(b.page_size).trim().toLowerCase()
    && a.margin_cm === b.margin_cm
    && (a.font_size_pt ?? null) === (b.font_size_pt ?? null)
    && a.header === b.header;
}

// ===== Estado e interface ===============================================================

const S = {
  pages: [],            // páginas (0-based) do original que têm blocos
  i: -1,                // índice em `pages` da página aberta
  page: null,           // página aberta (0-based)
  bodyFontPt: 0,        // corpo medido do documento
  hasOriginal: false,
  savedFormat: null,    // formato gravado no disco (o da sessão pode diferir depois de renderizar)
  loadedText: '',       // texto da página como veio do servidor (base do "não salvo" e do Descartar)
  loadedCanon: '',
  blocks: [],           // linhas de /api/page: {n, index, kind, hidden, font_pt, box}
  measured: [],         // tamanho padrão de cada bloco na escala medida (null se o bloco tinha font_size_pt)
  info: { data: null, ranges: null, error: null },   // análise do texto atual
  canon: '',
  starts: [0],          // offset do início de cada linha do texto
  tops: [0],            // topo (px) de cada linha no textarea
  cur: -1,              // bloco do cursor (0-based)
  sizeFor: -1,          // bloco cujo tamanho o campo "Tamanho" mostra
  sizeShown: '',        // valor mostrado no campo (Esc volta a ele)
  sessionTouched: false,   // o texto desta página foi enviado à sessão (renderizar/salvar) desde que carregou
  sessionDirty: false,     // a sessão tem alterações em memória que o texto da tela não mostra
  busy: null,
  busyStart: 0,
  timer: 0,
  leaveTarget: -1,
};

const ui = {};

class ApiError extends Error {
  constructor(status, message, data) {
    super(message);
    this.status = status;
    this.conflict = Boolean(data && data.conflict);   // o document.json mudou no disco (409 do salvar)
  }
}

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (method === 'POST') {
    opts.headers['Content-Type'] = 'application/json';   // o servidor exige (senão 415)
    opts.body = JSON.stringify(body || {});
  }
  let res;
  try {
    res = await fetch(url, opts);
  } catch (e) {
    throw new ApiError(0, 'sem conexão com o servidor (o "ocrpdf edit" ainda está rodando?)');
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* corpo vazio ou não JSON */ }
  if (!res.ok) throw new ApiError(res.status, (data && data.error) || `erro ${res.status}`, data);
  return data;
}

// --- estado na tela -----------------------------------------------------------------------

function textDirty() { return S.page !== null && S.canon !== S.loadedCanon; }
function formatState() {
  return readFormat({
    page: ui.fmtPage.value, margin: ui.fmtMargin.value, body: ui.fmtBody.value, header: ui.fmtHeader.checked,
  });
}
function formatDirty() { const f = formatState(); return !f.format || !sameFormat(f.format, S.savedFormat); }
function unsaved() { return textDirty() || formatDirty() || S.sessionDirty; }

function updateStatus() {
  const el = ui.status;
  if (S.busy) {
    const secs = Math.round((Date.now() - S.busyStart) / 1000);
    el.textContent = secs >= 2 ? `${S.busy} ${secs} s` : S.busy;
    el.className = 'status busy';
  } else if (S.page === null) {
    el.textContent = '';
    el.className = 'status';
  } else if (unsaved()) {
    el.textContent = 'não salvo';
    el.className = 'status unsaved';
  } else {
    el.textContent = 'salvo';
    el.className = 'status saved';
  }
  // aviso do conflito: recarregar descarta também o que está só na memória da sessão
  ui.conflictMemory.hidden = !S.sessionDirty;
}

function showMessage(text, kind) {
  ui.message.textContent = text || '';
  ui.message.className = 'message' + (kind ? ' ' + kind : '');
}
function showError(e) { showMessage(e && e.message ? e.message : String(e), 'error'); }

function updateControls() {
  const busy = Boolean(S.busy);
  const none = S.page === null;
  for (const b of [ui.save, ui.renderPage, ui.renderAll, ui.justifyAll, ui.pageSel]) b.disabled = busy || none;
  ui.prev.disabled = busy || none || S.i <= 0;
  ui.next.disabled = busy || none || S.i >= S.pages.length - 1;
  for (const b of [ui.leaveSave, ui.leaveDiscard, ui.leaveStay, ui.reload]) b.disabled = busy;
}

/** Executa uma chamada ao servidor com os botões desabilitados e o estado na faixa. */
async function run(label, fn) {
  if (S.busy) return false;
  S.busy = label;
  S.busyStart = Date.now();
  showMessage('');
  updateControls();
  updateStatus();
  S.timer = setInterval(updateStatus, 1000);
  try {
    await fn();
    return true;
  } catch (e) {
    if (e instanceof ApiError && e.conflict) {   // a faixa diz o motivo (mudou ou sumiu) e oferece recarregar
      const msg = e.message;
      ui.conflictReason.textContent = msg.charAt(0).toUpperCase() + msg.slice(1) + (msg.endsWith('.') ? '' : '.');
      ui.conflict.hidden = false;
    } else {
      showError(e);
    }
    return false;
  } finally {
    clearInterval(S.timer);
    S.busy = null;
    updateControls();
    updateStatus();
  }
}

// --- texto, numeração de linhas e bloco do cursor ------------------------------------------

function onTextChanged() {
  const text = ui.ta.value;
  S.info = analyzeText(text);
  S.canon = canonical(text, S.info.data);
  S.starts = [0];
  for (let i = text.indexOf('\n'); i >= 0; i = text.indexOf('\n', i + 1)) S.starts.push(i + 1);
  buildGutter();
  updateCursor(true);
  updateStatus();
}

/** Linha (0-based) de um offset, por busca binária em S.starts. */
function lineIndex(offset) {
  let lo = 0, hi = S.starts.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (S.starts[mid] <= offset) lo = mid; else hi = mid - 1;
  }
  return lo;
}

/** Numeração: mede no espelho a altura de cada linha (com a quebra do textarea) e replica na margem. */
function buildGutter() {
  const ta = ui.ta;
  const cs = getComputedStyle(ta);
  const width = ta.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  const lines = ta.value.split('\n');
  ui.mirror.style.width = Math.max(10, width) + 'px';
  const probe = document.createDocumentFragment();
  for (const line of lines) {
    const d = document.createElement('div');
    d.textContent = line || '\u200b';
    probe.appendChild(d);
  }
  ui.mirror.replaceChildren(probe);
  const heights = Array.from(ui.mirror.children, (d) => d.getBoundingClientRect().height);
  ui.mirror.replaceChildren();
  const nums = document.createDocumentFragment();
  S.tops = [];
  let top = 0;
  heights.forEach((h, k) => {
    S.tops.push(top);
    top += h;
    const d = document.createElement('div');
    d.style.height = h + 'px';
    d.textContent = String(k + 1);
    nums.appendChild(d);
  });
  ui.gutter.replaceChildren(nums);
  markGutter();
  syncGutterScroll();
}

function syncGutterScroll() {
  ui.gutter.style.transform = `translateY(${-ui.ta.scrollTop}px)`;
}

function markGutter() {
  const rows = ui.gutter.children;
  for (const el of ui.gutter.querySelectorAll('.blk, .err')) el.classList.remove('blk', 'err');
  const r = S.info.ranges && S.cur >= 0 ? S.info.ranges[S.cur] : null;
  if (r) {
    const last = lineIndex(r[1] - 1);
    for (let k = lineIndex(r[0]); k <= last && k < rows.length; k++) rows[k].classList.add('blk');
  }
  const err = S.info.error;
  if (err && err.line && rows[err.line - 1]) rows[err.line - 1].classList.add('err');
}

/** Recalcula o bloco do cursor; `force` redesenha mesmo sem mudança (o texto mudou). */
function updateCursor(force) {
  const cur = S.info.data ? blockAt(ui.ta.value, S.info.ranges, ui.ta.selectionStart) : -1;
  if (cur === S.cur && !force) return;
  const moved = cur !== S.cur;
  S.cur = cur;
  markGutter();
  renderBlockBar();
  renderBoxes();
  if (moved) revealBox();
}

/** Troca todo o texto preservando o desfazer (Ctrl+Z) quando o navegador permite. */
function replaceText(text, caret) {
  const ta = ui.ta;
  const top = ta.scrollTop;
  ta.focus({ preventScroll: true });
  ta.setSelectionRange(0, ta.value.length);
  let ok = false;
  try { ok = document.execCommand('insertText', false, text); } catch (e) { ok = false; }
  if (!ok || ta.value !== text) ta.value = text;
  ta.setSelectionRange(caret, caret);
  ta.scrollTop = top;
  onTextChanged();
  ta.scrollTop = top;
  syncGutterScroll();
}

/** Posição equivalente no bloco `n` de `newText` (mesmo deslocamento dentro do bloco). */
function caretInBlock(newText, n, oldRange, oldCaret) {
  const ranges = scanBlocks(newText);
  if (!ranges || !ranges[n]) return Math.min(oldCaret, newText.length);
  const rel = oldRange ? oldCaret - oldRange[0] : 0;
  const [s, e] = ranges[n];
  return s + Math.max(0, Math.min(rel, e - s - 1));
}

/** Ação da barra do bloco: parse, altera o objeto n, stringify, cursor de volta no bloco n. */
function blockAction(action, value, n = S.cur) {
  const { data, ranges } = S.info;
  if (!data || n < 0 || n >= data.length) return;
  const arr = JSON.parse(ui.ta.value);
  applyAction(arr[n], action, value);
  const text = JSON.stringify(arr, null, 2);
  replaceText(text, caretInBlock(text, n, ranges[n], ui.ta.selectionStart));
}

/** Leva o cursor ao bloco n (clique numa caixa do Original). */
function moveToBlock(n) {
  const ta = ui.ta;
  ta.focus({ preventScroll: true });   // antes de ler os intervalos: o foco pode aplicar o campo Tamanho
  const r = S.info.ranges && S.info.ranges[n];
  if (!r) return;
  ta.setSelectionRange(r[0] + 1, r[0] + 1);
  ta.scrollTop = Math.max(0, (S.tops[lineIndex(r[0])] || 0) - 24);
  syncGutterScroll();
  updateCursor(true);
}

// --- barra do bloco ----------------------------------------------------------------------

function currentScale() {
  const f = formatState();
  return f.format ? outputScale(f.format.font_size_pt, S.bodyFontPt) : null;
}

/** Tamanho do bloco n na escala da saída (pt), ou null se desconhecido. */
function outputSize(b, n, scale) {
  const own = styleOf(b).font_size_pt;
  const measured = typeof own === 'number' ? own : S.measured[n];
  return typeof measured === 'number' && scale ? round1(measured * scale) : null;
}

function renderBlockBar() {
  const { data, error } = S.info;
  const n = S.cur;
  const b = data && n >= 0 ? data[n] : null;
  const actions = [ui.bBold, ui.bItalic, ui.bJustify, ui.bBreak, ui.bLess, ui.bMore, ui.bSplit, ui.bHide];
  const label = ui.blockLabel;
  label.className = 'block-label';
  if (!b) {
    for (const el of actions) el.disabled = true;
    ui.bSize.disabled = true;
    for (const el of [ui.bBold, ui.bItalic, ui.bBreak, ui.bHide]) el.setAttribute('aria-pressed', 'false');
    ui.bLines.textContent = '0';
    ui.bHide.textContent = 'Ocultar bloco';
    if (document.activeElement !== ui.bSize) {
      ui.bSize.value = S.sizeShown = '';
      S.sizeFor = -1;
    }
    if (S.page === null) {
      label.textContent = 'bloco –';
    } else if (error) {
      const where = error.line ? ` na linha ${error.line}` + (error.col ? `, coluna ${error.col}` : '') : '';
      label.textContent = `JSON inválido${where}: ${error.message}`;
      label.className = 'block-label error';
    } else {
      label.textContent = 'cursor fora de um bloco';
    }
    return;
  }
  const kind = String(b.kind);
  const textual = TEXT_KINDS.includes(kind);
  const hidden = isHidden(b);
  label.textContent = `bloco ${n + 1} (${KIND_NAMES[kind] || kind}${hidden ? ', oculto' : ''})`;
  if (data.length !== S.blocks.length) {
    const warn = document.createElement('span');
    warn.className = 'warn';
    warn.textContent = ` — a página tem ${S.blocks.length} blocos e o texto tem ${data.length}: não grava`;
    label.appendChild(warn);
  }
  for (const el of actions) el.disabled = false;
  ui.bBold.disabled = ui.bItalic.disabled = ui.bSplit.disabled = !textual;
  ui.bBold.setAttribute('aria-pressed', String(textual && isBold(b)));
  ui.bItalic.setAttribute('aria-pressed', String(textual && isItalic(b)));
  ui.bJustify.disabled = !textual || isJustified(b);
  ui.bJustify.title = !textual ? 'só para blocos de texto' : isJustified(b) ? 'o bloco já está justificado' : '';
  ui.bBreak.setAttribute('aria-pressed', String(hasBreak(b)));
  ui.bLines.textContent = String(linesBefore(b));
  ui.bLess.disabled = linesBefore(b) <= 0;   // os botões só põem/tiram linhas em branco; negativo, no JSON
  ui.bHide.textContent = hidden ? 'Mostrar bloco' : 'Ocultar bloco';
  ui.bHide.setAttribute('aria-pressed', String(hidden));
  const scale = currentScale();
  ui.bSize.disabled = kind === 'figure' || !scale;
  ui.bSize.title = !scale ? 'corrija a faixa de formato' : kind === 'figure' ? 'figuras não têm tamanho de fonte' : '';
  if (document.activeElement !== ui.bSize) {
    S.sizeFor = n;
    const size = outputSize(b, n, scale);
    ui.bSize.value = S.sizeShown = size === null ? '' : String(size);
    ui.bSize.placeholder = size === null ? 'padrão' : '';
  }
}

let applyingSize = false;   // a troca de foco no meio da aplicação dispara outro change

/** Aplica o campo Tamanho ao bloco; false se o valor é inválido (a mensagem fica na faixa). */
function applySize() {
  const n = S.sizeFor;
  const scale = currentScale();
  const data = S.info.data;
  if (applyingSize || n < 0 || !data || n >= data.length || !scale) return true;
  const v = parseNum(ui.bSize.value);
  if (v !== null && !(Number.isFinite(v) && v > 0)) {
    showMessage('tamanho inválido: um número positivo em pt (vazio volta ao padrão)', 'error');
    return false;
  }
  const measured = v === null ? null : round1(v / scale);
  if (measured !== null && measured <= 0) {
    showMessage('tamanho pequeno demais', 'error');
    return false;
  }
  const own = styleOf(data[n]).font_size_pt;
  if ((typeof own === 'number' ? own : null) === measured) return true;   // nada mudou
  applyingSize = true;
  try {
    blockAction('size', measured, n);
  } finally {
    applyingSize = false;
  }
  return true;
}

/** Ctrl+S / Ctrl+Enter com o campo Tamanho em edição: o change só vem com Enter ou ao sair do campo. */
function applyPendingSize() {
  return document.activeElement !== ui.bSize || ui.bSize.value === S.sizeShown || applySize();
}

// --- aba Original --------------------------------------------------------------------------

function renderBoxes() {
  if (!S.hasOriginal || S.page === null) return;
  const data = S.info.data;
  const frag = document.createDocumentFragment();
  let last = null;   // caixa anterior: blocos divididos têm a mesma caixa e ganham um rótulo só
  S.blocks.forEach((row, k) => {
    if (!row.box) return;
    if (last && String(last.box) === String(row.box)) {
      last.num.textContent = `${last.first + 1}–${k + 1}`;
      if (k === S.cur) last.el.classList.add('cur');
      return;
    }
    const [x1, y1, x2, y2] = row.box;
    const hidden = data && data[k] ? isHidden(data[k]) : row.hidden;
    const d = document.createElement('div');
    d.className = 'box' + (hidden ? ' hidden' : '') + (k === S.cur ? ' cur' : '');
    d.style.left = x1 * 100 + '%';
    d.style.top = y1 * 100 + '%';
    d.style.width = (x2 - x1) * 100 + '%';
    d.style.height = (y2 - y1) * 100 + '%';
    d.title = `bloco ${k + 1} (${KIND_NAMES[row.kind] || row.kind})${hidden ? ', oculto' : ''}`;
    const num = document.createElement('span');
    num.className = 'num';
    num.textContent = String(k + 1);
    d.appendChild(num);
    d.addEventListener('mousedown', (e) => e.preventDefault());   // não tira o foco do textarea
    d.addEventListener('click', () => moveToBlock(k));
    frag.appendChild(d);
    last = { box: row.box, el: d, num, first: k };
  });
  ui.origBoxes.replaceChildren(frag);
}

/** Rola a aba Original até a caixa do bloco do cursor (só se ela estiver fora de vista). */
function revealBox() {
  if (ui.paneOriginal.hidden) return;
  const box = ui.origBoxes.querySelector('.box.cur');
  if (box) box.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

function loadOriginal() {
  if (!S.hasOriginal) {
    ui.origInfo.textContent = 'Sem o PDF original: abra o editor com --pdf <original.pdf> para ver a página escaneada.';
    ui.origWrap.hidden = true;
    return;
  }
  ui.origInfo.textContent = 'Clique num bloco para levar o cursor até ele. Tracejado e riscado = oculto.';
  ui.origWrap.hidden = false;
  ui.origWrap.style.maxWidth = '';
  ui.origImg.src = `/original/${S.page}.png`;
}

// --- aba Resultado -------------------------------------------------------------------------

function clearResults() {
  ui.resultInfo.textContent = 'Nada renderizado para esta página: use Renderizar esta página (prévia) ou Renderizar tudo (PDF final).';
  ui.resultPages.replaceChildren();
}

function showResults(images, captions) {
  const frag = document.createDocumentFragment();
  images.forEach((url, k) => {
    const fig = document.createElement('figure');
    const img = document.createElement('img');
    img.src = url;
    img.alt = captions[k];
    const cap = document.createElement('figcaption');
    cap.textContent = captions[k];
    fig.append(img, cap);
    frag.appendChild(fig);
  });
  ui.resultPages.replaceChildren(frag);
  selectTab('result');
}

function selectTab(name) {
  const result = name === 'result';
  ui.tabResult.setAttribute('aria-selected', String(result));
  ui.tabOriginal.setAttribute('aria-selected', String(!result));
  ui.paneResult.hidden = !result;
  ui.paneOriginal.hidden = result;
  if (!result) revealBox();
}

// --- carregar, salvar, renderizar ----------------------------------------------------------

/**
 * Carrega a página i (índice em S.pages). Com `keepView` (depois de salvar/justificar), mantém a
 * rolagem e o cursor e só troca o texto se o usuário não digitou desde `sentText`.
 */
async function loadPage(i, keepView = false, sentText = null) {
  const page = S.pages[i];
  const [st, pg] = await Promise.all([api('GET', '/api/state'), api('GET', `/api/page?n=${page}`)]);
  const newPage = page !== S.page;
  const loaded = JSON.parse(pg.text);
  const scale = outputScale(st.format.font_size_pt, S.bodyFontPt);   // escala em que veio o font_pt
  S.i = i;
  S.page = page;
  S.blocks = pg.blocks;
  S.measured = pg.blocks.map((row, k) => {
    const own = loaded[k] ? styleOf(loaded[k]).font_size_pt : undefined;
    return typeof own === 'number' ? null : row.font_pt / scale;
  });
  S.loadedText = pg.text;
  S.loadedCanon = JSON.stringify(loaded);
  S.sessionTouched = false;
  const ta = ui.ta;
  if (!keepView) {
    ta.value = pg.text;
    ta.setSelectionRange(0, 0);
    ta.scrollTop = 0;
  } else if (ta.value === sentText && S.canon !== S.loadedCanon) {
    const top = ta.scrollTop;
    const n = S.cur;
    const caret = n >= 0 ? caretInBlock(pg.text, n, S.info.ranges[n], ta.selectionStart) : 0;
    ta.value = pg.text;
    ta.setSelectionRange(caret, caret);
    ta.scrollTop = top;
  }
  onTextChanged();
  if (newPage) {
    ui.pageSel.value = String(i);
    clearResults();
    loadOriginal();
    history.replaceState(null, '', '#' + (page + 1));
  }
  updateControls();
}

/** Formato da faixa, ou null (mostra o erro). */
function formatOrError() {
  const f = formatState();
  if (!f.format) {
    showMessage(f.error, 'error');
    return null;
  }
  return f.format;
}

/** Depois de gravar `text` com `fmt`: a base do "não salvo" passa a ser o que está no disco. */
async function afterSaved(text, fmt) {
  S.savedFormat = fmt;
  S.sessionDirty = false;
  ui.conflict.hidden = true;
  await loadPage(S.i, true, text);
}

async function doSave() {
  const fmt = formatOrError();
  if (!fmt) throw new ApiError(0, formatState().error);
  const text = ui.ta.value;
  S.sessionTouched = true;
  await api('POST', '/api/save', { page: S.page, text, format: fmt });
  await afterSaved(text, fmt);
}

function save() { return run('salvando…', doSave); }

async function renderPage() {
  const fmt = formatOrError();
  if (!fmt) return;
  const text = ui.ta.value;
  const hl = S.cur >= 0 && S.cur < S.blocks.length ? S.blocks[S.cur].index : null;
  const hlN = S.cur + 1;
  await run('renderizando…', async () => {
    S.sessionTouched = true;   // o servidor aplica o texto à sessão (em memória)
    const r = await api('POST', '/api/render/page', { page: S.page, text, format: fmt, highlight: hl });
    ui.resultInfo.textContent = 'Prévia desta página (sem salvar). É aproximada: a quebra de página '
      + 'pode diferir do PDF final.' + (hl === null ? '' : ` O bloco ${hlN} (cursor) sai contornado em laranja.`);
    showResults(r.images, r.images.map((_, k) => `prévia ${k + 1} de ${r.images.length}`));
  });
}

async function renderAll() {
  const fmt = formatOrError();
  if (!fmt) return;
  const text = ui.ta.value;
  await run('renderizando tudo…', async () => {
    S.sessionTouched = true;
    let r;
    try {
      r = await api('POST', '/api/render/all', { page: S.page, text, format: fmt });
    } catch (e) {
      // 500 depois de gravar: a sessão limpa indica que a gravação aconteceu
      if (e.status === 500) {
        const st = await api('GET', '/api/state').catch(() => null);
        if (st && !st.dirty) await afterSaved(text, fmt).catch(() => {});
      }
      throw e;
    }
    await afterSaved(text, fmt);
    const finals = r.pages.map((p) => p + 1);
    // as páginas vêm em sequência: da do 1º bloco até a que tem o começo do bloco seguinte
    const where = finals.length > 1 ? `nas páginas ${finals[0]} a ${finals[finals.length - 1]}` : `na página ${finals[0]}`;
    ui.resultInfo.replaceChildren(
      'Salvo e PDF gravado em ', Object.assign(document.createElement('code'), { textContent: r.pdf }), '. ',
      finals.length
        ? `Os blocos desta página estão ${where} do PDF final (a última pode trazer o começo da página seguinte do original).`
        : 'Nenhum bloco visível desta página no PDF final.',
    );
    showResults(r.images, finals.map((p) => `página ${p} do PDF final`));
  });
}

async function justifyAll() {
  await run('justificando…', async () => {
    const text = ui.ta.value;
    let pushed = false;
    if (textDirty()) {   // leva antes as edições da tela para a sessão, para não perdê-las
      await api('POST', `/api/page?n=${S.page}`, { text });
      pushed = true;
      S.sessionTouched = true;   // "Descartar" desfaz o envio mesmo se o justify-all falhar
    }
    const r = await api('POST', '/api/justify-all', {});
    if (r.changed || pushed) S.sessionDirty = true;
    await loadPage(S.i, true, text);
    showMessage(r.changed
      ? `${r.changed} parágrafo(s) justificado(s) em todo o documento; falta salvar.`
      : 'Todos os parágrafos já estavam justificados.', 'ok');
  });
}

/** Divide o bloco do cursor em dois (no servidor, como o Justificar todos); o cursor vai ao bloco novo. */
async function splitBlock() {
  const ta = ui.ta;
  const at = ta.selectionStart;
  const text = ta.value.slice(0, at) + SPLIT_MARK + ta.value.slice(at);
  let inText = false;
  try {
    inText = JSON.parse(text).some((b) => b && TEXT_KINDS.includes(b.kind) && Array.isArray(b.spans)
      && b.spans.some((s) => s && typeof s.text === 'string' && s.text.includes(SPLIT_MARK)));
  } catch (e) { inText = false; }
  if (!inText) {
    showMessage('Para dividir, ponha o cursor dentro do texto ("text") do bloco, no ponto da divisão.', 'error');
    return;
  }
  await run('dividindo…', async () => {
    const r = await api('POST', `/api/split?n=${S.page}`, { text });
    S.sessionDirty = true;
    await loadPage(S.i, true, ta.value);
    moveToBlock(r.block);
    showMessage(`Bloco ${r.block} dividido em ${r.block} e ${r.block + 1}; falta salvar.`, 'ok');
  });
}

/**
 * Saída do conflito (o document.json mudou no disco): o servidor relê o arquivo e descarta a memória.
 * O texto e o formato da tela ficam como estão, "não salvos"; o próximo Salvar os grava sobre o arquivo novo.
 */
async function reloadFromDisk() {
  await run('recarregando…', async () => {
    const memory = S.sessionDirty;   // o setState abaixo o zera
    const lost = memory
      ? ' As alterações em memória de outras páginas e do "Justificar todos" foram descartadas.' : '';
    const st = await api('POST', '/api/reload', {});
    ui.conflict.hidden = true;
    clearResults();   // as imagens da renderização anterior foram descartadas no servidor
    const gone = S.page;
    setState(st);
    if (!S.pages.length) throw new ApiError(0, 'o document.json recarregado não tem blocos');
    const i = S.pages.indexOf(gone);
    if (i >= 0) {
      await loadPage(i, true, null);   // sentText null: o texto da tela nunca é trocado
      ui.pageSel.value = String(i);
      showMessage((textDirty() || formatDirty()
        ? 'Recarregado do disco. O texto desta página continua na tela, não salvo: Salvar o grava sobre o conteúdo novo.'
        : 'Recarregado do disco; esta página não tinha alterações.') + lost, 'ok');
    } else {
      const next = S.pages.findIndex((p) => p > gone);
      await loadPage(next >= 0 ? next : S.pages.length - 1);
      showMessage(`Recarregado do disco, mas a página ${gone + 1} não existe mais nele: o texto dela foi descartado.${lost}`, 'error');
    }
  });
}

// --- navegação -----------------------------------------------------------------------------

/** Sai da página atual sem gravar: desfaz na sessão o que "Renderizar" tenha aplicado dela. */
async function leaveTo(i) {
  if (S.sessionTouched) await api('POST', `/api/page?n=${S.page}`, { text: S.loadedText });
  await loadPage(i);
}

function requestPage(i) {
  ui.pageSel.value = String(S.i);
  if (S.busy || i === S.i || i < 0 || i >= S.pages.length) return;
  if (textDirty()) {
    openLeave(i);
    return;
  }
  run('carregando…', () => leaveTo(i));
}

function openLeave(i) {
  S.leaveTarget = i;
  ui.leaveText.textContent = `A página ${S.page + 1} tem alterações não salvas. `
    + `Salvar antes de ir para a página ${S.pages[i] + 1}?`;
  ui.leave.hidden = false;
  ui.leaveSave.focus();
}

function closeLeave() {
  ui.leave.hidden = true;
  S.leaveTarget = -1;
  ui.ta.focus({ preventScroll: true });
}

// --- início --------------------------------------------------------------------------------

/** Estado do documento vindo de /api/state ou /api/reload: páginas, corpo medido, formato gravado. */
function setState(st) {
  S.pages = st.pages;
  S.bodyFontPt = st.body_font_pt;
  S.hasOriginal = st.has_original;
  S.savedFormat = st.format;
  S.sessionDirty = st.dirty;
  ui.pageSel.replaceChildren(...S.pages.map((p, i) => new Option(String(p + 1), String(i))));
  ui.pageCount.textContent = S.pages.length ? String(S.pages[S.pages.length - 1] + 1) : '0';
}

function setFormatBar(fmt) {
  ui.fmtPage.value = fmt.page_size;
  ui.fmtMargin.value = String(fmt.margin_cm);
  ui.fmtBody.value = fmt.font_size_pt === null || fmt.font_size_pt === undefined ? '' : String(fmt.font_size_pt);
  ui.fmtHeader.checked = Boolean(fmt.header);
  onFormatChanged();
}

function onFormatChanged() {
  const f = formatState();
  for (const [field, el] of [['page', ui.fmtPage], ['margin', ui.fmtMargin], ['body', ui.fmtBody]]) {
    el.classList.toggle('invalid', !f.format && f.field === field);
  }
  // a margem só vale quando a página muda de tamanho (esmaecida, mas editável)
  const unused = ui.fmtPage.value.trim().toLowerCase() === 'original';
  ui.fmtMargin.classList.toggle('unused', unused);
  ui.fmtMargin.title = unused ? 'com a página original a margem não é usada (só com a4 ou LxA)' : '';
  renderBlockBar();
  updateStatus();
}

function bind() {
  const $ = (id) => document.getElementById(id);
  Object.assign(ui, {
    prev: $('prev'), next: $('next'), pageSel: $('page-select'), pageCount: $('page-count'),
    save: $('save'), renderPage: $('render-page'), renderAll: $('render-all'), justifyAll: $('justify-all'),
    status: $('status'), message: $('message'),
    fmtPage: $('fmt-page'), fmtMargin: $('fmt-margin'), fmtBody: $('fmt-body'), fmtHeader: $('fmt-header'),
    blockLabel: $('block-label'), bBold: $('b-bold'), bItalic: $('b-italic'), bJustify: $('b-justify'),
    bBreak: $('b-break'), bLess: $('b-less'), bLines: $('b-lines'), bMore: $('b-more'), bSize: $('b-size'),
    bSplit: $('b-split'), bHide: $('b-hide'),
    ta: $('text'), gutter: $('gutter-lines'), mirror: $('mirror'),
    tabResult: $('tab-result'), tabOriginal: $('tab-original'),
    paneResult: $('pane-result'), paneOriginal: $('pane-original'),
    resultInfo: $('result-info'), resultPages: $('result-pages'),
    origInfo: $('orig-info'), origWrap: $('orig-wrap'), origImg: $('orig-img'), origBoxes: $('orig-boxes'),
    leave: $('leave'), leaveText: $('leave-text'), conflict: $('conflict'), reload: $('reload'),
    conflictReason: $('conflict-reason'), conflictMemory: $('conflict-memory'),
    leaveSave: $('leave-save'), leaveDiscard: $('leave-discard'), leaveStay: $('leave-stay'),
  });

  ui.prev.addEventListener('click', () => requestPage(S.i - 1));
  ui.next.addEventListener('click', () => requestPage(S.i + 1));
  ui.pageSel.addEventListener('change', () => requestPage(Number(ui.pageSel.value)));
  ui.save.addEventListener('click', save);
  ui.renderPage.addEventListener('click', renderPage);
  ui.renderAll.addEventListener('click', renderAll);
  ui.justifyAll.addEventListener('click', justifyAll);
  ui.reload.addEventListener('click', reloadFromDisk);

  for (const el of [ui.fmtPage, ui.fmtMargin, ui.fmtBody, ui.fmtHeader]) {
    el.addEventListener('input', onFormatChanged);
    el.addEventListener('change', onFormatChanged);
  }

  const actions = [
    [ui.bBold, 'bold'], [ui.bItalic, 'italic'], [ui.bJustify, 'justify'], [ui.bBreak, 'break'],
    [ui.bLess, 'less'], [ui.bMore, 'more'], [ui.bHide, 'hide'],
  ];
  for (const [el, action] of actions) el.addEventListener('click', () => blockAction(action));
  ui.bSplit.addEventListener('click', splitBlock);
  // aplica no change (Enter ou saída do campo); o Enter só tira o foco, e o blur dispara o change
  ui.bSize.addEventListener('change', applySize);
  ui.bSize.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      ui.bSize.blur();
    } else if (e.key === 'Escape') {
      ui.bSize.value = S.sizeShown;   // desiste: sem change
      ui.bSize.blur();
    }
  });
  ui.bSize.addEventListener('blur', () => setTimeout(renderBlockBar, 0));

  const ta = ui.ta;
  ta.addEventListener('input', onTextChanged);
  ta.addEventListener('scroll', syncGutterScroll);
  for (const ev of ['keyup', 'mouseup', 'focus', 'select']) ta.addEventListener(ev, () => updateCursor(false));
  document.addEventListener('selectionchange', () => {
    if (document.activeElement === ta) updateCursor(false);
  });
  new ResizeObserver(() => buildGutter()).observe(ta);

  ui.tabResult.addEventListener('click', () => selectTab('result'));
  ui.tabOriginal.addEventListener('click', () => selectTab('original'));
  ui.origImg.addEventListener('load', () => {
    ui.origWrap.style.maxWidth = ui.origImg.naturalWidth + 'px';
    renderBoxes();
    revealBox();
  });
  ui.origImg.addEventListener('error', () => {
    if (!ui.origImg.getAttribute('src')) return;
    ui.origInfo.textContent = `Não foi possível carregar a página ${S.page + 1} do original.`;
    ui.origWrap.hidden = true;
  });

  ui.leaveSave.addEventListener('click', () => {
    const target = S.leaveTarget;
    ui.leave.hidden = true;
    run('salvando…', async () => {
      await doSave();
      await loadPage(target);
    }).then((ok) => { if (!ok) ui.ta.focus({ preventScroll: true }); });
  });
  ui.leaveDiscard.addEventListener('click', () => {
    const target = S.leaveTarget;
    ui.leave.hidden = true;
    run('carregando…', () => leaveTo(target));
  });
  ui.leaveStay.addEventListener('click', closeLeave);
  ui.leave.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeLeave(); });

  document.addEventListener('keydown', (e) => {
    if (!ui.leave.hidden) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && (e.key === 's' || e.key === 'S')) {
      e.preventDefault();
      if (!S.busy && S.page !== null && applyPendingSize()) save();
    } else if (mod && e.key === 'Enter') {
      e.preventDefault();
      if (!S.busy && S.page !== null && applyPendingSize()) renderPage();
    }
  });

  window.addEventListener('beforeunload', (e) => {
    if (S.page !== null && unsaved()) {
      e.preventDefault();
      e.returnValue = '';
    }
  });
}

async function start() {
  bind();
  updateControls();
  renderBlockBar();
  let st;
  try {
    st = await api('GET', '/api/state');
  } catch (e) {
    showError(e);
    updateStatus();
    return;
  }
  setState(st);
  document.title = `ocrpdf edit — ${st.title}`;
  setFormatBar(st.format);
  selectTab(S.hasOriginal ? 'original' : 'result');
  if (!S.pages.length) {
    showMessage('o documento não tem blocos', 'error');
    updateStatus();
    return;
  }
  const wanted = S.pages.indexOf(Number(location.hash.slice(1)) - 1);
  await run('carregando…', () => loadPage(Math.max(0, wanted)));
}

if (typeof document !== 'undefined') start();
