"""
Layout-first OCR for post-battle results (OCR.space backend).

Pipeline:
  1. Detect text/digit bands + SL/RP icon anchors (numpy, no Tesseract).
  2. Crop bands relative to icons / ink (resolution- and language-agnostic).
  3. OCR.space Engine 2 (auto language) with TextOverlay → structured lines.
  4. Prefer «Зароблено / Earned» overlay lines; fall back to full crop text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from PIL import Image

from .roi_layout import NormRect

log = logging.getLogger("lockon_bridge.layout_ocr")

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]


@dataclass(frozen=True)
class Box:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def cx(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2.0

    def pad(self, px: int, *, bounds: tuple[int, int]) -> "Box":
        w, h = bounds
        return Box(
            max(0, self.left - px),
            max(0, self.top - px),
            min(w, self.right + px),
            min(h, self.bottom + px),
        )


@dataclass(frozen=True)
class IconAnchor:
    kind: str  # "sl" | "rp"
    box: Box
    score: float


@dataclass(frozen=True)
class OverlayLine:
    text: str
    box: Box


_EARNED_HINT = re.compile(
    r"(?i)(?:зароблено|заработано|earned|3apo[o0]?n?e?h?[o0]|zaro[bh]leno|"
    r"total|всього|всего|bcboro|"
    r"без\s*премі|without\s*premium|без\s*премиум)",
)


def _png_bytes(image: Image.Image, *, max_width: int = 1600) -> bytes:
    rgb = image.convert("RGB")
    if rgb.width > max_width:
        scale = max_width / float(rgb.width)
        rgb = rgb.resize(
            (max_width, max(1, int(rgb.height * scale))),
            Image.Resampling.LANCZOS,
        )
    buf = BytesIO()
    rgb.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _results_panel_box(frame: Image.Image) -> Box:
    """Rough left results card — only a search window, not digit coords."""
    w, h = frame.size
    return Box(
        int(w * 0.02),
        int(h * 0.06),
        int(w * 0.72),
        int(h * 0.88),
    )


def _bright_ink_mask(arr: "np.ndarray") -> "np.ndarray":
    """White / yellow / cyan UI ink on dark WT panels."""
    r = arr[:, :, 0].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    b = arr[:, :, 2].astype(np.int16)
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    sat = (mx - mn) / np.maximum(mx, 1)
    white = (mx >= 145) & (sat <= 0.42)
    yellow = (r >= 150) & (g >= 110) & (b <= 160) & (mx >= 130)
    cyan = (b >= 125) & (g >= 95) & (b > r + 10) & (mx >= 120)
    return white | yellow | cyan


def detect_text_line_boxes(
    frame: Image.Image,
    *,
    search: Box | None = None,
) -> list[Box]:
    """
    Layout step: connected bright-ink bands → horizontal line boxes.

    No language assumptions — only geometry.
    """
    if np is None:
        return []
    w, h = frame.size
    region = search or _results_panel_box(frame)
    crop = frame.crop((region.left, region.top, region.right, region.bottom))
    arr = np.asarray(crop.convert("RGB"), dtype=np.uint8)
    mask = _bright_ink_mask(arr)
    if not bool(mask.any()):
        return []

    # Downsample for cheap CC / row projection.
    step = 2
    small = mask[::step, ::step]
    # Horizontal projection → line bands
    row_density = small.mean(axis=1)
    thr = max(0.02, float(np.percentile(row_density, 70)) * 0.45)
    active = row_density >= thr
    bands: list[tuple[int, int]] = []
    y = 0
    sh = active.shape[0]
    while y < sh:
        if not active[y]:
            y += 1
            continue
        y0 = y
        while y < sh and active[y]:
            y += 1
        if y - y0 >= 2:
            bands.append((y0, y))

    boxes: list[Box] = []
    for y0, y1 in bands:
        strip = small[y0:y1, :]
        cols = strip.any(axis=0)
        xs = np.where(cols)[0]
        if len(xs) < 8:
            continue
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        # Map back to frame coords
        box = Box(
            region.left + x0 * step,
            region.top + y0 * step,
            region.left + x1 * step,
            region.top + y1 * step,
        )
        if box.width < 40 or box.height < 8:
            continue
        if box.height > int(h * 0.12):
            continue
        boxes.append(box.pad(4, bounds=(w, h)))

    # Merge vertically overlapping neighbours into single lines.
    boxes.sort(key=lambda b: b.top)
    merged: list[Box] = []
    for box in boxes:
        if not merged:
            merged.append(box)
            continue
        prev = merged[-1]
        if box.top <= prev.bottom + 6 and abs(box.cy - prev.cy) < max(18, prev.height):
            merged[-1] = Box(
                min(prev.left, box.left),
                min(prev.top, box.top),
                max(prev.right, box.right),
                max(prev.bottom, box.bottom),
            )
        else:
            merged.append(box)
    return merged


def _color_icon_candidates(
    crop: Image.Image,
    *,
    origin: tuple[int, int],
) -> list[IconAnchor]:
    """Compact cyan bulbs + silver lions inside a search crop."""
    if np is None:
        return []
    arr = np.asarray(crop.convert("RGB"), dtype=np.int16)
    h, w = arr.shape[:2]
    if w < 40 or h < 40:
        return []
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    sat = (mx - mn) / np.maximum(mx, 1)

    cyan = (b > 145) & (g > 100) & (b > r + 30) & (g > r + 5) & (mx >= 135)
    silver = (mx >= 165) & (sat <= 0.24) & ((mx - mn) <= 48)
    # Icons sit to the right of their amounts.
    silver[:, : int(w * 0.20)] = False

    def _components(mask: "np.ndarray", kind: str) -> list[IconAnchor]:
        step = 2
        small = mask[::step, ::step]
        sh, sw = small.shape
        visited = np.zeros_like(small, dtype=bool)
        out: list[IconAnchor] = []
        for y in range(sh):
            for x in range(sw):
                if not small[y, x] or visited[y, x]:
                    continue
                stack = [(y, x)]
                visited[y, x] = True
                pts: list[tuple[int, int]] = []
                while stack:
                    cy, cx = stack.pop()
                    pts.append((cy, cx))
                    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                        ny, nx = cy + dy, cx + dx
                        if (
                            0 <= ny < sh
                            and 0 <= nx < sw
                            and small[ny, nx]
                            and not visited[ny, nx]
                        ):
                            visited[ny, nx] = True
                            stack.append((ny, nx))
                if len(pts) < 10:
                    continue
                ys = [p[0] for p in pts]
                xs = [p[1] for p in pts]
                box = Box(
                    origin[0] + min(xs) * step,
                    origin[1] + min(ys) * step,
                    origin[0] + (max(xs) + 1) * step,
                    origin[1] + (max(ys) + 1) * step,
                )
                if box.width > 56 or box.height > 52:
                    continue
                if box.width < 8 or box.height < 8:
                    continue
                out.append(IconAnchor(kind=kind, box=box, score=float(len(pts))))
        return out

    return _components(cyan, "rp") + _components(silver, "sl")


def _template_icon_candidates(
    crop: Image.Image,
    *,
    origin: tuple[int, int],
) -> list[IconAnchor]:
    """NCC template hits for shipped SL lion / RP bulb crops."""
    if np is None:
        return []
    try:
        from .ocr_preprocess import _load_reward_icon_templates, _ncc_score
    except Exception:  # noqa: BLE001
        return []

    hay = np.asarray(crop.convert("RGB"), dtype=np.uint8)
    ph, pw = hay.shape[:2]
    hits: list[IconAnchor] = []
    for name, tmpl in _load_reward_icon_templates():
        kind = "rp" if ("bulb" in name or name.startswith("rp")) else "sl"
        if "lion" in name or name.startswith("sl"):
            kind = "sl"
        tw0, th0 = tmpl.size
        use_blue = kind == "rp"
        for scale in (0.8, 1.0, 1.25, 1.55, 1.9):
            tw = max(10, int(tw0 * scale))
            th = max(10, int(th0 * scale))
            if tw >= pw or th >= ph:
                continue
            tarr = np.asarray(
                tmpl.resize((tw, th), Image.Resampling.LANCZOS),
                dtype=np.uint8,
            )
            if use_blue:
                needle = tarr[:, :, 2].astype(np.float32) - tarr[:, :, 0].astype(
                    np.float32
                )
                field = hay[:, :, 2].astype(np.float32) - hay[:, :, 0].astype(
                    np.float32
                )
            else:
                needle = tarr.mean(axis=2)
                field = hay.mean(axis=2)
            step = max(2, th // 4)
            thr = 0.62 if kind == "rp" else 0.68
            for y in range(0, ph - th, step):
                for x in range(int(pw * 0.15), pw - tw, step):
                    score = float(_ncc_score(field[y : y + th, x : x + tw], needle))
                    if score < thr:
                        continue
                    hits.append(
                        IconAnchor(
                            kind=kind,
                            box=Box(
                                origin[0] + x,
                                origin[1] + y,
                                origin[0] + x + tw,
                                origin[1] + y + th,
                            ),
                            score=score,
                        )
                    )
    # NMS
    hits.sort(key=lambda h: h.score, reverse=True)
    kept: list[IconAnchor] = []
    for hit in hits:
        if any(
            abs(hit.box.cx - k.box.cx) < 22 and abs(hit.box.cy - k.box.cy) < 18
            for k in kept
        ):
            continue
        kept.append(hit)
        if len(kept) >= 24:
            break
    return kept


def find_icon_anchors(frame: Image.Image) -> list[IconAnchor]:
    """SL / RP icon anchors inside the results panel (fast colour-first)."""
    panel = _results_panel_box(frame)
    # Mid band where Зароблено / row amounts usually sit — shrink search.
    w, h = frame.size
    mid = Box(
        panel.left,
        max(panel.top, int(h * 0.28)),
        panel.right,
        min(panel.bottom, int(h * 0.72)),
    )
    crop = frame.crop((mid.left, mid.top, mid.right, mid.bottom))
    origin = (mid.left, mid.top)
    anchors = _color_icon_candidates(crop, origin=origin)
    # Template only when colour found almost nothing (keeps settle latency low).
    if sum(1 for a in anchors if a.kind == "sl") < 1 or sum(
        1 for a in anchors if a.kind == "rp"
    ) < 1:
        anchors.extend(_template_icon_candidates(crop, origin=origin))
    anchors.sort(key=lambda a: a.score, reverse=True)
    kept: list[IconAnchor] = []
    for hit in anchors:
        if any(
            abs(hit.box.cx - k.box.cx) < 20 and abs(hit.box.cy - k.box.cy) < 16
            for k in kept
        ):
            continue
        kept.append(hit)
    return kept


def _icon_pair_row_crops(
    frame: Image.Image,
    anchors: list[IconAnchor],
) -> list[tuple[str, Image.Image, Box]]:
    """
    Amount strips left of SL→RP icon pairs on the same row.

    Relative to icons, not screen edges — survives resolution / UI shift.
    """
    w, h = frame.size
    lions = [a for a in anchors if a.kind == "sl"]
    bulbs = [a for a in anchors if a.kind == "rp"]
    pairs: list[tuple[IconAnchor, IconAnchor, float]] = []
    for lion in lions:
        for bulb in bulbs:
            if bulb.box.left <= lion.box.left:
                continue
            dy = abs(lion.box.cy - bulb.box.cy)
            if dy > 28:
                continue
            gap = bulb.box.left - lion.box.right
            if gap < -10 or gap > int(w * 0.25):
                continue
            pairs.append((lion, bulb, dy + gap * 0.01))
    pairs.sort(key=lambda p: p[0].box.cy)

    out: list[tuple[str, Image.Image, Box]] = []
    seen_y: list[float] = []
    for index, (lion, bulb, _score) in enumerate(pairs):
        cy = (lion.box.cy + bulb.box.cy) / 2.0
        if any(abs(cy - sy) < 20 for sy in seen_y):
            continue
        seen_y.append(cy)
        # Amounts sit to the left of the lion; keep a generous left run.
        icon_h = max(lion.box.height, bulb.box.height, 18)
        left = max(0, int(lion.box.left - icon_h * 14))
        # Prefer staying inside results panel.
        left = max(left, int(w * 0.03))
        right = min(w, bulb.box.right + icon_h)
        top = max(0, int(cy - icon_h * 1.6))
        bottom = min(h, int(cy + icon_h * 1.6))
        box = Box(left, top, right, bottom)
        if box.width < 80 or box.height < 16:
            continue
        tag = "anchor-earned" if index == 0 and cy > h * 0.35 else f"anchor-row{index}"
        # Mid/lower pairs are more often the Зароблено summary.
        if cy >= h * 0.38 and cy <= h * 0.62:
            tag = "anchor-earned"
        out.append((tag, frame.crop((box.left, box.top, box.right, box.bottom)), box))
    # Prefer earned-tagged first
    out.sort(key=lambda t: 0 if t[0] == "anchor-earned" else 1)
    return out[:6]


def _line_band_crops(
    frame: Image.Image,
    lines: list[Box],
) -> list[tuple[str, Image.Image, Box]]:
    w, h = frame.size
    out: list[tuple[str, Image.Image, Box]] = []
    for index, box in enumerate(lines):
        # Widen horizontally across the panel; keep line height.
        wide = Box(
            max(0, int(w * 0.03)),
            max(0, box.top - 4),
            min(w, max(box.right + 8, int(w * 0.55))),
            min(h, box.bottom + 4),
        )
        if wide.width < 60 or wide.height < 10:
            continue
        # Skip chrome-y top tabs
        if wide.bottom < h * 0.12:
            continue
        out.append(
            (
                f"ink-line{index}",
                frame.crop((wide.left, wide.top, wide.right, wide.bottom)),
                wide,
            )
        )
    return out[:10]


def layout_crops_for_ocr(frame: Image.Image) -> list[tuple[str, Image.Image]]:
    """
    Ordered crops for OCR.space.

    Priority: full results panel (column-split safe) → earned window →
    mid ink lines → icon-anchored rows → totals.
    """
    w, h = frame.size
    crops: list[tuple[str, Image.Image]] = []
    seen: set[tuple[int, int, int, int]] = set()

    def _add(tag: str, image: Image.Image, box: Box | None = None) -> None:
        key = (
            box.left if box else 0,
            box.top if box else 0,
            box.right if box else image.width,
            box.bottom if box else image.height,
        )
        q = (key[0] // 12, key[1] // 12, key[2] // 12, key[3] // 12)
        if q in seen:
            return
        seen.add(q)
        crops.append((tag, image))

    # 1) Full left results panel — OCR.space Engine2 often column-splits cells;
    #    the full panel still carries «Без преміума» / «Всього» + amount lines.
    panel = _results_panel_box(frame)
    # Already-cropped phone / fixture shots: use the whole frame.
    if w <= 1400 and h <= 900:
        _add("panel-full", frame, Box(0, 0, w, h))
    else:
        _add(
            "panel-full",
            frame.crop((panel.left, panel.top, panel.right, panel.bottom)),
            panel,
        )

    # 2) Earned / mid summary window (Messages-style «Зароблено»).
    earned = Box(int(w * 0.04), int(h * 0.40), int(w * 0.58), int(h * 0.64))
    _add(
        "fallback-earned",
        frame.crop((earned.left, earned.top, earned.right, earned.bottom)),
        earned,
    )

    # 3) Wide ink lines in the mid band.
    ink_lines = detect_text_line_boxes(frame)
    mid = [
        b
        for b in ink_lines
        if h * 0.38 <= b.cy <= h * 0.62 and b.width >= int(w * 0.18)
    ]
    log.info("layout ink lines=%s mid=%s", len(ink_lines), len(mid))
    for tag, image, box in _line_band_crops(frame, mid[:4]):
        _add(tag, image, box)

    # 4) Icon-anchored amount strips (relative to SL→RP pairs).
    anchors = find_icon_anchors(frame)
    log.info(
        "layout icons sl=%s rp=%s",
        sum(1 for a in anchors if a.kind == "sl"),
        sum(1 for a in anchors if a.kind == "rp"),
    )
    for tag, image, box in _icon_pair_row_crops(frame, anchors):
        if not (h * 0.40 <= box.cy <= h * 0.62):
            continue
        if box.width < int(w * 0.12):
            continue
        _add(tag, image, box)

    # 5) Wider totals fallback.
    totals = Box(int(w * 0.06), int(h * 0.26), int(w * 0.70), int(h * 0.70))
    _add(
        "fallback-totals",
        frame.crop((totals.left, totals.top, totals.right, totals.bottom)),
        totals,
    )
    return crops


def _overlay_lines_from_payload(data: dict[str, Any]) -> list[OverlayLine]:
    results = data.get("ParsedResults") or []
    if not results or not isinstance(results[0], dict):
        return []
    overlay = results[0].get("TextOverlay") or {}
    lines_raw = overlay.get("Lines") or []
    out: list[OverlayLine] = []
    for line in lines_raw:
        if not isinstance(line, dict):
            continue
        words = line.get("Words") or []
        if not words:
            continue
        text = " ".join(str(w.get("WordText") or "") for w in words).strip()
        if not text:
            continue
        try:
            left = min(int(w["Left"]) for w in words)
            top = min(int(w["Top"]) for w in words)
            right = max(int(w["Left"]) + int(w["Width"]) for w in words)
            bottom = max(int(w["Top"]) + int(w["Height"]) for w in words)
        except (KeyError, TypeError, ValueError):
            continue
        out.append(OverlayLine(text=text, box=Box(left, top, right, bottom)))
    return out


def select_structured_text(
    full_text: str,
    overlay_lines: list[Any],
) -> str:
    """
    Prefer overlay lines that look like Earned/Total/without-premium banks.

    When OCR.space column-splits, keep the header line **plus** the next few
    amount-only neighbours so parse can see ``Без преміума\\n555\\n2040``.
    """
    def _line_text(item: Any) -> str:
        if isinstance(item, OverlayLine):
            return item.text
        if isinstance(item, dict):
            return str(item.get("text") or "")
        return str(getattr(item, "text", "") or "")

    def _is_amountish(text: str) -> bool:
        return bool(re.search(r"\d", text)) and len(text) <= 24

    if overlay_lines:
        ordered = [t for t in (_line_text(x).strip() for x in overlay_lines) if t]
        # Expand header hits with following amount lines (column-split).
        bundles: list[str] = []
        for index, text in enumerate(ordered):
            if not _EARNED_HINT.search(text):
                continue
            chunk = [text]
            for nxt in ordered[index + 1 : index + 6]:
                if _EARNED_HINT.search(nxt) and not _is_amountish(nxt):
                    break
                if _is_amountish(nxt) or len(re.findall(r"\d", nxt)) >= 1:
                    chunk.append(nxt)
                elif len(chunk) > 1:
                    break
            bundles.append("\n".join(chunk))
        if bundles:
            return "\n\n".join(bundles[:3])
        amountish = [
            t
            for t in ordered
            if len(re.findall(r"\d[\d\s.,']{1,}\d", t)) >= 2
        ]
        if amountish:
            return "\n".join(amountish[:4])
        return "\n".join(ordered)
    return full_text


def crop_parse_zone(frame: Image.Image, zone: NormRect | None = None) -> Image.Image:
    """
    One rectangle as fractions of the full frame.

    ``zone`` defaults to the saved developer zone. The calibrator passes the
    rectangle being dragged so a test parse matches what is on screen, even
    before Save.
    """
    if zone is None:
        from .roi_calib import load_parse_zone

        zone = load_parse_zone()
    zone = zone.clamp()
    width, height = frame.size
    left = max(0, min(width - 2, int(width * zone.left)))
    top = max(0, min(height - 2, int(height * zone.top)))
    right = max(left + 2, min(width, int(width * zone.right)))
    bottom = max(top + 2, min(height, int(height * zone.bottom)))
    log.info(
        "parse zone crop (%s,%s)-(%s,%s) of %sx%s",
        left,
        top,
        right,
        bottom,
        width,
        height,
    )
    return frame.crop((left, top, right, bottom))


def ocrspace_layout_variants(
    frame: Image.Image,
    *,
    api_key: str | None = None,
    max_crops: int = 1,
    engine: str | None = None,
) -> list[tuple[str, str]]:
    """
    One read of the shipped parse zone.

    ``engine`` is ocrspace or easyocr. OCR.space stays the default.
    EasyOCR takes the call when the cloud engine is selected but the monthly
    limit is spent or the request fails.
    """
    del max_crops
    from .local_ocr import (
        LOCAL_ENGINES,
        begin_install,
        cloud_error_is_fallback,
        cloud_limited,
        read_text,
    )
    from .ocr_parse import parse_rewards_from_ocr_text
    from .ocr_space import ocr_space_parse
    from .settings import load_settings

    chosen = (engine or load_settings().ocr_backend or "ocrspace").strip().lower()
    if chosen in ("ocr.space", "cloud", "auto", "", "paddle"):
        chosen = "ocrspace" if chosen != "paddle" else "easyocr"

    def _local(name: str, *, wait: bool) -> list[tuple[str, str]]:
        crop = crop_parse_zone(frame)
        text = read_text(crop, engine=name, wait=wait)
        if not text.strip():
            return []
        log.info("layout OCR %s chars=%s head=%r", name, len(text), text[:80].replace("\n", " | "))
        return [(f"{name}:zone", text)]

    def _local_fallback(*, wait: bool) -> list[tuple[str, str]]:
        order = ["easyocr"]
        for name in order:
            try:
                variants = _local(name, wait=wait)
            except RuntimeError as exc:
                log.warning("local OCR %s skipped: %s", name, exc)
                if "ще встановлюється" in str(exc):
                    return []
                continue
            if variants:
                return variants
        begin_install("easyocr")
        return []

    if chosen in LOCAL_ENGINES:
        return _local_fallback(wait=False)

    if cloud_limited():
        log.info("OCR.space monthly or daily limit is spent — local OCR")
        return _local_fallback(wait=False)

    variants: list[tuple[str, str]] = []
    crops = [("zone", crop_parse_zone(frame))]
    for tag, crop in crops:
        png = _png_bytes(crop)
        try:
            parsed = ocr_space_parse(
                png, api_key=api_key, language="auto", overlay=True
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("OCR.space layout crop %s failed: %s", tag, exc)
            if cloud_error_is_fallback(str(exc)):
                return _local_fallback(wait=False)
            continue
        full = parsed.get("text") or ""
        lines = parsed.get("lines") or []
        text = select_structured_text(full, lines)
        if not text.strip():
            continue
        variants.append((f"ocrspace:layout/{tag}", text))
        log.info(
            "layout OCR %s chars=%s lines=%s head=%r",
            tag,
            len(text),
            len(lines),
            text[:80].replace("\n", " | "),
        )
        report = parse_rewards_from_ocr_text(text)
        if report is not None and (
            "earned" in tag
            or "panel" in tag
            or _EARNED_HINT.search(text)
            or float(report.confidence) >= 0.7
        ):
            return variants
    return variants
