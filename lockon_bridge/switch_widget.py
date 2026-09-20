"""Compact iOS-style toggle switch for Tkinter (no ttk theme deps)."""

from __future__ import annotations

import tkinter as tk
from typing import Any, Callable


class SwitchButton(tk.Canvas):
    """
    Pill track + sliding knob bound to a ``BooleanVar``.

    Click anywhere on the control to toggle. Optional ``command`` fires after
    the variable changes (same contract as ``ttk.Checkbutton``).
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        variable: tk.BooleanVar,
        command: Callable[[], None] | None = None,
        width: int = 52,
        height: int = 30,
        on_color: str = "#3d9a6a",
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
        # Never use self._w / self._h — those are Tkinter's Tcl widget path attrs.
        self._track_w = int(width)
        self._track_h = int(height)
        self._on = on_color
        self._off = off_color
        self._knob = knob_color
        self._pad = 3
        self._knob_r = max(8, (height // 2) - self._pad)
        self._trace_id: str | None = None

        self.bind("<Button-1>", self._on_click)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._trace_id = self._var.trace_add("write", self._on_var_write)
        self._redraw()

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
        # Flush paint before any heavy command (registry / schtasks / agent).
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
        track = self._on if on else self._off
        tw, th = self._track_w, self._track_h
        # Rounded track via overlapping ovals + rectangle.
        r = th // 2
        self.create_oval(0, 0, th, th, fill=track, outline="")
        self.create_oval(tw - th, 0, tw, th, fill=track, outline="")
        self.create_rectangle(r, 0, tw - r, th, fill=track, outline="")

        cx = (tw - self._pad - self._knob_r) if on else (self._pad + self._knob_r)
        cy = th // 2
        # Soft shadow under knob.
        self.create_oval(
            cx - self._knob_r + 1,
            cy - self._knob_r + 2,
            cx + self._knob_r + 1,
            cy + self._knob_r + 2,
            fill="#000000",
            outline="",
            stipple="gray50",
        )
        self.create_oval(
            cx - self._knob_r,
            cy - self._knob_r,
            cx + self._knob_r,
            cy + self._knob_r,
            fill=self._knob,
            outline="#d1d5db",
            width=1,
        )
