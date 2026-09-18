from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class BattleReport:
    captured_at_epoch_millis: int
    research_points: int
    silver_lions: int
    outcome: str
    raw_hash: str
    confidence: float
    source: str = "ocr"

    def to_json(self) -> dict[str, Any]:
        return {
            "capturedAtEpochMillis": self.captured_at_epoch_millis,
            "researchPoints": self.research_points,
            "silverLions": self.silver_lions,
            "outcome": self.outcome,
            "rawHash": self.raw_hash,
            "confidence": self.confidence,
            "source": self.source,
        }


_RP_LABEL = re.compile(
    r"(?:research\s*points?|очк(?:и|ов)?\s*исследован|досліджен|рп\b|\brp\b)",
    re.IGNORECASE,
)
_SL_LABEL = re.compile(
    r"(?:silver\s*lions?|серебрян(?:ые|ых)?\s*льв|срібн|сл\b|\bsl\b)",
    re.IGNORECASE,
)
_TOTAL = re.compile(r"\btotal\b|итого|разом|підсумок", re.IGNORECASE)
_AMOUNT = re.compile(r"([+\-]?\s*\d[\d\s.,'\u00A0]*)")

_VICTORY = re.compile(r"\b(?:victory|win|перемога|победа)\b", re.IGNORECASE)
_DEFEAT = re.compile(r"\b(?:defeat|loss|поразка|поражение)\b", re.IGNORECASE)


def _to_int(raw: str) -> int | None:
    digits = re.sub(r"[^\d]", "", raw)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _amount_near_label(text: str, label: re.Pattern[str]) -> int | None:
    """Prefer the amount immediately before the label on the same line; else after it."""
    for line in text.splitlines():
        match = label.search(line)
        if not match:
            continue
        before = list(_AMOUNT.finditer(line[: match.start()]))
        if before:
            value = _to_int(before[-1].group(1))
            if value is not None:
                return value
        after = _AMOUNT.search(line[match.end() :])
        if after:
            value = _to_int(after.group(1))
            if value is not None:
                return value
    for match in label.finditer(text):
        window = text[match.end() : match.end() + 48]
        am = _AMOUNT.search(window)
        if am:
            value = _to_int(am.group(1))
            if value is not None:
                return value
        window_before = text[max(0, match.start() - 48) : match.start()]
        ams = list(_AMOUNT.finditer(window_before))
        if ams:
            value = _to_int(ams[-1].group(1))
            if value is not None:
                return value
    return None


def parse_rewards_from_ocr_text(text: str) -> BattleReport | None:
    cleaned = text.replace("\r", "\n")
    rp = _amount_near_label(cleaned, _RP_LABEL)
    sl = _amount_near_label(cleaned, _SL_LABEL)

    if (rp is None or sl is None) and _TOTAL.search(cleaned):
        for line in cleaned.splitlines():
            if not _TOTAL.search(line):
                continue
            amounts = [_to_int(m.group(1)) for m in _AMOUNT.finditer(line)]
            amounts = [a for a in amounts if a is not None and a > 0]
            if len(amounts) >= 2:
                if rp is None:
                    rp = amounts[0]
                if sl is None:
                    sl = amounts[1]
            elif len(amounts) == 1:
                if rp is None and _RP_LABEL.search(line):
                    rp = amounts[0]
                if sl is None and _SL_LABEL.search(line):
                    sl = amounts[0]

    if rp is None and sl is None:
        return None

    outcome = "undecided"
    if _VICTORY.search(cleaned):
        outcome = "victory"
    elif _DEFEAT.search(cleaned):
        outcome = "defeat"

    digest = hashlib.sha256(cleaned.encode("utf-8", errors="ignore")).hexdigest()[:32]
    confidence = 0.55
    if rp is not None:
        confidence += 0.2
    if sl is not None:
        confidence += 0.2
    if _TOTAL.search(cleaned) or (_RP_LABEL.search(cleaned) and _SL_LABEL.search(cleaned)):
        confidence += 0.05

    return BattleReport(
        captured_at_epoch_millis=int(time.time() * 1000),
        research_points=max(0, rp or 0),
        silver_lions=max(0, sl or 0),
        outcome=outcome,
        raw_hash=digest,
        confidence=min(1.0, confidence),
    )
