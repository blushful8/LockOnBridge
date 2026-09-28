"""Win32 helpers for the developer zone editor. No reward-cell crops."""

from __future__ import annotations

import ctypes
import hashlib
import tkinter as tk

# SHA-256 of the developer unlock passphrase (plaintext is not stored in the repo).
_DEV_PASS_SHA256 = "37fc226db1b1805c57cc73b3e24bc121aee16b3065bfe73b6b34fd70acb4fb1c"

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_HWND_TOPMOST = -1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040

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


def _force_hwnd_topmost(hwnd: int, *, activate: bool = False) -> None:
    """Pin HWND above other topmost windows (panel must beat fullscreen overlay)."""
    flags = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_SHOWWINDOW
    if not activate:
        flags |= _SWP_NOACTIVATE
    user32.SetWindowPos(int(hwnd), _HWND_TOPMOST, 0, 0, 0, 0, flags)
