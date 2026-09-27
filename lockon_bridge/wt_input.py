"""
Low-level War Thunder input helpers: focus, mouse click, Ctrl+C, Esc, clipboard.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger("lockon_bridge.wt_input")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
WHEEL_DELTA = 120
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SW_RESTORE = 9
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
VK_CONTROL = 0x11
VK_ESCAPE = 0x1B
VK_C = 0x43
VK_RETURN = 0x0D
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
CF_UNICODETEXT = 13
CF_TEXT = 1
GMEM_MOVEABLE = 0x0002

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


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


@dataclass(frozen=True)
class ClipboardSnapshot:
    """User clipboard text captured before Bridge mutates it."""

    text: str
    had_text: bool


def focus_war_thunder(*, allow_unminimize: bool = False) -> bool:
    """Bring the WT client to the foreground without restoring a minimized window."""
    from .capture import find_war_thunder_hwnd, is_war_thunder_foreground

    hwnd = find_war_thunder_hwnd()
    if hwnd is None:
        return False
    try:
        if user32.IsIconic(wintypes.HWND(hwnd)):
            if not allow_unminimize:
                log.info("focus skipped: War Thunder is minimized")
                return False
            # Accidental minimize (rare). Prefer a quiet restore — no TOPMOST dance.
            log.info("WT minimized — soft restore (messages capture)")
            user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
            # Client size / GDI need a beat after restore (0.05 was too short).
            time.sleep(0.45)
    except Exception:  # noqa: BLE001
        pass
    if is_war_thunder_foreground():
        return True

    # AttachThreadInput lets SetForegroundWindow succeed when Bridge just withdrew.
    fg = int(user32.GetForegroundWindow() or 0)
    our_tid = int(kernel32.GetCurrentThreadId())
    fg_tid = 0
    if fg:
        _pid = wintypes.DWORD()
        fg_tid = int(
            user32.GetWindowThreadProcessId(wintypes.HWND(fg), ctypes.byref(_pid))
        )
    attached = False
    try:
        if fg and fg_tid and fg_tid != our_tid:
            attached = bool(user32.AttachThreadInput(our_tid, fg_tid, True))
        try:
            user32.BringWindowToTop(wintypes.HWND(hwnd))
        except Exception:  # noqa: BLE001
            pass
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
    except Exception as exc:  # noqa: BLE001
        log.debug("SetForegroundWindow failed: %s", exc)
        return False
    finally:
        if attached:
            try:
                user32.AttachThreadInput(our_tid, fg_tid, False)
            except Exception:  # noqa: BLE001
                pass
    time.sleep(0.05)
    return is_war_thunder_foreground()


def get_client_size(hwnd: int) -> tuple[int, int] | None:
    rect = wintypes.RECT()
    if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    w = int(rect.right - rect.left)
    h = int(rect.bottom - rect.top)
    if w < 100 or h < 100:
        return None
    return w, h


class ExclusiveWtSession:
    """
    Focus-only hangar capture — no TOPMOST / ClipCursor / taskbar tricks.

    TOPMOST and DXGI/mss both yank exclusive-fullscreen WT onto the desktop.
    Clicks use real client coords; we only re-assert foreground around gestures.
    """

    def __init__(self, hwnd: int) -> None:
        self.hwnd = int(hwnd)

    def __enter__(self) -> ExclusiveWtSession:
        focus_war_thunder(allow_unminimize=True)
        log.info("soft WT session on hwnd=%s (focus-only)", self.hwnd)
        return self

    def __exit__(self, *_exc: object) -> None:
        try:
            focus_war_thunder(allow_unminimize=False)
        except Exception:  # noqa: BLE001
            pass
        log.info("soft WT session released")


def _send_inputs(inputs: list[INPUT]) -> None:
    n = len(inputs)
    if n <= 0:
        return
    arr = (INPUT * n)(*inputs)
    sent = user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(INPUT))
    if sent != n:
        log.debug("SendInput sent %s/%s", sent, n)


def _key(vk: int, *, up: bool = False) -> INPUT:
    scan = int(user32.MapVirtualKeyW(int(vk), MAPVK_VK_TO_VSC) or 0)
    flags = KEYEVENTF_SCANCODE if scan else 0
    if up:
        flags |= KEYEVENTF_KEYUP
    return INPUT(
        type=INPUT_KEYBOARD,
        union=_INPUTUNION(
            ki=KEYBDINPUT(
                wVk=int(vk),
                wScan=scan,
                dwFlags=flags if scan else (KEYEVENTF_KEYUP if up else 0),
                time=0,
                dwExtraInfo=ULONG_PTR(0),
            )
        ),
    )


def press_ctrl_c(*, hwnd: int | None = None) -> None:
    """
    Simultaneous Ctrl+C chord for WT Messages copy.

    Both keys go down together, stay down briefly, then release together.
    Optionally AttachThreadInput to the WT thread first (caller may already
    have focus); Attach resets key state so it must happen *before* the chord.
    """
    VK_LCONTROL = 0xA2
    attached = False
    our_tid = 0
    wt_tid = 0
    if hwnd is not None:
        our_tid = int(kernel32.GetCurrentThreadId())
        pid = wintypes.DWORD()
        wt_tid = int(
            user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        )
        if wt_tid and wt_tid != our_tid:
            attached = bool(user32.AttachThreadInput(our_tid, wt_tid, True))
        try:
            user32.SetForegroundWindow(wintypes.HWND(hwnd))
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.03)

    try:
        # Drop any stuck modifiers that would poison the chord.
        for vk in (VK_CONTROL, VK_LCONTROL, 0xA3, 0x10, 0x12):
            if user32.GetAsyncKeyState(vk) & 0x8000:
                _send_inputs([_key(vk, up=True)])

        def _vk_only(vk: int, *, up: bool = False) -> INPUT:
            return INPUT(
                type=INPUT_KEYBOARD,
                union=_INPUTUNION(
                    ki=KEYBDINPUT(
                        wVk=int(vk),
                        wScan=0,
                        dwFlags=KEYEVENTF_KEYUP if up else 0,
                        time=0,
                        dwExtraInfo=ULONG_PTR(0),
                    )
                ),
            )

        # Both down at once → hold → both up (true chord, not staggered taps).
        _send_inputs([_vk_only(VK_LCONTROL), _vk_only(VK_C)])
        time.sleep(0.10)
        _send_inputs([_vk_only(VK_C, up=True), _vk_only(VK_LCONTROL, up=True)])
    finally:
        if attached:
            try:
                user32.AttachThreadInput(our_tid, wt_tid, False)
            except Exception:  # noqa: BLE001
                pass


def press_escape() -> None:
    """Esc with a short hold — zero-width taps are ignored by Dagor UI."""
    _send_inputs([_key(VK_ESCAPE)])
    time.sleep(0.08)
    _send_inputs([_key(VK_ESCAPE, up=True)])


def press_vk(vk: int, *, hold_sec: float = 0.0) -> None:
    """Tap a virtual-key (down → optional hold → up) via SendInput."""
    _send_inputs([_key(int(vk))])
    if hold_sec > 0:
        time.sleep(hold_sec)
    _send_inputs([_key(int(vk), up=True)])


def press_arrow_down(*, settle_sec: float = 0.12) -> None:
    press_vk(VK_DOWN)
    if settle_sec > 0:
        time.sleep(settle_sec)


def press_arrow_up(*, settle_sec: float = 0.12) -> None:
    press_vk(VK_UP)
    if settle_sec > 0:
        time.sleep(settle_sec)


def press_enter(*, settle_sec: float = 0.15) -> None:
    press_vk(VK_RETURN)
    if settle_sec > 0:
        time.sleep(settle_sec)


def get_cursor_pos() -> tuple[int, int] | None:
    pt = POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return None
    return int(pt.x), int(pt.y)


def set_cursor_pos(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


def click_screen_xy(x: int, y: int, *, hold_sec: float = 0.0) -> None:
    """Absolute screen click via SendInput across the virtual desktop."""
    vx = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    vy = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    vw = max(1, int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)))
    vh = max(1, int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)))
    ax = int(round((int(x) - vx) * 65535 / max(1, vw - 1)))
    ay = int(round((int(y) - vy) * 65535 / max(1, vh - 1)))
    abs_flags = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK

    def _mi(flags: int, *, data: int = 0) -> INPUT:
        return INPUT(
            type=INPUT_MOUSE,
            union=_INPUTUNION(
                mi=MOUSEINPUT(
                    dx=ax,
                    dy=ay,
                    mouseData=int(data),
                    dwFlags=flags,
                    time=0,
                    dwExtraInfo=ULONG_PTR(0),
                )
            ),
        )

    _send_inputs([_mi(MOUSEEVENTF_MOVE | abs_flags), _mi(MOUSEEVENTF_LEFTDOWN | abs_flags)])
    if hold_sec > 0:
        time.sleep(hold_sec)
    _send_inputs([_mi(MOUSEEVENTF_LEFTUP | abs_flags)])


def move_cursor_screen_xy(x: int, y: int) -> None:
    """Move cursor to absolute screen pixel (virtual-desktop aware)."""
    vx = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    vy = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    vw = max(1, int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)))
    vh = max(1, int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)))
    ax = int(round((int(x) - vx) * 65535 / max(1, vw - 1)))
    ay = int(round((int(y) - vy) * 65535 / max(1, vh - 1)))
    flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    _send_inputs(
        [
            INPUT(
                type=INPUT_MOUSE,
                union=_INPUTUNION(
                    mi=MOUSEINPUT(
                        dx=ax,
                        dy=ay,
                        mouseData=0,
                        dwFlags=flags,
                        time=0,
                        dwExtraInfo=ULONG_PTR(0),
                    )
                ),
            )
        ]
    )


def scroll_wheel(*, notches: int = -4, settle_sec: float = 0.12) -> None:
    """
    Vertical mouse wheel at the current cursor position.

    Negative ``notches`` scrolls down (reveal Messages footer Total / Session).
    """
    delta = int(notches) * WHEEL_DELTA
    # mouseData is DWORD; negative wheel needs two's complement in 32-bit.
    data = ctypes.c_int32(delta).value & 0xFFFFFFFF
    _send_inputs(
        [
            INPUT(
                type=INPUT_MOUSE,
                union=_INPUTUNION(
                    mi=MOUSEINPUT(
                        dx=0,
                        dy=0,
                        mouseData=data,
                        dwFlags=MOUSEEVENTF_WHEEL,
                        time=0,
                        dwExtraInfo=ULONG_PTR(0),
                    )
                ),
            )
        ]
    )
    if settle_sec > 0:
        time.sleep(settle_sec)


def client_to_screen(hwnd: int, x: int, y: int) -> tuple[int, int] | None:
    pt = POINT(x, y)
    if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(pt)):
        return None
    return int(pt.x), int(pt.y)


def _clamp_client_xy(hwnd: int, x: int, y: int, *, margin: int = 2) -> tuple[int, int]:
    """Keep clicks inside the client rect."""
    size = get_client_size(hwnd)
    if size is None:
        return int(x), int(y)
    w, h = size
    m = max(0, int(margin))
    cx = max(m, min(w - m - 1, int(x)))
    cy = max(m, min(h - m - 1, int(y)))
    return cx, cy


def click_client_xy(hwnd: int, x: int, y: int, *, hold_sec: float = 0.12) -> bool:
    """Click a WT client-pixel point. Brief hold so hangar buttons register."""
    # Prefer focus without restore — SW_RESTORE flashes exclusive fullscreen.
    if not focus_war_thunder(allow_unminimize=False):
        if not focus_war_thunder(allow_unminimize=True):
            return False
    cx, cy = _clamp_client_xy(hwnd, int(x), int(y))
    screen = client_to_screen(hwnd, cx, cy)
    if screen is None:
        return False
    click_screen_xy(screen[0], screen[1], hold_sec=hold_sec)
    time.sleep(0.04)
    return True


def click_client_xy_restore_cursor(hwnd: int, x: int, y: int) -> bool:
    """
    Click a WT client-pixel point, then put the cursor back where it was.

    Brief pause before restore so the game can process the click.
    """
    prev = get_cursor_pos()
    try:
        if not click_client_xy(hwnd, x, y):
            return False
        time.sleep(0.05)
    finally:
        if prev is not None:
            try:
                set_cursor_pos(prev[0], prev[1])
            except Exception:  # noqa: BLE001
                pass
    return True


def get_clipboard_text() -> str:
    """Read Unicode clipboard text; retry when WT briefly holds the clipboard."""
    for _ in range(8):
        if not user32.OpenClipboard(None):
            time.sleep(0.04)
            continue
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
    return ""


def clear_clipboard() -> bool:
    """Empty the clipboard. Returns True on success."""
    if not user32.OpenClipboard(None):
        return False
    try:
        return bool(user32.EmptyClipboard())
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    """Replace clipboard with Unicode text (or empty)."""
    if not text:
        return clear_clipboard()
    data = (text.replace("\0", "") + "\0").encode("utf-16-le")
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        return False
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        kernel32.GlobalFree(handle)
        return False
    try:
        ctypes.memmove(ptr, data, len(data))
    finally:
        kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(handle)
        return False
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    finally:
        user32.CloseClipboard()


def snapshot_clipboard() -> ClipboardSnapshot:
    text = get_clipboard_text()
    return ClipboardSnapshot(text=text, had_text=bool(text))


def restore_clipboard(snap: ClipboardSnapshot | None) -> None:
    if snap is None:
        return
    try:
        if snap.had_text:
            set_clipboard_text(snap.text)
        else:
            clear_clipboard()
    except Exception as exc:  # noqa: BLE001
        log.debug("restore_clipboard failed: %s", exc)


def poll_clipboard_after_copy(
    *,
    accept,
    timeout_sec: float = 1.4,
    interval_sec: float = 0.08,
    send_copy_each_iter: bool = True,
    reject_text: str | None = None,
    hwnd: int | None = None,
) -> tuple[str | None, object | None]:
    """
    Simultaneous Ctrl+C chord(s), then poll clipboard until ``accept(text)``.

    Does not EmptyClipboard. After injected attempts, keeps polling so a
    physical Ctrl+C during the window can still succeed (WT often ignores
    synthetic keys entirely).
    """
    deadline = time.monotonic() + max(0.1, timeout_sec)
    last_text = ""
    baseline = reject_text if reject_text is not None else None
    # A few simultaneous chords up front, then listen only.
    chords = 3 if send_copy_each_iter else 0
    for i in range(chords):
        press_ctrl_c(hwnd=hwnd)
        time.sleep(0.28)
        text = get_clipboard_text()
        if text and text != last_text:
            last_text = text
        if text and (baseline is None or text != baseline):
            try:
                valued = accept(text)
            except Exception:  # noqa: BLE001
                valued = None
            if valued:
                return text, valued
        if time.monotonic() >= deadline:
            break
        if i + 1 < chords:
            time.sleep(0.12)

    while time.monotonic() < deadline:
        text = get_clipboard_text()
        if text and text != last_text:
            last_text = text
        if text and (baseline is None or text != baseline):
            try:
                valued = accept(text)
            except Exception:  # noqa: BLE001
                valued = None
            if valued:
                return text, valued
        time.sleep(interval_sec)
    return (last_text or None), None
