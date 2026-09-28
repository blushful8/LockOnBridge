"""Battle id the phone uses to attach an OCR report to the right match.

A new UUID appears when the hangar gives way to a battle. That same id stays
on the results screen and on the published report. It is replaced only when
the next battle starts. Ending a battle clears ``active`` and keeps the id,
so a report can still be published after the phone has already left the match.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, replace

from .ocr_parse import BattleReport

log = logging.getLogger("lockon_bridge")


@dataclass(frozen=True)
class MatchSessionView:
    session_id: str
    active: bool

    def to_json(self) -> dict[str, str | bool]:
        return {"sessionId": self.session_id, "active": self.active}


class MatchSession:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session_id = ""
        self._active = False

    def view(self) -> MatchSessionView:
        with self._lock:
            return MatchSessionView(self._session_id, self._active)

    def current_id(self) -> str:
        with self._lock:
            return self._session_id

    def begin(self) -> str:
        """Hangar → battle. This is the only place a new id is issued."""
        new_id = str(uuid.uuid4())
        with self._lock:
            self._session_id = new_id
            self._active = True
        log.info("battle session started session=%s", new_id)
        return new_id

    def finish_results(self) -> None:
        """Battle is over. Keep the id so a late OCR report still matches."""
        with self._lock:
            if not self._active:
                return
            self._active = False
            sid = self._session_id
        if sid:
            log.info("battle session idle session=%s", sid)

    def stamp(self, report: BattleReport) -> BattleReport:
        """Put this battle's id on an OCR report that does not already have one."""
        current = self.current_id()
        if not current or (report.session_id or "").strip():
            return report
        return replace(report, session_id=current)
