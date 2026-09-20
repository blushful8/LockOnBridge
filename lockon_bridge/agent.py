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


def _interruptible_sleep(stop: threading.Event, seconds: float, *, slice_sec: float = 1.0) -> None:
    """Sleep up to ``seconds``, waking early when ``stop`` is set."""
    end = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < end and not stop.is_set():
        time.sleep(min(slice_sec, end - time.monotonic()))


class BridgeAgent:
    """
    Background controller: when enabled, HTTP :8112 stays up so the phone can
    always fetch the last OCR report. Phase/OCR watching runs only while War
    Thunder is running — idle Bridge barely touches the CPU.
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
        """Hot-update settings; restarts HTTP if already running (port/bind)."""
        with self._lock:
            self._settings = settings
            runtime = self._runtime
        if runtime is not None and runtime.running:
            runtime.stop()
            with self._lock:
                self._runtime = None

    def _ensure_http(self, settings: BridgeSettings) -> BridgeRuntime:
        with self._lock:
            runtime = self._runtime
            if runtime is not None and runtime.running:
                return runtime
            if runtime is not None:
                try:
                    runtime.stop()
                except Exception:  # noqa: BLE001
                    pass
            config = RuntimeConfig(
                bind=settings.bind,
                port=settings.port,
                game_host=settings.game_host,
                game_port=settings.game_port,
            )
            runtime = BridgeRuntime(config)
            self._runtime = runtime
        runtime.start_http()
        return runtime

    def _loop(self) -> None:
        self._set_status(AgentStatus.IDLE, "Starting…")
        try:
            while not self._stop.is_set():
                settings = self._settings
                try:
                    runtime = self._ensure_http(settings)
                except Exception as exc:  # noqa: BLE001
                    log.exception("failed to start Bridge HTTP: %s", exc)
                    self._set_status(AgentStatus.IDLE, f"Port {settings.port} busy/error")
                    _interruptible_sleep(self._stop, 5.0, slice_sec=0.5)
                    continue

                if is_war_thunder_running(force=True):
                    self._set_status(AgentStatus.ACTIVE, "War Thunder — Bridge active")
                    runtime.start_watch()
                    # While fighting / hangar: only re-check process every few seconds.
                    # Phase HTTP is handled inside the watch thread (slow in battle).
                    while not self._stop.is_set() and is_war_thunder_running():
                        with self._lock:
                            live = self._runtime
                        if live is None or not live.running:
                            break
                        _interruptible_sleep(self._stop, 3.0, slice_sec=1.0)
                    runtime.stop_watch()
                    log.info("War Thunder closed — phase watch stopped (HTTP kept)")
                else:
                    runtime.stop_watch()
                    idle = max(15.0, float(settings.idle_poll_sec))
                    self._set_status(
                        AgentStatus.IDLE,
                        f"Waiting for War Thunder — check every {idle:.0f}s "
                        f"(phone can still read :{settings.port})",
                    )
                    # One process scan per idle interval — no 0.5s process_iter spam.
                    _interruptible_sleep(self._stop, idle, slice_sec=1.0)
        except Exception as exc:  # noqa: BLE001
            log.exception("agent loop crashed: %s", exc)
            self._set_status(AgentStatus.DISABLED, f"Error: {exc}")
        finally:
            with self._lock:
                runtime = self._runtime
                self._runtime = None
            if runtime is not None:
                runtime.stop()
            if self._stop.is_set():
                self._set_status(AgentStatus.DISABLED, "Off")
