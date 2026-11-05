"""Capture English UI previews into docs/preview/ (clean window only, no desktop)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "preview"
PY = Path(r"D:\CodexData\LockOnBridge\build-python\pythonw.exe")
if not PY.is_file():
    PY = Path(r"D:\CodexData\LockOnBridge\build-python\python.exe")

SRCCOPY = 0x00CC0020


def _enable_dpi() -> None:
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _settings_path() -> Path:
    sys.path.insert(0, str(ROOT))
    from lockon_bridge.paths import settings_path

    return settings_path()


def _load_settings() -> dict:
    path = _settings_path()
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_settings(data: dict) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _find_hwnd() -> int:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = ctypes.c_void_p(0)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value.startswith("LockOn Bridge"):
            found.value = hwnd
            return False
        return True

    user32.EnumWindows(enum_proc, 0)
    return int(found.value or 0)


def _client_screen_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Client area in screen coordinates (excludes the OS title bar / borders)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    pt_tl = wintypes.POINT(rect.left, rect.top)
    pt_br = wintypes.POINT(rect.right, rect.bottom)
    user32.ClientToScreen(hwnd, ctypes.byref(pt_tl))
    user32.ClientToScreen(hwnd, ctypes.byref(pt_br))
    return pt_tl.x, pt_tl.y, pt_br.x, pt_br.y


def _capture_client_bitmap(hwnd: int) -> Image.Image:
    """BitBlt only the client area — no desktop shadow fringe, no white title bar."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    left, top, right, bottom = _client_screen_rect(hwnd)
    width = max(1, right - left)
    height = max(1, bottom - top)

    screen_dc = user32.GetDC(0)
    if not screen_dc:
        raise RuntimeError("GetDC failed")
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bmp = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    old = gdi32.SelectObject(mem_dc, bmp)
    gdi32.BitBlt(mem_dc, 0, 0, width, height, screen_dc, left, top, SRCCOPY)

    class BITMAPINFOHEADER(ctypes.Structure):
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

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height  # top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0

    buf_size = width * height * 4
    buf = (ctypes.c_ubyte * buf_size)()
    gdi32.GetDIBits(mem_dc, bmp, 0, height, ctypes.byref(buf), ctypes.byref(bmi), 0)

    gdi32.SelectObject(mem_dc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(0, screen_dc)

    image = Image.frombuffer("RGB", (width, height), bytes(buf), "raw", "BGRX", 0, 1)
    return image.copy()


def _trim_black_margins(image: Image.Image, *, thr: int = 8) -> Image.Image:
    """Remove PrintWindow/DWM black padding outside the opaque window frame."""
    px = image.load()
    w, h = image.size

    def dark(p: tuple[int, int, int]) -> bool:
        return p[0] <= thr and p[1] <= thr and p[2] <= thr

    left = 0
    while left < w and all(dark(px[left, y]) for y in range(h)):
        left += 1
    right = w - 1
    while right > left and all(dark(px[right, y]) for y in range(h)):
        right -= 1
    top = 0
    while top < h and all(dark(px[x, top]) for x in range(left, right + 1)):
        top += 1
    bottom = h - 1
    while bottom > top and all(dark(px[x, bottom]) for x in range(left, right + 1)):
        bottom -= 1
    return image.crop((left, top, right + 1, bottom + 1))


def _capture(path: Path) -> None:
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = 0
    for _ in range(60):
        hwnd = _find_hwnd()
        if hwnd:
            left, top, right, bottom = _client_screen_rect(hwnd)
            if right - left >= 480 and bottom - top >= 560:
                break
        time.sleep(0.25)
    if not hwnd:
        raise RuntimeError("LockOn Bridge window not found")

    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    # Move fully on-screen so BitBlt of the client area cannot clip into desktop.
    # Place near top-left so the client BitBlt cannot clip into desktop chrome.
    user32.SetWindowPos(hwnd, 0, 40, 40, 0, 0, 0x0001 | 0x0004)  # NOSIZE | NOZORDER
    time.sleep(0.5)

    image = _trim_black_margins(_capture_client_bitmap(hwnd))
    if image.width < 400:
        raise RuntimeError(f"capture too narrow: {image.size}")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", optimize=True)
    print(f"saved {path} {image.size}")


def _launch() -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.Popen(
        [str(PY), "-m", "lockon_bridge", "--ui"],
        cwd=str(ROOT),
        env=env,
    )


def _kill(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    subprocess.run(
        ["taskkill", "/F", "/IM", "LockOnBridge.exe"],
        capture_output=True,
        check=False,
    )


def main() -> int:
    _enable_dpi()
    OUT.mkdir(parents=True, exist_ok=True)
    original = _load_settings()
    patched = dict(original)
    patched["language"] = "en"
    patched["wt_ui_language"] = "en"
    shots = [
        ("main-en.png", True),
        ("main-off-en.png", False),
    ]
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "LockOnBridge.exe"],
            capture_output=True,
            check=False,
        )
        time.sleep(0.8)
        for name, enabled in shots:
            patched["enabled"] = enabled
            _save_settings(patched)
            proc = _launch()
            try:
                time.sleep(3.5)
                _capture(OUT / name)
            finally:
                _kill(proc)
                time.sleep(1.2)
    finally:
        _save_settings(original)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
