"""Publish gates: reject deferred overlays and unsettled preliminary screens."""

from __future__ import annotations

from lockon_bridge.debrief_quality import (
    looks_like_deferred_rewards_overlay,
    looks_like_preliminary_results,
    report_is_publishable,
)
from lockon_bridge.ocr_parse import BattleReport


def _report(
    rp: int = 948,
    sl: int = 2313,
    *,
    confidence: float = 0.9,
) -> BattleReport:
    return BattleReport(
        captured_at_epoch_millis=1,
        research_points=rp,
        silver_lions=sl,
        outcome="undecided",
        raw_hash="t",
        confidence=confidence,
    )


def test_deferred_overlay_detected_multi_lang():
    assert looks_like_deferred_rewards_overlay("Активні підсилювачі")
    assert looks_like_deferred_rewards_overlay("Active boosters remaining")
    assert looks_like_deferred_rewards_overlay("Часть награды will be credited")
    assert not looks_like_deferred_rewards_overlay("Без преміума 948 2313")


def test_preliminary_banner_detected_multi_lang():
    assert looks_like_preliminary_results("Попередні результати бою")
    assert looks_like_preliminary_results("Preliminary results")
    assert looks_like_preliminary_results("Предварительные результаты")
    assert not looks_like_preliminary_results("Результати бою\nБез преміума 948")


def test_publishable_rejects_deferred_and_empty():
    ok, reason = report_is_publishable(
        "Active boosters\nwill be credited after the battle ends",
        _report(),
    )
    assert not ok and reason == "deferred-overlay"

    ok, reason = report_is_publishable("ok", None)
    assert not ok and reason == "no-report"

    ok, reason = report_is_publishable("ok", _report(0, 0))
    assert not ok and reason == "empty-rewards"

    ok, reason = report_is_publishable("ok", _report(confidence=0.4))
    assert not ok and reason == "low-confidence"


def test_preliminary_needs_matching_vehicle_research():
    text = "Попередні результати\nДослідження техніки 948\nБез преміума 948 2313"
    # Header-like mismatch without a matching vehicle amount fails.
    ok, reason = report_is_publishable(
        "Preliminary results\nsome noise",
        _report(9478, 2313),
    )
    assert not ok and reason == "preliminary-no-vehicle"

    ok, reason = report_is_publishable(text, _report(948, 2313))
    assert ok and reason == "ok"


def test_clean_debrief_is_publishable():
    ok, reason = report_is_publishable(
        "Без преміума 948 2313\nДослідження техніки 948",
        _report(948, 2313),
    )
    assert ok and reason == "ok"
