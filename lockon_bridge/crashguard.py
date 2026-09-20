"""Crash breadcrumbs, faulthandler, and unhandled-exception reports."""

from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TextIO

from . import __version__
from .paths import log_dir

log = logging.getLogger("lockon_bridge.crash")

_installed = False
_fault_fp: TextIO | None = None


class FlushingRotatingFileHandler(RotatingFileHandler):
    """Flush after every record so a hard AV still leaves the last lines on disk."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        try:
            self.flush()
        except Exception:  # noqa: BLE001
            pass


def crash_dir() -> Path:
    path = log_dir() / "crashes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def breadcrumb(message: str) -> None:
    """Overwrite a single breadcrumb file — last step before a hard kill."""
    try:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text = f"{stamp} pid={os.getpid()} v={__version__} | {message}\n"
        (log_dir() / "breadcrumb.txt").write_text(text, encoding="utf-8")
        log.info("breadcrumb: %s", message)
    except Exception:  # noqa: BLE001
        pass


def write_crash_report(kind: str, detail: str) -> Path | None:
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = crash_dir() / f"crash_{stamp}_{kind}.txt"
        bread = ""
        try:
            bread = (log_dir() / "breadcrumb.txt").read_text(encoding="utf-8")
        except OSError:
            bread = "(no breadcrumb)"
        body = (
            f"LockOn Bridge crash report\n"
            f"version={__version__}\n"
            f"pid={os.getpid()}\n"
            f"kind={kind}\n"
            f"time_utc={stamp}\n"
            f"executable={sys.executable}\n"
            f"frozen={getattr(sys, 'frozen', False)}\n"
            f"argv={sys.argv!r}\n"
            f"\n--- breadcrumb ---\n{bread}\n"
            f"--- detail ---\n{detail}\n"
        )
        path.write_text(body, encoding="utf-8")
        # Keep a stable pointer to the newest report.
        (crash_dir() / "last_crash.txt").write_text(body, encoding="utf-8")
        log.error("crash report written → %s", path)
        return path
    except Exception as exc:  # noqa: BLE001
        try:
            log.error("could not write crash report: %s", exc)
        except Exception:  # noqa: BLE001
            pass
        return None


def _excepthook(exc_type, exc, tb) -> None:  # noqa: ANN001
    detail = "".join(traceback.format_exception(exc_type, exc, tb))
    write_crash_report("uncaught", detail)
    sys.__excepthook__(exc_type, exc, tb)


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    detail = "".join(
        traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
    )
    name = args.thread.name if args.thread else "?"
    write_crash_report(f"thread-{name}", detail)
    if hasattr(threading, "__excepthook__"):
        threading.__excepthook__(args)  # type: ignore[misc]


def install_crash_guard() -> None:
    """Call once at process start (before OCR / GUI heavy imports)."""
    global _installed, _fault_fp
    if _installed:
        return
    _installed = True
    try:
        log_dir().mkdir(parents=True, exist_ok=True)
        fault_path = crash_dir() / "faulthandler.log"
        _fault_fp = open(fault_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        faulthandler.enable(file=_fault_fp, all_threads=True)
        # Dump on common hard-fault signals when the platform supports it.
        if hasattr(faulthandler, "register") and hasattr(__import__("signal"), "SIGTERM"):
            try:
                import signal

                faulthandler.register(signal.SIGTERM, file=_fault_fp, all_threads=True)
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"faulthandler setup failed: {exc}\n")

    sys.excepthook = _excepthook
    if hasattr(threading, "excepthook"):
        threading.excepthook = _thread_excepthook  # type: ignore[assignment]

    breadcrumb("process start")
