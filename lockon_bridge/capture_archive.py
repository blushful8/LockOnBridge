"""Persist OCR frames for offline ROI pair tuning (and optional auto-learn)."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from PIL import Image

from .paths import data_root

log = logging.getLogger("lockon_bridge.capture_archive")

_MAX_CAPTURES = 40
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def captures_dir() -> Path:
    return data_root() / "captures"


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _prune(folder: Path, *, keep: int = _MAX_CAPTURES) -> None:
    files = sorted(
        (p for p in folder.glob("*.png") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def archive_capture_frame(
    image: Image.Image,
    *,
    kind: str,
    rp: int | None = None,
    sl: int | None = None,
    note: str = "",
) -> Path | None:
    """
    Save a full client frame under ``%LocalAppData%\\LockOnBridge\\captures\\``.

    ``kind``: ``fail`` (calib/OCR miss), ``ok`` (settled publish), ``sample``.
    Keeps the newest ``_MAX_CAPTURES`` PNGs.
    """
    folder = captures_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("captures dir: %s", exc)
        return None

    parts = [_stamp(), _SAFE.sub("_", (kind or "sample").strip()[:16] or "sample")]
    if rp is not None and sl is not None:
        parts.append(f"rp{int(rp)}_sl{int(sl)}")
    if note:
        parts.append(_SAFE.sub("_", note.strip())[:40])
    path = folder / ("_".join(parts) + ".png")
    try:
        rgb = image if image.mode == "RGB" else image.convert("RGB")
        rgb.save(path, format="PNG", optimize=True)
        _prune(folder)
        log.info("archived capture %s (%s)", path.name, kind)
        return path
    except OSError as exc:
        log.warning("archive capture failed: %s", exc)
        return None


def latest_capture_path(*, kind: str | None = None) -> Path | None:
    """Newest PNG in captures/, optionally filtered by kind substring in name."""
    folder = captures_dir()
    if not folder.is_dir():
        return None
    files = [
        p
        for p in folder.glob("*.png")
        if p.is_file() and (kind is None or f"_{kind}_" in f"_{p.stem}_")
    ]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def preferred_fail_image_path() -> Path | None:
    """Best starting point for calibrator: newest fail archive, else error_parse.png."""
    from .paths import error_parse_image_path

    latest = latest_capture_path(kind="fail")
    if latest is not None and latest.is_file():
        return latest
    err = error_parse_image_path()
    if err.is_file():
        return err
    return latest_capture_path()
