"""Notice over the results screen while the six shots are taken.

The card stays outside the reward crop. Keyboard and mouse are paused until
the shots are stored, then both the card and the pause are released before OCR.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

from .win_graphics import bind_graphics_api

bind_graphics_api()

log = logging.getLogger("lockon_bridge.result_hold")

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

_WM_PAINT = 0x000F
_WM_DESTROY = 0x0002
_WM_ERASEBKGND = 0x0014
_WS_POPUP = 0x80000000
_WS_EX_TOPMOST = 0x00000008
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_LAYERED = 0x00080000
_LWA_ALPHA = 0x2
_SW_SHOWNOACTIVATE = 8
_SWP_NOACTIVATE = 0x0010
_HWND_TOPMOST = ctypes.c_void_p(-1)
_WH_KEYBOARD_LL = 13
_WH_MOUSE_LL = 14
_PM_REMOVE = 1
_QS_ALLINPUT = 0x04FF
_CS_HREDRAW = 0x0002
_CS_VREDRAW = 0x0001
_COLOR_WINDOW = 5
_DT_WORDBREAK = 0x10
_DT_LEFT = 0
_DT_SINGLELINE = 0x20
_DT_END_ELLIPSIS = 0x8000
_DEFAULT_CHARSET = 1
_CLEARTYPE_QUALITY = 5
_FW_SEMIBOLD = 600
_FW_NORMAL = 400

_WINDOWS: dict[int, ResultHold] = {}
_CLASS_ATOM = 0


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class _PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", wintypes.BYTE * 32),
    ]


def _overlap_area(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    left = max(a[0], b[0])
    top = max(a[1], b[1])
    right = min(a[2], b[2])
    bottom = min(a[3], b[3])
    if right <= left or bottom <= top:
        return 0
    return (right - left) * (bottom - top)


def card_top_left(
    client_w: int,
    client_h: int,
    zone: tuple[float, float, float, float],
    card_w: int,
    card_h: int,
    margin: int = 24,
) -> tuple[int, int]:
    """Client-pixel origin for a card that avoids the reward crop when it can."""
    margin = max(8, int(margin))
    card_w = max(80, min(int(card_w), max(80, client_w - 2 * margin)))
    card_h = max(60, min(int(card_h), max(60, client_h - 2 * margin)))
    zx0 = int(zone[0] * client_w)
    zy0 = int(zone[1] * client_h)
    zx1 = int(zone[2] * client_w)
    zy1 = int(zone[3] * client_h)
    zone_px = (zx0, zy0, zx1, zy1)
    candidates = [
        ((client_w - card_w) // 2, client_h - card_h - margin),
        ((client_w - card_w) // 2, margin),
        (client_w - card_w - margin, (client_h - card_h) // 2),
        (margin, (client_h - card_h) // 2),
    ]
    best = candidates[0]
    best_hit = None
    for origin in candidates:
        x = max(margin, min(int(origin[0]), client_w - card_w - margin))
        y = max(margin, min(int(origin[1]), client_h - card_h - margin))
        hit = _overlap_area((x, y, x + card_w, y + card_h), zone_px)
        if best_hit is None or hit < best_hit:
            best = (x, y)
            best_hit = hit
            if hit == 0:
                break
    return best


_API_READY = False
_API_LOCK = threading.Lock()


def _bind_hook_api() -> None:
    global _API_READY
    with _API_LOCK:
        if _API_READY:
            return
        _bind_hook_api_once()
        _API_READY = True


def _bind_hook_api_once() -> None:
    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,
        _HOOKPROC,
        wintypes.HINSTANCE,
        wintypes.DWORD,
    ]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.CallNextHookEx.argtypes = [
        wintypes.HHOOK,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.MsgWaitForMultipleObjects.argtypes = [
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    user32.MsgWaitForMultipleObjects.restype = wintypes.DWORD
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.ClipCursor.argtypes = [ctypes.c_void_p]
    user32.ClipCursor.restype = wintypes.BOOL
    user32.UpdateLayeredWindow.argtypes = [
        wintypes.HWND,
        wintypes.HDC,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.HDC,
        ctypes.c_void_p,
        wintypes.COLORREF,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p


_HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


@_HOOKPROC
def _swallow_input(n_code, w_param, l_param):
    """Drop the event. The key or button is not recorded."""
    if n_code >= 0:
        return 1
    return user32.CallNextHookEx(None, n_code, w_param, l_param)


def _fullscreen_game() -> bool:
    """True when the game window covers its monitor.

    A notice or a desktop copy on top of that window drops exclusive fullscreen
    back to the desktop.
    """
    from .capture import find_war_thunder_hwnd

    hwnd = find_war_thunder_hwnd()
    if not hwnd:
        return False

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    monitor = user32.MonitorFromWindow(wintypes.HWND(hwnd), 2)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    rect = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return False
    mon_w = int(info.rcMonitor.right - info.rcMonitor.left)
    mon_h = int(info.rcMonitor.bottom - info.rcMonitor.top)
    win_w = int(rect.right - rect.left)
    win_h = int(rect.bottom - rect.top)
    return mon_w > 0 and mon_h > 0 and abs(win_w - mon_w) <= 8 and abs(win_h - mon_h) <= 8


def _fit_on_rect(
    x: int, y: int, width: int, height: int, left: int, top: int, right: int, bottom: int
) -> tuple[int, int]:
    """Keep the card inside the game window. A scaled position was falling off-screen."""
    max_x = max(left + 8, right - width - 8)
    max_y = max(top + 8, bottom - height - 8)
    return min(max(x, left + 8), max_x), min(max(y, top + 8), max_y)


def _enable_per_monitor_dpi() -> None:
    """Place the card in real pixels. Otherwise a 150% desktop pushes it off the monitor."""
    try:
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:  # noqa: BLE001
        pass


def _dpi_scale() -> float:
    try:
        dpi = int(user32.GetDpiForSystem())
    except Exception:  # noqa: BLE001
        dpi = 96
    if dpi < 96:
        dpi = 96
    return dpi / 96.0


def _block_input(blocked: bool) -> bool:
    user32.BlockInput.argtypes = [wintypes.BOOL]
    user32.BlockInput.restype = wintypes.BOOL
    try:
        return bool(user32.BlockInput(1 if blocked else 0))
    except Exception:  # noqa: BLE001
        return False


_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


@_WNDPROC
def _wnd_proc(hwnd, msg, wparam, lparam):
    if msg == _WM_ERASEBKGND:
        return 1
    if msg == _WM_PAINT:
        ps = _PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        if hdc:
            user32.EndPaint(hwnd, ctypes.byref(ps))
        return 0
    if msg == _WM_DESTROY:
        _WINDOWS.pop(int(hwnd), None)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _ensure_class() -> None:
    global _CLASS_ATOM
    if _CLASS_ATOM:
        return
    user32.DefWindowProcW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    cls = _WNDCLASSW()
    cls.style = _CS_HREDRAW | _CS_VREDRAW
    cls.lpfnWndProc = ctypes.cast(_wnd_proc, ctypes.c_void_p)
    cls.cbClsExtra = 0
    cls.cbWndExtra = 0
    cls.hInstance = kernel32.GetModuleHandleW(None)
    cls.hIcon = None
    cls.hCursor = user32.LoadCursorW(None, 32512)
    cls.hbrBackground = gdi32.CreateSolidBrush(0x0030241E)
    cls.lpszMenuName = None
    cls.lpszClassName = "LockOnBridgeResultHold"
    atom = user32.RegisterClassW(ctypes.byref(cls))
    if not atom:
        raise OSError("RegisterClassW failed")
    _CLASS_ATOM = int(atom)


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


def _ui_font(size: int, *, bold: bool):
    from pathlib import Path

    from PIL import ImageFont

    filename = "segoeuib.ttf" if bold else "segoeui.ttf"
    path = Path(__import__("os").environ.get("WINDIR", r"C:\Windows")) / "Fonts" / filename
    try:
        return ImageFont.truetype(str(path), size=max(10, int(size)))
    except OSError:
        return ImageFont.load_default()


def _wrap_text(draw, text: str, font, width: int) -> list[str]:
    words = (text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        try:
            length = float(draw.textlength(trial, font=font))
        except Exception:  # noqa: BLE001
            length = float(len(trial) * 8)
        if length <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


class ResultHold:
    """In-game notice while the six shots are taken. Input stays paused."""

    def __init__(self, total: int, *, block_input: bool = True) -> None:
        self.total = max(1, int(total))
        self._block_enabled = bool(block_input)
        self._blocked = False
        self._hooks: list[int] = []
        self._hwnd = 0
        self.title = ""
        self.body = ""
        self.progress = ""
        self._card_w = 560
        self._card_h = 188
        self._origin = (80, 80)
        self._clipped = False
        self._stop_drive = threading.Event()
        self._ready = threading.Event()
        self._dirty = threading.Event()
        self._drive: threading.Thread | None = None
        self._hook_thread: threading.Thread | None = None

    def __enter__(self) -> ResultHold:
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        from .i18n import strings_for
        from .settings import load_settings

        try:
            language = load_settings().language
        except Exception:  # noqa: BLE001
            language = "en"
        text = strings_for(language)
        self.title = text.grab_hold_title
        self.body = text.grab_hold_body
        self.set_progress(1)
        self._stop_drive.clear()
        self._ready.clear()
        self._hook_thread = threading.Thread(
            target=self._hook_loop, name="result-hold-input", daemon=True
        )
        self._hook_thread.start()
        self._drive = threading.Thread(target=self._drive_notice, name="result-hold", daemon=True)
        self._drive.start()
        self._ready.wait(2.0)

    def set_progress(self, index: int) -> None:
        shown = min(max(1, int(index)), self.total)
        current = getattr(self, "_shown", 0)
        if shown < current:
            return
        self._shown = shown
        self.progress = f"{shown} / {self.total}"
        self._dirty.set()

    def close(self) -> None:
        self._stop_drive.set()
        for thread in (self._drive, self._hook_thread):
            if thread is not None and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=2.0)
        self._drive = None
        self._hook_thread = None

    def _hook_loop(self) -> None:
        """Swallow keys and clicks on their own thread so the pause cannot time out."""
        _bind_hook_api()
        user32.PeekMessageW(ctypes.byref(wintypes.MSG()), None, 0, 0, 0)
        self._install_hooks()
        if self._block_enabled:
            self._blocked = _block_input(True)
            if not self._blocked:
                log.warning("BlockInput failed; low-level pause is still on")
        try:
            while not self._stop_drive.is_set():
                self._drain()
                user32.MsgWaitForMultipleObjects(0, None, False, 15, _QS_ALLINPUT)
            self._drain()
        finally:
            if self._blocked:
                _block_input(False)
                self._blocked = False
            self._release_cursor()
            self._remove_hooks()

    def _drive_notice(self) -> None:
        """Keep a composited card above the game for the whole burst."""
        _enable_per_monitor_dpi()
        _bind_hook_api()
        user32.PeekMessageW(ctypes.byref(wintypes.MSG()), None, 0, 0, 0)
        try:
            self._create_window()
            self._present()
            self._clip_cursor()
        except Exception as exc:  # noqa: BLE001
            log.warning("result notice failed: %s", exc)
        self._ready.set()
        try:
            while not self._stop_drive.is_set():
                self._drain()
                if self._dirty.is_set():
                    self._dirty.clear()
                    try:
                        self._present()
                    except Exception as exc:  # noqa: BLE001
                        log.warning("result notice redraw failed: %s", exc)
                self._raise_notice()
                user32.MsgWaitForMultipleObjects(0, None, False, 30, _QS_ALLINPUT)
            self._drain()
        finally:
            self._release_cursor()
            self._destroy()

    def _install_hooks(self) -> None:
        if not self._block_enabled:
            return
        for kind, name in ((_WH_KEYBOARD_LL, "keyboard"), (_WH_MOUSE_LL, "mouse")):
            handle = user32.SetWindowsHookExW(kind, _swallow_input, None, 0)
            if not handle:
                err = int(kernel32.GetLastError())
                log.warning("input pause hook %s failed (%s)", name, err)
                continue
            self._hooks.append(int(handle))
        if len(self._hooks) == 2:
            log.info("input paused for the result shots")

    def _remove_hooks(self) -> None:
        for handle in self._hooks:
            user32.UnhookWindowsHookEx(wintypes.HHOOK(handle))
        self._hooks.clear()

    def _raise_notice(self) -> None:
        hwnd = self._hwnd
        if not hwnd:
            return
        user32.SetWindowPos(
            wintypes.HWND(hwnd),
            _HWND_TOPMOST,
            self._origin[0],
            self._origin[1],
            self._card_w,
            self._card_h,
            _SWP_NOACTIVATE | 0x0040,
        )

    def _drain(self) -> None:
        msg = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, _PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _create_window(self) -> None:
        _ensure_class()
        scale = _dpi_scale()
        self._card_w = int(560 * scale)
        self._card_h = int(188 * scale)
        self._origin = self._screen_origin(self._card_w, self._card_h)
        user32.CreateWindowExW.restype = wintypes.HWND
        # Do not make the game the owner. That drops its frame buffer and
        # every shot comes back empty, so nothing is saved or sent.
        hwnd = user32.CreateWindowExW(
            _WS_EX_TOPMOST | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE | _WS_EX_LAYERED,
            "LockOnBridgeResultHold",
            "",
            _WS_POPUP,
            self._origin[0],
            self._origin[1],
            self._card_w,
            self._card_h,
            None,
            None,
            kernel32.GetModuleHandleW(None),
            None,
        )
        if not hwnd:
            raise OSError(f"CreateWindowExW failed ({int(kernel32.GetLastError())})")
        self._hwnd = int(hwnd)
        _WINDOWS[self._hwnd] = self
        # Keep the notice out of composed screenshots without covering the ROI.
        try:
            affinity = user32.SetWindowDisplayAffinity
            affinity.argtypes = [wintypes.HWND, wintypes.DWORD]
            affinity.restype = wintypes.BOOL
            if not affinity(wintypes.HWND(self._hwnd), 0x11):
                log.debug("notice capture exclusion unavailable")
        except AttributeError:
            pass
        user32.ShowWindow(wintypes.HWND(self._hwnd), _SW_SHOWNOACTIVATE)
        log.info(
            "result notice at %s,%s size %sx%s",
            self._origin[0],
            self._origin[1],
            self._card_w,
            self._card_h,
        )

    def _screen_origin(self, card_w: int, card_h: int) -> tuple[int, int]:
        from .capture import find_war_thunder_hwnd
        from .roi_calib import load_parse_zone

        hwnd = find_war_thunder_hwnd()
        if hwnd:
            rect = wintypes.RECT()
            if user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                width = int(rect.right - rect.left)
                height = int(rect.bottom - rect.top)
                if width >= 400 and height >= 300:
                    zone = load_parse_zone()
                    local = card_top_left(
                        width,
                        height,
                        (zone.left, zone.top, zone.right, zone.bottom),
                        card_w,
                        card_h,
                        margin=int(28 * _dpi_scale()),
                    )
                    point = wintypes.POINT(local[0], local[1])
                    if user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(point)):
                        outer = wintypes.RECT()
                        if user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(outer)):
                            return _fit_on_rect(
                                int(point.x),
                                int(point.y),
                                card_w,
                                card_h,
                                int(outer.left),
                                int(outer.top),
                                int(outer.right),
                                int(outer.bottom),
                            )
                        return int(point.x), int(point.y)
        work = wintypes.RECT()
        if user32.SystemParametersInfoW(48, 0, ctypes.byref(work), 0):
            return (
                int(work.left + (work.right - work.left - card_w) // 2),
                int(work.bottom - card_h - int(48 * _dpi_scale())),
            )
        return 80, 80

    def _clip_cursor(self) -> None:
        if not self._block_enabled or not self._hwnd:
            return
        rect = wintypes.RECT(
            self._origin[0],
            self._origin[1],
            self._origin[0] + self._card_w,
            self._origin[1] + self._card_h,
        )
        if user32.ClipCursor(ctypes.byref(rect)):
            self._clipped = True

    def _release_cursor(self) -> None:
        if not self._clipped:
            return
        user32.ClipCursor(None)
        self._clipped = False

    def _render_card(self):
        from PIL import Image, ImageDraw

        scale = _dpi_scale()
        width, height = self._card_w, self._card_h
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        radius = int(16 * scale)
        draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=(24, 28, 34, 236))
        draw.rectangle((0, 0, max(6, int(6 * scale)), height), fill=(118, 185, 0, 255))
        bold = _ui_font(int(22 * scale), bold=True)
        regular = _ui_font(int(15 * scale), bold=False)
        pad = int(28 * scale)
        title = self.title or " "
        body = self.body or " "
        progress = self.progress or " "
        draw.text((pad, int(22 * scale)), title, font=bold, fill=(244, 241, 234, 255))
        y = int(62 * scale)
        max_w = width - pad * 2
        for line in _wrap_text(draw, body, regular, max_w)[:3]:
            draw.text((pad, y), line, font=regular, fill=(208, 200, 190, 255))
            y += int(22 * scale)
        draw.text((pad, height - int(36 * scale)), progress, font=bold, fill=(118, 185, 0, 255))
        return image

    def _present(self) -> None:
        hwnd = self._hwnd
        if not hwnd:
            return
        import numpy as np

        image = self._render_card()
        width, height = image.size
        pixels = np.array(image)
        alpha = pixels[:, :, 3:4].astype(np.float32) / 255.0
        pixels[:, :, :3] = (pixels[:, :, :3].astype(np.float32) * alpha).astype(np.uint8)
        bgra = np.ascontiguousarray(pixels[:, :, [2, 1, 0, 3]])
        class _BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        header = _BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0
        screen = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(screen)
        bits = ctypes.c_void_p()
        gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC,
            ctypes.c_void_p,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        bitmap = gdi32.CreateDIBSection(
            mem, ctypes.byref(header), 0, ctypes.byref(bits), None, 0
        )
        if not bitmap or not bits:
            if mem:
                gdi32.DeleteDC(mem)
            if screen:
                user32.ReleaseDC(None, screen)
            log.warning("result notice bitmap failed")
            return
        ctypes.memmove(bits, bgra.ctypes.data, width * height * 4)
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteObject.restype = wintypes.BOOL
        old = gdi32.SelectObject(mem, bitmap)
        dst = wintypes.POINT(self._origin[0], self._origin[1])
        src = wintypes.POINT(0, 0)
        size = _SIZE(width, height)
        blend = _BLENDFUNCTION(0, 0, 255, 1)
        ok = user32.UpdateLayeredWindow(
            wintypes.HWND(hwnd),
            screen,
            ctypes.byref(dst),
            ctypes.byref(size),
            mem,
            ctypes.byref(src),
            0,
            ctypes.byref(blend),
            2,
        )
        if not ok:
            log.warning("result notice compose failed (%s)", int(kernel32.GetLastError()))
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)

    def _destroy(self) -> None:
        hwnd = self._hwnd
        self._hwnd = 0
        if not hwnd:
            return
        _WINDOWS.pop(hwnd, None)
        user32.DestroyWindow(wintypes.HWND(hwnd))
