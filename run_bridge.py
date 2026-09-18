"""PyInstaller entry point for LockOnBridge.exe"""
from __future__ import annotations

import sys

# Before any Tk / GUI import — sharp text on HiDPI.
from lockon_bridge.dpi import enable_windows_dpi_awareness

enable_windows_dpi_awareness()

from lockon_bridge.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
