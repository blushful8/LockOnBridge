from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from . import __version__
from .settings import load_settings, update_settings

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
            self.send_header("Access-Control-Allow-Methods", "GET, PUT, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._send(204)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path in ("/v1/health", "/health"):
                settings = load_settings()
                self._send(
                    200,
                    {
                        "ok": True,
                        "version": __version__,
                        "hasPremiumAccount": settings.has_premium_account,
                    },
                )
                return
            if path in ("/v1/preferences", "/preferences"):
                settings = load_settings()
                self._send(200, {"hasPremiumAccount": settings.has_premium_account})
                return
            if path in ("/v1/reports", "/reports"):
                reports = store.list_reports()
                self._send(200, {"reports": [r.to_json() for r in reports]})
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

        def do_PUT(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path not in ("/v1/preferences", "/preferences"):
                self._send(404, {"error": "not_found"})
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send(400, {"error": "invalid_json"})
                return
            if not isinstance(payload, dict):
                self._send(400, {"error": "invalid_json"})
                return
            if "hasPremiumAccount" not in payload:
                self._send(400, {"error": "missing_hasPremiumAccount"})
                return
            updated = update_settings(has_premium_account=bool(payload.get("hasPremiumAccount")))
            self._send(200, {"hasPremiumAccount": updated.has_premium_account})

    return Handler


def serve(store: ReportStore, host: str = "0.0.0.0", port: int = 8112) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(store))
    return server
