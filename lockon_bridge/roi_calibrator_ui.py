"""Developer ROI calibrator — two draggable RP/SL boxes (session unlock only)."""

from __future__ import annotations

import ctypes
import hashlib
import logging
import sys
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image

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


def verify_dev_passphrase(raw: str) -> bool:
    digest = hashlib.sha256((raw or "").strip().encode("utf-8")).hexdigest()
    return digest == _DEV_PASS_SHA256


def _window_rect(hwnd: int):
    from .capture import _window_rect as _cap_rect

    return _cap_rect(hwnd)


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


@dataclass
class _DragState:
    kind: str
    start_x: int
    start_y: int
    orig: NormRect


class RoiCalibrator:
    """Interactive 2-box calibrator glued to the WT client."""

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
        self._overlay: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._panel: tk.Toplevel | None = None
        self._status: tk.Label | None = None
        self._job: str | None = None
        self._last_geom: tuple[int, int, int, int] | None = None
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
        self._drag = None
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
            self._job = self.master.after(350, self._tick)

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
        win.attributes("-topmost", True)
        win.geometry("420x240+80+80")
        win.protocol("WM_DELETE_WINDOW", self.close)

        self._status = tk.Label(
            win,
            text="Перетягни зелений (RP) і блакитний (SL) на екрані WT.",
            font=("Segoe UI", 10),
            fg="#c8ccd4",
            bg="#12141a",
            wraplength=390,
            justify="left",
            anchor="w",
        )
        self._status.pack(fill="x", padx=12, pady=(10, 6))

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
                "Дроби 0..1 від клієнта WT — масштабуються на FullHD/2K/16:9/16:10. "
                "Save → репо + LocalAppData; після релізу всі отримують через автооновлення."
            ),
            font=("Segoe UI", 9),
            fg="#8b909a",
            bg="#12141a",
            wraplength=390,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 10))
        self._panel = win

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

    def _ensure_overlay(self) -> None:
        if self._overlay is not None:
            try:
                if bool(self._overlay.winfo_exists()):
                    return
            except Exception:
                pass
        win = tk.Toplevel(self.master)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
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
        try:
            _style_tool_topmost(_toplevel_hwnd(win))
        except Exception:
            pass

    def _redraw_overlay(self, *, force_boxes: bool) -> None:
        self._ensure_overlay()
        win = self._overlay
        canvas = self._canvas
        if win is None or canvas is None:
            return

        hwnd = find_war_thunder_hwnd()
        bounds = _window_rect(hwnd) if hwnd else None
        if bounds is None:
            canvas.delete("all")
            win.geometry("480x36+60+60")
            canvas.create_rectangle(0, 0, 480, 36, fill="#000000", outline="")
            canvas.create_text(
                8, 8, anchor="nw", fill="#ffffff", font=("Segoe UI", 11),
                text="Відкрий War Thunder (windowed/borderless) для калібрування",
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
                _style_tool_topmost(_toplevel_hwnd(win))
            except Exception:
                pass

        if not force_boxes and not geom_changed and canvas.find_withtag("box"):
            return

        canvas.delete("all")
        probe = Image.new("RGB", (width, height), (0, 0, 0))
        col = self._active_column()
        self._draw_box(canvas, probe, col.rp, _RP_COLOR, "RP", "rp")
        self._draw_box(canvas, probe, col.sl, _SL_COLOR, "SL", "sl")
        which = "WITH" if self._edit_with else "WITHOUT"
        canvas.create_rectangle(0, 0, width, 28, fill="#000000", outline="", tags="hud")
        canvas.create_text(
            8, 6, anchor="nw", fill="#ffffff", font=("Segoe UI", 11),
            text=f"Calibrator  {which}  {width}x{height}  drag box / corner",
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

    def _hit_test(self, x: int, y: int) -> str | None:
        if self._last_geom is None:
            return None
        _l, _t, w, h = self._last_geom
        probe = Image.new("RGB", (w, h), (0, 0, 0))
        col = self._active_column()
        for key, rect in (("rp", col.rp), ("sl", col.sl)):
            box = pixel_box(probe, rect, min_width=8, min_height=8)
            if box is None:
                continue
            l, t, r, b = box
            if r - _HANDLE <= x <= r and b - _HANDLE <= y <= b:
                return f"resize-{key}"
            if l <= x <= r and t <= y <= b:
                return f"move-{key}"
        return None

    def _on_press(self, event) -> None:
        kind = self._hit_test(int(event.x), int(event.y))
        if not kind:
            return
        key = kind.split("-", 1)[1]
        col = self._active_column()
        orig = col.rp if key == "rp" else col.sl
        self._drag = _DragState(kind=kind, start_x=int(event.x), start_y=int(event.y), orig=orig)

    def _on_drag(self, event) -> None:
        if self._drag is None or self._last_geom is None:
            return
        _l, _t, width, height = self._last_geom
        if width < 2 or height < 2:
            return
        probe = Image.new("RGB", (width, height), (0, 0, 0))
        fl, ft, fr, fb = content_frame(probe)
        fw = max(1, fr - fl)
        fh = max(1, fb - ft)
        dx = (int(event.x) - self._drag.start_x) / float(fw)
        dy = (int(event.y) - self._drag.start_y) / float(fh)
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
