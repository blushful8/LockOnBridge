"""Scale-safe digit ROI tests — with/without-premium + Всього cells."""

from pathlib import Path

from PIL import Image

from lockon_bridge.ocr_parse import choose_best_report, parse_rewards_from_ocr_text
from lockon_bridge.roi_layout import (
    TOTAL_DIGIT_ROIS,
    WITH_DIGIT_ROIS,
    WITHOUT_DIGIT_ROIS,
    content_frame,
    crop_norm,
    iter_reward_digit_rois,
)
from lockon_bridge.roi_rewards import (
    extract_roi_reward_variants,
    pair_from_digit_text,
    report_from_roi_image,
)

FIXTURES = Path(__file__).parent / "fixtures"
FULL = FIXTURES / "results_uk_full_1025_7262.jpg"
CROPPED = FIXTURES / "results_uk_1025_7262.jpg"
LAST_BATTLE = FIXTURES / "last_battle_capture.png"


def _load(path: Path) -> Image.Image:
    assert path.is_file(), f"missing fixture: {path}"
    return Image.open(path).convert("RGB")


def test_content_frame_keeps_normal_aspect():
    img = Image.new("RGB", (1920, 1080), (0, 0, 0))
    assert content_frame(img) == (0, 0, 1920, 1080)


def test_content_frame_letterboxes_ultrawide_only():
    img = Image.new("RGB", (3440, 1440), (0, 0, 0))
    left, top, right, bottom = content_frame(img)
    assert top == 0 and bottom == 1440
    assert right - left == int(round(1440 * 16 / 9))
    assert left > 0


def test_rois_are_fractions_not_pixels():
    for rect in (*WITH_DIGIT_ROIS, *WITHOUT_DIGIT_ROIS, *TOTAL_DIGIT_ROIS):
        assert 0.0 <= rect.left < rect.right <= 1.0
        assert 0.0 <= rect.top < rect.bottom <= 1.0


def test_amount_regex_does_not_glue_icon_ghost_into_next_rp():
    """``4219 276`` must not become ``9276`` (trailing icon 9 + next RP)."""
    from lockon_bridge.ocr_parse import _amounts_in

    assert _amounts_in("548 2 4219 276 1 2469") == [548, 2421, 276, 1246]
    assert pair_from_digit_text("548 2 4219 276 1 2469") == (548, 2421)
    without = parse_rewards_from_ocr_text(
        "З преміумом 548 2 4219 Без преміума 276 1 2469",
        prefer_premium_rewards=False,
    )
    assert without is not None
    assert (without.research_points, without.silver_lions) == (276, 1246)
    with_prem = parse_rewards_from_ocr_text(
        "З преміумом 548 2 4219 Без преміума 276 1 2469",
        prefer_premium_rewards=True,
    )
    assert with_prem is not None
    assert (with_prem.research_points, with_prem.silver_lions) == (548, 2421)


def test_crop_norm_scales_with_resolution():
    small = Image.new("RGB", (800, 600), (10, 10, 10))
    large = Image.new("RGB", (3840, 2160), (10, 10, 10))
    rect = WITHOUT_DIGIT_ROIS[0]
    a = crop_norm(small, rect)
    b = crop_norm(large, rect)
    assert a is not None and b is not None
    # Larger frame → larger crop (fractional geometry, not fixed pixels).
    assert b.width > a.width * 3
    assert b.height > a.height * 2
    # Relative position stays in the same band of the frame.
    assert abs(a.width / small.width - b.width / large.width) < 0.02


def test_pair_from_digit_text_deglues_trailing():
    assert pair_from_digit_text("1 025 7 262") == (1025, 7262)
    assert pair_from_digit_text("1025 72620") == (1025, 7262)
    assert pair_from_digit_text("10259 72620") == (1025, 7262)
    assert pair_from_digit_text("1025 72629") == (1025, 7262)


def test_pair_from_digit_text_keeps_high_premium_farm():
    """Real 15k RP / 100k+ SL must not be chopped by OCR-ghost deglue."""
    assert pair_from_digit_text("15000 108920") == (15000, 108920)
    assert pair_from_digit_text("15000 100000") == (15000, 100000)
    assert pair_from_digit_text("15 000 126500") == (15000, 126500)
    assert pair_from_digit_text("15200 126500") == (15200, 126500)
    # Round thousands ending in 00 stay intact (not treated as 72620-style ghosts).
    assert pair_from_digit_text("15000 28000") == (15000, 28000)


def test_full_frame_consensus_1025_7262():
    img = _load(FULL)
    tags = {tag for tag, _ in iter_reward_digit_rois(img)}
    assert "total-digits" in tags
    assert any(t.startswith("without-digits") for t in tags)

    report = report_from_roi_image(img, prefer_with=False)
    assert report is not None
    assert report.research_points == 1025
    assert report.silver_lions == 7262
    assert report.source == "ocr-roi"
    assert report.confidence >= 0.90


def test_full_frame_rejects_premium_sl():
    """Must not land on premium SL (10892) when without-premium is 7262."""
    img = _load(FULL)
    variants = extract_roi_reward_variants(img, prefer_with=False)
    assert any(tag == "roi:consensus" for tag, _ in variants) or any(
        "7262" in text for _tag, text in variants
    )
    candidates = [
        (text, parse_rewards_from_ocr_text(text, prefer_premium_rewards=False))
        for _tag, text in variants
    ]
    best = choose_best_report(candidates)
    assert best is not None
    _text, report = best
    assert report.research_points == 1025
    assert report.silver_lions == 7262
    assert report.silver_lions != 10892


def test_cropped_chat_fixture_still_resolves():
    """Older chat-cropped panel should still resolve via total ROI fallback."""
    if not CROPPED.is_file():
        return
    img = _load(CROPPED)
    report = report_from_roi_image(img, prefer_with=False)
    assert report is not None
    assert report.research_points == 1025
    assert report.silver_lions == 7262


def test_full_client_left_panel_without_premium():
    """Live WT client: premium table is on the left, not the mid-panel chat band."""
    if not LAST_BATTLE.is_file():
        return
    img = _load(LAST_BATTLE)
    report = report_from_roi_image(img, prefer_with=False)
    assert report is not None
    assert report.research_points == 276
    assert report.silver_lions == 1246
    assert report.source == "ocr-roi"


def test_full_client_left_panel_with_premium():
    if not LAST_BATTLE.is_file():
        return
    img = _load(LAST_BATTLE)
    report = report_from_roi_image(img, prefer_with=True)
    assert report is not None
    assert report.research_points == 548
    assert report.silver_lions == 2421
    assert report.source == "ocr-roi"
