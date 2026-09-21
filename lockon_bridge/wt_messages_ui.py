"""
Locate / click War Thunder hangar «Messages» and capture battle results via Ctrl+C.

Clicks use **normalized content-frame coordinates** (0..1) so FullHD / 1440p / 4K /
ultrawide letterbox map to the same hangar control. Template match is optional;
primary path is a NormPoint cluster (+ optional settings envelope_nx/ny).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PIL import Image

from .battle_msg_parse import (
    looks_like_messages_panel_clipboard,
    parse_battle_msg_clipboard,
)
from .capture import find_war_thunder_hwnd, grab_wt_client_image
from .ocr_parse import BattleReport
from .roi_layout import content_frame
from .wt_input import (
    click_client_xy_restore_cursor,
    focus_war_thunder,
    get_clipboard_text,
    get_client_size,
    poll_clipboard_after_copy,
    press_ctrl_c,
    press_escape,
    restore_clipboard,
    snapshot_clipboard,
)

log = logging.getLogger("lockon_bridge.wt_messages")

_ICON_DIR = Path(__file__).resolve().parent / "message_icons"

# Bottom-right hangar strip search window (content-frame fractions).
_ENV_ROI = (0.78, 0.82, 1.0, 1.0)
_NCC_MIN = 0.62

# Hangar Messages icon — rightmost social control (content-frame NormPoints).
# Cluster covers UI-scale / aspect drift without absolute pixels.
_ENVELOPE_NORM_POINTS: tuple[tuple[float, float], ...] = (
    (0.972, 0.958),
    (0.960, 0.955),
    (0.980, 0.962),
    (0.948, 0.950),
    (0.935, 0.945),
    (0.988, 0.970),
    (0.920, 0.940),
    (0.905, 0.935),
)

# «Битви» / Battles tab inside the Messages panel (upper strip of the panel).
_BATTLES_TAB_NORM_POINTS: tuple[tuple[float, float], ...] = (
    (0.62, 0.20),
    (0.58, 0.19),
    (0.66, 0.21),
    (0.54, 0.18),
    (0.70, 0.22),
    (0.50, 0.17),
    (0.74, 0.20),
)


def norm_to_client_xy(
    frame_w: int,
    frame_h: int,
    nx: float,
    ny: float,
    *,
    frame: Image.Image | None = None,
) -> tuple[int, int]:
    """
    Map content-frame fractions → client pixels.

    When ``frame`` is given, use the same ultrawide letterbox as OCR ROIs.
    """
    nx = max(0.0, min(1.0, float(nx)))
    ny = max(0.0, min(1.0, float(ny)))
    if frame is not None:
        fl, ft, fr, fb = content_frame(frame)
        fw = max(1, fr - fl)
        fh = max(1, fb - ft)
        return fl + int(round(fw * nx)), ft + int(round(fh * ny))
    return int(round(frame_w * nx)), int(round(frame_h * ny))


def _load_envelope_templates() -> list[Image.Image]:
    out: list[Image.Image] = []
    if not _ICON_DIR.is_dir():
        return out
    for path in sorted(_ICON_DIR.glob("envelope*.png")):
        # Skip tiny placeholder assets that cannot match a real UI icon.
        try:
            img = Image.open(path).convert("RGB")
        except OSError as exc:
            log.debug("envelope template %s: %s", path.name, exc)
            continue
        if min(img.size) < 20:
            continue
        out.append(img)
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
    try:
        import numpy as np
    except ImportError:
        return None
    templates = _load_envelope_templates()
    if not templates:
        return None
    w, h = rgb.size
    fl, ft, fr, fb = content_frame(rgb)
    fw = max(1, fr - fl)
    fh = max(1, fb - ft)
    x0 = fl + int(fw * _ENV_ROI[0])
    y0 = ft + int(fh * _ENV_ROI[1])
    x1 = fl + int(fw * _ENV_ROI[2])
    y1 = ft + int(fh * _ENV_ROI[3])
    if x1 - x0 < 16 or y1 - y0 < 16:
        return None
    hay = np.asarray(rgb.crop((x0, y0, x1, y1)), dtype=np.uint8)
    hh, hw = hay.shape[:2]
    field = hay.mean(axis=2)
    best_score = -1.0
    best_box: tuple[int, int, int, int] | None = None
    for tmpl in templates:
        tw0, th0 = tmpl.size
        for scale in (0.5, 0.7, 0.9, 1.1, 1.4, 1.8, 2.2):
            tw = max(12, int(round(tw0 * scale)))
            th = max(12, int(round(th0 * scale)))
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
    if best_box is None or best_score < _NCC_MIN:
        return None
    return best_score, best_box


def _gold_blob_envelope_box(rgb: Image.Image) -> tuple[int, int, int, int] | None:
    try:
        import numpy as np
    except ImportError:
        return None
    fl, ft, fr, fb = content_frame(rgb)
    fw = max(1, fr - fl)
    fh = max(1, fb - ft)
    x0 = fl + int(fw * _ENV_ROI[0])
    y0 = ft + int(fh * _ENV_ROI[1])
    crop = np.asarray(rgb.crop((x0, y0, fr, fb)), dtype=np.int16)
    if crop.size == 0:
        return None
    r, g, b = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
    mask = (r > 160) & (g > 120) & (b < 120) & (r > b + 40) & (g > b + 20)
    ys, xs = np.where(mask)
    if len(xs) < 40:
        return None
    cx0, cx1 = int(xs.min()), int(xs.max())
    cy0, cy1 = int(ys.min()), int(ys.max())
    bw, bh = cx1 - cx0 + 1, cy1 - cy0 + 1
    if bw < 8 or bh < 8 or bw > crop.shape[1] * 0.5 or bh > crop.shape[0] * 0.7:
        return None
    return x0 + cx0, y0 + cy0, x0 + cx1 + 1, y0 + cy1 + 1


def _settings_envelope_norm() -> tuple[float, float] | None:
    try:
        from .settings import load_settings

        s = load_settings()
        if s.envelope_nx is None or s.envelope_ny is None:
            return None
        return float(s.envelope_nx), float(s.envelope_ny)
    except Exception:  # noqa: BLE001
        return None


def envelope_click_candidates(
    frame: Image.Image,
) -> list[tuple[str, int, int]]:
    """
    Ordered (label, client_x, client_y) click targets for the Messages envelope.

    Resolution-independent: norms are mapped through ``content_frame``.
    """
    w, h = frame.size
    out: list[tuple[str, int, int]] = []
    seen: set[tuple[int, int]] = set()

    def _add(label: str, x: int, y: int) -> None:
        key = (x // 3, y // 3)  # de-dupe near-duplicates
        if key in seen:
            return
        if not (0 <= x < w and 0 <= y < h):
            return
        seen.add(key)
        out.append((label, x, y))

    calib = _settings_envelope_norm()
    if calib is not None:
        x, y = norm_to_client_xy(w, h, calib[0], calib[1], frame=frame)
        _add(f"calib:{calib[0]:.3f},{calib[1]:.3f}", x, y)

    hit = _match_envelope_in_roi(frame)
    if hit is not None:
        score, box = hit
        x0, y0, x1, y1 = box
        _add(f"tmpl:{score:.2f}", (x0 + x1) // 2, (y0 + y1) // 2)

    gold = _gold_blob_envelope_box(frame)
    if gold is not None:
        x0, y0, x1, y1 = gold
        _add("gold", (x0 + x1) // 2, (y0 + y1) // 2)

    for i, (nx, ny) in enumerate(_ENVELOPE_NORM_POINTS):
        x, y = norm_to_client_xy(w, h, nx, ny, frame=frame)
        _add(f"norm:{i}:{nx:.3f},{ny:.3f}", x, y)

    return out


def _probe_messages_open() -> tuple[str, object | None]:
    """One Ctrl+C; return (text, parsed_report_or_None)."""
    press_ctrl_c()
    time.sleep(0.04)
    text = get_clipboard_text()
    report = parse_battle_msg_clipboard(text) if text else None
    return text or "", report


def click_envelope(*, frame: Image.Image | None = None) -> bool:
    """
    Focus WT and click Messages using NormPoint candidates (+ optional match).

    Tries candidates until Ctrl+C looks like a Messages panel dump.
    """
    if not focus_war_thunder():
        log.info("click_envelope: WT not focused")
        return False
    hwnd = find_war_thunder_hwnd()
    if hwnd is None:
        return False
    img = frame
    if img is None:
        img = grab_wt_client_image(focus=False, require_foreground=True)
    if img is None:
        size = get_client_size(hwnd)
        if size is None:
            return False
        # Synthetic blank for norm mapping only (no template/gold).
        img = Image.new("RGB", size)

    candidates = envelope_click_candidates(img)
    if not candidates:
        log.info("envelope: no click candidates")
        return False

    for label, x, y in candidates:
        log.info("envelope try %s client=(%s,%s) frame=%sx%s", label, x, y, *img.size)
        if not click_client_xy_restore_cursor(hwnd, x, y):
            continue
        time.sleep(0.08)
        text, report = _probe_messages_open()
        if report is not None or looks_like_messages_panel_clipboard(text):
            log.info(
                "envelope open ok via %s (text_len=%s battle=%s)",
                label,
                len(text),
                report is not None,
            )
            return True
        # Wrong control / nothing — dismiss and try next point.
        press_escape()
        time.sleep(0.04)

    log.info("envelope not opened after %s candidate(s)", len(candidates))
    return False


def _ensure_battles_tab(hwnd: int, frame: Image.Image) -> bool:
    """
    If current clipboard is not a battle dump, click Battles-tab NormPoints.
    Returns True if a battle parse succeeds afterward.
    """
    text, report = _probe_messages_open()
    if report is not None:
        return True
    if not looks_like_messages_panel_clipboard(text):
        # Panel may still be open without copyable text yet.
        pass

    w, h = frame.size
    for i, (nx, ny) in enumerate(_BATTLES_TAB_NORM_POINTS):
        x, y = norm_to_client_xy(w, h, nx, ny, frame=frame)
        log.info("battles tab try %s norm=(%.3f,%.3f) client=(%s,%s)", i, nx, ny, x, y)
        click_client_xy_restore_cursor(hwnd, x, y)
        time.sleep(0.06)
        text, report = _probe_messages_open()
        if report is not None:
            log.info("battles tab ok via point %s", i)
            return True
    return False


def capture_clipboard_battle_report(
    *,
    timeout_sec: float = 1.4,
    open_messages: bool = True,
    hangar_settle_sec: float = 0.35,
) -> tuple[BattleReport | None, str]:
    """
    Open Messages, ensure Battles, Ctrl+C until parse, Esc, restore user clipboard.

    Returns ``(report, reason)``. ``reason`` is ``ok`` or a short fail code.
    """
    snap = snapshot_clipboard()
    opened = False
    try:
        if not focus_war_thunder():
            return None, "wt_not_focused"
        if hangar_settle_sec > 0:
            time.sleep(hangar_settle_sec)

        hwnd = find_war_thunder_hwnd()
        if hwnd is None:
            return None, "wt_not_focused"

        frame = grab_wt_client_image(focus=False, require_foreground=True)
        if frame is None:
            size = get_client_size(hwnd)
            if size is None:
                return None, "wt_not_focused"
            frame = Image.new("RGB", size)

        if open_messages:
            if not click_envelope(frame=frame):
                return None, "envelope_not_found"
            opened = True
            time.sleep(0.05)
            # Re-grab in case UI scaled; norms still apply on last frame size.
            if not _ensure_battles_tab(hwnd, frame):
                # Still allow a longer poll — last battle may already be focused.
                pass

        text, report = poll_clipboard_after_copy(
            accept=parse_battle_msg_clipboard,
            timeout_sec=timeout_sec,
            interval_sec=0.08,
            send_copy_each_iter=True,
        )

        if opened or open_messages:
            press_escape()
            time.sleep(0.04)

        if report is None or not isinstance(report, BattleReport):
            reason = "wrong_tab" if text and looks_like_messages_panel_clipboard(text) else "parse_timeout"
            log.info(
                "clipboard results failed reason=%s text_len=%s",
                reason,
                0 if not text else len(text),
            )
            return None, reason

        log.info(
            "clipboard results ok RP=%s SL=%s conf=%.2f",
            report.research_points,
            report.silver_lions,
            report.confidence,
        )
        return report, "ok"
    finally:
        restore_clipboard(snap)
