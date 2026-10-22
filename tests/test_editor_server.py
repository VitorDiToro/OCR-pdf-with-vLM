import json
import threading

import httpx
import pytest
from pypdf import PdfReader

from ocrpdf import cli, editor
from ocrpdf.editor import rendering, server
from ocrpdf.editor.session import EditSession
from ocrpdf.model import Block, Document, Source, Span
from synth import benjamin_like_page, make_pdf


def _doc(tmp_path, source_path: str = "") -> EditSession:
    doc = Document(source_path=source_path)
    doc.blocks = [
        Block(kind="paragraph", spans=[Span(f"Parágrafo {i} do texto.")], source=Source(page=i // 2, bbox=(10, 20, 100, 60)))
        for i in range(4)
    ]
    path = tmp_path / "document.json"
    doc.save(path)
    return EditSession(path)


@pytest.fixture
def api(tmp_path):
    started = []

    def start(session=None, pdf=None):
        session = session or _doc(tmp_path)
        srv = server.make_server(session, tmp_path / "out.pdf", pdf, port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        started.append(srv)
        return session, httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=60)

    yield start
    for srv in started:
        srv.shutdown()
        srv.server_close()


def test_state_and_page(api):
    s, c = api()
    st = c.get("/api/state").json()
    assert st["pages"] == [0, 1] and st["has_original"] is False and st["dirty"] is False
    assert st["format"] == {"page_size": "original", "margin_cm": 2.5, "font_size_pt": None, "header": True}
    pg = c.get("/api/page", params={"n": 1}).json()
    assert len(pg["blocks"]) == 2 and pg["blocks"][0]["box"] is None
    assert json.loads(pg["text"])
    assert c.get("/api/page", params={"n": 9}).status_code == 404
    assert c.get("/api/page", params={"n": "x"}).status_code == 400
    assert c.get("/original/0.png").status_code == 404
    assert c.get("/api/inexistente").status_code == 404


def test_page_post_and_justify(api):
    s, c = api()
    assert c.post("/api/page?n=0", json={"text": "[1 2"}).status_code == 400
    ok = c.post("/api/page?n=0", json={"text": s.page_text(0)})
    assert ok.json() == {"ok": True}
    assert isinstance(c.post("/api/justify-all", json={}).json()["changed"], int)


def test_save_flow(api, tmp_path):
    s, c = api()
    data = json.loads(s.page_text(1))
    data[0]["spans"][0]["text"] = "gravado"
    fmt = {"page_size": "a4", "margin_cm": 2.0, "font_size_pt": None, "header": True}
    r = c.post("/api/save", json={"page": 1, "text": json.dumps(data), "format": fmt})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert "gravado" in (tmp_path / "document.json").read_text()
    assert Document.load(tmp_path / "document.json").output.page_size == "a4"
    r = c.post("/api/save", json={"page": 1, "text": '[{"kind": "paragraph"} {', "format": fmt})
    assert r.status_code == 400 and "linha" in r.json()["error"]
    (tmp_path / "document.json").write_text((tmp_path / "document.json").read_text() + " ")
    import os
    os.utime(tmp_path / "document.json", ns=(1, 1))
    r = c.post("/api/save", json={"page": 1, "text": s.page_text(1), "format": fmt})
    assert r.status_code == 409


def test_bad_format_is_400(api):
    s, c = api()
    r = c.post("/api/save", json={"page": 1, "text": s.page_text(1), "format": {"margin_cm": "x"}})
    assert r.status_code == 400
    r = c.post("/api/save", json={"page": 1, "text": s.page_text(1), "format": {"page_size": "xyz"}})
    assert r.status_code == 400


def test_render_page_returns_png_urls(api):
    s, c = api()
    body = {"page": 1, "text": s.page_text(1), "format": s.format().__dict__, "highlight": 2}
    r = c.post("/api/render/page", json=body)
    assert r.status_code == 200
    urls = r.json()["images"]
    assert urls and c.get(urls[0]).content.startswith(b"\x89PNG")
    assert c.get("/img/zzz/0.png").status_code == 404


def test_render_all_writes_pdf(api, tmp_path):
    s, c = api()
    body = {"page": 1, "text": s.page_text(1), "format": s.format().__dict__}
    r = c.post("/api/render/all", json=body).json()
    assert (tmp_path / "out.pdf").read_bytes().startswith(b"%PDF")
    assert r["pdf"] and r["pages"] and len(r["images"]) == len(r["pages"])
    assert c.get(r["images"][0]).content.startswith(b"\x89PNG")
    assert s.dirty is False   # salvou antes


def test_render_all_aborts_on_save_error(api, tmp_path):
    s, c = api()
    r = c.post("/api/render/all", json={"page": 1, "text": "[", "format": s.format().__dict__})
    assert r.status_code == 400 and not (tmp_path / "out.pdf").exists()


def test_second_render_while_busy_is_refused(api, monkeypatch):
    s, c = api()
    entered, release = threading.Event(), threading.Event()
    real = rendering.render_full

    def slow(*a, **k):
        entered.set()
        release.wait(30)
        return real(*a, **k)

    monkeypatch.setattr(rendering, "render_full", slow)
    body = {"page": 1, "text": s.page_text(1), "format": s.format().__dict__}
    out = []
    t = threading.Thread(target=lambda: out.append(c.post("/api/render/all", json=body)))
    t.start()
    assert entered.wait(30)
    r = c.post("/api/render/all", json=body)
    release.set()
    t.join()
    assert r.status_code == 409 and "renderização em andamento" in r.json()["error"]
    assert "conflict" not in r.json()   # ocupado não é conflito: a página não oferece recarregar
    assert out[0].status_code == 200


def test_original_and_box(api, tmp_path):
    img, _ = benjamin_like_page()
    pdf = make_pdf(tmp_path / "s.pdf", [img, img], dpi=200)
    s, c = api(pdf=pdf)
    assert c.get("/api/state").json()["has_original"] is True
    assert c.get("/original/0.png").content.startswith(b"\x89PNG")
    assert c.get("/original/9.png").status_code == 404
    box = c.get("/api/page", params={"n": 0}).json()["blocks"][0]["box"]
    assert len(box) == 4 and all(0 <= v <= 1 for v in box) and box[0] < box[2]


def test_static_files(api):
    s, c = api()
    assert c.get("/").status_code == 200
    assert c.get("/static/../server.py").status_code == 404


def test_cli_edit_passes_arguments(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(editor, "serve", lambda *a, **k: calls.append((a, k)))
    assert cli.main(["edit", "d/document.json", "-o", "x.pdf", "--no-browser", "--port", "0"]) == 0
    (a, k), = calls
    assert str(a[0]) == "d/document.json" and str(a[1]) == "x.pdf" and a[2] is None
    assert k == {"port": 0, "open_browser": False}


def test_serve_starts_and_stops(tmp_path, monkeypatch):
    sess = _doc(tmp_path)
    made = []
    real = server.make_server
    monkeypatch.setattr(server, "make_server", lambda *a, **k: made.append(real(*a, **k)) or made[-1])
    t = threading.Thread(target=server.serve, args=(sess.path, tmp_path / "o.pdf", None),
                         kwargs={"port": 0, "open_browser": False})
    t.start()
    for _ in range(300):
        if made:
            break
        threading.Event().wait(0.05)
    port = made[0].server_address[1]
    assert httpx.get(f"http://127.0.0.1:{port}/api/state").status_code == 200
    made[0].shutdown()
    t.join(10)
    assert not t.is_alive()


def test_unexpected_error_is_500_json_and_lock_released(api, monkeypatch):
    s, c = api()
    real = rendering.render_full
    monkeypatch.setattr(rendering, "render_full", lambda *a, **k: (_ for _ in ()).throw(OSError("disco cheio")))
    body = {"page": 1, "text": s.page_text(1), "format": s.format().__dict__}
    r = c.post("/api/render/all", json=body)
    assert r.status_code == 500 and "disco cheio" in r.json()["error"]
    monkeypatch.setattr(rendering, "render_full", real)
    assert c.post("/api/render/all", json=body).status_code == 200


def test_post_requires_json_content_type(api, tmp_path):
    s, c = api()
    path = tmp_path / "document.json"
    before = path.read_bytes()
    fmt = {"page_size": "a4", "margin_cm": 2.0, "font_size_pt": None, "header": True}
    body = json.dumps({"page": 1, "text": s.page_text(1), "format": fmt})
    r = c.post("/api/save", content=body, headers={"Content-Type": "text/plain"})
    assert r.status_code == 415 and "application/json" in r.json()["error"]
    assert path.read_bytes() == before
    assert c.post("/api/justify-all", content=b"", headers={"Content-Type": "text/plain"}).status_code == 415
    r = c.post("/api/save", content=body, headers={"Content-Type": "application/json; charset=utf-8"})
    assert r.status_code == 200 and path.read_bytes() != before


# --- conflito e recarga, NaN, páginas do Renderizar tudo, Host, erros de início ----------

def _touch_externally(path, text: str) -> None:
    """Outro programa regrava o document.json: muda o texto do bloco 0 (página 0)."""
    import os

    doc = Document.load(path)
    doc.blocks[0].spans = [Span(text)]
    doc.save(path)
    os.utime(path, ns=(1, path.stat().st_mtime_ns + 10**9))


def test_conflict_then_reload_then_save(api, tmp_path):
    s, c = api()
    path = tmp_path / "document.json"
    fmt = s.format().__dict__
    r = c.post("/api/render/page", json={"page": 1, "text": s.page_text(1), "format": fmt})
    old_image = r.json()["images"][0]
    assert c.post("/api/save", json={"page": 1, "text": s.page_text(1), "format": fmt}).status_code == 200
    _touch_externally(path, "alterado por fora")
    data = json.loads(s.page_text(1))
    data[0]["spans"][0]["text"] = "editado na tela"
    body = {"page": 1, "text": json.dumps(data), "format": fmt}
    r = c.post("/api/save", json=body)
    assert r.status_code == 409 and r.json()["conflict"] is True
    # recarregar a página do navegador não basta: a sessão continua com a base antiga
    c.get("/api/state")
    c.get("/api/page", params={"n": 1})
    assert c.post("/api/save", json=body).status_code == 409
    assert c.post("/api/render/all", json=body).json()["conflict"] is True
    r = c.post("/api/reload", json={})
    assert r.status_code == 200 and r.json()["dirty"] is False and r.json()["pages"] == [0, 1]
    assert c.get(old_image).status_code == 404   # imagens da sessão antiga descartadas
    assert "alterado por fora" in c.get("/api/page", params={"n": 0}).json()["text"]
    assert c.post("/api/save", json=body).status_code == 200
    back = Document.load(path)
    assert back.blocks[0].spans[0].text == "alterado por fora"
    assert back.blocks[2].spans[0].text == "editado na tela"


def test_reload_of_a_broken_file_keeps_the_session(api, tmp_path):
    s, c = api()
    path = tmp_path / "document.json"
    c.post("/api/page?n=1", json={"text": s.page_text(1).replace("Parágrafo 2", "Mudou")})
    path.write_text("{ quebrado", encoding="utf-8")
    r = c.post("/api/reload", json={})
    assert r.status_code == 400 and "document.json" in r.json()["error"]
    assert c.get("/api/state").json()["dirty"] is True
    assert c.post("/api/reload", content=b"{}", headers={"Content-Type": "text/plain"}).status_code == 415


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "1e999"])
def test_nan_and_infinity_are_400_and_nothing_is_written(api, tmp_path, raw):
    s, c = api()
    path = tmp_path / "document.json"
    before = path.read_bytes()
    fmt = '{"page_size": "a4", "margin_cm": 2.0, "font_size_pt": null, "header": true}'
    text = s.page_text(1).replace('"kind": "paragraph"', f'"kind": "paragraph", "style": {{"indent_em": {raw}}}', 1)
    body = '{"page": 1, "text": ' + json.dumps(text) + ', "format": ' + fmt + "}"
    r = c.post("/api/save", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    bad_fmt = fmt.replace("2.0", raw)
    body = '{"page": 1, "text": ' + json.dumps(s.page_text(1)) + ', "format": ' + bad_fmt + "}"
    r = c.post("/api/save", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert path.read_bytes() == before and not s.dirty


def _long_block_session(tmp_path) -> EditSession:
    """Página pequena; o bloco 1 (página 0 do original) ocupa várias páginas do PDF final."""
    from ocrpdf.model import PageGeometry

    words = " ".join(f"palavra{k}" for k in range(900))
    doc = Document(page=PageGeometry(300, 300, 30, 30, 30, 30), body_font_pt=10)
    doc.blocks = [
        Block(kind="paragraph", spans=[Span("Abertura curta.")], source=Source(page=0)),
        Block(kind="paragraph", spans=[Span(words)], source=Source(page=0)),
        Block(kind="paragraph", spans=[Span("Oculto.")], source=Source(page=1)),
        Block(kind="paragraph", spans=[Span("Depois do longo.")], source=Source(page=1)),
        Block(kind="paragraph", spans=[Span("Fim.")], source=Source(page=2)),
    ]
    doc.blocks[2].style.hidden = True
    path = tmp_path / "document.json"
    doc.save(path)
    return EditSession(path)


def test_render_all_shows_every_page_of_a_long_block(api, tmp_path):
    s, c = api(_long_block_session(tmp_path))
    fmt = s.format().__dict__
    r = c.post("/api/render/all", json={"page": 0, "text": s.page_text(0), "format": fmt}).json()
    total = len(PdfReader(tmp_path / "out.pdf").pages)
    mapping = rendering.render_full(s.doc, s.format(), tmp_path / "check.pdf", tmp_path)[0]
    assert total >= 4 and mapping[3] >= 3
    # da página do 1º bloco até a página onde começa o próximo bloco visível (bloco 3), inclusive
    assert r["pages"] == list(range(0, mapping[3] + 1)) and len(r["images"]) == len(r["pages"])
    # última página do original: sem próximo bloco, vai até o fim do PDF
    r = c.post("/api/render/all", json={"page": 2, "text": s.page_text(2), "format": fmt}).json()
    assert r["pages"] == list(range(mapping[4], total))


@pytest.mark.parametrize("host", ["evil.example", "evil.example:{port}", "127.0.0.1:1", "localhost"])
def test_foreign_host_is_403(api, tmp_path, host):
    s, c = api()
    port = c.base_url.port
    path = tmp_path / "document.json"
    before = path.read_bytes()
    h = {"Host": host.format(port=port)}
    r = c.get("/api/page", params={"n": 0}, headers=h)
    assert r.status_code == 403 and "error" in r.json()
    assert c.get("/", headers=h).status_code == 403
    r = c.post("/api/save", json={"page": 1, "text": s.page_text(1), "format": s.format().__dict__}, headers=h)
    assert r.status_code == 403 and path.read_bytes() == before
    assert c.get("/api/state", headers={"Host": f"localhost:{port}"}).status_code == 200
    assert c.get("/api/state", headers={"Host": f"LOCALHOST:{port}"}).status_code == 200


def test_cli_edit_start_errors_are_messages(tmp_path, capsys):
    import socket

    assert cli.main(["edit", str(tmp_path / "nada.json"), "-o", "x.pdf", "--no-browser"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ocrpdf: erro:") and "não encontrado" in err and "Traceback" not in err
    (tmp_path / "ruim.json").write_text("{", encoding="utf-8")
    assert cli.main(["edit", str(tmp_path / "ruim.json"), "-o", "x.pdf", "--no-browser"]) == 1
    assert "ocrpdf: erro:" in capsys.readouterr().err
    sess = _doc(tmp_path)
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert cli.main(["edit", str(sess.path), "-o", "x.pdf", "--no-browser", "--port", str(port)]) == 1
    err = capsys.readouterr().err
    assert "ocrpdf: erro:" in err and str(port) in err and "--port" in err


# --- revisão do PR #2 ---------------------------------------------------------------------

def test_serve_refuses_output_equal_to_the_original(tmp_path, monkeypatch, capsys):
    def no_server(*a, **k):
        raise AssertionError("não devia abrir o servidor")

    monkeypatch.setattr(server, "make_server", no_server)
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(b"%PDF-1.4 original")
    sess = _doc(tmp_path)
    assert cli.main(["edit", str(sess.path), "-o", str(pdf), "--pdf", str(pdf), "--no-browser"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ocrpdf: erro:") and "PDF escaneado" in err
    sess = _doc(tmp_path, source_path="in.pdf")        # relativo à pasta do document.json
    assert cli.main(["edit", str(sess.path), "-o", str(tmp_path / "." / "in.pdf"), "--no-browser"]) == 1
    assert "PDF escaneado" in capsys.readouterr().err
    assert pdf.read_bytes() == b"%PDF-1.4 original"


def _raw_post(c, length: str) -> tuple[int, bytes]:
    """POST /api/save com o Content-Length dado e sem corpo (o httpx não deixa mandar um inválido)."""
    import socket

    port = c.base_url.port
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall((f"POST /api/save HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                      f"Content-Type: application/json\r\nContent-Length: {length}\r\n\r\n").encode())
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    return int(head.split()[1]), body


def test_bad_content_length(api, tmp_path):
    s, c = api()
    before = (tmp_path / "document.json").read_bytes()
    status, body = _raw_post(c, "-1")
    assert status == 400 and "Content-Length" in json.loads(body)["error"]
    status, body = _raw_post(c, str(server._MAX_BODY + 1))   # sem mandar o corpo: não pode esperar por ele
    assert status == 413 and "grande demais" in json.loads(body)["error"]
    assert (tmp_path / "document.json").read_bytes() == before


@pytest.mark.parametrize("fmt", [{"margin_cm": -5}, {"font_size_pt": 1e308}, {"margin_cm": None},
                                 {"page_size": 5}, {"header": "sim"}])
def test_invalid_format_is_400_and_nothing_is_written(api, tmp_path, fmt):
    s, c = api()
    path = tmp_path / "document.json"
    before = path.read_bytes()
    data = json.loads(s.page_text(1))
    data[0]["spans"][0]["text"] = "não grava"
    full = {"page_size": "a4", "margin_cm": 2.0, "font_size_pt": None, "header": True, **fmt}
    r = c.post("/api/save", json={"page": 1, "text": json.dumps(data), "format": full})
    assert r.status_code == 400 and r.json()["error"].startswith("formato inválido:")
    assert path.read_bytes() == before and not s.dirty and "não grava" not in s.page_text(1)


@pytest.mark.parametrize("highlight", [1.5, True, -1, 99, "2"])
def test_bad_highlight_is_400_before_touching_the_session(api, highlight):
    s, c = api()
    data = json.loads(s.page_text(1))
    data[0]["spans"][0]["text"] = "não aplica"
    body = {"page": 1, "text": json.dumps(data), "format": s.format().__dict__, "highlight": highlight}
    r = c.post("/api/render/page", json=body)
    assert r.status_code == 400 and "highlight" in r.json()["error"]
    assert not s.dirty and "não aplica" not in s.page_text(1)


@pytest.mark.parametrize("page", [True, 0.9, 1.5, "1.0"])
def test_page_must_be_an_integer(api, page):
    s, c = api()
    r = c.post("/api/save", json={"page": page, "text": s.page_text(1), "format": s.format().__dict__})
    assert r.status_code == 400 and "'page'" in r.json()["error"]
    assert c.post("/api/save", json={"page": 1.0, "text": s.page_text(1), "format": s.format().__dict__}).status_code == 200


def test_negative_image_index_is_404(api):
    s, c = api()
    r = c.post("/api/render/page", json={"page": 1, "text": s.page_text(1), "format": s.format().__dict__})
    token = r.json()["images"][0].split("/")[2]
    assert c.get(f"/img/{token}/0.png").status_code == 200
    assert c.get(f"/img/{token}/-1.png").status_code == 404


def test_save_after_the_document_was_deleted_is_a_conflict(api, tmp_path):
    s, c = api()
    (tmp_path / "document.json").rename(tmp_path / "outro.json")
    r = c.post("/api/save", json={"page": 1, "text": s.page_text(1), "format": s.format().__dict__})
    assert r.status_code == 409 and r.json()["conflict"] is True
    assert "apagado ou renomeado" in r.json()["error"]


def test_render_all_writes_the_html_like_cli_render(api, tmp_path):
    s, c = api()
    html = tmp_path / "document.html"
    r = c.post("/api/render/all", json={"page": 1, "text": s.page_text(1), "format": s.format().__dict__}).json()
    assert r["pdf"] == str((tmp_path / "out.pdf").resolve())
    editor_html = html.read_text(encoding="utf-8")
    assert 'id="b0"' not in editor_html
    html.unlink()
    assert cli.main(["render", str(tmp_path / "document.json"), "-o", str(tmp_path / "cli.pdf")]) == 0
    assert html.read_text(encoding="utf-8") == editor_html
