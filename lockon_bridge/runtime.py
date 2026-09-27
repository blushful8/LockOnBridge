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

log = logging.getLogger("lockon_bridge")

def shot_gap_after(index: int, *, frames: int = 6, uniform: float = 0.5) -> float:
    """Pause after a 0-based shot before the next one.

    Default six-shot run: 0.5 s, then 0.65 s, 1 s, and 2 s before the last shot.
    """
    if frames != 6 or uniform != 0.5:
        return uniform
    if index <= 0:
        return 0.5
    if index <= 2:
        return 0.65
    if index <= 3:
        return 1.0
    return 2.0


def consecutive_reward_verdict(
    pairs: list[tuple[int, int] | None],
    *,
    final: bool,
) -> tuple[str, tuple[int, int] | None]:
    """
    Compare decoded rewards of consecutive screenshots.

    The first equal neighbor pair is a success and can be sent immediately.
    A mismatch before the last slot continues. If the last two still differ,
    the result cannot be guaranteed.
    """
    if len(pairs) < 2:
        return ("failure" if final else "continue"), None
    last = len(pairs) - 1
    for index in range(1, len(pairs)):
        left, right = pairs[index - 1], pairs[index]
        if left is not None and right is not None and left == right:
            return "success", right
        if final and index == last:
            return "failure", None
    return "continue", None

# Align with LockOn Android BridgeRepository default minConfidence.
_MIN_CONFIDENT_REPORT = 0.7
_MAX_PROVISIONAL_ATTEMPTS = 5


@dataclass
class _PendingProvisional:
    report_id: str
    session_id: str
    raw_hash: str
    attempts: int = 0


