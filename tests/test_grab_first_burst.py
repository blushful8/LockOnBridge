"""Grab-first burst: snapshot buffer, then OCR offline."""

from __future__ import annotations

from PIL import Image

from lockon_bridge.ocr_parse import BattleReport
from lockon_bridge.report_store import ReportStore
from lockon_bridge.runtime import BridgeRuntime, RuntimeConfig
from lockon_bridge.settle import LeanRoiFrameGate


def _frame(color: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGB", (64, 48), color)


class _FakeWorker:
    def start(self) -> None:
        return None

    def close(self) -> None:
        return None


def _patch_stable_pixels(monkeypatch) -> None:
    def always_stable(self, frame):
        self.last_mae = 0.0
        self.stable_count = self.cfg.stable_required
        self.last_sig = Image.new("L", (8, 8), 0)
        return True

    monkeypatch.setattr(LeanRoiFrameGate, "observe", always_stable)


def test_grab_first_settles_from_buffer_without_live_window(monkeypatch, tmp_path):
    """OCR runs on buffered frames even if later grabs would fail (WT closed)."""
    grabs = [_frame((10, 20, 30)), _frame((11, 21, 31)), _frame((12, 22, 32))]
    grab_calls = {"n": 0}

    def fake_grab(*, focus=False, require_foreground=True):
        i = grab_calls["n"]
        grab_calls["n"] += 1
        return grabs[i] if i < len(grabs) else None

    ocr_calls: list[int] = []

    def fake_ocr(frame, **kwargs):
        ocr_calls.append(id(frame))
        n = len(ocr_calls)
        if n == 1:
            return b"", []
        return b"png", [("roi:calib-p0", "Без преміума 900 5000")]

    published: list[BattleReport] = []

    monkeypatch.setattr("lockon_bridge.runtime.grab_wt_client_image", fake_grab)
    monkeypatch.setattr("lockon_bridge.runtime.ocr_saved_frame", fake_ocr)
    monkeypatch.setattr("lockon_bridge.ocr_isolate.PersistentOcrWorker", _FakeWorker)
    monkeypatch.setattr(
        "lockon_bridge.runtime.last_capture_meta",
        lambda: {"pair_index": 0, "prefer_with": False},
    )
    _patch_stable_pixels(monkeypatch)

    monkeypatch.setattr(
        "lockon_bridge.paths.data_root",
        lambda: tmp_path,
    )

    store = ReportStore()
    orig_publish = store.publish

    def track_publish(report):
        published.append(report)
        return orig_publish(report)

    store.publish = track_publish  # type: ignore[method-assign]

    rt = BridgeRuntime(
        RuntimeConfig(frames=3, grab_frame_gap=0.0, capture_delay_sec=0.0)
    )
    rt.store = store
    rt._capture_burst()

    assert grab_calls["n"] == 3
    assert len(ocr_calls) >= 2
    assert published, "expected a published report from buffer OCR"
    assert published[0].research_points == 900
    assert published[0].silver_lions == 5000


def test_grab_first_stops_early_after_settle(monkeypatch, tmp_path):
    """Do not OCR remaining buffer frames once settle publishes."""
    pool = [_frame((1, 1, 1)) for _ in range(5)]

    def fake_grab(*, focus=False, require_foreground=True):
        return pool.pop(0) if pool else None

    ocr_n = {"n": 0}

    def fake_ocr(frame, **kwargs):
        ocr_n["n"] += 1
        return b"png", [("roi:calib-p1", "Без преміума 111 2222")]

    monkeypatch.setattr("lockon_bridge.runtime.grab_wt_client_image", fake_grab)
    monkeypatch.setattr("lockon_bridge.runtime.ocr_saved_frame", fake_ocr)
    monkeypatch.setattr("lockon_bridge.ocr_isolate.PersistentOcrWorker", _FakeWorker)
    monkeypatch.setattr(
        "lockon_bridge.runtime.last_capture_meta",
        lambda: {"pair_index": 1, "prefer_with": False},
    )
    monkeypatch.setattr("lockon_bridge.paths.data_root", lambda: tmp_path)
    _patch_stable_pixels(monkeypatch)

    rt = BridgeRuntime(
        RuntimeConfig(frames=5, grab_frame_gap=0.0, capture_delay_sec=0.0)
    )
    rt._capture_burst()
    # First OCR starts the streak; the next identical decode publishes and stops.
    assert ocr_n["n"] == 2


def test_six_different_shots_are_not_published(monkeypatch, tmp_path):
    pool = [_frame((i, 0, 0)) for i in range(6)]

    def fake_grab(*, focus=False, require_foreground=True):
        return pool.pop(0) if pool else None

    ocr_n = {"n": 0}

    def fake_ocr(frame, **kwargs):
        ocr_n["n"] += 1
        n = ocr_n["n"]
        return b"png", [("roi:calib-p1", f"Без преміума {100 + n} {5000 + n}")]

    monkeypatch.setattr("lockon_bridge.runtime.grab_wt_client_image", fake_grab)
    monkeypatch.setattr("lockon_bridge.runtime.ocr_saved_frame", fake_ocr)
    monkeypatch.setattr("lockon_bridge.ocr_isolate.PersistentOcrWorker", _FakeWorker)
    monkeypatch.setattr("lockon_bridge.runtime.last_capture_meta", lambda: None)
    monkeypatch.setattr("lockon_bridge.paths.data_root", lambda: tmp_path)
    _patch_stable_pixels(monkeypatch)

    published: list[BattleReport] = []
    rt = BridgeRuntime(RuntimeConfig(frames=6, grab_frame_gap=0.0, capture_delay_sec=0.0))
    rt.store.publish = lambda report: published.append(report) or True  # type: ignore[method-assign]
    rt._capture_burst()
    assert published == []


def test_neighbor_match_publishes_and_stops(monkeypatch, tmp_path):
    from lockon_bridge.runtime import consecutive_reward_verdict

    assert consecutive_reward_verdict(
        [(1, 2), (1, 2)],
        final=False,
    ) == ("success", (1, 2))
    series = [(10, 20), (11, 21), (12, 22), (13, 23), (14, 24), (15, 25)]
    assert consecutive_reward_verdict(series, final=True)[0] == "failure"
    assert consecutive_reward_verdict([(1, 1), (2, 2)], final=False)[0] == "continue"


def test_default_shot_gaps_stretch_the_tail():
    from lockon_bridge.runtime import shot_gap_after

    assert [shot_gap_after(i) for i in range(5)] == [0.5, 0.65, 0.65, 1.0, 2.0]
    assert shot_gap_after(0, frames=6, uniform=0.0) == 0.0
