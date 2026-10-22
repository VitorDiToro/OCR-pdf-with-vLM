"""Interface de linha de comando.

  ocrpdf convert entrada.pdf [-o saida.pdf]   # OCR + montagem + renderização
  ocrpdf analyze entrada.pdf [-w pasta]       # só OCR + montagem (gera document.json)
  ocrpdf render  pasta/document.json -o x.pdf # re-renderiza após editar o JSON
  ocrpdf edit    pasta/document.json -o x.pdf # editor no navegador (local)
"""

from __future__ import annotations

import argparse
import copy
import logging
import math
import signal
import sys
from contextlib import contextmanager
from pathlib import Path

import httpx

from . import editor
from .italic.stage import MODEL_RULES, uses_model
from .model import Document, OutputFormat
from .ocr.vlm import PRESETS, VlmEngine
from .output_format import apply_format, parse_page_size
from .pipeline import ItalicConfig, Pipeline
from .render import render_pdf
from .servers import ITALIC_HF_MODEL, ITALIC_SERVER_ENV, RestartSwitcher, SleepSwitcher, Switcher, default_script


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        with _signals_exit():
            return args.func(args)
    except RuntimeError as e:   # servidor fora do ar, que não subiu etc.: a mensagem já diz o que fazer
        print(f"ocrpdf: erro: {e}", file=sys.stderr)
        return 1


@contextmanager
def _signals_exit():
    """SIGTERM e SIGHUP (kill, terminal fechado) viram SystemExit: os `finally` rodam e o modo restart
    derruba o servidor que subiu. Sem isso o Python sai direto e o vLLM fica órfão na GPU (ele roda
    em sessão própria e não recebe o sinal do terminal)."""
    def leave(signum, frame):
        raise SystemExit(128 + signum)
    previous = {sig: signal.signal(sig, leave) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ocrpdf", description="PDF escaneado → PDF com texto real.")
    p.add_argument("-v", "--verbose", action="store_true", help="mostra o progresso (logging INFO)")
    sub = p.add_subparsers(required=True)

    # -v também depois do subcomando; SUPPRESS impede que o padrão do subparser apague o -v global.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
                        help="mostra o progresso (logging INFO)")

    ocr = argparse.ArgumentParser(add_help=False, parents=[common])
    ocr.add_argument("input", type=Path, help="PDF escaneado")
    ocr.add_argument("-w", "--work-dir", type=Path, help="pasta de trabalho (padrão: <saída>_work)")
    ocr.add_argument("--server", default="http://127.0.0.1:8000/v1", help="URL da API OpenAI-compatível")
    ocr.add_argument("--model", default="model", help="nome do modelo servido (--served-model-name)")
    ocr.add_argument("--preset", choices=sorted(PRESETS), default="dots", help="prompt/coords do modelo")
    ocr.add_argument("--dpi", type=int, default=200, help="resolução da rasterização")
    ocr.add_argument("--pages", help='páginas, ex.: "1-3,7" (padrão: todas)')
    ocr.add_argument("--workers", type=int, default=4, help="páginas processadas em paralelo")
    ocr.add_argument("--font", default="auto", help='auto | serif | sans | mono | nome de uma família instalada')
    ocr.add_argument("--lang", default="pt-BR", help="idioma do documento (metadado do PDF)")
    ocr.add_argument("--force", action="store_true", help="refaz o OCR e as marcas de itálico mesmo com cache")
    # etapa 2: itálico por palavra (inclinação medida na imagem; opcionalmente, um 2º modelo)
    ocr.add_argument("--no-italic", action="store_true",
                     help="sem a etapa 2: fica o itálico do OCR, sem conferir na imagem")
    ocr.add_argument("--italic-rule", choices=["slant", *MODEL_RULES], default="slant",
                     help="slant = a inclinação medida na imagem decide; sem medida, vale o OCR (basta o "
                          "servidor do OCR). Com um 2º modelo no ar (scripts/serve_pair.sh): r1 = só o que ele "
                          "marcou; r2 = o que o OCR ou ele marcou (nas duas, a inclinação precisa confirmar)")
    ocr.add_argument("--italic-server", default="http://127.0.0.1:8001/v1", help="URL da API do 2º modelo (r1/r2)")
    ocr.add_argument("--italic-model", default="model", help="nome servido do 2º modelo (--served-model-name)")
    ocr.add_argument("--italic-preset", choices=sorted(PRESETS), default="qwen3vl", help="preset do 2º modelo")
    ocr.add_argument("--switch", choices=["sleep", "restart"], default="sleep",
                     help="alternância dos servidores na GPU: sleep = par já no ar (scripts/serve_pair.sh); "
                          "restart = a app sobe e derruba cada um com scripts/serve_vllm.sh")
    ocr.add_argument("--ocr-hf-model", default="rednote-hilab/dots.mocr", help="modelo do OCR (só no modo restart)")
    ocr.add_argument("--italic-hf-model", default=ITALIC_HF_MODEL, help="2º modelo (só no modo restart)")

    conv = sub.add_parser("convert", parents=[ocr], help="OCR + renderização")
    conv.add_argument("-o", "--output", type=Path, help="PDF de saída (padrão: <entrada>_ocr.pdf)")
    conv.add_argument("--no-header", action="store_true", help="omite o cabeçalho corrente")
    _add_format_options(conv)
    conv.set_defaults(func=_convert)

    ana = sub.add_parser("analyze", parents=[ocr], help="só OCR e montagem do document.json")
    ana.set_defaults(func=_analyze)

    ren = sub.add_parser("render", parents=[common], help="renderiza um document.json")
    ren.add_argument("document", type=Path)
    ren.add_argument("-o", "--output", type=Path, required=True)
    ren.add_argument("--no-header", action="store_true")
    _add_format_options(ren)
    ren.set_defaults(func=_render)

    edi = sub.add_parser("edit", parents=[common], help="editor local do document.json no navegador")
    edi.add_argument("document", type=Path)
    edi.add_argument("-o", "--output", type=Path, required=True, help="PDF gerado pelo botão de renderizar")
    edi.add_argument("--pdf", type=Path, help="PDF escaneado original (padrão: source_path do document.json)")
    edi.add_argument("--port", type=int, default=0, help="porta em 127.0.0.1 (padrão: uma livre)")
    edi.add_argument("--no-browser", action="store_true", help="não abre o navegador")
    edi.set_defaults(func=_edit)
    return p


