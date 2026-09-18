"""Generate LockOn Bridge app icon (reticle brand mark) as PNG + ICO."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent
BG = (18, 20, 24, 255)
CYAN = (34, 211, 238, 255)
AMBER = (245, 166, 35, 255)


def draw_reticle(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), BG)
    d = ImageDraw.Draw(img, "RGBA")
    c = size / 2
    span = float(size)
    outer = span * 0.335
    mid = span * 0.285
    inner = span * 0.235

    def glow_arc(radius: float, width: float, color: tuple[int, int, int, int], start: float, end: float) -> None:
        r, g, b, a = color
        for mul, alpha in ((3.2, 0.12), (1.8, 0.22), (1.0, 1.0)):
            w = max(1, int(width * mul))
            col = (r, g, b, max(1, int(a * alpha)))
            bbox = [c - radius, c - radius, c + radius, c + radius]
            d.arc(bbox, start=start, end=end, fill=col, width=w)

    for radius in range(int(outer), 0, -2):
        alpha = int(18 * (1 - radius / outer))
        d.ellipse(
            [c - radius, c - radius, c + radius, c + radius],
            outline=(34, 211, 238, alpha),
        )

    for start in (20, 110, 200, 290):
        glow_arc(outer, span * 0.017, CYAN, start, start + 70)
    for i in range(0, 360, 12):
        glow_arc(mid, span * 0.007, (34, 211, 238, 140), i, i + 6)
    for start in (30, 120, 210, 300):
        glow_arc(inner, span * 0.010, (34, 211, 238, 200), start, start + 60)

    wedge: list[tuple[float, float]] = [(c, c)]
    for ang in range(0, 56, 2):
        rad = math.radians(ang - 90)
        wedge.append((c + outer * math.cos(rad), c + outer * math.sin(rad)))
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(overlay).polygon(wedge, fill=(245, 166, 35, 45))
    img = Image.alpha_composite(img, overlay)
    d = ImageDraw.Draw(img, "RGBA")

    cw = max(1, int(span * 0.012))
    gap = span * 0.04
    arm = span * 0.12
    for col, w in (((34, 211, 238, 45), cw * 3), (CYAN, cw)):
        d.line([(c - arm, c), (c - gap, c)], fill=col, width=w)
        d.line([(c + gap, c), (c + arm, c)], fill=col, width=w)
        d.line([(c, c - arm), (c, c - gap)], fill=col, width=w)
        d.line([(c, c + gap), (c, c + arm)], fill=col, width=w)

    dot = max(2, int(span * 0.018))
    d.ellipse([c - dot, c - dot, c + dot, c + dot], fill=AMBER)

    bl = span * 0.12
    inset = span * 0.14
    bw = max(2, int(span * 0.018))
    for x, y, sx, sy in (
        (inset, inset, 1, 1),
        (size - inset, inset, -1, 1),
        (inset, size - inset, 1, -1),
        (size - inset, size - inset, -1, -1),
    ):
        d.line([(x, y), (x + sx * bl, y)], fill=CYAN, width=bw)
        d.line([(x, y), (x, y + sy * bl)], fill=CYAN, width=bw)

    return img


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [draw_reticle(s) for s in sizes]
    png = OUT / "lockon_bridge.png"
    ico = OUT / "lockon_bridge.ico"
    images[-1].save(png)
    images[-1].save(ico, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"wrote {png}")
    print(f"wrote {ico} ({ico.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
