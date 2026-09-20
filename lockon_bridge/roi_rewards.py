"""Digit / landmark ROI extraction for with/without-premium + totals cells."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from dataclasses import dataclass
from io import BytesIO

from PIL import Image

from .ocr_parse import _amounts_in, _plausible_reward_pair
from .ocr_preprocess import soft_upscale
from .roi_layout import is_full_client_frame, iter_reward_digit_rois

log = logging.getLogger("lockon_bridge.roi")

_LETTER = re.compile(r"[A-Za-zА-Яа-яІіЇїЄєҐґЁё]", re.UNICODE)


def _column_pair_usable(rp: int, sl: int) -> bool:
    """Reject OCR junk that passes the loose plausible check (e.g. RP==SL)."""
    if not _plausible_reward_pair(rp, sl):
        return False
    if sl <= rp:
        return False
    # 7262 + 72629 (trailing OCR ghost on SL) is not a real reward pair.
    trimmed_sl = _ocr_ghost_trim(sl)
    if trimmed_sl is not None and abs(trimmed_sl - rp) <= max(2, rp // 50):
        return False
    ratio = sl / max(1, rp)
    return 1.8 <= ratio <= 80.0

_TOTAL_LABEL = re.compile(
    r"всього|всего|vsego|bcboro|bcsoro|total|итого|gesamt",
    re.IGNORECASE,
)
# WinRT often splits «Без преміума» → «bea» + «rupeMiYMa» / «npeMiYMa».
_WITHOUT_LABEL = re.compile(
    r"без\s*прем|без\s*prem|without|ohne\s*premium|"
    r"be[zsаa3]|bez\s*prem|5[bв]\s*npe|ru?pe?[mn]i?[yуu0о]?m",
    re.IGNORECASE,
)
_WITH_LABEL = re.compile(
    r"з\s*прем|с\s*прем|with\s*prem|mit\s*premium|"
    r"[3zс]\s*npe|npe?[mn]i?[yуu0о]?m",
    re.IGNORECASE,
)


def _soft_upscale(image: Image.Image, *, min_height: int = 96) -> Image.Image:
    return soft_upscale(image, min_height=min_height)


def _png_bytes(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def windows_roi_text(image: Image.Image, *, prepared: bool = False) -> str:
    """Windows.Media.Ocr — digit-focus variants (or a single preprocessed crop)."""
    from .ocr_backends import windows_ocr_variants
    from .ocr_preprocess import digit_focus_variants

    variants = [("given", image)] if prepared else digit_focus_variants(image)
    best = ""
    best_score = -1
    for _tag, prep in variants:
        for _eng, text in windows_ocr_variants(_png_bytes(prep)):
            amounts = _amounts_in(text, min_value=50)
            score = len(amounts) * 10 + sum(len(str(a)) for a in amounts)
            if score > best_score:
                best_score = score
                best = text
    return best


def tesseract_digits_text(image: Image.Image, *, prepared: bool = False) -> str:
    """Digit-only Tesseract — whitelist 0-9, several PSM × preprocess variants."""
    from .ocr_backends import ensure_core_tessdata, find_tesseract_exe, tessdata_dir
    from .ocr_preprocess import digit_focus_variants

    exe = find_tesseract_exe()
    if exe is None:
        return ""
    try:
        import pytesseract
    except ImportError:
        return ""

    pytesseract.pytesseract.tesseract_cmd = str(exe)
    ensure_core_tessdata()
    local = tessdata_dir()
    use_local = any(local.glob("*.traineddata"))
    variants = [("given", image)] if prepared else digit_focus_variants(image)
    # Prepared crops already went through digit_focus — keep PSM set lean.
    if prepared:
        configs = [
            "--psm 7 -c tessedit_char_whitelist=0123456789",
            "--psm 8 -c tessedit_char_whitelist=0123456789",
        ]
    else:
        configs = [
            "--psm 7 -c tessedit_char_whitelist=0123456789",
            "--psm 8 -c tessedit_char_whitelist=0123456789",
            "--psm 13 -c tessedit_char_whitelist=0123456789",
        ]
    if use_local:
        configs = [f"--tessdata-dir {local} {c}" for c in configs]

    texts: list[str] = []
    for _tag, prep in variants:
        for config in configs:
            try:
                raw = pytesseract.image_to_string(prep, lang="eng", config=config) or ""
            except Exception as exc:  # noqa: BLE001
                log.debug("digit OCR failed (%s): %s", config, exc)
                continue
            cleaned = re.sub(r"[^\d\s]+", " ", raw)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                texts.append(cleaned)
    if not texts:
        return ""
    return max(texts, key=lambda t: (sum(ch.isdigit() for ch in t), len(t)))


def _ocr_ghost_trim(value: int) -> int | None:
    """
    Optional OCR trailing-digit trim. Never mutates real high rewards in place.

    Safe cases only:
      • trailing 9 on a 4-digit token (3799 → 379) — lion/bulb icon ghost
      • trailing 9 on a 5-digit token (10259 → 1025) — rare for real rewards
      • trailing 0 on a *non-round* 5-digit token (72620 → 7262), but NOT
        15000 / 20000 / 10000 (real thousand amounts farmers hit with premium)

    6+ digit values (100000+ SL) are never trimmed.
    """
    # Lion / bulb icon glued as trailing 9 on a 4-digit cell (379 → 3799).
    if 1_000 <= value <= 9_999 and value % 10 == 9:
        trimmed = value // 10
        if 50 <= trimmed <= 4_500:
            return trimmed
    if not (10_000 <= value <= 99_999):
        return None
    trimmed = value // 10
    if not (200 <= trimmed <= 45_000):
        return None
    if value % 10 == 9:
        return trimmed
    if value % 10 == 0 and value % 100 != 0:
        # 72620 → 7262; keep 15000 / 28000 / etc.
        return trimmed
    return None


def _prefer_amount_with_ghost_trim(amount: int) -> list[int]:
    """Raw amount first, then optional icon-ghost trim candidate."""
    out = [amount]
    trimmed = _ocr_ghost_trim(amount)
    if trimmed is not None and trimmed not in out:
        out.append(trimmed)
    return out


def _best_column_sl(rp: int, sl_candidates: list[int]) -> int | None:
    """Pick SL that pairs with RP; prefer classic arcade ratio + icon-ghost trim."""
    best: int | None = None
    best_score = -1.0
    for sl in sl_candidates:
        if not _column_pair_usable(rp, sl):
            continue
        ratio = sl / max(1, rp)
        score = 0.0
        if 2.5 <= ratio <= 20.0:
            score += 3.0
        elif 1.8 <= ratio < 2.5:
            score += 1.0
        elif 20.0 < ratio <= 40.0:
            score += 0.2
        # Prefer trimmed lion-ghost (3799→379) when both are usable.
        if sl != sl_candidates[0] and sl_candidates[0] % 10 == 9:
            score += 1.5
        if score > best_score:
            best_score = score
            best = sl
    return best


def pair_from_digit_text(text: str) -> tuple[int, int] | None:
    """Two reward amounts from an ROI reading (RP then SL)."""
    # Keep real premium-farm SL (100k+) / high RP; only drop absurd OCR monsters.
    raw = [a for a in _amounts_in(text, min_value=50) if a <= 250_000]
    if not raw:
        return None

    amounts = list(raw)
    ghost_of: dict[int, int] = {}
    for value in raw:
        trimmed = _ocr_ghost_trim(value)
        if trimmed is not None and trimmed not in amounts:
            amounts.append(trimmed)
            ghost_of[trimmed] = value

    if len(amounts) < 2:
        return None

    def _pair_score(rp: int, sl: int) -> float:
        if not _plausible_reward_pair(rp, sl) or sl <= rp:
            return -1.0
        ratio = sl / max(1, rp)
        score = 0.0
        if 4.0 <= ratio <= 20.0:
            score += 2.0
        elif 2.5 <= ratio < 4.0:
            score += 0.5
        if 200 <= rp <= 4_500:
            score += 1.0
        elif 4_500 < rp <= 50_000:
            score += 0.6  # high RP with boosters / premium account grind
        if 800 <= sl <= 45_000:
            score += 1.0
        elif 45_000 < sl <= 250_000:
            score += 0.6  # 100k+ SL is real — do not punish
        # Prefer ghost-trimmed pair only when BOTH sides came from OCR ghosts
        # (10259+72620 → 1025+7262), not when only one side trimmed (15000+72620).
        if rp in ghost_of and sl in ghost_of:
            score += 1.5
        elif rp in ghost_of or sl in ghost_of:
            score -= 0.25
        return score

    best: tuple[int, int] | None = None
    best_score = -1.0

    # Prefer reading order: first pair, and (only on 3-token lines) the trailing
    # adjacent pair so ``1050 1473 11834`` keeps real RP/SL not the crumb.
    if len(raw) >= 2:
        ordered: list[tuple[int, int, float]] = [(raw[0], raw[1], 0.0)]
        if len(raw) == 3:
            ordered.append((raw[-2], raw[-1], 1.25))
        for a, b, bonus in ordered:
            for rp, sl in ((a, b), (b, a)):
                score = _pair_score(rp, sl) + bonus
                if score > best_score:
                    best_score = score
                    best = (rp, sl)

    for i, rp in enumerate(amounts):
        for sl in amounts[i + 1 :]:
            for cand in ((rp, sl), (sl, rp)):
                score = _pair_score(*cand)
                if score > best_score:
                    best_score = score
                    best = cand
    return best


def _vote_pair(pairs: list[tuple[int, int]]) -> tuple[int, int] | None:
    if not pairs:
        return None
    counts = Counter(pairs)

    def _rank(candidate: tuple[int, int]) -> tuple[float, int]:
        rp, sl = candidate
        n = counts[candidate]
        score = float(n) * 10.0
        if not _plausible_reward_pair(rp, sl) or sl <= rp:
            return (score - 5.0, n)
        ratio = sl / max(1, rp)
        if 4.0 <= ratio <= 20.0:
            score += 3.0
        elif 2.5 <= ratio < 4.0:
            score += 1.0
        if 200 <= rp <= 4_500:
            score += 1.0
        if 800 <= sl <= 45_000:
            score += 1.0
        # Near-duplicate allies boost confidence.
        allies = sum(
            1
            for other_rp, other_sl in pairs
            if abs(other_rp - rp) <= max(15, int(rp * 0.02))
            and abs(other_sl - sl) <= max(30, int(sl * 0.02))
        )
        score += min(3, allies) * 0.5
        return (score, n)

    return max(counts.keys(), key=_rank)


def _read_roi_pair(
    crop: Image.Image,
    *,
    prefer_stable: bool = False,
) -> tuple[str, tuple[int, int] | None]:
    """
    Soft OCR for a digit crop — Windows OCR then Tesseract.

    ``prefer_stable`` is kept for call-site compatibility (always Win→Tess).
    """
    del prefer_stable  # historical flag; RapidOCR removed
    engines: list[tuple[str, object]] = [
        ("win", windows_roi_text),
        ("tess", tesseract_digits_text),
    ]

    first_text = ""
    first_pair: tuple[int, int] | None = None
    for _name, reader in engines:
        try:
            text = str(reader(crop) or "")
        except Exception:  # noqa: BLE001
            continue
        if not text:
            continue
        pair = pair_from_digit_text(text)
        if pair is not None and _column_pair_usable(*pair):
            return text, pair
        if not first_text:
            first_text = text
            first_pair = pair
    return first_text, first_pair


def _digit_amount_candidates(crop: Image.Image) -> list[tuple[str, int]]:
    """All plausible single-cell amounts from digit-focus OCR variants."""
    from .ocr_preprocess import digit_focus_variants

    out: list[tuple[str, int]] = []
    for tag, prep in digit_focus_variants(crop):
        # WinRT on colour soft / dim_soft only — B&W masks often hurt Windows.Media.Ocr.
        readers = (tesseract_digits_text,)
        if tag in ("soft", "dim_soft"):
            readers = (windows_roi_text, tesseract_digits_text)
        for reader in readers:
            try:
                text = str(reader(prep, prepared=True) or "")
            except Exception:  # noqa: BLE001
                continue
            if not text:
                continue
            for amount in _amounts_in(text, min_value=50):
                if amount <= 250_000:
                    out.append((text, int(amount)))
                    # Weak icon-ghost alternate (lion/bulb as trailing digit).
                    trimmed = _ocr_ghost_trim(int(amount))
                    if trimmed is not None:
                        out.append((str(trimmed), trimmed))
                    elif 10_000 <= int(amount) <= 99_999:
                        # Any trailing junk digit on a 5-digit cell (58694 → 5869).
                        soft = int(amount) // 10
                        if 200 <= soft <= 45_000:
                            out.append((str(soft), soft))
    return out


def _pick_voted_amount(cands: list[tuple[str, int]]) -> tuple[str, int] | None:
    """Majority vote; prefer longer digit strings (3–5) over 1–2 digit noise."""
    if not cands:
        return None
    votes: Counter[int] = Counter(amount for _text, amount in cands)

    def _rank(amount: int) -> tuple[int, int, int, int]:
        digits = len(str(amount))
        # Reward cells are almost always 3–5 digits.
        length_score = 3 if 3 <= digits <= 5 else (1 if digits == 2 else 0)
        return (votes[amount], length_score, digits, amount)

    best = max(votes.keys(), key=_rank)
    # Prefer a digits-only source so letter-gate does not reject a good vote.
    digit_only = [
        t
        for t, a in cands
        if a == best and t and not _LETTER.search(t)
    ]
    if digit_only:
        return max(digit_only, key=lambda t: sum(ch.isdigit() for ch in t)), best
    text = next(t for t, a in cands if a == best)
    # Synthesize clean text when every engine glued letters onto a solid amount.
    return str(best), best


def _read_roi_amount(
    crop: Image.Image,
    *,
    prefer_stable: bool = False,
) -> tuple[str, int | None]:
    """Single reward cell (RP or SL) — voted digit-only OCR across preprocess variants."""
    del prefer_stable
    picked = _pick_voted_amount(_digit_amount_candidates(crop))
    if picked is not None:
        return picked[0], picked[1]
    # Fallback: legacy dual-engine path (pair_from may still help odd crops).
    text, pair = _read_roi_pair(crop)
    amounts = [a for a in _amounts_in(text or "", min_value=50) if a <= 250_000]
    if amounts:
        return text or "", int(max(amounts))
    if pair is not None:
        return text, int(max(pair))
    return text or "", None


def _cell_is_clean_number(text: str, amount: int | None) -> bool:
    """True only when the cell yielded a number and OCR did not see letters."""
    if amount is None:
        return False
    if _LETTER.search(text or ""):
        return False
    return True


def save_error_parse_frame(image: Image.Image) -> None:
    """
    Keep the latest failed frame for calibrator tuning.

    Writes ``error_parse.png`` (overwrite) and a timestamped copy under
    ``captures/`` so older failures are not lost when adding ROI pairs later.
    """
    from .capture_archive import archive_capture_frame
    from .paths import data_root, error_parse_image_path

    path = error_parse_image_path()
    try:
        data_root().mkdir(parents=True, exist_ok=True)
        rgb = image if image.mode == "RGB" else image.convert("RGB")
        rgb.save(path, format="PNG", optimize=True)
        log.info("All calib pairs failed — wrote %s", path)
    except OSError as exc:
        log.warning("Could not write %s: %s", path, exc)
    archive_capture_frame(image, kind="fail", note="calib")


@dataclass(frozen=True)
class PairProbeRow:
    """One calib pair OCR attempt (for calibrator preview)."""

    pair_index: int
    rp_text: str
    sl_text: str
    research_points: int | None
    silver_lions: int | None
    accepted: bool
    reason: str  # ok | crop | letters | unusable


def _probe_one_pair_rects(
    image: Image.Image,
    rp_rect,
    sl_rect,
    *,
    pair_index: int,
    prefix: str,
) -> PairProbeRow:
    """OCR a single RP+SL NormRect pair (shared by runtime + calibrator preview)."""
    from .ocr_preprocess import blank_trailing_reward_icon
    from .roi_layout import crop_norm

    rp_crop = crop_norm(image, rp_rect)
    sl_crop = crop_norm(image, sl_rect)
    if rp_crop is None or sl_crop is None:
        log.debug("calib pair %s-p%s: crop missing", prefix, pair_index)
        return PairProbeRow(
            pair_index=pair_index,
            rp_text="",
            sl_text="",
            research_points=None,
            silver_lions=None,
            accepted=False,
            reason="crop",
        )

    rp_crop = blank_trailing_reward_icon(rp_crop)
    sl_crop = blank_trailing_reward_icon(sl_crop)
    rp_text, rp_amt = _read_roi_amount(rp_crop, prefer_stable=True)
    sl_text, sl_amt = _read_roi_amount(sl_crop, prefer_stable=True)
    if rp_amt is not None:
        trimmed_rp = _ocr_ghost_trim(rp_amt)
        if trimmed_rp is not None and rp_amt % 10 == 9:
            rp_amt = trimmed_rp
            rp_text = str(trimmed_rp)
    rp_ok = _cell_is_clean_number(rp_text, rp_amt)
    sl_ok = _cell_is_clean_number(sl_text, sl_amt)
    if not rp_ok or not sl_ok:
        log.info(
            "calib pair %s-p%s rejected (need both numeric): rp=%r sl=%r",
            prefix,
            pair_index,
            (rp_text or "")[:40],
            (sl_text or "")[:40],
        )
        return PairProbeRow(
            pair_index=pair_index,
            rp_text=rp_text or "",
            sl_text=sl_text or "",
            research_points=rp_amt if rp_ok else None,
            silver_lions=sl_amt if sl_ok else None,
            accepted=False,
            reason="letters",
        )
    assert rp_amt is not None and sl_amt is not None
    chosen_sl = _best_column_sl(rp_amt, _prefer_amount_with_ghost_trim(sl_amt))
    if chosen_sl is None:
        log.info(
            "calib pair %s-p%s rejected (unusable): %s/%s",
            prefix,
            pair_index,
            rp_amt,
            sl_amt,
        )
        return PairProbeRow(
            pair_index=pair_index,
            rp_text=rp_text or "",
            sl_text=sl_text or "",
            research_points=rp_amt,
            silver_lions=sl_amt,
            accepted=False,
            reason="unusable",
        )
    log.info(
        "calib pair %s-p%s OK → %s / %s",
        prefix,
        pair_index,
        rp_amt,
        chosen_sl,
    )
    return PairProbeRow(
        pair_index=pair_index,
        rp_text=rp_text or "",
        sl_text=sl_text or "",
        research_points=rp_amt,
        silver_lions=chosen_sl,
        accepted=True,
        reason="ok",
    )


def _without_invalid_vs_premium(
    rp: int,
    sl: int,
    *,
    prem_rp: int,
    prem_sl: int,
) -> bool:
    """
    Without-premium totals must be strictly below with-premium on both axes.

    Equal/higher RP or SL means the OCR box almost certainly read the premium
    column (or junk) — reject and try the next calib pair.
    """
    return rp >= prem_rp or sl >= prem_sl


def try_calibrated_column_pair(
    image: Image.Image,
    *,
    prefer_with: bool,
    record_failure: bool = True,
    calib=None,
    premium_ceiling: tuple[int, int] | None = None,
) -> tuple[int, int, int] | None:
    """
    Walk calibrated fallback pairs in order.

    Accept a pair only when BOTH RP and SL read as clean numbers (no letters in
    either cell). If one cell has letters / no digits → try the next pair.
    Without-premium also rejects values ≥ known with-premium RP/SL.
    When every pair fails and ``record_failure``, overwrite error_parse.png.
    ``calib`` — optional in-memory CalibratedRois (calibrator preview).
    """
    from .roi_calib import calibrated_all_pair_rects

    ceiling = premium_ceiling
    if not prefer_with and ceiling is None:
        # One recursive call into WITH only (that branch never asks for ceiling).
        with_hit = try_calibrated_column_pair(
            image,
            prefer_with=True,
            record_failure=False,
            calib=calib,
            premium_ceiling=None,
        )
        if with_hit is not None:
            ceiling = (int(with_hit[1]), int(with_hit[2]))

    stack = calibrated_all_pair_rects(prefer_with=prefer_with, calib=calib)
    if not stack:
        return None
    prefix = "with" if prefer_with else "without"
    for index, rp_rect, sl_rect in stack:
        row = _probe_one_pair_rects(
            image, rp_rect, sl_rect, pair_index=index, prefix=prefix
        )
        if not (
            row.accepted
            and row.research_points is not None
            and row.silver_lions is not None
        ):
            continue
        if (
            not prefer_with
            and ceiling is not None
            and _without_invalid_vs_premium(
                row.research_points,
                row.silver_lions,
                prem_rp=ceiling[0],
                prem_sl=ceiling[1],
            )
        ):
            log.info(
                "calib pair %s-p%s rejected (>= premium %s/%s): %s/%s",
                prefix,
                index,
                ceiling[0],
                ceiling[1],
                row.research_points,
                row.silver_lions,
            )
            continue
        return index, row.research_points, row.silver_lions
    if record_failure:
        save_error_parse_frame(image)
    return None


@dataclass(frozen=True)
class CalibPairsProbe:
    """Full per-pair dump for both premium columns (calibrator live preview)."""

    without: list[PairProbeRow]
    with_premium: list[PairProbeRow]

    def first_ok(self, *, with_premium: bool) -> PairProbeRow | None:
        rows = self.with_premium if with_premium else self.without
        for row in rows:
            if row.accepted:
                return row
        return None

    def format_lines(self) -> str:
        lines: list[str] = []

        def _block(title: str, rows: list[PairProbeRow]) -> None:
            lines.append(title)
            if not rows:
                lines.append("  (немає пар)")
                return
            first = next((r for r in rows if r.accepted), None)
            for row in rows:
                mark = "OK" if row.accepted else "—"
                if row.accepted:
                    body = f"RP {row.research_points} / SL {row.silver_lions}"
                elif row.reason == "crop":
                    body = "немає crop"
                elif row.reason == "ge_premium":
                    body = (
                        f">= преміум (RP {row.research_points} / "
                        f"SL {row.silver_lions})"
                    )
                elif row.reason == "unusable":
                    body = (
                        f"відхилено (RP {row.research_points} / "
                        f"SL {row.silver_lions})"
                    )
                else:
                    rp_show = (
                        str(row.research_points)
                        if row.research_points is not None
                        else repr((row.rp_text or "")[:24])
                    )
                    sl_show = (
                        str(row.silver_lions)
                        if row.silver_lions is not None
                        else repr((row.sl_text or "")[:24])
                    )
                    body = f"RP {rp_show} / SL {sl_show}"
                star = " <- first" if first is not None and row is first else ""
                lines.append(f"  #{row.pair_index + 1} [{mark}] {body}{star}")
            lines.append("")

        _block("Без преміуму:", self.without)
        _block("З преміумом:", self.with_premium)
        return "\n".join(lines).rstrip() + "\n"


def probe_all_calib_pairs(
    image: Image.Image,
    *,
    calib=None,
) -> CalibPairsProbe:
    """OCR every calib pair in both columns (no failure dump side-effect)."""
    from .roi_calib import calibrated_all_pair_rects

    def _col(with_prem: bool) -> list[PairProbeRow]:
        stack = calibrated_all_pair_rects(prefer_with=with_prem, calib=calib) or []
        prefix = "with" if with_prem else "without"
        return [
            _probe_one_pair_rects(
                image, rp_rect, sl_rect, pair_index=index, prefix=prefix
            )
            for index, rp_rect, sl_rect in stack
        ]

    with_rows = _col(True)
    without_rows = _col(False)
    prem = next((r for r in with_rows if r.accepted), None)
    if (
        prem is not None
        and prem.research_points is not None
        and prem.silver_lions is not None
    ):
        filtered: list[PairProbeRow] = []
        for row in without_rows:
            if (
                row.accepted
                and row.research_points is not None
                and row.silver_lions is not None
                and _without_invalid_vs_premium(
                    row.research_points,
                    row.silver_lions,
                    prem_rp=prem.research_points,
                    prem_sl=prem.silver_lions,
                )
            ):
                log.info(
                    "calib preview without-p%s rejected (>= premium %s/%s): %s/%s",
                    row.pair_index,
                    prem.research_points,
                    prem.silver_lions,
                    row.research_points,
                    row.silver_lions,
                )
                filtered.append(
                    PairProbeRow(
                        pair_index=row.pair_index,
                        rp_text=row.rp_text,
                        sl_text=row.sl_text,
                        research_points=row.research_points,
                        silver_lions=row.silver_lions,
                        accepted=False,
                        reason="ge_premium",
                    )
                )
            else:
                filtered.append(row)
        without_rows = filtered

    return CalibPairsProbe(without=without_rows, with_premium=with_rows)


@dataclass(frozen=True)
class ColumnProbeHit:
    pair_index: int  # 0-based calib pair
    research_points: int
    silver_lions: int


@dataclass(frozen=True)
class DualColumnProbe:
    """Both premium columns probed independently (for Test OCR diagnostics)."""

    prefer_with: bool
    without: ColumnProbeHit | None
    with_premium: ColumnProbeHit | None

    def banked(self) -> ColumnProbeHit | None:
        return self.with_premium if self.prefer_with else self.without

    def to_dict(self) -> dict:
        def _hit(h: ColumnProbeHit | None) -> dict | None:
            if h is None:
                return None
            return {
                "pairIndex": h.pair_index,
                "pairNumber": h.pair_index + 1,
                "researchPoints": h.research_points,
                "silverLions": h.silver_lions,
            }

        banked = self.banked()
        return {
            "preferWith": self.prefer_with,
            "without": _hit(self.without),
            "with": _hit(self.with_premium),
            "banked": _hit(banked),
        }

    def format_lines(self, *, uk: bool = True) -> str:
        if uk:
            pref = "З преміумом" if self.prefer_with else "Без преміуму"
            lines = [f"Налаштування Bridge: банк колонки «{pref}»", ""]

            def _fmt(label: str, hit: ColumnProbeHit | None) -> str:
                if hit is None:
                    return f"{label}: не зчитано (жодна calib-пара)"
                return (
                    f"{label}: пара #{hit.pair_index + 1} -> "
                    f"RP {hit.research_points} / SL {hit.silver_lions}"
                )

            lines.append(_fmt("Без преміуму", self.without))
            lines.append(_fmt("З преміумом", self.with_premium))
            banked = self.banked()
            lines.append("")
            if banked is None:
                lines.append(f"У звіт піде: (немає) — колонка «{pref}» порожня")
            else:
                lines.append(
                    f"У звіт піде: «{pref}» пара #{banked.pair_index + 1} -> "
                    f"RP {banked.research_points} / SL {banked.silver_lions}"
                )
            return "\n".join(lines)

        pref = "WITH premium" if self.prefer_with else "WITHOUT premium"
        lines = [f"Bridge setting: bank «{pref}» column", ""]

        def _fmt_en(label: str, hit: ColumnProbeHit | None) -> str:
            if hit is None:
                return f"{label}: not read (no calib pair)"
            return (
                f"{label}: pair #{hit.pair_index + 1} -> "
                f"RP {hit.research_points} / SL {hit.silver_lions}"
            )

        lines.append(_fmt_en("Without premium", self.without))
        lines.append(_fmt_en("With premium", self.with_premium))
        banked = self.banked()
        lines.append("")
        if banked is None:
            lines.append(f"Would publish: (none) — «{pref}» empty")
        else:
            lines.append(
                f"Would publish: «{pref}» pair #{banked.pair_index + 1} -> "
                f"RP {banked.research_points} / SL {banked.silver_lions}"
            )
        return "\n".join(lines)


def probe_both_premium_columns(
    image: Image.Image,
    *,
    prefer_with: bool | None = None,
    calib=None,
) -> DualColumnProbe:
    """Read WITH and WITHOUT calibrated stacks separately (no side-effect dump)."""
    if prefer_with is None:
        try:
            from .settings import load_settings

            prefer_with = bool(load_settings().has_premium_account)
        except Exception:  # noqa: BLE001
            prefer_with = False

    def _one(with_col: bool) -> ColumnProbeHit | None:
        hit = try_calibrated_column_pair(
            image, prefer_with=with_col, record_failure=False, calib=calib
        )
        if hit is None:
            return None
        index, rp, sl = hit
        return ColumnProbeHit(pair_index=index, research_points=rp, silver_lions=sl)

    without = _one(False)
    with_prem = _one(True)
    # If both miss, keep one failure dump for calibrator.
    if without is None and with_prem is None:
        save_error_parse_frame(image)
    return DualColumnProbe(
        prefer_with=bool(prefer_with),
        without=without,
        with_premium=with_prem,
    )


# --- Landmark path (WinRT word boxes) ---


async def _windows_words(png: bytes, lang_tag: str) -> list[tuple[str, float, float, float, float]]:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(png)
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    try:
        language = Language(lang_tag)
        engine = (
            OcrEngine.try_create_from_language(language)
            if OcrEngine.is_language_supported(language)
            else None
        )
    except Exception:  # noqa: BLE001
        engine = None
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return []
    result = await engine.recognize_async(bitmap)
    out: list[tuple[str, float, float, float, float]] = []
    for line in result.lines:
        for word in line.words:
            rect = word.bounding_rect
            out.append(
                (
                    str(word.text),
                    float(rect.x),
                    float(rect.y),
                    float(rect.width),
                    float(rect.height),
                )
            )
    return out


def _raw_digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _glue_spaced_amounts(
    words: list[tuple[str, float, float, float, float]],
    *,
    width: float,
    height: float,
) -> list[tuple[int, float, float]]:
    """
    Rebuild reward amounts from word boxes, gluing ``2`` + ``421`` → 2421.

    WinRT often emits the thousands digit as its own word on the SL row.
    """
    tokens: list[tuple[str, float, float]] = []
    for text, x, y, w, _h in words:
        digits = _raw_digits(text)
        if not digits:
            continue
        nx = (x + w * 0.5) / max(1.0, width)
        ny = y / max(1.0, height)
        tokens.append((digits, nx, ny))

    tokens.sort(key=lambda item: (round(item[2], 3), item[1]))
    amounts: list[tuple[int, float, float]] = []
    index = 0
    while index < len(tokens):
        digits, nx, ny = tokens[index]
        # Glue 1–2 digit thousands head with a 3-digit body on the same row.
        if (
            index + 1 < len(tokens)
            and 1 <= len(digits) <= 2
            and int(digits) < 50
            and len(tokens[index + 1][0]) == 3
            and abs(tokens[index + 1][2] - ny) <= 0.02
            and 0.0 < (tokens[index + 1][1] - nx) <= 0.06
        ):
            body = tokens[index + 1][0]
            # Drop a trailing icon ghost on the body (4219 → 421) when 4 digits leaked in.
            value = int(digits) * 1000 + int(body)
            mid_x = (nx + tokens[index + 1][1]) / 2.0
            amounts.append((value, mid_x, ny))
            index += 2
            continue
        # 4-digit body that is really 3-digit + ghost (4219) next to a 1-digit head
        # already handled above; standalone 3–6 digit tokens:
        if len(digits) >= 3:
            try:
                value = int(digits)
            except ValueError:
                index += 1
                continue
            # Soft-trim trailing 9 from 4-digit OCR when value looks like SL body+ghost.
            if len(digits) == 4 and value % 10 == 9 and 200 <= value // 10 <= 999:
                # Prefer as-is if already a plausible standalone amount (≥1000).
                if value < 2000:
                    value = value // 10
            if 50 <= value <= 250_000:
                amounts.append((value, nx, ny))
        index += 1
    return amounts


def pairs_from_landmarks(image: Image.Image) -> list[tuple[str, str]]:
    """
    Locate with/without / «Всього» via word boxes, then take nearby amounts.

    Coordinates come from OCR boxes → independent of absolute pixel density.
    """
    prepared = _soft_upscale(image, min_height=720)
    png = _png_bytes(prepared)
    width, height = prepared.size
    words: list[tuple[str, float, float, float, float]] = []
    for lang in ("ru", "en-US", "uk"):
        try:
            words = asyncio.run(_windows_words(png, lang))
        except Exception as exc:  # noqa: BLE001
            log.debug("landmark OCR %s failed: %s", lang, exc)
            words = []
        if len(words) >= 20:
            break
    if not words:
        return []

    def norm(x: float, y: float) -> tuple[float, float]:
        return x / max(1.0, width), y / max(1.0, height)

    amount_words = _glue_spaced_amounts(words, width=float(width), height=float(height))
    labels_total: list[tuple[float, float]] = []
    labels_without: list[tuple[float, float]] = []
    labels_with: list[tuple[float, float]] = []
    for text, x, y, w, _h in words:
        nx, ny = norm(x + w * 0.5, y)
        if _TOTAL_LABEL.search(text):
            labels_total.append((nx, ny))
        # Prefer explicit without markers; avoid classifying bare «npeMiYM» as with
        # when a without-ish token sits on the same row.
        if _WITHOUT_LABEL.search(text) and not re.match(r"^[3zс]$", text, re.IGNORECASE):
            # «npeMiYMa» alone is ambiguous — only count when text looks without-ish
            # or sits to the right of a short «bea/bez» sibling (handled via band).
            if re.search(r"be[zsаa3]|без|without|ohne|bez", text, re.IGNORECASE) or re.search(
                r"ru?pe?[mn]i", text, re.IGNORECASE
            ):
                labels_without.append((nx, ny))
        if _WITH_LABEL.search(text) and not re.search(
            r"be[zsаa3]|без|without|ohne", text, re.IGNORECASE
        ):
            labels_with.append((nx, ny))

    # Merge adjacent «3» + «npeMiYM0M» / «bea» + «rupeMiYMa» into column anchors.
    short_tokens = []
    for text, x, y, w, _h in words:
        nx, ny = norm(x + w * 0.5, y)
        short_tokens.append((text, nx, ny))
    for i, (text, nx, ny) in enumerate(short_tokens):
        if re.match(r"^[3zсЗ]$", text) and i + 1 < len(short_tokens):
            nxt, nx2, ny2 = short_tokens[i + 1]
            if abs(ny2 - ny) <= 0.02 and _WITH_LABEL.search(nxt):
                labels_with.append(((nx + nx2) / 2.0, ny))
        if re.match(r"^be[zsаa3]?$", text, re.IGNORECASE) and i + 1 < len(short_tokens):
            nxt, nx2, ny2 = short_tokens[i + 1]
            if abs(ny2 - ny) <= 0.02 and re.search(r"npe|prem|pe?[mn]i", nxt, re.IGNORECASE):
                labels_without.append(((nx + nx2) / 2.0, ny))

    variants: list[tuple[str, str]] = []

    def pair_near(
        label_xy: tuple[float, float],
        *,
        x_min: float | None = None,
        x_max: float | None = None,
        band: float = 0.07,
        y_bias: float = 0.03,
    ) -> tuple[int, int] | None:
        lx, ly = label_xy
        target_y = ly + y_bias
        nearby = [
            (value, nx, ny)
            for value, nx, ny in amount_words
            if abs(ny - target_y) <= band
            and (x_min is None or nx >= x_min)
            and (x_max is None or nx <= x_max)
        ]
        nearby.sort(key=lambda item: (abs(item[2] - target_y), item[1]))
        values = [item[0] for item in nearby[:6]]
        return pair_from_digit_text(" ".join(str(v) for v in values))

    for lx, ly in labels_total:
        pair = pair_near((lx, ly), x_min=lx - 0.02, band=0.035, y_bias=0.0)
        if pair:
            rp, sl = pair
            variants.append(("roi:landmark-total", f"Всього {rp} {sl}"))

    for lx, ly in labels_without:
        pair = pair_near((lx, ly), x_min=lx - 0.04, x_max=lx + 0.10)
        if pair is None:
            pair = pair_near((lx, ly), x_min=lx - 0.02)
        if pair:
            rp, sl = pair
            variants.append(("roi:landmark-without", f"Без преміума {rp} {sl}"))

    for lx, ly in labels_with:
        pair = pair_near((lx, ly), x_min=lx - 0.04, x_max=lx + 0.08)
        if pair:
            rp, sl = pair
            variants.append(("roi:landmark-with", f"З преміумом {rp} {sl}"))

    # Fallback bands: left-panel full client + mid-panel chat crops.
    full = is_full_client_frame(image)

    def band_pair(
        tag: str,
        label: str,
        *,
        x0: float,
        x1: float,
        y0: float,
        y1: float,
    ) -> None:
        band = [(v, x, y) for v, x, y in amount_words if y0 <= y <= y1 and x0 <= x <= x1]
        if not band:
            return
        band.sort(key=lambda item: (item[2], item[1]))
        pair = pair_from_digit_text(" ".join(str(v) for v, _, _ in band[:6]))
        if pair:
            rp, sl = pair
            variants.append((tag, f"{label} {rp} {sl}"))

    if full:
        band_pair("roi:band-with", "З преміумом", x0=0.20, x1=0.295, y0=0.08, y1=0.18)
        band_pair("roi:band-without", "Без преміума", x0=0.290, x1=0.380, y0=0.08, y1=0.18)
        band_pair("roi:band-with", "З преміумом", x0=0.50, x1=0.575, y0=0.08, y1=0.17)
        band_pair("roi:band-without", "Без преміума", x0=0.575, x1=0.650, y0=0.08, y1=0.17)
        for x0, x1 in ((0.20, 0.38), (0.50, 0.65)):
            panel = [
                (v, x, y) for v, x, y in amount_words if 0.08 <= y <= 0.18 and x0 <= x <= x1
            ]
            if len(panel) < 4:
                continue
            panel.sort(key=lambda item: (item[2], item[1]))
            xs = sorted(item[1] for item in panel)
            mid_x = xs[len(xs) // 2]
            with_vals = [v for v, x, _y in panel if x < mid_x]
            without_vals = [v for v, x, _y in panel if x >= mid_x]
            with_pair = pair_from_digit_text(" ".join(str(v) for v in with_vals[:4]))
            without_pair = pair_from_digit_text(" ".join(str(v) for v in without_vals[:4]))
            if with_pair:
                rp, sl = with_pair
                variants.append(("roi:band-with", f"З преміумом {rp} {sl}"))
            if without_pair:
                rp, sl = without_pair
                variants.append(("roi:band-without", f"Без преміума {rp} {sl}"))
    else:
        band_pair("roi:band-without", "Без преміума", x0=0.40, x1=0.55, y0=0.12, y1=0.24)
    band_pair("roi:band-total", "Всього", x0=0.48, x1=0.68, y0=0.42, y1=0.50)

    return variants


def extract_roi_reward_variants(
    image: Image.Image,
    *,
    prefer_with: bool | None = None,
    dense: bool = False,
) -> list[tuple[str, str]]:
    """
    Synthetic OCR texts for choose_best / parse_rewards_from_ocr_text.

    Digits first (fast). Landmark WinRT only if digit votes are weak. Dense ROI
    set is a fallback when the lean set cannot form a usable preferred pair.

    Digit-backed header beats «Всього». Band/landmark-only header never beats a
    strong total vote (avoids 2946/7399 junk over live 1473/11834).
    """
    if prefer_with is None:
        try:
            from .settings import load_settings

            prefer_with = bool(load_settings().has_premium_account)
        except Exception:  # noqa: BLE001
            prefer_with = False

    variants: list[tuple[str, str]] = []
    with_digit_pairs: list[tuple[int, int]] = []
    without_digit_pairs: list[tuple[int, int]] = []
    total_digit_pairs: list[tuple[int, int]] = []
    with_band_pairs: list[tuple[int, int]] = []
    without_band_pairs: list[tuple[int, int]] = []
    total_band_pairs: list[tuple[int, int]] = []

    def _ingest_digit_rois(*, use_dense: bool) -> None:
        # Calibrated stack: try pair0 → pair1 → … until RP+SL are real numbers.
        if not use_dense:
            from .roi_calib import has_usable_calibration

            if has_usable_calibration():
                hit = try_calibrated_column_pair(image, prefer_with=bool(prefer_with))
                if hit is not None:
                    index, rp, sl = hit
                    label = "З преміумом" if prefer_with else "Без преміума"
                    variants.insert(0, (f"roi:calib-p{index}", f"{label} {rp} {sl}"))
                    bucket = with_digit_pairs if prefer_with else without_digit_pairs
                    bucket.extend([(rp, sl), (rp, sl), (rp, sl)])
                    return
                # All pairs failed (letters/junk) — fall through to catalogue ROIs.

        for tag, crop in iter_reward_digit_rois(
            image, dense=use_dense, prefer_with=prefer_with
        ):
            # When calib exists, lean calibrated cells were already tried above.
            if (
                not use_dense
                and (tag.endswith("-rp") or tag.endswith("-sl"))
                and (tag.startswith("with") or tag.startswith("without"))
            ):
                from .roi_calib import has_usable_calibration

                if has_usable_calibration():
                    continue

            # Calibrated single-cell tags: with-rp / with-sl / without-rp / without-sl
            if tag.endswith("-rp") or tag.endswith("-sl"):
                text, amount = _read_roi_amount(crop)
                if amount is None:
                    if text:
                        variants.append((f"roi:{tag}", text))
                    continue
                variants.append((f"roi:{tag}", str(amount)))
                continue

            text, pair = _read_roi_pair(crop)
            if not text:
                continue
            if pair is None:
                variants.append((f"roi:{tag}", text))
                continue
            rp, sl = pair
            if tag.startswith("with") and not tag.startswith("without"):
                with_digit_pairs.extend([pair, pair])
                labeled = f"З преміумом {rp} {sl}"
            elif tag.startswith("without"):
                without_digit_pairs.extend([pair, pair])
                labeled = f"Без преміума {rp} {sl}"
            elif tag.startswith("both"):
                from .ocr_parse import (
                    _with_pair_from_premium_amounts,
                    _without_pair_from_premium_amounts,
                )

                amounts = [a for a in _amounts_in(text, min_value=50) if a <= 250_000]
                w_pair = _with_pair_from_premium_amounts(amounts)
                wo_pair = _without_pair_from_premium_amounts(amounts)
                got_cols = False
                w_ok = w_pair[0] is not None and w_pair[1] is not None
                wo_ok = wo_pair[0] is not None and wo_pair[1] is not None
                if w_ok:
                    with_digit_pairs.extend([(w_pair[0], w_pair[1])] * 2)
                    variants.append((f"roi:{tag}-with", f"З преміумом {w_pair[0]} {w_pair[1]}"))
                    got_cols = True
                if wo_ok:
                    # Same pair as with ⇒ OCR only saw one column — do not invent without.
                    if not w_ok or (wo_pair[0], wo_pair[1]) != (w_pair[0], w_pair[1]):
                        without_digit_pairs.extend([(wo_pair[0], wo_pair[1])] * 2)
                        variants.append(
                            (
                                f"roi:{tag}-without",
                                f"Без преміума {wo_pair[0]} {wo_pair[1]}",
                            )
                        )
                        got_cols = True
                # Naive left-to-right pair pollutes the preferred column when OCR
                # emits all four cells (1720 921 10955 6817 → fake 1720/9210).
                if got_cols:
                    continue
                if prefer_with:
                    with_digit_pairs.append(pair)
                    labeled = f"З преміумом {rp} {sl}"
                else:
                    without_digit_pairs.append(pair)
                    labeled = f"Без преміума {rp} {sl}"
            else:
                total_digit_pairs.extend([pair, pair])
                labeled = f"Всього {rp} {sl}"
            variants.append((f"roi:{tag}", labeled))

        # Assemble calibrated RP+SL cells into a column pair.
        rp_hits = [
            int(text)
            for tag, text in variants
            if tag.endswith("-rp") and text.isdigit()
        ]
        sl_hits = [
            int(text)
            for tag, text in variants
            if tag.endswith("-sl") and text.isdigit()
        ]
        if rp_hits and sl_hits:
            pair = (rp_hits[0], sl_hits[0])
            if _column_pair_usable(*pair):
                label = "З преміумом" if prefer_with else "Без преміума"
                bucket = with_digit_pairs if prefer_with else without_digit_pairs
                bucket.extend([pair, pair, pair])
                variants.insert(0, ("roi:calibrated", f"{label} {pair[0]} {pair[1]}"))

    _ingest_digit_rois(use_dense=dense)

    def _usable(pair: tuple[int, int] | None) -> bool:
        return pair is not None and _column_pair_usable(*pair)

    preferred_digit = _vote_pair(with_digit_pairs if prefer_with else without_digit_pairs)
    total_early = _vote_pair(total_digit_pairs)
    # Bankable if header OR total already works — skip dense/landmarks.
    lean_ok = _usable(preferred_digit) or _usable(total_early)
    if not lean_ok and not dense:
        _ingest_digit_rois(use_dense=True)
        preferred_digit = _vote_pair(with_digit_pairs if prefer_with else without_digit_pairs)
        total_early = _vote_pair(total_digit_pairs)
        lean_ok = _usable(preferred_digit) or _usable(total_early)

    if not lean_ok:
        # Landmark WinRT over the full frame is slow; skip when calibrated pairs
        # already covered the preferred column (fallback dense ROIs are enough).
        from .roi_calib import has_usable_calibration

        if not has_usable_calibration():
            try:
                landmark_variants = pairs_from_landmarks(image)
                variants.extend(landmark_variants)
                for tag, text in landmark_variants:
                    pair = pair_from_digit_text(text)
                    if pair is None:
                        continue
                    if "without" in tag:
                        without_band_pairs.append(pair)
                    elif "with" in tag and "without" not in tag:
                        with_band_pairs.append(pair)
                    else:
                        total_band_pairs.append(pair)
            except Exception as exc:  # noqa: BLE001
                log.warning("landmark ROI failed: %s", exc)

    with_digit_vote = _vote_pair(with_digit_pairs)
    # If a «without» crop actually read the with column (or higher), drop those.
    if with_digit_vote is not None:
        wrp, wsl = with_digit_vote
        without_digit_pairs = [
            p
            for p in without_digit_pairs
            if not _without_invalid_vs_premium(p[0], p[1], prem_rp=wrp, prem_sl=wsl)
        ]
        without_band_pairs = [
            p
            for p in without_band_pairs
            if not _without_invalid_vs_premium(p[0], p[1], prem_rp=wrp, prem_sl=wsl)
        ]
    without_digit_vote = _vote_pair(without_digit_pairs)
    total_digit_vote = _vote_pair(total_digit_pairs)
    with_band_vote = _vote_pair(with_band_pairs)
    without_band_vote = _vote_pair(without_band_pairs)
    total_band_vote = _vote_pair(total_band_pairs)

    # Prefer digit ROI votes; band/landmark only fills gaps.
    with_vote = with_digit_vote or with_band_vote
    without_vote = without_digit_vote or without_band_vote
    total = total_digit_vote or total_band_vote
    digit_pref = with_digit_vote if prefer_with else without_digit_vote
    preferred_digit_ok = digit_pref is not None and _column_pair_usable(*digit_pref)
    preferred = with_vote if prefer_with else without_vote
    other = without_vote if prefer_with else with_vote

    if preferred and total and not prefer_with:
        pref_ok = _column_pair_usable(*preferred)
        tot_ok = _column_pair_usable(*total)
        if preferred == total and pref_ok:
            rp, sl = preferred
            variants.insert(0, ("roi:consensus", f"Без преміума {rp} {sl}\nВсього {rp} {sl}"))
        elif pref_ok and preferred[0] == total[0]:
            rp = preferred[0]
            sl = total[1] if tot_ok else preferred[1]
            variants.insert(0, ("roi:consensus-total-sl", f"Без преміума {rp} {sl}\nВсього {rp} {sl}"))
        elif pref_ok and tot_ok and preferred[1] == total[1] and preferred_digit_ok:
            # Same SL, different RP: digit-backed header beats free-research Всього RP.
            rp, sl = preferred
            variants.insert(
                0,
                ("roi:consensus-without-rp", f"Без преміума {rp} {sl}\nВсього {total[0]} {sl}"),
            )
        elif pref_ok and preferred_digit_ok:
            rp, sl = preferred
            variants.insert(0, ("roi:without-over-total", f"Без преміума {rp} {sl}"))
        elif tot_ok:
            # Band-only / weak header must not beat a solid Всього (live 1473/11834).
            rp, sl = total
            variants.insert(0, ("roi:prefer-total", f"Без преміума {rp} {sl}\nВсього {rp} {sl}"))
        elif pref_ok:
            rp, sl = preferred
            variants.insert(0, ("roi:without-over-total", f"Без преміума {rp} {sl}"))
    elif preferred:
        # Digit-backed preferred alone, or band when no total.
        if prefer_with or preferred_digit_ok or total is None:
            rp, sl = preferred
            label = "З преміумом" if prefer_with else "Без преміума"
            tag = "roi:with-only" if prefer_with else "roi:without-only"
            variants.insert(0, (tag, f"{label} {rp} {sl}"))
            if other and other != preferred:
                o_rp, o_sl = other
                o_label = "Без преміума" if prefer_with else "З преміумом"
                variants.insert(1, ("roi:other-column", f"{o_label} {o_rp} {o_sl}"))
        elif total and _column_pair_usable(*total):
            rp, sl = total
            variants.insert(0, ("roi:prefer-total", f"Без преміума {rp} {sl}\nВсього {rp} {sl}"))
        else:
            rp, sl = preferred
            variants.insert(0, ("roi:without-only", f"Без преміума {rp} {sl}"))
    elif total and not prefer_with:
        rp, sl = total
        variants.insert(0, ("roi:total-only", f"Всього {rp} {sl}"))
    elif without_vote and not prefer_with:
        rp, sl = without_vote
        variants.insert(0, ("roi:without-only", f"Без преміума {rp} {sl}"))
    elif with_vote and prefer_with:
        rp, sl = with_vote
        variants.insert(0, ("roi:with-only", f"З преміумом {rp} {sl}"))
    # Do NOT fall back to the other premium column — that banks the wrong totals.

    return variants


def report_from_roi_image(image: Image.Image, *, prefer_with: bool | None = None):
    """
    High-confidence report when ROI / landmark pairs resolve.

    Hard rule: bank the preferred premium **header** column first.
    «Всього» / total-row tags are only used when no usable header pair exists.
    """
    from .ocr_parse import parse_rewards_from_ocr_text

    if prefer_with is None:
        try:
            from .settings import load_settings

            prefer_with = bool(load_settings().has_premium_account)
        except Exception:  # noqa: BLE001
            prefer_with = False

    variants = extract_roi_reward_variants(image, prefer_with=prefer_with)

    total_tags = {
        "roi:prefer-total",
        "roi:total-only",
        "roi:landmark-total",
        "roi:band-total",
    }

    def _try_tag(tag: str, text: str):
        if not prefer_with and tag in (
            "roi:landmark-with",
            "roi:band-with",
            "roi:with-only",
            "roi:other-column",
        ):
            # Never bank the with-premium column when the phone wants without.
            return None
        if prefer_with and tag in (
            "roi:landmark-without",
            "roi:band-without",
            "roi:without-only",
            "roi:without-over-total",
            "roi:other-column",
        ):
            return None
        report = parse_rewards_from_ocr_text(text, prefer_premium_rewards=prefer_with)
        if report is None:
            return None
        # Landmark/band junk (e.g. 7262/7262) must not bank ahead of prefer-total.
        if not _column_pair_usable(report.research_points, report.silver_lions):
            return None
        if tag.startswith("roi:consensus"):
            report.confidence = max(report.confidence, 0.94)
        elif tag.startswith("roi:landmark") or tag.startswith("roi:band"):
            report.confidence = max(report.confidence, 0.90)
        elif tag in ("roi:with-only", "roi:without-only", "roi:without-over-total"):
            report.confidence = max(report.confidence, 0.92)
        elif tag in ("roi:prefer-total", "roi:total-only"):
            report.confidence = max(report.confidence, 0.90)
        else:
            report.confidence = max(report.confidence, 0.82)
        report.source = "ocr-roi"
        return report

    # Pass 1: consensus / clean header columns only (skip raw band until after).
    strong_header = {
        "roi:without-over-total",
        "roi:without-only",
        "roi:with-only",
        "roi:landmark-without",
        "roi:landmark-with",
    }
    for tag, text in variants:
        if not tag.startswith("roi:"):
            continue
        if tag.startswith("roi:consensus") or tag in strong_header:
            report = _try_tag(tag, text)
            if report is not None:
                return report

    # Pass 2: total / prefer-total (chat crops when header OCR is junk).
    for tag, text in variants:
        if tag in total_tags:
            report = _try_tag(tag, text)
            if report is not None:
                return report

    # Pass 3: last-resort band landmarks (only if usable).
    for tag, text in variants:
        if tag in ("roi:band-without", "roi:band-with"):
            report = _try_tag(tag, text)
            if report is not None:
                return report
    return None
