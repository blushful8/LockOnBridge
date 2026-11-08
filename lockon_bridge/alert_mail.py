"""Rate-limited failure emails for LockOn Bridge operators.

SMTP credentials (Gmail App Password) live in a DPAPI blob under LocalAppData.
Without them, alerts are still written to ``logs/alerts/`` so nothing is lost.
Outbound SMTP to ``smtp.gmail.com`` is ordinary client mail traffic — antivirus
rarely flags it; what they chase is credential theft / process injection, not
error reports.
"""

from __future__ import annotations

import json
import logging
import smtplib
import threading
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from . import __version__
from .paths import data_root, log_dir

log = logging.getLogger("lockon_bridge.alert_mail")

# XOR-obfuscated default recipient (not credentials). Plaintext stays out of source/git.
_ALERT_TO_KEY = b"LockOnBridge.alert-to.v1"
_ALERT_TO_BLOB = bytes.fromhex(
    "28060e002e40360b1c101e10400f050e32134015064258522302"
)
_SECRET_NAME = "alert_mail.bin"
_SECRET_ENTROPY = b"LockOnBridge.alert-mail.v1"
_MIN_INTERVAL_SEC = 15 * 60
_lock = threading.Lock()
_last_sent: dict[str, float] = {}


def default_alert_to() -> str:
    """Decode the built-in alert recipient at runtime."""
    key = _ALERT_TO_KEY
    blob = _ALERT_TO_BLOB
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(blob)).decode("ascii")


def _secret_path() -> Path:
    return data_root() / _SECRET_NAME


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(buf: bytes) -> tuple[DATA_BLOB, Any]:
        raw = ctypes.create_string_buffer(buf, len(buf))
        blob = DATA_BLOB()
        blob.cbData = len(buf)
        blob.pbData = ctypes.cast(raw, ctypes.POINTER(ctypes.c_char))
        return blob, raw

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, _keep = _blob(data)
    entropy, _entropy_keep = _blob(_SECRET_ENTROPY)
    out = DATA_BLOB()
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            None,
            ctypes.byref(entropy),
            None,
            None,
            flags,
            ctypes.byref(out),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            ctypes.byref(entropy),
            None,
            None,
            flags,
            ctypes.byref(out),
        )
    if not ok:
        raise OSError("Windows DPAPI refused the alert-mail blob")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def store_smtp_config(
    *,
    user: str,
    password: str,
    to_addr: str | None = None,
    host: str = "smtp.gmail.com",
    port: int = 587,
) -> None:
    """Persist Gmail (or other) SMTP settings encrypted for this Windows user."""
    user = (user or "").strip()
    password = (password or "").strip()
    fallback = default_alert_to()
    to_addr = (to_addr or fallback).strip() or fallback
    if not user or not password:
        raise ValueError("SMTP user and app password are required")
    payload = {
        "v": 1,
        "host": host.strip() or "smtp.gmail.com",
        "port": int(port) or 587,
        "user": user,
        "password": password,
        "to": to_addr,
    }
    path = _secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_dpapi(json.dumps(payload).encode("utf-8"), protect=True))
    log.info("alert mail SMTP stored (DPAPI) → %s", to_addr)


def load_smtp_config() -> dict[str, Any] | None:
    path = _secret_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(_dpapi(path.read_bytes(), protect=False).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.warning("alert mail blob unreadable: %s", exc)
        return None
    if not isinstance(raw, dict):
        return None
    user = str(raw.get("user") or "").strip()
    password = str(raw.get("password") or "").strip()
    if not user or not password:
        return None
    return {
        "host": str(raw.get("host") or "smtp.gmail.com").strip(),
        "port": int(raw.get("port") or 587),
        "user": user,
        "password": password,
        "to": str(raw.get("to") or default_alert_to()).strip() or default_alert_to(),
    }


def smtp_configured() -> bool:
    return load_smtp_config() is not None


def _alert_dir() -> Path:
    path = log_dir() / "alerts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_local_copy(kind: str, subject: str, body: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in kind)[:40]
    path = _alert_dir() / f"{stamp}_{safe}.txt"
    path.write_text(f"{subject}\n\n{body}\n", encoding="utf-8")
    return path


def _should_send(kind: str) -> bool:
    now = time.monotonic()
    last = _last_sent.get(kind, 0.0)
    if now - last < _MIN_INTERVAL_SEC:
        return False
    _last_sent[kind] = now
    return True


def _send_smtp(cfg: dict[str, Any], subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["user"]
    msg["To"] = cfg["to"]
    msg.set_content(body)
    with smtplib.SMTP(cfg["host"], int(cfg["port"]), timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(cfg["user"], cfg["password"])
        smtp.send_message(msg)


class _ErrorMailHandler(logging.Handler):
    """Forward ERROR+ log records to email (rate-limited by logger name)."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno < logging.ERROR:
                return
            # Avoid mail loops / noise from the mailer itself.
            if record.name.startswith("lockon_bridge.alert_mail"):
                return
            msg = self.format(record)
            notify_failure(f"log-{record.name}", msg)
        except Exception:  # noqa: BLE001
            self.handleError(record)


_log_handler_installed = False


def install_error_mail_handler() -> None:
    """Attach a process-wide ERROR→email handler once."""
    global _log_handler_installed
    if _log_handler_installed:
        return
    root = logging.getLogger()
    for existing in root.handlers:
        if isinstance(existing, _ErrorMailHandler):
            _log_handler_installed = True
            return
    handler = _ErrorMailHandler(level=logging.ERROR)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    _log_handler_installed = True


def notify_failure(kind: str, detail: str, *, force: bool = False) -> None:
    """
    Email a Bridge failure (rate-limited per ``kind``).

    Always writes a local copy under ``logs/alerts/``. Sends mail only when
    SMTP is configured and the rate limit allows it (or ``force``).
    """
    kind = (kind or "failure").strip() or "failure"
    detail = (detail or "").strip() or "(no detail)"
    subject = f"[LockOn Bridge] {kind} (v{__version__})"
    body = (
        f"LockOn Bridge failure alert\n"
        f"version={__version__}\n"
        f"kind={kind}\n"
        f"time_utc={datetime.now(timezone.utc).isoformat()}\n"
        f"\n--- detail ---\n{detail}\n"
    )
    try:
        local = _write_local_copy(kind, subject, body)
        log.info("alert saved → %s", local)
    except Exception as exc:  # noqa: BLE001
        log.warning("alert local save failed: %s", exc)

    with _lock:
        if not force and not _should_send(kind):
            log.debug("alert mail suppressed (rate limit) kind=%s", kind)
            return
        cfg = load_smtp_config()
        if cfg is None:
            log.warning(
                "alert mail skipped — configure Gmail App Password "
                "(recipient %s)",
                default_alert_to(),
            )
            return
        try:
            _send_smtp(cfg, subject, body)
            log.info("alert mail sent → %s kind=%s", cfg["to"], kind)
        except Exception as exc:  # noqa: BLE001
            log.warning("alert mail send failed: %s", exc)
