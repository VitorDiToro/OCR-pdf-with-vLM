"""Servidor HTTP local do editor: API JSON + arquivos estáticos, só em 127.0.0.1."""

from __future__ import annotations

import dataclasses
import errno
import json
import logging
import mimetypes
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..model import OutputFormat
from ..output_format import validate_output_format
from . import rendering
from .session import ConflictError, EditError, EditSession

_STATIC = Path(__file__).with_name("static")
_log = logging.getLogger(__name__)
_BUSY = "renderização em andamento; aguarde terminar"
_MAX_BODY = 10 * 1024 * 1024   # corpo maior: 413, sem ler


class _HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class _App:
    """Estado compartilhado pelas requisições (a sessão, o PDF de saída, as imagens da última renderização)."""

    def __init__(self, session: EditSession, output: Path, pdf: Path | None) -> None:
        self.session = session
        self.output = Path(output).resolve()   # a resposta do Renderizar tudo mostra o caminho completo
        self.pdf = pdf
        self.base_dir = session.path.parent
        self.render_lock = threading.Lock()   # uma renderização por vez
        self.state_lock = threading.RLock()   # protege a sessão
        # pypdfium2 não é thread-safe: toda chamada que rasteriza PDF (original_image, pdf_page_images,
        # render_page_images) passa por este lock. Ordem de aquisição: state_lock -> pdf_lock.
        self.pdf_lock = threading.Lock()
        self.images: dict[str, list[bytes]] = {}   # imagens da última renderização (por token)
        self._counter = 0
        self._sizes: dict[int, tuple[float, float]] = {}

    def keep_images(self, images: list[bytes]) -> list[str]:
        """Guarda só as imagens da última renderização; devolve as URLs."""
        self._counter += 1
        token = str(self._counter)
        self.images = {token: images}
        return [f"/img/{token}/{k}.png" for k in range(len(images))]

    def original_size(self, page: int) -> tuple[float, float] | None:
        if self.pdf is None:
            return None
        if page not in self._sizes:
            try:
                with self.pdf_lock:
                    self._sizes[page] = rendering.original_size(self.pdf, page)
            except Exception:   # página fora do PDF ou PDF ilegível: sem caixa
                return None
        return self._sizes[page]

    # --- ações ------------------------------------------------------------

    def check_page(self, page: int) -> int:
        if page not in self.session.pages():
            raise _HttpError(404, f"página {page} não existe no documento")
        return page

    def state(self) -> dict:
        s = self.session
        with self.state_lock:
            return {
                "title": s.doc.source or s.path.parent.name,
                "pages": s.pages(),
                "format": dataclasses.asdict(s.format()),
                "body_font_pt": s.doc.body_font_pt,
                "has_original": self.pdf is not None,
                "dirty": s.dirty,
            }

    def page(self, page: int) -> dict:
        s = self.session
        with self.state_lock:
            self.check_page(page)
            blocks = s.page_blocks(page, s.format())
            size = self.original_size(page)
            for row in blocks:
                bbox = s.doc.blocks[row["index"]].source.bbox
                row["box"] = None
                if size and bbox:
                    x1, y1, x2, y2 = bbox
                    row["box"] = [x1 / size[0], y1 / size[1], x2 / size[0], y2 / size[1]]
            return {"text": s.page_text(page), "blocks": blocks}

    def apply(self, body: dict, with_format: bool = True) -> int:
        """Aplica texto (e formato) da página à sessão, em memória; devolve a página."""
        page = self.check_page(_int(body.get("page"), "page"))
        text = body.get("text")
        if not isinstance(text, str):
            raise _HttpError(400, "campo 'text' ausente")
        fmt = _format(body.get("format")) if with_format else None   # tipos conferidos antes de mexer na sessão
        self.session.apply_page(page, text)
        if fmt is not None:
            self.session.set_format(fmt)
        return page

    def save(self, body: dict) -> int:
        with self.state_lock:
            page = self.apply(body)
            self.session.save()
            return page

    def render_page(self, body: dict) -> dict:
        s = self.session
        with self.state_lock:
            highlight = body.get("highlight")   # conferido antes de mexer na sessão
            if highlight is not None and (not isinstance(highlight, int) or isinstance(highlight, bool)
                                          or not 0 <= highlight < len(s.doc.blocks)):
                raise _HttpError(400, "'highlight' deve ser o índice de um bloco")
            page = self.apply(body)
            with self.pdf_lock:
                images = rendering.render_page_images(
                    s.doc, s.page_indices(page), s.format(), self.base_dir, highlight=highlight)
        return {"images": self.keep_images(images)}

    def reload(self) -> dict:
        """Relê o document.json do disco (saída do conflito de mtime); descarta a memória e as imagens."""
        with self.state_lock:
            self.session.reload()
            self.images = {}
            return self.state()

    def render_all(self, body: dict) -> dict:
        s = self.session
        with self.state_lock:
            page = self.save(body)
            # o HTML como o `ocrpdf render` grava (ao lado do document.json, sem os ids dos blocos)
            mapping, count = rendering.render_full(s.doc, s.format(), self.output, self.base_dir,
                                                   html_out=s.path.with_suffix(".html"))
            wanted = rendering.final_pages(mapping, s.page_indices(page), count)
        with self.pdf_lock:
            images = rendering.pdf_page_images(self.output, wanted) if wanted else []
        return {"pdf": str(self.output), "pages": wanted, "images": self.keep_images(images)}


