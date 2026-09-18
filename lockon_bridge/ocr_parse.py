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


# Labels are intentionally loose — Windows OCR often mangles Cyrillic into Latin lookalikes.
_RP_LABEL = re.compile(
    r"(?:"
    r"research\s*points?"
    r"|researc\w*\s*point\w*"
    r"|points?\s*(?:of\s*)?research"
    r"|очк(?:и|ов|а)?\s*исслед\w*"
    r"|исслед\w*"
    r"|очк(?:и|ів|а)?\s*дослідж\w*"
    r"|дослідж\w*"
    # Latinized Ukrainian/Russian OCR garbage (avoid bare «nocnin…» — that is often «прогрес досліджень»)
    r"|ochk\w*\s*(?:issled|doslid|nocnin)\w*"
    r"|dos[l1i]id\w*"
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
    r"|sri[b6]\w*"
    r"|serebr\w*"
    r"|l[eе]v(?:y|i|iv)?"
    r"|\bs\.?\s*l\.?\b"
    r"|\bsl\b"
    r")",
    re.IGNORECASE,
)
_TOTAL = re.compile(
    r"\btotal\b|итого|разом|підсумок|итог\w*|reward\s*total|всего|bcboro|vsego",
    re.IGNORECASE,
)
# Thousand separators allowed (1 250 / 1,250 / 1.250 / 1'250). Trailing junk quote OK.
_AMOUNT = re.compile(
    r"([+\-]?\s*(?:\d{1,3}(?:[\s.,'\u00A0]\d{3})+|\d+))"
)

_VICTORY = re.compile(
    r"\b(?:victory|win|перемога|победа|peremoga)\b",
    re.IGNORECASE,
)
_DEFEAT = re.compile(
    r"\b(?:defeat|loss|поразка|поражение|npoBaneHa|провален|провал)\b",
    re.IGNORECASE,
)

# Ukrainian results screen columns (also Latinized by broken OCR).
# Require a "with/without" marker — bare "npeMiYMa" alone is ambiguous.
_WITH_PREMIUM = re.compile(
    r"(?:"
    r"з\s*преміум\w*"
    r"|с\s*премиум\w*"
    r"|with\s*premium"
    r"|[3zс]\s*npe?[mn]i?[yуu0о]?m\w*"
    r")",
    re.IGNORECASE,
)
_WITHOUT_PREMIUM = re.compile(
    r"(?:"
    r"без\s*преміум\w*"
    r"|без\s*премиум\w*"
    r"|without\s*premium"
    r"|w/?o\s*premium"
    r"|be[zs3]\s*npe?[mn]i?[yуu0о]?m\w*"
    r")",
    re.IGNORECASE,
)
# Second column header sometimes OCR'd as bare «npeMiYMa» after the with-premium amount.
_BARE_PREMIUM_WORD = re.compile(
    r"(?<![bezs3zс])\bnpe?[mn]i?[yуu0о]?m[aаeе]?\b",
    re.IGNORECASE,
)
_TOTAL_RESEARCH = re.compile(
    r"(?:"
    r"всього\s*дослід\w*"
    r"|всего\s*исслед\w*"
    r"|total\s*research"
    r"|bc[eo]ro\s*nocnin\w*"
    r"|vsego\s*(?:issled|nocnin)\w*"
    r")",
    re.IGNORECASE,
)

_DESKTOP_NOISE = re.compile(
    r"(?:telegram|nvidia\s*app|malware\s*protection|epic\s*games|"
    r"lockon\s*bridge|порт\s*http|nop[rt]\s*http|check\s*for\s*updates|"
    r"перевірити\s*оновлення|nepeeipnw\s*onoene)",
    re.IGNORECASE,
)
_WT_HINT = re.compile(
    r"(?:npe?[mn]i?[yуu]?m|преміум|premium|haropon|нагород|місі|"
    r"micifl|mission|research|silver|nocnin|дослід|war\s*thunder|"
    r"очк|льв|npoBaneHa|провал)",
    re.IGNORECASE,
)

_MIN_REWARD = 10


def looks_like_desktop_noise(text: str) -> bool:
    """True when OCR mostly captured the desktop / Bridge window, not WT results."""
    if not text:
        return True
    noise = len(_DESKTOP_NOISE.findall(text))
    hints = len(_WT_HINT.findall(text))
    return noise >= 2 and hints == 0


def _to_int(raw: str) -> int | None:
    digits = re.sub(r"[^\d]", "", raw)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _amounts_in(text: str, *, min_value: int = _MIN_REWARD) -> list[int]:
    values: list[int] = []
    for match in _AMOUNT.finditer(text):
        value = _to_int(match.group(1))
        if value is not None and value >= min_value:
            values.append(value)
    return values


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
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for index, line in enumerate(lines):
        if not _TOTAL.search(line):
            continue
        chunk = "\n".join(lines[index : index + 4])
        amounts = _amounts_in(chunk)
        if len(amounts) >= 2:
            return amounts[0], amounts[1]
        if len(amounts) == 1:
            return amounts[0], None
    return None, None


