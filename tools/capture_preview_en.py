"""Capture English UI previews into docs/preview/."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "preview"
PY = Path(r"D:\CodexData\LockOnBridge\build-python\pythonw.exe")
if not PY.is_file():
    PY = Path(r"D:\CodexData\LockOnBridge\build-python\python.exe")


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


def _window_bbox(hwnd: int) -> tuple[int, int, int, int]:
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def _capture(path: Path) -> None:
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = 0
    for _ in range(60):
        hwnd = _find_hwnd()
        if hwnd:
            left, top, right, bottom = _window_bbox(hwnd)
            if right - left >= 480 and bottom - top >= 600:
                break
        time.sleep(0.25)
    if not hwnd:
        raise RuntimeError("LockOn Bridge window not found")
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.6)
    left, top, right, bottom = _window_bbox(hwnd)
    # Pad slightly so chrome is included; clamp to screen.
    bbox = (left, top, right, bottom)
    img = ImageGrab.grab(bbox=bbox, all_screens=True)
    # Crop empty letterbox if grab overshot.
    if img.width < 400:
        raise RuntimeError(f"capture too narrow: {img.size} bbox={bbox}")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)
    print(f"saved {path} {img.size} bbox={bbox}")


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
