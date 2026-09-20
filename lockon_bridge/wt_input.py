"""
Low-level War Thunder input helpers: focus, mouse click, Ctrl+C, Esc, clipboard.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

log = logging.getLogger("lockon_bridge.wt_input")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
VK_CONTROL = 0x11
VK_ESCAPE = 0x1B
VK_C = 0x43
CF_UNICODETEXT = 13
CF_TEXT = 1

ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUTUNION),
    ]


def focus_war_thunder() -> bool:
    """Bring the WT client to the foreground without restoring a minimized window."""
    from .capture import find_war_thunder_hwnd, is_war_thunder_foreground

    hwnd = find_war_thunder_hwnd()
    if hwnd is None:
        return False
    try:
        if user32.IsIconic(wintypes.HWND(hwnd)):
            log.info("focus skipped: War Thunder is minimized")
            return False
    except Exception:  # noqa: BLE001
        pass
    if is_war_thunder_foreground():
        return True
    try:
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
    except Exception as exc:  # noqa: BLE001
        log.debug("SetForegroundWindow failed: %s", exc)
        return False
    time.sleep(0.05)
    return is_war_thunder_foreground()


def _send_inputs(inputs: list[INPUT]) -> None:
    n = len(inputs)
    if n <= 0:
        return
    arr = (INPUT * n)(*inputs)
    sent = user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(INPUT))
    if sent != n:
        log.debug("SendInput sent %s/%s", sent, n)


def _key(vk: int, *, up: bool = False) -> INPUT:
    flags = KEYEVENTF_KEYUP if up else 0
    return INPUT(
        type=INPUT_KEYBOARD,
        union=_INPUTUNION(
            ki=KEYBDINPUT(
                wVk=vk,
                wScan=0,
                dwFlags=flags,
                time=0,
                dwExtraInfo=ULONG_PTR(0),
            )
        ),
    )


def press_ctrl_c() -> None:
    _send_inputs(
        [
            _key(VK_CONTROL),
            _key(VK_C),
            _key(VK_C, up=True),
            _key(VK_CONTROL, up=True),
        ]
    )


def press_escape() -> None:
    _send_inputs([_key(VK_ESCAPE), _key(VK_ESCAPE, up=True)])


def click_screen_xy(x: int, y: int) -> None:
    """Absolute screen click via SendInput (0..65535 normalized)."""
    sx = max(1, int(user32.GetSystemMetrics(0)))  # SM_CXSCREEN
    sy = max(1, int(user32.GetSystemMetrics(1)))  # SM_CYSCREEN
    ax = int(round(x * 65535 / max(1, sx - 1)))
    ay = int(round(y * 65535 / max(1, sy - 1)))
    move = INPUT(
        type=INPUT_MOUSE,
        union=_INPUTUNION(
            mi=MOUSEINPUT(
                dx=ax,
                dy=ay,
                mouseData=0,
                dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
                time=0,
                dwExtraInfo=ULONG_PTR(0),
            )
        ),
    )
    down = INPUT(
        type=INPUT_MOUSE,
        union=_INPUTUNION(
            mi=MOUSEINPUT(
                dx=ax,
                dy=ay,
                mouseData=0,
                dwFlags=MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE,
                time=0,
                dwExtraInfo=ULONG_PTR(0),
            )
        ),
    )
    up = INPUT(
        type=INPUT_MOUSE,
        union=_INPUTUNION(
            mi=MOUSEINPUT(
                dx=ax,
                dy=ay,
                mouseData=0,
                dwFlags=MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE,
                time=0,
                dwExtraInfo=ULONG_PTR(0),
            )
        ),
    )
    _send_inputs([move, down, up])


def client_to_screen(hwnd: int, x: int, y: int) -> tuple[int, int] | None:
    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    pt = POINT(x, y)
    if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(pt)):
        return None
    return int(pt.x), int(pt.y)


def get_clipboard_text() -> str:
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            handle = user32.GetClipboardData(CF_TEXT)
            if not handle:
                return ""
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return ""
            try:
                return ctypes.string_at(ptr).decode("utf-8", errors="replace")
            finally:
                kernel32.GlobalUnlock(handle)
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clear_clipboard() -> bool:
    """Empty the clipboard. Returns True on success."""
    if not user32.OpenClipboard(None):
        return False
    try:
        return bool(user32.EmptyClipboard())
    finally:
        user32.CloseClipboard()


def poll_clipboard_after_copy(
    *,
    accept,
    timeout_sec: float = 1.4,
    interval_sec: float = 0.08,
    send_copy_each_iter: bool = True,
) -> tuple[str | None, object | None]:
    """
    Repeatedly Ctrl+C and read clipboard until ``accept(text)`` returns a truthy
    value (the parsed object), or timeout.

    Returns ``(raw_text, accepted_value)`` or ``(None, None)``.
    """
    deadline = time.monotonic() + max(0.1, timeout_sec)
    last_text = ""
    while time.monotonic() < deadline:
        if send_copy_each_iter:
            press_ctrl_c()
            time.sleep(0.02)
        text = get_clipboard_text()
        if text and text != last_text:
            last_text = text
        if text:
            try:
                valued = accept(text)
            except Exception:  # noqa: BLE001
                valued = None
            if valued:
                return text, valued
        time.sleep(interval_sec)
    return None, None
