"""Developer editor for the single OCR zone shipped to every user.

Same shell as the old pair calibrator: a borderless overlay locked to the
War Thunder window (any monitor) or a borderless fullscreen screenshot, plus a
topmost control panel. Fractions are of the full frame, matching the crop the
shipping OCR path uses.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from PIL import Image, ImageTk

from .dpi import fit_toplevel
from .paths import is_frozen
from .roi_calib import default_parse_zone, load_parse_zone, save_parse_zone
from .roi_calibrator_ui import (
    _force_hwnd_topmost,
    _style_tool_topmost,
    _toplevel_hwnd,
    _window_rect,
)
from .roi_layout import NormRect

log = logging.getLogger("lockon_bridge.parse_zone_ui")

_KEY = "#ff00ff"
_ZONE = "#ff66ff"
_HANDLE = 12
_SRC_WT = "wt"
_SRC_IMAGE = "image"

user32 = ctypes.windll.user32


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


def _monitor_at(x: int, y: int) -> tuple[int, int, int, int]:
    """(left, top, width, height) of the monitor nearest ``x, y``."""
    hmon = user32.MonitorFromPoint(_POINT(int(x), int(y)), 2)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    if hmon and user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
        rect = info.rcMonitor
        return (
            int(rect.left),
            int(rect.top),
            max(1, int(rect.right - rect.left)),
            max(1, int(rect.bottom - rect.top)),
        )
    return 0, 0, int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


class ParseZoneEditor:
    """Drag one rectangle over the WT client or a fullscreen screenshot."""

    def __init__(self, master: tk.Misc, *, on_publish=None) -> None:
        self.master = master
        self._on_publish = on_publish
        self._zone = load_parse_zone()
        self._source = _SRC_WT
        self._image: Image.Image | None = None
        self._image_path: Path | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._img_scale = 1.0
        self._img_ox = 0
        self._img_oy = 0
        self._overlay: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._panel: tk.Toplevel | None = None
        self._preview: tk.Toplevel | None = None
        self._preview_text: tk.Text | None = None
        self._preview_busy = False
        self._status: tk.Label | None = None
        self._src_var: tk.StringVar | None = None
        self._job: str | None = None
        self._last_geom: tuple[int, int, int, int] | None = None
        self._logic_size: tuple[int, int] | None = None
        self._drag: str | None = None
        self._origin_frac: tuple[float, float] = (0.0, 0.0)
        self._origin_zone = self._zone
        self._running = False
        self._panel_size_locked = False
        self._preview_size_locked = False
        self._view_var: tk.StringVar | None = None
        self._preview_raw = ""
        self._preview_phone = ""

    def open(self) -> None:
        self._zone = load_parse_zone()
        if self._running:
            self._redraw(force=True)
            self._fit_panel()
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
        for attr in ("_overlay", "_panel", "_preview"):
            win = getattr(self, attr)
            if win is not None:
                try:
                    win.destroy()
                except Exception:
                    pass
                setattr(self, attr, None)
        self._panel_size_locked = False
        self._preview_size_locked = False
        self._preview_text = None
        self._preview_busy = False
        self._canvas = None
        self._status = None
        self._photo = None
        self._drag = None
        self._last_geom = None
        self._logic_size = None

    def _lift(self) -> None:
        for win in (self._overlay, self._panel, self._preview):
            if win is None:
                continue
            try:
                win.attributes("-topmost", True)
                win.lift()
            except Exception:
                pass
        self._pin_controls()

    def _pin_controls(self) -> None:
        for win in (self._panel, self._preview):
            if win is None:
                continue
            try:
                if not bool(win.winfo_exists()):
                    continue
                win.attributes("-topmost", True)
                win.lift()
                _force_hwnd_topmost(_toplevel_hwnd(win), activate=False)
            except Exception:
                pass

    def _schedule(self) -> None:
        if self._job is not None:
            try:
                self.master.after_cancel(self._job)
            except Exception:
                pass
        if self._running:
            delay = 800 if self._source == _SRC_IMAGE else 350
            self._job = self.master.after(delay, self._tick)

    def _tick(self) -> None:
        self._job = None
        if not self._running:
            return
        try:
            if self._drag is None:
                self._redraw(force=False)
            self._pin_controls()
        except Exception as exc:
            log.warning("zone editor tick failed: %s", exc)
        self._schedule()

    def _button(self, parent: tk.Misc, text: str, command) -> None:
        tk.Button(
            parent,
            text=text,
            command=command,
            font=("Segoe UI", 10),
            fg="#e8eaed",
            bg="#2a2f38",
            activebackground="#3a414d",
            relief="flat",
            padx=10,
            pady=6,
            cursor="hand2",
        ).pack(side="left", padx=(0, 8))

    def _ensure_panel(self) -> None:
        if self._panel is not None:
            try:
                if bool(self._panel.winfo_exists()):
                    return
            except Exception:
                pass
        win = tk.Toplevel(self.master)
        win.title("OCR zone")
        win.configure(bg="#12141a")
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.bind("<Escape>", lambda _e: self.close())
        win.bind("<FocusOut>", lambda _e: self.master.after_idle(self._pin_controls), add="+")
        win.bind("<Map>", lambda _e: self._pin_controls(), add="+")

        self._status = tk.Label(
            win,
            text="Обери джерело і посунь рамку на таблицю результатів.",
            font=("Segoe UI", 10),
            fg="#c8ccd4",
            bg="#12141a",
            wraplength=460,
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
            (_SRC_WT, "War Thunder (оверлей на вікні клієнта)"),
            (_SRC_IMAGE, "Скріншот / зображення (fullscreen)"),
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
        self._button(src_btns, "Відкрити зображення…", self._open_image)
        self._button(src_btns, "Останній fail", self._open_last_fail)
        self._button(src_btns, "Папка captures", self._open_captures_folder)

        btns = tk.Frame(win, bg="#12141a")
        btns.pack(fill="x", padx=12, pady=(10, 8))
        for text, cmd in (
            ("Зберегти", self._save),
            ("Скинути", self._reset),
            ("Перевірити зону", self._open_parse_preview),
            ("Закрити", self.close),
        ):
            self._button(btns, text, cmd)

        tk.Label(
            win,
            text=(
                "Рамка стоїть на весь клієнт War Thunder (будь-який монітор) "
                "або на весь екран зі скріншотом. Тягни всередині рамки, щоб "
                "зсунути; кути — розмір. На скріншоті можна намалювати нову "
                "рамку на порожньому місці. «Перевірити зону» читає поточну "
                "рамку через OCR.space, навіть якщо її ще не збережено. Esc — закрити."
            ),
            font=("Segoe UI", 9),
            fg="#8b909a",
            bg="#12141a",
            wraplength=460,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 10))
        self._panel = win
        self._panel_size_locked = False
        self._fit_panel()
        self._set_status(self._zone_status())
        self._pin_controls()

    def _fit_panel(self) -> None:
        win = self._panel
        if win is None or self._panel_size_locked:
            return
        try:
            win.update_idletasks()
            width = max(480, int(win.winfo_reqwidth()))
            for child in win.winfo_children():
                if isinstance(child, tk.Label):
                    try:
                        child.configure(wraplength=max(400, width - 36))
                    except tk.TclError:
                        pass
            win.update_idletasks()
        except Exception:
            pass
        fit_toplevel(win, min_w=520, min_h=280, pad_w=20, pad_h=36, x=40, y=40, fixed=True)
        self._panel_size_locked = True
        self._pin_controls()

    def _set_status(self, text: str) -> None:
        if self._status is not None:
            self._status.configure(text=text)

    def _zone_status(self) -> str:
        z = self._zone
        where = "repo + local" if not is_frozen() else "local only"
        return (
            f"зона {z.left:.3f},{z.top:.3f} – {z.right:.3f},{z.bottom:.3f}  ·  {where}"
        )

    def _on_source_toggle(self) -> None:
        self._source = (self._src_var.get() if self._src_var else _SRC_WT) or _SRC_WT
        self._last_geom = None
        self._logic_size = None
        self._photo = None
        self._rebuild_overlay()
        self._redraw(force=True)
        if self._source == _SRC_WT:
            self._set_status("Джерело: War Thunder. Рамка лягає на вікно клієнта.")
        else:
            name = self._image_path.name if self._image_path else "відкрий зображення"
            self._set_status(f"Джерело: {name}")

    def _open_image(self) -> None:
        from .capture_archive import captures_dir

        initial = captures_dir()
        try:
            initial.mkdir(parents=True, exist_ok=True)
        except OSError:
            initial = Path.home()
        path = filedialog.askopenfilename(
            parent=self._panel or self.master,
            title="Скріншот для зони OCR",
            initialdir=str(initial),
            filetypes=[
                ("Images", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._load_image_path(Path(path))

    def _open_last_fail(self) -> None:
        from .capture_archive import preferred_fail_image_path

        path = preferred_fail_image_path()
        if path is None or not path.is_file():
            self._set_status("Немає збереженого fail-кадру (LocalAppData\\LockOnBridge\\captures).")
            return
        self._load_image_path(path)

    def _open_captures_folder(self) -> None:
        import os

        from .capture_archive import captures_dir

        folder = captures_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))  # noqa: S606
        except OSError as exc:
            self._set_status(f"Не вдалося відкрити папку: {exc}")

    def _load_image_path(self, path: Path) -> None:
        try:
            image = Image.open(path).convert("RGB")
        except OSError as exc:
            self._set_status(f"Не вдалося відкрити: {exc}")
            return
        if self._src_var is not None:
            self._src_var.set(_SRC_IMAGE)
        self._source = _SRC_IMAGE
        self._image = image
        self._image_path = path
        self._last_geom = None
        self._logic_size = image.size
        self._photo = None
        self._rebuild_overlay()
        self._redraw(force=True)
        self._set_status(f"Зображення {path.name} ({image.size[0]}×{image.size[1]})")

    def _reset(self) -> None:
        self._zone = default_parse_zone()
        self._redraw(force=True)
        self._set_status("Скинуто до типової зони. Натисни «Зберегти», щоб записати.")

    def _save(self) -> None:
        zone = self._normalized()
        if zone is None:
            self._set_status("Зона занадто маленька.")
            return
        paths = save_parse_zone(zone, also_package=not is_frozen())
        self._zone = zone
        self._set_status("Збережено:\n" + "\n".join(str(p) for p in paths))
        self._panel_size_locked = False
        self._fit_panel()

    def _rebuild_overlay(self) -> None:
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
        win.overrideredirect(True)
        if self._source == _SRC_IMAGE:
            win.configure(bg="#000000")
            canvas = tk.Canvas(win, bg="#000000", highlightthickness=0, bd=0, cursor="crosshair")
        else:
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
        canvas.bind("<ButtonPress-1>", lambda _e: self._pin_controls(), add="+")
        canvas.bind("<Escape>", lambda _e: self.close())
        win.bind("<Escape>", lambda _e: self.close())
        win.bind("<FocusIn>", lambda _e: self._pin_controls(), add="+")
        self._overlay = win
        self._canvas = canvas
        win.update_idletasks()
        try:
            _style_tool_topmost(_toplevel_hwnd(win))
            _force_hwnd_topmost(_toplevel_hwnd(win), activate=False)
        except Exception:
            pass
        self._pin_controls()

    def _panel_point(self) -> tuple[int, int]:
        panel = self._panel
        if panel is not None:
            try:
                return int(panel.winfo_rootx()) + 20, int(panel.winfo_rooty()) + 20
            except Exception:
                pass
        return 40, 40

    def _redraw(self, *, force: bool) -> None:
        self._ensure_overlay()
        win = self._overlay
        canvas = self._canvas
        if win is None or canvas is None:
            return
        if self._source == _SRC_IMAGE:
            self._redraw_image(win, canvas, force=force)
            return
        self._redraw_wt(win, canvas, force=force)

    def _redraw_wt(self, win: tk.Toplevel, canvas: tk.Canvas, *, force: bool) -> None:
        from .capture import find_war_thunder_hwnd

        hwnd = find_war_thunder_hwnd()
        bounds = _window_rect(hwnd) if hwnd else None
        if bounds is None:
            canvas.delete("all")
            win.geometry("560x48+60+60")
            canvas.create_rectangle(0, 0, 560, 48, fill="#000000", outline="")
            canvas.create_text(
                10,
                12,
                anchor="nw",
                fill="#ffffff",
                font=("Segoe UI", 11),
                text="Відкрий War Thunder (windowed / borderless)",
            )
            self._last_geom = None
            self._logic_size = None
            self._pin_controls()
            return
        left, top, right, bottom = bounds
        width = max(1, right - left)
        height = max(1, bottom - top)
        geom = (left, top, width, height)
        if geom != self._last_geom:
            win.geometry(f"{width}x{height}{left:+d}{top:+d}")
            self._last_geom = geom
            self._logic_size = (width, height)
            win.update_idletasks()
            try:
                _style_tool_topmost(_toplevel_hwnd(win))
            except Exception:
                pass
        if not force and canvas.find_withtag("box"):
            self._pin_controls()
            return
        canvas.delete("all")
        self._paint_zone(canvas, width, height, ox=0, oy=0, scale=1.0)
        self._pin_controls()

    def _redraw_image(self, win: tk.Toplevel, canvas: tk.Canvas, *, force: bool) -> None:
        mx, my, sw, sh = _monitor_at(*self._panel_point())
        if self._image is None:
            canvas.delete("all")
            win.geometry(f"{sw}x{sh}{mx:+d}{my:+d}")
            canvas.create_text(
                sw // 2,
                sh // 2,
                anchor="center",
                fill="#ffffff",
                font=("Segoe UI", 16),
                text="Відкрий скріншот\nEsc — закрити",
            )
            self._last_geom = None
            self._logic_size = None
            self._pin_controls()
            return
        iw, ih = self._image.size
        self._logic_size = (iw, ih)
        scale = min(sw / float(iw), sh / float(ih))
        dw = max(1, int(round(iw * scale)))
        dh = max(1, int(round(ih * scale)))
        self._img_scale = scale
        self._img_ox = (sw - dw) // 2
        self._img_oy = (sh - dh) // 2
        geom = (mx, my, sw, sh, dw, dh)
        changed = geom != self._last_geom or self._photo is None
        if changed:
            win.geometry(f"{sw}x{sh}{mx:+d}{my:+d}")
            display = (
                self._image
                if abs(scale - 1.0) < 1e-3
                else self._image.resize((dw, dh), Image.Resampling.LANCZOS)
            )
            self._photo = ImageTk.PhotoImage(display)
            self._last_geom = geom  # type: ignore[assignment]
            win.update_idletasks()
            try:
                _style_tool_topmost(_toplevel_hwnd(win))
            except Exception:
                pass
        if not force and not changed and canvas.find_withtag("box"):
            self._pin_controls()
            return
        canvas.delete("all")
        canvas.create_rectangle(0, 0, sw, sh, fill="#000000", outline="")
        if self._photo is not None:
            canvas.create_image(self._img_ox, self._img_oy, anchor="nw", image=self._photo)
        self._paint_zone(canvas, iw, ih, ox=self._img_ox, oy=self._img_oy, scale=scale)
        self._pin_controls()

    def _paint_zone(
        self,
        canvas: tk.Canvas,
        width: int,
        height: int,
        *,
        ox: int,
        oy: int,
        scale: float,
    ) -> None:
        zone = self._zone
        x0 = int(round(zone.left * width * scale)) + ox
        y0 = int(round(zone.top * height * scale)) + oy
        x1 = int(round(zone.right * width * scale)) + ox
        y1 = int(round(zone.bottom * height * scale)) + oy
        # Stipple keeps the game visible and still receives drags (a hollow
        # outline on the color-key overlay would let clicks fall through).
        canvas.create_rectangle(
            x0,
            y0,
            x1,
            y1,
            outline=_ZONE,
            width=3,
            fill="#111111",
            stipple="gray50",
            tags=("box",),
        )
        canvas.create_text(
            x0 + 6,
            max(oy + 4, y0 - 18),
            anchor="nw",
            fill=_ZONE,
            font=("Segoe UI Semibold", 12),
            text="OCR",
            tags=("box",),
        )
        for name, hx, hy in (
            ("nw", x0, y0),
            ("ne", x1, y0),
            ("sw", x0, y1),
            ("se", x1, y1),
        ):
            canvas.create_rectangle(
                hx - _HANDLE // 2,
                hy - _HANDLE // 2,
                hx + _HANDLE // 2,
                hy + _HANDLE // 2,
                fill=_ZONE,
                outline="#000000",
                tags=("box", f"handle-{name}"),
            )

    def _zone_px(self) -> tuple[int, int, int, int] | None:
        size = self._logic_size
        if size is None:
            return None
        width, height = size
        zone = self._zone
        if self._source == _SRC_IMAGE:
            scale = self._img_scale
            ox, oy = self._img_ox, self._img_oy
        else:
            scale, ox, oy = 1.0, 0, 0
        return (
            int(round(zone.left * width * scale)) + ox,
            int(round(zone.top * height * scale)) + oy,
            int(round(zone.right * width * scale)) + ox,
            int(round(zone.bottom * height * scale)) + oy,
        )

    def _event_frac(self, x: int, y: int) -> tuple[float, float] | None:
        size = self._logic_size
        if size is None:
            return None
        width, height = size
        if width < 2 or height < 2:
            return None
        if self._source == _SRC_IMAGE:
            lx = (x - self._img_ox) / max(1e-6, self._img_scale)
            ly = (y - self._img_oy) / max(1e-6, self._img_scale)
        else:
            lx, ly = float(x), float(y)
        return (
            max(0.0, min(1.0, lx / float(width))),
            max(0.0, min(1.0, ly / float(height))),
        )

    def _hit(self, x: int, y: int) -> str:
        box = self._zone_px()
        if box is None:
            return "miss"
        x0, y0, x1, y1 = box
        pad = _HANDLE + 4
        corners = {"nw": (x0, y0), "ne": (x1, y0), "sw": (x0, y1), "se": (x1, y1)}
        for name, (cx, cy) in corners.items():
            if abs(x - cx) <= pad and abs(y - cy) <= pad:
                return name
        if x0 <= x <= x1 and y0 <= y <= y1:
            return "move"
        return "draw" if self._source == _SRC_IMAGE else "miss"

    def _on_press(self, event: tk.Event) -> None:
        kind = self._hit(int(event.x), int(event.y))
        frac = self._event_frac(int(event.x), int(event.y))
        if kind == "miss" or frac is None:
            return
        self._drag = kind
        self._origin_frac = frac
        self._origin_zone = self._zone
        if kind == "draw":
            self._zone = NormRect(frac[0], frac[1], frac[0] + 0.01, frac[1] + 0.01, "parse-zone")
            self._redraw(force=True)

    def _on_drag(self, event: tk.Event) -> None:
        if self._drag is None:
            return
        frac = self._event_frac(int(event.x), int(event.y))
        if frac is None:
            return
        x, y = frac
        ox, oy = self._origin_frac
        z = self._origin_zone
        if self._drag == "move":
            dx, dy = x - ox, y - oy
            width = z.right - z.left
            height = z.bottom - z.top
            left = min(max(0.0, z.left + dx), 1.0 - width)
            top = min(max(0.0, z.top + dy), 1.0 - height)
            self._zone = NormRect(left, top, left + width, top + height, "parse-zone")
        elif self._drag == "draw":
            self._zone = NormRect(min(ox, x), min(oy, y), max(ox, x), max(oy, y), "parse-zone")
        else:
            left, top, right, bottom = z.left, z.top, z.right, z.bottom
            if "w" in self._drag:
                left = x
            if "e" in self._drag:
                right = x
            if "n" in self._drag:
                top = y
            if "s" in self._drag:
                bottom = y
            self._zone = NormRect(
                min(left, right),
                min(top, bottom),
                max(left, right),
                max(top, bottom),
                "parse-zone",
            ).clamp()
        self._redraw(force=True)
        self._set_status(self._zone_status())

    def _on_release(self, _event: tk.Event) -> None:
        self._drag = None
        zone = self._normalized()
        if zone is None:
            self._zone = self._origin_zone
            self._redraw(force=True)
            self._set_status("Зона занадто маленька — повернув попередню.")
        else:
            self._zone = zone
        self._pin_controls()

    def _normalized(self) -> NormRect | None:
        zone = self._zone.clamp()
        if zone.right - zone.left < 0.04 or zone.bottom - zone.top < 0.04:
            return None
        return zone

    def _open_parse_preview(self) -> None:
        self._ensure_preview()
        self._run_parse_preview()

    def _ensure_preview(self) -> None:
        if self._preview is not None:
            try:
                if bool(self._preview.winfo_exists()):
                    self._preview.deiconify()
                    self._pin_controls()
                    return
            except Exception:
                pass
        win = tk.Toplevel(self.master)
        win.title("Перевірка зони OCR")
        win.configure(bg="#12141a")
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self._hide_preview)
        win.bind("<FocusOut>", lambda _e: self.master.after_idle(self._pin_controls), add="+")
        mode = tk.Frame(win, bg="#12141a")
        mode.pack(fill="x", padx=12, pady=(10, 0))
        self._view_var = tk.StringVar(value="raw")
        for value, label in (
            ("raw", "Як віддав OCR"),
            ("phone", "На телефон"),
        ):
            tk.Radiobutton(
                mode,
                text=label,
                variable=self._view_var,
                value=value,
                command=self._show_active_preview,
                bg="#12141a",
                fg="#e8eaed",
                selectcolor="#2a2f38",
                activebackground="#12141a",
                activeforeground="#ffffff",
                font=("Segoe UI", 10),
            ).pack(side="left", padx=(0, 12))
        host = tk.Frame(win, bg="#12141a")
        host.pack(fill="both", expand=True, padx=12, pady=(12, 8))
        scroll = tk.Scrollbar(host, orient="vertical")
        scroll.pack(side="right", fill="y")
        text = tk.Text(
            host,
            height=22,
            width=54,
            font=("Consolas", 10),
            fg="#e8eaed",
            bg="#1a1e26",
            relief="flat",
            padx=8,
            pady=8,
            wrap="word",
            yscrollcommand=scroll.set,
        )
        text.pack(side="left", fill="both", expand=True)
        scroll.configure(command=text.yview)
        text.insert("1.0", "Натисни «Перевірити зону».")
        text.configure(state="disabled")
        self._preview_text = text
        row = tk.Frame(win, bg="#12141a")
        row.pack(fill="x", padx=12, pady=(0, 12))
        self._button(row, "Оновити", self._run_parse_preview)
        self._button(row, "Сховати", self._hide_preview)
        self._preview = win
        self._preview_size_locked = False
        x, y = 560, 40
        panel = self._panel
        if panel is not None:
            try:
                x = int(panel.winfo_rootx()) + int(panel.winfo_width()) + 12
                y = int(panel.winfo_rooty())
            except Exception:
                pass
        fit_toplevel(win, min_w=460, min_h=360, pad_w=16, pad_h=24, x=x, y=y, fixed=True)
        self._preview_size_locked = True
        self._pin_controls()

    def _hide_preview(self) -> None:
        if self._preview is not None:
            try:
                self._preview.withdraw()
            except Exception:
                pass

    def _set_preview(self, body: str) -> None:
        widget = self._preview_text
        if widget is None:
            return
        try:
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", body)
            widget.configure(state="disabled")
        except tk.TclError:
            pass

    def _grab_frame(self) -> Image.Image | None:
        if self._source == _SRC_IMAGE:
            return None if self._image is None else self._image.copy()
        overlay = self._overlay
        was_mapped = False
        if overlay is not None:
            try:
                was_mapped = bool(overlay.winfo_viewable())
                if was_mapped:
                    overlay.withdraw()
                    overlay.update_idletasks()
            except Exception:
                was_mapped = False
        try:
            from .capture import grab_wt_client_image

            return grab_wt_client_image(focus=False, require_foreground=False)
        finally:
            if overlay is not None and was_mapped:
                try:
                    overlay.deiconify()
                    self._pin_controls()
                except Exception:
                    pass

    def _run_parse_preview(self) -> None:
        if self._preview_busy:
            return
        self._ensure_preview()
        zone = self._normalized()
        frame = self._grab_frame()
        if zone is None:
            self._set_preview("Зона занадто маленька.")
            return
        if frame is None:
            msg = (
                "Немає зображення — відкрий скріншот."
                if self._source == _SRC_IMAGE
                else "Немає кадру WT — відкрий War Thunder (windowed/borderless)."
            )
            self._set_preview(msg)
            self._set_status(msg)
            return
        self._preview_busy = True
        self._set_preview("OCR виділеної зони… Engine 3 може відповідати довше.")
        self._set_status("Перевірка зони OCR…")

        def work() -> None:
            error: str | None = None
            body: tuple[str, str, object] | None = None
            try:
                body = _preview_views(frame, zone)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                log.warning("zone parse preview failed: %s", exc)

            def done() -> None:
                self._preview_busy = False
                if error:
                    self._preview_raw = f"Помилка OCR:\n{error}"
                    self._preview_phone = self._preview_raw
                    self._set_status(f"Перевірка зони: {error}")
                else:
                    raw_view, phone_view, report = body
                    self._preview_raw = raw_view
                    self._preview_phone = phone_view
                    self._remember_and_publish(report)
                    self._set_status(
                        "Перевірка зони готова — перемкни «Як віддав OCR» / «На телефон»."
                    )
                self._show_active_preview()
                self._pin_controls()

            try:
                self.master.after(0, done)
            except tk.TclError:
                self._preview_busy = False

        threading.Thread(target=work, name="zone-parse-preview", daemon=True).start()

    def _show_active_preview(self) -> None:
        mode = self._view_var.get() if self._view_var is not None else "raw"
        body = self._preview_phone if mode == "phone" else self._preview_raw
        self._set_preview(body or "Натисни «Перевірити зону».")

    def _remember_and_publish(self, report) -> None:
        from .runtime import write_last_ocr_dump

        parts = [self._preview_raw.strip()]
        if report is None:
            parts.append("=> (no parse)")
        else:
            parts.append(
                f"=> RP={report.research_points} SL={report.silver_lions}"
            )
        try:
            write_last_ocr_dump("\n".join(parts))
        except OSError as exc:
            log.warning("zone check dump failed: %s", exc)
        if report is None or self._on_publish is None:
            return
        try:
            self._on_publish(report)
        except Exception as exc:  # noqa: BLE001
            log.warning("zone check publish failed: %s", exc)


def _preview_views(frame: Image.Image, zone: NormRect) -> tuple[str, str, object]:
    from .layout_ocr import crop_parse_zone, select_structured_text
    from .ocr_parse import parse_rewards_from_ocr_text
    from .ocr_space import active_ocr_engine, ocr_space_parse, raw_ocr_text
    from .settings import load_settings

    crop = crop_parse_zone(frame, zone)
    buf = _png(crop)
    parsed = ocr_space_parse(buf, language="auto", overlay=True)
    full = parsed.get("text") or ""
    lines = parsed.get("lines") or []
    dump = raw_ocr_text(parsed.get("raw") if isinstance(parsed.get("raw"), dict) else None)
    if not dump:
        dump = full.strip() or "(OCR повернув порожній текст)"
    header = [
        f"Engine {active_ocr_engine()}",
        (
            f"зона {zone.left:.3f},{zone.top:.3f} – {zone.right:.3f},{zone.bottom:.3f}"
            f"   crop {crop.size[0]}×{crop.size[1]} з {frame.size[0]}×{frame.size[1]}"
        ),
    ]
    raw_view = "\n".join(header) + "\n\n" + dump
    structured = select_structured_text(full, lines) or dump
    premium = bool(load_settings().has_premium_account)
    report = parse_rewards_from_ocr_text(structured, prefer_premium_rewards=premium)
    return raw_view, _phone_view(report, premium=premium), report


def _phone_view(report, *, premium: bool) -> str:
    """Body the phone reads from GET /v1/latest-report."""
    import json

    account = "увімкнено" if premium else "вимкнено"
    if report is None:
        return (
            "На телефон нічого не піде: RP/SL не зібрались.\n"
            f"Преміум-акаунт: {account}"
        )
    payload = report.to_json()
    return (
        f"Преміум-акаунт: {account}\n"
        f"RP {payload['researchPoints']}\n"
        f"SL {payload['silverLions']}\n\n"
        "GET /v1/latest-report\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _png(image: Image.Image) -> bytes:
    from io import BytesIO

    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
