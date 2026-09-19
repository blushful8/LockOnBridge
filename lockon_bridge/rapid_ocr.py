"""RapidOCR (ONNX) digit reader for reward ROI crops — free Apache-2.0."""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

log = logging.getLogger("lockon_bridge.rapid")

_engine: Any | None = None
_engine_lock = threading.Lock()
_engine_failed = False


def rapidocr_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        import onnxruntime  # noqa: F401

        return not _engine_failed
    except ImportError:
        return False


def _get_engine() -> Any | None:
    global _engine, _engine_failed
    if _engine_failed:
        return None
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        if _engine_failed:
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
