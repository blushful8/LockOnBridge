"""
Parse War Thunder «Messages → Battles» clipboard dumps.

Field standard (locale-agnostic):
  - Footer zone (after bare hex sessionId, else last lines)
  - 3 currency amounts → SL = 1st, free-RP→RP = 2nd, drop 3rd (Всього / Total)
  - else 2 amounts → SL = 1st, RP = 2nd (Зароблено / Earned)
  - Amounts may be 0 or huge; no min floors
  - Units / language labels are optional score boosts only
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass

from .ocr_parse import BattleReport

# Optional unit aliases (score boost only — never required).
_SL_UNITS = (
    "silver lions",
    "silver lion",
    "lions",
    "lion",
    "сл",
    "sl",
)
_FREE_RP_UNITS = (
    "free research points",
    "free research",
    "free rp",
    "freerp",
    "frp",
    "вод",
    "своб",
)
_RP_UNITS = (
    "research points",
    "research",
    "од",
    "rp",
)

_SESSION_HEX = re.compile(
    r"(?<![0-9a-fA-F])([0-9a-fA-F]{8,16})(?![0-9a-fA-F])",
)
# Soft latin-only outcome markers (clipboard without frame). No OCR slang.
_OUTCOME_WIN = re.compile(r"\bvictory\b", re.IGNORECASE)
_OUTCOME_LOSS = re.compile(r"\bdefeat\b", re.IGNORECASE)
# Clock / duration tokens must not count as currency.
_TIME_TOKEN = re.compile(
    r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)",
)
_NUMBER = re.compile(
    r"(?<!\d)(\d{1,3}(?:[\s\u00a0.,']\d{3})+|\d+)(?!\d)",
)

_FOOTER_TAIL_LINES = 24


def detect_battle_outcome(text: str) -> str:
    """victory | defeat | undecided — latin markers only; prefer frame color."""
    if not text:
        return "undecided"
    if _OUTCOME_WIN.search(text):
        return "victory"
    if _OUTCOME_LOSS.search(text):
        return "defeat"
    return "undecided"


def detect_outcome_from_frame(frame) -> str:
    """
    victory | defeat | undecided from Messages panel outcome badge color.

    Green-dominant strip → victory; red-dominant → defeat. Language-free.
    """
    if frame is None:
        return "undecided"
    try:
        from PIL import ImageStat
    except Exception:  # noqa: BLE001
        return "undecided"
    try:
        w, h = frame.size
        strip = frame.convert("RGB").crop(
            (int(w * 0.22), int(h * 0.12), int(w * 0.72), int(h * 0.28))
        )
        # Sample mid band where the outcome word/badge sits.
        sw, sh = strip.size
        badge = strip.crop(
            (int(sw * 0.15), int(sh * 0.25), int(sw * 0.85), int(sh * 0.75))
        )
        # Keep saturated non-gray pixels only.
        px = badge.load()
        bw, bh = badge.size
        colored: list[tuple[int, int, int]] = []
        for y in range(bh):
            for x in range(bw):
                r, g, b = px[x, y][:3]
                mx = max(r, g, b)
                mn = min(r, g, b)
                if mx < 40:
                    continue
                if mx - mn < 28:
                    continue
                colored.append((r, g, b))
        if len(colored) < 12:
            return "undecided"
        rs = sum(p[0] for p in colored) / len(colored)
        gs = sum(p[1] for p in colored) / len(colored)
        bs = sum(p[2] for p in colored) / len(colored)
        # Victory badges are green/lime; defeat are red/orange.
        if gs > rs + 18 and gs > bs + 10:
            return "victory"
        if rs > gs + 18 and rs > bs + 10:
            return "defeat"
        # Soft secondary: mean channel dominance via ImageStat on full strip.
        stat = ImageStat.Stat(badge)
        r_m, g_m, b_m = stat.mean[:3]
        if g_m > r_m + 15 and g_m > b_m + 8:
            return "victory"
        if r_m > g_m + 15 and r_m > b_m + 8:
            return "defeat"
    except Exception:  # noqa: BLE001
        return "undecided"
    return "undecided"


def _unit_pattern(aliases: tuple[str, ...]) -> re.Pattern[str]:
    parts = sorted((re.escape(a) for a in aliases), key=len, reverse=True)
    return re.compile(r"(?:%s)" % "|".join(parts), re.IGNORECASE)


_SL_RE = _unit_pattern(_SL_UNITS)
_FREE_RP_RE = _unit_pattern(_FREE_RP_UNITS)
_RP_RE = _unit_pattern(_RP_UNITS)


def _parse_int_token(raw: str) -> int | None:
    digits = re.sub(r"[^\d]", "", raw or "")
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _extract_session_id(text: str) -> str:
    m = _SESSION_HEX.search(text or "")
    return m.group(1).lower() if m else ""


def _footer_zone_lines(text: str) -> list[str]:
    """Lines after session hex, else last N non-empty lines."""
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in cleaned.split("\n") if ln.strip()]
    if not lines:
        return []
    sess = _SESSION_HEX.search(cleaned)
    if sess is not None:
        # Prefer content at/after the session token line.
        after: list[str] = []
        seen = False
        for ln in lines:
            if not seen and _SESSION_HEX.search(ln):
                seen = True
                after.append(ln)
                continue
            if seen:
                after.append(ln)
        if after:
            return after
    return lines[-_FOOTER_TAIL_LINES:]


def _normalize_ocr_money_line(line: str) -> str:
    """
    Repair common Messages-panel OCR glitches on Total / Earned lines.

    Examples seen in the wild:
      «7 535@» → 7535
      «1 445%» → 1445   (OCR puts % on free-RP, not a real percent)
      «1 8069» → 1806   (trailing icon digit glued onto grouped thousands)
      «14459»  → 1445   (same glue without the thousands space)
    """
    if not line:
        return ""
    s = line
    # Grouped thousands + junk char: 7 535@ / 1 445% / 1 806,
    s = re.sub(
        r"(?<!\d)(\d{1,3})\s+(\d{3})(?=[^\d\s]|$)",
        lambda m: m.group(1) + m.group(2),
        s,
    )
    # Grouped thousands + extra glued digit: 1 8069 → 1806
    s = re.sub(
        r"(?<!\d)(\d{1,3})\s+(\d{3})(\d)(?!\d)",
        lambda m: m.group(1) + m.group(2),
        s,
    )
    return s


def _amounts_in_line(line: str) -> list[int]:
    """Currency-like integers on a line (time tokens stripped). Allow 0."""
    if not line:
        return []
    stripped = _normalize_ocr_money_line(_TIME_TOKEN.sub(" ", line))
    # Session hex must not contribute leading digits as currency (745ebb… → 745).
    stripped = _SESSION_HEX.sub(" ", stripped)
    # Drop signed repair costs like -676 from competing with totals.
    stripped = re.sub(r"-\s*\d+", " ", stripped)
    amounts: list[int] = []
    for m in _NUMBER.finditer(stripped):
        n = _parse_int_token(m.group(1))
        if n is None:
            continue
        amounts.append(n)
    # Real activity percents are 0–100. OCR often paints «1445%» on free-RP —
    # only strip true percent-range values.
    pct_nums = {
        _parse_int_token(m.group(1))
        for m in re.finditer(
            r"(?<!\d)(\d{1,3}(?:[\s\u00a0.,']\d{3})+|\d+)\s*%",
            stripped,
        )
    }
    pct_nums.discard(None)
    pct_nums = {a for a in pct_nums if a is not None and 0 <= a <= 100}
    if pct_nums:
        amounts = [a for a in amounts if a not in pct_nums]
    # On Total-style 3-amount lines, 2nd/3rd often get a trailing icon digit
    # («14459», «18069»). Never touch the 1st amount (real SL can be 5 digits).
    if len(amounts) >= 3:
        fixed = [amounts[0]]
        for a in amounts[1:3]:
            if 10000 <= a <= 99999 and a % 10 in (5, 6, 8, 9, 0):
                trimmed = a // 10
                if 100 <= trimmed <= 9999:
                    a = trimmed
            fixed.append(a)
        fixed.extend(amounts[3:])
        amounts = fixed
    return amounts


def _unit_boost(line: str) -> float:
    """Small optional boost when known unit glyphs appear (not required)."""
    low = line.lower()
    score = 0.0
    if _SL_RE.search(low):
        score += 0.15
    if _FREE_RP_RE.search(low):
        score += 0.15
    if _RP_RE.search(low):
        score += 0.05
    # OCR often keeps «Bcboro» / «Bcoro» for Всього — structural Total hint.
    if re.search(r"\b(?:bcboro|bcogo|bcbro|total|vsego|vсьо|всього|всего)\b", low):
        score += 0.4
    return score


@dataclass(frozen=True)
class _FooterHit:
    sl: int
    rp: int
    score: float
    n_amounts: int


# Activity rows: «53 + (Підсилювач)27 = 80» — not footer totals.
_BOOSTER_EQ = re.compile(
    r"\d\s*\+\s*.{0,40}=\s*\d",
    re.UNICODE,
)


def _footer_candidate(line: str) -> _FooterHit | None:
    if _BOOSTER_EQ.search(line):
        return None
    amounts = _amounts_in_line(line)
    # Total line often has trailing OCR junk (Ctrl+C hint) → 4+ amounts.
    # Prefer the first three as SL / free-RP / module-RP.
    if len(amounts) >= 3:
        # Activity headers look like «6 2359 306» (tiny count + SL + RP).
        if amounts[0] < 40 and amounts[1] >= 100:
            return None
        sl, rp = amounts[0], amounts[1]
        if not (sl == 0 or sl >= 50) or not (rp == 0 or rp >= 50):
            return None
        score = 2.0 + _unit_boost(line)
        if len(amounts) > 3:
            score -= 0.05  # slight penalty vs clean 3-amount lines
        return _FooterHit(sl=sl, rp=rp, score=score, n_amounts=3)
    if len(amounts) == 2:
        # Skip «6 2359»-style if it somehow lost the third amount.
        if amounts[0] < 40 and amounts[1] >= 200:
            return None
        sl, rp = amounts[0], amounts[1]
        if not (sl == 0 or sl >= 50) or not (rp == 0 or rp >= 50):
            return None
        score = 1.0 + _unit_boost(line)
        return _FooterHit(sl=sl, rp=rp, score=score, n_amounts=2)
    return None


def _pick_footer_amounts(text: str) -> tuple[int, int, int] | None:
    """
    Prefer best-scoring 3-amount footer line (Total), else best 2-amount (Earned).

    Score beats last-wins so activity-table OCR after the footer cannot override
    a real «Всього / Total» line.
    Returns (sl, rp, n_amounts) or None.
    """
    zone = _footer_zone_lines(text)
    best3: _FooterHit | None = None
    best2: _FooterHit | None = None
    for line in zone:
        hit = _footer_candidate(line)
        if hit is None:
            continue
        if hit.n_amounts == 3:
            if best3 is None or hit.score >= best3.score:
                best3 = hit
        elif hit.n_amounts == 2:
            if best2 is None or hit.score >= best2.score:
                best2 = hit
    chosen = best3 if best3 is not None else best2
    if chosen is None:
        return None
    return chosen.sl, chosen.rp, chosen.n_amounts


def looks_like_battle_msg_clipboard(text: str) -> bool:
    if not text or len(text) < 24:
        return False
    if parse_battle_msg_clipboard(text) is not None:
        return True
    # Soft structural: footer-looking multi-line with 2+ currency amounts.
    zone = _footer_zone_lines(text)
    for line in zone:
        if len(_amounts_in_line(line)) >= 2:
            return text.count("\n") >= 2
    return False


def looks_like_messages_panel_clipboard(text: str) -> bool:
    """True when Ctrl+C likely hit the hangar Messages panel (any tab)."""
    if not text or len(text.strip()) < 40:
        return False
    if parse_battle_msg_clipboard(text) is not None:
        return True
    if _SESSION_HEX.search(text):
        return True
    # Structural soft: multi-line + hex-like or multiple amount lines.
    if text.count("\n") >= 3 and len(_footer_zone_lines(text)) >= 2:
        return True
    return False


def parse_battle_msg_clipboard(text: str) -> BattleReport | None:
    """
    Extract SL + free-RP from Messages dump footer.

    Maps free-RP (ВОД / Free RP) → ``research_points``.
    """
    if not text or not text.strip():
        return None
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    picked = _pick_footer_amounts(cleaned)
    if picked is None:
        return None
    sl, rp, n_amounts = picked
    outcome = detect_battle_outcome(cleaned)
    digest = hashlib.sha256(cleaned.encode("utf-8", errors="ignore")).hexdigest()[:32]
    session_id = _extract_session_id(cleaned)
    if session_id:
        digest = hashlib.sha256(
            f"{digest}:{session_id}".encode()
        ).hexdigest()[:32]
    # 3-amount Total means the battle detail is finished — not provisional.
    provisional = outcome == "undecided" and n_amounts < 3
    return BattleReport(
        captured_at_epoch_millis=int(time.time() * 1000),
        research_points=rp,
        silver_lions=sl,
        outcome=outcome,
        raw_hash=digest,
        confidence=0.98,
        source="clipboard-msg",
        provisional=provisional,
        session_id=session_id,
    )
