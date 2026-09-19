"""
Scale-safe reward ROIs for War Thunder post-battle results.

Rectangles are fractions of the WT client frame (or a mild ultrawide letterbox).
Absolute pixels are never hard-coded. Landmark word boxes (WinRT) further absorb
FullHD / 2K / 4K / UI-scale drift.

Calibrated on a full UA client frame (aspect ≈1.6): without-premium column sits
under the right header (~0.44–0.51 × 0.14–0.21); «Всього» RP/SL sit mid-panel
(~0.50–0.64 × 0.44–0.49). Extra wide/shift variants absorb UI-scale drift.
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

# Right column only — left edge ≥0.42 avoids premium SL (10892).
WITHOUT_DIGIT_ROIS: tuple[NormRect, ...] = (
    NormRect(0.44, 0.14, 0.51, 0.21, "without-digits"),
    NormRect(0.43, 0.13, 0.52, 0.22, "without-digits-wide"),
    NormRect(0.445, 0.145, 0.505, 0.205, "without-digits-tight"),
    NormRect(0.42, 0.12, 0.53, 0.23, "without-digits-shift"),
)

# «Всього» row only — bottom ≤0.49 avoids «Дослідження модифікацій» (1025 again).
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
    """All without-premium + totals digit crops from a WT client frame."""
    out: list[tuple[str, Image.Image]] = []
    for rect in (*WITHOUT_DIGIT_ROIS, *TOTAL_DIGIT_ROIS):
        crop = crop_norm(image, rect)
        if crop is not None:
            out.append((rect.tag, crop))
    return out
