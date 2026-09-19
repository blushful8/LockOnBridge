"""
War Thunder post-battle OCR → without-premium RP / SL.

Primary path: scale-safe digit ROIs (``roi_layout`` / ``roi_rewards``) on the WT
client frame — without-premium cells + «Всього» row, cross-checked.

Fallback: full-panel OCR text with label-driven mapping (not absolute pixels).

Canonical priority (see parse_rewards_from_ocr_text):
  1. Premium table → *without* column (4-cell with/without grid)
  2. «Всього» / Total row (SL then RP, optional vehicle / free RP)
  3. Modification-research RP with Total SL when table cells are mangled
  4. Explicit RP / SL labels
  5. Victory / participation boost line (last resort)

Amount tokens allow at most **one** thousands separator (``1 788``, ``12 446``).
Never glue ``13 672 336`` into a single integer. Team place
(``місце в команді: N``) is stripped before the premium grid.
"""

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
# Exactly one thousands group, and only when the left part is 1–2 digits
# (``1 788``, ``12 446``, ``4 608``). Never treat ``672 336`` (two RP cells) as 672336.
# ``(?<!\d)`` blocks icon-ghost glue: ``4219 276`` must not become ``9276``.
_AMOUNT = re.compile(
    r"([+\-]?\s*(?:(?<!\d)(?!0[.,])\d{1,2}(?:[\s.,'\u00A0]\d{3})|\d+))"
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
# «Ваше місце в команді: 13» sits in front of the RP/SL grid — drop the place digit.
_TEAM_PLACE = re.compile(
    r"(?:micue|місце|место|place).{0,40}?\d{1,2}\b",
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
    r"очк|льв|npoBaneHa|провал|forschung|recherche|лион|lion)",
    re.IGNORECASE,
)

# Ignore tiny / version-like numbers. Real match rewards are almost always ≥ this.
_MIN_REWARD = 50
# «Всього» SL can be well under 3k on short arcade matches (e.g. 2 912).
_MIN_TOTAL_SL = 800


def looks_like_desktop_noise(text: str) -> bool:
    if not text:
        return True
    noise = len(_DESKTOP_NOISE.findall(text))
    hints = len(_WT_HINT.findall(text))
    return noise >= 2 and hints == 0


def _normalize_results_text(text: str) -> str:
    """Keep newlines (label columns), collapse spaces, drop team-place before the grid."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[^\S\n]+", " ", text)
    return _TEAM_PLACE.sub(" ", text)


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
    """Amount next to a currency label — prefer immediate neighbour on the same line."""
    for line in text.splitlines():
        match = label.search(line)
        if not match:
            continue
        before = list(_AMOUNT.finditer(line[: match.start()]))
        after = _AMOUNT.search(line[match.end() :])
        # ``+2,100 Research Points`` — value sits immediately before the label.
        if before and match.start() - before[-1].end() <= 3:
            value = _to_int(before[-1].group(1))
            if value is not None and value >= _MIN_REWARD:
                return value
        if after:
            value = _to_int(after.group(1))
            if value is not None and value >= _MIN_REWARD:
                return value
        if before:
            value = _to_int(before[-1].group(1))
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
        if ams and match.start() - ams[-1].end() <= 3:
            value = _to_int(ams[-1].group(1))
            if value is not None and value >= _MIN_REWARD:
                return value
    return None


def _pair_from_total_block(text: str) -> tuple[int | None, int | None]:
    """
    «Всього» / Total row is the authoritative *without premium* pair on UA/RU results.

    Typical OCR tail after the detail rows:
      … achievements_sl, total_sl, total_rp, vehicle_rp, free_rp
    e.g. 1779 1783 12446 1788 1909 1276  → 1788 / 12446

    Returns (rp, sl, score) quality via internal scoring; callers use the pair only.
    """
    flat = re.sub(r"\s+", " ", text)
    windows: list[str] = []
    for match in _TOTAL.finditer(flat):
        windows.append(flat[match.start() : match.start() + 420])
    mods = re.search(
        r"(?:nocninxeH\w*\s*M0A|модифікац\w*|модификац\w*|M0A14\w*|bc[eo]ro\s*nocnin)",
        flat,
        re.IGNORECASE,
    )
    if mods:
        windows.append(flat[max(0, mods.start() - 120) : mods.start() + 200])
    if not windows:
        return None, None

    scored: list[tuple[int, int, int]] = []
    for window in windows:
        amounts = _amounts_in(window)
        for index in range(len(amounts) - 1):
            a, b = amounts[index], amounts[index + 1]
            # Prefer SL then RP (lions column read before research column).
            if a >= _MIN_TOTAL_SL and b >= _MIN_REWARD and a > b * 2 and _plausible_reward_pair(b, a):
                rp, sl = b, a
                score = 4
                if index + 2 < len(amounts):
                    nxt = amounts[index + 2]
                    # Vehicle research is only a little above mods/total RP — not the
                    # next combat SL line (e.g. 2388 air-kills then 3109 fatal).
                    vehicle_cap = rp + min(500, max(200, rp // 5))
                    if rp <= nxt <= vehicle_cap:
                        score += 3  # vehicle research RP follows totals
                        if index + 3 < len(amounts) and amounts[index + 3] < rp:
                            score += 1  # free RP after vehicle research
                if index > 0 and _MIN_REWARD <= amounts[index - 1] < sl:
                    score += 1  # achievements / participation SL before total
                # Classic debrief: participation SL + achievements SL immediately before Всього.
                if index >= 2:
                    prev2, prev1 = amounts[index - 2], amounts[index - 1]
                    if (
                        400 <= prev2 <= 5_000
                        and 400 <= prev1 <= 5_000
                        and prev2 < sl
                        and prev1 < sl
                    ):
                        score += 2
                scored.append((score, rp, sl))
            # RP then SL — only when Total label window is short/classic.
            elif (
                b >= _MIN_TOTAL_SL
                and a >= _MIN_REWARD
                and b > a * 2
                and _plausible_reward_pair(a, b)
                and len(amounts) <= 4
            ):
                scored.append((2, a, b))

    if scored:
        scored.sort(key=lambda item: (item[0], item[2]), reverse=True)
        best_score, rp, sl = scored[0]
        # Require a confident totals hit before overriding premium cells.
        if best_score >= 5 or (best_score >= 2 and len(scored) == 1):
            return rp, sl
    return None, None


def _mods_research_rp(text: str) -> int | None:
    """Without-premium RP equals «Дослідження модифікацій» on the results screen."""
    flat = re.sub(r"\s+", " ", text)
    match = re.search(
        r"(?:дослідження\s*модифікац\w*|исследование\s*модификац\w*|"
        r"nocninxeH\w*\s*M0A\w*|M0A14\w*\s*nocnin|"
        r"modification\s*research)",
        flat,
        re.IGNORECASE,
    )
    if not match:
        return None
    before = _amounts_in(flat[max(0, match.start() - 48) : match.start()])
    if before:
        return before[-1]
    after = _amounts_in(flat[match.end() : match.end() + 48])
    return after[0] if after else None


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
    """Drop trailing OCR junk and icon-ghost digits on 5-digit tokens."""
    cleaned: list[int] = []
    for value in amounts:
        # Icon next to SL often appends a trailing 9 (14709 → 1470, 24219 → 2421).
        if 10_000 <= value <= 99_999 and value % 10 == 9:
            trimmed = value // 10
            if 200 <= trimmed <= 45_000:
                value = trimmed
        cleaned.append(value)
    while len(cleaned) >= 3 and cleaned[-1] < 200 and cleaned[-1] < cleaned[0]:
        cleaned.pop()
    return cleaned


def _plausible_reward_pair(rp: int | None, sl: int | None) -> bool:
    if rp is None or sl is None:
        return False
    if rp < _MIN_REWARD or sl < _MIN_REWARD:
        return False
    # Reject OCR glue from FPS / timestamps / hash fragments.
    if rp > 50_000 or sl > 200_000:
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


def _with_pair_from_premium_amounts(amounts: list[int]) -> tuple[int | None, int | None]:
    """
    Resolve *with premium* (RP, SL) — the larger of each RP/SL pair in the comparison table.
    """
    amounts = _sanitize_premium_amounts(amounts)
    if len(amounts) >= 4:
        a, b, c, d = amounts[0], amounts[1], amounts[2], amounts[3]
        if max(a, b) < min(c, d):
            pair = max(a, b), max(c, d)
            if _plausible_reward_pair(*pair):
                return pair
        # With-row first.
        pair = a, b
        if _plausible_reward_pair(*pair) and max(a, b) >= max(c, d):
            return pair
        pair = c, d
        if _plausible_reward_pair(*pair):
            return pair
        amounts = amounts[:3]
    if len(amounts) == 3:
        a, b, c = amounts[0], amounts[1], amounts[2]
        # with_rp, without_rp, with_sl-ish — prefer larger RP with largest SL.
        if max(a, b) < c and _plausible_reward_pair(max(a, b), c):
            return max(a, b), c
        return None, None
    if len(amounts) >= 2:
        pair = amounts[0], amounts[1]
        if _plausible_reward_pair(*pair):
            return pair
    if len(amounts) == 1:
        return amounts[0], None
    return None, None


def _pair_from_premium_columns(
    text: str,
    *,
    prefer_with: bool = False,
) -> tuple[int | None, int | None]:
    """
    Extract RP/SL from the with/without premium comparison table.

    [prefer_with]=False → without-premium column (default, non-premium accounts).
    [prefer_with]=True → with-premium column (what a premium account actually banks).
    """
    flat = re.sub(r"\s+", " ", text)
    pick = _with_pair_from_premium_amounts if prefer_with else _without_pair_from_premium_amounts

    without = WITHOUT_PREMIUM.search(flat)
    if without and not prefer_with:
        tail = flat[without.end() :]
        next_with = WITH_PREMIUM.search(tail)
        chunk = tail[: next_with.start()] if next_with else tail[:160]
        amounts = _amounts_in(chunk)
        if len(amounts) >= 5 and 1 <= amounts[0] <= 16:
            amounts = amounts[1:]
        pair = _pair_after_without_label(amounts) if not prefer_with else pick(amounts)
        if pair[0] is not None:
            return pair

    with_m = WITH_PREMIUM.search(flat)
    if with_m and prefer_with:
        tail = flat[with_m.end() :]
        next_without = WITHOUT_PREMIUM.search(tail)
        chunk = tail[: next_without.start()] if next_without else tail[:160]
        amounts = _amounts_in(chunk)
        if len(amounts) >= 5 and 1 <= amounts[0] <= 16:
            amounts = amounts[1:]
        pair = pick(amounts)
        if pair[0] is not None:
            return pair

    # Truncated second header «npeMiYMa» after with-premium (classic without-premium OCR).
    if with_m and not prefer_with:
        after_with = flat[with_m.end() :]
        bare = _BARE_PREMIUM_WORD.search(after_with)
        if bare:
            amounts = _amounts_in(after_with[bare.end() : bare.end() + 100])
            if len(amounts) >= 5 and 1 <= amounts[0] <= 16:
                amounts = amounts[1:]
            if len(amounts) >= 2 and _plausible_reward_pair(amounts[0], amounts[1]):
                return amounts[0], amounts[1]

    # Fallback: scan after either header / bare four-cell block.
    for label in (WITH_PREMIUM, WITHOUT_PREMIUM):
        match = label.search(flat)
        if not match:
            continue
        amounts = _amounts_in(flat[match.end() : match.end() + 200])
        if len(amounts) >= 5 and 1 <= amounts[0] <= 16:
            amounts = amounts[1:]
        pair = pick(amounts)
        if pair[0] is not None:
            return pair

    # Four-cell block only.
    amounts = _amounts_in(flat)
    if len(amounts) >= 4:
        pair = pick(amounts[:4])
        if pair[0] is not None:
            return pair
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


def parse_rewards_from_ocr_text(
    text: str,
    *,
    prefer_premium_rewards: bool | None = None,
) -> BattleReport | None:
    """
    Map OCR text to post-battle RP/SL.

    [prefer_premium_rewards]:
      True  → with-premium column (premium account earnings)
      False → without-premium column / «Всього»
      None  → read Bridge settings.has_premium_account
    """
    if looks_like_desktop_noise(text):
        return None

    if prefer_premium_rewards is None:
        try:
            from .settings import load_settings

            prefer_premium_rewards = bool(load_settings().has_premium_account)
        except Exception:  # noqa: BLE001
            prefer_premium_rewards = False

    cleaned = _normalize_results_text(text)
    rp = sl = None
    premium_hit = False
    reward_hit = False

    # --- 1. Premium comparison table ---
    if WITHOUT_PREMIUM.search(cleaned) or WITH_PREMIUM.search(cleaned):
        prem_rp, prem_sl = _pair_from_premium_columns(
            cleaned,
            prefer_with=prefer_premium_rewards,
        )
        if _plausible_reward_pair(prem_rp, prem_sl):
            premium_hit = True
            rp, sl = prem_rp, prem_sl

    # --- 2. «Всього» / Total — authoritative for *without* premium; skip override when
    # preferring with-premium so we do not replace 10892 with 7262.
    tot_rp, tot_sl = _pair_from_total_block(cleaned)
    if _plausible_reward_pair(tot_rp, tot_sl):
        if prefer_premium_rewards and premium_hit:
            pass  # keep with-premium table pair
        elif not premium_hit:
            rp, sl = tot_rp, tot_sl
            premium_hit = True
        elif rp is not None and sl is not None and (rp, sl) != (tot_rp, tot_sl):
            assert tot_rp is not None and tot_sl is not None
            if tot_sl >= int(sl * 0.95):
                rp, sl = tot_rp, tot_sl
            elif abs(tot_rp - rp) >= 50 and _plausible_reward_pair(tot_rp, sl):
                rp = tot_rp
    else:
        # --- 3. Mods research RP when Total was weak but table RP looks mangled ---
        mods_rp = _mods_research_rp(cleaned)
        if (
            premium_hit
            and not prefer_premium_rewards
            and mods_rp is not None
            and rp is not None
            and sl is not None
            and abs(mods_rp - rp) >= 50
            and mods_rp < sl
            and _plausible_reward_pair(mods_rp, sl)
        ):
            rp = mods_rp

    # --- 4–5. Labels / boost line (fill gaps only) ---
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
        prem_rp, prem_sl = _pair_from_premium_columns(
            cleaned,
            prefer_with=prefer_premium_rewards,
        )
        rp = rp if rp is not None else prem_rp
        sl = sl if sl is not None else prem_sl

    if rp is None:
        rp = _research_from_total_line(cleaned)

    if not premium_hit and not reward_hit and (rp is None or sl is None):
        tot_rp, tot_sl = _pair_from_total_block(cleaned)
        rp = rp if rp is not None else tot_rp
        sl = sl if sl is not None else tot_sl

    # Deglue OCR (29128 → 2912) when the shorter token is also present.
    if rp is not None and sl is not None:
        rp, sl = _prefer_deglued_pair(cleaned, rp, sl)

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


def _prefer_deglued_pair(text: str, rp: int, sl: int) -> tuple[int, int]:
    """If OCR appended a digit (2912→29128), prefer the shorter amount also in the text."""
    amounts = set(_amounts_in(text, min_value=1))
    # Only rewrite when the long value is *not* an exact token but the short one is.
    if sl not in amounts:
        for candidate in (sl // 10, sl // 100):
            if (
                candidate >= _MIN_TOTAL_SL
                and candidate in amounts
                and _plausible_reward_pair(rp, candidate)
                and sl == candidate * 10 + (sl % 10)
            ):
                return rp, candidate
    if rp not in amounts:
        for candidate in (rp // 10, rp // 100):
            if (
                candidate >= _MIN_REWARD
                and candidate in amounts
                and _plausible_reward_pair(candidate, sl)
                and rp == candidate * 10 + (rp % 10)
            ):
                return candidate, sl
    return rp, sl



def choose_best_report(candidates: list[tuple[str, BattleReport | None]]) -> tuple[str, BattleReport] | None:
    """Pick the strongest parse among per-engine OCR texts. Never merges texts."""
    scored: list[tuple[float, str, BattleReport]] = []
    for text, report in candidates:
        if report is None:
            continue
        score = float(report.confidence)
        # Geometric ROI consensus beats flat full-frame heuristics.
        lower = text.lower()
        dual_roi = "без преміума" in lower and "всього" in lower
        if dual_roi:
            score += 0.35
            report = BattleReport(
                captured_at_epoch_millis=report.captured_at_epoch_millis,
                research_points=report.research_points,
                silver_lions=report.silver_lions,
                outcome=report.outcome,
                raw_hash=report.raw_hash,
                confidence=max(report.confidence, 0.92),
                source="ocr-roi" if report.source == "ocr" else report.source,
                id=report.id,
            )
        elif text.lstrip().startswith("Всього") or text.lstrip().startswith("Без"):
            score += 0.18
            report = BattleReport(
                captured_at_epoch_millis=report.captured_at_epoch_millis,
                research_points=report.research_points,
                silver_lions=report.silver_lions,
                outcome=report.outcome,
                raw_hash=report.raw_hash,
                confidence=max(report.confidence, 0.85),
                source="ocr-roi" if report.source == "ocr" else report.source,
                id=report.id,
            )
        rp, sl = report.research_points, report.silver_lions
        amounts = set(_amounts_in(text, min_value=1))
        # Prefer clean SL when OCR also emitted a glued sibling (2912 and 29128).
        if sl in amounts and (sl // 10) in amounts and _plausible_reward_pair(rp, sl // 10):
            sl = sl // 10
        if rp in amounts and (rp // 10) in amounts and _plausible_reward_pair(rp // 10, sl):
            rp = rp // 10
        # Prefer classic without-premium arcade shape over partial combat lines.
        ratio = sl / max(1, rp)
        if 4.0 <= ratio <= 20.0:
            score += 0.22
        elif 2.5 <= ratio < 4.0:
            score += 0.05
        elif ratio > 50 or ratio < 0.4:
            score -= 0.2
        if 1_500 <= sl <= 40_000:
            score += 0.1
        elif sl > 50_000:
            score -= 0.15
        if 200 <= rp <= 4_500:
            score += 0.08
        elif rp >= 5_000:
            # Combat SL lines often land here when misread as RP.
            score -= 0.12
        if _TOTAL.search(text) or WITHOUT_PREMIUM.search(text) or WITH_PREMIUM.search(text):
            score += 0.12
        # Prefer without-premium SL when a with/without pair (~1.25–1.85×) is visible.
        large = [a for a in _amounts_in(text) if 5_000 <= a <= 50_000]
        best_pair: tuple[int, int] | None = None
        best_dist = 9.0
        for index, first in enumerate(large):
            for second in large[index + 1 :]:
                lo_i, hi_i = (first, second) if first <= second else (second, first)
                # Skip combat crumbs; without/with SL are almost always ≥ 8k.
                if lo_i < 8_000 or hi_i > 45_000:
                    continue
                pair_ratio = hi_i / max(1, lo_i)
                if 1.25 <= pair_ratio <= 1.85 and abs(pair_ratio - 1.5) < best_dist:
                    best_dist = abs(pair_ratio - 1.5)
                    best_pair = (lo_i, hi_i)
        if best_pair is not None:
            lo, hi = best_pair
            if abs(sl - lo) <= max(200, int(lo * 0.04)):
                score += 0.2
            elif abs(sl - hi) <= max(200, int(hi * 0.04)):
                score -= 0.15
        if rp in amounts and sl in amounts:
            score += 0.25
        if sl not in amounts and (sl // 10) in amounts:
            score -= 0.45
        elif rp not in amounts and (rp // 10) in amounts:
            score -= 0.35
        if WITHOUT_PREMIUM.search(text) and WITH_PREMIUM.search(text):
            score += 0.1
        # Rebuild report if we deglued for scoring so the published pair matches.
        if (rp, sl) != (report.research_points, report.silver_lions):
            report = BattleReport(
                captured_at_epoch_millis=report.captured_at_epoch_millis,
                research_points=rp,
                silver_lions=sl,
                outcome=report.outcome,
                raw_hash=report.raw_hash,
                confidence=report.confidence,
                source=report.source,
                id=report.id,
            )
        scored.append((score, text, report))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    _score, text, report = scored[0]
    # Among near-ties with the same SL, prefer the lower RP (mods vs vehicle research).
    for cand_score, cand_text, cand_report in scored[1:]:
        if _score - cand_score > 0.05:
            break
        if abs(cand_report.silver_lions - report.silver_lions) <= max(
            80, int(report.silver_lions * 0.03)
        ):
            if (
                cand_report.research_points < report.research_points
                and cand_report.research_points >= int(report.research_points * 0.85)
            ):
                text, report = cand_text, cand_report
                _score = cand_score
    return text, report


def summarize_ocr_text(text: str, limit: int = 400) -> str:
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= limit:
        return flat or "(empty OCR)"
    return flat[: limit - 1] + "…"
