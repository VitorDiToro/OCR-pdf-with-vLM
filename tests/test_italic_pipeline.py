"""Etapa 2 do itálico integrada: pipeline (evidência, cache das marcas, trocador), assemble e CLI.

Página sintética com `text_image` (algumas palavras impressas em itálico); o OCR e o 2º modelo são
`FakeEngine`s; o trocador só registra os papéis pedidos.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
import pytest

from ocrpdf import cli
from ocrpdf.assemble import assemble
from ocrpdf.inline import parse_flat
from ocrpdf.italic.words import italic_flags, tokens
from ocrpdf.model import Document, Span
from ocrpdf.ocr.base import PageLayout, Region
from ocrpdf.pipeline import ItalicConfig, Pipeline
from ocrpdf.servers import ITALIC_HF_MODEL, RestartSwitcher, SleepSwitcher
from synth import FakeEngine, bbox_of, benjamin_like_page, flat_words, make_pdf, sample_lines, text_image

# palavras 3 e 4 ("Pequena história") impressas em itálico; as demais, normais
BODY = [[(w, i in (2, 3)) for i, w in enumerate("Benjamin escreveu Pequena história depois daquela viagem longa".split())]]
BODY += sample_lines()[1:3]
TOKS = flat_words(BODY)
_PAD = 4   # folga do bbox do bloco em torno da tinta, como a do dots


def markdown(toks: list[str], italic: set[int]) -> str:
    """Texto do OCR com `*…*` nas palavras de índice em `italic`."""
    return " ".join(f"*{t}*" if i in italic else t for i, t in enumerate(toks))


def mark(toks: list[str], italic: set[int]) -> str:
    """Resposta do 2º modelo: o mesmo texto com <i>…</i> nas palavras de índice em `italic`."""
    return " ".join(f"<i>{t}</i>" if i in italic else t for i, t in enumerate(toks))


def page(*blocks: tuple[str, list[list[tuple[str, bool]]], str]) -> tuple:
    """Desenha os blocos `(categoria, linhas, Markdown do OCR)` um sob o outro (com uma linha em
    branco entre eles); devolve a imagem e as regiões com o bbox de cada bloco."""
    lines, rows = [], []
    for _, block_lines, _ in blocks:
        rows.append(range(len(lines), len(lines) + len(block_lines)))
        lines += block_lines + [[]]
    image, truth = text_image(lines)
    regions = []
    for (category, _, md), rr in zip(blocks, rows):
        x1, y1, x2, y2 = bbox_of([truth[k] for k in rr])
        regions.append(Region(category, (x1 - _PAD, y1 - _PAD, x2 + _PAD, y2 + _PAD), md))
    return image, regions


class RecordingSwitcher:
    def __init__(self) -> None:
        self.roles: list[str] = []
        self.closed = 0

    def use(self, role: str) -> None:
        self.roles.append(role)

    def close(self) -> None:
        self.closed += 1


class FailingOn(RecordingSwitcher):
    """Trocador cujo servidor `role` está fora do ar."""

    def __init__(self, role: str) -> None:
        super().__init__()
        self.role = role

    def use(self, role: str) -> None:
        super().use(role)
        if role == self.role:
            raise RuntimeError("servidor vLLM inacessível; suba o par com scripts/serve_pair.sh")


def _refused():
    raise httpx.ConnectError("conexão recusada")


def _model_not_found():
    # --italic-model diferente do nome servido: o vLLM responde 404 (o VlmEngine repassa após as tentativas)
    req = httpx.Request("POST", "http://127.0.0.1:8001/v1/chat/completions")
    raise httpx.HTTPStatusError("Client error '404 Not Found'", request=req,
                                response=httpx.Response(404, request=req))


def _malformed():
    raise KeyError("choices")


def _not_text():
    return {"choices": []}


class BrokenEngine(FakeEngine):
    """2º modelo que falha em toda chamada do jeito dado por `failure`."""

    def __init__(self, failure) -> None:
        super().__init__([])
        self.failure = failure

    def ask(self, image, question, max_tokens=128):
        super().ask(image, question, max_tokens)
        return self.failure()


class CropRecorder(FakeEngine):
    """2º modelo que guarda o tamanho dos recortes recebidos."""

    def __init__(self, answers: list[str]) -> None:
        super().__init__([], answers=answers)
        self.sizes: list[tuple[int, int]] = []

    def ask(self, image, question, max_tokens=128):
        self.sizes.append(image.size)
        return super().ask(image, question, max_tokens)


@dataclass
class Run:
    doc: Document
    ocr: FakeEngine
    marker: FakeEngine


def run(tmp_path, answers=(), dots=(), rule="r2", switcher=None, force=False, blocks=None,
        italic=True, marker=None, dpi=200) -> Run:
    image, regions = page(*(blocks or [("text", BODY, markdown(TOKS, set(dots)))]))
    pdf = make_pdf(tmp_path / "in.pdf", [image])
    ocr = FakeEngine([regions])
    marker = marker or FakeEngine([], answers=list(answers))
    config = ItalicConfig(marker, "modelo-falso", rule=rule, workers=1) if italic else None
    doc = Pipeline(ocr, tmp_path / "w", dpi=dpi, workers=1, italic=config,
                   switcher=switcher).analyze(pdf, font="sans", force=force)
    return Run(doc, ocr, marker)


def italic_words(spans: list[Span]) -> list[str]:
    return [t for t, f in zip(tokens(spans), italic_flags(spans)) if f]


def doc_italics(doc: Document) -> set[str]:
    return {t for b in doc.blocks for t in italic_words(b.spans)}


# --- pipeline -----------------------------------------------------------------

def test_model_marks_need_slant_confirmation(tmp_path):
    # o OCR não marcou nada; o 2º modelo marca as itálicas (3 e 4) e também a 7, impressa normal
    r = run(tmp_path, answers=[mark(TOKS, {2, 3, 6})], rule="r2")
    assert doc_italics(r.doc) == {TOKS[2], TOKS[3]}
    assert r.marker.asked == 1


def test_r1_drops_dots_italic_not_repeated_by_model(tmp_path):
    r = run(tmp_path, dots={2, 3}, answers=[mark(TOKS, {3})], rule="r1")
    assert doc_italics(r.doc) == {TOKS[3]}


def test_r2_keeps_dots_italic_confirmed_by_slant(tmp_path):
    # o dots marca 3, 4 e 7 (impressa normal); o 2º modelo, só a 4
    r = run(tmp_path, dots={2, 3, 6}, answers=[mark(TOKS, {3})], rule="r2")
    assert doc_italics(r.doc) == {TOKS[2], TOKS[3]}


def test_switch_order_and_cache(tmp_path):
    sw = RecordingSwitcher()
    run(tmp_path, answers=[mark(TOKS, {2, 3})], switcher=sw)
    assert sw.roles == ["ocr", "italic"] and sw.closed == 1
    r = run(tmp_path, switcher=sw)                    # OCR e marcas em cache: nenhum servidor
    assert r.marker.asked == 0 and r.ocr.calls == 0 and sw.roles == ["ocr", "italic"]
    assert doc_italics(r.doc) == {TOKS[2], TOKS[3]}   # as marcas vieram do cache
    r = run(tmp_path, answers=[mark(TOKS, {2, 3})], switcher=sw, force=True)
    assert r.marker.asked == 1 and r.ocr.calls == 1
    assert sw.roles == ["ocr", "italic", "ocr", "italic"]


def test_italic_server_down_keeps_ocr_cache(tmp_path):
    sw = FailingOn("italic")
    with pytest.raises(RuntimeError, match="serve_pair.sh"):
        run(tmp_path, switcher=sw)
    assert sw.closed == 1                             # o trocador é fechado mesmo com o erro
    r = run(tmp_path, answers=[mark(TOKS, {2, 3})])
    assert r.ocr.calls == 0 and r.marker.asked == 1   # não refez o OCR; pediu as marcas


@pytest.mark.parametrize("failure,name", [(_refused, "ConnectError"), (_model_not_found, "HTTPStatusError"),
                                          (_malformed, "KeyError"), (_not_text, "TypeError")])
def test_marker_failure_is_an_error_and_not_cached(tmp_path, failure, name):
    # só a resposta recebida que altera o texto vai para o cache; qualquer falha para a execução
    with pytest.raises(RuntimeError, match=rf"modelo-falso.*{name}.*serve_pair.sh"):
        run(tmp_path, marker=BrokenEngine(failure))
    r = run(tmp_path, answers=[mark(TOKS, {2, 3})])
    assert r.ocr.calls == 0 and r.marker.asked == 1   # a falha não foi para o cache
    assert doc_italics(r.doc) == {TOKS[2], TOKS[3]}


def test_rejected_answer_is_cached(tmp_path):
    r = run(tmp_path, answers=[" ".join(TOKS).replace("viagem", "viajem")])   # o modelo "corrigiu"
    assert r.marker.asked == 1 and doc_italics(r.doc) == set()                 # vale o dots
    assert run(tmp_path).marker.asked == 0


@pytest.mark.parametrize("field,value", [("model", [True]), ("tokens", "outro texto")])
def test_stale_or_malformed_cached_marks_are_requested_again(tmp_path, field, value):
    run(tmp_path, answers=[mark(TOKS, {2, 3})])
    path = tmp_path / "w" / "italic" / "page_0001.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["blocks"]["0"][field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    r = run(tmp_path, answers=[mark(TOKS, {2, 3})])
    assert r.marker.asked == 1 and doc_italics(r.doc) == {TOKS[2], TOKS[3]}


def test_collect_evidence_uses_only_cached_marks(tmp_path):
    run(tmp_path, answers=[mark(TOKS, {2, 3, 6})])
    marker, sw = FakeEngine([]), RecordingSwitcher()
    pipeline = Pipeline(FakeEngine([]), tmp_path / "w", workers=1,
                        italic=ItalicConfig(marker, "modelo-falso"), switcher=sw)
    evidence = pipeline.collect_evidence(tmp_path / "in.pdf")
    assert list(evidence) == [(0, 0)]
    ev = evidence[(0, 0)]
    assert ev.tokens == TOKS and ev.model == [i in (2, 3, 6) for i in range(len(TOKS))]
    assert ev.slant[2] > 7 and ev.slant[6] < 2
    assert marker.asked == 0 and sw.roles == []
    other = Pipeline(FakeEngine([]), tmp_path / "w", workers=1, italic=ItalicConfig(marker, "outro"))
    assert other.collect_evidence(tmp_path / "in.pdf", pages="1")[(0, 0)].model is None


@pytest.mark.parametrize("dpi", [200, 100])
def test_crop_is_the_padded_region_at_ocr_resolution(tmp_path, dpi):
    run(tmp_path, italic=False)                       # OCR em cache a 200 dpi
    marker = CropRecorder([mark(TOKS, {2, 3})])
    r = run(tmp_path, marker=marker, dpi=dpi)         # a 100 dpi a página é ampliada à do cache
    x1, y1, x2, y2 = page(("text", BODY, ""))[1][0].bbox
    (w, h), = marker.sizes
    assert abs(w - (x2 - x1 + 16)) <= 1 and abs(h - (y2 - y1 + 16)) <= 1
    assert doc_italics(r.doc) == {TOKS[2], TOKS[3]}


NOTE1 = [[(w, w in ("Pequena", "história")) for w in "¹ Ver o ensaio Pequena história sobre fotografia.".split()]]
NOTE2 = [[(w, w in ("A", "idéia", "do", "cinema"))
          for w in "² O texto foi publicado em A idéia do cinema (Rio de Janeiro, 1969).".split()]]
NOTES_MD = ("¹ Ver o ensaio *Pequena história* sobre fotografia.\n"
            "² O texto foi publicado em *A idéia do cinema* (Rio de Janeiro, 1969).")


def test_footnote_region_with_label_keeps_alignment(tmp_path):
    # uma região com duas notas (rótulos "¹" e "²" são tokens); o 2º modelo repete as marcas do dots
    spans = parse_flat(NOTES_MD)
    toks, dots = tokens(spans), {i for i, f in enumerate(italic_flags(spans)) if f}
    assert toks[0] == "1" and len(dots) == 6
    r = run(tmp_path, blocks=[("footnote", NOTE1 + NOTE2, NOTES_MD)], answers=[mark(toks, dots)])
    assert r.marker.asked == 1
    n1, n2 = r.doc.notes.values()
    assert (n1.label, n2.label) == ("1", "2")
    assert italic_words(n1.blocks[0].spans) == ["Pequena", "história"]
    # "A" é curta, sem medida e tem vizinha normal à esquerda: fica normal (precisão > cobertura);
    # um cursor desalinhado deslocaria as marcas ("A idéia do" ou "do cinema (Rio")
    assert italic_words(n2.blocks[0].spans) == ["idéia", "do", "cinema"]


@pytest.mark.parametrize("sent", [False, True])
def test_blocks_without_model_marks_keep_ocr_spans(tmp_path, sent):
    # itálico parcial de palavra ("*Aura*s") não cabe nas marcas por token: sem marcas do 2º modelo
    # (bloco não suspeito, ou suspeito com resposta rejeitada) os spans do OCR ficam intactos
    words = "Benjamin discute Auras perdidas na Pequena história recente".split()
    lines = [[(w, sent and w == "Pequena") for w in words]]    # "Pequena" inclinada → bloco suspeito
    md = "Benjamin discute *Aura*s perdidas na Pequena história recente"
    r = run(tmp_path, blocks=[("text", lines, md)], answers=["texto alterado pelo modelo"])
    assert r.marker.asked == (1 if sent else 0)
    (block,) = r.doc.blocks
    assert [(s.text, s.italic) for s in block.spans] == [
        ("Benjamin discute ", False), ("Aura", True), ("s perdidas na Pequena história recente", False)]


def test_block_without_slant_evidence_keeps_ocr_spans(tmp_path):
    # bbox numa área em branco: nenhuma palavra casa com a imagem e toda inclinação fica sem medida;
    # mesmo com as marcas do 2º modelo iguais às do dots, a fusão descartaria todo o itálico
    md = "Benjamin discute *Aura*s e *Pequena história* hoje"
    spans = parse_flat(md)
    toks, dots = tokens(spans), {i for i, f in enumerate(italic_flags(spans)) if f}
    image, _ = page(("text", BODY, ""))
    pdf = make_pdf(tmp_path / "in.pdf", [image])
    marker = FakeEngine([], answers=[mark(toks, dots)])
    pipeline = Pipeline(FakeEngine([[Region("text", (151, 600, 1547, 700), md)]]), tmp_path / "w",
                        workers=1, italic=ItalicConfig(marker, "modelo-falso"))
    doc = pipeline.analyze(pdf, font="sans")
    ev = pipeline.collect_evidence(pdf)[(0, 0)]
    assert marker.asked == 1 and ev.model == [i in dots for i in range(len(toks))]
    assert all(s is None for s in ev.slant)
    (block,) = doc.blocks
    assert [(s.text, s.italic) for s in block.spans] == [(s.text, s.italic) for s in spans]


def test_no_italic_never_calls_marker(tmp_path):
    sw = RecordingSwitcher()
    r = run(tmp_path, dots={6}, italic=False, switcher=sw)
    assert r.marker.asked == 0 and sw.roles == ["ocr"] and sw.closed == 1
    assert doc_italics(r.doc) == {TOKS[6]}           # itálico do dots, sem confirmação
    assert not (tmp_path / "w" / "italic").exists()


# --- assemble -----------------------------------------------------------------

def _layout(*regions: Region) -> PageLayout:
    return PageLayout(0, 1700, 2200, 612.0, 792.0, list(regions))


def test_assemble_applies_marks_with_a_cursor_on_every_path():
    regions = [
        Region("title", (151, 100, 900, 150), "Um *título* aqui"),
        Region("text", (151, 200, 1547, 300), "um dois\n\ntrês quatro"),
        Region("list-item", (151, 320, 1547, 350), "- item longo"),
        Region("caption", (151, 360, 1547, 390), "Figura de exemplo"),
        Region("footnote", (151, 2000, 1547, 2100), "¹ Nota um.\n² Nota dois."),
    ]
    italic = {(0, 0): [False, True, True], (0, 1): [False, True, True, False],
              (0, 2): [False, True, False], (0, 3): [True, False, False],
              (0, 4): [False, False, True, False, False, True]}
    doc = assemble([_layout(*regions)], italic=italic)
    blocks = {b.kind: [] for b in doc.blocks}
    for b in doc.blocks:
        blocks[b.kind].append(italic_words(b.spans))
    assert blocks["title"] == [["título", "aqui"]]
    assert blocks["paragraph"] == [["dois"], ["três"]]
    assert blocks["list_item"] == [["item"]]
    assert blocks["caption"] == [["Figura"]]
    n1, n2 = doc.notes.values()
    assert (n1.label, n2.label) == ("1", "2")
    assert italic_words(n1.blocks[0].spans) == ["um."]
    assert italic_words(n2.blocks[0].spans) == ["dois."]


def test_assemble_applies_marks_to_endnotes():
    regions = [Region("section-header", (151, 100, 900, 150), "Notas"),
               Region("text", (151, 200, 1547, 300), "1. Primeira nota\n2. Segunda nota")]
    doc = assemble([_layout(*regions)], italic={(0, 1): [False, True, False, False, False, True]})
    n1, n2 = doc.notes.values()
    assert italic_words(n1.blocks[0].spans) == ["Primeira"]
    assert italic_words(n2.blocks[0].spans) == ["nota"]


def test_assemble_keeps_ocr_spans_when_marks_do_not_fit(caplog):
    layout = _layout(Region("text", (151, 400, 1547, 500), "uma *duas* três"))
    for marks in ([True, True], [True, True, True, True]):      # 3 tokens
        doc = assemble([layout], italic={(0, 0): marks})
        assert doc_italics(doc) == {"duas"}
    assert "itálico" in caplog.text


# --- CLI ----------------------------------------------------------------------

def test_cli_italic_options_and_defaults():
    args = cli._parser().parse_args(["analyze", "in.pdf"])
    assert args.server == "http://127.0.0.1:8000/v1"
    assert (args.italic_server, args.italic_model, args.italic_preset) == ("http://127.0.0.1:8001/v1", "model", "qwen3vl")
    assert (args.italic_rule, args.switch, args.no_italic) == ("r2", "sleep", False)
    assert (args.ocr_hf_model, args.italic_hf_model) == ("rednote-hilab/dots.mocr", ITALIC_HF_MODEL)
    assert cli._parser().parse_args(["convert", "in.pdf", "--no-italic", "--italic-rule", "r1"]).italic_rule == "r1"
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["render", "doc.json", "-o", "x.pdf", "--no-italic"])


def _models_client(payload) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/models"
        return httpx.Response(200, json=payload)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_cli_sleep_mode_model_id_and_switcher(tmp_path):
    url = "http://127.0.0.1:8001/v1"
    root = {"data": [{"id": "model", "root": "Qwen/Qwen3-VL-8B-Instruct-FP8"}]}
    assert cli._served_model(url, _models_client(root)) == "Qwen/Qwen3-VL-8B-Instruct-FP8"
    assert cli._served_model(url, _models_client({"data": [{"id": "model"}]})) == "model"

    def refuse(req):
        raise httpx.ConnectError("recusado", request=req)
    with pytest.raises(RuntimeError, match="serve_pair.sh"):
        cli._served_model(url, httpx.Client(transport=httpx.MockTransport(refuse)))

    sw = cli._switcher(cli._parser().parse_args(["analyze", "in.pdf"]), tmp_path)
    assert isinstance(sw, SleepSwitcher)
    assert sw.roots == {"ocr": "http://127.0.0.1:8000", "italic": "http://127.0.0.1:8001"}


def test_cli_restart_mode_uses_hf_models(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_served_model", lambda *a, **kw: pytest.fail("modo restart não consulta o servidor"))
    args = cli._parser().parse_args(["analyze", "in.pdf", "--switch", "restart", "--italic-rule", "r1"])
    sw = cli._switcher(args, tmp_path)
    assert isinstance(sw, RestartSwitcher)
    assert sw.models == {"ocr": ("rednote-hilab/dots.mocr", 8000), "italic": (ITALIC_HF_MODEL, 8001)}
    config = cli._italic(args)
    assert (config.model_id, config.rule, config.workers) == (ITALIC_HF_MODEL, "r1", 4)
    assert cli._italic(cli._parser().parse_args(["analyze", "in.pdf", "--no-italic"])) is None


def test_cli_no_italic_switchers(tmp_path):
    args = cli._parser().parse_args(["analyze", "in.pdf", "--no-italic", "--switch", "restart"])
    restart = cli._switcher(args, tmp_path)
    assert isinstance(restart, RestartSwitcher)
    assert restart.models == {"ocr": ("rednote-hilab/dots.mocr", 8000)}   # só sobe o dots
    sleep = cli._switcher(cli._parser().parse_args(["analyze", "in.pdf", "--no-italic"]), tmp_path)
    assert isinstance(sleep, SleepSwitcher) and set(sleep.roots) == {"ocr", "italic"}


def test_cli_no_italic_with_plain_ocr_server(tmp_path, monkeypatch):
    # só o dots no ar, do serve_vllm.sh simples (404 em /is_sleeping); nada na 8001
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.port == 8001:
            raise httpx.ConnectError("recusado", request=req)
        calls.append(f"{req.method} {req.url.port} {req.url.path}")
        return httpx.Response(404)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(cli, "SleepSwitcher", lambda roots: SleepSwitcher(roots, client=client))
    image, regions = benjamin_like_page()
    engine = FakeEngine([regions])
    monkeypatch.setattr(cli, "VlmEngine", lambda **kw: engine)
    pdf = make_pdf(tmp_path / "in.pdf", [image])
    assert cli.main(["analyze", str(pdf), "-w", str(tmp_path / "w"), "--no-italic", "--workers", "1"]) == 0
    assert engine.calls == 1 and (tmp_path / "w" / "document.json").exists()
    assert calls == ["GET 8000 /is_sleeping"]          # nenhum /sleep nem /wake_up


def test_cli_reports_server_error_without_traceback(tmp_path, monkeypatch, capsys):
    def down(*a, **kw):
        raise RuntimeError("servidor do itálico inacessível; suba o par com scripts/serve_pair.sh")
    monkeypatch.setattr(cli, "_served_model", down)
    assert cli.main(["analyze", str(tmp_path / "in.pdf"), "-w", str(tmp_path / "w")]) == 1
    assert "ocrpdf: erro: servidor do itálico inacessível" in capsys.readouterr().err
