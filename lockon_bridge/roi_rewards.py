"""Digit / landmark ROI extraction for with/without-premium + totals cells."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from io import BytesIO

from PIL import Image, ImageEnhance, ImageOps

from .ocr_parse import _amounts_in, _plausible_reward_pair
from .roi_layout import is_full_client_frame, iter_reward_digit_rois

log = logging.getLogger("lockon_bridge.roi")


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
    """Upscale small ROI crops without harsh binarization (keeps blue RP glyphs)."""
    scale = 1.0
    if image.height < min_height:
        scale = min_height / float(image.height)
    scale = max(scale, 2.0)
    width = max(1, int(image.width * scale))
    height = max(1, int(image.height * scale))
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    image = ImageOps.autocontrast(image, cutoff=1)
    image = ImageEnhance.Contrast(image).enhance(1.35)
    image = ImageEnhance.Sharpness(image).enhance(1.25)
    return image


def _png_bytes(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def tesseract_digits_text(image: Image.Image) -> str:
    """OCR preferring digits — soft preprocess (no hard binary)."""
    from .ocr_backends import ensure_core_tessdata, find_tesseract_exe, tessdata_dir

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
    prepared = _soft_upscale(image)
    configs = [
        "--psm 6 -c tessedit_char_whitelist=0123456789 ",
        "--psm 7 -c tessedit_char_whitelist=0123456789 ",
    ]
    if use_local:
        configs = [f"--tessdata-dir {local} {c}" for c in configs]

    texts: list[str] = []
    for config in configs:
        try:
            raw = pytesseract.image_to_string(prepared, lang="eng", config=config) or ""
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


def windows_roi_text(image: Image.Image) -> str:
    """Windows.Media.Ocr on a soft-upscaled ROI (best for blue RP digits)."""
    from .ocr_backends import windows_ocr_variants

    prepared = _soft_upscale(image)
    variants = windows_ocr_variants(_png_bytes(prepared))
    if not variants:
        return ""
    # Prefer the variant with the most plausible reward digits.
    best = ""
    best_score = -1
    for _tag, text in variants:
        amounts = _amounts_in(text, min_value=50)
        score = len(amounts) * 10 + sum(len(str(a)) for a in amounts)
        if score > best_score:
            best_score = score
            best = text
    return best


def _ocr_ghost_trim(value: int) -> int | None:
    """
    Optional OCR trailing-digit trim. Never mutates real high rewards in place.

    Safe cases only:
      • trailing 9 on a 5-digit token (10259 → 1025) — rare for real rewards
      • trailing 0 on a *non-round* 5-digit token (72620 → 7262), but NOT
        15000 / 20000 / 10000 (real thousand amounts farmers hit with premium)

    6+ digit values (100000+ SL) are never trimmed.
    """
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

    # Prefer reading order when both raw tokens already form a good pair.
    if len(raw) >= 2:
        a, b = raw[0], raw[1]
        for rp, sl in ((a, b), (b, a)):
            score = _pair_score(rp, sl)
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


def _read_roi_pair(crop: Image.Image) -> tuple[str, tuple[int, int] | None]:
    """Windows OCR first (accurate on blue RP); Tesseract digits as fallback."""
    text = windows_roi_text(crop)
    pair = pair_from_digit_text(text) if text else None
    if pair is not None and _column_pair_usable(*pair):
        return text, pair
    digits = tesseract_digits_text(crop)
    if digits:
        tess_pair = pair_from_digit_text(digits)
        if tess_pair is not None and _column_pair_usable(*tess_pair):
            return digits, tess_pair
    if pair is not None:
        return text, pair
    return text or digits, None


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
    """
    if prefer_with is None:
        try:
            from .settings import load_settings

            prefer_with = bool(load_settings().has_premium_account)
        except Exception:  # noqa: BLE001
            prefer_with = False

    variants: list[tuple[str, str]] = []
    with_pairs: list[tuple[int, int]] = []
    without_pairs: list[tuple[int, int]] = []
    total_pairs: list[tuple[int, int]] = []

    def _ingest_digit_rois(*, use_dense: bool) -> None:
        for tag, crop in iter_reward_digit_rois(image, dense=use_dense):
            text, pair = _read_roi_pair(crop)
            if not text:
                continue
            if pair is None:
                variants.append((f"roi:{tag}", text))
                continue
            rp, sl = pair
            if tag.startswith("with") and not tag.startswith("without"):
                with_pairs.append(pair)
                with_pairs.append(pair)  # digit ROI outweighs landmark band
                labeled = f"З преміумом {rp} {sl}"
            elif tag.startswith("without"):
                without_pairs.append(pair)
                without_pairs.append(pair)
                labeled = f"Без преміума {rp} {sl}"
            elif tag.startswith("both"):
                from .ocr_parse import (
                    _with_pair_from_premium_amounts,
                    _without_pair_from_premium_amounts,
                )

                amounts = [a for a in _amounts_in(text, min_value=50) if a <= 250_000]
                w_pair = _with_pair_from_premium_amounts(amounts)
                wo_pair = _without_pair_from_premium_amounts(amounts)
                if w_pair[0] is not None and w_pair[1] is not None:
                    with_pairs.extend([(w_pair[0], w_pair[1])] * 2)
                    variants.append((f"roi:{tag}-with", f"З преміумом {w_pair[0]} {w_pair[1]}"))
                if wo_pair[0] is not None and wo_pair[1] is not None:
                    without_pairs.extend([(wo_pair[0], wo_pair[1])] * 2)
                    variants.append(
                        (f"roi:{tag}-without", f"Без преміума {wo_pair[0]} {wo_pair[1]}")
                    )
                if prefer_with:
                    with_pairs.append(pair)
                    labeled = f"З преміумом {rp} {sl}"
                else:
                    without_pairs.append(pair)
                    labeled = f"Без преміума {rp} {sl}"
            else:
                total_pairs.append(pair)
                total_pairs.append(pair)
                labeled = f"Всього {rp} {sl}"
            variants.append((f"roi:{tag}", labeled))

    _ingest_digit_rois(use_dense=dense)

    preferred_early = _vote_pair(with_pairs if prefer_with else without_pairs)
    need_more = preferred_early is None or not _column_pair_usable(*preferred_early)
    if need_more and not dense:
        _ingest_digit_rois(use_dense=True)
        preferred_early = _vote_pair(with_pairs if prefer_with else without_pairs)
        need_more = preferred_early is None or not _column_pair_usable(*preferred_early)

    if need_more:
        try:
            landmark_variants = pairs_from_landmarks(image)
            variants.extend(landmark_variants)
            for tag, text in landmark_variants:
                pair = pair_from_digit_text(text)
                if pair is None:
                    continue
                if "without" in tag:
                    without_pairs.append(pair)
                elif "with" in tag and "without" not in tag:
                    with_pairs.append(pair)
                else:
                    total_pairs.append(pair)
        except Exception as exc:  # noqa: BLE001
            log.warning("landmark ROI failed: %s", exc)

    with_vote = _vote_pair(with_pairs)
    without_vote = _vote_pair(without_pairs)
    total = _vote_pair(total_pairs)
    preferred = with_vote if prefer_with else without_vote
    other = without_vote if prefer_with else with_vote

    if preferred and total and not prefer_with:
        pref_ok = _column_pair_usable(*preferred)
        tot_ok = _column_pair_usable(*total)
        if preferred == total and pref_ok:
            rp, sl = preferred
            variants.insert(0, ("roi:consensus", f"Без преміума {rp} {sl}\nВсього {rp} {sl}"))
        elif pref_ok and preferred[0] == total[0]:
            # Same RP, trust total SL when usable (digit ROI often cleaner).
            rp = preferred[0]
            sl = total[1] if tot_ok else preferred[1]
            variants.insert(0, ("roi:consensus-total-sl", f"Без преміума {rp} {sl}\nВсього {rp} {sl}"))
        elif pref_ok and tot_ok and preferred[1] == total[1]:
            # Same SL, different RP: header «Без преміума» is battle RP;
            # «Всього» RP is often free research — keep preferred.
            rp, sl = preferred
            variants.insert(
                0,
                ("roi:consensus-without-rp", f"Без преміума {rp} {sl}\nВсього {total[0]} {sl}"),
            )
        elif pref_ok:
            # Usable without column always beats Всього when values disagree.
            rp, sl = preferred
            variants.insert(0, ("roi:without-over-total", f"Без преміума {rp} {sl}"))
        elif tot_ok:
            # Without-column OCR junk (chat crops) — fall back to Всього.
            rp, sl = total
            variants.insert(0, ("roi:prefer-total", f"Без преміума {rp} {sl}\nВсього {rp} {sl}"))
    elif preferred:
        rp, sl = preferred
        label = "З преміумом" if prefer_with else "Без преміума"
        tag = "roi:with-only" if prefer_with else "roi:without-only"
        variants.insert(0, (tag, f"{label} {rp} {sl}"))
        if other and other != preferred:
            o_rp, o_sl = other
            o_label = "Без преміума" if prefer_with else "З преміумом"
            variants.insert(1, ("roi:other-column", f"{o_label} {o_rp} {o_sl}"))
    elif total and not prefer_with:
        rp, sl = total
        variants.insert(0, ("roi:total-only", f"Всього {rp} {sl}"))
    elif without_vote:
        rp, sl = without_vote
        variants.insert(0, ("roi:without-only", f"Без преміума {rp} {sl}"))
    elif with_vote:
        rp, sl = with_vote
        variants.insert(0, ("roi:with-only", f"З преміумом {rp} {sl}"))

    return variants


