"""Developer ROI calibrator — two draggable RP/SL boxes (session unlock only).

Sources:
  • War Thunder client (live)
  • Any other top-level window (click under cursor)
  • Image file / чужий скріншот (open dialog)
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import sys
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog

from PIL import Image, ImageTk

from .capture import find_war_thunder_hwnd
from .roi_calib import (
    CalibratedRois,
    ColumnRois,
    default_calibrated_rois,
    load_calibrated_rois,
    save_calibrated_rois,
)
from .roi_layout import NormRect, content_frame, pixel_box

log = logging.getLogger("lockon_bridge.roi_calib_ui")

# SHA-256 of the developer unlock passphrase (plaintext is not stored in the repo).
_DEV_PASS_SHA256 = "37fc226db1b1805c57cc73b3e24bc121aee16b3065bfe73b6b34fd70acb4fb1c"

_KEY = "#ff00ff"
_RP_COLOR = "#00ff66"
_SL_COLOR = "#33ccff"
_HANDLE = 10

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000

user32 = ctypes.windll.user32

# Source modes
_SRC_WT = "wt"
_SRC_HWND = "hwnd"
_SRC_IMAGE = "image"


def verify_dev_passphrase(raw: str) -> bool:
    digest = hashlib.sha256((raw or "").strip().encode("utf-8")).hexdigest()
    return digest == _DEV_PASS_SHA256


def _window_rect(hwnd: int):
    from .capture import _window_rect as _cap_rect

    return _cap_rect(hwnd)


def _window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return (buf.value or "").strip()


def _toplevel_hwnd(win: tk.Misc) -> int:
    hwnd = int(win.winfo_id())
    while True:
        parent = int(user32.GetParent(hwnd) or 0)
        if not parent:
            return hwnd
        hwnd = parent


def _style_tool_topmost(hwnd: int) -> None:
    style = int(user32.GetWindowLongW(hwnd, _GWL_EXSTYLE))
    user32.SetWindowLongW(
        hwnd,
        _GWL_EXSTYLE,
        style | _WS_EX_LAYERED | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE,
    )


def _hwnd_under_cursor() -> int | None:
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return None
    hwnd = int(user32.WindowFromPoint(pt) or 0)
    if not hwnd:
        return None
    # Climb to the top-level owner/root.
    root = int(user32.GetAncestor(hwnd, 2) or 0)  # GA_ROOT = 2
    return root or hwnd


@dataclass
class _DragState:
    kind: str
    start_x: float
    start_y: float
    orig: NormRect


class RoiCalibrator:
    """Interactive 2-box calibrator — WT, any window, or a screenshot image."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        on_closed: Callable[[], None] | None = None,
    ) -> None:
        self.master = master
        self._on_closed = on_closed
        self._calib = load_calibrated_rois() or default_calibrated_rois()
        self._edit_with = False
        self._source = _SRC_WT
        self._target_hwnd: int | None = None
        self._image: Image.Image | None = None
        self._image_path: Path | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._img_scale = 1.0
        self._img_ox = 0
        self._img_oy = 0
        self._overlay: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._panel: tk.Toplevel | None = None
        self._status: tk.Label | None = None
        self._src_var: tk.StringVar | None = None
        self._job: str | None = None
        self._pick_job: str | None = None
        # (left, top, width, height) of overlay/canvas in screen or local coords.
        # For image mode left/top are 0 and width/height are canvas size.
        self._last_geom: tuple[int, int, int, int] | None = None
        # Logical content size used for NormRect (image pixels or window client).
        self._logic_size: tuple[int, int] | None = None
        self._drag: _DragState | None = None
        self._running = False
        self._col_var: tk.StringVar | None = None

    @property
    def running(self) -> bool:
        return self._running

    def open(self) -> None:
        if self._running:
            self._lift()
            return
        self._running = True
        self._ensure_panel()
        self._ensure_overlay()
        self._schedule()
        self.master.after(40, self._tick)

    def close(self) -> None:
        self._running = False
        if self._job is not None:
            try:
                self.master.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        if self._pick_job is not None:
            try:
                self.master.after_cancel(self._pick_job)
            except Exception:
                pass
            self._pick_job = None
        for attr in ("_overlay", "_panel"):
            win = getattr(self, attr)
            if win is not None:
                try:
                    win.destroy()
                except Exception:
                    pass
                setattr(self, attr, None)
        self._canvas = None
        self._status = None
        self._last_geom = None
        self._logic_size = None
        self._drag = None
        self._photo = None
        if self._on_closed is not None:
            try:
                self._on_closed()
            except Exception:
                pass

    def _lift(self) -> None:
        for win in (self._panel, self._overlay):
            if win is None:
                continue
            try:
                win.lift()
                win.attributes("-topmost", True)
            except Exception:
                pass

    def _schedule(self) -> None:
        if self._job is not None:
            try:
                self.master.after_cancel(self._job)
            except Exception:
                pass
        if self._running:
            # Image mode does not need frequent geom tracking.
            delay = 800 if self._source == _SRC_IMAGE else 350
            self._job = self.master.after(delay, self._tick)

    def _tick(self) -> None:
        self._job = None
        if not self._running:
            return
        try:
            if self._drag is None:
                self._redraw_overlay(force_boxes=False)
        except Exception as exc:
            log.warning("calibrator tick failed: %s", exc)
        self._schedule()

    def _active_column(self) -> ColumnRois:
        return self._calib.with_premium if self._edit_with else self._calib.without_premium

    def _set_active_column(self, col: ColumnRois) -> None:
        if self._edit_with:
            self._calib = CalibratedRois(
                version=self._calib.version,
                with_premium=col,
                without_premium=self._calib.without_premium,
            )
        else:
            self._calib = CalibratedRois(
                version=self._calib.version,
                with_premium=self._calib.with_premium,
                without_premium=col,
            )

    def _ensure_panel(self) -> None:
        if self._panel is not None:
            try:
                if bool(self._panel.winfo_exists()):
                    return
            except Exception:
                pass
        win = tk.Toplevel(self.master)
        win.title("ROI calibrator (dev)")
        win.configure(bg="#12141a")
        win.geometry("460x360+80+80")
        win.protocol("WM_DELETE_WINDOW", self.close)

        self._status = tk.Label(
            win,
            text="Обери джерело, потім перетягни RP (зелений) і SL (блакитний).",
            font=("Segoe UI", 10),
            fg="#c8ccd4",
            bg="#12141a",
            wraplength=430,
            justify="left",
            anchor="w",
        )
        self._status.pack(fill="x", padx=12, pady=(10, 6))

        src = tk.LabelFrame(
            win,
            text="Джерело",
            font=("Segoe UI", 9),
            fg="#8b909a",
            bg="#12141a",
            labelanchor="nw",
        )
        src.pack(fill="x", padx=12, pady=4)
        self._src_var = tk.StringVar(value=_SRC_WT)
        for value, label in (
            (_SRC_WT, "War Thunder"),
            (_SRC_HWND, "Інше вікно"),
            (_SRC_IMAGE, "Скріншот / зображення"),
        ):
            tk.Radiobutton(
                src,
                text=label,
                variable=self._src_var,
                value=value,
                command=self._on_source_toggle,
                bg="#12141a",
                fg="#e8eaed",
                selectcolor="#2a2f38",
                activebackground="#12141a",
                activeforeground="#ffffff",
                font=("Segoe UI", 10),
            ).pack(anchor="w", padx=8, pady=1)

        src_btns = tk.Frame(src, bg="#12141a")
        src_btns.pack(fill="x", padx=8, pady=(4, 8))
        tk.Button(
            src_btns,
            text="Вибрати вікно…",
            command=self._start_pick_window,
            font=("Segoe UI", 9),
            fg="#e8eaed",
            bg="#2a2f38",
            activebackground="#3a414d",
            relief="flat",
            padx=8,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            src_btns,
            text="Відкрити зображення…",
            command=self._open_image,
            font=("Segoe UI", 9),
            fg="#e8eaed",
            bg="#2a2f38",
            activebackground="#3a414d",
            relief="flat",
            padx=8,
            pady=4,
            cursor="hand2",
        ).pack(side="left")

        col = tk.Frame(win, bg="#12141a")
        col.pack(fill="x", padx=12, pady=4)
        self._col_var = tk.StringVar(value="without")
        tk.Radiobutton(
            col,
            text="Без преміуму",
            variable=self._col_var,
            value="without",
            command=self._on_column_toggle,
            bg="#12141a",
            fg="#e8eaed",
            selectcolor="#2a2f38",
            activebackground="#12141a",
            activeforeground="#ffffff",
            font=("Segoe UI", 10),
        ).pack(side="left", padx=(0, 12))
        tk.Radiobutton(
            col,
            text="З преміумом",
            variable=self._col_var,
            value="with",
            command=self._on_column_toggle,
            bg="#12141a",
            fg="#e8eaed",
            selectcolor="#2a2f38",
            activebackground="#12141a",
            activeforeground="#ffffff",
            font=("Segoe UI", 10),
        ).pack(side="left")

        btns = tk.Frame(win, bg="#12141a")
        btns.pack(fill="x", padx=12, pady=(10, 8))
        for text, cmd in (
            ("Зберегти", self._save),
            ("Скинути колонку", self._reset_column),
            ("Закрити", self.close),
        ):
            tk.Button(
                btns,
                text=text,
                command=cmd,
                font=("Segoe UI", 10),
                fg="#e8eaed",
                bg="#2a2f38",
                activebackground="#3a414d",
                relief="flat",
                padx=10,
                pady=6,
                cursor="hand2",
            ).pack(side="left", padx=(0, 8))

        tk.Label(
            win,
            text=(
                "Дроби 0..1 від кадру (вікно або зображення) — масштабуються на будь-який "
                "екран. Чужий скріншот: відкрий файл і вирівняй RP/SL по цифрах."
            ),
            font=("Segoe UI", 9),
            fg="#8b909a",
            bg="#12141a",
            wraplength=430,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 10))
        self._panel = win

    def _on_source_toggle(self) -> None:
        self._source = (self._src_var.get() if self._src_var else _SRC_WT) or _SRC_WT
        self._last_geom = None
        self._logic_size = None
        self._rebuild_overlay_shell()
        self._redraw_overlay(force_boxes=True)
        self._update_status_source()

    def _update_status_source(self) -> None:
        if self._status is None:
            return
        if self._source == _SRC_WT:
            self._status.configure(text="Джерело: War Thunder (живе вікно).")
        elif self._source == _SRC_HWND:
            title = _window_title(self._target_hwnd) if self._target_hwnd else "—"
            self._status.configure(
                text=f"Джерело: інше вікно — {title or hex(self._target_hwnd or 0)}"
            )
        else:
            name = self._image_path.name if self._image_path else "—"
            size = f"{self._image.size[0]}x{self._image.size[1]}" if self._image else "?"
            self._status.configure(text=f"Джерело: зображення {name} ({size})")

    def _start_pick_window(self) -> None:
        if self._src_var is not None:
            self._src_var.set(_SRC_HWND)
        self._source = _SRC_HWND
        if self._status is not None:
            self._status.configure(
                text="Наведи курсор на потрібне вікно… захват через 2 с"
            )
        if self._pick_job is not None:
            try:
                self.master.after_cancel(self._pick_job)
            except Exception:
                pass
        self._pick_job = self.master.after(2000, self._finish_pick_window)

    def _finish_pick_window(self) -> None:
        self._pick_job = None
        hwnd = _hwnd_under_cursor()
        if not hwnd or not user32.IsWindow(hwnd):
            if self._status is not None:
                self._status.configure(text="Не вдалося захопити вікно під курсором.")
            return
        # Don't pick our own calibrator / Bridge windows.
        skip: set[int] = set()
        for win in (self._panel, self._overlay, self.master):
            if win is None:
                continue
            try:
                skip.add(_toplevel_hwnd(win))
            except Exception:
                pass
        if hwnd in skip:
            if self._status is not None:
                self._status.configure(
                    text="Курсор був над Bridge — наведи на чуже вікно і знову «Вибрати вікно»."
                )
            return
        self._target_hwnd = int(hwnd)
        self._image = None
        self._image_path = None
        self._photo = None
        self._last_geom = None
        self._rebuild_overlay_shell()
        self._redraw_overlay(force_boxes=True)
        self._update_status_source()

    def _open_image(self) -> None:
        path = filedialog.askopenfilename(
            parent=self._panel or self.master,
            title="Скріншот для калібрування ROI",
            filetypes=[
                ("Images", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            image = Image.open(path).convert("RGB")
        except OSError as exc:
            if self._status is not None:
                self._status.configure(text=f"Не вдалося відкрити: {exc}")
            return
        if self._src_var is not None:
            self._src_var.set(_SRC_IMAGE)
        self._source = _SRC_IMAGE
        self._image = image
        self._image_path = Path(path)
        self._target_hwnd = None
        self._last_geom = None
        self._logic_size = image.size
        self._rebuild_overlay_shell()
        self._redraw_overlay(force_boxes=True)
        self._update_status_source()

    def _on_column_toggle(self) -> None:
        self._edit_with = bool(self._col_var and self._col_var.get() == "with")
        self._redraw_overlay(force_boxes=True)
        if self._status is not None:
            which = "З преміумом" if self._edit_with else "Без преміуму"
            self._status.configure(text=f"Редагується колонка: {which}")

    def _reset_column(self) -> None:
        defaults = default_calibrated_rois()
        self._set_active_column(
            defaults.with_premium if self._edit_with else defaults.without_premium
        )
        self._redraw_overlay(force_boxes=True)

    def _save(self) -> None:
        also_pkg = not bool(getattr(sys, "frozen", False))
        paths = save_calibrated_rois(self._calib, also_package=also_pkg)
        if self._status is not None:
            self._status.configure(text="Збережено:\n" + "\n".join(str(p) for p in paths))

    def _rebuild_overlay_shell(self) -> None:
        """Recreate overlay: transparent-on-window vs image canvas."""
        if self._overlay is not None:
            try:
                self._overlay.destroy()
            except Exception:
                pass
        self._overlay = None
        self._canvas = None
        self._photo = None
        self._ensure_overlay()

    def _ensure_overlay(self) -> None:
        if self._overlay is not None:
            try:
                if bool(self._overlay.winfo_exists()):
                    return
            except Exception:
                pass
        win = tk.Toplevel(self.master)
        win.attributes("-topmost", True)
        if self._source == _SRC_IMAGE:
            win.title("ROI calibrator — screenshot")
            win.configure(bg="#0a0b0e")
            canvas = tk.Canvas(win, bg="#0a0b0e", highlightthickness=0, bd=0, cursor="crosshair")
        else:
            win.overrideredirect(True)
            try:
                win.attributes("-transparentcolor", _KEY)
            except tk.TclError:
                pass
            win.configure(bg=_KEY)
            canvas = tk.Canvas(win, bg=_KEY, highlightthickness=0, bd=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        canvas.bind("<ButtonPress-1>", self._on_press)
        canvas.bind("<B1-Motion>", self._on_drag)
        canvas.bind("<ButtonRelease-1>", self._on_release)
        self._overlay = win
        self._canvas = canvas
        win.update_idletasks()
        if self._source != _SRC_IMAGE:
            try:
                _style_tool_topmost(_toplevel_hwnd(win))
            except Exception:
                pass

    def _resolve_target_hwnd(self) -> int | None:
        if self._source == _SRC_WT:
            return find_war_thunder_hwnd()
        if self._source == _SRC_HWND:
            if self._target_hwnd and user32.IsWindow(self._target_hwnd):
                return self._target_hwnd
            return None
        return None

    def _redraw_overlay(self, *, force_boxes: bool) -> None:
        self._ensure_overlay()
        win = self._overlay
        canvas = self._canvas
        if win is None or canvas is None:
            return

        if self._source == _SRC_IMAGE:
            self._redraw_image_mode(win, canvas, force_boxes=force_boxes)
            return

        hwnd = self._resolve_target_hwnd()
        bounds = _window_rect(hwnd) if hwnd else None
        if bounds is None:
            canvas.delete("all")
            win.geometry("520x40+60+60")
            canvas.create_rectangle(0, 0, 520, 40, fill="#000000", outline="")
            hint = {
                _SRC_WT: "Відкрий War Thunder (windowed/borderless)",
                _SRC_HWND: "Натисни «Вибрати вікно…» і наведи курсор на ціль",
            }.get(self._source, "Немає цілі")
            canvas.create_text(
                8, 10, anchor="nw", fill="#ffffff", font=("Segoe UI", 11), text=hint
            )
            self._last_geom = None
            self._logic_size = None
            return

        left, top, right, bottom = bounds
        width = max(1, right - left)
        height = max(1, bottom - top)
        geom = (left, top, width, height)
        geom_changed = geom != self._last_geom
        if geom_changed:
            win.geometry(f"{width}x{height}+{left}+{top}")
            self._last_geom = geom
            self._logic_size = (width, height)
            win.update_idletasks()
            try:
                _style_tool_topmost(_toplevel_hwnd(win))
            except Exception:
                pass

        if not force_boxes and not geom_changed and canvas.find_withtag("box"):
            return

        canvas.delete("all")
        probe = Image.new("RGB", (width, height), (0, 0, 0))
        self._paint_boxes(canvas, probe, width, height, label_suffix="")

    def _redraw_image_mode(
        self, win: tk.Toplevel, canvas: tk.Canvas, *, force_boxes: bool
    ) -> None:
        if self._image is None:
            canvas.delete("all")
            win.geometry("520x80+100+120")
            canvas.create_text(
                12, 24, anchor="nw", fill="#ffffff", font=("Segoe UI", 11),
                text="Відкрий скріншот (Відкрити зображення…)",
            )
            self._last_geom = None
            self._logic_size = None
            return

        iw, ih = self._image.size
        self._logic_size = (iw, ih)

        # Fit into ~90% of screen while keeping aspect.
        sw = max(640, int(win.winfo_screenwidth() * 0.9))
        sh = max(480, int(win.winfo_screenheight() * 0.85))
        scale = min(sw / float(iw), sh / float(ih), 1.0)
        dw = max(1, int(round(iw * scale)))
        dh = max(1, int(round(ih * scale)))
        self._img_scale = scale
        self._img_ox = 0
        self._img_oy = 28  # HUD strip

        geom = (0, 0, dw, dh + 28)
        geom_changed = geom != self._last_geom or self._photo is None
        if geom_changed:
            win.geometry(f"{dw}x{dh + 28}+40+40")
            self._last_geom = geom
            display = self._image if scale >= 0.999 else self._image.resize(
                (dw, dh), Image.Resampling.LANCZOS
            )
            self._photo = ImageTk.PhotoImage(display)
            win.update_idletasks()

        if not force_boxes and not geom_changed and canvas.find_withtag("box"):
            return

        canvas.delete("all")
        canvas.create_rectangle(0, 0, dw, 28, fill="#000000", outline="", tags="hud")
        which = "WITH" if self._edit_with else "WITHOUT"
        name = self._image_path.name if self._image_path else "image"
        canvas.create_text(
            8, 6, anchor="nw", fill="#ffffff", font=("Segoe UI", 11),
            text=f"Calibrator  {which}  {iw}x{ih}  {name}  scale={scale:.2f}",
            tags="hud",
        )
        if self._photo is not None:
            canvas.create_image(
                self._img_ox, self._img_oy, anchor="nw", image=self._photo, tags="bg"
            )
        # Boxes in canvas coords = image pixels * scale + offset.
        probe = self._image
        col = self._active_column()
        self._draw_box_scaled(canvas, probe, col.rp, _RP_COLOR, "RP", "rp")
        self._draw_box_scaled(canvas, probe, col.sl, _SL_COLOR, "SL", "sl")

    def _paint_boxes(
        self,
        canvas: tk.Canvas,
        probe: Image.Image,
        width: int,
        height: int,
        *,
        label_suffix: str,
    ) -> None:
        col = self._active_column()
        self._draw_box(canvas, probe, col.rp, _RP_COLOR, "RP", "rp")
        self._draw_box(canvas, probe, col.sl, _SL_COLOR, "SL", "sl")
        which = "WITH" if self._edit_with else "WITHOUT"
        canvas.create_rectangle(0, 0, width, 28, fill="#000000", outline="", tags="hud")
        canvas.create_text(
            8, 6, anchor="nw", fill="#ffffff", font=("Segoe UI", 11),
            text=f"Calibrator  {which}  {width}x{height}{label_suffix}  drag box / corner",
            tags="hud",
        )

    def _draw_box(self, canvas, probe, rect, color, label, key) -> None:
        box = pixel_box(probe, rect, min_width=8, min_height=8)
        if box is None:
            return
        l, t, r, b = box
        canvas.create_rectangle(l, t, r - 1, b - 1, outline=color, width=3, tags=("box", key))
        canvas.create_text(
            l + 4, max(30, t - 16), anchor="nw", fill=color,
            font=("Segoe UI Semibold", 11), text=label, tags=("box", key),
        )
        canvas.create_rectangle(
            r - _HANDLE, b - _HANDLE, r, b, fill=color, outline="#000000",
            tags=("box", key, f"handle-{key}"),
        )

    def _draw_box_scaled(self, canvas, probe, rect, color, label, key) -> None:
        box = pixel_box(probe, rect, min_width=4, min_height=4)
        if box is None:
            return
        s = self._img_scale
        ox, oy = self._img_ox, self._img_oy
        l = int(round(box[0] * s)) + ox
        t = int(round(box[1] * s)) + oy
        r = int(round(box[2] * s)) + ox
        b = int(round(box[3] * s)) + oy
        canvas.create_rectangle(l, t, r - 1, b - 1, outline=color, width=3, tags=("box", key))
        canvas.create_text(
            l + 4, max(oy + 2, t - 16), anchor="nw", fill=color,
            font=("Segoe UI Semibold", 11), text=label, tags=("box", key),
        )
        canvas.create_rectangle(
            r - _HANDLE, b - _HANDLE, r, b, fill=color, outline="#000000",
            tags=("box", key, f"handle-{key}"),
        )

    def _canvas_to_logic(self, x: int, y: int) -> tuple[float, float] | None:
        """Map canvas pixel → logical image/window content coords."""
        if self._logic_size is None:
            return None
        if self._source == _SRC_IMAGE:
            lx = (x - self._img_ox) / max(1e-6, self._img_scale)
            ly = (y - self._img_oy) / max(1e-6, self._img_scale)
            return lx, ly
        return float(x), float(y)

    def _hit_test(self, x: int, y: int) -> str | None:
        if self._logic_size is None:
            return None
        mapped = self._canvas_to_logic(x, y)
        if mapped is None:
            return None
        lx, ly = mapped
        iw, ih = self._logic_size
        probe = (
            self._image
            if self._source == _SRC_IMAGE and self._image is not None
            else Image.new("RGB", (iw, ih), (0, 0, 0))
        )
        col = self._active_column()
        # Hit slop in logical pixels (scale-aware for image mode).
        handle = max(8.0, _HANDLE / max(1e-6, self._img_scale if self._source == _SRC_IMAGE else 1.0))
        for key, rect in (("rp", col.rp), ("sl", col.sl)):
            box = pixel_box(probe, rect, min_width=4, min_height=4)
            if box is None:
                continue
            l, t, r, b = box
            if r - handle <= lx <= r and b - handle <= ly <= b:
                return f"resize-{key}"
            if l <= lx <= r and t <= ly <= b:
                return f"move-{key}"
        return None

    def _on_press(self, event) -> None:
        kind = self._hit_test(int(event.x), int(event.y))
        if not kind:
            return
        key = kind.split("-", 1)[1]
        col = self._active_column()
        orig = col.rp if key == "rp" else col.sl
        mapped = self._canvas_to_logic(int(event.x), int(event.y))
        if mapped is None:
            return
        self._drag = _DragState(
            kind=kind,
            start_x=float(mapped[0]),
            start_y=float(mapped[1]),
            orig=orig,
        )

    def _on_drag(self, event) -> None:
        if self._drag is None or self._logic_size is None:
            return
        mapped = self._canvas_to_logic(int(event.x), int(event.y))
        if mapped is None:
            return
        iw, ih = self._logic_size
        if iw < 2 or ih < 2:
            return
        probe = (
            self._image
            if self._source == _SRC_IMAGE and self._image is not None
            else Image.new("RGB", (iw, ih), (0, 0, 0))
        )
        fl, ft, fr, fb = content_frame(probe)
        fw = max(1, fr - fl)
        fh = max(1, fb - ft)
        dx = (mapped[0] - self._drag.start_x) / float(fw)
        dy = (mapped[1] - self._drag.start_y) / float(fh)
        o = self._drag.orig
        key = self._drag.kind.split("-", 1)[1]
        if self._drag.kind.startswith("move-"):
            bw = o.right - o.left
            bh = o.bottom - o.top
            left = min(max(0.0, o.left + dx), 1.0 - bw)
            top = min(max(0.0, o.top + dy), 1.0 - bh)
            new = NormRect(left, top, left + bw, top + bh, o.tag).clamp()
        else:
            right = min(1.0, max(o.left + 0.02, o.right + dx))
            bottom = min(1.0, max(o.top + 0.015, o.bottom + dy))
            new = NormRect(o.left, o.top, right, bottom, o.tag).clamp()
        col = self._active_column()
        if key == "rp":
            self._set_active_column(ColumnRois(rp=new, sl=col.sl))
        else:
            self._set_active_column(ColumnRois(rp=col.rp, sl=new))
        self._redraw_overlay(force_boxes=True)

    def _on_release(self, _event) -> None:
        self._drag = None
