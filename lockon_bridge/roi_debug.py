"""Developer ROI overlay — continuous on-game boxes + cheap Bridge preview."""

from __future__ import annotations

import ctypes
import logging
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageTk

from .capture import find_war_thunder_hwnd, grab_wt_client_image
from .roi_layout import content_frame, is_full_client_frame, iter_roi_pixel_boxes, pixel_box

log = logging.getLogger("lockon_bridge.roi_debug")

_LEAN = (0, 255, 102)
_DENSE = (255, 170, 0)
_FRAME = (51, 204, 255)
_KEY = "#ff00ff"  # chroma key (must not match box colours)
_LEAN_HEX = "#00ff66"
_DENSE_HEX = "#ffaa00"
_FRAME_HEX = "#33ccff"
_HUD_HEX = "#ffffff"
_WITH_RP = "#00ff66"
_WITH_SL = "#33ffcc"
_WITHOUT_RP = "#ffcc00"
_WITHOUT_SL = "#66aaff"

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_HWND_TOPMOST = -1
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040

user32 = ctypes.windll.user32


def _debug_roi_boxes(
    image: Image.Image,
) -> list[tuple[str, tuple[int, int, int, int], bool]]:
    """
    Boxes for developer overlay / annotate.

    Prefer the 4 calibrated cells (with/without × RP/SL). Fall back to lean
    catalogue only when calibration is missing.
    """
    from .roi_calib import load_calibrated_rois

    calib = load_calibrated_rois()
    if calib is not None:
        out: list[tuple[str, tuple[int, int, int, int], bool]] = []
        for prefix, col in (
            ("with", calib.with_premium),
            ("without", calib.without_premium),
        ):
            for i, pair in enumerate(col.pairs):
                for kind, rect in (("rp", pair.rp), ("sl", pair.sl)):
                    boxed = pixel_box(image, rect)
                    if boxed is None:
                        continue
                    tag = f"{prefix}-p{i}-{kind}" if len(col.pairs) > 1 else f"{prefix}-{kind}"
                    out.append((tag, boxed, i == 0))
        if out:
            return out
    return iter_roi_pixel_boxes(image, dense=False)


def annotate_roi_image(image: Image.Image, *, dense: bool = False) -> Image.Image:
    """Draw digit ROI boxes + tags on a copy of ``image``."""
    out = image.copy().convert("RGB")
    draw = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("segoeui.ttf", 16)
        font_small = ImageFont.truetype("segoeui.ttf", 13)
    except OSError:
        font = ImageFont.load_default()
        font_small = font

    fl, ft, fr, fb = content_frame(image)
    draw.rectangle((fl, ft, fr - 1, fb - 1), outline=_FRAME, width=2)

    if dense:
        boxes = iter_roi_pixel_boxes(image, dense=True)
    else:
        boxes = _debug_roi_boxes(image)
    color_by_tag = {
        "with-rp": _LEAN,
        "with-sl": (51, 255, 204),
        "without-rp": (255, 204, 0),
        "without-sl": (102, 170, 255),
    }
    for tag, (left, top, right, bottom), is_lean in boxes:
        color = color_by_tag.get(tag, _LEAN if is_lean else _DENSE)
        width = 3 if is_lean else 2
        draw.rectangle((left, top, right - 1, bottom - 1), outline=color, width=width)
        label = f"{tag} [{left},{top}-{right},{bottom}]"
        ty = max(0, top - 18)
        draw.text((left + 2, ty), label, fill=color, font=font_small)

    w, h = image.size
    kind = "full-client" if is_full_client_frame(image) else "chat-crop"
    hud = (
        f"ROI debug  {w}x{h}  {kind}  "
        f"boxes={len(boxes)}  frame=({fl},{ft})-({fr},{fb})"
    )
    draw.rectangle((0, 0, w, 28), fill=(0, 0, 0))
    draw.text((8, 6), hud, fill=(255, 255, 255), font=font)
    return out