def report_from_roi_image(image: Image.Image, *, prefer_with: bool | None = None):
    """High-confidence report when ROI / landmark pairs resolve."""
    from .ocr_parse import parse_rewards_from_ocr_text

    if prefer_with is None:
        try:
            from .settings import load_settings

            prefer_with = bool(load_settings().has_premium_account)
        except Exception:  # noqa: BLE001
            prefer_with = False

    variants = extract_roi_reward_variants(image, prefer_with=prefer_with)
    for tag, text in variants:
        if not tag.startswith("roi:"):
            continue
        if tag.startswith("roi:consensus") or tag in (
            "roi:prefer-total",
            "roi:without-over-total",
            "roi:total-only",
            "roi:without-only",
            "roi:with-only",
            "roi:landmark-total",
            "roi:landmark-without",
            "roi:landmark-with",
            "roi:band-total",
            "roi:band-without",
            "roi:band-with",
        ):
            # Skip the non-preferred column landmark when we already know preference.
            if prefer_with and tag in ("roi:landmark-without", "roi:band-without", "roi:without-only"):
                # Still allow if no with variant exists later — handled by loop order
                # (with-only / band-with inserted first when prefer_with).
                if any(
                    t in ("roi:with-only", "roi:landmark-with", "roi:band-with")
                    or t.startswith("roi:consensus")
                    for t, _ in variants
                ):
                    continue
            if not prefer_with and tag in ("roi:landmark-with", "roi:band-with", "roi:with-only"):
                if any(
                    t in ("roi:without-only", "roi:landmark-without", "roi:band-without")
                    or t.startswith("roi:consensus")
                    or t == "roi:total-only"
                    for t, _ in variants
                ):
                    continue
            report = parse_rewards_from_ocr_text(text, prefer_premium_rewards=prefer_with)
            if report is None:
                continue
            if tag.startswith("roi:consensus"):
                report.confidence = max(report.confidence, 0.94)
            elif tag.startswith("roi:landmark") or tag.startswith("roi:band"):
                report.confidence = max(report.confidence, 0.90)
            elif tag in ("roi:with-only", "roi:without-only", "roi:without-over-total"):
                report.confidence = max(report.confidence, 0.92)
            elif tag == "roi:prefer-total":
                report.confidence = max(report.confidence, 0.88)
            else:
                report.confidence = max(report.confidence, 0.82)
            report.source = "ocr-roi"
            return report
    return None
