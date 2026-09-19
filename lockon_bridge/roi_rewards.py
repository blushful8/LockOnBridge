"""Digit / landmark ROI extraction for without-premium + totals cells."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from io import BytesIO

from PIL import Image, ImageEnhance, ImageOps

from .ocr_parse import _amounts_in, _plausible_reward_pair
from .roi_layout import iter_reward_digit_rois

log = logging.getLogger("lockon_bridge.roi")

_TOTAL_LABEL = re.compile(
    r"всього|всего|vsego|bcboro|bcsoro|total|итого|gesamt",
    re.IGNORECASE,
)
_WITHOUT_LABEL = re.compile(
    r"без\s*прем|без\s*prem|without|ohne\s*premium|5[bв]\s*npe|bez\s*prem",
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
    best, n = counts.most_common(1)[0]
    if n >= 2:
        return best
    if len(pairs) == 1:
        return pairs[0]
    for candidate, _ in counts.most_common():
        rp, sl = candidate
        allies = sum(
            1
            for other_rp, other_sl in pairs
            if abs(other_rp - rp) <= max(15, int(rp * 0.02))
            and abs(other_sl - sl) <= max(30, int(sl * 0.02))
        )
        if allies >= 2:
            return candidate
    return counts.most_common(1)[0][0]


def _read_roi_pair(crop: Image.Image) -> tuple[str, tuple[int, int] | None]:
    """Prefer Windows OCR on ROI; fall back to digit Tesseract."""
    text = windows_roi_text(crop)
    pair = pair_from_digit_text(text) if text else None
    if pair is not None:
        return text, pair
    digits = tesseract_digits_text(crop)
    if digits:
        pair = pair_from_digit_text(digits)
        return digits, pair
    return text, None


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


def _amount_from_word(text: str) -> int | None:
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    try:
        value = int(digits)
    except ValueError:
        return None
    if value < 50 or value > 250_000:
        return None
    # Do not auto-trim here — pair_from_digit_text expands OCR ghosts safely.
    return value


def pairs_from_landmarks(image: Image.Image) -> list[tuple[str, str]]:
    """
    Locate «Всього» / without-premium via word boxes, then take nearby amounts.

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

    amount_words: list[tuple[int, float, float]] = []
    labels_total: list[tuple[float, float]] = []
    labels_without: list[tuple[float, float]] = []
    for text, x, y, w, _h in words:
        nx, ny = norm(x + w * 0.5, y)
        if _TOTAL_LABEL.search(text):
            labels_total.append((nx, ny))
        if _WITHOUT_LABEL.search(text):
            labels_without.append((nx, ny))
        value = _amount_from_word(text)
        if value is not None:
            amount_words.append((value, nx, ny))

    variants: list[tuple[str, str]] = []

    def pair_near(label_xy: tuple[float, float], *, right_only: bool, band: float) -> tuple[int, int] | None:
        lx, ly = label_xy
        nearby = [
            (value, nx, ny)
            for value, nx, ny in amount_words
            if abs(ny - ly) <= band and (nx >= lx - 0.02 if right_only else True)
        ]
        nearby.sort(key=lambda item: (abs(item[2] - ly), item[1]))
        values = [item[0] for item in nearby[:6]]
        return pair_from_digit_text(" ".join(str(v) for v in values))

    for lx, ly in labels_total:
        pair = pair_near((lx, ly), right_only=True, band=0.035)
        if pair:
            rp, sl = pair
            variants.append(("roi:landmark-total", f"Всього {rp} {sl}"))

    for lx, ly in labels_without:
        # Without column digits sit under/near the header — slightly below.
        pair = pair_near((lx, ly + 0.02), right_only=False, band=0.06)
        if pair is None:
            pair = pair_near((lx, ly), right_only=True, band=0.08)
        if pair:
            rp, sl = pair
            variants.append(("roi:landmark-without", f"Без преміума {rp} {sl}"))

    # Fallback: amounts in the calibrated without / total bands even without labels.
    without_band = [(v, x, y) for v, x, y in amount_words if 0.13 <= y <= 0.23 and 0.40 <= x <= 0.55]
    total_band = [(v, x, y) for v, x, y in amount_words if 0.43 <= y <= 0.49 and 0.48 <= x <= 0.68]
    if without_band:
        without_band.sort(key=lambda item: item[2])
        pair = pair_from_digit_text(" ".join(str(v) for v, _, _ in without_band[:4]))
        if pair:
            rp, sl = pair
            variants.append(("roi:band-without", f"Без преміума {rp} {sl}"))
    if total_band:
        total_band.sort(key=lambda item: item[1])
        pair = pair_from_digit_text(" ".join(str(v) for v, _, _ in total_band[:4]))
        if pair:
            rp, sl = pair
            variants.append(("roi:band-total", f"Всього {rp} {sl}"))

    return variants