@dataclass
class RuntimeConfig:
    bind: str = "0.0.0.0"
    port: int = 8112
    game_host: str = "127.0.0.1"
    game_port: int = 8111
    # Six screenshots, half a second apart. Publish only when two neighbors match.
    frames: int = 6
    grab_frame_gap: float = 0.5
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
        self._pending_provisional: list[_PendingProvisional] = []

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
        """Post-battle grab-first OCR (OCR.space) — no hangar navigation."""
        from .crashguard import breadcrumb

        breadcrumb("capture_burst ocrspace begin")
        self._capture_burst_ocr()

    def _publish_capture_report(self, report) -> None:
        """Publish top capture; track / clear provisional pending by session."""
        sid = (getattr(report, "session_id", None) or "").lower()
        pending = next(
            (p for p in self._pending_provisional if p.session_id and p.session_id == sid),
            None,
        )
        if pending is not None and not report.provisional:
            if self.store.replace_by_id(pending.report_id, report):
                log.info(
                    "provisional finalized (top) session=%s RP=%s SL=%s outcome=%s",
                    sid[:12],
                    report.research_points,
                    report.silver_lions,
                    report.outcome,
                )
            self._pending_provisional = [
                p for p in self._pending_provisional if p.report_id != pending.report_id
            ]
            return

        if self.store.publish(report):
            log.info(
                "OCR results published RP=%s SL=%s provisional=%s conf=%.2f",
                report.research_points,
                report.silver_lions,
                report.provisional,
                report.confidence,
            )
        else:
            log.info(
                "OCR results duplicate RP=%s SL=%s",
                report.research_points,
                report.silver_lions,
            )

        if report.provisional:
            latest = self.store.latest()
            rid = latest.id if latest is not None else report.id
            if rid and not any(p.report_id == rid for p in self._pending_provisional):
                self._pending_provisional.append(
                    _PendingProvisional(
                        report_id=rid,
                        session_id=sid,
                        raw_hash=report.raw_hash,
                    )
                )
                log.info(
                    "provisional pending id=%s… session=%s (n=%s)",
                    rid[:8],
                    sid[:12] or "—",
                    len(self._pending_provisional),
                )

    def _apply_provisional_refreshes(self, refreshed: dict) -> None:
        """Apply arrow-walk hits; each pending gets one attempt per capture burst."""
        if not self._pending_provisional:
            return
        still: list[_PendingProvisional] = []
        for pending in self._pending_provisional:
            key = (pending.session_id or "").lower()
            hit = refreshed.get(key) if key else None
            if hit is not None and not hit.provisional:
                if self.store.replace_by_id(pending.report_id, hit):
                    log.info(
                        "provisional finalized (arrow) session=%s RP=%s SL=%s outcome=%s",
                        key[:12],
                        hit.research_points,
                        hit.silver_lions,
                        hit.outcome,
                    )
                continue
            pending.attempts += 1
            if pending.attempts >= _MAX_PROVISIONAL_ATTEMPTS:
                log.info(
                    "provisional drop session=%s after %s attempts",
                    key[:12] or pending.report_id[:8],
                    pending.attempts,
                )
                continue
            if hit is not None and hit.provisional:
                log.info(
                    "provisional still open session=%s attempts=%s",
                    key[:12],
                    pending.attempts,
                )
            still.append(pending)
        self._pending_provisional = still

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
            "battle ended — waiting %.2fs then 6 shots at 0.50/0.65/0.65/1.00/2.00s; "
            "publish when two neighbors decode the same RP/SL",
            cfg.capture_delay_sec,
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
                gap = shot_gap_after(
                    index, frames=cfg.frames, uniform=cfg.grab_frame_gap
                )
                self._stop.wait(gap)

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
        sticky_pair: int | None = None
        sticky_ceiling: tuple[int, int] | None = None
        total = len(buffer)
        decoded: list[tuple[int, int] | None] = []
        prev_frame = None
        confirm_done = 0
        planned = len(buffer)
        held: tuple[object, object, object] | None = None

        def _climbed_into(pairs: list[tuple[int, int] | None]) -> bool:
            known = [pair for pair in pairs if pair is not None]
            if len(known) < 2 or known[-1] != known[-2]:
                return False
            target = known[-1]
            return any(pair[0] < target[0] or pair[1] < target[1] for pair in known[:-1])

        def _confirm_after_rise() -> None:
            nonlocal confirm_done, total
            if confirm_done >= 2 or index + 1 < len(buffer):
                return
            known = [pair for pair in decoded if pair is not None]
            if not known:
                return
            last = known[-1]
            rose = any(pair[0] < last[0] or pair[1] < last[1] for pair in known[:-1])
            if not rose:
                return
            confirm_done += 1
            log.info(
                "count-up paused at RP=%s SL=%s — confirmation shot %s/2",
                last[0],
                last[1],
                confirm_done,
            )
            self._stop.wait(1.5)
            extra = grab_wt_client_image(focus=False, require_foreground=True)
            if extra is None:
                log.info("confirmation grab skipped (War Thunder not in foreground)")
                return
            buffer.append(extra.copy())
            total = len(buffer)
            log.info("confirmation grab kept size=%sx%s", extra.size[0], extra.size[1])

        try:
            for index, frame in enumerate(buffer):
                if self._stop.is_set():
                    return
                try:
                    breadcrumb(f"ocr_buffer frame {index + 1}/{total}")
                    set_last_ocr_frame(frame)
                    png, variants = ocr_saved_frame(
                        frame,
                        roi_only=False,
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

                    breadcrumb(
                        f"ocr_buffer frame {index + 1}/{total} variants={len(variants)}"
                    )
                    pair_now: tuple[int, int] | None = None
                    dump_parts: list[str] = []
                    best = None
                    if variants:
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
                    if best is not None and best[1].confidence >= _MIN_CONFIDENT_REPORT:
                        pair_now = (
                            best[1].research_points,
                            best[1].silver_lions,
                        )
                        last_preview = summarize_ocr_text(best[0])
                    elif variants:
                        last_preview = summarize_ocr_text(variants[0][1])
                    else:
                        last_preview = "(empty OCR)"
                    decoded.append(pair_now)
                    if pair_now is None and held is not None and index >= planned:
                        log.info(
                            "confirmation left the results screen — sending last match RP=%s SL=%s",
                            held[0].research_points,
                            held[0].silver_lions,
                        )
                        best = ("held", held[0])
                        frame = held[1]
                        prev_frame = held[2]
                        matched = (
                            held[0].research_points,
                            held[0].silver_lions,
                        )
                    else:
                        final = index + 1 >= total
                        verdict, matched = consecutive_reward_verdict(decoded, final=final)
                        shown = (
                            "none"
                            if pair_now is None
                            else f"RP={pair_now[0]} SL={pair_now[1]}"
                        )
                        log.info(
                            "ocr %s/%s: %s | compare=%s",
                            index + 1,
                            total,
                            shown,
                            verdict if len(decoded) > 1 else "wait",
                        )
                        if verdict != "success" or matched is None or best is None:
                            prev_frame = frame
                            if index + 1 >= len(buffer):
                                _confirm_after_rise()
                            continue
                        if _climbed_into(decoded) and confirm_done == 0:
                            log.info(
                                "ocr %s/%s: RP=%s SL=%s matched, but an earlier shot was lower — still counting",
                                index + 1,
                                total,
                                matched[0],
                                matched[1],
                            )
                            held = (best[1], frame, prev_frame)
                            prev_frame = frame
                            if index + 1 >= len(buffer):
                                _confirm_after_rise()
                            continue
                    report = best[1]
                    try:
                        if prev_frame is not None:
                            archive_capture_frame(
                                prev_frame,
                                kind="ok",
                                rp=report.research_points,
                                sl=report.silver_lions,
                                note="match-a",
                            )
                        archive_capture_frame(
                            frame,
                            kind="ok",
                            rp=report.research_points,
                            sl=report.silver_lions,
                            note="match-b",
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    if self.store.publish(report):
                        log.info(
                            "ocr %s/%s: neighbor match RP=%s SL=%s — sent",
                            index + 1,
                            total,
                            report.research_points,
                            report.silver_lions,
                        )
                        try:
                            maybe_auto_learn_pair(
                                frame,
                                rp=report.research_points,
                                sl=report.silver_lions,
                                prefer_with=prefer,
                            )
                        except Exception as exc:  # noqa: BLE001
                            log.debug("auto-learn skipped: %s", exc)
                    else:
                        log.info(
                            "ocr %s/%s: neighbor match duplicate RP=%s SL=%s",
                            index + 1,
                            total,
                            report.research_points,
                            report.silver_lions,
                        )
                    return
                except Exception as exc:  # noqa: BLE001
                    log.warning("ocr %s/%s failed: %s", index + 1, total, exc)
                    decoded.append(None)
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

            log.info(
                "burst finished without a guaranteed pair | shots=%s | last_ocr=%s",
                len(decoded),
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
            write_last_ocr_dump(text)
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


def write_last_ocr_dump(text: str) -> None:
    """Overwrite ``last_ocr.txt`` and prefix the local time of this write."""
    from datetime import datetime

    path = log_dir() / "last_ocr.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    body = (text or "").strip()
    path.write_text(
        f"[{stamp}]\n\n{body}\n" if body else f"[{stamp}]\n",
        encoding="utf-8-sig",
    )
