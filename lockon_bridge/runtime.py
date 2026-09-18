from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from typing import Optional

from .capture import ocr_screen
from .ocr_parse import parse_rewards_from_ocr_text
from .phase import read_phase
from .report_store import ReportStore
from .server import serve

log = logging.getLogger("lockon_bridge")


@dataclass
class RuntimeConfig:
    bind: str = "0.0.0.0"
    port: int = 8112
    game_host: str = "127.0.0.1"
    game_port: int = 8111
    frames: int = 5
    frame_gap: float = 1.2
    # Hangar/battle phase poll — only while War Thunder is running.
    poll_sec: float = 1.5


class BridgeRuntime:
    """HTTP + phase watcher that can be started/stopped with the game process."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.store = ReportStore()
        self._server: Optional[ThreadingHTTPServer] = None
        self._http_thread: Optional[threading.Thread] = None
        self._watch_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.store = ReportStore()
        self._server = serve(self.store, host=self.config.bind, port=self.config.port)
        self._http_thread = threading.Thread(
            target=self._server.serve_forever,
            name="lockon-http",
            daemon=True,
        )
        self._http_thread.start()
        self._watch_thread = threading.Thread(
            target=self._watch_loop,
            name="lockon-phase",
            daemon=True,
        )
        self._watch_thread.start()
        log.info(
            "Bridge active on port %s (game %s:%s)",
            self.config.port,
            self.config.game_host,
            self.config.game_port,
        )

    def stop(self) -> None:
        self._stop.set()
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:  # noqa: BLE001
                pass
            try:
                server.server_close()
            except Exception:  # noqa: BLE001
                pass
        if self._http_thread is not None:
            self._http_thread.join(timeout=3.0)
            self._http_thread = None
        if self._watch_thread is not None:
            self._watch_thread.join(timeout=3.0)
            self._watch_thread = None
        self.store = ReportStore()
        log.info("Bridge stopped (War Thunder closed)")

    def _watch_loop(self) -> None:
        was_in_battle = False
        cfg = self.config
        while not self._stop.is_set():
            try:
                snapshot = read_phase(cfg.game_host, cfg.game_port)
            except Exception as exc:  # noqa: BLE001
                log.debug("phase poll error: %s", exc)
                self._stop.wait(cfg.poll_sec)
                continue
            if snapshot is None:
                self._stop.wait(cfg.poll_sec)
                continue
            if snapshot.in_battle:
                if not was_in_battle:
                    log.info("in battle")
                was_in_battle = True
            elif was_in_battle:
                was_in_battle = False
                self._capture_burst()
            self._stop.wait(cfg.poll_sec)

    def _capture_burst(self) -> None:
        cfg = self.config
        log.info("battle ended — capturing %s frame(s)", cfg.frames)
        for index in range(cfg.frames):
            if self._stop.is_set():
                return
            try:
                text = ocr_screen()
                report = parse_rewards_from_ocr_text(text)
                if report is None:
                    log.info("frame %s/%s: no RP/SL labels yet", index + 1, cfg.frames)
                elif self.store.publish(report):
                    log.info(
                        "frame %s/%s: RP=%s SL=%s conf=%.2f",
                        index + 1,
                        cfg.frames,
                        report.research_points,
                        report.silver_lions,
                        report.confidence,
                    )
                    return
                else:
                    log.info("frame %s/%s: duplicate, skip", index + 1, cfg.frames)
            except Exception as exc:  # noqa: BLE001
                log.warning("frame %s/%s failed: %s", index + 1, cfg.frames, exc)
            self._stop.wait(cfg.frame_gap)
        log.info("burst finished without a confident report")