def extract_roi_reward_variants(image: Image.Image) -> list[tuple[str, str]]:
    """
    Synthetic OCR texts for choose_best / parse_rewards_from_ocr_text.

    Combines landmark boxes + relative digit ROIs, then emits a consensus when
    without-premium and Всього agree.
    """
    variants: list[tuple[str, str]] = []
    without_pairs: list[tuple[int, int]] = []
    total_pairs: list[tuple[int, int]] = []

    try:
        landmark_variants = pairs_from_landmarks(image)
        variants.extend(landmark_variants)
        for tag, text in landmark_variants:
            pair = pair_from_digit_text(text)
            if pair is None:
                continue
            if "without" in tag:
                without_pairs.append(pair)
            else:
                total_pairs.append(pair)
    except Exception as exc:  # noqa: BLE001
        log.warning("landmark ROI failed: %s", exc)

    for tag, crop in iter_reward_digit_rois(image):
        text, pair = _read_roi_pair(crop)
        if not text:
            continue
        if pair is None:
            variants.append((f"roi:{tag}", text))
            continue
        rp, sl = pair
        if tag.startswith("without"):
            without_pairs.append(pair)
            labeled = f"Без преміума {rp} {sl}"
        else:
            total_pairs.append(pair)
            labeled = f"Всього {rp} {sl}"
        variants.append((f"roi:{tag}", labeled))

    without = _vote_pair(without_pairs)
    total = _vote_pair(total_pairs)

    if without and total:
        if without == total:
            rp, sl = without
            variants.insert(0, ("roi:consensus", f"Без преміума {rp} {sl}\nВсього {rp} {sl}"))
        elif without[0] == total[0]:
            rp, sl = total
            variants.insert(0, ("roi:consensus-total-sl", f"Без преміума {rp} {sl}\nВсього {rp} {sl}"))
        else:
            rp, sl = total
            variants.insert(0, ("roi:prefer-total", f"Без преміума {rp} {sl}\nВсього {rp} {sl}"))
    elif total:
        rp, sl = total
        variants.insert(0, ("roi:total-only", f"Всього {rp} {sl}"))
    elif without:
        rp, sl = without
        variants.insert(0, ("roi:without-only", f"Без преміума {rp} {sl}"))

    return variants


def report_from_roi_image(image: Image.Image):
    """High-confidence report when ROI / landmark pairs resolve."""
    from .ocr_parse import parse_rewards_from_ocr_text

    variants = extract_roi_reward_variants(image)
    for tag, text in variants:
        if not tag.startswith("roi:"):
            continue
        if tag.startswith("roi:consensus") or tag in (
            "roi:prefer-total",
            "roi:total-only",
            "roi:without-only",
            "roi:landmark-total",
            "roi:landmark-without",
            "roi:band-total",
            "roi:band-without",
        ):
            report = parse_rewards_from_ocr_text(text)
            if report is None:
                continue
            if tag.startswith("roi:consensus"):
                report.confidence = max(report.confidence, 0.94)
            elif tag.startswith("roi:landmark") or tag.startswith("roi:band"):
                report.confidence = max(report.confidence, 0.90)
            elif tag == "roi:prefer-total":
                report.confidence = max(report.confidence, 0.88)
            else:
                report.confidence = max(report.confidence, 0.82)
            report.source = "ocr-roi"
            return report
    return None
