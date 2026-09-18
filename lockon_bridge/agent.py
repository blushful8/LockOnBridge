from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Callable, Optional

from .process_watch import is_war_thunder_running
from .runtime import BridgeRuntime, RuntimeConfig
from .settings import BridgeSettings

log = logging.getLogger("lockon_bridge")


class AgentStatus(str, Enum):
    DISABLED = "disabled"
    IDLE = "idle"
    ACTIVE = "active"
    STOPPING = "stopping"


StatusCallback = Callable[[AgentStatus, str], None]


class BridgeAgent:
    """
    Background controller: when enabled, waits for War Thunder, then runs OCR/HTTP.
    When disabled, no threads and no sockets.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._runtime: Optional[BridgeRuntime] = None
        self._status = AgentStatus.DISABLED
        self._detail = "Off"
        self._on_status: Optional[StatusCallback] = None
        self._settings = BridgeSettings()

    def set_status_callback(self, callback: StatusCallback | None) -> None:
        self._on_status = callback

    @property
    def status(self) -> AgentStatus:
        return self._status

    @property
    def detail(self) -> str:
        return self._detail

    def _set_status(self, status: AgentStatus, detail: str) -> None:
        self._status = status
        self._detail = detail
        cb = self._on_status
        if cb is not None:
            try:
                cb(status, detail)
            except Exception:  # noqa: BLE001
                pass

    def start(self, settings: BridgeSettings) -> None:
        with self._lock:
            self._settings = settings
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="lockon-agent",
                daemon=True,
            )
            self._thread.start()

    def stop(self, join: bool = True) -> None:
        with self._lock:
            self._stop.set()
            runtime = self._runtime
            thread = self._thread
        if runtime is not None:
            runtime.stop()
        self._set_status(AgentStatus.STOPPING, "Stopping…")
        if join and thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        with self._lock:
            self._runtime = None
            self._thread = None
        self._set_status(AgentStatus.DISABLED, "Off")

    def apply_settings(self, settings: BridgeSettings) -> None:
        """Hot-update settings; restarts runtime if already active."""
        with self._lock:
            self._settings = settings
            runtime = self._runtime
        if runtime is not None and runtime.running:
            # Port/bind changes need a restart of HTTP — stop and let loop restart.
            runtime.stop()
            with self._lock:
                self._runtime = None

    def _loop(self) -> None:
        self._set_status(AgentStatus.IDLE, "Waiting for War Thunder…")
        try:
            while not self._stop.is_set():
                settings = self._settings
                if not is_war_thunder_running():
                    self._set_status(
                        AgentStatus.IDLE,
                        f"Idle — checks every {settings.idle_poll_sec:.0f}s",
                    )
                    # Sleep in chunks so Disable reacts quickly.
                    waited = 0.0
                    while waited < settings.idle_poll_sec and not self._stop.is_set():
                        time.sleep(0.5)
                        waited += 0.5
                    continue

                self._set_status(AgentStatus.ACTIVE, "War Thunder — Bridge active")
                config = RuntimeConfig(
                    bind=settings.bind,
                    port=settings.port,
                    game_host=settings.game_host,
                    game_port=settings.game_port,
                )
                runtime = BridgeRuntime(config)
                with self._lock:
                    self._runtime = runtime
                runtime.start()
                try:
                    while not self._stop.is_set() and is_war_thunder_running():
                        time.sleep(1.0)
                finally:
                    runtime.stop()
                    with self._lock:
                        if self._runtime is runtime:
                            self._runtime = None
                if not self._stop.is_set():
                    self._set_status(AgentStatus.IDLE, "War Thunder closed")
                    time.sleep(1.0)
        except Exception as exc:  # noqa: BLE001
            log.exception("agent loop crashed: %s", exc)
            self._set_status(AgentStatus.DISABLED, f"Error: {exc}")
        finally:
            if self._stop.is_set():
                self._set_status(AgentStatus.DISABLED, "Off")
