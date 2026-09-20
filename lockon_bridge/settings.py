from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from typing import Any

from .i18n import detect_system_language
from .paths import DEFAULT_PORT, settings_path


@dataclass
class BridgeSettings:
    enabled: bool = False
    port: int = DEFAULT_PORT
    # How often to look for aces.exe while the game is closed (keep this slow).
    idle_poll_sec: float = 120.0
    bind: str = "0.0.0.0"
    game_host: str = "127.0.0.1"
    game_port: int = 8111
    language: str = "en"
    # War Thunder UI language for OCR pack advice (en/uk/ru/…).
    wt_ui_language: str = "uk"
    # auto | windows | tesseract
    ocr_backend: str = "auto"
    # User already answered the OCR pack setup prompt.
    ocr_setup_done: bool = False
    # Phone preference: True → OCR the with-premium column (what a premium account banks).
    has_premium_account: bool = False
    # Developer: live transparent ROI overlay + annotated dumps.
    debug_show_rois: bool = False
    # After battle: open Messages → Ctrl+C → parse (OCR is fallback).
    use_clipboard_results: bool = True
    # Launch Bridge at Windows logon (scheduled task). Independent of agent run.
    autostart_with_windows: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BridgeSettings":
        port = int(raw.get("port", DEFAULT_PORT))
        if port < 1 or port > 65535:
            port = DEFAULT_PORT
        idle = float(raw.get("idle_poll_sec", 120.0))
        # Soft-migrate previous shipping default (30s) → 2 minutes.
        if abs(idle - 30.0) < 0.01:
            idle = 120.0
        if idle < 15.0:
            idle = 15.0
        if idle > 600.0:
            idle = 600.0
        language = str(raw.get("language") or "").strip().lower()
        if language not in ("en", "uk"):
            language = detect_system_language()
        wt_ui = str(raw.get("wt_ui_language") or language or "uk").strip().lower()
        if len(wt_ui) > 8:
            wt_ui = wt_ui[:8]
        backend = str(raw.get("ocr_backend") or "auto").strip().lower()
        if backend not in ("auto", "windows", "tesseract"):
            backend = "auto"
        # Default True when key missing (previous builds always registered on enable).
        if "autostart_with_windows" in raw:
            autostart = bool(raw.get("autostart_with_windows"))
        else:
            autostart = True
        return cls(
            enabled=bool(raw.get("enabled", False)),
            port=port,
            idle_poll_sec=idle,
            bind=str(raw.get("bind", "0.0.0.0")),
            game_host=str(raw.get("game_host", "127.0.0.1")),
            game_port=int(raw.get("game_port", 8111)),
            language=language,
            wt_ui_language=wt_ui or "uk",
            ocr_backend=backend,
            ocr_setup_done=bool(raw.get("ocr_setup_done", False)),
            has_premium_account=bool(raw.get("has_premium_account", False)),
            debug_show_rois=bool(raw.get("debug_show_rois", False)),
            use_clipboard_results=bool(raw.get("use_clipboard_results", True)),
            autostart_with_windows=autostart,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_lock = threading.RLock()


def load_settings() -> BridgeSettings:
    path = settings_path()
    with _lock:
        if not path.is_file():
            return BridgeSettings(language=detect_system_language())
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return BridgeSettings(language=detect_system_language())
            updated = BridgeSettings.from_dict(raw)
            # Persist soft-migration (e.g. idle 30s → 120s) so next boots stay light.
            if updated.to_dict().get("idle_poll_sec") != raw.get("idle_poll_sec"):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(updated.to_dict(), indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
            return updated
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return BridgeSettings(language=detect_system_language())


def save_settings(settings: BridgeSettings) -> None:
    path = settings_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(settings.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def update_settings(**kwargs: Any) -> BridgeSettings:
    with _lock:
        current = load_settings()
        data = current.to_dict()
        data.update(kwargs)
        updated = BridgeSettings.from_dict(data)
        save_settings(updated)
        return updated
