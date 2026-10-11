from __future__ import annotations

import json
import hmac
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from . import __version__
from .security import identity, tls_context
from .settings import load_settings, update_settings

if TYPE_CHECKING:
    from .match_session import MatchSession
    from .report_store import ReportStore


def make_handler(store: ReportStore, session: MatchSession | None = None, *, token: str):
    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            self.request.settimeout(5.0)
            self.request.do_handshake()
            super().setup()

        def _authorize(self) -> bool:
            values = self.headers.get_all("Authorization", [])
            expected = ("Bearer " + token).encode("ascii")
            if len(values) == 1 and hmac.compare_digest(values[0].encode("utf-8"), expected):
                return True
            self.close_connection = True
            self._send(401, {"error": "pairing_required"})
            return False

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            print(f"[http] {self.address_string()} {format % args}")

        def _send(self, code: int, payload: dict | None = None) -> None:
            body = b"" if payload is None else json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._send(405, {"error": "method_not_allowed"})

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorize():
                return
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
            if path in ("/v1/session", "/session"):
                if session is None:
                    self._send(404, {"error": "not_found"})
                    return
                self._send(200, session.view().to_json())
                return
            if path in ("/v1/reports", "/reports"):
                reports = store.list_reports()
                self._send(200, {"reports": [r.to_json() for r in reports]})
                return
            if path in ("/v1/latest-report", "/latest-report"):
                report = store.latest()
                if report is None:
                    self._send(204)
                    return
                self._send(200, report.to_json())
                return
            self._send(404, {"error": "not_found"})

        def do_PUT(self) -> None:  # noqa: N802
            if not self._authorize():
                return
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path not in ("/v1/preferences", "/preferences"):
                self._send(404, {"error": "not_found"})
                return
            lengths = self.headers.get_all("Content-Length", [])
            try:
                if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                    raise ValueError("Invalid body framing")
                length = int(lengths[0])
                if length < 0:
                    raise ValueError("Negative body length")
            except ValueError:
                self.close_connection = True
                self._send(400, {"error": "invalid_content_length"})
                return
            if length > 4096:
                self.close_connection = True
                self._send(413, {"error": "body_too_large"})
                return
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
            if not isinstance(payload["hasPremiumAccount"], bool):
                self._send(400, {"error": "invalid_hasPremiumAccount"})
                return
            updated = update_settings(has_premium_account=payload["hasPremiumAccount"])
            self._send(200, {"hasPremiumAccount": updated.has_premium_account})

    return Handler


def serve(
    store: ReportStore,
    host: str = "0.0.0.0",
    port: int = 8112,
    session: MatchSession | None = None,
) -> ThreadingHTTPServer:
    material = identity()
    context = tls_context(material)

    class SecureServer(ThreadingHTTPServer):
        daemon_threads = True
        slots = threading.BoundedSemaphore(16)

        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(5.0)
            try:
                return context.wrap_socket(connection, server_side=True, do_handshake_on_connect=False), address
            except Exception:
                connection.close()
                raise

        def process_request(self, request, client_address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except Exception:
                self.slots.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.slots.release()

        def handle_error(self, request, client_address):
            # Untrusted/malformed TLS clients must not flood logs with tracebacks.
            pass

    return SecureServer((host, port), make_handler(store, session, token=material["token"]))
