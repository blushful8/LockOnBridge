from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from io import BytesIO
from typing import Optional

from .capture import (
    grab_wt_client_image,
    last_capture_meta,
    last_ocr_frame,
    ocr_saved_frame,
    set_last_ocr_frame,
)
from .capture_archive import save_result_shots
from .match_session import MatchSession
from .ocr_parse import choose_best_report, parse_rewards_from_ocr_text, summarize_ocr_text
from .paths import log_dir
from .phase import read_phase
from .report_store import ReportStore
from .server import serve

log = logging.getLogger("lockon_bridge")

_CAPTURE_LOCK = threading.Lock()

def shot_gap_after(index: int, *, frames: int = 6, uniform: float = 0.5) -> float:
    """Pause after a 0-based shot before the next one.

    Default six-shot run sums to about 6 s: 0.5 + 0.65 + 0.65 + 1 + 2.
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


def select_reward_read(
    pairs: list[tuple[int, int] | None],
) -> tuple[int | None, str]:
    """Select only after every captured frame has been decoded.

    A count-up may pause at equal neighbors before its final value. Prefer
    the latest readable frame unless either reward regresses. In that case,
    use the latest equal neighboring pair preceding the first regression.
    Unreadable frames neither form matches nor hide a subsequent decrease.
    """
    latest = None
    confirmed = None
    previous = None
    regression = False
    for index, pair in enumerate(pairs):
        if pair is None:
            continue
        if previous is not None and (
            pair[0] < previous[0] or pair[1] < previous[1]
        ):
            regression = True
        if (
            not regression
            and index > 0
            and pairs[index - 1] == pair
        ):
            confirmed = index
        previous = pair
        latest = index
    if regression and confirmed is not None:
        return confirmed, "regression: earlier neighbor match"
    return latest, "latest recognized (no earlier match)" if regression else "latest recognized"

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
    # Six screenshots. Pauses 0.5, 0.65, 0.65, 1 and 2 seconds sum to about 6.
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
        self.match = MatchSession()
        self.store = ReportStore(session=self.match)
        self._was_in_battle = False
        self._hangar_streak = 0
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
        self._server = serve(
            self.store,
            host=self.config.bind,
            port=self.config.port,
            session=self.match,
        )
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
        thread = self._watch_thread
        if thread is not None and thread.is_alive():
            if not self._watch_stop.is_set():
                return
            if threading.current_thread() is not thread:
                thread.join(timeout=15.0)
            if thread.is_alive():
                log.info("phase watch busy — not starting a second one")
                return
        if not self.running:
            self.start_http()
        self._watch_stop.clear()
        self._was_in_battle = False
        self._hangar_streak = 0
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
        if (
            thread is not None
            and thread.is_alive()
            and threading.current_thread() is not thread
        ):
            thread.join(timeout=3.0)
        if thread is not None and thread.is_alive():
            # A burst is still publishing. Dropping the thread here used to
            # start a second watch and a new session id on top of this one.
            log.info("phase watch still finishing a capture")
            return
        self._watch_thread = None
        self._was_in_battle = False
        self._hangar_streak = 0
        self.match.finish_results()

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
        # Do NOT clear self.store or the last battle id — the phone may still
        # match a report published from the results screen.
        log.info("Bridge HTTP stopped (last report kept)")

    def _watch_wait(self, seconds: float) -> None:
        """Sleep that wakes on either full stop or watch-only stop."""
        end = time.monotonic() + max(0.05, seconds)
        while time.monotonic() < end:
            if self._stop.is_set() or self._watch_stop.is_set():
                return
            time.sleep(min(0.25, end - time.monotonic()))

    def _battle_just_ended(self, in_battle: bool) -> bool:
        """Track hangar ↔ battle. A new id is issued only on entry.

        One hangar reading is not enough: map_info briefly goes invalid in
        the middle of a flight. Two readings in a row confirm the results
        screen, which stays up much longer than a single poll.
        """
        if in_battle:
            if not self._was_in_battle:
                self.match.begin()
            self._was_in_battle = True
            self._hangar_streak = 0
            return False
        if not self._was_in_battle:
            return False
        self._hangar_streak += 1
        if self._hangar_streak < 2:
            return False
        self._was_in_battle = False
        self._hangar_streak = 0
        return True

    def _watch_loop(self) -> None:
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
            if self._battle_just_ended(snapshot.in_battle):
                try:
                    self._capture_burst()
                finally:
                    # Leave the id in place. The phone is often already in the
                    # hangar while this report is still being published.
                    self.match.finish_results()
                self._watch_wait(hangar)
            elif snapshot.in_battle:
                self._watch_wait(battle)
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

    def _capture_burst_ocr(self, *, publish: bool = True):
        """One burst at a time, so the notice counter does not run twice."""
        if not _CAPTURE_LOCK.acquire(blocking=False):
            log.info("capture already running — skip overlapping burst")
            return None
        try:
            from .result_hold import ResultHold

            with ResultHold(total=self.config.frames) as hold:
                return self._capture_burst_ocr_body(publish=publish, hold=hold)
        finally:
            _CAPTURE_LOCK.release()

    def _capture_burst_ocr_body(self, *, publish: bool = True, hold):
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
            "decode every captured shot before selecting RP/SL",
            cfg.capture_delay_sec,
        )
        # --- Phase A: shots while the results screen is held still ---
        grab_t0 = time.monotonic()
        buffer: list = []
        taken_at: list[float] = []
        if cfg.capture_delay_sec > 0:
            self._stop.wait(cfg.capture_delay_sec)
        for index in range(cfg.frames):
            if self._stop.is_set():
                break
            hold.set_progress(index + 1)
            breadcrumb(f"grab_burst frame {index + 1}/{cfg.frames}")
            frame = grab_wt_client_image(
                focus=False,
                require_foreground=True,
                allow_mss=False,
            )
            if frame is None:
                log.info(
                    "grab %s/%s: skipped (no frame)",
                    index + 1,
                    cfg.frames,
                )
            else:
                # Copy so later WT paints cannot mutate our buffer.
                buffer.append(frame.copy())
                taken_at.append(time.time())
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
        hold.begin_processing()
        if buffer:
            save_result_shots(buffer, taken_at=taken_at)
        if self._stop.is_set():
            return

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
        total = len(buffer)
        decoded: list[tuple[int, int] | None] = []
        reads: list[tuple[object | None, object]] = []
        try:
            for index, frame in enumerate(buffer):
                if self._stop.is_set():
                    return
                try:
                    hold.set_processing_progress(index + 1, total)
                    breadcrumb(f"ocr_buffer frame {index + 1}/{total}")
                    set_last_ocr_frame(frame)
                    png, variants = ocr_saved_frame(
                        frame,
                        worker=worker,
                        prefer_with=prefer,
                    )

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
                    reads.append((best[1] if pair_now is not None and best is not None else None, frame))
                    log.info(
                        "ocr %s/%s: %s | waiting for all captured shots",
                        index + 1,
                        total,
                        "none" if pair_now is None else f"RP={pair_now[0]} SL={pair_now[1]}",
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("ocr %s/%s failed: %s", index + 1, total, exc)
                    if len(decoded) <= index:
                        decoded.append(None)
                        reads.append((None, frame))
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

            shot_i, reason = select_reward_read(decoded)
            if shot_i is not None:
                report, _shot = reads[shot_i]
                log.info(
                    "all %s shots decoded — selected shot %s/%s RP=%s SL=%s | %s",
                    total,
                    shot_i + 1,
                    total,
                    report.research_points,
                    report.silver_lions,
                    reason,
                )
                report = self.match.stamp(report)
                if publish and self.store.publish(report):
                    log.info(
                        "ocr shot %s: selected RP=%s SL=%s session=%s — sent",
                        shot_i + 1,
                        report.research_points,
                        report.silver_lions,
                        report.session_id or "—",
                    )
                elif publish:
                    log.info(
                        "ocr shot %s: selected duplicate RP=%s SL=%s",
                        shot_i + 1,
                        report.research_points,
                        report.silver_lions,
                    )
                else:
                    log.info(
                        "ocr shot %s: selected RP=%s SL=%s — not sent",
                        shot_i + 1,
                        report.research_points,
                        report.silver_lions,
                    )
                return report
            log.info(
                "burst finished without a recognized pair | shots=%s | last_ocr=%s",
                len(decoded),
                last_preview or "(none)",
            )
        finally:
            if worker is not None:
                try:
                    worker.close()
                except Exception:  # noqa: BLE001
                    pass

    def _write_ocr_dump(self, text: str, *, png: bytes | None = None) -> None:
        try:
            write_last_ocr_dump(text)
            from .layout_ocr import crop_parse_zone

            frame = last_ocr_frame()
            if frame is not None:
                buf = BytesIO()
                crop_parse_zone(frame).save(buf, format="PNG")
                (log_dir() / "last_capture.png").write_bytes(buf.getvalue())
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