def save_annotated_rois(
    image: Image.Image,
    path: Path,
    *,
    dense: bool = True,
) -> Path | None:
    """Write annotated PNG; return path or None on failure."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        annotate_roi_image(image, dense=dense).save(path, format="PNG")
        return path
    except OSError as exc:
        log.warning("annotated ROI dump failed: %s", exc)
        return None


def save_ocr_crop_dumps(
    frame: Image.Image,
    dump_dir: Path,
    *,
    debug_full: bool = False,
) -> Path | None:
    """
    Write what OCR actually reads: small lean ROI crops (+ optional full debug).

    - ``last_capture.png`` — collage of lean digit crops (not the whole client)
    - ``roi_crops/*.png`` — each lean crop separately (all calibrated pairs)
    - ``last_capture_rois.png`` / ``last_capture_full.png`` — only when ``debug_full``
    """
    from .roi_calib import calibrated_all_pair_rects, has_usable_calibration
    from .roi_layout import crop_norm, iter_reward_digit_rois
    from .settings import load_settings

    try:
        dump_dir.mkdir(parents=True, exist_ok=True)
        crops_dir = dump_dir / "roi_crops"
        if crops_dir.is_dir():
            for old in crops_dir.glob("*.png"):
                try:
                    old.unlink()
                except OSError:
                    pass
        else:
            crops_dir.mkdir(parents=True, exist_ok=True)

        saved: list[tuple[str, Image.Image]] = []

        # Dump EVERY calibrated fallback pair (not only pair 0) so failures are visible.
        if has_usable_calibration():
            try:
                prefer_with = bool(load_settings().has_premium_account)
            except Exception:  # noqa: BLE001
                prefer_with = False
            prefix = "with" if prefer_with else "without"
            stack = calibrated_all_pair_rects(prefer_with=prefer_with) or []
            for index, rp_rect, sl_rect in stack:
                for suffix, rect in (("rp", rp_rect), ("sl", sl_rect)):
                    crop = crop_norm(frame, rect)
                    if crop is None:
                        continue
                    tag = f"{prefix}-p{index}-{suffix}"
                    crop_path = crops_dir / f"{tag}.png"
                    crop.save(crop_path, format="PNG")
                    saved.append((tag, crop))

        if not saved:
            lean = iter_reward_digit_rois(frame, dense=False)
            for tag, crop in lean:
                safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in tag)
                crop_path = crops_dir / f"{safe}.png"
                crop.save(crop_path, format="PNG")
                saved.append((tag, crop))

        if not saved:
            # Fallback: keep a downscaled full frame so logs are never empty.
            preview = frame.copy()
            preview.thumbnail((1280, 800), Image.Resampling.BILINEAR)
            preview.save(dump_dir / "last_capture.png", format="PNG")
            return dump_dir / "last_capture.png"

        collage = _collage_crops(saved)
        collage_path = dump_dir / "last_capture.png"
        collage.save(collage_path, format="PNG")

        if debug_full:
            save_annotated_rois(frame, dump_dir / "last_capture_rois.png", dense=False)
            # Full client only for calibration — not the OCR input.
            full = frame.copy()
            full.thumbnail((1600, 1000), Image.Resampling.BILINEAR)
            full.save(dump_dir / "last_capture_full.png", format="PNG")
        else:
            for name in ("last_capture_rois.png", "last_capture_full.png"):
                stale = dump_dir / name
                if stale.is_file():
                    try:
                        stale.unlink()
                    except OSError:
                        pass

        return collage_path
    except OSError as exc:
        log.warning("OCR crop dump failed: %s", exc)
        return None


def _collage_crops(items: list[tuple[str, Image.Image]], *, pad: int = 8) -> Image.Image:
    """Horizontal strip of labeled lean crops for ``last_capture.png``."""
    if not items:
        return Image.new("RGB", (64, 64), (20, 20, 24))
    try:
        font = ImageFont.truetype("segoeui.ttf", 14)
    except OSError:
        font = ImageFont.load_default()

    labeled: list[Image.Image] = []
    for tag, crop in items:
        rgb = crop.convert("RGB")
        # Upscale tiny cells so the collage is readable in Explorer.
        if rgb.height < 64:
            scale = 64 / float(rgb.height)
            rgb = rgb.resize(
                (max(1, int(rgb.width * scale)), max(1, int(rgb.height * scale))),
                Image.Resampling.NEAREST,
            )
        banner_h = 22
        tile = Image.new("RGB", (rgb.width, rgb.height + banner_h), (12, 14, 18))
        tile.paste(rgb, (0, banner_h))
        draw = ImageDraw.Draw(tile)
        draw.text((4, 3), tag, fill=(0, 255, 120), font=font)
        labeled.append(tile)

    width = sum(im.width for im in labeled) + pad * (len(labeled) + 1)
    height = max(im.height for im in labeled) + pad * 2
    out = Image.new("RGB", (width, height), (8, 10, 14))
    x = pad
    for im in labeled:
        out.paste(im, (x, pad))
        x += im.width + pad
    return out


def _schematic_roi_image(width: int, height: int) -> Image.Image:
    """Cheap layout diagram — no screen grab, no OCR."""
    width = max(320, width)
    height = max(180, height)
    img = Image.new("RGB", (width, height), (14, 16, 22))
    return annotate_roi_image(img, dense=False)


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    from .capture import _window_rect as _cap_rect

    return _cap_rect(hwnd)


def _toplevel_hwnd(win: tk.Misc) -> int:
    """Resolve the real Win32 HWND for a Tk Toplevel (walk parents)."""
    hwnd = int(win.winfo_id())
    while True:
        parent = int(user32.GetParent(hwnd) or 0)
        if not parent:
            return hwnd
        hwnd = parent


def _set_click_through(hwnd: int) -> None:
    style = int(user32.GetWindowLongW(hwnd, _GWL_EXSTYLE))
    user32.SetWindowLongW(
        hwnd,
        _GWL_EXSTYLE,
        style
        | _WS_EX_LAYERED
        | _WS_EX_TRANSPARENT
        | _WS_EX_TOOLWINDOW
        | _WS_EX_NOACTIVATE,
    )
    user32.SetWindowPos(
        hwnd,
        _HWND_TOPMOST,
        0,
        0,
        0,
        0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_SHOWWINDOW,
    )


class RoiDebugOverlay:
    """
    Continuous ROI visualisation while the setting is on:

    1. Click-through neon boxes on the War Thunder client — **geometry only**
       (HWND + canvas). No screenshots, no OCR.
    2. Bridge panel with a **schematic** layout (also no grab). Optional one-shot
       «Capture» for an annotated screenshot when you need it.

    Overlay is hidden briefly during Test OCR so boxes are not captured.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        refresh_ms: int = 750,
        on_disabled: Callable[[], None] | None = None,
    ) -> None:
        self.master = master
        self.refresh_ms = max(250, int(refresh_ms))
        self._on_disabled = on_disabled
        self._enabled = False
        self._paused = False

        self._overlay: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._last_geom: tuple[int, int, int, int] | None = None

        self._preview: tk.Toplevel | None = None
        self._preview_label: tk.Label | None = None
        self._preview_status: tk.Label | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._capture_busy = False

        self._job: str | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        if self._enabled:
            self._ensure_overlay()
            self._ensure_preview()
            self._schedule()
            self.master.after(30, self._tick)
        else:
            self._cancel()
            self._destroy_all()

    def pause(self) -> None:
        """Hide overlays so Test OCR does not read neon boxes."""
        self._paused = True
        self._cancel()
        self._hide_overlay()

    def resume(self) -> None:
        self._paused = False
        if self._enabled:
            self._ensure_overlay()
            self._ensure_preview()
            self._schedule()
            self.master.after(30, self._tick)

    def on_ui_rebuilt(self) -> None:
        self._overlay = None
        self._canvas = None
        self._preview = None
        self._preview_label = None
        self._preview_status = None
        self._photo = None
        self._last_geom = None
        if self._enabled and not self._paused:
            self._ensure_overlay()
            self._ensure_preview()
            self._schedule()

    def shutdown(self) -> None:
        self.set_enabled(False)

    def _cancel(self) -> None:
        if self._job is not None:
            try:
                self.master.after_cancel(self._job)
            except Exception:  # noqa: BLE001
                pass
            self._job = None

    def _schedule(self) -> None:
        self._cancel()
        if not self._enabled or self._paused:
            return
        self._job = self.master.after(self.refresh_ms, self._tick)

    def _destroy_all(self) -> None:
        for attr in ("_overlay", "_preview"):
            win = getattr(self, attr)
            if win is not None:
                try:
                    win.destroy()
                except Exception:  # noqa: BLE001
                    pass
                setattr(self, attr, None)
        self._canvas = None
        self._preview_label = None
        self._preview_status = None
        self._photo = None
        self._last_geom = None

    def _hide_overlay(self) -> None:
        if self._overlay is not None:
            try:
                self._overlay.withdraw()
            except Exception:  # noqa: BLE001
                pass

    def _ensure_overlay(self) -> None:
        if self._overlay is not None:
            try:
                if bool(self._overlay.winfo_exists()):
                    try:
                        self._overlay.deiconify()
                    except Exception:  # noqa: BLE001
                        pass
                    return
            except Exception:  # noqa: BLE001
                pass
            self._overlay = None
            self._canvas = None

        win = tk.Toplevel(self.master)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-transparentcolor", _KEY)
        except tk.TclError:
            pass
        win.configure(bg=_KEY)
        try:
            win.attributes("-toolwindow", True)
        except tk.TclError:
            pass
        canvas = tk.Canvas(win, bg=_KEY, highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        self._overlay = win
        self._canvas = canvas
        win.update_idletasks()
        try:
            _set_click_through(_toplevel_hwnd(win))
        except Exception as exc:  # noqa: BLE001
            log.debug("click-through setup skipped: %s", exc)

    def _ensure_preview(self) -> None:
        if self._preview is not None:
            try:
                if bool(self._preview.winfo_exists()):
                    return
            except Exception:  # noqa: BLE001
                pass
            self._preview = None

        win = tk.Toplevel(self.master)
        win.title("OCR ROI — layout (no OCR)")
        win.configure(bg="#12141a")
        win.attributes("-topmost", True)
        win.geometry("900x560+60+60")
        win.resizable(False, False)
        win.minsize(900, 560)
        win.maxsize(900, 560)
        win.protocol("WM_DELETE_WINDOW", self._on_close_requested)

        status = tk.Label(
            win,
            text=(
                "Continuous layout only — no screenshots, no OCR. "
                "Use Capture once when you want a real annotated frame."
            ),
            font=("Segoe UI", 10),
            fg="#c8ccd4",
            bg="#12141a",
            anchor="w",
            justify="left",
            wraplength=860,
        )
        status.pack(fill="x", padx=10, pady=(8, 4))

        btn_row = tk.Frame(win, bg="#12141a")
        btn_row.pack(fill="x", padx=10, pady=(0, 6))
        tk.Button(
            btn_row,
            text="Capture annotated screenshot once",
            command=self._capture_once_clicked,
            font=("Segoe UI", 10),
            fg="#e8eaed",
            bg="#2a2f38",
            activebackground="#3a414d",
            activeforeground="#ffffff",
            relief="flat",
            padx=10,
            pady=6,
            cursor="hand2",
        ).pack(side="left")

        label = tk.Label(win, bg="#0a0c10")
        label.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        hint = tk.Label(
            win,
            text=(
                "On-game boxes = 4 calibrated cells (with/without × RP/SL). "
                "Edit via More → ROI calibrator. Prefer borderless/windowed WT."
            ),
            font=("Segoe UI", 9),
            fg="#8b909a",
            bg="#12141a",
            anchor="w",
            wraplength=860,
            justify="left",
        )
        hint.pack(fill="x", padx=10, pady=(0, 8))

        self._preview = win
        self._preview_label = label
        self._preview_status = status
        # First schematic immediately.
        self.master.after(50, self._redraw_schematic_preview)

    def _on_close_requested(self) -> None:
        self.set_enabled(False)
        try:
            from .settings import update_settings

            update_settings(debug_show_rois=False)
        except Exception:  # noqa: BLE001
            pass
        if self._on_disabled is not None:
            try:
                self._on_disabled()
            except Exception:  # noqa: BLE001
                pass

    def _tick(self) -> None:
        self._job = None
        if not self._enabled or self._paused:
            return
        try:
            prev = self._last_geom
            self._redraw_overlay()
            # Schematic only when WT size/position band changes (still no grab).
            if self._last_geom is not None and self._last_geom != prev:
                self._redraw_schematic_preview()
        except Exception as exc:  # noqa: BLE001
            log.warning("ROI debug tick failed: %s", exc)
        self._schedule()

    def _redraw_overlay(self) -> None:
        self._ensure_overlay()
        win = self._overlay
        canvas = self._canvas
        if win is None or canvas is None:
            return

        hwnd = find_war_thunder_hwnd()
        bounds = _window_rect(hwnd) if hwnd else None
        if bounds is None:
            canvas.delete("all")
            win.geometry("520x40+48+48")
            canvas.create_rectangle(0, 0, 520, 40, fill="#000000", outline="")
            canvas.create_text(
                10,
                10,
                anchor="nw",
                fill=_HUD_HEX,
                font=("Segoe UI", 11),
                text="ROI debug ON — open War Thunder (windowed/borderless)",
            )
            self._last_geom = None
            return

        left, top, right, bottom = bounds
        width = max(1, right - left)
        height = max(1, bottom - top)
        geom = (left, top, width, height)
        geom_changed = geom != self._last_geom
        if geom_changed:
            win.geometry(f"{width}x{height}+{left}+{top}")
            self._last_geom = geom
            win.update_idletasks()
            try:
                _set_click_through(_toplevel_hwnd(win))
            except Exception:  # noqa: BLE001
                pass

        # Always redraw — calibration can change without a geometry change.
        probe = Image.new("RGB", (width, height), (0, 0, 0))
        canvas.delete("all")

        fl, ft, fr, fb = content_frame(probe)
        canvas.create_rectangle(fl, ft, fr - 1, fb - 1, outline=_FRAME_HEX, width=2)

        boxes = _debug_roi_boxes(probe)
        color_by_tag = {
            "with-rp": _WITH_RP,
            "with-sl": _WITH_SL,
            "without-rp": _WITHOUT_RP,
            "without-sl": _WITHOUT_SL,
        }
        for tag, (l, t, r, b), is_lean in boxes:
            color = color_by_tag.get(tag, _LEAN_HEX if is_lean else _DENSE_HEX)
            canvas.create_rectangle(l, t, r - 1, b - 1, outline=color, width=4)
            canvas.create_rectangle(l + 1, t + 1, r - 2, b - 2, outline="#000000", width=1)
            canvas.create_text(
                l + 6,
                max(30, t - 16),
                anchor="nw",
                fill=color,
                font=("Segoe UI Semibold", 11),
                text=tag,
            )

        kind = "full-client" if is_full_client_frame(probe) else "chat-crop"
        canvas.create_rectangle(0, 0, width, 28, fill="#000000", outline="")
        canvas.create_text(
            8,
            6,
            anchor="nw",
            fill=_HUD_HEX,
            font=("Segoe UI", 11),
            text=(
                f"ROI calibrated  {width}x{height}  {kind}  "
                f"boxes={len(boxes)}  (4 cells: with/without × RP/SL)"
            ),
        )

    def _redraw_schematic_preview(self) -> None:
        if self._preview_label is None or self._preview_status is None:
            return
        if self._last_geom is not None:
            _l, _t, width, height = self._last_geom
        else:
            width, height = 1920, 1080
        # Downscale schematic for the panel.
        max_w, max_h = 880, 480
        scale = min(max_w / float(width), max_h / float(height), 1.0)
        sw = max(320, int(width * scale))
        sh = max(180, int(height * scale))
        schematic = _schematic_roi_image(sw, sh)
        self._preview_status.configure(
            text=(
                f"Schematic {width}x{height} → panel {sw}x{sh}  "
                f"(no screenshot / no OCR — Capture for a real frame)"
            )
        )
        self._show_preview_image(schematic)

    def _capture_once_clicked(self) -> None:
        if self._capture_busy:
            return
        self._capture_busy = True
        if self._preview_status is not None:
            self._preview_status.configure(text="Capturing one annotated frame…")

        def work() -> None:
            err: str | None = None
            image: Image.Image | None = None
            try:
                frame = grab_wt_client_image(focus=False)
                if frame is None:
                    err = "War Thunder window not found"
                else:
                    image = annotate_roi_image(frame, dense=False)
                    from .paths import log_dir

                    save_annotated_rois(frame, log_dir() / "last_capture_rois.png")
            except Exception as exc:  # noqa: BLE001
                err = str(exc)

            def done() -> None:
                self._capture_busy = False
                if err or image is None:
                    if self._preview_status is not None:
                        self._preview_status.configure(text=f"Capture failed: {err}")
                    return
                # Fit into panel.
                try:
                    max_w = max(320, int(self._preview_label.winfo_width()) - 4)  # type: ignore[union-attr]
                    max_h = max(240, int(self._preview_label.winfo_height()) - 4)  # type: ignore[union-attr]
                except Exception:  # noqa: BLE001
                    max_w, max_h = 880, 480
                scale = min(max_w / float(image.width), max_h / float(image.height), 1.0)
                shown = image
                if scale < 0.99:
                    shown = image.resize(
                        (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                        Image.Resampling.BILINEAR,
                    )
                self._show_preview_image(shown)
                if self._preview_status is not None:
                    self._preview_status.configure(
                        text=(
                            f"Captured {image.width}x{image.height} — "
                            f"also saved to logs/last_capture_rois.png"
                        )
                    )

            try:
                self.master.after(0, done)
            except Exception:  # noqa: BLE001
                self._capture_busy = False

        threading.Thread(target=work, name="roi-capture-once", daemon=True).start()

    def _show_preview_image(self, image: Image.Image) -> None:
        if self._preview_label is None:
            return
        photo = ImageTk.PhotoImage(image)
        self._photo = photo
        self._preview_label.configure(image=photo)
