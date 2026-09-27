"""Build LockOn Bridge PNG + ICO from the canonical brand artwork."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parent
SOURCE = OUT / "brand_source.jpg"
PNG = OUT / "lockon_bridge.png"
ICO = OUT / "lockon_bridge.ico"
# Master square for window / tray; ICO embeds the common Windows sizes.
MASTER = 256
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def _load_square() -> Image.Image:
    if not SOURCE.is_file():
        raise SystemExit(f"missing brand source: {SOURCE}")
    img = Image.open(SOURCE).convert("RGBA")
    w, h = img.size
    if w != h:
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
    return img


def main() -> None:
    master = _load_square()
    if master.size != (MASTER, MASTER):
        master = master.resize((MASTER, MASTER), Image.Resampling.LANCZOS)
    # Opaque matte — Explorer sometimes treats near-black + soft alpha poorly.
    bg = Image.new("RGBA", master.size, (16, 17, 21, 255))
    master = Image.alpha_composite(bg, master)
    master.save(PNG, format="PNG", optimize=True)
    # Classic BMP entries inside ICO — more reliable for Desktop .lnk than PNG-ICO.
    master.save(
        ICO,
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        bitmap_format="bmp",
    )
    print(f"wrote {PNG} ({PNG.stat().st_size} bytes)")
    print(f"wrote {ICO} ({ICO.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