def _pair_from_label_columns(text: str) -> tuple[int | None, int | None]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    rp = sl = None
    for index, line in enumerate(lines):
        if rp is None and _RP_LABEL.search(line):
            inline = _amount_near_label(line, _RP_LABEL)
            if inline is not None:
                rp = inline
            elif index + 1 < len(lines):
                am = _AMOUNT.search(lines[index + 1])
                if am:
                    rp = _to_int(am.group(1))
        if sl is None and _SL_LABEL.search(line):
            inline = _amount_near_label(line, _SL_LABEL)
            if inline is not None:
                sl = inline
            elif index + 1 < len(lines):
                am = _AMOUNT.search(lines[index + 1])
                if am:
                    sl = _to_int(am.group(1))
    return rp, sl


def _pair_from_premium_columns(text: str) -> tuple[int | None, int | None]:
    """
    Ukrainian/Russian results UI often shows «З преміумом» / «Без преміуму».
    OCR frequently turns that into Latin garbage like «3 npeMiYM0M» / «be3 npeMiYMa».
    Prefer the without-premium pair (base RP then SL).
    """
    flat = re.sub(r"\s+", " ", text)

    without = _WITHOUT_PREMIUM.search(flat)
    if without:
        tail = flat[without.end() :]
        next_with = _WITH_PREMIUM.search(tail)
        chunk = tail[: next_with.start()] if next_with else tail[:120]
        amounts = _amounts_in(chunk)
        if len(amounts) >= 2:
            return amounts[0], amounts[1]
        if len(amounts) == 1:
            return amounts[0], None

    # «3 npeMiYMOM 9 596' npeMiYMa 6 127'» — bare second header = without-premium column.
    with_m = _WITH_PREMIUM.search(flat)
    if with_m:
        after_with = flat[with_m.end() :]
        bare = _BARE_PREMIUM_WORD.search(after_with)
        if bare:
            amounts = _amounts_in(after_with[bare.end() : bare.end() + 80])
            if len(amounts) >= 2:
                return amounts[0], amounts[1]
            if len(amounts) == 1:
                # Only SL (or only RP) visible in that column — keep as RP so the phone
                # gets a non-zero research figure; SL stays unknown.
                return amounts[0], None
        # Fall back to the with-premium amounts when without column is missing.
        amounts = _amounts_in(after_with[:80])
        if len(amounts) >= 2:
            return amounts[0], amounts[1]

    return None, None


def _research_from_total_line(text: str) -> int | None:
    flat = re.sub(r"\s+", " ", text)
    match = _TOTAL_RESEARCH.search(flat)
    if not match:
        return None
    window = flat[max(0, match.start() - 40) : match.end() + 40]
    amounts = _amounts_in(window)
    if not amounts:
        return None
    # Prefer the amount immediately before the label (common OCR layout: "1 088 Bcboro…").
    before = _amounts_in(flat[max(0, match.start() - 40) : match.start()])
    if before:
        return before[-1]
    return amounts[0]


def parse_rewards_from_ocr_text(text: str) -> BattleReport | None:
    if looks_like_desktop_noise(text):
        return None

    cleaned = text.replace("\r", "\n")
    rp = sl = None

    # Premium columns are the most reliable signal on UA/RU results screens.
    if _WITHOUT_PREMIUM.search(cleaned) or _WITH_PREMIUM.search(cleaned):
        prem_rp, prem_sl = _pair_from_premium_columns(cleaned)
        rp, sl = prem_rp, prem_sl

    if rp is None:
        rp = _amount_near_label(cleaned, _RP_LABEL)
    if sl is None:
        sl = _amount_near_label(cleaned, _SL_LABEL)

    if rp is None or sl is None:
        col_rp, col_sl = _pair_from_label_columns(cleaned)
        rp = rp if rp is not None else col_rp
        sl = sl if sl is not None else col_sl

    if rp is None or sl is None:
        prem_rp, prem_sl = _pair_from_premium_columns(cleaned)
        rp = rp if rp is not None else prem_rp
        sl = sl if sl is not None else prem_sl

    if rp is None:
        rp = _research_from_total_line(cleaned)

    if rp is None or sl is None:
        tot_rp, tot_sl = _pair_from_total_block(cleaned)
        rp = rp if rp is not None else tot_rp
        sl = sl if sl is not None else tot_sl

    # Accept a report when at least one currency is known — phone can still show partial data.
    if rp is None and sl is None:
        return None

    outcome = "undecided"
    if _VICTORY.search(cleaned):
        outcome = "victory"
    elif _DEFEAT.search(cleaned):
        outcome = "defeat"

    digest = hashlib.sha256(cleaned.encode("utf-8", errors="ignore")).hexdigest()[:32]
    confidence = 0.45
    if rp is not None:
        confidence += 0.2
    if sl is not None:
        confidence += 0.2
    if rp is not None and sl is not None:
        confidence += 0.1
    if _WITHOUT_PREMIUM.search(cleaned) or _WITH_PREMIUM.search(cleaned):
        confidence += 0.05
    elif _TOTAL.search(cleaned) or (_RP_LABEL.search(cleaned) and _SL_LABEL.search(cleaned)):
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
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= limit:
        return flat or "(empty OCR)"
    return flat[: limit - 1] + "…"
