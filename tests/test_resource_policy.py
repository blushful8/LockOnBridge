"""Idle / in-battle resource policy."""

from lockon_bridge.process_watch import is_war_thunder_running, war_thunder_pids
from lockon_bridge.settings import BridgeSettings
from lockon_bridge.runtime import RuntimeConfig


def test_idle_default_is_two_minutes():
    s = BridgeSettings()
    assert s.idle_poll_sec == 120.0


def test_idle_soft_migrates_old_30s_default():
    s = BridgeSettings.from_dict({"idle_poll_sec": 30.0})
    assert s.idle_poll_sec == 120.0


def test_battle_poll_slower_than_hangar():
    cfg = RuntimeConfig()
    assert cfg.poll_battle_sec >= cfg.poll_hangar_sec
    assert cfg.poll_hangar_sec >= 1.5
    assert cfg.poll_battle_sec >= 2.5


def test_process_pid_cache_hits(monkeypatch):
    calls = {"n": 0}

    def fake_iter(_attrs):
        calls["n"] += 1
        return iter([])

    monkeypatch.setattr("lockon_bridge.process_watch.psutil.process_iter", fake_iter)
    import lockon_bridge.process_watch as pw

    pw._pids_cache = None
    assert war_thunder_pids() == []
    assert war_thunder_pids() == []
    assert calls["n"] == 1  # second call served from cache
    assert is_war_thunder_running() is False
    assert calls["n"] == 1
