"""Generate LockOn Bridge UI chrome PNGs (transparent, brand cyan/amber HUD)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "ui"
RAW = ROOT / "assets" / "ui_raw"

# Brand (from lockon_bridge.png + app chrome)
CYAN = (46, 196, 182, 255)       # #2ec4b6
CYAN_DIM = (28, 140, 130, 255)
CYAN_GLOW = (46, 196, 182, 90)
AMBER = (196, 92, 38, 255)       # #c45c26
PANEL = (36, 40, 48, 255)        # #242830
SLATE = (75, 85, 99, 255)        # #4b5563
SLATE_DIM = (55, 65, 81, 255)
WHITE = (255, 255, 255, 255)
KNOB_RIM = (209, 213, 219, 255)
OFF_BADGE = (107, 114, 128, 255)
ACTIVE = (56, 189, 248, 255)     # sky for "active"
IDLE = (234, 179, 8, 255)        # amber-yellow idle


def _new(w: int, h: int) -> Image.Image:
    return Image.new("RGBA", (w, h), (0, 0, 0, 0))


def _round_rect(draw: ImageDraw.ImageDraw, box, radius: int, fill) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _soft_shadow(base: Image.Image, mask: Image.Image, offset=(2, 3), blur=4, alpha=70) -> Image.Image:
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer = Image.new("RGBA", base.size, (0, 0, 0, alpha))
    shadow.paste(layer, offset, mask)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    out = Image.alpha_composite(shadow, base)
    return out


def make_switch(on: bool, scale: int = 2) -> Image.Image:
    w, h = 56 * scale, 32 * scale
    img = _new(w, h)
    track = CYAN if on else SLATE
    track_inner = CYAN_DIM if on else SLATE_DIM
    pad = 3 * scale
    r = h // 2

    # Track body
    body = _new(w, h)
    d = ImageDraw.Draw(body)
    _round_rect(d, (0, 0, w - 1, h - 1), r, track)
    # Inner top sheen
    sheen = _new(w, h)
    sd = ImageDraw.Draw(sheen)
    _round_rect(sd, (2, 2, w - 3, h // 2), r - 2, (255, 255, 255, 38 if on else 22))
    body = Image.alpha_composite(body, sheen)
    # Inner edge
    edge = _new(w, h)
    ed = ImageDraw.Draw(edge)
    _round_rect(ed, (1, 1, w - 2, h - 2), r - 1, None)
    ed.rounded_rectangle((1, 1, w - 2, h - 2), radius=r - 1, outline=track_inner, width=max(1, scale // 2))
    body = Image.alpha_composite(body, edge)

    # Knob
    kr = (h // 2) - pad
    cx = (w - pad - kr) if on else (pad + kr)
    cy = h // 2
    knob_layer = _new(w, h)
    # Soft real shadow (blur), not stipple
    sh = _new(w, h)
    shd = ImageDraw.Draw(sh)
    shd.ellipse((cx - kr + 1, cy - kr + 2, cx + kr + 1, cy + kr + 2), fill=(0, 0, 0, 90))
    sh = sh.filter(ImageFilter.GaussianBlur(2 * scale))
    knob_layer = Image.alpha_composite(knob_layer, sh)
    kd = ImageDraw.Draw(knob_layer)
    kd.ellipse((cx - kr, cy - kr, cx + kr, cy + kr), fill=WHITE, outline=KNOB_RIM, width=max(1, scale))
    # Tiny specular
    kd.ellipse(
        (cx - kr // 2, cy - kr // 2 - scale, cx + kr // 4, cy - scale),
        fill=(255, 255, 255, 70),
    )
    # Cyan ring hint when ON
    if on:
        kd.ellipse(
            (cx - kr + scale, cy - kr + scale, cx + kr - scale, cy + kr - scale),
            outline=(*CYAN[:3], 90),
            width=max(1, scale // 2),
        )

    out = Image.alpha_composite(body, knob_layer)
    return out


def make_badge(fill, border, w: int = 220, h: int = 44) -> Image.Image:
    img = _new(w, h)
    d = ImageDraw.Draw(img)
    r = h // 2
    _round_rect(d, (0, 0, w - 1, h - 1), r, fill)
    d.rounded_rectangle((1, 1, w - 2, h - 2), radius=r - 1, outline=border, width=2)
    # Top sheen
    sheen = _new(w, h)
    sd = ImageDraw.Draw(sheen)
    _round_rect(sd, (3, 2, w - 4, h // 2), r - 2, (255, 255, 255, 28))
    return Image.alpha_composite(img, sheen)


def make_button(fill, border, w: int = 480, h: int = 72) -> Image.Image:
    img = _new(w, h)
    d = ImageDraw.Draw(img)
    r = 14
    _round_rect(d, (0, 0, w - 1, h - 1), r, fill)
    d.rounded_rectangle((1, 1, w - 2, h - 2), radius=r - 1, outline=border, width=2)
    sheen = _new(w, h)
    sd = ImageDraw.Draw(sheen)
    _round_rect(sd, (4, 3, w - 5, h // 2), r - 2, (255, 255, 255, 20))
    # Cyan corner ticks (HUD)
    tick = CYAN if fill[0] < 80 else AMBER
    for x0, y0, x1, y1 in (
        (8, 8, 22, 8),
        (8, 8, 8, 22),
        (w - 23, 8, w - 9, 8),
        (w - 9, 8, w - 9, 22),
        (8, h - 9, 22, h - 9),
        (8, h - 23, 8, h - 9),
        (w - 23, h - 9, w - 9, h - 9),
        (w - 9, h - 23, w - 9, h - 9),
    ):
        d.line((x0, y0, x1, y1), fill=tick, width=2)
    return Image.alpha_composite(img, sheen)


def make_card(w: int = 640, h: int = 360) -> Image.Image:
    img = _new(w, h)
    d = ImageDraw.Draw(img)
    _round_rect(d, (0, 0, w - 1, h - 1), 18, (*PANEL[:3], 245))
    d.rounded_rectangle((1, 1, w - 2, h - 2), radius=17, outline=(*CYAN[:3], 70), width=2)
    # Corner brackets
    L = 28
    t = 3
    c = CYAN
    brackets = [
        [(12, 12), (12 + L, 12), (12, 12), (12, 12 + L)],
        [(w - 13 - L, 12), (w - 13, 12), (w - 13, 12), (w - 13, 12 + L)],
        [(12, h - 13), (12 + L, h - 13), (12, h - 13 - L), (12, h - 13)],
        [(w - 13 - L, h - 13), (w - 13, h - 13), (w - 13, h - 13 - L), (w - 13, h - 13)],
    ]
    for (a, b, c1, c2) in brackets:
        d.line((*a, *b), fill=c, width=t)
        d.line((*c1, *c2), fill=c, width=t)
    return img


def make_header_strip(w: int = 640, h: int = 12) -> Image.Image:
    img = _new(w, h)
    d = ImageDraw.Draw(img)
    for x in range(w):
        t = x / max(1, w - 1)
        if t < 0.55:
            col = CYAN
        else:
            # blend to amber
            u = (t - 0.55) / 0.45
            col = tuple(int(CYAN[i] * (1 - u) + AMBER[i] * u) for i in range(4))
        d.line((x, 0, x, h - 1), fill=col)
    # soft ends
    fade = _new(w, h)
    return img


def make_icon_phone(ok: bool, size: int = 64) -> Image.Image:
    img = _new(size, size)
    d = ImageDraw.Draw(img)
    color = CYAN if ok else AMBER
    # phone body
    m = size // 6
    d.rounded_rectangle((m + 4, m // 2, size - m - 4, size - m // 2), radius=size // 8, outline=color, width=3)
    d.ellipse((size // 2 - 4, size - m - 2, size // 2 + 4, size - m + 6), fill=color)
    # status mark
    if ok:
        d.line((size // 2 - 8, size // 2, size // 2 - 2, size // 2 + 8), fill=color, width=3)
        d.line((size // 2 - 2, size // 2 + 8, size // 2 + 12, size // 2 - 8), fill=color, width=3)
    else:
        d.line((size // 2 - 8, size // 2 - 8, size // 2 + 8, size // 2 + 8), fill=color, width=3)
        d.line((size // 2 + 8, size // 2 - 8, size // 2 - 8, size // 2 + 8), fill=color, width=3)
    return img


def make_icon_radar(size: int = 96) -> Image.Image:
    """Compact header mark echoing the app logo geometry."""
    img = _new(size, size)
    d = ImageDraw.Draw(img)
    cx = cy = size // 2
    for rad, width in ((size // 2 - 4, 3), (size // 3, 2), (size // 5, 2)):
        d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), outline=CYAN, width=width)
    # crosshair
    d.line((cx, 8, cx, size - 8), fill=CYAN, width=2)
    d.line((8, cy, size - 8, cy), fill=CYAN, width=2)
    d.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=AMBER)
    # corner brackets
    L = 14
    for x, y, dx, dy in (
        (4, 4, 1, 1),
        (size - 5, 4, -1, 1),
        (4, size - 5, 1, -1),
        (size - 5, size - 5, -1, -1),
    ):
        d.line((x, y, x + L * dx, y), fill=CYAN, width=2)
        d.line((x, y, x, y + L * dy), fill=CYAN, width=2)
    return img


def make_divider(w: int = 640, h: int = 4) -> Image.Image:
    img = _new(w, h)
    d = ImageDraw.Draw(img)
    d.rectangle((0, h // 2 - 1, w, h // 2 + 1), fill=(*CYAN[:3], 100))
    d.rectangle((0, h // 2 - 1, w // 5, h // 2 + 1), fill=AMBER)
    return img


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    assets: dict[str, Image.Image] = {
        "switch_on.png": make_switch(True),
        "switch_off.png": make_switch(False),
        "badge_on.png": make_badge(CYAN, CYAN_DIM),
        "badge_off.png": make_badge(OFF_BADGE, SLATE_DIM),
        "badge_idle.png": make_badge((*IDLE[:3], 230), (180, 140, 20, 255)),
        "badge_active.png": make_badge((*ACTIVE[:3], 230), (14, 116, 144, 255)),
        "btn_normal.png": make_button((*PANEL[:3], 255), (*CYAN[:3], 90)),
        "btn_hover.png": make_button((47, 52, 60, 255), CYAN),
        "btn_accent.png": make_button((*AMBER[:3], 230), (160, 70, 25, 255)),
        "card_panel.png": make_card(),
        "header_strip.png": make_header_strip(),
        "header_mark.png": make_icon_radar(96),
        "icon_phone_ok.png": make_icon_phone(True),
        "icon_phone_warn.png": make_icon_phone(False),
        "divider.png": make_divider(),
    }

    for name, image in assets.items():
        path = RAW / name
        image.save(path, "PNG", optimize=True)
        print(f"raw {name} {image.size} {path.stat().st_size}B")


if __name__ == "__main__":
    main()
