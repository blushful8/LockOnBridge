from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from typing import Optional

from .capture import ocr_screen_capture
from .ocr_parse import choose_best_report, parse_rewards_from_ocr_text, summarize_ocr_text
from .paths import log_dir
from .phase import read_phase
from .report_store import ReportStore
from .server import serve
from .settle import SettleConfig, SettleTracker

log = logging.getLogger("lockon_bridge")

# Align with LockOn Android BridgeRepository default minConfidence.
_MIN_CONFIDENT_REPORT = 0.7


@dataclass
class RuntimeConfig:
    bind: str = "0.0.0.0"
    port: int = 8112
    game_host: str = "127.0.0.1"
    game_port: int = 8111
    # Sample long enough for the RP/SL count-up animation to finish.
    frames: int = 20
    frame_gap: float = 0.75
    # Wait for the rewards tab after hangar flip; count-up often needs a few more seconds.
    capture_delay_sec: float = 4.0
    # Consecutive near-identical OCR pairs required before publish (never publish frame 1 alone).
    settle_stable_frames: int = 2
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
        # Keep existing store (and disk-backed last report) across WT sessions.
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
        # Do NOT clear self.store — phone may still poll after the game closes.
        log.info("Bridge HTTP stopped (last report kept)")

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
        log.info(
            "battle ended — waiting %.1fs then capturing up to %s frame(s) "
            "(publish only after %s stable reads)",
            cfg.capture_delay_sec,
            cfg.frames,
            cfg.settle_stable_frames,
        )
        if cfg.capture_delay_sec > 0:
            self._stop.wait(cfg.capture_delay_sec)

        tracker = SettleTracker(
            cfg=SettleConfig(stable_required=max(2, cfg.settle_stable_frames)),
        )
        last_preview = ""

        for index in range(cfg.frames):
            if self._stop.is_set():
                return
            try:
                png, variants = ocr_screen_capture()
                if not variants:
                    last_preview = "(empty OCR)"
                    log.info(
                        "frame %s/%s: no OCR text | engines empty",
                        index + 1,
                        cfg.frames,
                    )
                    self._write_ocr_dump("", png=png)
                    self._stop.wait(cfg.frame_gap)
                    continue

                candidates = [
                    (text, parse_rewards_from_ocr_text(text)) for _tag, text in variants
                ]
                best = choose_best_report(candidates)
                dump_parts: list[str] = []
                for tag, text in variants:
                    parsed = parse_rewards_from_ocr_text(text)
                    if parsed is None:
                        dump_parts.append(f"[{tag}]\n{text}\n=> (no parse)")
                    else:
                        dump_parts.append(
                            f"[{tag}]\n{text}\n"
                            f"=> RP={parsed.research_points} SL={parsed.silver_lions}"
                        )
                self._write_ocr_dump("\n\n---OCR---\n\n".join(dump_parts), png=png)

                if best is None:
                    last_preview = summarize_ocr_text(variants[0][1])
                    log.info(
                        "frame %s/%s: no RP/SL yet | engines=%s | ocr=%s",
                        index + 1,
                        cfg.frames,
                        ",".join(tag for tag, _ in variants),
                        last_preview,
                    )
                else:
                    text, report = best
                    last_preview = summarize_ocr_text(text)
                    if report.confidence < _MIN_CONFIDENT_REPORT:
                        log.info(
                            "frame %s/%s: RP=%s SL=%s conf=%.2f (below %.2f — keep capturing)",
                            index + 1,
                            cfg.frames,
                            report.research_points,
                            report.silver_lions,
                            report.confidence,
                            _MIN_CONFIDENT_REPORT,
                        )
                    else:
                        settled = tracker.observe(report)
                        if settled is None:
                            log.info(
                                "frame %s/%s: RP=%s SL=%s conf=%.2f "
                                "(waiting settle %s/%s)",
                                index + 1,
                                cfg.frames,
                                report.research_points,
                                report.silver_lions,
                                report.confidence,
                                tracker.stable_count,
                                tracker.cfg.stable_required,
                            )
                        else:
                            if self.store.publish(settled):
                                log.info(
                                    "frame %s/%s: settled RP=%s SL=%s conf=%.2f",
                                    index + 1,
                                    cfg.frames,
                                    settled.research_points,
                                    settled.silver_lions,
                                    settled.confidence,
                                )
                            else:
                                log.info(
                                    "frame %s/%s: settled duplicate RP=%s SL=%s",
                                    index + 1,
                                    cfg.frames,
                                    settled.research_points,
                                    settled.silver_lions,
                                )
                            return
            except Exception as exc:  # noqa: BLE001
                log.warning("frame %s/%s failed: %s", index + 1, cfg.frames, exc)
            self._stop.wait(cfg.frame_gap)

        fallback = tracker.finalize()
        if fallback is not None and fallback.confidence >= _MIN_CONFIDENT_REPORT:
            if self.store.publish(fallback):
                log.info(
                    "burst ended — published best available RP=%s SL=%s "
                    "(stable=%s/%s) | last_ocr=%s",
                    fallback.research_points,
                    fallback.silver_lions,
                    tracker.stable_count,
                    tracker.cfg.stable_required,
                    last_preview or "(none)",
                )
                return
        log.info(
            "burst finished without a confident report | last_ocr=%s",
            last_preview or "(none)",
        )

    def _write_ocr_dump(self, text: str, *, png: bytes | None = None) -> None:
        try:
            path = log_dir() / "last_ocr.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text or "", encoding="utf-8")
            if png:
                (log_dir() / "last_capture.png").write_bytes(png)
        except OSError:
            pass
