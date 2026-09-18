from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from . import __version__

if TYPE_CHECKING:
    from .report_store import ReportStore


def make_handler(store: ReportStore):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A003
            print(f"[http] {self.address_string()} {format % args}")

        def _send(self, code: int, payload: dict | None = None) -> None:
            body = b"" if payload is None else json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path in ("/v1/health", "/health"):
                self._send(200, {"ok": True, "version": __version__})
                return
            if path in ("/v1/latest-report", "/latest-report"):
                report = store.latest()
                if report is None:
                    self.send_response(204)
                    self.end_headers()
                    return
                self._send(200, report.to_json())
                return
            self._send(404, {"error": "not_found"})

    return Handler


def serve(store: ReportStore, host: str = "0.0.0.0", port: int = 8112) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(store))
    return server
