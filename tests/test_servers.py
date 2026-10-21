from __future__ import annotations

import socket
import time
from pathlib import Path

import httpx
import pytest

from ocrpdf.servers import (
    NullSwitcher,
    RestartSwitcher,
    SleepSwitcher,
    default_script,
)

FAKE_SERVE = Path(__file__).parent / "fake_serve.sh"
ROOTS = {"ocr": "http://127.0.0.1:8000", "italic": "http://127.0.0.1:8001"}


def mock_client(calls: list[str], state: dict[str, bool]) -> httpx.Client:
    """state[porta] = dorme?; registra 'MÉTODO porta /caminho' e atualiza o estado."""
    def handler(req: httpx.Request) -> httpx.Response:
        port = str(req.url.port)
        calls.append(f"{req.method} {port} {req.url.path}")
        if req.url.path == "/is_sleeping":
            return httpx.Response(200, json={"is_sleeping": state[port]})
        if req.url.path == "/sleep":
            assert req.url.params["level"] == "1"
            state[port] = True
        elif req.url.path == "/wake_up":
            state[port] = False
        return httpx.Response(200)
    return httpx.Client(transport=httpx.MockTransport(handler))


def refusing_client() -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("recusado", request=req)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sleep_switcher_alternates():
    calls, state = [], {"8000": False, "8001": True}
    sw = SleepSwitcher(ROOTS, client=mock_client(calls, state))
    sw.use("italic")
    assert calls == ["GET 8000 /is_sleeping", "GET 8001 /is_sleeping",
                     "POST 8000 /sleep", "POST 8001 /wake_up"]
    calls.clear()
    sw.use("italic")
    assert all(c.startswith("GET") for c in calls)


def test_sleep_switcher_reports_missing_server():
    with pytest.raises(RuntimeError, match="serve_pair.sh"):
        SleepSwitcher(ROOTS, client=refusing_client()).use("ocr")


def test_sleep_switcher_close_keeps_servers():
    calls, state = [], {"8000": False, "8001": True}
    sw = SleepSwitcher(ROOTS, client=mock_client(calls, state))
    sw.close()
    assert calls == []


def test_null_switcher_does_nothing():
    sw = NullSwitcher()
    sw.use("ocr")
    sw.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _up(port: int) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


def _down(port: int, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not _up(port):
            return True
        time.sleep(0.1)
    return False


def test_restart_switcher_starts_and_stops(tmp_path):
    p1, p2 = _free_port(), _free_port()
    sw = RestartSwitcher({"ocr": ("m1", p1), "italic": ("m2", p2)}, FAKE_SERVE, tmp_path,
                         ready_timeout=30)
    try:
        sw.use("ocr")
        assert _up(p1)
        sw.use("italic")
        assert _up(p2) and _down(p1)
    finally:
        sw.close()
    assert _down(p2)


def test_restart_switcher_reports_dead_process(tmp_path):
    script = tmp_path / "morre.sh"
    script.write_text("#!/usr/bin/env bash\necho falhou-ao-subir\nexit 3\n")
    script.chmod(0o755)
    sw = RestartSwitcher({"ocr": ("m", _free_port())}, script, tmp_path, ready_timeout=10)
    try:
        with pytest.raises(RuntimeError, match="falhou-ao-subir"):
            sw.use("ocr")
    finally:
        sw.close()


def test_default_script_exists():
    assert default_script().name == "serve_vllm.sh"