def _int(value, name: str) -> int:
    """Inteiro do JSON ou texto de dígitos da query; true e 0.9 não são índices."""
    try:
        if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
            raise ValueError
        return int(value)
    except (TypeError, ValueError, OverflowError):
        raise _HttpError(400, f"'{name}' deve ser um número inteiro") from None


def _format(data) -> OutputFormat:
    if not isinstance(data, dict):
        raise _HttpError(400, "campo 'format' ausente")
    known = {f.name for f in dataclasses.fields(OutputFormat)}
    fmt = OutputFormat(**{k: v for k, v in data.items() if k in known})
    try:
        validate_output_format(fmt)
    except ValueError as e:
        raise _HttpError(400, f"formato inválido: {e}") from None
    return fmt


def _handler(app: _App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):   # silencioso
            pass

        # --- saída ---
        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _dispatch(self, routes) -> None:
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if not self._local_host():
                    # DNS rebinding: um site cujo nome passa a apontar para 127.0.0.1 chega aqui com o
                    # Host dele, e não o nosso; sem isso, poderia ler e gravar como se fosse a página
                    raise _HttpError(403, "Host não permitido: abra o editor por 127.0.0.1 ou localhost")
                if self.command == "POST" and not self._is_json():
                    # exigir application/json força um preflight CORS (que não respondemos): páginas de
                    # outras origens não conseguem gravar nem renderizar por aqui
                    raise _HttpError(415, "Content-Type deve ser application/json")
                for pattern, fn in routes:
                    if pattern(url.path):
                        return fn(url.path, query)
                raise _HttpError(404, "não encontrado")
            except _HttpError as e:
                self._json({"error": str(e)}, e.status)
            except ConflictError as e:   # "conflict": a página oferece recarregar (o 409 de ocupado não tem)
                self._json({"error": str(e), "conflict": True}, 409)
            except (EditError, ValueError) as e:
                self._json({"error": str(e)}, 400)
            except Exception as e:   # falha inesperada (WeasyPrint, disco, pdfium): resposta JSON, não conexão caída
                _log.exception("erro interno em %s %s", self.command, self.path)
                self._json({"error": f"erro interno: {e}"}, 500)

        def _local_host(self) -> bool:
            port = self.server.server_address[1]
            host = (self.headers.get("Host") or "").strip().lower()
            return host in (f"127.0.0.1:{port}", f"localhost:{port}")

        def _is_json(self) -> bool:
            ctype = self.headers.get("Content-Type") or ""
            return ctype.split(";")[0].strip().lower() == "application/json"

        def _body(self) -> dict:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1   # não numérico: inválido, como o negativo
            if n < 0:   # read(-1) leria até o cliente fechar a conexão
                raise _HttpError(400, "Content-Length inválido")
            if n > _MAX_BODY:
                raise _HttpError(413, f"corpo da requisição grande demais (máximo de {_MAX_BODY // 2**20} MB)")
            try:
                data = json.loads(self.rfile.read(n) or b"{}")   # NaN/Infinity: recusados em _format e na sessão
            except ValueError:
                raise _HttpError(400, "corpo da requisição não é um JSON válido") from None
            if not isinstance(data, dict):
                raise _HttpError(400, "o corpo deve ser um objeto JSON")
            return data

        def _rendering(self, fn, body: dict) -> None:
            if not app.render_lock.acquire(blocking=False):
                raise _HttpError(409, _BUSY)
            try:
                self._json(fn(body))
            finally:
                app.render_lock.release()

        # --- GET ---
        def do_GET(self) -> None:
            self._dispatch([
                (lambda p: p == "/", lambda p, q: self._static("index.html")),
                (lambda p: p.startswith("/static/"), lambda p, q: self._static(p[len("/static/"):])),
                (lambda p: p == "/api/state", lambda p, q: self._json(app.state())),
                (lambda p: p == "/api/page", lambda p, q: self._json(app.page(_int(q.get("n"), "n")))),
                (lambda p: p.startswith("/img/"), lambda p, q: self._image(p)),
                (lambda p: p.startswith("/original/"), lambda p, q: self._original(p)),
            ])

        def _static(self, name: str) -> None:
            path = (_STATIC / name).resolve()
            if _STATIC.resolve() not in path.parents or not path.is_file():
                raise _HttpError(404, "arquivo não encontrado")
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, path.read_bytes(), ctype)

        def _image(self, path: str) -> None:
            try:
                _, _, token, name = path.split("/")
                images = app.images[token]
                k = int(name.removesuffix(".png"))
                if k < 0:   # images[-1] seria a última
                    raise IndexError
                data = images[k]
            except (ValueError, KeyError, IndexError):
                raise _HttpError(404, "imagem não encontrada") from None
            self._send(200, data, "image/png")

        def _original(self, path: str) -> None:
            if app.pdf is None:
                raise _HttpError(404, "sem PDF original")
            try:
                page = int(path[len("/original/"):].removesuffix(".png"))
                with app.pdf_lock:
                    png = rendering.original_image(app.pdf, page)[0]
            except Exception:
                raise _HttpError(404, "página do original não encontrada") from None
            self._send(200, png, "image/png")

        # --- POST ---
        def do_POST(self) -> None:
            def page_post(p, q):
                body = self._body()   # lido fora do lock, como nos outros POST
                with app.state_lock:
                    page = app.check_page(_int(q.get("n"), "n"))
                    text = body.get("text")
                    if not isinstance(text, str):
                        raise _HttpError(400, "campo 'text' ausente")
                    app.session.apply_page(page, text)
                self._json({"ok": True})

            def split(p, q):
                body = self._body()
                with app.state_lock:
                    page = app.check_page(_int(q.get("n"), "n"))
                    text = body.get("text")
                    if not isinstance(text, str):
                        raise _HttpError(400, "campo 'text' ausente")
                    self._json({"block": app.session.split_block(page, text)})

            def justify(p, q):
                with app.state_lock:
                    self._json({"changed": app.session.justify_all()})

            def save(p, q):
                app.save(self._body())
                self._json({"ok": True})

            self._dispatch([
                (lambda p: p == "/api/page", page_post),
                (lambda p: p == "/api/split", split),
                (lambda p: p == "/api/justify-all", justify),
                (lambda p: p == "/api/save", save),
                (lambda p: p == "/api/reload", lambda p, q: self._json(app.reload())),
                (lambda p: p == "/api/render/page", lambda p, q: self._rendering(app.render_page, self._body())),
                (lambda p: p == "/api/render/all", lambda p, q: self._rendering(app.render_all, self._body())),
            ])

    return Handler


