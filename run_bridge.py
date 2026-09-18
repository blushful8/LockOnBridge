"""PyInstaller entry point for LockOnBridge.exe"""
from __future__ import annotations

import sys

from lockon_bridge.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
