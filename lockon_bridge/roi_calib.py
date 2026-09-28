"""The one results-table rectangle used by OCR.space and EasyOCR."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .paths import data_root
from .roi_layout import NormRect

log = logging.getLogger("lockon_bridge.roi_calib")

_CALIB_NAME = "roi_calibrated.json"


def _norm_from_dict(raw: Any, tag: str) -> NormRect | None:
    if not isinstance(raw, dict):
        return None
    try:
        rect = NormRect(
            left=float(raw["left"]),
            top=float(raw["top"]),
            right=float(raw["right"]),
            bottom=float(raw["bottom"]),
            tag=tag,
        ).clamp()
    except (KeyError, TypeError, ValueError):
        return None
    if rect.right - rect.left < 0.01 or rect.bottom - rect.top < 0.01:
        return None
    return rect


def _rect_to_dict(rect: NormRect) -> dict[str, float]:
    return {
        "left": round(rect.left, 5),
        "top": round(rect.top, 5),
        "right": round(rect.right, 5),
        "bottom": round(rect.bottom, 5),
    }


def default_parse_zone() -> NormRect:
    """
    One results-table crop for every user.

    Fractions of the WT client (resolution-independent). Padded so a small
    banner shift still keeps the reward table inside.
    """
    return NormRect(0.16, 0.12, 0.58, 0.68, "parse-zone")


def _zone_from_raw(raw: Any) -> tuple[int, NormRect] | None:
    if not isinstance(raw, dict):
        return None
    rect = _norm_from_dict(raw.get("zone"), "parse-zone")
    if rect is None:
        return None
    try:
        version = int(raw.get("version", 1))
    except (TypeError, ValueError):
        version = 1
    return version, rect


def packaged_calib_path() -> Path:
    return Path(__file__).resolve().parent / _CALIB_NAME


def user_calib_path() -> Path:
    return data_root() / _CALIB_NAME


def load_parse_zone() -> NormRect:
    """Shipped zone, overridden by a newer developer save in LocalAppData."""
    best_key = (-1, -1)
    best: NormRect | None = None
    for path, tie in ((user_calib_path(), 1), (packaged_calib_path(), 0)):
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        parsed = _zone_from_raw(raw)
        if parsed is None:
            continue
        version, rect = parsed
        key = (version, tie)
        if key > best_key:
            best_key = key
            best = rect
    return best if best is not None else default_parse_zone()


def save_parse_zone(rect: NormRect, *, also_package: bool = False) -> list[Path]:
    """
    Store one parse zone.

    Always writes LocalAppData. ``also_package`` also writes the repo file so
    the next release ships the same rectangle to every user.
    """
    zone = rect.clamp()
    payload_zone = _rect_to_dict(zone)

    def _write(path: Path) -> None:
        raw: dict[str, Any] = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    raw = loaded
            except (OSError, json.JSONDecodeError):
                raw = {}
        raw.pop("with", None)
        raw.pop("without", None)
        try:
            version = int(raw.get("version", 1))
        except (TypeError, ValueError):
            version = 1
        raw["version"] = max(version, 3)
        raw["zone"] = payload_zone
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    written: list[Path] = []
    user = user_calib_path()
    _write(user)
    written.append(user)
    if also_package:
        pkg = packaged_calib_path()
        _write(pkg)
        written.append(pkg)
    log.info("parse zone saved → %s", ", ".join(str(p) for p in written))
    return written
