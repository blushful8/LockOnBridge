from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from typing import Optional

from .capture import (
    grab_wt_client_image,
    last_capture_meta,
    last_ocr_frame,
    ocr_saved_frame,
    set_last_ocr_frame,
)
from .capture_archive import archive_capture_frame
from .ocr_parse import choose_best_report, parse_rewards_from_ocr_text, summarize_ocr_text
from .paths import log_dir
from .phase import read_phase
from .report_store import ReportStore
from .roi_auto_learn import maybe_auto_learn_pair
from .server import serve
from .settle import LeanRoiFrameGate, SettleConfig, SettleTracker

log = logging.getLogger("lockon_bridge")

# Align with LockOn Android BridgeRepository default minConfidence.
_MIN_CONFIDENT_REPORT = 0.7


@dataclass
class RuntimeConfig:
    bind: str = "0.0.0.0"
    port: int = 8112
    game_host: str = "127.0.0.1"
    game_port: int = 8111
    # Hard ceiling: ideally settle in 2–3 frames; never more than 5 screenshots.
    frames: int = 5
    # Phase A: gap between rapid grabs (OCR happens later on the buffer).
    grab_frame_gap: float = 0.18
    # Idle gap *after* OCR finishes — legacy sequential path / CLI; grab-first ignores for OCR.
    frame_gap: float = 0.25
    # First frames: almost no idle; catch quick results closes.
    early_frames: int = 2
    early_frame_gap: float = 0.08
    # Almost no wait: results often appear immediately; a long delay misses users who close fast.
    # Count-up is handled by SettleTracker, not by sitting idle before the first screenshot.
    capture_delay_sec: float = 0.15
    # Consecutive near-identical OCR pairs required before publish (never publish frame 1 alone).
    settle_stable_frames: int = 2
    # Also require lean ROI pixels to match this many times (count-up animation gate).
    settle_frame_stable: int = 2
    # Phase poll while in hangar (need to notice battle start; still light HTTP only).
    poll_hangar_sec: float = 2.0
    # Phase poll during battle — slower so we barely touch the game while fighting.
    poll_battle_sec: float = 3.0
    # When game HTTP is unreachable, back off harder.
    poll_offline_sec: float = 5.0
    # Legacy alias used by CLI ``--poll``.
    poll_sec: float = 2.0


