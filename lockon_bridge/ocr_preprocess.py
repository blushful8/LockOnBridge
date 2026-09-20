"""ROI preprocess for game UI text — soft path + HSV bright-text mask (numpy)."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]

log = logging.getLogger("lockon_bridge.ocr_preprocess")

_ICON_DIR = Path(__file__).resolve().parent / "reward_icons"


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


def _bg_fill(rgb: Image.Image) -> tuple[int, int, int]:
    width, height = rgb.size
    sample = rgb.crop((0, 0, max(1, width // 8), height))
    if np is not None:
        arr = np.asarray(sample, dtype=np.int32)
        return tuple(int(x) for x in arr.reshape(-1, 3).mean(axis=0))  # type: ignore[return-value]
    pixels = list(sample.getdata())
    if not pixels:
        return (32, 32, 32)
    return tuple(int(sum(c[i] for c in pixels) / len(pixels)) for i in range(3))  # type: ignore[return-value]


@lru_cache(maxsize=1)
def _load_reward_icon_templates() -> list[tuple[str, Image.Image]]:
    """Shipped RP bulb / SL lion crops (icon only, no digits)."""
    out: list[tuple[str, Image.Image]] = []
    for name in ("rp_bulb.png", "sl_lion.png"):
        path = _ICON_DIR / name
        if not path.is_file():
            continue
        try:
            out.append((name, Image.open(path).convert("RGB")))
        except OSError as exc:
            log.debug("reward icon %s: %s", path, exc)
    # Extra HD bulb crops if present (same folder).
    for path in sorted(_ICON_DIR.glob("hd_bulb_*.png")):
        try:
            out.append((path.name, Image.open(path).convert("RGB")))
        except OSError:
            continue
    return out


def _ncc_score(hay: "np.ndarray", needle: "np.ndarray") -> float:
    """Normalized correlation of two same-shaped float arrays in [-1, 1]."""
    a = hay.astype(np.float32).ravel()
    b = needle.astype(np.float32).ravel()
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom < 1e-6:
        return -1.0
    return float(np.dot(a, b) / denom)


def _match_template_right(
    rgb: Image.Image,
    template: Image.Image,
    *,
    kind: str,
    search_left_frac: float = 0.35,
) -> tuple[float, tuple[int, int, int, int]] | None:
    """Best NCC match of ``template`` in the right portion of ``rgb``."""
    if np is None:
        return None
    hay = np.asarray(rgb, dtype=np.uint8)
    h, w = hay.shape[:2]
    th0, tw0 = template.size[1], template.size[0]
    if tw0 < 6 or th0 < 6 or w < tw0 + 2 or h < th0 + 2:
        return None

    x0 = int(w * search_left_frac)
    best_score = -1.0
    best_box: tuple[int, int, int, int] | None = None

    # Scale templates to cell icon size (roughly cell height).
    scales: list[tuple[int, int]] = []
    for scale in (0.55, 0.7, 0.85, 1.0, 1.2, 1.45, 1.7, 2.0):
        tw = max(8, int(round(tw0 * scale)))
        th = max(8, int(round(th0 * scale)))
        if tw >= w - x0 or th >= h:
            continue
        scales.append((tw, th))
    # Also target ~70–100% of cell height.
    for frac in (0.65, 0.8, 0.95):
        th = max(8, min(h - 1, int(round(h * frac))))
        tw = max(8, int(round(tw0 * (th / float(th0)))))
        if tw < w - x0 and th < h:
            scales.append((tw, th))

    use_blue = "bulb" in kind or "rp" in kind
    seen: set[tuple[int, int]] = set()
    for tw, th in scales:
        key = (tw, th)
        if key in seen:
            continue
        seen.add(key)
        tmpl = np.asarray(
            template.resize((tw, th), Image.Resampling.LANCZOS),
            dtype=np.uint8,
        )
        if use_blue:
            needle = tmpl[:, :, 2].astype(np.float32) - tmpl[:, :, 0].astype(np.float32)
            field = hay[:, :, 2].astype(np.float32) - hay[:, :, 0].astype(np.float32)
        else:
            needle = tmpl.mean(axis=2)
            field = hay.mean(axis=2)

        max_y = h - th
        max_x = w - tw
        if max_y < 0 or max_x < x0:
            continue
        step_y = max(1, th // 5)
        step_x = max(1, tw // 5)
        local_best = -1.0
        local_box: tuple[int, int, int, int] | None = None
        for y in range(0, max_y + 1, step_y):
            for x in range(x0, max_x + 1, step_x):
                patch = field[y : y + th, x : x + tw]
                score = _ncc_score(patch, needle)
                if score > local_best:
                    local_best = score
                    local_box = (x, y, x + tw, y + th)

        if local_box is None:
            continue
        bx0, by0, _, _ = local_box
        for y in range(max(0, by0 - step_y), min(max_y, by0 + step_y) + 1):
            for x in range(max(x0, bx0 - step_x), min(max_x, bx0 + step_x) + 1):
                patch = field[y : y + th, x : x + tw]
                score = _ncc_score(patch, needle)
                if score > local_best:
                    local_best = score
                    local_box = (x, y, x + tw, y + th)

        if local_best > best_score and local_box is not None:
            best_score = local_best
            best_box = local_box

    if best_box is None:
        return None
    return best_score, best_box


def _color_icon_box(rgb: Image.Image) -> tuple[int, int, int, int] | None:
    """Detect bulb (cyan) or lion (silver blob) on the right half without templates."""
    if np is None:
        return None
    arr = np.asarray(rgb, dtype=np.int16)
    h, w = arr.shape[:2]
    if w < 24 or h < 8:
        return None
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    x0_bulb = int(w * 0.40)
    x0_lion = int(w * 0.55)

    cyan = (b > 130) & (g > 90) & (b > r + 25) & (g > r + 5)
    cyan[:, :x0_bulb] = False

    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    sat = (mx - mn) / np.maximum(mx, 1)
    # Compact silver lion — bright, low sat, far-right (digits stay left).
    silver = (mx >= 145) & (sat <= 0.28) & ((mx - mn) <= 55)
    silver[:, :x0_lion] = False

    def _bbox(mask: "np.ndarray") -> tuple[int, int, int, int] | None:
        ys, xs = np.where(mask)
        if len(xs) < 12:
            return None
        left, right = int(xs.min()), int(xs.max()) + 1
        top, bottom = int(ys.min()), int(ys.max()) + 1
        bw, bh = right - left, bottom - top
        # Reject full-width text washes; icons are compact.
        if bw > int(w * 0.55) or bh < max(4, h // 5):
            return None
        if left < int(w * 0.35):
            return None
        # Prefer icons that touch the right margin (WT draws them flush right).
        if right < int(w * 0.82):
            return None
        pad = 2
        return (
            max(0, left - pad),
            max(0, top - pad),
            min(w, right + pad),
            min(h, bottom + pad),
        )

    bulb = _bbox(cyan)
    if bulb is not None:
        return bulb
    return _bbox(silver)


def blank_trailing_reward_icon(
    image: Image.Image,
    *,
    right_frac: float = 0.22,
) -> Image.Image:
    """
    Remove WT RP bulb / SL lion from a single reward cell without eating digits.

    Prefer template match against shipped icon crops, then colour blob detect.
    Only if nothing is found, blank a *mild* right strip (narrower than before).
    """
    rgb = image.convert("RGB")
    width, height = rgb.size
    if width < 24 or height < 8:
        return rgb

    fill = _bg_fill(rgb)
    boxes: list[tuple[int, int, int, int]] = []

    best_hit: tuple[float, tuple[int, int, int, int], str] | None = None
    for name, tmpl in _load_reward_icon_templates():
        hit = _match_template_right(rgb, tmpl, kind=name.lower())
        if hit is None:
            continue
        score, box = hit
        if score < 0.52:
            continue
        if best_hit is None or score > best_hit[0]:
            best_hit = (score, box, name)
    if best_hit is not None:
        score, box, name = best_hit
        boxes.append(box)
        log.debug("reward icon match %s score=%.2f box=%s", name, score, box)

    # Colour fallback only when templates miss — silver digits look like lions.
    if not boxes:
        color_box = _color_icon_box(rgb)
        if color_box is not None:
            boxes.append(color_box)

    out = rgb.copy()
    draw = ImageDraw.Draw(out)
    if boxes:
        # Merge / blank only icon boxes (not the whole strip).
        for x0, y0, x1, y1 in boxes:
            # Expand slightly so mane/glow does not leak into OCR.
            pad_x = max(1, (x1 - x0) // 10)
            pad_y = max(1, (y1 - y0) // 10)
            draw.rectangle(
                [
                    max(0, x0 - pad_x),
                    max(0, y0 - pad_y),
                    min(width, x1 + pad_x),
                    min(height, y1 + pad_y),
                ],
                fill=fill,
            )
        return out

    # Fallback: mild right strip — last resort when templates miss.
    cut = max(1, min(width - 1, int(round(width * (1.0 - right_frac)))))
    draw.rectangle([cut, 0, width, height], fill=fill)
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
