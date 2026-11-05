"""Compact toggle switch — prefers brand PNG assets, Canvas fallback."""

from __future__ import annotations

import tkinter as tk
from typing import Any, Callable


class SwitchButton(tk.Canvas):
    """
    Pill track + sliding knob bound to a ``BooleanVar``.

    Uses ``assets/ui/switch_on.png`` / ``switch_off.png`` when present
    (no Tk stipple grain). Optional ``command`` fires after the variable changes.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        variable: tk.BooleanVar,
        command: Callable[[], None] | None = None,
        width: int = 52,
        height: int = 30,
        on_color: str = "#2ec4b6",
        off_color: str = "#4b5563",
        knob_color: str = "#ffffff",
        bg: str = "#242830",
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            bg=bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **kwargs,
        )
        self._var = variable
        self._command = command
        self._track_w = int(width)
        self._track_h = int(height)
        self._on = on_color
        self._off = off_color
        self._knob = knob_color
        self._pad = 3
        self._knob_r = max(8, (height // 2) - self._pad)
        self._trace_id: str | None = None
        self._photo_on = None
        self._photo_off = None
        self._load_photos()

        self.bind("<Button-1>", self._on_click)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._trace_id = self._var.trace_add("write", self._on_var_write)
        self._redraw()

    def _load_photos(self) -> None:
        try:
            from .ui_assets import photo
        except Exception:  # noqa: BLE001
            return
        size = (self._track_w, self._track_h)
        self._photo_on = photo("switch_on.png", size=size)
        self._photo_off = photo("switch_off.png", size=size)

    def _on_destroy(self, _event: Any = None) -> None:
        self._drop_trace()

    def _drop_trace(self) -> None:
        tid = self._trace_id
        self._trace_id = None
        if tid is None:
            return
        try:
            self._var.trace_remove("write", tid)
        except Exception:  # noqa: BLE001
            pass

    def _on_var_write(self, *_args: Any) -> None:
        self._redraw()

    def _on_click(self, _event: Any = None) -> None:
        self._var.set(not bool(self._var.get()))
        try:
            self.update_idletasks()
        except Exception:  # noqa: BLE001
            pass
        if self._command is not None:
            self._command()

    def _redraw(self) -> None:
        try:
            if not self.winfo_exists():
                return
        except Exception:  # noqa: BLE001
            return
        self.delete("all")
        on = bool(self._var.get())
        photo = self._photo_on if on else self._photo_off
        if photo is not None:
            self.create_image(
                self._track_w // 2,
                self._track_h // 2,
                image=photo,
                anchor="center",
            )
            return

        # Fallback vector draw (no stipple — avoids grainy dither).
        track = self._on if on else self._off
        tw, th = self._track_w, self._track_h
        r = th // 2
        self.create_oval(0, 0, th, th, fill=track, outline="")
        self.create_oval(tw - th, 0, tw, th, fill=track, outline="")
        self.create_rectangle(r, 0, tw - r, th, fill=track, outline="")

        cx = (tw - self._pad - self._knob_r) if on else (self._pad + self._knob_r)
        cy = th // 2
        self.create_oval(
            cx - self._knob_r,
            cy - self._knob_r,
            cx + self._knob_r,
            cy + self._knob_r,
            fill=self._knob,
            outline="#d1d5db",
            width=1,
        )
