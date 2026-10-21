"""Alternância entre os servidores vLLM (OCR e 2º modelo) que dividem a mesma GPU.

Dois modos, ambos atrás da interface `Switcher`:
  - sleep  (padrão): os dois servidores ficam no ar (scripts/serve_pair.sh); só um fica acordado.
    A troca usa os endpoints de desenvolvimento do vLLM (`/sleep`, `/wake_up`, `/is_sleeping`),
    que ficam na raiz do servidor, não em /v1. Medido numa RTX 3060: dormir ~1 s, acordar ~1,3 s.
  - restart: a própria app sobe/derruba servidores executando scripts/serve_vllm.sh.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Protocol

import httpx

# Provisório; a escolha definitiva do 2º modelo é feita na Task 10.
ITALIC_HF_MODEL = "Qwen/Qwen3-VL-8B-Instruct-FP8"

_STOP_TIMEOUT = 90.0


class Switcher(Protocol):
    def use(self, role: str) -> None: ...   # "ocr" | "italic"
    def close(self) -> None: ...


class NullSwitcher:
    """Não faz nada: um único servidor atende tudo."""

    def use(self, role: str) -> None:
        pass

    def close(self) -> None:
        pass


class SleepSwitcher:
    """Alterna servidores já no ar com /sleep e /wake_up. `roots` não inclui /v1."""

    def __init__(self, roots: dict[str, str], client: httpx.Client | None = None):
        self.roots = {r: u.rstrip("/") for r, u in roots.items()}
        self._client = client or httpx.Client(timeout=120.0)

    def use(self, role: str) -> None:
        try:
            sleeping = {r: self._is_sleeping(u) for r, u in self.roots.items()}
            for r, u in self.roots.items():
                if r != role and not sleeping[r]:
                    self._post(u, "/sleep", params={"level": 1})
            if sleeping[role]:
                self._post(self.roots[role], "/wake_up")
        except httpx.TransportError as e:
            raise RuntimeError(
                f"servidor vLLM inacessível ({e}); suba o par com scripts/serve_pair.sh"
            ) from e

    def close(self) -> None:
        # No modo sleep os servidores sobrevivem entre execuções: nada a derrubar.
        pass

    def _is_sleeping(self, root: str) -> bool:
        r = self._client.get(f"{root}/is_sleeping")
        r.raise_for_status()
        return bool(r.json()["is_sleeping"])

    def _post(self, root: str, path: str, params: dict | None = None) -> None:
        self._client.post(f"{root}{path}", params=params).raise_for_status()


class RestartSwitcher:
    """Mantém um servidor por vez, iniciando-o com `script modelo porta`."""

    def __init__(self, models: dict[str, tuple[str, int]], script: Path, log_dir: Path,
                 ready_timeout: float = 900.0):
        self.models = models
        self.script = Path(script)
        self.log_dir = Path(log_dir)
        self.ready_timeout = ready_timeout
        self._proc: subprocess.Popen | None = None
        self._role: str | None = None

    def use(self, role: str) -> None:
        if role == self._role and self._proc is not None and self._proc.poll() is None:
            return
        self._stop()
        model, port = self.models[role]
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"{role}.log"
        with open(log_path, "wb") as log:
            self._proc = subprocess.Popen(
                [str(self.script), model, str(port)],
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
        self._role = role
        self._wait_ready(port, log_path)

    def close(self) -> None:
        self._stop()

    def _wait_ready(self, port: int, log_path: Path) -> None:
        url = f"http://127.0.0.1:{port}/v1/models"
        end = time.monotonic() + self.ready_timeout
        while True:
            if self._proc.poll() is not None:
                self._fail(f"o servidor terminou (código {self._proc.returncode})", log_path)
            try:
                if httpx.get(url, timeout=2.0).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if time.monotonic() > end:
                self._fail(f"servidor não ficou pronto em {self.ready_timeout:.0f} s", log_path)
            time.sleep(0.5)

    def _fail(self, msg: str, log_path: Path) -> None:
        self._stop()
        tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-20:])
        raise RuntimeError(f"{msg}. Fim de {log_path}:\n{tail}")

    def _stop(self) -> None:
        proc, self._proc, self._role = self._proc, None, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGINT)
                try:
                    proc.wait(timeout=_STOP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            # Filhos que sobraram no grupo (o vLLM cria processos de trabalho).
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass


def default_script() -> Path:
    script = Path(__file__).resolve().parents[2] / "scripts" / "serve_vllm.sh"
    if not script.is_file():
        raise FileNotFoundError(f"scripts/serve_vllm.sh não encontrado em {script}")
    return script
