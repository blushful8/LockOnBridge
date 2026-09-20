"""Reward count-up settle tracker — do not publish mid-animation totals."""

from PIL import Image, ImageDraw

from lockon_bridge.ocr_parse import BattleReport
from lockon_bridge.settle import (
    LeanRoiFrameGate,
    SettleTracker,
    lean_roi_signature,
    looks_like_countup,
    pairs_near,
    signature_mae,
    signatures_near,
)


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


def _fake_results_frame(*, digit_fill: int) -> Image.Image:
    """Synthetic full-client frame with solid blobs in calibrated lean ROI cells."""
    from lockon_bridge.roi_calib import load_calibrated_rois
    from lockon_bridge.roi_layout import pixel_box

    img = Image.new("RGB", (1920, 1080), (20, 20, 30))
    draw = ImageDraw.Draw(img)
    fill = (digit_fill, digit_fill, digit_fill)
    calib = load_calibrated_rois()
    if calib is not None:
        for col in (calib.with_premium, calib.without_premium):
            for rect in (col.rp, col.sl):
                box = pixel_box(img, rect, min_width=8, min_height=4)
                if box is not None:
                    draw.rectangle(box, fill=fill)
    else:
        # Catalogue fallback when calib file is absent.
        draw.rectangle((360, 160, 780, 290), fill=fill)
        draw.rectangle((960, 475, 1220, 520), fill=fill)
    return img


def test_lean_roi_signature_changes_when_digits_move():
    a = lean_roi_signature(_fake_results_frame(digit_fill=40))
    b = lean_roi_signature(_fake_results_frame(digit_fill=200))
    assert a is not None and b is not None
    assert signature_mae(a, b) > 10.0
    assert not signatures_near(a, b)


def test_frame_gate_waits_for_stable_pixels():
    gate = LeanRoiFrameGate()
    moving = _fake_results_frame(digit_fill=40)
    settled = _fake_results_frame(digit_fill=200)
    assert gate.observe(moving) is False
    assert gate.observe(_fake_results_frame(digit_fill=90)) is False  # still changing
    assert gate.observe(settled) is False  # first settled look
    assert gate.observe(settled) is True  # second identical → stable
