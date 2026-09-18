from __future__ import annotations

import threading
from typing import Optional

from .ocr_parse import BattleReport


class ReportStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: Optional[BattleReport] = None
        self._seen_hashes: set[str] = set()

    def publish(self, report: BattleReport) -> bool:
        """Returns True when the report is new (not a duplicate hash)."""
        with self._lock:
            if report.raw_hash in self._seen_hashes:
                return False
            self._seen_hashes.add(report.raw_hash)
            # Bound memory for long sessions
            if len(self._seen_hashes) > 64:
                self._seen_hashes = set(list(self._seen_hashes)[-32:])
            self._latest = report
            return True

    def latest(self) -> Optional[BattleReport]:
        with self._lock:
            return self._latest
