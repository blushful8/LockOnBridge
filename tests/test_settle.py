"""Reward count-up settle tracker — do not publish mid-animation totals."""

from lockon_bridge.ocr_parse import BattleReport
from lockon_bridge.settle import SettleTracker, looks_like_countup, pairs_near


def _report(rp: int, sl: int, *, conf: float = 0.9) -> BattleReport:
    return BattleReport(
        captured_at_epoch_millis=1,
        research_points=rp,
        silver_lions=sl,
        outcome="undecided",
        raw_hash=f"{rp}-{sl}",
        confidence=conf,
    )


def test_pairs_near_allows_small_ocr_jitter():
    assert pairs_near((489, 1637), (490, 1640))
    assert not pairs_near((489, 1637), (520, 1800))


def test_looks_like_countup_detects_rising_totals():
    assert looks_like_countup((489, 1637), (520, 1800))
    assert looks_like_countup((489, 1637), (489, 1900))
    assert not looks_like_countup((520, 1800), (489, 1637))


def test_tracker_does_not_publish_first_frame():
    tracker = SettleTracker()
    assert tracker.observe(_report(489, 1637)) is None
    assert tracker.stable_count == 1


def test_tracker_publishes_after_two_stable_frames():
    tracker = SettleTracker()
    assert tracker.observe(_report(560, 2100)) is None
    settled = tracker.observe(_report(560, 2100))
    assert settled is not None
    assert (settled.research_points, settled.silver_lions) == (560, 2100)


def test_tracker_waits_out_countup_then_settles():
    """Mid-animation 489/1637 must not win over the final settled pair."""
    tracker = SettleTracker()
    assert tracker.observe(_report(489, 1637)) is None
    assert tracker.observe(_report(520, 1800)) is None  # still rising
    assert tracker.observe(_report(548, 2050)) is None
    assert tracker.observe(_report(560, 2100)) is None
    settled = tracker.observe(_report(560, 2100))
    assert settled is not None
    assert (settled.research_points, settled.silver_lions) == (560, 2100)


def test_finalize_returns_best_if_screen_closes_early():
    tracker = SettleTracker()
    tracker.observe(_report(489, 1637))
    tracker.observe(_report(520, 1800))
    fallback = tracker.finalize()
    assert fallback is not None
    assert (fallback.research_points, fallback.silver_lions) == (520, 1800)
