"""
Locate / click the War Thunder hangar «Messages» envelope and capture battle
results via Ctrl+C clipboard (primary path; OCR is a separate fallback).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PIL import Image

from .battle_msg_parse import parse_battle_msg_clipboard
from .capture import find_war_thunder_hwnd, grab_wt_client_image
from .ocr_parse import BattleReport
from .wt_input import (
    clear_clipboard,
    click_screen_xy,
    client_to_screen,
    focus_war_thunder,
    poll_clipboard_after_copy,
    press_escape,
)

log = logging.getLogger("lockon_bridge.wt_messages")

_ICON_DIR = Path(__file__).resolve().parent / "message_icons"

# Bottom-right hangar strip where the envelope lives (normalized client coords).
_ENV_ROI = (0.82, 0.86, 1.0, 1.0)
_NCC_MIN = 0.55


def _load_envelope_templates() -> list[Image.Image]:
    out: list[Image.Image] = []
    if not _ICON_DIR.is_dir():
        return out
    for path in sorted(_ICON_DIR.glob("*.png")):
        try:
            out.append(Image.open(path).convert("RGB"))
        except OSError as exc:
            log.debug("envelope template %s: %s", path.name, exc)
    return out


def _ncc(a, b) -> float:
    import numpy as np

    av = a.astype(np.float64).ravel()
    bv = b.astype(np.float64).ravel()
    if av.size != bv.size or av.size == 0:
        return -1.0
    av = av - av.mean()
    bv = bv - bv.mean()
    denom = float(np.sqrt((av * av).sum() * (bv * bv).sum()))
    if denom < 1e-9:
        return -1.0
    return float((av * bv).sum() / denom)


def _match_envelope_in_roi(
    rgb: Image.Image,
) -> tuple[float, tuple[int, int, int, int]] | None:
    """Best template match of the envelope inside the bottom-right ROI."""
    try:
        import numpy as np
    except ImportError:
        return None
    templates = _load_envelope_templates()
    if not templates:
        return None
    w, h = rgb.size
    x0 = int(w * _ENV_ROI[0])
    y0 = int(h * _ENV_ROI[1])
    x1 = int(w * _ENV_ROI[2])
    y1 = int(h * _ENV_ROI[3])
    if x1 - x0 < 16 or y1 - y0 < 16:
        return None
    hay_rgb = rgb.crop((x0, y0, x1, y1))
    hay = np.asarray(hay_rgb, dtype=np.uint8)
    hh, hw = hay.shape[:2]
    field = hay.mean(axis=2)
    best_score = -1.0
    best_box: tuple[int, int, int, int] | None = None
    for tmpl in templates:
        tw0, th0 = tmpl.size
        for scale in (0.6, 0.75, 0.9, 1.0, 1.15, 1.35, 1.6, 1.9):
            tw = max(10, int(round(tw0 * scale)))
            th = max(10, int(round(th0 * scale)))
            if tw >= hw or th >= hh:
                continue
            needle = np.asarray(
                tmpl.resize((tw, th), Image.Resampling.LANCZOS),
                dtype=np.uint8,
            ).mean(axis=2)
            step = max(1, min(tw, th) // 4)
            for y in range(0, hh - th + 1, step):
                for x in range(0, hw - tw + 1, step):
                    score = _ncc(field[y : y + th, x : x + tw], needle)
                    if score > best_score:
                        best_score = score
                        best_box = (x0 + x, y0 + y, x0 + x + tw, y0 + y + th)
            # Refine around best for this scale.
            if best_box is not None:
                bx0, by0, bx1, by1 = best_box
                lx = bx0 - x0
                ly = by0 - y0
                for y in range(max(0, ly - step), min(hh - th, ly + step) + 1):
                    for x in range(max(0, lx - step), min(hw - tw, lx + step) + 1):
                        score = _ncc(field[y : y + th, x : x + tw], needle)
                        if score > best_score:
                            best_score = score
                            best_box = (x0 + x, y0 + y, x0 + x + tw, y0 + y + th)
    if best_box is None or best_score < _NCC_MIN:
        return None
    return best_score, best_box


def _gold_blob_envelope_box(rgb: Image.Image) -> tuple[int, int, int, int] | None:
    """Fallback: bright gold/yellow badge blob in the messages ROI."""
    try:
        import numpy as np
    except ImportError:
        return None
    w, h = rgb.size
    x0 = int(w * _ENV_ROI[0])
    y0 = int(h * _ENV_ROI[1])
    crop = np.asarray(rgb.crop((x0, y0, w, h)), dtype=np.int16)
    if crop.size == 0:
        return None
    r, g, b = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
    # Warm gold / notification yellow.
    mask = (r > 160) & (g > 120) & (b < 120) & (r > b + 40) & (g > b + 20)
    ys, xs = np.where(mask)
    if len(xs) < 40:
        return None
    # Prefer the densest cluster via bounding box of all gold pixels — hangar
    # envelope is usually the only gold control in this strip.
    cx0, cx1 = int(xs.min()), int(xs.max())
    cy0, cy1 = int(ys.min()), int(ys.max())
    bw, bh = cx1 - cx0 + 1, cy1 - cy0 + 1
    if bw < 8 or bh < 8 or bw > crop.shape[1] * 0.5 or bh > crop.shape[0] * 0.7:
        return None
    return x0 + cx0, y0 + cy0, x0 + cx1 + 1, y0 + cy1 + 1


def locate_envelope_client_xy(
    frame: Image.Image | None = None,
) -> tuple[int, int] | None:
    """
    Return client-pixel center of the messages envelope, or None.
    """
    img = frame
    if img is None:
        img = grab_wt_client_image(focus=False, require_foreground=True)
    if img is None:
        return None
    hit = _match_envelope_in_roi(img)
    box = hit[1] if hit is not None else _gold_blob_envelope_box(img)
    if box is None:
        log.info("envelope not found in bottom-right ROI")
        return None
    x0, y0, x1, y1 = box
    score = hit[0] if hit is not None else 0.0
    log.info(
        "envelope at client (%s,%s)-(%s,%s) score=%.2f",
        x0,
        y0,
        x1,
        y1,
        score,
    )
    return (x0 + x1) // 2, (y0 + y1) // 2


def click_envelope(*, frame: Image.Image | None = None) -> bool:
    """Focus WT and click the messages envelope. False if not found."""
    if not focus_war_thunder():
        log.info("click_envelope: WT not focused")
        return False
    hwnd = find_war_thunder_hwnd()
    if hwnd is None:
        return False
    xy = locate_envelope_client_xy(frame)
    if xy is None:
        return False
    screen = client_to_screen(hwnd, xy[0], xy[1])
    if screen is None:
        return False
    click_screen_xy(screen[0], screen[1])
    time.sleep(0.18)
    return True


def capture_clipboard_battle_report(
    *,
    timeout_sec: float = 1.4,
    open_messages: bool = True,
) -> tuple[BattleReport | None, str]:
    """
    Open Messages (optional), Ctrl+C until a battle dump parses, Esc, clear.

    Returns ``(report, reason)``. ``reason`` is ``ok`` or a short fail code.
    """
    if not focus_war_thunder():
        return None, "wt_not_focused"
    if open_messages:
        if not click_envelope():
            return None, "envelope_not_found"
        # Let the panel settle; clear so we only accept freshly copied text.
        clear_clipboard()
        time.sleep(0.12)

    text, report = poll_clipboard_after_copy(
        accept=parse_battle_msg_clipboard,
        timeout_sec=timeout_sec,
        interval_sec=0.08,
        send_copy_each_iter=True,
    )
    # Always dismiss the panel if we opened it (or leave hangar clean).
    press_escape()
    time.sleep(0.05)

    if report is None or not isinstance(report, BattleReport):
        log.info(
            "clipboard results failed (text_len=%s)",
            0 if not text else len(text),
        )
        return None, "parse_timeout"

    clear_clipboard()
    log.info(
        "clipboard results ok RP=%s SL=%s conf=%.2f",
        report.research_points,
        report.silver_lions,
        report.confidence,
    )
    return report, "ok"
