from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from circle_predict.dashboard import get_dashboard_payload
from circle_predict.env import load_env


ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = ROOT / "web"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 15121


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/dashboard":
            self._send_json(get_dashboard_payload())
            return
        if parsed.path == "/health":
            self._send_json({"status": "ok"})
            return
        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def _send_json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run(host: str | None = None, port: int | None = None) -> None:
    load_env()
    host = host or os.getenv("CIRCLEPREDICT_HOST", DEFAULT_HOST)
    port = port or int(os.getenv("CIRCLEPREDICT_PORT", str(DEFAULT_PORT)))
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"CirclePredict running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
