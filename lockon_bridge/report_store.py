from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any, Optional

from .ocr_parse import BattleReport
from .paths import data_root

log = logging.getLogger("lockon_bridge")

MAX_REPORTS = 10


def _latest_path():
    return data_root() / "last_report.json"


def _reports_path():
    return data_root() / "reports.json"


class ReportStore:
    """
    Ring buffer of the last [MAX_REPORTS] OCR reports + latest pointer.

    Phone syncs the buffer on splash; live hangar-close still uses latest().
    Memory and disk stay in sync so HTTP never serves a stale in-memory value.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reports: list[BattleReport] = []
        self._seen_hashes: set[str] = set()
        self._load_disk_unlocked()

    def _load_disk_unlocked(self) -> None:
        loaded: list[BattleReport] = []
        path = _reports_path()
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                items = raw.get("reports") if isinstance(raw, dict) else raw
                if isinstance(items, list):
                    for item in items:
                        report = _report_from_json(item)
                        if report is not None:
                            loaded.append(report)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                log.debug("Could not load reports.json: %s", exc)

        if not loaded:
            legacy = _latest_path()
            if legacy.is_file():
                try:
                    raw = json.loads(legacy.read_text(encoding="utf-8"))
                    report = _report_from_json(raw)
                    if report is not None:
                        loaded = [report]
                except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    log.debug("Could not load last_report.json: %s", exc)

        self._reports = loaded[:MAX_REPORTS]
        self._seen_hashes = {r.raw_hash for r in self._reports if r.raw_hash}

    def _refresh_from_disk_unlocked(self) -> None:
        """Reload ring when disk has a newer head than memory."""
        path = _reports_path()
        if not path.is_file():
            # Fall back to single-file refresh for older installs mid-upgrade.
            legacy = _latest_path()
            if not legacy.is_file():
                return
            try:
                raw = json.loads(legacy.read_text(encoding="utf-8"))
                report = _report_from_json(raw)
                if report is None:
                    return
                if not self._reports or report.captured_at_epoch_millis > self._reports[0].captured_at_epoch_millis:
                    self._prepend_unlocked(report)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                return
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            items = raw.get("reports") if isinstance(raw, dict) else raw
            if not isinstance(items, list) or not items:
                return
            head = _report_from_json(items[0])
            if head is None:
                return
            current = self._reports[0] if self._reports else None
            if current is None or head.captured_at_epoch_millis > current.captured_at_epoch_millis:
                loaded: list[BattleReport] = []
                for item in items:
                    report = _report_from_json(item)
                    if report is not None:
                        loaded.append(report)
                self._reports = loaded[:MAX_REPORTS]
                self._seen_hashes = {r.raw_hash for r in self._reports if r.raw_hash}
                log.info(
                    "ReportStore: reloaded buffer head RP=%s SL=%s (n=%s)",
                    head.research_points,
                    head.silver_lions,
                    len(self._reports),
                )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return

    def _ensure_id(self, report: BattleReport) -> BattleReport:
        if report.id:
            return report
        return BattleReport(
            captured_at_epoch_millis=report.captured_at_epoch_millis,
            research_points=report.research_points,
            silver_lions=report.silver_lions,
            outcome=report.outcome,
            raw_hash=report.raw_hash,
            confidence=report.confidence,
            source=report.source,
            id=str(uuid.uuid4()),
        )

    def _prepend_unlocked(self, report: BattleReport) -> None:
        report = self._ensure_id(report)
        self._reports = [report] + [r for r in self._reports if r.raw_hash != report.raw_hash]
        self._reports = self._reports[:MAX_REPORTS]
        self._seen_hashes.add(report.raw_hash)
        if len(self._seen_hashes) > 64:
            self._seen_hashes = {r.raw_hash for r in self._reports}

    def _save_disk_unlocked(self) -> None:
        root = data_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
            payload = {"reports": [r.to_json() for r in self._reports]}
            _reports_path().write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if self._reports:
                _latest_path().write_text(
                    json.dumps(self._reports[0].to_json(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except OSError as exc:
            log.warning("Could not save report buffer: %s", exc)

    def publish(self, report: BattleReport) -> bool:
        """Prepend a new OCR report. Returns True when the buffer head changed."""
        with self._lock:
            self._refresh_from_disk_unlocked()
            report = self._ensure_id(report)
            if self._reports:
                head = self._reports[0]
                if report.raw_hash == head.raw_hash:
                    return False
                if report.captured_at_epoch_millis < head.captured_at_epoch_millis:
                    log.info(
                        "ReportStore: ignore older OCR RP=%s SL=%s (have newer)",
                        report.research_points,
                        report.silver_lions,
                    )
                    return False
            # Same hash already in buffer (not head) — move to front.
            if report.raw_hash in self._seen_hashes:
                existing = next((r for r in self._reports if r.raw_hash == report.raw_hash), None)
                if existing is not None:
                    self._reports = [existing] + [r for r in self._reports if r.raw_hash != report.raw_hash]
                    self._save_disk_unlocked()
                    return True
            self._prepend_unlocked(report)
            self._save_disk_unlocked()
            return True

    def latest(self) -> Optional[BattleReport]:
        with self._lock:
            self._refresh_from_disk_unlocked()
            return self._reports[0] if self._reports else None

    def list_reports(self) -> list[BattleReport]:
        with self._lock:
            self._refresh_from_disk_unlocked()
            return list(self._reports)


def _report_from_json(raw: Any) -> BattleReport | None:
    if not isinstance(raw, dict):
        return None
    try:
        rp = int(raw.get("researchPoints"))
        sl = int(raw.get("silverLions"))
        if rp < 0 or sl < 0:
            return None
        report_id = str(raw.get("id") or "").strip() or str(uuid.uuid4())
        return BattleReport(
            captured_at_epoch_millis=int(raw.get("capturedAtEpochMillis") or 0),
            research_points=rp,
            silver_lions=sl,
            outcome=str(raw.get("outcome") or "undecided"),
            raw_hash=str(raw.get("rawHash") or "disk"),
            confidence=float(raw.get("confidence") or 0.7),
            source=str(raw.get("source") or "ocr"),
            id=report_id,
        )
    except (TypeError, ValueError):
        return None
