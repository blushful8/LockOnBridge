from __future__ import annotations

import sys
from pathlib import Path


PRODUCT_ID = "LockOnBridge"
PRODUCT_NAME = "LockOn Bridge"
TASK_NAME = "LockOn Bridge"
DEFAULT_PORT = 8112


def data_root() -> Path:
    return Path.home() / "AppData" / "Local" / PRODUCT_ID


def app_install_dir() -> Path:
    """Stable onedir install location (exe + _internal)."""
    return data_root() / "app"


def settings_path() -> Path:
    return data_root() / "settings.json"


def log_dir() -> Path:
    return data_root() / "logs"


def log_file() -> Path:
    return log_dir() / "bridge.log"


def error_parse_image_path() -> Path:
    """Single overwriteable frame when all calibrated ROI pairs fail OCR."""
    return data_root() / "error_parse.png"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_executable() -> Path:
    """Path of the currently running binary."""
    return Path(sys.executable).resolve()


def installed_exe_path() -> Path:
    return app_install_dir() / "LockOnBridge.exe"


def installed_uninstall_path() -> Path:
    return app_install_dir() / "uninstall.exe"


def desktop_dir() -> Path:
    return Path.home() / "Desktop"


def desktop_shortcut_path() -> Path:
    return desktop_dir() / f"{PRODUCT_NAME}.lnk"
