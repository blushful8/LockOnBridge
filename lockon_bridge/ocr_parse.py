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


# Labels are intentionally loose — Windows OCR often swaps letters (rn→m, etc.).
_RP_LABEL = re.compile(
    r"(?:"
    r"research\s*points?"
    r"|researc\w*\s*point\w*"
    r"|points?\s*(?:of\s*)?research"
    r"|очк(?:и|ов|а)?\s*исслед\w*"
    r"|исслед\w*"
    r"|очк(?:и|ів|а)?\s*дослідж\w*"
    r"|дослідж\w*"
    r"|\br\.?\s*p\.?\b"
    r"|\brp\b"
    r")",
    re.IGNORECASE,
)
_SL_LABEL = re.compile(
    r"(?:"
    r"silver\s*lions?"
    r"|silve\w*\s*lion\w*"
    r"|серебрян\w*\s*льв\w*"
    r"|серебрян\w*"
    r"|срібн\w*\s*лев\w*"
    r"|срібн\w*"
    r"|льв(?:ы|ів|и)?"
    r"|\bs\.?\s*l\.?\b"
    r"|\bsl\b"
    r")",
    re.IGNORECASE,
)
_TOTAL = re.compile(
    r"\btotal\b|итого|разом|підсумок|итог\w*|reward\s*total|всего",
    re.IGNORECASE,
)
# Thousand separators allowed (1 250 / 1,250 / 1.250) but not gluing two values across a wide gap.
_AMOUNT = re.compile(
    r"([+\-]?\s*(?:\d{1,3}(?:[\s.,'\u00A0]\d{3})+|\d+))"
)

_VICTORY = re.compile(r"\b(?:victory|win|перемога|победа)\b", re.IGNORECASE)
_DEFEAT = re.compile(r"\b(?:defeat|loss|поразка|поражение)\b", re.IGNORECASE)

# Typical post-battle totals are rarely tiny; filter noise like "1" from ranks.
_MIN_REWARD = 10


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
        window = text[match.end() : match.end() + 64]
        am = _AMOUNT.search(window)
        if am:
            value = _to_int(am.group(1))
            if value is not None:
                return value
        window_before = text[max(0, match.start() - 64) : match.start()]
        ams = list(_AMOUNT.finditer(window_before))
        if ams:
            value = _to_int(ams[-1].group(1))
            if value is not None:
                return value
    return None


def _pair_from_total_block(text: str) -> tuple[int | None, int | None]:
    """
    Results screen often has a Total row / block with RP then SL as the two largest figures.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for index, line in enumerate(lines):
        if not _TOTAL.search(line):
            continue
        chunk = "\n".join(lines[index : index + 4])
        amounts = [_to_int(m.group(1)) for m in _AMOUNT.finditer(chunk)]
        amounts = [a for a in amounts if a is not None and a >= _MIN_REWARD]
        if len(amounts) >= 2:
            return amounts[0], amounts[1]
        if len(amounts) == 1:
            # Single total on the line — try next line for the second currency.
            return amounts[0], None
    return None, None


def _pair_from_label_columns(text: str) -> tuple[int | None, int | None]:
    """
    Some OCR dumps put labels and numbers on adjacent lines:
      Research Points
      1 250
      Silver Lions
      8 400
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    rp = sl = None
    for index, line in enumerate(lines):
        if rp is None and _RP_LABEL.search(line):
            inline = _amount_near_label(line, _RP_LABEL)
            if inline is not None:
                rp = inline
            elif index + 1 < len(lines):
                nxt = _to_int(lines[index + 1]) if _AMOUNT.fullmatch(lines[index + 1].strip()) else None
                if nxt is None:
                    am = _AMOUNT.search(lines[index + 1])
                    nxt = _to_int(am.group(1)) if am else None
                if nxt is not None:
                    rp = nxt
        if sl is None and _SL_LABEL.search(line):
            inline = _amount_near_label(line, _SL_LABEL)
            if inline is not None:
                sl = inline
            elif index + 1 < len(lines):
                am = _AMOUNT.search(lines[index + 1])
                if am:
                    sl = _to_int(am.group(1))
    return rp, sl


def parse_rewards_from_ocr_text(text: str) -> BattleReport | None:
    cleaned = text.replace("\r", "\n")
    rp = _amount_near_label(cleaned, _RP_LABEL)
    sl = _amount_near_label(cleaned, _SL_LABEL)

    if rp is None or sl is None:
        col_rp, col_sl = _pair_from_label_columns(cleaned)
        rp = rp if rp is not None else col_rp
        sl = sl if sl is not None else col_sl

    if rp is None or sl is None:
        tot_rp, tot_sl = _pair_from_total_block(cleaned)
        rp = rp if rp is not None else tot_rp
        sl = sl if sl is not None else tot_sl

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


def summarize_ocr_text(text: str, limit: int = 400) -> str:
    """One-line preview for logs (keeps enough context to tune the parser)."""
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= limit:
        return flat or "(empty OCR)"
    return flat[: limit - 1] + "…"
