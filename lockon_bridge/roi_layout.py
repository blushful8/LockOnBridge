"""
Scale-safe reward ROIs for War Thunder post-battle results.

Rectangles are fractions of the WT client frame (or a mild ultrawide letterbox).
Absolute pixels are never hard-coded. Landmark word boxes (WinRT) further absorb
FullHD / 2K / 4K / UI-scale drift.

Two layouts are covered:
  • Full WT client — **left** rewards panel (~0.22–0.37 × 0.09–0.16), seen on
    some air hangar result overlays.
  • Full WT client — **center** header under «Мої результати»
    (~0.51–0.64 × 0.09–0.16): З/Без преміумом sit above the kill list.
  • Chat-cropped / scaled panels: mid-band ROIs (~0.42–0.55 × 0.12–0.23).
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

# With-premium column — left panel + center header (full client).
WITH_DIGIT_ROIS: tuple[NormRect, ...] = (
    NormRect(0.220, 0.095, 0.295, 0.160, "with-digits"),
    NormRect(0.215, 0.090, 0.300, 0.165, "with-digits-wide"),
    NormRect(0.225, 0.100, 0.290, 0.155, "with-digits-tight"),
    # Center header under results tabs (Мої результати).
    NormRect(0.515, 0.095, 0.575, 0.155, "with-digits-center"),
    NormRect(0.510, 0.090, 0.580, 0.160, "with-digits-center-wide"),
)

# Without-premium column — left / center (full client) + mid-panel (chat crops).
WITHOUT_DIGIT_ROIS: tuple[NormRect, ...] = (
    NormRect(0.295, 0.095, 0.370, 0.160, "without-digits"),
    NormRect(0.290, 0.090, 0.380, 0.165, "without-digits-wide"),
    NormRect(0.300, 0.100, 0.365, 0.155, "without-digits-tight"),
    # Center header «Без преміума» (right of З преміумом).
    NormRect(0.580, 0.095, 0.640, 0.155, "without-digits-center"),
    NormRect(0.575, 0.090, 0.645, 0.160, "without-digits-center-wide"),
    # Chat-cropped / older mid-panel calibration (results_uk_full_*).
    NormRect(0.440, 0.140, 0.510, 0.210, "without-digits-mid"),
    NormRect(0.430, 0.130, 0.520, 0.220, "without-digits-mid-wide"),
    NormRect(0.445, 0.145, 0.505, 0.205, "without-digits-mid-tight"),
    NormRect(0.420, 0.120, 0.530, 0.230, "without-digits-mid-shift"),
)

# Both premium columns together (fallback when single-column OCR is empty).
BOTH_DIGIT_ROIS: tuple[NormRect, ...] = (
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


def crop_norm(image: Image.Image, rect: NormRect) -> Image.Image | None:
    """Crop ``rect`` relative to the active content frame."""
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
    if right - left < 24 or bottom - top < 16:
        return None
    return image.crop((left, top, right, bottom))


def iter_reward_digit_rois(image: Image.Image) -> list[tuple[str, Image.Image]]:
    """With / without / both / totals digit crops from a WT client frame."""
    full = is_full_client_frame(image)
    rects: list[NormRect] = []
    if full:
        # Left + center header columns; skip mid chat ROIs (they misread on full client).
        rects.extend(WITH_DIGIT_ROIS)
        rects.extend(r for r in WITHOUT_DIGIT_ROIS if "-mid" not in r.tag)
        rects.extend(r for r in BOTH_DIGIT_ROIS if "-mid" not in r.tag)
    else:
        rects.extend(r for r in WITHOUT_DIGIT_ROIS if "-mid" in r.tag)
        rects.extend(r for r in BOTH_DIGIT_ROIS if "-mid" in r.tag)
    rects.extend(TOTAL_DIGIT_ROIS)

    out: list[tuple[str, Image.Image]] = []
    for rect in rects:
        crop = crop_norm(image, rect)
        if crop is not None:
            out.append((rect.tag, crop))
    return out
