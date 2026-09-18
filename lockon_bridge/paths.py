from __future__ import annotations

import sys
from pathlib import Path


PRODUCT_ID = "LockOnBridge"
PRODUCT_NAME = "LockOn Bridge"
TASK_NAME = "LockOn Bridge"
DEFAULT_PORT = 8112


def data_root() -> Path:
    return Path.home() / "AppData" / "Local" / PRODUCT_ID


def settings_path() -> Path:
    return data_root() / "settings.json"


def log_dir() -> Path:
    return data_root() / "logs"


def log_file() -> Path:
    return log_dir() / "bridge.log"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_executable() -> Path:
    """Path used for autostart / uninstall registration."""
    if is_frozen():
        return Path(sys.executable).resolve()
    return Path(sys.executable).resolve()


def installed_exe_path() -> Path:
    return data_root() / "LockOnBridge.exe"


def desktop_dir() -> Path:
    return Path.home() / "Desktop"


def desktop_shortcut_path() -> Path:
    return desktop_dir() / f"{PRODUCT_NAME}.lnk"