def make_server(session: EditSession, output: Path, pdf: Path | None, port: int = 0) -> ThreadingHTTPServer:
    """Servidor pronto (ainda sem `serve_forever`); `port=0` escolhe uma porta livre."""
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(_App(session, Path(output), pdf)))
    server.daemon_threads = True
    return server


def _find_original(session: EditSession, pdf: Path | None) -> Path | None:
    """`--pdf` ou o `source_path` do documento (relativo à pasta do document.json); None se não existir."""
    candidates = [Path(pdf)] if pdf else ([Path(session.doc.source_path)] if session.doc.source_path else [])
    for c in candidates:
        for path in (c, session.path.parent / c):
            if path.is_file():
                return path
    return None


def _is_original(session: EditSession, output: Path, pdf: Path | None) -> bool:
    """`output` é o `--pdf` ou o `source_path` (como estão ou relativos à pasta do document.json)?"""
    names = [n for n in (pdf, session.doc.source_path) if n]
    target = Path(output).resolve()
    return any(target == p.resolve() for n in names for p in (Path(n), session.path.parent / n))


def serve(document: Path, output: Path, pdf: Path | None, port: int = 0, open_browser: bool = True) -> None:
    """Abre o editor e bloqueia até Ctrl+C. Erros de início viram RuntimeError (a CLI mostra a mensagem)."""
    try:
        session = EditSession(document)
    except EditError as e:
        raise RuntimeError(str(e)) from None
    if _is_original(session, output, pdf):   # os dois: o --pdf pode ser uma cópia do source_path
        raise RuntimeError(f"o PDF de saída ({output}) é o próprio PDF escaneado (--pdf ou source_path); "
                           "escolha outro com -o")
    original = _find_original(session, pdf)
    if original is None:
        print("ocrpdf: sem PDF original (use --pdf); a aba Original ficará indisponível")
    try:
        server = make_server(session, output, original, port)
    except (OSError, OverflowError) as e:
        if getattr(e, "errno", None) == errno.EADDRINUSE:
            raise RuntimeError(f"a porta {port} de 127.0.0.1 já está em uso; escolha outra com --port "
                               "(0 = uma livre)") from None
        raise RuntimeError(f"não foi possível abrir o servidor em 127.0.0.1:{port}: "
                           f"{getattr(e, 'strerror', None) or e}") from None
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Editor em {url}  (Ctrl+C para sair)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        server.server_close()
        if session.dirty:
            print("ocrpdf: há alterações não gravadas (descartadas)")
