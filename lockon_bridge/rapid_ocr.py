"""RapidOCR (ONNX) digit reader for reward ROI crops — free Apache-2.0."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
from typing import Any

from PIL import Image

log = logging.getLogger("lockon_bridge.rapid")

_engine: Any | None = None
_engine_lock = threading.Lock()
_engine_failed = False
_probe_done = False


def rapidocr_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        import onnxruntime  # noqa: F401

        return not _engine_failed
    except ImportError:
        return False


def _probe_rapidocr_subprocess() -> bool:
    """
    onnxruntime can AV-crash the whole process on some GPUs/drivers.
    Probe in a child so the Bridge UI survives.
    """
    env = os.environ.copy()
    env["LOCKON_RAPIDOCR_PROBE"] = "1"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--rapidocr-probe"]
        else:
            cmd = [sys.executable, "-m", "lockon_bridge", "--rapidocr-probe"]
        proc = subprocess.run(
            cmd,
            timeout=90,
            env=env,
            creationflags=creationflags,
            capture_output=True,
        )
        ok = proc.returncode == 0
        if not ok:
            log.warning(
                "RapidOCR probe failed (code=%s): %s",
                proc.returncode,
                (proc.stderr or proc.stdout or b"")[:400],
            )
        return ok
    except Exception as exc:  # noqa: BLE001
        log.warning("RapidOCR probe error: %s", exc)
        return False


def run_rapidocr_probe_main() -> int:
    """Entry for ``--rapidocr-probe`` — exit 0 if engine constructs cleanly."""
    try:
        from rapidocr_onnxruntime import RapidOCR

        RapidOCR()
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"rapidocr probe failed: {exc}", file=sys.stderr)
        return 1


def _get_engine() -> Any | None:
    global _engine, _engine_failed, _probe_done
    if _engine_failed:
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
            elif not _probe_rapidocr_subprocess():
                _engine_failed = True
                log.warning("RapidOCR disabled after unsafe probe — using Win/Tess only")
                return None
        try:
            from rapidocr_onnxruntime import RapidOCR

            _engine = RapidOCR()
            log.info("RapidOCR engine ready")
            return _engine
        except Exception as exc:  # noqa: BLE001
            _engine_failed = True
            log.warning("RapidOCR unavailable: %s", exc)
            return None


def rapidocr_digits_text(image: Image.Image) -> str:
    """
    Read digit text from a small reward crop.

    Soft colour upscale first; HSV bright-text mask only when soft yields fewer
    than two reward-sized amounts (keeps the happy path fast).
    """
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
