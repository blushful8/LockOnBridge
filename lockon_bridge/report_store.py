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
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: Optional[BattleReport] = None
        self._seen_hashes: set[str] = set()
        self._load_disk()

    def _load_disk(self) -> None:
        path = _report_path()
        if not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            report = _report_from_json(raw)
            if report is not None:
                self._latest = report
                self._seen_hashes.add(report.raw_hash)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            log.debug("Could not load last_report.json: %s", exc)

    def _save_disk(self, report: BattleReport) -> None:
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
        """Returns True when the report is new (not a duplicate hash)."""
        with self._lock:
            if report.raw_hash in self._seen_hashes:
                return False
            self._seen_hashes.add(report.raw_hash)
            if len(self._seen_hashes) > 64:
                self._seen_hashes = set(list(self._seen_hashes)[-32:])
            self._latest = report
            self._save_disk(report)
            return True

    def latest(self) -> Optional[BattleReport]:
        with self._lock:
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
