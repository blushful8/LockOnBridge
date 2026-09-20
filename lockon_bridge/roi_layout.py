"""
Scale-safe reward ROIs for War Thunder post-battle results.

Rectangles are fractions of the WT client frame (or a mild ultrawide letterbox).
Absolute pixels are never hard-coded. Landmark word boxes (WinRT) further absorb
FullHD / 2K / 4K / UI-scale drift.

Layouts covered:
  • Full WT client — **left summary** under «Ваше місце» (~0.18–0.42 × 0.14–0.28)
  • Full WT client — older **left** strip (~0.22–0.37 × 0.09–0.16)
  • Full WT client — **center** header under «Мої результати» (~0.51–0.64 × 0.09–0.16)
  • Chat-cropped / scaled panels: mid-band ROIs (~0.42–0.55 × 0.12–0.23)
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image


@dataclass(frozen=True)
class NormRect:
    """Crop in 0..1 of the active frame width/height."""

    left: float
    top: float
    right: float
    bottom: float
    tag: str

    def clamp(self) -> NormRect:
        return NormRect(
            left=max(0.0, min(1.0, self.left)),
            top=max(0.0, min(1.0, self.top)),
            right=max(0.0, min(1.0, self.right)),
            bottom=max(0.0, min(1.0, self.bottom)),
            tag=self.tag,
        )


# Only letterbox extreme ultrawide (≥≈21:9). 16:9 / 16:10 / chat screenshots stay as-is.
_ULTRAWIDE_ASPECT = 1.95

# With-premium column — summary box first, then legacy left + center header.
WITH_DIGIT_ROIS: tuple[NormRect, ...] = (
    # Mission-results summary (З преміумом) — live 2560×1600 / FullHD.
    NormRect(0.200, 0.150, 0.320, 0.265, "with-digits-summary"),
    NormRect(0.190, 0.140, 0.330, 0.275, "with-digits-summary-wide"),
    NormRect(0.220, 0.095, 0.295, 0.160, "with-digits"),
    NormRect(0.215, 0.090, 0.300, 0.165, "with-digits-wide"),
    NormRect(0.225, 0.100, 0.290, 0.155, "with-digits-tight"),
    # Center header under results tabs (Мої результати).
    NormRect(0.515, 0.095, 0.575, 0.155, "with-digits-center"),
    NormRect(0.510, 0.090, 0.580, 0.160, "with-digits-center-wide"),
)

# Without-premium column — summary / left / center / chat mid-panel.
WITHOUT_DIGIT_ROIS: tuple[NormRect, ...] = (
    NormRect(0.330, 0.150, 0.435, 0.265, "without-digits-summary"),
    NormRect(0.320, 0.140, 0.445, 0.275, "without-digits-summary-wide"),
    NormRect(0.295, 0.095, 0.370, 0.160, "without-digits"),
    NormRect(0.290, 0.090, 0.380, 0.165, "without-digits-wide"),
    NormRect(0.300, 0.100, 0.365, 0.155, "without-digits-tight"),
    NormRect(0.580, 0.095, 0.640, 0.155, "without-digits-center"),
    NormRect(0.575, 0.090, 0.645, 0.160, "without-digits-center-wide"),
    # Chat-cropped / older mid-panel calibration (results_uk_full_*).
    NormRect(0.440, 0.140, 0.510, 0.210, "without-digits-mid"),
    NormRect(0.430, 0.130, 0.520, 0.220, "without-digits-mid-wide"),
    NormRect(0.445, 0.145, 0.505, 0.205, "without-digits-mid-tight"),
    NormRect(0.420, 0.120, 0.530, 0.230, "without-digits-mid-shift"),
)

# Both premium columns — summary 4-cell is the most reliable full-client fallback.
BOTH_DIGIT_ROIS: tuple[NormRect, ...] = (
    NormRect(0.180, 0.140, 0.420, 0.280, "both-digits-summary"),
    NormRect(0.170, 0.130, 0.430, 0.290, "both-digits-summary-wide"),
    NormRect(0.220, 0.095, 0.370, 0.165, "both-digits"),
    NormRect(0.210, 0.085, 0.385, 0.175, "both-digits-wide"),
    NormRect(0.500, 0.090, 0.645, 0.160, "both-digits-center"),
    NormRect(0.420, 0.120, 0.550, 0.230, "both-digits-mid"),
)

# «Всього» row — mid-panel; bottom ≤0.49 avoids «Дослідження модифікацій».
TOTAL_DIGIT_ROIS: tuple[NormRect, ...] = (
    NormRect(0.50, 0.440, 0.64, 0.485, "total-digits"),
    NormRect(0.48, 0.430, 0.66, 0.495, "total-digits-wide"),
    NormRect(0.52, 0.445, 0.63, 0.480, "total-digits-tight"),
    NormRect(0.49, 0.425, 0.65, 0.500, "total-digits-shift"),
    # Slightly higher band for taller UI scale / 16:10.
    NormRect(0.48, 0.400, 0.66, 0.470, "total-digits-high"),
)


def content_frame(image: Image.Image) -> tuple[int, int, int, int]:
    """
    Active frame inside ``image``.

    Ultrawide monitors crop to a centered 16:9 band. Narrower or normal frames use
    the full client — avoids destroying chat screenshots / 16:10 windows.
    """
    width, height = image.size
    if width < 2 or height < 2:
        return 0, 0, max(1, width), max(1, height)

    aspect = width / float(height)
    if aspect >= _ULTRAWIDE_ASPECT:
        target_w = int(round(height * 16.0 / 9.0))
        left = max(0, (width - target_w) // 2)
        return left, 0, min(width, left + target_w), height
    return 0, 0, width, height


def is_full_client_frame(image: Image.Image) -> bool:
    """
    True for a real WT window capture (FullHD+), False for chat-cropped panels.

    Left-panel premium ROIs only apply on full client frames; mid-panel ROIs cover
    the older chat fixtures where the table was already centred in the crop.
    """
    width, height = image.size
    return width >= 1600 or (width >= 1280 and height >= 900)


def pixel_box(
    image: Image.Image,
    rect: NormRect,
    *,
    min_width: int = 24,
    min_height: int = 16,
) -> tuple[int, int, int, int] | None:
    """Absolute pixel box for ``rect`` inside ``image`` (content-frame relative)."""
    rect = rect.clamp()
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    fl, ft, fr, fb = content_frame(image)
    fw = max(1, fr - fl)
    fh = max(1, fb - ft)
    left = fl + int(round(fw * rect.left))
    top = ft + int(round(fh * rect.top))
    right = fl + int(round(fw * rect.right))
    bottom = ft + int(round(fh * rect.bottom))
    if right - left < min_width or bottom - top < min_height:
        return None
    return left, top, right, bottom


def crop_norm(image: Image.Image, rect: NormRect) -> Image.Image | None:
    """Crop ``rect`` relative to the active content frame."""
    box = pixel_box(image, rect)
    if box is None:
        return None
    return image.crop(box)


def select_reward_digit_rects(
    image: Image.Image,
    *,
    dense: bool = False,
    prefer_with: bool | None = None,
) -> list[NormRect]:
    """
    Digit ROI rectangles for reward OCR (no crops).

    When ``roi_calibrated.json`` is present, lean path uses exactly RP+SL for the
    premium column. Dense / missing calib keeps the legacy catalogue.
    """
    if not dense:
        from .roi_calib import calibrated_rects_for

        if prefer_with is None:
            try:
                from .settings import load_settings

                prefer_with = bool(load_settings().has_premium_account)
            except Exception:  # noqa: BLE001
                prefer_with = False
        calibrated = calibrated_rects_for(image, prefer_with=bool(prefer_with))
        if calibrated:
            return list(calibrated)

    full = is_full_client_frame(image)
    rects: list[NormRect] = []

    def _first(pool: tuple[NormRect, ...], *needles: str) -> list[NormRect]:
        chosen: list[NormRect] = []
        for needle in needles:
            for rect in pool:
                if needle in rect.tag and rect not in chosen:
                    chosen.append(rect)
                    break
        return chosen

    if dense:
        if full:
            rects.extend(r for r in WITH_DIGIT_ROIS)
            rects.extend(r for r in WITHOUT_DIGIT_ROIS if "-mid" not in r.tag)
            rects.extend(r for r in BOTH_DIGIT_ROIS if "-mid" not in r.tag)
        else:
            rects.extend(r for r in WITHOUT_DIGIT_ROIS if "-mid" in r.tag)
            rects.extend(r for r in BOTH_DIGIT_ROIS if "-mid" in r.tag)
        rects.extend(TOTAL_DIGIT_ROIS)
    elif full:
        # One 4-cell summary (column-split) + one total — covers live mission results.
        rects.extend(_first(BOTH_DIGIT_ROIS, "both-digits-summary"))
        rects.extend(_first(TOTAL_DIGIT_ROIS, "total-digits", "total-digits-wide"))
    else:
        rects.extend(
            _first(
                tuple(r for r in WITHOUT_DIGIT_ROIS if "-mid" in r.tag),
                "without-digits-mid-wide",
                "without-digits-mid",
            )
        )
        rects.extend(_first(BOTH_DIGIT_ROIS, "both-digits-mid"))
        rects.extend(_first(TOTAL_DIGIT_ROIS, "total-digits-wide", "total-digits"))

    out: list[NormRect] = []
    seen: set[str] = set()
    for rect in rects:
        if rect.tag in seen:
            continue
        seen.add(rect.tag)
        out.append(rect)
    return out


def iter_roi_pixel_boxes(
    image: Image.Image,
    *,
    dense: bool = True,
) -> list[tuple[str, tuple[int, int, int, int], bool]]:
    """
    ``(tag, (l,t,r,b), is_lean)`` for digit ROIs.

    Lean tags match the default OCR path; dense adds the full catalogue.
    """
    lean_tags = {r.tag for r in select_reward_digit_rects(image, dense=False)}
    out: list[tuple[str, tuple[int, int, int, int], bool]] = []
    for rect in select_reward_digit_rects(image, dense=dense):
        box = pixel_box(image, rect)
        if box is None:
            continue
        out.append((rect.tag, box, rect.tag in lean_tags))
    return out


def iter_reward_digit_rois(
    image: Image.Image,
    *,
    dense: bool = False,
    prefer_with: bool | None = None,
) -> list[tuple[str, Image.Image]]:
    """Digit crops for reward OCR (see ``select_reward_digit_rects``)."""
    out: list[tuple[str, Image.Image]] = []
    for rect in select_reward_digit_rects(image, dense=dense, prefer_with=prefer_with):
        crop = crop_norm(image, rect)
        if crop is not None:
            out.append((rect.tag, crop))
    return out
