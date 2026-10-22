import json
import threading

import httpx
import pytest

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
