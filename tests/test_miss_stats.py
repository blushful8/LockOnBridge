from lockon_bridge.miss_stats import normalize_reason, record_miss, snapshot


def test_normalize_reason_strips_noise():
    assert normalize_reason(" Gate: Preliminary ") == "gate:-preliminary"
    assert normalize_reason("") == "unknown"


def test_record_miss_increments(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lockon_bridge.miss_stats.data_root",
        lambda: tmp_path,
    )
    first = record_miss("empty-ocr", session_id="abc")
    assert first["total"] == 1
    assert first["byReason"]["empty-ocr"] == 1
    second = record_miss("empty-ocr", session_id="def")
    assert second["total"] == 2
    assert second["byReason"]["empty-ocr"] == 2
    assert snapshot()["recent"][0]["sessionId"] == "def"
