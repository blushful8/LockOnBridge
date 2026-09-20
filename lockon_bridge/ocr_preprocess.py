"""ROI preprocess for game UI text — soft path + HSV bright-text mask (numpy)."""

from __future__ import annotations

from PIL import Image, ImageEnhance, ImageOps

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]


def soft_upscale(image: Image.Image, *, min_height: int = 96) -> Image.Image:
    """Upscale + mild contrast (keeps coloured RP/SL glyphs for neural OCR)."""
    scale = 1.0
    if image.height < min_height:
        scale = min_height / float(image.height)
    scale = max(scale, 2.0)
    width = max(1, int(image.width * scale))
    height = max(1, int(image.height * scale))
    out = image.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
    out = ImageOps.autocontrast(out, cutoff=1)
    out = ImageEnhance.Contrast(out).enhance(1.35)
    out = ImageEnhance.Sharpness(out).enhance(1.25)
    return out


def blank_trailing_reward_icon(
    image: Image.Image,
    *,
    right_frac: float = 0.28,
) -> Image.Image:
    """
    Blank the right strip of a single RP/SL cell where WT draws the bulb/lion icon.

    OCR often reads the lion mane as a trailing ``9`` (``379`` → ``3799``) or the
    RP bulb as ``9``/``8``. Digits sit on the left; icons are always on the right.
    """
    rgb = image.convert("RGB")
    width, height = rgb.size
    if width < 24 or height < 8:
        return rgb
    cut = max(1, min(width - 1, int(round(width * (1.0 - right_frac)))))
    # Sample a dark background from the far-left margin (away from white digits).
    sample = rgb.crop((0, 0, max(1, width // 8), height))
    if np is not None:
        arr = np.asarray(sample, dtype=np.int32)
        fill = tuple(int(x) for x in arr.reshape(-1, 3).mean(axis=0))
    else:
        pixels = list(sample.getdata())
        fill = (
            tuple(int(sum(c[i] for c in pixels) / len(pixels)) for i in range(3))
            if pixels
            else (32, 32, 32)
        )
    out = rgb.copy()
    from PIL import ImageDraw

    ImageDraw.Draw(out).rectangle([cut, 0, width, height], fill=fill)
    return out


def hsv_bright_text_mask(image: Image.Image, *, invert: bool = True) -> Image.Image | None:
    """
    Keep bright game UI text (white / yellow «Всього» / cyan-blue RP), black out the rest.

    Returns black text on white (``invert=True``, Tesseract-friendly) or white on black.
    """
    if np is None:
        return None
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    # Value 0..255, saturation 0..1
    value = mx
    sat = np.divide(mx - mn, mx, out=np.zeros_like(mx), where=mx > 1e-3)

    # White / light grey UI digits
    white = (value >= 175) & (sat <= 0.35)
    # Yellow / gold «Всього» row
    yellow = (r >= 160) & (g >= 120) & (b <= 150) & ((r + g) >= (b * 2.2)) & (value >= 140)
    # Cyan / light-blue RP numerals
    cyan = (b >= 130) & (g >= 100) & (r <= 190) & (b >= r - 10) & (value >= 130) & (sat >= 0.12)

    mask = white | yellow | cyan
    if not bool(mask.any()):
        return None

    # Grow mask slightly so thin strokes survive
    from numpy.lib.stride_tricks import sliding_window_view

    try:
        padded = np.pad(mask.astype(np.uint8), 1, mode="constant")
        windows = sliding_window_view(padded, (3, 3))
        mask = windows.max(axis=(-1, -2)).astype(bool)
    except Exception:  # noqa: BLE001
        pass

    if invert:
        # Black glyphs on white — classic OCR
        canvas = np.full(mask.shape, 255, dtype=np.uint8)
        canvas[mask] = 0
    else:
        canvas = np.zeros(mask.shape, dtype=np.uint8)
        canvas[mask] = 255
    return Image.fromarray(canvas, mode="L").convert("RGB")


def preprocess_variants(image: Image.Image, *, allow_hsv: bool = True) -> list[tuple[str, Image.Image]]:
    """Soft colour first; optional HSV only when caller asks (weak soft read)."""
    out: list[tuple[str, Image.Image]] = [("soft", soft_upscale(image))]
    if not allow_hsv:
        return out
    masked = hsv_bright_text_mask(image, invert=True)
    if masked is not None:
        out.append(("hsv", soft_upscale(masked)))
    return out
