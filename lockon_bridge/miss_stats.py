"""Local counters + recent events for OCR/reward misses.

Persists under the Bridge data root so we can see *why* rewards failed
without digging through full OCR dumps.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from .paths import data_root

log = logging.getLogger("lockon_bridge.miss_stats")

_LOCK = threading.Lock()
_MAX_RECENT = 40


def _path() -> Path:
    return data_root() / "miss_stats.json"


def _empty() -> dict[str, Any]:
    return {
        "total": 0,
        "byReason": {},
        "recent": [],
        "updatedAtEpochMillis": 0,
    }


def _load_unlocked() -> dict[str, Any]:
    path = _path()
    if not path.is_file():
        return _empty()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return _empty()
        by_reason = raw.get("byReason")
        recent = raw.get("recent")
        return {
            "total": int(raw.get("total") or 0),
            "byReason": dict(by_reason) if isinstance(by_reason, dict) else {},
            "recent": list(recent) if isinstance(recent, list) else [],
            "updatedAtEpochMillis": int(raw.get("updatedAtEpochMillis") or 0),
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        log.debug("miss_stats load failed: %s", exc)
        return _empty()


def _save_unlocked(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def normalize_reason(reason: str) -> str:
    text = (reason or "").strip().lower().replace(" ", "-")
    cleaned = "".join(
        ch for ch in text if ch.isalnum() or ch in "-_:.,"
    )
    return (cleaned or "unknown")[:80]


def record_miss(
    reason: str,
    *,
    session_id: str = "",
    detail: str = "",
) -> dict[str, Any]:
    """Increment counters and append a recent event. Returns snapshot."""
    code = normalize_reason(reason)
    stamp = int(time.time() * 1000)
    event = {
        "atEpochMillis": stamp,
        "reason": code,
        "sessionId": (session_id or "").strip()[:64],
        "detail": (detail or "").strip()[:240],
    }
    with _LOCK:
        data = _load_unlocked()
        data["total"] = int(data.get("total") or 0) + 1
        by_reason = data.setdefault("byReason", {})
        if not isinstance(by_reason, dict):
            by_reason = {}
            data["byReason"] = by_reason
        by_reason[code] = int(by_reason.get(code) or 0) + 1
        recent = data.setdefault("recent", [])
        if not isinstance(recent, list):
            recent = []
            data["recent"] = recent
        recent.insert(0, event)
        del recent[_MAX_RECENT:]
        data["updatedAtEpochMillis"] = stamp
        try:
            _save_unlocked(data)
        except OSError as exc:
            log.warning("miss_stats save failed: %s", exc)
        return dict(data)


def snapshot() -> dict[str, Any]:
    with _LOCK:
        return _load_unlocked()
