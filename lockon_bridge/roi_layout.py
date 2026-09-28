"""Resolution-independent rectangles on a War Thunder client frame."""

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


def content_frame(image: Image.Image) -> tuple[int, int, int, int]:
    """
    Active frame inside ``image``.

    Ultrawide monitors crop to a centered 16:9 band. Narrower or normal frames use
    the full client.
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
