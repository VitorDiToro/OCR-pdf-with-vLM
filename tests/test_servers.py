from __future__ import annotations

import signal
import socket
import subprocess
import sys
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


def mock_client(calls: list[str], state: dict[str, bool | str]) -> httpx.Client:
    """state[porta] = dorme? (bool), "simples" (sem modo sleep: 404) ou "fora" (conexão recusada);
    registra 'MÉTODO porta /caminho' das requisições atendidas e atualiza o estado."""
    def handler(req: httpx.Request) -> httpx.Response:
        port = str(req.url.port)
        if state[port] == "fora":
            raise httpx.ConnectError("recusado", request=req)
        calls.append(f"{req.method} {port} {req.url.path}")
        if state[port] == "simples":
            return httpx.Response(404)
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


def test_sleep_switcher_accepts_plain_ocr_server_alone():
    # dots do serve_vllm.sh simples (sem /is_sleeping) e nada na 8001: segue sem trocar nada
    calls, state = [], {"8000": "simples", "8001": "fora"}
    SleepSwitcher(ROOTS, client=mock_client(calls, state)).use("ocr")
    assert calls == ["GET 8000 /is_sleeping"]


def test_sleep_switcher_wakes_target_and_sleeps_other():
    # o par como o serve_pair.sh o deixa: dots dormindo, 2º modelo acordado
    calls, state = [], {"8000": True, "8001": False}
    SleepSwitcher(ROOTS, client=mock_client(calls, state)).use("ocr")
    assert calls == ["GET 8000 /is_sleeping", "GET 8001 /is_sleeping",
                     "POST 8001 /sleep", "POST 8000 /wake_up"]
    assert state == {"8000": False, "8001": True}


def test_sleep_switcher_refuses_other_awake_server_without_sleep_mode():
    calls, state = [], {"8000": True, "8001": "simples"}
    with pytest.raises(RuntimeError, match="não cabem na GPU.*serve_pair.sh"):
        SleepSwitcher(ROOTS, client=mock_client(calls, state)).use("ocr")
    assert not any(c.startswith("POST") for c in calls)


def test_sleep_switcher_ignores_other_without_sleep_mode_when_target_is_awake():
    # dots simples (serve_vllm.sh) e outro serviço qualquer na 8001 respondendo 404: nada a acordar,
    # nada disputa a VRAM por causa da troca; antes, abortava com "acordado e não tem modo sleep"
    calls, state = [], {"8000": "simples", "8001": "simples"}
    SleepSwitcher(ROOTS, client=mock_client(calls, state)).use("ocr")
    assert calls == ["GET 8000 /is_sleeping", "GET 8001 /is_sleeping"]


def test_sleep_switcher_unreachable_target_keeps_hint():
    calls, state = [], {"8000": "simples", "8001": "fora"}
    with pytest.raises(RuntimeError, match="serve_pair.sh"):
        SleepSwitcher(ROOTS, client=mock_client(calls, state)).use("italic")


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


def test_restart_switcher_passes_role_env(tmp_path):
    # o servidor do 2º modelo sobe com variáveis próprias (limite de imagem etc.); o do OCR, sem elas
    script = tmp_path / "serve.sh"
    script.write_text(f'#!/usr/bin/env bash\necho "MAX_LEN=${{MAX_LEN:-}}"\nexec "{FAKE_SERVE}" "$@"\n')
    script.chmod(0o755)
    sw = RestartSwitcher({"ocr": ("m1", _free_port()), "italic": ("m2", _free_port())}, script, tmp_path,
                         ready_timeout=30, env={"italic": {"MAX_LEN": "8192"}})
    try:
        sw.use("ocr")
        sw.use("italic")
    finally:
        sw.close()
    assert "MAX_LEN=\n" in (tmp_path / "ocr.log").read_text()
    assert "MAX_LEN=8192\n" in (tmp_path / "italic.log").read_text()


_SIGNALED = """
import sys, time
from pathlib import Path
from ocrpdf.cli import _signals_exit
from ocrpdf.servers import RestartSwitcher
sw = RestartSwitcher({"ocr": ("m", int(sys.argv[1]))}, Path(sys.argv[2]), Path(sys.argv[3]), ready_timeout=30)
with _signals_exit():
    try:
        sw.use("ocr")
        print("pronto", flush=True)
        time.sleep(60)
    finally:
        sw.close()
"""


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_signal_to_ocrpdf_stops_the_server(tmp_path, sig):
    # kill, terminal fechado: sem tratar o sinal, o finally não roda e o vLLM fica órfão na GPU
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-c", _SIGNALED, str(port), str(FAKE_SERVE), str(tmp_path)],
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "pronto" and _up(port)
        proc.send_signal(sig)
        assert proc.wait(timeout=30) == 128 + sig
        assert _down(port)
    finally:
        proc.kill()


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
