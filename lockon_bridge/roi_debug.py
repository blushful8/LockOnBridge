"""Developer ROI overlay + annotated capture dumps for OCR calibration."""

from __future__ import annotations

import ctypes
import logging
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .capture import find_war_thunder_hwnd
from .roi_layout import content_frame, is_full_client_frame, iter_roi_pixel_boxes

log = logging.getLogger("lockon_bridge.roi_debug")

# Transparent key for Tk layered window (not used in game UI).
_KEY = "#010203"
_LEAN_COLOR = "#00ff66"
_DENSE_COLOR = "#ffaa00"
_FRAME_COLOR = "#33ccff"
_HUD_COLOR = "#ffffff"

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080

user32 = ctypes.windll.user32


def annotate_roi_image(image: Image.Image, *, dense: bool = True) -> Image.Image:
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
    draw.rectangle((fl, ft, fr - 1, fb - 1), outline=(51, 204, 255), width=2)

    boxes = iter_roi_pixel_boxes(image, dense=dense)
    for tag, (left, top, right, bottom), is_lean in boxes:
        color = (0, 255, 102) if is_lean else (255, 170, 0)
        width = 3 if is_lean else 2
        draw.rectangle((left, top, right - 1, bottom - 1), outline=color, width=width)
        label = f"{'*' if is_lean else ''}{tag} [{left},{top}-{right},{bottom}]"
        ty = max(0, top - 18)
        draw.text((left + 2, ty), label, fill=color, font=font_small)

    w, h = image.size
    kind = "full-client" if is_full_client_frame(image) else "chat-crop"
    lean_n = sum(1 for _tag, _box, is_lean in boxes if is_lean)
    hud = (
        f"ROI debug  {w}x{h}  {kind}  "
        f"lean={lean_n}/{len(boxes)}  frame=({fl},{ft})-({fr},{fb})"
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


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    from .capture import _window_rect as _cap_rect

    return _cap_rect(hwnd)


def _set_click_through(hwnd: int) -> None:
    style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    user32.SetWindowLongW(
        hwnd,
        _GWL_EXSTYLE,
        int(style) | _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW,
    )


class RoiDebugOverlay:
    """
    Click-through topmost overlay aligned to the War Thunder client.

    Draws dense ROI catalogue; lean (active) boxes are thicker/green.
    """

    def __init__(self, master: tk.Misc, *, refresh_ms: int = 250) -> None:
        self.master = master
        self.refresh_ms = max(100, int(refresh_ms))
        self._enabled = False
        self._win: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._job: str | None = None
        self._last_geom: tuple[int, int, int, int] | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        if self._enabled:
            self._ensure_window()
            self._schedule()
        else:
            self._cancel()
            self._destroy_window()

    def on_ui_rebuilt(self) -> None:
        """``_build_ui`` destroys root children — recreate if still enabled."""
        self._win = None
        self._canvas = None
        self._last_geom = None
        if self._enabled:
            self._ensure_window()
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
        if not self._enabled:
            return
        self._job = self.master.after(self.refresh_ms, self._tick)

    def _destroy_window(self) -> None:
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:  # noqa: BLE001
                pass
        self._win = None
        self._canvas = None
        self._last_geom = None

    def _ensure_window(self) -> None:
        if self._win is not None:
            try:
                if self._win.winfo_exists():
                    return
            except Exception:  # noqa: BLE001
                pass
            self._win = None
            self._canvas = None

        win = tk.Toplevel(self.master)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-transparentcolor", _KEY)
        except tk.TclError:
            pass
        win.configure(bg=_KEY)
        # Keep out of taskbar / Alt-Tab as much as possible.
        try:
            win.attributes("-toolwindow", True)
        except tk.TclError:
            pass
        canvas = tk.Canvas(win, bg=_KEY, highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        self._win = win
        self._canvas = canvas
        win.update_idletasks()
        try:
            hwnd = int(win.winfo_id())
            # On Windows, winfo_id is the HWND for Tk.
            _set_click_through(hwnd)
        except Exception as exc:  # noqa: BLE001
            log.debug("click-through setup skipped: %s", exc)

    def _tick(self) -> None:
        self._job = None
        if not self._enabled:
            return
        try:
            self._redraw()
        except Exception as exc:  # noqa: BLE001
            log.debug("ROI overlay redraw failed: %s", exc)
        self._schedule()

    def _redraw(self) -> None:
        self._ensure_window()
        win = self._win
        canvas = self._canvas
        if win is None or canvas is None:
            return

        hwnd = find_war_thunder_hwnd()
        bounds = _window_rect(hwnd) if hwnd else None
        if bounds is None:
            canvas.delete("all")
            # Park off-screen small; still show a hint on the Bridge monitor.
            win.geometry("420x36+40+40")
            canvas.create_text(
                8,
                8,
                anchor="nw",
                fill=_HUD_COLOR,
                font=("Segoe UI", 11),
                text="ROI debug: War Thunder window not found",
            )
            self._last_geom = None
            return

        left, top, right, bottom = bounds
        width = max(1, right - left)
        height = max(1, bottom - top)
        geom = (left, top, width, height)
        if geom != self._last_geom:
            win.geometry(f"{width}x{height}+{left}+{top}")
            self._last_geom = geom
            win.update_idletasks()
            try:
                _set_click_through(int(win.winfo_id()))
            except Exception:  # noqa: BLE001
                pass

        # Synthetic image so NormRect math matches capture path.
        probe = Image.new("RGB", (width, height), (0, 0, 0))
        canvas.delete("all")

        fl, ft, fr, fb = content_frame(probe)
        canvas.create_rectangle(
            fl,
            ft,
            fr - 1,
            fb - 1,
            outline=_FRAME_COLOR,
            width=2,
        )

        boxes = iter_roi_pixel_boxes(probe, dense=True)
        for tag, (l, t, r, b), is_lean in boxes:
            color = _LEAN_COLOR if is_lean else _DENSE_COLOR
            canvas.create_rectangle(
                l,
                t,
                r - 1,
                b - 1,
                outline=color,
                width=3 if is_lean else 2,
            )
            canvas.create_text(
                l + 4,
                max(2, t - 14),
                anchor="nw",
                fill=color,
                font=("Segoe UI", 9),
                text=f"{'*' if is_lean else ''}{tag}",
            )

        kind = "full-client" if is_full_client_frame(probe) else "chat-crop"
        lean_n = sum(1 for _tag, _box, is_lean in boxes if is_lean)
        canvas.create_rectangle(0, 0, width, 26, fill="#000000", outline="")
        canvas.create_text(
            8,
            5,
            anchor="nw",
            fill=_HUD_COLOR,
            font=("Segoe UI", 10),
            text=(
                f"ROI debug  {width}x{height}  {kind}  "
                f"lean={lean_n}/{len(boxes)}  * = active OCR path"
            ),
        )
