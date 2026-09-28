"""Battle session id stays from hangar entry through the published report."""

from __future__ import annotations

import json
import threading
from urllib.request import urlopen

from lockon_bridge.ocr_parse import BattleReport
from lockon_bridge.report_store import ReportStore
from lockon_bridge.runtime import BridgeRuntime, RuntimeConfig
from lockon_bridge.server import serve


def _report(raw_hash: str, session_id: str = "") -> BattleReport:
    return BattleReport(
        captured_at_epoch_millis=1_000,
        research_points=559,
        silver_lions=2313,
        outcome="defeat",
        raw_hash=raw_hash,
        confidence=0.9,
        session_id=session_id,
    )


def test_session_id_survives_battle_end_and_changes_only_on_the_next_battle(tmp_path, monkeypatch):
    monkeypatch.setattr("lockon_bridge.report_store.data_root", lambda: tmp_path)
    rt = BridgeRuntime(RuntimeConfig(frames=1, grab_frame_gap=0.0, capture_delay_sec=0.0))

    assert rt.match.view().to_json() == {"sessionId": "", "active": False}
    assert rt._battle_just_ended(False) is False

    assert rt._battle_just_ended(True) is False
    first = rt.match.current_id()
    assert first
    assert rt.match.view().to_json() == {"sessionId": first, "active": True}

    assert rt._battle_just_ended(True) is False
    assert rt.match.current_id() == first

    assert rt._battle_just_ended(False) is True
    assert rt.match.current_id() == first
    assert rt.match.view().active is True

    stored = rt.store.publish(_report("battle-1"))
    assert stored
    published = rt.store.latest()
    assert published is not None
    assert published.session_id == first

    rt.match.finish_results()
    assert rt.match.view().to_json() == {"sessionId": first, "active": False}

    assert rt._battle_just_ended(True) is False
    second = rt.match.current_id()
    assert second and second != first
    assert rt.match.view().active is True


def test_existing_session_text_is_not_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr("lockon_bridge.report_store.data_root", lambda: tmp_path)
    rt = BridgeRuntime(RuntimeConfig())
    rt.match.begin()
    assert rt.store.publish(_report("hex", session_id="744a9c1e2f00"))
    latest = rt.store.latest()
    assert latest is not None
    assert latest.session_id == "744a9c1e2f00"


def test_session_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr("lockon_bridge.report_store.data_root", lambda: tmp_path)
    store = ReportStore()
    from lockon_bridge.match_session import MatchSession

    session = MatchSession()
    server = serve(store, host="127.0.0.1", port=0, session=session)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        with urlopen(f"http://127.0.0.1:{port}/v1/session", timeout=2) as response:
            assert response.status == 200
            assert json.loads(response.read().decode("utf-8")) == {
                "sessionId": "",
                "active": False,
            }
        sid = session.begin()
        with urlopen(f"http://127.0.0.1:{port}/v1/session", timeout=2) as response:
            assert json.loads(response.read().decode("utf-8")) == {
                "sessionId": sid,
                "active": True,
            }
        session.finish_results()
        with urlopen(f"http://127.0.0.1:{port}/v1/session", timeout=2) as response:
            assert json.loads(response.read().decode("utf-8")) == {
                "sessionId": sid,
                "active": False,
            }
    finally:
        server.shutdown()
        server.server_close()