def _add_format_options(p: argparse.ArgumentParser) -> None:
    """Formato de saída; o `convert` o grava no document.json e o `render` usa o gravado, com a opção por cima."""
    p.add_argument("--page-size", type=_page_size_arg, default=None, metavar="TAMANHO",
                   help="original | a4 | <largura>x<altura> em cm, ex.: 17,6x25 (padrão original ou o gravado)")
    p.add_argument("--margin", type=float, default=None,
                   help="margens em cm, decimal com ponto (padrão 2.5 ou o gravado); só vale quando --page-size não é original")
    p.add_argument("--font-size", type=_font_size_arg, default=None, metavar="PT",
                   help="tamanho do corpo em pt, decimal com ponto; títulos, notas e cabeçalho escalam junto (padrão: o gravado ou o medido)")


def _page_size_arg(spec: str) -> str:
    """Valida com parse_page_size e devolve o texto; só ArgumentTypeError mostra a mensagem em português."""
    try:
        parse_page_size(spec)
        return spec.strip()
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def _font_size_arg(spec: str) -> float:
    try:
        value = float(spec)
    except ValueError:
        raise argparse.ArgumentTypeError(f"tamanho de fonte inválido: {spec}") from None
    if not (math.isfinite(value) and value > 0):
        raise argparse.ArgumentTypeError("o tamanho da fonte deve ser positivo")
    return value


def _output_format(args, saved: OutputFormat | None) -> OutputFormat:
    """Formato gravado (ou o padrão) com as opções passadas por cima."""
    fmt = copy.copy(saved) if saved else OutputFormat()
    if args.page_size is not None:
        fmt.page_size = args.page_size
    if args.margin is not None:
        fmt.margin_cm = args.margin
    if args.font_size is not None:
        fmt.font_size_pt = args.font_size
    if args.no_header:
        fmt.header = False
    return fmt


