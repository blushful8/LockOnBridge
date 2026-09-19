from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional

from .ocr_parse import BattleReport
from .paths import data_root

log = logging.getLogger("lockon_bridge")


def _report_path():
    return data_root() / "last_report.json"


class ReportStore:
    """
    Single source of truth for the latest OCR report.

    Memory and last_report.json must stay in sync — the phone reads HTTP which
    used to drift from disk when a prior in-memory value outlived a newer file.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: Optional[BattleReport] = None
        self._seen_hashes: set[str] = set()
        self._load_disk_unlocked()

    def _load_disk_unlocked(self) -> None:
        path = _report_path()
        if not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            report = _report_from_json(raw)
            if report is None:
                return
            self._latest = report
            self._seen_hashes.add(report.raw_hash)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            log.debug("Could not load last_report.json: %s", exc)

    def _refresh_from_disk_unlocked(self) -> None:
        """Prefer disk when it has a newer capturedAt than memory (or memory empty)."""
        path = _report_path()
        if not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            report = _report_from_json(raw)
            if report is None:
                return
            current = self._latest
            if current is None or report.captured_at_epoch_millis > current.captured_at_epoch_millis:
                self._latest = report
                self._seen_hashes.add(report.raw_hash)
                if current is not None and current.raw_hash != report.raw_hash:
                    log.info(
                        "ReportStore: loaded newer disk report RP=%s SL=%s",
                        report.research_points,
                        report.silver_lions,
                    )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return

    def _save_disk_unlocked(self, report: BattleReport) -> None:
        path = _report_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(report.to_json(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("Could not save last_report.json: %s", exc)

    def publish(self, report: BattleReport) -> bool:
        """Keep the newest report. Returns True when HTTP clients should see a change."""
        with self._lock:
            self._refresh_from_disk_unlocked()
            current = self._latest
            if current is not None:
                if report.raw_hash == current.raw_hash:
                    return False
                if report.captured_at_epoch_millis < current.captured_at_epoch_millis:
                    log.info(
                        "ReportStore: ignore older OCR RP=%s SL=%s (have newer)",
                        report.research_points,
                        report.silver_lions,
                    )
                    return False
            self._seen_hashes.add(report.raw_hash)
            if len(self._seen_hashes) > 64:
                self._seen_hashes = set(list(self._seen_hashes)[-32:])
            self._latest = report
            self._save_disk_unlocked(report)
            return True

    def latest(self) -> Optional[BattleReport]:
        with self._lock:
            self._refresh_from_disk_unlocked()
            return self._latest


def _report_from_json(raw: Any) -> BattleReport | None:
    if not isinstance(raw, dict):
        return None
    try:
        rp = int(raw.get("researchPoints"))
        sl = int(raw.get("silverLions"))
        if rp < 0 or sl < 0:
            return None
        return BattleReport(
            captured_at_epoch_millis=int(raw.get("capturedAtEpochMillis") or 0),
            research_points=rp,
            silver_lions=sl,
            outcome=str(raw.get("outcome") or "undecided"),
            raw_hash=str(raw.get("rawHash") or "disk"),
            confidence=float(raw.get("confidence") or 0.7),
            source=str(raw.get("source") or "ocr"),
        )
    except (TypeError, ValueError):
        return None
