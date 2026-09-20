"""Calibrated RP/SL ROI fractions — shipped for all users, editable in dev mode.

Schema v2: each premium column holds an ordered list of RP+SL pairs. OCR tries
pair 0, then 1, … until both cells read as usable numbers (UI layout can shift
when WT shows bonus banners, etc.). Legacy v1 ``{rp,sl}`` still loads as one pair.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import data_root
from .roi_layout import NormRect

log = logging.getLogger("lockon_bridge.roi_calib")

_CALIB_NAME = "roi_calibrated.json"
_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class RoiPair:
    rp: NormRect
    sl: NormRect


@dataclass(frozen=True)
class ColumnRois:
    """Ordered fallback stack: ``pairs[0]`` is primary, then alternatives."""

    pairs: tuple[RoiPair, ...]

    def __post_init__(self) -> None:
        if not self.pairs:
            raise ValueError("ColumnRois requires at least one RP/SL pair")

    @property
    def rp(self) -> NormRect:
        return self.pairs[0].rp

    @property
    def sl(self) -> NormRect:
        return self.pairs[0].sl

    def pair_at(self, index: int) -> RoiPair:
        return self.pairs[index]

    def replace_pair(self, index: int, pair: RoiPair) -> ColumnRois:
        items = list(self.pairs)
        items[index] = pair
        return ColumnRois(pairs=tuple(items))

    def replace_cell(self, index: int, *, kind: str, rect: NormRect) -> ColumnRois:
        cur = self.pairs[index]
        if kind == "rp":
            return self.replace_pair(index, RoiPair(rp=rect, sl=cur.sl))
        if kind == "sl":
            return self.replace_pair(index, RoiPair(rp=cur.rp, sl=rect))
        raise ValueError(f"kind must be rp|sl, got {kind!r}")

    def add_pair(self, pair: RoiPair | None = None) -> ColumnRois:
        if pair is None:
            # Clone primary, nudged slightly so boxes are visible as distinct.
            base = self.pairs[0]
            pair = RoiPair(
                rp=NormRect(
                    min(0.95, base.rp.left + 0.02),
                    min(0.95, base.rp.top + 0.02),
                    min(0.99, base.rp.right + 0.02),
                    min(0.99, base.rp.bottom + 0.02),
                    base.rp.tag,
                ).clamp(),
                sl=NormRect(
                    min(0.95, base.sl.left + 0.02),
                    min(0.95, base.sl.top + 0.02),
                    min(0.99, base.sl.right + 0.02),
                    min(0.99, base.sl.bottom + 0.02),
                    base.sl.tag,
                ).clamp(),
            )
        return ColumnRois(pairs=self.pairs + (pair,))

    def remove_pair(self, index: int) -> ColumnRois:
        if len(self.pairs) <= 1:
            return self
        items = [p for i, p in enumerate(self.pairs) if i != index]
        return ColumnRois(pairs=tuple(items))


@dataclass(frozen=True)
class CalibratedRois:
    version: int
    with_premium: ColumnRois
    without_premium: ColumnRois

    def column(self, *, prefer_with: bool) -> ColumnRois:
        return self.with_premium if prefer_with else self.without_premium

    @property
    def pair_count(self) -> int:
        return max(len(self.with_premium.pairs), len(self.without_premium.pairs))

    def with_synced_pair_counts(self) -> CalibratedRois:
        """Pad the shorter column so with/without always share the same pair indices."""
        w = list(self.with_premium.pairs)
        wo = list(self.without_premium.pairs)
        while len(w) < len(wo):
            w.append(_nudge_pair(w[-1] if w else wo[-1], tag_prefix="with"))
        while len(wo) < len(w):
            wo.append(_nudge_pair(wo[-1] if wo else w[-1], tag_prefix="without"))
        if len(w) == len(self.with_premium.pairs) and len(wo) == len(self.without_premium.pairs):
            return self
        return CalibratedRois(
            version=self.version,
            with_premium=ColumnRois(pairs=tuple(w)),
            without_premium=ColumnRois(pairs=tuple(wo)),
        )

    def add_fallback_pair(self) -> CalibratedRois:
        """Append one new pair slot to BOTH premium columns (same index)."""
        return CalibratedRois(
            version=self.version,
            with_premium=self.with_premium.add_pair(),
            without_premium=self.without_premium.add_pair(),
        ).with_synced_pair_counts()

    def remove_fallback_pair(self, index: int) -> CalibratedRois:
        """Remove the same pair index from BOTH columns."""
        return CalibratedRois(
            version=self.version,
            with_premium=self.with_premium.remove_pair(index),
            without_premium=self.without_premium.remove_pair(index),
        ).with_synced_pair_counts()


def _nudge_pair(base: RoiPair, *, tag_prefix: str) -> RoiPair:
    return RoiPair(
        rp=NormRect(
            min(0.95, base.rp.left + 0.02),
            min(0.95, base.rp.top + 0.02),
            min(0.99, base.rp.right + 0.02),
            min(0.99, base.rp.bottom + 0.02),
            f"{tag_prefix}-rp",
        ).clamp(),
        sl=NormRect(
            min(0.95, base.sl.left + 0.02),
            min(0.95, base.sl.top + 0.02),
            min(0.99, base.sl.right + 0.02),
            min(0.99, base.sl.bottom + 0.02),
            f"{tag_prefix}-sl",
        ).clamp(),
    )


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


def _pair_from_dict(raw: Any, *, prefix: str, index: int) -> RoiPair | None:
    if not isinstance(raw, dict):
        return None
    rp = _norm_from_dict(raw.get("rp"), f"{prefix}-p{index}-rp")
    sl = _norm_from_dict(raw.get("sl"), f"{prefix}-p{index}-sl")
    if rp is None or sl is None:
        return None
    return RoiPair(rp=rp, sl=sl)


def _column_from_dict(raw: Any, *, prefix: str) -> ColumnRois | None:
    if not isinstance(raw, dict):
        return None
    pairs_raw = raw.get("pairs")
    pairs: list[RoiPair] = []
    if isinstance(pairs_raw, list) and pairs_raw:
        for i, item in enumerate(pairs_raw):
            pair = _pair_from_dict(item, prefix=prefix, index=i)
            if pair is not None:
                pairs.append(pair)
    else:
        # Legacy v1: flat {rp, sl}
        legacy = _pair_from_dict(raw, prefix=prefix, index=0)
        if legacy is not None:
            # Retag to primary-style tags expected by older tests / debug.
            pairs.append(
                RoiPair(
                    rp=NormRect(
                        legacy.rp.left,
                        legacy.rp.top,
                        legacy.rp.right,
                        legacy.rp.bottom,
                        f"{prefix}-rp",
                    ),
                    sl=NormRect(
                        legacy.sl.left,
                        legacy.sl.top,
                        legacy.sl.right,
                        legacy.sl.bottom,
                        f"{prefix}-sl",
                    ),
                )
            )
    if not pairs:
        return None
    return ColumnRois(pairs=tuple(pairs))


def _rect_to_dict(rect: NormRect) -> dict[str, float]:
    return {
        "left": round(rect.left, 5),
        "top": round(rect.top, 5),
        "right": round(rect.right, 5),
        "bottom": round(rect.bottom, 5),
    }


def _pair_to_dict(pair: RoiPair) -> dict[str, Any]:
    return {"rp": _rect_to_dict(pair.rp), "sl": _rect_to_dict(pair.sl)}


def calibrated_to_dict(calib: CalibratedRois) -> dict[str, Any]:
    return {
        "version": int(calib.version),
        "with": {"pairs": [_pair_to_dict(p) for p in calib.with_premium.pairs]},
        "without": {"pairs": [_pair_to_dict(p) for p in calib.without_premium.pairs]},
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
    with_raw = raw.get("with")
    if isinstance(with_raw, dict) and isinstance(with_raw.get("pairs"), list):
        version = max(version, _SCHEMA_VERSION)
    return CalibratedRois(
        version=version,
        with_premium=with_col,
        without_premium=without_col,
    ).with_synced_pair_counts()


def packaged_calib_path() -> Path:
    return Path(__file__).resolve().parent / _CALIB_NAME


def user_calib_path() -> Path:
    return data_root() / _CALIB_NAME


def default_calibrated_rois() -> CalibratedRois:
    """Seed fractions from the live summary bands (dev will refine by dragging)."""
    return CalibratedRois(
        version=_SCHEMA_VERSION,
        with_premium=ColumnRois(
            pairs=(
                RoiPair(
                    rp=NormRect(0.200, 0.150, 0.320, 0.205, "with-rp"),
                    sl=NormRect(0.200, 0.210, 0.320, 0.265, "with-sl"),
                ),
            )
        ),
        without_premium=ColumnRois(
            pairs=(
                RoiPair(
                    rp=NormRect(0.330, 0.150, 0.435, 0.205, "without-rp"),
                    sl=NormRect(0.330, 0.210, 0.435, 0.265, "without-sl"),
                ),
            )
        ),
    )


def _try_read_calib(path: Path) -> CalibratedRois | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("ROI calib read failed (%s): %s", path, exc)
        return None
    calib = calibrated_from_dict(raw)
    if calib is None:
        log.warning("ROI calib invalid: %s", path)
    return calib


def load_calibrated_rois() -> CalibratedRois | None:
    """
    Load RP/SL fractions (0..1 of WT content_frame — scales across resolutions).

    Prefer the richer / newer calibration:
    - Higher ``version`` wins (so a shipped update can replace stale LocalAppData).
    - Same version → more pairs wins (local Save with extra fallbacks sticks).
    - Still tied → LocalAppData over packaged (dev edits keep working in .exe).
    """
    candidates: list[tuple[CalibratedRois, int]] = []
    # Prefer-user tie-break: user=1, packaged=0
    for path, tie in ((user_calib_path(), 1), (packaged_calib_path(), 0)):
        calib = _try_read_calib(path)
        if calib is not None:
            candidates.append((calib, tie))
    if not candidates:
        return None
    best, _ = max(
        candidates,
        key=lambda item: (item[0].version, item[0].pair_count, item[1]),
    )
    return best


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
    # Always persist as schema v2 with pairs[].
    to_save = CalibratedRois(
        version=_SCHEMA_VERSION,
        with_premium=calib.with_premium,
        without_premium=calib.without_premium,
    )
    payload = json.dumps(calibrated_to_dict(to_save), indent=2, ensure_ascii=False) + "\n"
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
    pair_index: int = 0,
) -> list[NormRect] | None:
    """Return [rp, sl] for one pair (default primary), or None."""
    del image  # fractions are resolution-independent; image kept for API symmetry
    calib = load_calibrated_rois()
    if calib is None:
        return None
    col = calib.column(prefer_with=prefer_with)
    if pair_index < 0 or pair_index >= len(col.pairs):
        return None
    pair = col.pairs[pair_index]
    return [pair.rp, pair.sl]


def calibrated_all_pair_rects(
    *,
    prefer_with: bool,
    calib: CalibratedRois | None = None,
) -> list[tuple[int, NormRect, NormRect]] | None:
    """``[(pair_index, rp, sl), ...]`` for sequential OCR fallback."""
    loaded = calib if calib is not None else load_calibrated_rois()
    if loaded is None:
        return None
    col = loaded.column(prefer_with=prefer_with)
    return [(i, p.rp, p.sl) for i, p in enumerate(col.pairs)]
