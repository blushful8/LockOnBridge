"""Best-effort auto-append of ROI pairs when OCR settles without calib."""

from __future__ import annotations

import asyncio
import logging
import re
from io import BytesIO

from PIL import Image

from .roi_calib import (
    CalibratedRois,
    ColumnRois,
    RoiPair,
    load_calibrated_rois,
    save_calibrated_rois,
)
from .roi_layout import NormRect

log = logging.getLogger("lockon_bridge.roi_auto_learn")

_MAX_AUTO_PAIRS = 10
_PAD_X = 0.012
_PAD_Y = 0.006


def _png(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _similar(a: NormRect, b: NormRect, *, tol: float = 0.018) -> bool:
    return (
        abs(a.left - b.left) <= tol
        and abs(a.top - b.top) <= tol
        and abs(a.right - b.right) <= tol
        and abs(a.bottom - b.bottom) <= tol
    )


def _box_to_norm(
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    frame_w: float,
    frame_h: float,
    tag: str,
) -> NormRect:
    return NormRect(
        (x / frame_w) - _PAD_X,
        (y / frame_h) - _PAD_Y,
        ((x + w) / frame_w) + _PAD_X,
        ((y + h) / frame_h) + _PAD_Y,
        tag,
    ).clamp()


def _find_amount_box(
    words: list[tuple[str, float, float, float, float]],
    amount: int,
    *,
    frame_w: float,
    frame_h: float,
    x_lo: float,
    x_hi: float,
) -> tuple[float, float, float, float] | None:
    """Return pixel (x,y,w,h) for a word/glue matching ``amount`` in the x band."""
    target = str(int(amount))
    # Prefer exact digit words in band.
    candidates: list[tuple[float, float, float, float, float]] = []
    for text, x, y, w, h in words:
        digits = re.sub(r"\D", "", text or "")
        if digits != target:
            continue
        nx = (x + w * 0.5) / max(1.0, frame_w)
        if not (x_lo <= nx <= x_hi):
            continue
        # score: prefer mid-header band (results table)
        ny = y / max(1.0, frame_h)
        score = 0.0 if 0.08 <= ny <= 0.35 else 1.0
        candidates.append((score, x, y, w, h))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[2]))
    _s, x, y, w, h = candidates[0]
    return x, y, w, h


def propose_pair_from_landmarks(
    image: Image.Image,
    *,
    rp: int,
    sl: int,
    prefer_with: bool,
) -> RoiPair | None:
    """
    Build RP+SL NormRects from WinRT word boxes that match the settled amounts.
    """
    from .roi_rewards import _windows_words

    width, height = image.size
    if width < 400 or height < 300:
        return None
    try:
        words = asyncio.run(_windows_words(_png(image), "uk-UA"))
        if not words:
            words = asyncio.run(_windows_words(_png(image), "en-US"))
    except Exception as exc:  # noqa: BLE001
        log.debug("auto-learn OCR words failed: %s", exc)
        return None
    if not words:
        return None

    # Typical column centers from shipped calib (with left of without).
    if prefer_with:
        x_lo, x_hi = 0.18, 0.48
    else:
        x_lo, x_hi = 0.35, 0.70

    rp_box = _find_amount_box(words, rp, frame_w=width, frame_h=height, x_lo=x_lo, x_hi=x_hi)
    sl_box = _find_amount_box(words, sl, frame_w=width, frame_h=height, x_lo=x_lo, x_hi=x_hi)
    if rp_box is None or sl_box is None:
        return None
    # SL should sit slightly below RP in the header table.
    if sl_box[1] + 2 < rp_box[1]:
        return None

    prefix = "with" if prefer_with else "without"
    return RoiPair(
        rp=_box_to_norm(*rp_box, frame_w=width, frame_h=height, tag=f"{prefix}-rp-auto"),
        sl=_box_to_norm(*sl_box, frame_w=width, frame_h=height, tag=f"{prefix}-sl-auto"),
    )


def maybe_auto_learn_pair(
    image: Image.Image,
    *,
    rp: int,
    sl: int,
    prefer_with: bool,
) -> bool:
    """
    If calibrated pairs missed this layout but landmarks see RP/SL, append a pair.

    Returns True when a new pair was saved. Caps at ``_MAX_AUTO_PAIRS`` per column.
    """
    calib = load_calibrated_rois()
    if calib is None:
        return False
    col = calib.column(prefer_with=prefer_with)
    if len(col.pairs) >= _MAX_AUTO_PAIRS:
        return False

    # Skip if an existing pair already crops these amounts cleanly — no need.
    from .roi_layout import crop_norm
    from .roi_rewards import _cell_is_clean_number, _read_roi_amount
    from .ocr_preprocess import blank_trailing_reward_icon

    for pair in col.pairs:
        rp_crop = crop_norm(image, pair.rp)
        sl_crop = crop_norm(image, pair.sl)
        if rp_crop is None or sl_crop is None:
            continue
        rp_crop = blank_trailing_reward_icon(rp_crop)
        sl_crop = blank_trailing_reward_icon(sl_crop)
        rp_text, rp_amt = _read_roi_amount(rp_crop, prefer_stable=True)
        sl_text, sl_amt = _read_roi_amount(sl_crop, prefer_stable=True)
        if (
            _cell_is_clean_number(rp_text, rp_amt)
            and _cell_is_clean_number(sl_text, sl_amt)
            and rp_amt == rp
            and sl_amt == sl
        ):
            return False

    proposed = propose_pair_from_landmarks(
        image, rp=rp, sl=sl, prefer_with=prefer_with
    )
    if proposed is None:
        return False
    for pair in col.pairs:
        if _similar(pair.rp, proposed.rp) and _similar(pair.sl, proposed.sl):
            return False

    if prefer_with:
        new_with = col.add_pair(proposed)
        new_wo = calib.without_premium.add_pair()  # synced slot
        updated = CalibratedRois(
            version=max(calib.version, 2),
            with_premium=new_with,
            without_premium=new_wo,
        ).with_synced_pair_counts()
    else:
        new_wo = col.add_pair(proposed)
        new_with = calib.with_premium.add_pair()
        updated = CalibratedRois(
            version=max(calib.version, 2),
            with_premium=new_with,
            without_premium=new_wo,
        ).with_synced_pair_counts()

    save_calibrated_rois(updated, also_package=False)
    log.info(
        "auto-learned ROI pair (%s) for RP=%s SL=%s → %s pairs",
        "with" if prefer_with else "without",
        rp,
        sl,
        updated.pair_count,
    )
    return True