def _formatted(args, doc: Document) -> Document | None:
    """Aplica o formato (gravado + opções); em caso de valor inválido, avisa e devolve None."""
    try:
        return apply_format(doc, _output_format(args, doc.output))
    except ValueError as e:
        print(f"ocrpdf: erro: {e}", file=sys.stderr)
        return None


def _work_dir(args, output: Path | None) -> Path:
    if args.work_dir:
        return args.work_dir
    base = output or args.input
    return base.with_name(base.stem + "_work")


def _run_analysis(args, work: Path) -> Document:
    engine = VlmEngine(base_url=args.server, model=args.model, preset=args.preset)
    pipeline = Pipeline(engine, work, dpi=args.dpi, workers=args.workers,
                        italic=_italic(args), switcher=_switcher(args, work))
    return pipeline.analyze(args.input, pages=args.pages, font=args.font, lang=args.lang, force=args.force)


def _italic(args) -> ItalicConfig | None:
    if args.no_italic:
        return None
    if not uses_model(args.italic_rule):
        return ItalicConfig(rule=args.italic_rule, workers=args.workers)   # nem consulta o 2º modelo
    # o id entra no cache das marcas: trocar de modelo invalida as marcas antigas
    model_id = args.italic_hf_model if args.switch == "restart" else _served_model(args.italic_server)
    engine = VlmEngine(base_url=args.italic_server, model=args.italic_model, preset=args.italic_preset)
    return ItalicConfig(engine, model_id, rule=args.italic_rule, workers=args.workers)


def _switcher(args, work: Path) -> Switcher:
    if args.switch == "restart":
        models = {"ocr": (args.ocr_hf_model, _port(args.server))}
        if not args.no_italic and uses_model(args.italic_rule):
            models["italic"] = (args.italic_hf_model, _port(args.italic_server))
        return RestartSwitcher(models, default_script(), work / "logs", env={"italic": ITALIC_SERVER_ENV})
    # sem 2º modelo também: o SleepSwitcher aceita só o dots no ar (até sem modo sleep) e, com o
    # par no ar, acorda o dots e faz o 2º modelo dormir
    return SleepSwitcher({"ocr": _root(args.server), "italic": _root(args.italic_server)})


def _served_model(url: str, client: httpx.Client | None = None) -> str:
    """Modelo servido em `url` (o `root` do 1º de GET /models, isto é, o nome no Hugging Face)."""
    try:
        resp = (client or httpx).get(f"{url.rstrip('/')}/models", timeout=10.0)
        resp.raise_for_status()
        first = resp.json()["data"][0]
        return first.get("root") or first["id"]
    except (httpx.HTTPError, ValueError, LookupError, TypeError, AttributeError) as e:
        raise RuntimeError(
            f"servidor do itálico inacessível em {url} ({e}); suba o par com scripts/serve_pair.sh"
        ) from e


def _root(url: str) -> str:
    """URL da API sem o /v1: os endpoints de sleep/wake ficam na raiz do servidor."""
    url = url.rstrip("/")
    return url[:-3] if url.endswith("/v1") else url


def _port(url: str) -> int:
    return httpx.URL(url).port or 80


def _convert(args) -> int:
    output = args.output or args.input.with_name(args.input.stem + "_ocr.pdf")
    # erros de formato (margem grande demais para a página) aparecem antes do OCR, que é demorado
    if _formatted(args, Document()) is None:
        return 2
    work = _work_dir(args, output)
    doc = _run_analysis(args, work)
    doc.output = _output_format(args, None)
    doc.save(work / "document.json")
    if (doc := _formatted(args, doc)) is None:
        return 2
    render_pdf(doc, output, base_dir=work, html_out=work / "document.html")
    print(f"{output}  (intermediário: {work / 'document.json'})")
    return 0


def _analyze(args) -> int:
    work = _work_dir(args, None)
    _run_analysis(args, work)
    print(work / "document.json")
    return 0


def _render(args) -> int:
    doc = Document.load(args.document)
    if (doc := _formatted(args, doc)) is None:
        return 2
    render_pdf(doc, args.output, base_dir=args.document.parent, html_out=args.document.with_suffix(".html"))
    print(args.output)
    return 0


def _edit(args) -> int:
    editor.serve(args.document, args.output, args.pdf, port=args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    sys.exit(main())
