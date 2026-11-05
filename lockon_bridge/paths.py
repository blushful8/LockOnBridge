from __future__ import annotations

import os
import sys
import winreg
from pathlib import Path


PRODUCT_ID = "LockOnBridge"
PRODUCT_NAME = "LockOn Bridge"
TASK_NAME = "LockOn Bridge"
DEFAULT_PORT = 8112

_REG_KEY = rf"Software\{PRODUCT_ID}"
_REG_VALUE = "DataRoot"

_data_root_cache: Path | None = None


def default_local_appdata_root() -> Path:
    """Legacy / fallback root under the Windows profile (usually on C:)."""
    return Path.home() / "AppData" / "Local" / PRODUCT_ID


def suggest_install_root() -> Path:
    """Prefer a non-system drive so the ~100MB+ app tree stays off C:."""
    for letter in ("D", "E", "F", "G"):
        drive = Path(f"{letter}:/")
        try:
            if drive.is_dir():
                return drive / PRODUCT_ID
        except OSError:
            continue
    return default_local_appdata_root()


def _read_registry_data_root() -> Path | None:
    if sys.platform != "win32":
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _REG_VALUE)
    except OSError:
        return None
    text = str(value or "").strip()
    if not text:
        return None
    return Path(text)


def write_data_root(path: Path) -> Path:
    """Persist the install/data root (tiny HKCU pointer — not the bulky tree)."""
    global _data_root_cache
    resolved = path.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as key:
                winreg.SetValueEx(key, _REG_VALUE, 0, winreg.REG_SZ, str(resolved))
        except OSError:
            pass
    _data_root_cache = resolved
    return resolved


def clear_data_root_registry() -> None:
    global _data_root_cache
    _data_root_cache = None
    if sys.platform != "win32":
        return
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _REG_KEY)
    except FileNotFoundError:
        pass
    except OSError:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, _REG_VALUE)
        except OSError:
            pass


def clear_data_root_cache() -> None:
    global _data_root_cache
    _data_root_cache = None


def running_install_layout_root() -> Path | None:
    """If this exe already lives in ``<root>/app/LockOnBridge.exe``, return ``<root>``."""
    if not is_frozen():
        return None
    try:
        parent = app_executable().parent
    except OSError:
        return None
    if parent.name.lower() != "app":
        return None
    return parent.parent


def legacy_install_present(root: Path | None = None) -> bool:
    """True when an older LocalAppData (or given) tree looks already installed."""
    base = root or default_local_appdata_root()
    return (base / "app" / "LockOnBridge.exe").is_file() or (base / "settings.json").is_file()


def data_root_configured() -> bool:
    if os.environ.get("LOCKON_BRIDGE_DATA_DIR"):
        return True
    if _read_registry_data_root() is not None:
        return True
    if running_install_layout_root() is not None:
        return True
    return legacy_install_present()


def _resolve_data_root() -> Path:
    override = os.environ.get("LOCKON_BRIDGE_DATA_DIR")
    if override:
        return Path(override).expanduser()

    configured = _read_registry_data_root()
    if configured is not None:
        return configured

    layout = running_install_layout_root()
    if layout is not None:
        return layout

    legacy = default_local_appdata_root()
    if legacy_install_present(legacy):
        return legacy

    # Unconfigured first launch — callers should run ensure_data_root_ready first.
    return legacy


def data_root() -> Path:
    global _data_root_cache
    if _data_root_cache is not None:
        return _data_root_cache
    resolved = _resolve_data_root()
    _data_root_cache = resolved
    return resolved


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


def captures_dir() -> Path:
    """Timestamped OCR frames kept for offline ROI pair tuning."""
    return data_root() / "captures"


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


def cleanup_obsolete_localappdata_junk() -> None:
    """Remove leftover folders current builds never use (frees C: for old installs)."""
    legacy = default_local_appdata_root()
    for name in ("ocr-venv", "app-before-ocr-fix"):
        path = legacy / name
        if not path.is_dir():
            continue
        try:
            import shutil

            shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass
