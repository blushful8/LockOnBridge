"""Calibrated RP/SL ROI fractions — shipped for all users, editable in dev mode."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import data_root, is_frozen
from .roi_layout import NormRect

log = logging.getLogger("lockon_bridge.roi_calib")

_CALIB_NAME = "roi_calibrated.json"


@dataclass(frozen=True)
class ColumnRois:
    rp: NormRect
    sl: NormRect


@dataclass(frozen=True)
class CalibratedRois:
    version: int
    with_premium: ColumnRois
    without_premium: ColumnRois

    def column(self, *, prefer_with: bool) -> ColumnRois:
        return self.with_premium if prefer_with else self.without_premium


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


def _column_from_dict(raw: Any, *, prefix: str) -> ColumnRois | None:
    if not isinstance(raw, dict):
        return None
    rp = _norm_from_dict(raw.get("rp"), f"{prefix}-rp")
    sl = _norm_from_dict(raw.get("sl"), f"{prefix}-sl")
    if rp is None or sl is None:
        return None
    return ColumnRois(rp=rp, sl=sl)


def _rect_to_dict(rect: NormRect) -> dict[str, float]:
    return {
        "left": round(rect.left, 5),
        "top": round(rect.top, 5),
        "right": round(rect.right, 5),
        "bottom": round(rect.bottom, 5),
    }


def calibrated_to_dict(calib: CalibratedRois) -> dict[str, Any]:
    return {
        "version": int(calib.version),
        "with": {
            "rp": _rect_to_dict(calib.with_premium.rp),
            "sl": _rect_to_dict(calib.with_premium.sl),
        },
        "without": {
            "rp": _rect_to_dict(calib.without_premium.rp),
            "sl": _rect_to_dict(calib.without_premium.sl),
        },
    }


def calibrated_from_dict(raw: Any) -> CalibratedRois | None:
    if not isinstance(raw, dict):
        return None
    try:
        version = int(raw.get("version", 1))
    except (TypeError, ValueError):
        version = 1
    with_col = _column_from_dict(raw.get("with"), prefix="with")
    without_col = _column_from_dict(raw.get("without"), prefix="without")
    if with_col is None or without_col is None:
        return None
    return CalibratedRois(
        version=version,
        with_premium=with_col,
        without_premium=without_col,
    )


def packaged_calib_path() -> Path:
    return Path(__file__).resolve().parent / _CALIB_NAME


def user_calib_path() -> Path:
    return data_root() / _CALIB_NAME


def default_calibrated_rois() -> CalibratedRois:
    """Seed fractions from the live summary bands (dev will refine by dragging)."""
    return CalibratedRois(
        version=1,
        with_premium=ColumnRois(
            rp=NormRect(0.200, 0.150, 0.320, 0.205, "with-rp"),
            sl=NormRect(0.200, 0.210, 0.320, 0.265, "with-sl"),
        ),
        without_premium=ColumnRois(
            rp=NormRect(0.330, 0.150, 0.435, 0.205, "without-rp"),
            sl=NormRect(0.330, 0.210, 0.435, 0.265, "without-sl"),
        ),
    )


def load_calibrated_rois() -> CalibratedRois | None:
    """
    Load RP/SL fractions (0..1 of WT content_frame — scales across resolutions).

    - Source tree: LocalAppData first (live Save), then packaged repo file.
    - Frozen release: packaged (shipped in update) first, so auto-update applies
      for everyone; LocalAppData only if the package file is missing.
    """
    if is_frozen():
        candidates = (packaged_calib_path(), user_calib_path())
    else:
        candidates = (user_calib_path(), packaged_calib_path())
    for path in candidates:
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("ROI calib read failed (%s): %s", path, exc)
            continue
        calib = calibrated_from_dict(raw)
        if calib is not None:
            return calib
        log.warning("ROI calib invalid: %s", path)
    return None


def save_calibrated_rois(
    calib: CalibratedRois,
    *,
    also_package: bool = False,
) -> list[Path]:
    """
    Persist calibration.

    Always writes LocalAppData. When ``also_package`` (source tree), also writes
    the repo file so the next release ships it.
    """
    payload = json.dumps(calibrated_to_dict(calib), indent=2, ensure_ascii=False) + "\n"
    written: list[Path] = []
    user = user_calib_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(payload, encoding="utf-8")
    written.append(user)
    if also_package:
        pkg = packaged_calib_path()
        pkg.write_text(payload, encoding="utf-8")
        written.append(pkg)
    log.info("ROI calibration saved → %s", ", ".join(str(p) for p in written))
    return written


def has_usable_calibration() -> bool:
    return load_calibrated_rois() is not None


def calibrated_rects_for(
    image,
    *,
    prefer_with: bool,
) -> list[NormRect] | None:
    """Return [rp, sl] NormRects for the active column, or None."""
    del image  # fractions are resolution-independent; image kept for API symmetry
    calib = load_calibrated_rois()
    if calib is None:
        return None
    col = calib.column(prefer_with=prefer_with)
    return [col.rp, col.sl]
