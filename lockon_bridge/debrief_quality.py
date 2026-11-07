"""Gates that decide whether a post-battle OCR read is safe to publish."""

from __future__ import annotations

import re

from .ocr_parse import BattleReport, vehicle_research_amount

# Boosters / "remaining rewards later" overlay — never a bankable debrief.
_DEFERRED_OVERLAY = re.compile(
    r"(?i)(?:"
    r"активн\w*\s*підсилювач|"
    r"активн\w*\s*усилител|"
    r"active\s*(?:booster|modifier)s?|"
    r"частина\s*нагород|"
    r"часть\s*награды|"
    r"will\s*be\s*(?:credited|awarded|granted)|"
    r"після\s*закін|"
    r"после\s*окончан|"
    r"after\s*(?:the\s*)?(?:battle|match)\s*ends"
    r")",
)

# Early leave / still-settling banner. Publish only when vehicle research is present.
_PRELIMINARY = re.compile(
    r"(?i)(?:"
    r"попередн\w*\s*результат|"
    r"предварительн\w*\s*результат|"
    r"preliminary\s*results?|"
    r"zwischenergebn|"
    r"r[eé]sultats?\s*pr[eé]liminaires?|"
    r"resultados?\s*preliminares?"
    r")",
)

_MIN_PUBLISH_CONFIDENCE = 0.65


def looks_like_deferred_rewards_overlay(text: str) -> bool:
    """True for the mid-screen boosters / deferred-rewards panel."""
    return bool(text and _DEFERRED_OVERLAY.search(text))


def looks_like_preliminary_results(text: str) -> bool:
    """True when OCR saw the preliminary-results banner."""
    return bool(text and _PRELIMINARY.search(text))


def report_is_publishable(
    text: str,
    report: BattleReport | None,
    *,
    prefer_with: bool = False,
) -> tuple[bool, str]:
    """
    Return (ok, reason). Reject overlays and unsettled preliminary screens so
    the phone does not bank junk under a real session id.
    """
    if report is None:
        return False, "no-report"
    if report.research_points <= 0 and report.silver_lions <= 0:
        return False, "empty-rewards"
    if report.confidence < _MIN_PUBLISH_CONFIDENCE:
        return False, "low-confidence"
    if looks_like_deferred_rewards_overlay(text):
        return False, "deferred-overlay"
    if looks_like_preliminary_results(text):
        vehicle = vehicle_research_amount(text, prefer_with=prefer_with)
        if vehicle is None:
            return False, "preliminary-no-vehicle"
        slack = max(5, vehicle // 50)
        if abs(report.research_points - vehicle) > slack:
            return False, "preliminary-rp-mismatch"
    return True, "ok"
