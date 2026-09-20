"""RapidOCR (ONNX) digit reader — only inside OCR worker / probe children.

onnxruntime can ACCESS_VIOLATION (MSVCP140) the whole process on some GPUs.
The Bridge UI / agent process must NEVER import rapidocr_onnxruntime or
onnxruntime. Set ``LOCKON_ALLOW_RAPIDOCR=1`` only in isolated workers.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image

from .paths import data_root

log = logging.getLogger("lockon_bridge.rapid")

_engine: Any | None = None
_engine_lock = threading.Lock()
_engine_failed = False
_probe_done = False

_ALLOW_ENV = "LOCKON_ALLOW_RAPIDOCR"
_STATUS_NAME = "rapidocr_status.json"


def _rapidocr_allowed() -> bool:
    return os.environ.get(_ALLOW_ENV, "").strip() == "1"


def _status_path() -> Path:
    return data_root() / _STATUS_NAME


def read_rapidocr_status() -> dict[str, Any] | None:
    path = _status_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def write_rapidocr_status(*, ok: bool, detail: str = "") -> None:
    payload = {
        "ok": bool(ok),
        "detail": detail[:500],
        "checked_at": time.time(),
        "pid": os.getpid(),
    }
    path = _status_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        log.warning("could not write RapidOCR status: %s", exc)


def rapidocr_available() -> bool:
    """True only when this process may use RapidOCR and status is not banned."""
    if not _rapidocr_allowed():
        return False
    if _engine_failed:
        return False
    status = read_rapidocr_status()
    if status is not None and status.get("ok") is False:
        return False
    return True


def _probe_rapidocr_subprocess() -> bool:
    """
    onnxruntime can AV-crash the whole process on some GPUs/drivers.
    Probe in a child so the Bridge UI / OCR worker survives.
    """
    env = os.environ.copy()
    env[_ALLOW_ENV] = "1"
    env["LOCKON_RAPIDOCR_PROBE"] = "1"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--rapidocr-probe"]
        else:
            cmd = [sys.executable, "-m", "lockon_bridge", "--rapidocr-probe"]
        log.info("RapidOCR probe starting (subprocess)")
        proc = subprocess.run(
            cmd,
            timeout=90,
            env=env,
            creationflags=creationflags,
            capture_output=True,
        )
        ok = proc.returncode == 0
        if not ok:
            detail = (proc.stderr or proc.stdout or b"")[:400]
            log.warning(
                "RapidOCR probe failed (code=%s): %s",
                proc.returncode,
                detail,
            )
            write_rapidocr_status(ok=False, detail=f"probe exit {proc.returncode}")
        else:
            write_rapidocr_status(ok=True, detail="probe ok")
            log.info("RapidOCR probe OK")
        return ok
    except Exception as exc:  # noqa: BLE001
        log.warning("RapidOCR probe error: %s", exc)
        write_rapidocr_status(ok=False, detail=str(exc))
        return False


def run_rapidocr_probe_main() -> int:
    """Entry for ``--rapidocr-probe`` — exit 0 if engine constructs cleanly."""
    os.environ[_ALLOW_ENV] = "1"
    try:
        from rapidocr_onnxruntime import RapidOCR

        RapidOCR()
        write_rapidocr_status(ok=True, detail="probe main ok")
        return 0
    except Exception as exc:  # noqa: BLE001
        write_rapidocr_status(ok=False, detail=str(exc))
        print(f"rapidocr probe failed: {exc}", file=sys.stderr)
        return 1


def _get_engine() -> Any | None:
    global _engine, _engine_failed, _probe_done
    if not _rapidocr_allowed():
        return None
    if _engine_failed:
        return None
    status = read_rapidocr_status()
    if status is not None and status.get("ok") is False:
        _engine_failed = True
        return None
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        if _engine_failed:
            return None
        if not _probe_done:
            _probe_done = True
            # Skip nested probe when we ARE the probe child.
            if os.environ.get("LOCKON_RAPIDOCR_PROBE") == "1":
                pass
            elif status is None or "ok" not in status:
                if not _probe_rapidocr_subprocess():
                    _engine_failed = True
                    log.warning(
                        "RapidOCR disabled after unsafe probe — using Win/Tess only"
                    )
                    return None
            elif status.get("ok") is False:
                _engine_failed = True
                return None
        try:
            from rapidocr_onnxruntime import RapidOCR

            _engine = RapidOCR()
            log.info("RapidOCR engine ready (worker process)")
            return _engine
        except Exception as exc:  # noqa: BLE001
            _engine_failed = True
            write_rapidocr_status(ok=False, detail=str(exc))
            log.warning("RapidOCR unavailable: %s", exc)
            return None


def rapidocr_digits_text(image: Image.Image) -> str:
    """
    Read digit text from a small reward crop.

    Soft colour upscale first; HSV bright-text mask only when soft yields fewer
    than two reward-sized amounts (keeps the happy path fast).

    No-op in the Bridge main process (``LOCKON_ALLOW_RAPIDOCR`` unset).
    """
    if not _rapidocr_allowed():
        return ""
    engine = _get_engine()
    if engine is None:
        return ""
    try:
        import numpy as np
    except ImportError:
        return ""

    from .ocr_parse import _amounts_in
    from .ocr_preprocess import preprocess_variants

    best = ""
    best_score = -1
    best_multi = ""
    for _tag, prepared in preprocess_variants(image):
        arr = np.asarray(prepared.convert("RGB"))
        try:
            result, _elapse = engine(arr)
        except Exception as exc:  # noqa: BLE001
            log.debug("RapidOCR failed (%s): %s", _tag, exc)
            continue
        if not result:
            continue
        parts: list[str] = []
        for item in result:
            if not item or len(item) < 2:
                continue
            text = str(item[1]).strip()
            if not text:
                continue
            cleaned = re.sub(r"[^\d\s]+", " ", text)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                parts.append(cleaned)
        joined = " ".join(parts)
        if not joined:
            continue
        score = sum(ch.isdigit() for ch in joined) * 10 + len(joined)
        if score > best_score:
            best_score = score
            best = joined
        if len(_amounts_in(joined, min_value=50)) >= 2:
            best_multi = joined
            if _tag == "soft":
                return joined
    return best_multi or best
