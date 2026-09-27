from __future__ import annotations

import json
from pathlib import Path

from lockon_bridge.ocr_parse import BattleReport
from lockon_bridge.report_store import MAX_REPORTS, ReportStore


def _report(rp: int, sl: int, captured: int, raw_hash: str) -> BattleReport:
    return BattleReport(
        captured_at_epoch_millis=captured,
        research_points=rp,
        silver_lions=sl,
        outcome="defeat",
        raw_hash=raw_hash,
        confidence=0.75,
    )


def test_ring_buffer_keeps_ten_newest(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("lockon_bridge.report_store.data_root", lambda: tmp_path)
    store = ReportStore()
    for index in range(12):
        assert store.publish(_report(100 + index, 1000 + index, 1_000 + index, f"h{index}"))
    reports = store.list_reports()
    assert len(reports) == MAX_REPORTS
    assert reports[0].research_points == 111
    assert reports[-1].research_points == 102
    assert all(r.id for r in reports)

    disk = json.loads((tmp_path / "reports.json").read_text(encoding="utf-8"))
    assert len(disk["reports"]) == MAX_REPORTS
    latest = json.loads((tmp_path / "last_report.json").read_text(encoding="utf-8"))
    assert latest["researchPoints"] == 111
    assert latest["id"]


def test_dedupe_same_hash(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("lockon_bridge.report_store.data_root", lambda: tmp_path)
    store = ReportStore()
    assert store.publish(_report(1708, 15902, 100, "same"))
    assert store.publish(_report(1708, 15902, 200, "same")) is False
    assert len(store.list_reports()) == 1


def test_replace_provisional_by_id(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("lockon_bridge.report_store.data_root", lambda: tmp_path)
    store = ReportStore()
    first = BattleReport(
        captured_at_epoch_millis=100,
        research_points=300,
        silver_lions=1200,
        outcome="undecided",
        raw_hash="prov1",
        confidence=0.9,
        provisional=True,
        session_id="sessaa",
    )
    assert store.publish(first)
    rid = store.latest().id  # type: ignore[union-attr]
    final = BattleReport(
        captured_at_epoch_millis=200,
        research_points=350,
        silver_lions=1500,
        outcome="victory",
        raw_hash="final1",
        confidence=0.98,
        provisional=False,
        session_id="sessaa",
    )
    assert store.replace_by_id(rid, final)
    latest = store.latest()
    assert latest is not None
    assert latest.id == rid
    assert latest.research_points == 350
    assert latest.outcome == "victory"
    assert latest.provisional is False
    assert latest.session_id == "sessaa"
