"""
Locate / click War Thunder hangar «Messages» and capture battle results via Ctrl+C.

Clicks use **normalized content-frame coordinates** (0..1) so FullHD / 1440p / 4K /
ultrawide letterbox map to the same hangar control. Primary target is the rightmost
icon on the bottom social bar (envelope).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PIL import Image

from .battle_msg_parse import (
    detect_battle_outcome,
    detect_outcome_from_frame,
    parse_battle_msg_clipboard,
)
from .capture import find_war_thunder_hwnd, grab_wt_client_image
from .ocr_parse import BattleReport
from .roi_layout import content_frame
from .wt_input import (
    ExclusiveWtSession,
    click_client_xy,
    click_client_xy_restore_cursor,
    client_to_screen,
    focus_war_thunder,
    get_clipboard_text,
    get_client_size,
    get_cursor_pos,
    move_cursor_screen_xy,
    poll_clipboard_after_copy,
    press_arrow_down,
    press_ctrl_c,
    press_escape,
    restore_clipboard,
    scroll_wheel,
    set_cursor_pos,
    snapshot_clipboard,
)

log = logging.getLogger("lockon_bridge.wt_messages")

_ICON_DIR = Path(__file__).resolve().parent / "message_icons"

# Bottom social-bar search window (content-frame fractions).
_ENV_ROI = (0.78, 0.90, 1.0, 1.0)
_NCC_MIN = 0.55

# Measured on live 2560×1600 hangar: envelope = rightmost bar icon ≈ (0.943, 0.971).
# Soft session keeps focus/clip inside the WT client — click the real icon coords,
# never hide the taskbar (that flashes desktop) and never offset the target.
_ENVELOPE_NORM_POINTS: tuple[tuple[float, float], ...] = (
    (0.943, 0.971),
    (0.940, 0.968),
    (0.946, 0.974),
    (0.938, 0.970),
    (0.948, 0.972),
    (0.935, 0.968),
    (0.950, 0.975),
)

# «Битви» / Battles — second tab; Y is the tab *text* row (~0.123 on 1600p).
# Measured via OCR on 2560×1600: center ≈ (0.239, 0.123).
_BATTLES_TAB_NORM_POINTS: tuple[tuple[float, float], ...] = (
    (0.239, 0.123),
    (0.245, 0.123),
    (0.233, 0.122),
    (0.250, 0.124),
)

# Messages modal close «X» (top-right of the dark panel). Esc is often ignored
# by WT the same way injected Ctrl+C is — mouse close is reliable.
# Keep points inset from the extreme top-right chrome.
_CLOSE_X_NORM_POINTS: tuple[tuple[float, float], ...] = (
    (0.855, 0.118),
    (0.848, 0.115),
    (0.862, 0.120),
    (0.840, 0.112),
    (0.880, 0.110),
    (0.900, 0.108),
)

_EMPTY_MSG = (
    "немає повідомлень",
    "нет сообщений",
    "no messages",
    "keine nachrichten",
)

_BATTLES_LABELS = (
    "битви",
    "битва",
    "бои",
    "бой",
    "battles",
    "battle",
    "buts",  # OCR mangling of Битиви
    "6итви",
)


def norm_to_client_xy(
    frame_w: int,
    frame_h: int,
    nx: float,
    ny: float,
    *,
    frame: Image.Image | None = None,
) -> tuple[int, int]:
    """Map content-frame fractions → client pixels (ultrawide-aware when ``frame``)."""
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
        try:
            img = Image.open(path).convert("RGB")
        except OSError as exc:
            log.debug("envelope template %s: %s", path.name, exc)
            continue
        if min(img.size) < 16:
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
        for scale in (0.7, 0.85, 1.0, 1.15, 1.35, 1.6, 2.0, 2.4):
            tw = max(12, int(round(tw0 * scale)))
            th = max(12, int(round(th0 * scale)))
            if tw >= hw or th >= hh:
                continue
            needle = np.asarray(
                tmpl.resize((tw, th), Image.Resampling.LANCZOS),
                dtype=np.uint8,
            ).mean(axis=2)
            step = max(1, min(tw, th) // 5)
            for y in range(0, hh - th + 1, step):
                for x in range(0, hw - tw + 1, step):
                    score = _ncc(field[y : y + th, x : x + tw], needle)
                    if score > best_score:
                        best_score = score
                        best_box = (x0 + x, y0 + y, x0 + x + tw, y0 + y + th)
    if best_box is None or best_score < _NCC_MIN:
        if best_box is not None:
            log.info("envelope tmpl best score=%.2f (below %.2f)", best_score, _NCC_MIN)
        return None
    return best_score, best_box


def _light_icon_envelope_xy(rgb: Image.Image) -> tuple[int, int] | None:
    """
    Rightmost bright icon on the bottom social bar (envelope is last).

    Works without a template — white/grey stroke icons on a dark bar.
    """
    try:
        import numpy as np
    except ImportError:
        return None
    fl, ft, fr, fb = content_frame(rgb)
    fw = max(1, fr - fl)
    fh = max(1, fb - ft)
    x0 = fl + int(fw * 0.65)
    y0 = ft + int(fh * 0.92)
    roi = np.asarray(rgb.crop((x0, y0, fr, fb)), dtype=np.int16)
    if roi.size == 0:
        return None
    r, g, b = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]
    light = (r > 160) & (g > 160) & (b > 160)
    light &= np.abs(r.astype(int) - g) < 30
    light &= np.abs(g.astype(int) - b) < 30
    cols = light.any(axis=0)
    clusters: list[tuple[int, int, int, int, int]] = []
    i = 0
    n = int(cols.shape[0])
    while i < n:
        if not cols[i]:
            i += 1
            continue
        j = i
        while j < n and cols[j]:
            j += 1
        if j - i >= 8:
            sub = light[:, i:j]
            ys, _xs = np.where(sub)
            if len(ys) >= 20:
                clusters.append(
                    (i, j, int(ys.min()), int(ys.max()), int(len(ys)))
                )
        i = j
    if not clusters:
        return None
    # Envelope = rightmost cluster in the bar.
    c = max(clusters, key=lambda t: t[1])
    cx = x0 + (c[0] + c[1]) // 2
    cy = y0 + (c[2] + c[3]) // 2
    log.info(
        "envelope bar-icon client=(%s,%s) norm=(%.4f,%.4f) clusters=%s",
        cx,
        cy,
        (cx - fl) / float(fw),
        (cy - ft) / float(fh),
        len(clusters),
    )
    return cx, cy


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
    """Ordered (label, client_x, client_y) for the Messages envelope."""
    w, h = frame.size
    out: list[tuple[str, int, int]] = []
    seen: set[tuple[int, int]] = set()

    def _add(label: str, x: int, y: int) -> None:
        key = (x // 2, y // 2)
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

    bar = _light_icon_envelope_xy(frame)
    if bar is not None:
        _add("bar-rightmost", bar[0], bar[1])

    for i, (nx, ny) in enumerate(_ENVELOPE_NORM_POINTS):
        x, y = norm_to_client_xy(w, h, nx, ny, frame=frame)
        _add(f"norm:{i}:{nx:.3f},{ny:.3f}", x, y)

    return out


def _probe_copy() -> tuple[str, BattleReport | None]:
    press_ctrl_c()
    time.sleep(0.05)
    text = get_clipboard_text()
    report = parse_battle_msg_clipboard(text) if text else None
    return text or "", report


def _panel_looks_empty(frame: Image.Image) -> bool:
    """True when Messages body shows «Немає повідомлень» / No messages."""
    try:
        import pytesseract

        from .ocr_backends import find_tesseract_exe

        exe = find_tesseract_exe()
        if exe is None:
            return False
        pytesseract.pytesseract.tesseract_cmd = str(exe)
        w, h = frame.size
        body = frame.crop((int(w * 0.22), int(h * 0.25), int(w * 0.78), int(h * 0.70)))
        text = (pytesseract.image_to_string(body, lang="ukr+eng") or "").lower()
        return any(s in text for s in _EMPTY_MSG)
    except Exception as exc:  # noqa: BLE001
        log.debug("empty-panel OCR skipped: %s", exc)
        return False


def _prep_messages_ocr(img: Image.Image) -> Image.Image:
    """Boost contrast/scale for washed GDI Messages grabs."""
    from PIL import ImageEnhance, ImageOps

    g = ImageOps.grayscale(img)
    g = ImageOps.autocontrast(g, cutoff=1)
    g = ImageEnhance.Contrast(g).enhance(1.6)
    scale = 2 if max(g.size) < 1400 else 1
    if scale > 1:
        g = g.resize((g.size[0] * scale, g.size[1] * scale), Image.Resampling.LANCZOS)
    return g


def _ocr_messages_battle_report(frame: Image.Image) -> BattleReport | None:
    """
    Read Total/Session from an already-open Messages → Battles panel.

    Used only when synthetic Ctrl+C is empty (WT filters injected keys).
    Money comes from the **footer band** (Session / Total), not the hangar.
    """
    try:
        import pytesseract

        from .ocr_backends import find_tesseract_exe
    except Exception as exc:  # noqa: BLE001
        log.debug("messages OCR imports failed: %s", exc)
        return None
    exe = find_tesseract_exe()
    if exe is None:
        log.info("messages OCR skipped: no tesseract")
        return None
    pytesseract.pytesseract.tesseract_cmd = str(exe)

    footer = _content_crop(frame, 0.12, 0.68, 0.90, 0.98)
    summary = _content_crop(frame, 0.12, 0.45, 0.90, 0.78)
    panel = _content_crop(frame, 0.14, 0.10, 0.86, 0.95)

    try:
        footer_text = (
            pytesseract.image_to_string(_prep_messages_ocr(footer), lang="ukr+eng") or ""
        )
        summary_text = (
            pytesseract.image_to_string(_prep_messages_ocr(summary), lang="ukr+eng") or ""
        )
        panel_text = (
            pytesseract.image_to_string(_prep_messages_ocr(panel), lang="ukr+eng") or ""
        )
    except Exception as exc:  # noqa: BLE001
        log.info("messages panel OCR failed: %s", exc)
        return None

    # Prefer footer-only (Total + Session). Fall back to wider crops only if needed.
    candidates = (
        footer_text,
        f"{footer_text}\n{summary_text}",
        f"{footer_text}\n{summary_text}\n{panel_text}",
    )
    report: BattleReport | None = None
    for text in candidates:
        text = (text or "").strip()
        if not text:
            continue
        got = parse_battle_msg_clipboard(text)
        if got is not None and (got.session_id or "").strip():
            report = got
            break
    if report is None:
        log.info(
            "messages panel OCR no session footer=%r summary=%r",
            footer_text[:120].replace("\n", " | "),
            summary_text[:80].replace("\n", " | "),
        )
        return None

    log.info(
        "messages panel OCR ok SL=%s RP=%s session=%s footer=%r",
        report.silver_lions,
        report.research_points,
        (report.session_id or "")[:12],
        footer_text[:120].replace("\n", " | "),
    )
    outcome = detect_outcome_from_frame(frame)
    if outcome == "undecided":
        outcome = detect_battle_outcome(
            "\n".join(p for p in candidates if p).strip()
        )
    if outcome != report.outcome or (outcome != "undecided" and report.provisional):
        from dataclasses import replace

        provisional = (
            False
            if (outcome == "undecided" and not report.provisional)
            else (outcome == "undecided")
        )
        report = replace(report, outcome=outcome, provisional=provisional)
    return BattleReport(
        captured_at_epoch_millis=report.captured_at_epoch_millis,
        research_points=report.research_points,
        silver_lions=report.silver_lions,
        confidence=min(float(report.confidence), 0.93),
        source="messages-ocr",
        outcome=report.outcome,
        raw_hash=report.raw_hash,
        provisional=bool(report.provisional),
        session_id=report.session_id or "",
    )


def _modal_fingerprint(frame: Image.Image | None) -> float:
    """Mean luminance of the central Messages modal band (open ≈ dark panel)."""
    if frame is None:
        return -1.0
    try:
        from PIL import ImageStat

        w, h = frame.size
        band = frame.convert("L").crop(
            (int(w * 0.20), int(h * 0.12), int(w * 0.80), int(h * 0.55))
        )
        return float(ImageStat.Stat(band).mean[0])
    except Exception:  # noqa: BLE001
        return -1.0


def _modal_still_open(before: float, after_frame: Image.Image | None) -> bool:
    """True when the Messages modal likely remains after a close click."""
    after = _modal_fingerprint(after_frame)
    if before < 0 or after < 0:
        # Unknown — assume still open so we keep trying close points.
        return True
    # Open modal is a dark overlay; hangar is brighter/busier. If mean barely
    # moved, the X click missed.
    return abs(after - before) < 12.0 and after < 90.0


def _messages_panel_open(frame: Image.Image | None) -> bool:
    """Heuristic: Messages/Battles modal still visible (OCR + luminance)."""
    if frame is None:
        return False
    fp = _modal_fingerprint(frame)
    if 0 <= fp < 85.0:
        # Dark central band — likely the modal, even if OCR is washed.
        return True
    try:
        import pytesseract

        from .ocr_backends import find_tesseract_exe

        exe = find_tesseract_exe()
        if exe is None:
            return fp >= 0 and fp < 100.0
        pytesseract.pytesseract.tesseract_cmd = str(exe)
        w, h = frame.size
        head = frame.crop((int(w * 0.15), int(h * 0.08), int(w * 0.85), int(h * 0.22)))
        text = (pytesseract.image_to_string(head, lang="ukr+eng") or "").lower()
        markers = (
            "битви",
            "бои",
            "battles",
            "повідом",
            "сообщен",
            "messages",
            "ctrl+c",
            "ctrl + c",
        )
        return any(m in text for m in markers)
    except Exception:  # noqa: BLE001
        return False


def _find_close_x_client_xy(frame: Image.Image) -> tuple[int, int] | None:
    """Locate the Messages modal «X» via OCR glyph, then bright-pixel fallback."""
    w, h = frame.size
    x0, y0 = int(w * 0.72), int(h * 0.05)
    x1, y1 = int(w * 0.96), int(h * 0.22)
    if x1 - x0 < 20 or y1 - y0 < 20:
        return None
    try:
        import pytesseract

        from .ocr_backends import find_tesseract_exe

        exe = find_tesseract_exe()
        if exe is not None:
            pytesseract.pytesseract.tesseract_cmd = str(exe)
            crop = frame.crop((x0, y0, x1, y1))
            data = pytesseract.image_to_data(
                crop, lang="eng+ukr", output_type=pytesseract.Output.DICT
            )
            best: tuple[float, int, int] | None = None
            for i, raw in enumerate(data["text"]):
                token = (raw or "").strip()
                ok = token in ("X", "x", "×", "Х", "х") or (
                    len(token) == 1 and token.lower() in ("x", "х")
                )
                if not ok:
                    continue
                try:
                    conf = float(data["conf"][i])
                except ValueError:
                    conf = 0.0
                ww, hh = int(data["width"][i]), int(data["height"][i])
                if ww < 6 or hh < 6 or ww > 80 or hh > 80:
                    continue
                cx = x0 + int(data["left"][i]) + ww // 2
                cy = y0 + int(data["top"][i]) + hh // 2
                score = conf + (20.0 if token in ("X", "×", "Х") else 0.0)
                if best is None or score > best[0]:
                    best = (score, cx, cy)
            if best is not None:
                log.info(
                    "close X via OCR client=(%s,%s) conf≈%.0f",
                    best[1],
                    best[2],
                    best[0],
                )
                return best[1], best[2]
    except Exception as exc:  # noqa: BLE001
        log.debug("close X OCR skipped: %s", exc)

    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return None
    roi = np.asarray(frame.crop((x0, y0, x1, y1)).convert("L"), dtype=np.float64)
    thr = float(np.percentile(roi, 94))
    mask = roi >= max(180.0, thr)
    if int(mask.sum()) < 8:
        return None
    best_px: tuple[float, int, int] | None = None
    for cy in range(5, roi.shape[0] - 5, 2):
        for cx in range(5, roi.shape[1] - 5, 2):
            if not mask[cy, cx]:
                continue
            patch = roi[cy - 4 : cy + 5, cx - 4 : cx + 5]
            mean = float(patch.mean())
            if mean < 170:
                continue
            score = mean + 0.15 * cx
            if best_px is None or score > best_px[0]:
                best_px = (score, x0 + cx, y0 + cy)
    if best_px is None:
        return None
    return best_px[1], best_px[2]


def _panel_looks_closed(before_fp: float, after_frame: Image.Image | None) -> bool:
    """True when the Messages modal likely disappeared."""
    after = _modal_fingerprint(after_frame)
    if after < 0:
        return False
    # Hangar is brighter than the dark modal band.
    if after >= 95.0:
        return True
    if before_fp >= 0 and (after - before_fp) >= 14.0:
        return True
    # Modal markers gone (tabs / dark overlay).
    try:
        if not _messages_panel_open(after_frame):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _nav_frame(*, require_foreground: bool, allow_mss: bool = False) -> Image.Image | None:
    """Grab for Messages navigation (GDI) or panel OCR (mss allowed)."""
    return grab_wt_client_image(
        focus=False,
        require_foreground=require_foreground,
        allow_mss=allow_mss,
    )


def _find_battles_tab_xy(frame: Image.Image) -> tuple[int, int] | None:
    """OCR the Messages tab strip for Battles / Битви."""
    try:
        import pytesseract

        from .ocr_backends import find_tesseract_exe

        exe = find_tesseract_exe()
        if exe is None:
            return None
        pytesseract.pytesseract.tesseract_cmd = str(exe)
        w, h = frame.size
        x0, y0 = int(w * 0.10), int(h * 0.10)
        x1, y1 = int(w * 0.92), int(h * 0.22)
        tab = frame.crop((x0, y0, x1, y1))
        data = pytesseract.image_to_data(
            tab, lang="ukr+eng", output_type=pytesseract.Output.DICT
        )
        best: tuple[float, int, int] | None = None
        second_left: tuple[int, int] | None = None
        left_words: list[tuple[int, int, int, str]] = []
        n = len(data["text"])
        for i in range(n):
            raw = (data["text"][i] or "").strip()
            if not raw:
                continue
            try:
                conf = float(data["conf"][i])
            except ValueError:
                conf = -1.0
            if conf < 0:
                continue
            ww, hh = int(data["width"][i]), int(data["height"][i])
            if ww < 12 or hh < 8:
                continue
            cx = x0 + int(data["left"][i]) + ww // 2
            cy = y0 + int(data["top"][i]) + hh // 2
            left_words.append((int(data["left"][i]), cx, cy, raw))
            low = raw.lower().replace("і", "i").replace("ї", "i")
            hit = any(label in raw.lower() or label in low for label in _BATTLES_LABELS)
            if not hit and low in ("buts", "bitv", "bitvi", "6uts", "bitbu"):
                hit = True
            if not hit:
                continue
            score = conf + (50.0 if "бит" in raw.lower() or "battle" in low else 0.0)
            # Prefer tabs on the left half (Battles is early).
            if cx > w * 0.45:
                score -= 30.0
            if best is None or score > best[0]:
                best = (score, cx, cy)
        if best is not None:
            log.info(
                "battles tab OCR at client=(%s,%s) score=%.1f",
                best[1],
                best[2],
                best[0],
            )
            return best[1], best[2]
        # Fallback: second word from the left on the tab strip (Всі, Битви, …).
        left_words.sort(key=lambda t: t[0])
        if len(left_words) >= 2:
            _left, cx, cy, raw = left_words[1]
            log.info("battles tab OCR fallback 2nd word %r at (%s,%s)", raw, cx, cy)
            return cx, cy
        return None
    except Exception as exc:  # noqa: BLE001
        log.debug("battles tab OCR failed: %s", exc)
        return None


def _pick_battles_tab_xy(frame: Image.Image) -> tuple[str, int, int]:
    """Battles tab — NormPoints only (OCR mis-clicks garbage like '@')."""
    w, h = frame.size
    nx, ny = _BATTLES_TAB_NORM_POINTS[0]
    x, y = norm_to_client_xy(w, h, nx, ny, frame=frame)
    return f"norm:{nx:.3f},{ny:.3f}", x, y


def click_envelope(*, frame: Image.Image | None = None) -> bool:
    """Focus WT and click Messages once (best candidate)."""
    if not focus_war_thunder():
        log.info("click_envelope: WT not focused (is it minimized?)")
        return False
    hwnd = find_war_thunder_hwnd()
    if hwnd is None:
        return False
    img = frame or _nav_frame(require_foreground=True)
    if img is None:
        size = get_client_size(hwnd)
        if size is None:
            return False
        img = Image.new("RGB", size)
    candidates = envelope_click_candidates(img)
    if not candidates:
        return False
    _label, x, y = candidates[0]
    return click_client_xy_restore_cursor(hwnd, x, y)


def _panel_frame_for_ocr(fallback: Image.Image) -> Image.Image:
    """
    Frame for Messages panel read when Ctrl+C is empty.

    Prefer **mss** — GDI often returns the hangar under the DX overlay (or black),
    which is what produced junk like SL=306. Only return a frame that still looks
    like the Messages modal when possible.
    """
    mss = _nav_frame(require_foreground=False, allow_mss=True)
    if mss is not None and _messages_panel_open(mss):
        return mss
    gdi = _nav_frame(require_foreground=False, allow_mss=False)
    if gdi is not None and _messages_panel_open(gdi):
        return gdi
    if mss is not None:
        log.info("mss frame present but Messages modal not detected")
        return mss
    return gdi or fallback


def _content_crop(
    frame: Image.Image,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> Image.Image:
    """Crop fractions of the ultrawide-aware content frame."""
    fl, ft, fr, fb = content_frame(frame)
    cw = max(1, fr - fl)
    ch = max(1, fb - ft)
    return frame.crop(
        (
            fl + int(cw * left),
            ft + int(ch * top),
            fl + int(cw * right),
            ft + int(ch * bottom),
        )
    )


def _close_messages_via_esc(hwnd: int | None = None) -> None:
    """
    Close Messages: focus → soft-click detail → Esc.

    One Esc only (no Esc×2). If hwnd is known, follow with a single close-X
    click — injected Esc is often ignored by Dagor while mouse clicks work.
    """
    log.info("4/4 close Messages via Esc x1")
    focus_war_thunder(allow_unminimize=False)
    if hwnd is not None:
        try:
            size = get_client_size(hwnd)
            if size is not None:
                w, h = size
                x, y = norm_to_client_xy(w, h, 0.55, 0.52)
                click_client_xy(hwnd, x, y, hold_sec=0.05)
                time.sleep(0.08)
        except Exception:  # noqa: BLE001
            pass
    press_escape()
    time.sleep(0.20)
    if hwnd is None:
        return
    try:
        size = get_client_size(hwnd)
        if size is None:
            return
        w, h = size
        nx, ny = _CLOSE_X_NORM_POINTS[0]
        xx, yy = norm_to_client_xy(w, h, nx, ny)
        log.info("4/4 Esc follow-up close-X client=(%s,%s)", xx, yy)
        click_client_xy(hwnd, xx, yy, hold_sec=0.06)
        time.sleep(0.15)
    except Exception:  # noqa: BLE001
        pass


def _close_messages_panel(hwnd: int, frame: Image.Image | None = None) -> None:
    """Close Messages with a single Esc."""
    del frame  # unused — Esc only
    _close_messages_via_esc(hwnd)


def _arm_messages_keyboard(hwnd: int, frame: Image.Image) -> None:
    """Soft-click the battle detail so keys / wheel hit the report pane."""
    focus_war_thunder(allow_unminimize=False)
    w, h = frame.size
    x, y = norm_to_client_xy(w, h, 0.55, 0.55, frame=frame)
    click_client_xy(hwnd, x, y, hold_sec=0.06)
    time.sleep(0.12)


def _scroll_messages_detail(hwnd: int, frame: Image.Image) -> None:
    """Scroll the detail pane down to reveal Session / Total if clipped."""
    w, h = frame.size
    x, y = norm_to_client_xy(w, h, 0.55, 0.62, frame=frame)
    screen = client_to_screen(hwnd, x, y)
    if screen is None:
        return
    move_cursor_screen_xy(screen[0], screen[1])
    time.sleep(0.04)
    for _ in range(2):
        scroll_wheel(notches=-6, settle_sec=0.10)
    time.sleep(0.12)


def _report_looks_like_battle_footer(report: BattleReport | None) -> bool:
    """Require session hex + footer totals (reject incomplete Ctrl+C dumps)."""
    if report is None:
        return False
    if not (report.session_id or "").strip():
        return False
    sl = int(report.silver_lions)
    rp = int(report.research_points)
    if sl == 0 and rp == 0:
        return True
    if sl < 100 and rp < 100:
        return False
    return True


def _read_open_messages_battle(
    frame: Image.Image,
    *,
    hwnd: int | None = None,
    timeout_sec: float = 3.5,
    reject_clipboard: str | None = None,
) -> tuple[BattleReport | None, Image.Image]:
    """
    Focus detail → simultaneous Ctrl+C chord → parse clipboard.

    No OCR. WT often ignores injected keys; we still chord correctly and keep
    polling so a physical Ctrl+C during the wait can land.
    """
    close_frame = frame
    if hwnd is not None:
        _arm_messages_keyboard(hwnd, frame)

    _text, report = poll_clipboard_after_copy(
        accept=parse_battle_msg_clipboard,
        timeout_sec=max(2.5, timeout_sec),
        interval_sec=0.10,
        send_copy_each_iter=True,
        reject_text=reject_clipboard,
        hwnd=hwnd,
    )
    if isinstance(report, BattleReport) and _report_looks_like_battle_footer(report):
        log.info(
            "Ctrl+C clipboard ok SL=%s RP=%s session=%s",
            report.silver_lions,
            report.research_points,
            (report.session_id or "")[:12],
        )
        return report, close_frame

    if _text:
        log.info(
            "Ctrl+C text present but not a battle footer (%s chars)",
            len(_text),
        )
    else:
        log.info(
            "Ctrl+C empty — WT ignored synthetic chord "
            "(OS key-state OK; clipboard sequence unchanged)"
        )
    return None, close_frame


def capture_clipboard_battle_report(
    *,
    timeout_sec: float = 3.5,
    open_messages: bool = True,
    hangar_settle_sec: float = 0.25,
    refresh_session_ids: frozenset[str] | set[str] | None = None,
    max_arrow_steps: int = 6,
) -> tuple[BattleReport | None, dict[str, BattleReport], str]:
    """
    Hangar gesture:

      1. Envelope (1 click)
      2. Battles tab (1 click)
      3. Soft-focus detail → simultaneous Ctrl+C chord (clipboard only)
      4. Esc x1 (+ one close-X — injected Esc is often ignored)

    Returns ``(top_report|None, refreshed_by_session_id, reason)``.
    """
    snap = None
    opened = False
    got_report = False
    prev_cursor = get_cursor_pos()
    refreshed: dict[str, BattleReport] = {}
    want = {s.lower() for s in (refresh_session_ids or ()) if s}
    try:
        if not focus_war_thunder(allow_unminimize=True):
            return None, refreshed, "wt_not_focused"
        if hangar_settle_sec > 0:
            time.sleep(hangar_settle_sec)

        hwnd = find_war_thunder_hwnd()
        if hwnd is None:
            return None, refreshed, "wt_not_focused"

        with ExclusiveWtSession(hwnd):
            frame = _nav_frame(require_foreground=True)
            if frame is None:
                size = get_client_size(hwnd)
                if size is None:
                    return None, refreshed, "wt_not_focused"
                frame = Image.new("RGB", size)

            if open_messages:
                candidates = envelope_click_candidates(frame)
                if not candidates:
                    return None, refreshed, "envelope_not_found"
                label, ex, ey = candidates[0]
                log.info(
                    "1/4 envelope %s client=(%s,%s) frame=%sx%s",
                    label,
                    ex,
                    ey,
                    frame.size[0],
                    frame.size[1],
                )
                if not click_client_xy(hwnd, ex, ey):
                    return None, refreshed, "wt_not_focused"
                opened = True
                time.sleep(0.40)
                if not focus_war_thunder(allow_unminimize=False):
                    if not focus_war_thunder(allow_unminimize=True):
                        log.info("WT lost focus after envelope")
                        return None, refreshed, "wt_not_focused"
                fresh = _nav_frame(require_foreground=False, allow_mss=False)
                if fresh is not None:
                    frame = fresh

            tab_label, tx, ty = _pick_battles_tab_xy(frame)
            log.info("2/4 battles %s client=(%s,%s)", tab_label, tx, ty)
            if not click_client_xy(hwnd, tx, ty):
                return None, refreshed, "wt_not_focused"
            time.sleep(0.45)
            if not focus_war_thunder(allow_unminimize=False):
                if not focus_war_thunder(allow_unminimize=True):
                    log.info("WT lost focus after Battles click")
                    return None, refreshed, "wt_not_focused"
            fresh = _nav_frame(require_foreground=False, allow_mss=False)
            if fresh is not None:
                frame = fresh
            if _panel_looks_empty(frame):
                log.info("Messages → Battles empty")
                _close_messages_via_esc(hwnd)
                opened = False
                return None, refreshed, "no_messages"

            snap = snapshot_clipboard()
            baseline = snap.text if snap.had_text else ""
            log.info("3/4 Ctrl+C selected battle")
            report, close_frame = _read_open_messages_battle(
                frame,
                hwnd=hwnd,
                timeout_sec=timeout_sec,
                reject_clipboard=baseline or None,
            )

            # Walk older list rows for provisional sessions still pending.
            if want and isinstance(report, BattleReport) and report.session_id:
                want.discard(report.session_id.lower())
            if want:
                steps = max(0, int(max_arrow_steps))
                log.info(
                    "3b/4 arrow-walk up to %s for pending sessions=%s",
                    steps,
                    sorted(want),
                )
                for step in range(steps):
                    if not want:
                        break
                    if not focus_war_thunder(allow_unminimize=False):
                        break
                    press_arrow_down(settle_sec=0.18)
                    walked, close_frame = _read_open_messages_battle(
                        close_frame,
                        hwnd=hwnd,
                        timeout_sec=0.8,
                        reject_clipboard=baseline or None,
                    )
                    if not isinstance(walked, BattleReport):
                        continue
                    sid = (walked.session_id or "").lower()
                    if sid and sid in want:
                        refreshed[sid] = walked
                        want.discard(sid)
                        log.info(
                            "arrow step %s hit session=%s provisional=%s RP=%s SL=%s",
                            step + 1,
                            sid[:12],
                            walked.provisional,
                            walked.research_points,
                            walked.silver_lions,
                        )

            # Always close with Esc x1 after the copy attempt.
            _close_messages_via_esc(hwnd)
            opened = False

            if isinstance(report, BattleReport):
                got_report = True
                log.info(
                    "clipboard results ok RP=%s SL=%s provisional=%s source=%s",
                    report.research_points,
                    report.silver_lions,
                    report.provisional,
                    report.source,
                )
                return report, refreshed, "ok"
            return None, refreshed, "parse_timeout"
    finally:
        if opened:
            try:
                hwnd2 = find_war_thunder_hwnd()
                if hwnd2 is not None:
                    _close_messages_via_esc(hwnd2)
                else:
                    press_escape()
            except Exception:  # noqa: BLE001
                pass
        if prev_cursor is not None:
            try:
                set_cursor_pos(prev_cursor[0], prev_cursor[1])
            except Exception:  # noqa: BLE001
                pass
        # Only restore the user's prior clipboard after a successful parse.
        # On failure leave whatever WT wrote so the dump is inspectable.
        if got_report:
            restore_clipboard(snap)
