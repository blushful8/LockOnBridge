from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any

from .lang_labels import (
    DEFEAT,
    REWARD_LINE,
    RP_LABEL,
    SL_LABEL,
    VICTORY,
    WITH_PREMIUM,
    WITHOUT_PREMIUM,
)


@dataclass
class BattleReport:
    captured_at_epoch_millis: int
    research_points: int
    silver_lions: int
    outcome: str
    raw_hash: str
    confidence: float
    source: str = "ocr"
    id: str = ""

    def to_json(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "capturedAtEpochMillis": self.captured_at_epoch_millis,
            "researchPoints": self.research_points,
            "silverLions": self.silver_lions,
            "outcome": self.outcome,
            "rawHash": self.raw_hash,
            "confidence": self.confidence,
            "source": self.source,
        }
        return payload


_TOTAL = re.compile(
    r"\btotal\b|итого|разом|підсумок|итог\w*|reward\s*total|"
    r"всего|всього|bcboro|vsego|bcsoro|"
    r"gesamt|somme|totale|suma|合計|합계|总计|總計",
    re.IGNORECASE,
)
# Thousands separators (EU `1.088` / `1 088`) — never treat ratio OCR `0.954` as 954.
_AMOUNT = re.compile(
    r"([+\-]?\s*(?:(?!0[.,])\d{1,3}(?:[\s.,'\u00A0]\d{3})+|\d+))"
)
_RATIO_AMOUNT = re.compile(r"^[+\-]?\s*0[.,]\d+$")
_BARE_PREMIUM_WORD = re.compile(
    r"(?<![bezs3zс])\b(?:npe?[mn]i?[yуu0о]?m[aаeе]?|premium)\b",
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
_PERCENT = re.compile(r"\+\s*\d+\s*%")

_DESKTOP_NOISE = re.compile(
    r"(?:telegram|nvidia\s*app|malware\s*protection|epic\s*games|"
    r"lockon\s*bridge|порт\s*http|nop[rt]\s*http|check\s*for\s*updates|"
    r"перевірити\s*оновлення|nepeeipnw\s*onoene)",
    re.IGNORECASE,
)
_WT_HINT = re.compile(
    r"(?:npe?[mn]i?[yуu]?m|преміум|premium|haropon|нагород|місі|"
    r"micifl|mission|research|silver|nocnin|дослід|war\s*thunder|"
    r"очк|льв|npoBaneHa|провал|forschung|recherche|лион|lion)",
    re.IGNORECASE,
)

# Ignore tiny / version-like numbers. Real match rewards are almost always ≥ this.
_MIN_REWARD = 50


def looks_like_desktop_noise(text: str) -> bool:
    if not text:
        return True
    noise = len(_DESKTOP_NOISE.findall(text))
    hints = len(_WT_HINT.findall(text))
    return noise >= 2 and hints == 0


def _to_int(raw: str) -> int | None:
    stripped = raw.strip()
    # K/D / efficiency ratios (0.954) must not become reward integers via "." thousands rules.
    if _RATIO_AMOUNT.match(stripped):
        return None
    digits = re.sub(r"[^\d]", "", stripped)
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
            if value is not None and value >= _MIN_REWARD:
                return value
        after = _AMOUNT.search(line[match.end() :])
        if after:
            value = _to_int(after.group(1))
            if value is not None and value >= _MIN_REWARD:
                return value
    for match in label.finditer(text):
        window = text[match.end() : match.end() + 64]
        am = _AMOUNT.search(window)
        if am:
            value = _to_int(am.group(1))
            if value is not None and value >= _MIN_REWARD:
                return value
        window_before = text[max(0, match.start() - 64) : match.start()]
        ams = list(_AMOUNT.finditer(window_before))
        if ams:
            value = _to_int(ams[-1].group(1))
            if value is not None and value >= _MIN_REWARD:
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
        if rp is None and RP_LABEL.search(line):
            inline = _amount_near_label(line, RP_LABEL)
            if inline is not None:
                rp = inline
            elif index + 1 < len(lines):
                am = _AMOUNT.search(lines[index + 1])
                if am:
                    value = _to_int(am.group(1))
                    if value is not None and value >= _MIN_REWARD:
                        rp = value
        if sl is None and SL_LABEL.search(line):
            inline = _amount_near_label(line, SL_LABEL)
            if inline is not None:
                sl = inline
            elif index + 1 < len(lines):
                am = _AMOUNT.search(lines[index + 1])
                if am:
                    value = _to_int(am.group(1))
                    if value is not None and value >= _MIN_REWARD:
                        sl = value
    return rp, sl


def _sanitize_premium_amounts(amounts: list[int]) -> list[int]:
    """Drop trailing OCR junk (e.g. «60» from «бойових» → 60hOBhX)."""
    cleaned = list(amounts)
    while len(cleaned) >= 3 and cleaned[-1] < 200 and cleaned[-1] < cleaned[0]:
        cleaned.pop()
    return cleaned


def _plausible_reward_pair(rp: int | None, sl: int | None) -> bool:
    if rp is None or sl is None:
        return False
    if rp < _MIN_REWARD or sl < _MIN_REWARD:
        return False
    # Real SL is almost always greater than RP; tiny SL with large RP is OCR junk.
    if sl < rp:
        return False
    ratio = sl / max(1, rp)
    if ratio > 40:
        return False
    return True


def _without_pair_from_premium_amounts(amounts: list[int]) -> tuple[int | None, int | None]:
    """
    Resolve *without premium* (RP, SL) from the post-battle comparison table.

    OCR often emits labels first, then cells either:
      column-major: with_rp, without_rp, with_sl, without_sl
      column-major SL-swapped: with_rp, without_rp, without_sl, with_sl
      row-major:    with_rp, with_sl, without_rp, without_sl
    Classic row after the without label alone: without_rp, without_sl.
    Sometimes with_rp is missing: without_rp, with_sl, without_sl.

    Without-premium values are the smaller of each RP pair and each SL pair.
    """
    amounts = _sanitize_premium_amounts(amounts)
    if len(amounts) >= 4:
        a, b, c, d = amounts[0], amounts[1], amounts[2], amounts[3]
        # Both RPs then both SLs (order of with/without inside each pair may swap).
        if max(a, b) < min(c, d):
            pair = min(a, b), min(c, d)
            if _plausible_reward_pair(*pair):
                return pair
        # With-row then without-row.
        pair = c, d
        if _plausible_reward_pair(*pair):
            return pair
        # Without-row then with-row.
        pair = a, b
        if _plausible_reward_pair(*pair) and min(a, b) <= min(c, d):
            return pair
        amounts = amounts[:3]
    if len(amounts) == 3:
        a, b, c = amounts[0], amounts[1], amounts[2]
        # without_rp, with_sl, without_sl (with_rp lost by OCR).
        if a < b and c < b and c > a and _plausible_reward_pair(a, c):
            return a, c
        # with_rp, without_rp, without_sl (with_sl delayed/missing).
        if max(a, b) < c and _plausible_reward_pair(min(a, b), c):
            return min(a, b), c
        return None, None
    if len(amounts) >= 2:
        pair = amounts[0], amounts[1]
        if _plausible_reward_pair(*pair):
            return pair
        return None, None
    if len(amounts) == 1:
        return amounts[0], None
    return None, None


def _pair_after_without_label(amounts: list[int]) -> tuple[int | None, int | None]:
    """Amounts sitting after a without-premium label (not the 4-cell with/without grid)."""
    amounts = _sanitize_premium_amounts(amounts)
    if len(amounts) >= 4:
        a, b, c, d = amounts[0], amounts[1], amounts[2], amounts[3]
        # Column grid: take the smaller RP and smaller SL (without premium).
        if max(a, b) < min(c, d):
            pair = min(a, b), min(c, d)
            if _plausible_reward_pair(*pair):
                return pair
        if _plausible_reward_pair(a, b):
            return a, b
        if _plausible_reward_pair(c, d):
            return c, d
        return None, None
    if len(amounts) == 3:
        a, b, c = amounts[0], amounts[1], amounts[2]
        if a < b and c < b and c > a and _plausible_reward_pair(a, c):
            return a, c
        if max(a, b) < c and _plausible_reward_pair(min(a, b), c):
            return min(a, b), c
    if len(amounts) >= 2 and _plausible_reward_pair(amounts[0], amounts[1]):
        return amounts[0], amounts[1]
    if len(amounts) == 1:
        return amounts[0], None
    return None, None


def _pair_from_premium_columns(text: str) -> tuple[int | None, int | None]:
    """
    Prefer *without premium* RP/SL (earned without account boost).

    Full-window OCR text is kept for future features; this helper only extracts that pair.
    """
    flat = re.sub(r"\s+", " ", text)

    without = WITHOUT_PREMIUM.search(flat)
    if without:
        tail = flat[without.end() :]
        next_with = WITH_PREMIUM.search(tail)
        chunk = tail[: next_with.start()] if next_with else tail[:160]
        amounts = _amounts_in(chunk)
        if len(amounts) < 2:
            with_before = WITH_PREMIUM.search(flat[: without.start() + 1])
            if with_before:
                between = flat[with_before.end() : without.start()]
                if len(_amounts_in(between)) == 0:
                    amounts = _amounts_in(flat[without.end() : without.end() + 160])
        pair = _pair_after_without_label(amounts)
        if pair[0] is not None and pair[1] is not None:
            return pair

    with_m = WITH_PREMIUM.search(flat)
    if with_m:
        after_with = flat[with_m.end() :]
        # Second header «npeMiYMa» (truncated without-premium) → amounts after it.
        bare = _BARE_PREMIUM_WORD.search(after_with)
        if bare:
            amounts = _amounts_in(after_with[bare.end() : bare.end() + 100])
            if len(amounts) >= 2:
                return amounts[0], amounts[1]
        # Four-cell block only — never the first two cells (those are with-premium).
        amounts = _amounts_in(after_with[:200])
        if len(amounts) >= 4:
            pair = _without_pair_from_premium_amounts(amounts)
            if pair[0] is not None and pair[1] is not None:
                return pair
        loose = re.search(
            r"(?:5\s*[bв]|be[zs3]|без)\s*npe?\w*.{0,12}?"
            r"((?:\d{1,3}(?:[\s.,'\u00A0]\d{3})+|\d{3,}))"
            r".{0,12}?"
            r"((?:\d{1,3}(?:[\s.,'\u00A0]\d{3})+|\d{3,}))",
            flat,
            re.IGNORECASE,
        )
        if loose:
            rp = _to_int(loose.group(1))
            sl = _to_int(loose.group(2))
            if rp is not None and sl is not None and rp >= _MIN_REWARD and sl >= _MIN_REWARD:
                return rp, sl

    return None, None


def _pair_from_reward_boost_line(text: str) -> tuple[int | None, int | None]:
    """
    UA/RU results often show:
      Нагорода за перемогу: +100%, +47%   1 148   2 732
    OCR: Haropona 3a nepeMory: +100% , +47%' 1 148 2 732'

    Requires a real reward-line label. Totals must sit immediately after the last
    +N% — otherwise team-board columns after a participation boost become RP/SL.
    """
    flat = re.sub(r"\s+", " ", text)
    reward = REWARD_LINE.search(flat)
    if not reward:
        return None, None
    window = flat[reward.start() : reward.start() + 220]
    percents = list(_PERCENT.finditer(window))
    if not percents:
        return None, None
    after = window[percents[-1].end() :]
    # Real debrief puts RP/SL right after the boost percents (usually < ~36 chars).
    near = after[:36]
    amounts = _amounts_in(near)
    if len(amounts) < 2:
        return None, None
    rp, sl = amounts[0], amounts[1]
    # Identical consecutive board cells after a boost line are almost never real totals.
    if rp == sl:
        return None, None
    return rp, sl


def _research_from_total_line(text: str) -> int | None:
    flat = re.sub(r"\s+", " ", text)
    match = _TOTAL_RESEARCH.search(flat)
    if not match:
        return None
    before = _amounts_in(flat[max(0, match.start() - 40) : match.start()])
    if before:
        return before[-1]
    after = _amounts_in(flat[match.end() : match.end() + 40])
    return after[0] if after else None


def parse_rewards_from_ocr_text(text: str) -> BattleReport | None:
    if looks_like_desktop_noise(text):
        return None

    cleaned = text.replace("\r", "\n")
    rp = sl = None
    premium_hit = False
    reward_hit = False

    if WITHOUT_PREMIUM.search(cleaned) or WITH_PREMIUM.search(cleaned):
        prem_rp, prem_sl = _pair_from_premium_columns(cleaned)
        if _plausible_reward_pair(prem_rp, prem_sl):
            premium_hit = True
            rp, sl = prem_rp, prem_sl

    if rp is None or sl is None:
        reward_rp, reward_sl = _pair_from_reward_boost_line(cleaned)
        if reward_rp is not None or reward_sl is not None:
            reward_hit = True
            rp = rp if rp is not None else reward_rp
            sl = sl if sl is not None else reward_sl

    if rp is None:
        rp = _amount_near_label(cleaned, RP_LABEL)
    if sl is None:
        sl = _amount_near_label(cleaned, SL_LABEL)

    if rp is None or sl is None:
        col_rp, col_sl = _pair_from_label_columns(cleaned)
        rp = rp if rp is not None else col_rp
        sl = sl if sl is not None else col_sl

    if not premium_hit and (rp is None or sl is None):
        prem_rp, prem_sl = _pair_from_premium_columns(cleaned)
        rp = rp if rp is not None else prem_rp
        sl = sl if sl is not None else prem_sl

    if rp is None:
        rp = _research_from_total_line(cleaned)

    if not premium_hit and not reward_hit and (rp is None or sl is None):
        tot_rp, tot_sl = _pair_from_total_block(cleaned)
        rp = rp if rp is not None else tot_rp
        sl = sl if sl is not None else tot_sl

    # Require a plausible pair — never publish RP=1 / SL=0 junk or swapped columns.
    if not _plausible_reward_pair(rp, sl):
        # Premium path may have claimed a bad pair; fall back to totals.
        if premium_hit:
            tot_rp, tot_sl = _pair_from_total_block(cleaned)
            if _plausible_reward_pair(tot_rp, tot_sl):
                rp, sl = tot_rp, tot_sl
                premium_hit = False
            else:
                return None
        else:
            return None
    assert rp is not None and sl is not None

    outcome = "undecided"
    if VICTORY.search(cleaned):
        outcome = "victory"
    elif DEFEAT.search(cleaned):
        outcome = "defeat"

    digest = hashlib.sha256(cleaned.encode("utf-8", errors="ignore")).hexdigest()[:32]
    confidence = 0.5
    if premium_hit:
        confidence += 0.2
    if reward_hit:
        confidence += 0.15
    if RP_LABEL.search(cleaned) and SL_LABEL.search(cleaned):
        confidence += 0.1
    if outcome != "undecided":
        confidence += 0.05

    return BattleReport(
        captured_at_epoch_millis=int(time.time() * 1000),
        research_points=rp,
        silver_lions=sl,
        outcome=outcome,
        raw_hash=digest,
        confidence=min(1.0, confidence),
    )


def choose_best_report(candidates: list[tuple[str, BattleReport | None]]) -> tuple[str, BattleReport] | None:
    """Pick the strongest parse among per-engine OCR texts. Never merges texts."""
    scored: list[tuple[float, str, BattleReport]] = []
    for text, report in candidates:
        if report is None:
            continue
        score = report.confidence
        # Prefer both currencies well above the floor.
        score += min(report.research_points, report.silver_lions) / 100_000.0
        # Penalize OCR glue (e.g. 78049 instead of 7804) via implausible SL/RP ratio.
        ratio = report.silver_lions / max(1, report.research_points)
        if 1.2 <= ratio <= 30:
            score += 0.08
        elif ratio > 50 or ratio < 0.4:
            score -= 0.2
        scored.append((score, text, report))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    _score, text, report = scored[0]
    return text, report


def summarize_ocr_text(text: str, limit: int = 400) -> str:
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= limit:
        return flat or "(empty OCR)"
    return flat[: limit - 1] + "…"
