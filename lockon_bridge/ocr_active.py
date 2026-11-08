"""Process-wide record of which OCR provider last ran a debrief read."""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_state: dict[str, Any] = {
    "provider": "",  # ocrspace | easyocr | ""
    "reason": "",  # preferred | fallback | selected | ""
    "updated_at": 0.0,
}


def note_ocr_provider(provider: str, *, reason: str = "") -> None:
    """Remember the engine that produced the latest layout OCR text."""
    name = (provider or "").strip().lower()
    if name in ("ocr.space", "cloud"):
        name = "ocrspace"
    if name not in ("ocrspace", "easyocr"):
        return
    why = (reason or "").strip().lower()
    with _lock:
        _state["provider"] = name
        _state["reason"] = why
        _state["updated_at"] = time.time()


def last_ocr_provider() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def provider_label(provider: str) -> str:
    name = (provider or "").strip().lower()
    if name == "easyocr":
        return "EasyOCR"
    if name in ("ocrspace", "ocr.space", "cloud"):
        return "OCR.space"
    return ""
