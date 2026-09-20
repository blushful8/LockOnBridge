"""PyInstaller entry point for LockOnBridge.exe"""
from __future__ import annotations

import sys

# Before any Tk / GUI import — sharp text on HiDPI.
from lockon_bridge.dpi import enable_windows_dpi_awareness

enable_windows_dpi_awareness()

# Crash breadcrumbs / faulthandler as early as possible.
from lockon_bridge.crashguard import install_crash_guard

install_crash_guard()

# OCR worker: WinRT / Tesseract in an isolated child — no GUI.
if "--ocr-worker" in sys.argv:
    from lockon_bridge.__main__ import main

    raise SystemExit(main())

from lockon_bridge.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