class BridgeRuntime:
    """HTTP (always) + optional phase watcher (only while War Thunder runs)."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.store = ReportStore()
        self._server: Optional[ThreadingHTTPServer] = None
        self._http_thread: Optional[threading.Thread] = None
        self._watch_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._watch_stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def watching(self) -> bool:
        return self._watch_thread is not None and self._watch_thread.is_alive()

    def start(self) -> None:
        """Start HTTP + phase watch (CLI / full session)."""
        self.start_http()
        self.start_watch()

    def start_http(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._server = serve(self.store, host=self.config.bind, port=self.config.port)
        self._http_thread = threading.Thread(
            target=self._server.serve_forever,
            name="lockon-http",
            daemon=True,
        )
        self._http_thread.start()
        log.info(
            "Bridge HTTP on port %s (game %s:%s)",
            self.config.port,
            self.config.game_host,
            self.config.game_port,
        )

    def start_watch(self) -> None:
        """Begin hangar/battle phase polling (call only while WT is running)."""
        if self.watching:
            return
        if not self.running:
            self.start_http()
        self._watch_stop.clear()
        self._watch_thread = threading.Thread(
            target=self._watch_loop,
            name="lockon-phase",
            daemon=True,
        )
        self._watch_thread.start()
        log.info(
            "Phase watch started (hangar=%.1fs battle=%.1fs)",
            self.config.poll_hangar_sec,
            self.config.poll_battle_sec,
        )

    def stop_watch(self) -> None:
        """Stop phase polling; keep HTTP + last report for the phone."""
        self._watch_stop.set()
        thread = self._watch_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self._watch_thread = None

    def stop(self) -> None:
        self._stop.set()
        self.stop_watch()
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
        # Do NOT clear self.store — phone may still poll after the game closes.
        log.info("Bridge HTTP stopped (last report kept)")

    def _watch_wait(self, seconds: float) -> None:
        """Sleep that wakes on either full stop or watch-only stop."""
        end = time.monotonic() + max(0.05, seconds)
        while time.monotonic() < end:
            if self._stop.is_set() or self._watch_stop.is_set():
                return
            time.sleep(min(0.25, end - time.monotonic()))

    def _watch_loop(self) -> None:
        was_in_battle = False
        cfg = self.config
        hangar = max(1.0, float(cfg.poll_hangar_sec or cfg.poll_sec or 2.0))
        battle = max(1.5, float(cfg.poll_battle_sec or 3.0))
        offline = max(battle, float(cfg.poll_offline_sec or 5.0))
        log.debug(
            "phase watch loop hangar=%.1fs battle=%.1fs offline=%.1fs",
            hangar,
            battle,
            offline,
        )
        while not self._stop.is_set() and not self._watch_stop.is_set():
            try:
                snapshot = read_phase(cfg.game_host, cfg.game_port)
            except Exception as exc:  # noqa: BLE001
                log.debug("phase poll error: %s", exc)
                self._watch_wait(offline)
                continue
            if snapshot is None:
                # Game HTTP not up yet / briefly unreachable — do not spam.
                self._watch_wait(offline)
                continue
            if snapshot.in_battle:
                if not was_in_battle:
                    log.info("in battle")
                was_in_battle = True
                self._watch_wait(battle)
            elif was_in_battle:
                was_in_battle = False
                self._capture_burst()
                self._watch_wait(hangar)
            else:
                self._watch_wait(hangar)

    def _capture_burst(self) -> None:
        """Clipboard Messages path first; grab-first OCR as fallback."""
        from .crashguard import breadcrumb

        try:
            from .settings import load_settings as _ls

            use_clip = bool(_ls().use_clipboard_results)
        except Exception:  # noqa: BLE001
            use_clip = True

        if use_clip:
            breadcrumb("capture_burst clipboard-msg begin")
            log.info("battle ended — trying Messages clipboard results")
            try:
                from .wt_messages_ui import capture_clipboard_battle_report

                report, reason = capture_clipboard_battle_report(
                    hangar_settle_sec=0.45,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("clipboard results error: %s", exc)
                report, reason = None, f"error:{exc}"
            if report is not None:
                if self.store.publish(report):
                    log.info(
                        "clipboard results published RP=%s SL=%s conf=%.2f",
                        report.research_points,
                        report.silver_lions,
                        report.confidence,
                    )
                else:
                    log.info(
                        "clipboard results duplicate RP=%s SL=%s",
                        report.research_points,
                        report.silver_lions,
                    )
                return
            log.info(
                "clipboard results unavailable (%s) — OCR grab-first fallback",
                reason,
            )

        self._capture_burst_ocr()

    def _capture_burst_ocr(self) -> None:
        """Grab-first burst: snapshot WT quickly, then OCR the buffer offline."""
        from .crashguard import breadcrumb
        from .ocr_isolate import PersistentOcrWorker

        cfg = self.config
        breadcrumb(
            f"capture_burst begin frames={cfg.frames} delay={cfg.capture_delay_sec} "
            f"grab_gap={cfg.grab_frame_gap}"
        )
        log.info(
            "battle ended — waiting %.2fs then grab-first up to %s frame(s) "
            "(grab gap %.2fs; OCR after buffer; publish after %s OCR + %s pixel-stable)",
            cfg.capture_delay_sec,
            cfg.frames,
            cfg.grab_frame_gap,
            cfg.settle_stable_frames,
            cfg.settle_frame_stable,
        )
        if cfg.capture_delay_sec > 0:
            self._stop.wait(cfg.capture_delay_sec)

        # --- Phase A: rapid grabs while results screen is still up ---
        grab_t0 = time.monotonic()
        buffer: list = []
        for index in range(cfg.frames):
            if self._stop.is_set():
                return
            breadcrumb(f"grab_burst frame {index + 1}/{cfg.frames}")
            frame = grab_wt_client_image(focus=False, require_foreground=True)
            if frame is None:
                log.info(
                    "grab %s/%s: skipped (War Thunder not in foreground)",
                    index + 1,
                    cfg.frames,
                )
            else:
                # Copy so later WT paints cannot mutate our buffer.
                buffer.append(frame.copy())
                log.info(
                    "grab %s/%s: ok size=%sx%s",
                    index + 1,
                    cfg.frames,
                    frame.size[0],
                    frame.size[1],
                )
            if index + 1 < cfg.frames:
                self._stop.wait(cfg.grab_frame_gap)

        grab_elapsed = time.monotonic() - grab_t0
        log.info(
            "grab_burst done frames=%s/%s elapsed=%.2fs",
            len(buffer),
            cfg.frames,
            grab_elapsed,
        )
        breadcrumb(f"grab_burst done kept={len(buffer)}/{cfg.frames}")
        if not buffer:
            log.info("burst finished without frames (no WT foreground)")
            return

        settle_cfg = SettleConfig(stable_required=max(2, cfg.settle_stable_frames))
        tracker = SettleTracker(cfg=settle_cfg)
        frame_gate = LeanRoiFrameGate(
            cfg=SettleConfig(
                stable_required=max(2, cfg.settle_frame_stable),
                frame_mae_max=settle_cfg.frame_mae_max,
                frame_sig_size=settle_cfg.frame_sig_size,
            )
        )

        try:
            from .settings import load_settings as _ls

            prefer = bool(_ls().has_premium_account)
        except Exception:  # noqa: BLE001
            prefer = False

        worker: PersistentOcrWorker | None
        try:
            worker = PersistentOcrWorker()
            worker.start()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "persistent OCR worker unavailable (%s) — one-shot fallback", exc
            )
            worker = None

        last_preview = ""
        saw_confident = False
        sticky_pair: int | None = None
        sticky_ceiling: tuple[int, int] | None = None
        total = len(buffer)

        try:
            for index, frame in enumerate(buffer):
                if self._stop.is_set():
                    return
                try:
                    breadcrumb(f"ocr_buffer frame {index + 1}/{total}")
                    set_last_ocr_frame(frame)
                    png, variants = ocr_saved_frame(
                        frame,
                        roi_only=saw_confident,
                        worker=worker,
                        pair_hint=sticky_pair,
                        prefer_with=prefer,
                        premium_ceiling=sticky_ceiling,
                    )
                    meta = last_capture_meta()
                    if isinstance(meta, dict) and "pair_index" in meta:
                        sticky_pair = int(meta["pair_index"])
                        ceiling = meta.get("premium_ceiling")
                        if (
                            isinstance(ceiling, (list, tuple))
                            and len(ceiling) >= 2
                            and ceiling[0] is not None
                            and ceiling[1] is not None
                        ):
                            sticky_ceiling = (int(ceiling[0]), int(ceiling[1]))
                        log.info(
                            "sticky calib hint pair=%s ceiling=%s",
                            sticky_pair,
                            sticky_ceiling,
                        )

                    breadcrumb(
                        f"ocr_buffer frame {index + 1}/{total} variants={len(variants)}"
                    )
                    pixels_stable = frame_gate.observe(frame)
                    if not variants:
                        last_preview = "(empty OCR)"
                        log.info(
                            "ocr %s/%s: no OCR text | pix_stable=%s mae=%s",
                            index + 1,
                            total,
                            pixels_stable,
                            None
                            if frame_gate.last_mae is None
                            else f"{frame_gate.last_mae:.1f}",
                        )
                        self._write_ocr_dump("", png=png)
                        continue

                    candidates = [
                        (
                            text,
                            parse_rewards_from_ocr_text(
                                text, prefer_premium_rewards=prefer
                            ),
                        )
                        for _tag, text in variants
                    ]
                    best = choose_best_report(
                        candidates, prefer_premium_rewards=prefer
                    )
                    dump_parts: list[str] = []
                    for tag, text in variants:
                        parsed = parse_rewards_from_ocr_text(
                            text, prefer_premium_rewards=prefer
                        )
                        if parsed is None:
                            dump_parts.append(f"[{tag}]\n{text}\n=> (no parse)")
                        else:
                            dump_parts.append(
                                f"[{tag}]\n{text}\n"
                                f"=> RP={parsed.research_points} "
                                f"SL={parsed.silver_lions}"
                            )
                    self._write_ocr_dump(
                        "\n\n---OCR---\n\n".join(dump_parts), png=png
                    )

                    if best is None:
                        last_preview = summarize_ocr_text(variants[0][1])
                        log.info(
                            "ocr %s/%s: no RP/SL yet | engines=%s | pix=%s/%s | ocr=%s",
                            index + 1,
                            total,
                            ",".join(tag for tag, _ in variants),
                            frame_gate.stable_count,
                            frame_gate.cfg.stable_required,
                            last_preview,
                        )
                        continue

                    text, report = best
                    last_preview = summarize_ocr_text(text)
                    if report.confidence < _MIN_CONFIDENT_REPORT:
                        log.info(
                            "ocr %s/%s: RP=%s SL=%s conf=%.2f "
                            "(below %.2f — keep OCR buffer)",
                            index + 1,
                            total,
                            report.research_points,
                            report.silver_lions,
                            report.confidence,
                            _MIN_CONFIDENT_REPORT,
                        )
                        continue

                    saw_confident = True
                    settled = tracker.observe(report)
                    if settled is None:
                        log.info(
                            "ocr %s/%s: RP=%s SL=%s conf=%.2f "
                            "(ocr settle %s/%s, pix %s/%s mae=%s)",
                            index + 1,
                            total,
                            report.research_points,
                            report.silver_lions,
                            report.confidence,
                            tracker.stable_count,
                            tracker.cfg.stable_required,
                            frame_gate.stable_count,
                            frame_gate.cfg.stable_required,
                            None
                            if frame_gate.last_mae is None
                            else f"{frame_gate.last_mae:.1f}",
                        )
                        continue
                    if not pixels_stable:
                        log.info(
                            "ocr %s/%s: RP=%s SL=%s conf=%.2f "
                            "(OCR settled — waiting lean ROI pixels %s/%s mae=%s)",
                            index + 1,
                            total,
                            settled.research_points,
                            settled.silver_lions,
                            settled.confidence,
                            frame_gate.stable_count,
                            frame_gate.cfg.stable_required,
                            None
                            if frame_gate.last_mae is None
                            else f"{frame_gate.last_mae:.1f}",
                        )
                        continue

                    try:
                        archive_capture_frame(
                            frame,
                            kind="ok",
                            rp=settled.research_points,
                            sl=settled.silver_lions,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    if self.store.publish(settled):
                        log.info(
                            "ocr %s/%s: settled RP=%s SL=%s conf=%.2f "
                            "(OCR+pixels stable, grab-first buffer)",
                            index + 1,
                            total,
                            settled.research_points,
                            settled.silver_lions,
                            settled.confidence,
                        )
                        try:
                            maybe_auto_learn_pair(
                                frame,
                                rp=settled.research_points,
                                sl=settled.silver_lions,
                                prefer_with=prefer,
                            )
                        except Exception as exc:  # noqa: BLE001
                            log.debug("auto-learn skipped: %s", exc)
                    else:
                        log.info(
                            "ocr %s/%s: settled duplicate RP=%s SL=%s",
                            index + 1,
                            total,
                            settled.research_points,
                            settled.silver_lions,
                        )
                    return
                except Exception as exc:  # noqa: BLE001
                    log.warning("ocr %s/%s failed: %s", index + 1, total, exc)
                    try:
                        from .crashguard import write_crash_report
                        import traceback

                        write_crash_report(
                            f"burst-ocr-{index + 1}",
                            "".join(
                                traceback.format_exception(
                                    type(exc), exc, exc.__traceback__
                                )
                            ),
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    breadcrumb(f"ocr_buffer frame {index + 1} error: {exc}")

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
                    try:
                        frame = last_ocr_frame()
                        if frame is not None:
                            archive_capture_frame(
                                frame,
                                kind="ok",
                                rp=fallback.research_points,
                                sl=fallback.silver_lions,
                                note="finalize",
                            )
                            maybe_auto_learn_pair(
                                frame,
                                rp=fallback.research_points,
                                sl=fallback.silver_lions,
                                prefer_with=prefer,
                            )
                    except Exception:  # noqa: BLE001
                        pass
                    return
            log.info(
                "burst finished without a confident report | last_ocr=%s",
                last_preview or "(none)",
            )
            try:
                frame = last_ocr_frame()
                if frame is not None:
                    archive_capture_frame(frame, kind="fail", note="burst")
            except Exception:  # noqa: BLE001
                pass
        finally:
            if worker is not None:
                try:
                    worker.close()
                except Exception:  # noqa: BLE001
                    pass

    def _write_ocr_dump(self, text: str, *, png: bytes | None = None) -> None:
        try:
            path = log_dir() / "last_ocr.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text or "", encoding="utf-8")
            from .roi_debug import save_ocr_crop_dumps
            from .settings import load_settings

            frame = last_ocr_frame()
            if frame is not None:
                save_ocr_crop_dumps(
                    frame,
                    log_dir(),
                    debug_full=bool(load_settings().debug_show_rois),
                )
            elif png:
                (log_dir() / "last_capture.png").write_bytes(png)
        except OSError:
            pass
