"""Servidor falso para os testes do RestartSwitcher: GET /v1/models → 200 em 127.0.0.1:<porta>."""
from __future__ import annotations

import os
import signal
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        ok = self.path == "/v1/models"
        body = b'{"data": []}' if ok else b"{}"
        self.send_response(200 if ok else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silencioso
        pass


def main() -> None:
    server = HTTPServer(("127.0.0.1", int(sys.argv[1])), _Handler)
    # SIGINT/SIGTERM encerram sem traceback; FAKE_IGNORE_INT=1 imita um vLLM preso no encerramento
    ignore = os.environ.get("FAKE_IGNORE_INT") == "1"
    signal.signal(signal.SIGINT, signal.SIG_IGN if ignore else lambda *_: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print("pronto", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
