"""
Parse War Thunder «Messages → Battles» clipboard dumps (Ctrl+C).

Locale-agnostic: prefer structure (two currency amounts: SL + free-RP) over
hard-coded «Зароблено». Three-amount lines (SL + free-RP + RP, e.g. «Всього»
after repair) are rejected.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass

from .ocr_parse import BattleReport

# Currency unit aliases (lowercase). Order: longer phrases first in patterns.
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

_EARNED_BONUS = re.compile(
    r"(?:зароблено|заработано|earned|gewonnen|gagn[eé]|guadagnat)",
    re.IGNORECASE,
)
_SESSION = re.compile(
    r"(?:сесія|сессия|session)\s*[:=\s]\s*([0-9a-f]{6,})",
    re.IGNORECASE,
)
_OUTCOME_WIN = re.compile(
    r"(?:перемога|победа|victory|gewonnen|victoire)",
    re.IGNORECASE,
)
_OUTCOME_LOSS = re.compile(
    r"(?:поразка|поражение|defeat|niederlage|d[eé]faite)",
    re.IGNORECASE,
)


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


@dataclass(frozen=True)
class _CurrencyHit:
    kind: str  # "sl" | "free_rp" | "rp"
    amount: int
    start: int
    end: int


def _classify_unit(token: str) -> str | None:
    t = token.strip().lower()
    if not t:
        return None
    # Free-RP before bare RP so «free rp» / «вод» win over «rp» / «од».
    if _FREE_RP_RE.fullmatch(t):
        return "free_rp"
    if _SL_RE.fullmatch(t):
        return "sl"
    if _RP_RE.fullmatch(t):
        return "rp"
    return None


def _hits_in_line(line: str) -> list[_CurrencyHit]:
    """Find amount+unit pairs; unit may sit immediately after the number."""
    hits: list[_CurrencyHit] = []
    # number + optional punctuation + unit word(s)
    pair_re = re.compile(
        r"(?<!\d)(\d{1,3}(?:[\s\u00a0.,']\d{3})+|\d+)"
        r"\s*[,:]?\s*"
        r"([A-Za-zА-Яа-яЁёЇїІіЄєҐґ]{1,24}"
        r"(?:\s+[A-Za-zА-Яа-яЁёЇїІіЄєҐґ]{1,16}){0,3})",
        re.UNICODE,
    )
    for m in pair_re.finditer(line):
        amount = _parse_int_token(m.group(1))
        if amount is None or amount < 1:
            continue
        unit_raw = m.group(2).strip()
        # Trim trailing junk after first currency token cluster.
        unit_token = unit_raw
        kind = None
        # Try progressively shorter prefixes of the unit phrase.
        parts = unit_raw.split()
        for n in range(len(parts), 0, -1):
            candidate = " ".join(parts[:n])
            kind = _classify_unit(candidate)
            if kind is not None:
                unit_token = candidate
                break
        if kind is None:
            # Single-token fallback (СЛ / ВОД / ОД / SL / RP).
            first = parts[0] if parts else unit_raw
            kind = _classify_unit(first)
            unit_token = first
        if kind is None:
            continue
        end = m.start(2) + len(unit_token)
        hits.append(_CurrencyHit(kind=kind, amount=amount, start=m.start(), end=end))
    return hits


def _line_candidate(line: str) -> tuple[int, int, float] | None:
    """
    Return (sl, free_rp_as_report_rp, score) when the line is an earned-style
    two-currency row (SL + free RP) without a third RP amount.
    """
    hits = _hits_in_line(line)
    if not hits:
        return None
    sl_hits = [h for h in hits if h.kind == "sl"]
    frp_hits = [h for h in hits if h.kind == "free_rp"]
    rp_hits = [h for h in hits if h.kind == "rp"]
    # Structural reject: three-currency total (SL + ВОД + ОД).
    if len(sl_hits) >= 1 and len(frp_hits) >= 1 and len(rp_hits) >= 1:
        return None
    if len(sl_hits) != 1 or len(frp_hits) != 1:
        return None
    if rp_hits:
        return None
    sl = sl_hits[0].amount
    rp = frp_hits[0].amount
    if sl < 1 or rp < 1:
        return None
    score = 1.0
    if _EARNED_BONUS.search(line):
        score += 0.5
    # Prefer lines where SL appears before free-RP (natural reading order).
    if sl_hits[0].start < frp_hits[0].start:
        score += 0.1
    return sl, rp, score


def looks_like_battle_msg_clipboard(text: str) -> bool:
    if not text or len(text) < 24:
        return False
    if parse_battle_msg_clipboard(text) is not None:
        return True
    # Soft: multi-line + at least one SL unit and one free-RP unit somewhere.
    lower = text.lower()
    has_sl = any(u in lower for u in (" сл", " sl", "сл,", "sl,", "лions"))
    has_frp = any(u in lower for u in ("вод", "free rp", "frp", "free research"))
    return has_sl and has_frp and text.count("\n") >= 2


def looks_like_messages_panel_clipboard(text: str) -> bool:
    """True when Ctrl+C likely hit the hangar Messages panel (any tab)."""
    if not text or len(text.strip()) < 40:
        return False
    if parse_battle_msg_clipboard(text) is not None:
        return True
    if _SESSION.search(text):
        return True
    lower = text.lower()
    hints = (
        "ctrl+c",
        "ctrl + c",
        "буфера обміну",
        "буфер обмена",
        "clipboard",
        "зароблено",
        "заработано",
        "earned",
        "сесія",
        "сессия",
        "session",
        "битв",
        "battle",
    )
    return sum(1 for h in hints if h in lower) >= 2


def parse_battle_msg_clipboard(text: str) -> BattleReport | None:
    """
    Extract without-premium-equivalent SL + RP from a Messages clipboard dump.

    Maps free-RP (ВОД / Free RP) → ``research_points`` (matches results «без преміуму»).
    """
    if not text or not text.strip():
        return None
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    best: tuple[float, int, int] | None = None
    for line in cleaned.split("\n"):
        line = line.strip()
        if not line:
            continue
        cand = _line_candidate(line)
        if cand is None:
            continue
        sl, rp, score = cand
        if best is None or score > best[0]:
            best = (score, sl, rp)
    if best is None:
        return None
    _score, sl, rp = best
    outcome = "undecided"
    if _OUTCOME_WIN.search(cleaned):
        outcome = "victory"
    elif _OUTCOME_LOSS.search(cleaned):
        outcome = "defeat"
    digest = hashlib.sha256(cleaned.encode("utf-8", errors="ignore")).hexdigest()[:32]
    sess = _SESSION.search(cleaned)
    if sess:
        digest = hashlib.sha256(
            f"{digest}:{sess.group(1).lower()}".encode()
        ).hexdigest()[:32]
    return BattleReport(
        captured_at_epoch_millis=int(time.time() * 1000),
        research_points=rp,
        silver_lions=sl,
        outcome=outcome,
        raw_hash=digest,
        confidence=0.98,
        source="clipboard-msg",
    )
