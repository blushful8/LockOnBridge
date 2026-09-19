from __future__ import annotations

import asyncio
import ctypes
import logging
from ctypes import wintypes
from io import BytesIO

import mss
from PIL import Image, ImageEnhance, ImageOps
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapDecoder
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

from .process_watch import war_thunder_pids

log = logging.getLogger("lockon_bridge.capture")

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

# Prefer packs that match common WT UI languages; still try every installed pack.
_PREFERRED_TAGS = (
    "uk-UA",
    "uk",
    "ru-RU",
    "ru",
    "en-US",
    "en-GB",
    "en",
    "de-DE",
    "fr-FR",
    "es-ES",
    "pl-PL",
    "pt-BR",
    "it-IT",
    "cs-CZ",
    "tr-TR",
    "ja",
    "ko",
    "zh-Hans",
    "zh-Hant",
)

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_DWMWA_EXTENDED_FRAME_BOUNDS = 9


def list_installed_ocr_languages() -> list[tuple[str, str]]:
    """Return [(tag, display_name), ...] for OCR packs installed on this PC."""
    result: list[tuple[str, str]] = []
    try:
        for language in OcrEngine.available_recognizer_languages:
            tag = str(language.language_tag)
            try:
                name = str(language.display_name)
            except Exception:  # noqa: BLE001
                name = tag
            result.append((tag, name))
    except Exception:  # noqa: BLE001
        pass
    return result


def _ocr_engines() -> list:
    engines: list = []
    seen: set[str] = set()

    def add(engine) -> None:
        if engine is None:
            return
        try:
            tag = str(engine.recognizer_language.language_tag).lower()
        except Exception:  # noqa: BLE001
            tag = str(id(engine))
        if tag in seen:
            return
        seen.add(tag)
        engines.append(engine)

    # Preferred first (when installed), then every other installed pack.
    for tag in _PREFERRED_TAGS:
        try:
            language = Language(tag)
            if OcrEngine.is_language_supported(language):
                add(OcrEngine.try_create_from_language(language))
        except Exception:  # noqa: BLE001
            continue

    add(OcrEngine.try_create_from_user_profile_languages())

    for tag, _name in list_installed_ocr_languages():
        try:
            language = Language(tag)
            if OcrEngine.is_language_supported(language):
                add(OcrEngine.try_create_from_language(language))
        except Exception:  # noqa: BLE001
            continue

    return engines


async def _recognize_png_with_engine(data: bytes, engine) -> str:
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(data)
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    result = await engine.recognize_async(bitmap)
    return result.text or ""


async def _recognize_png_variants(data: bytes) -> list[tuple[str, str]]:
    """
    Run each installed OCR engine separately.
    Returns [(engine_tag, text), ...] — never concatenates languages into one blob.
    """
    engines = _ocr_engines()
    if not engines:
        raise RuntimeError(
            "Windows OCR engine unavailable. Install an OCR language pack "
            "(Settings → Time & language → Language & region)."
        )
    variants: list[tuple[str, str]] = []
    for engine in engines:
        try:
            tag = str(engine.recognizer_language.language_tag)
        except Exception:  # noqa: BLE001
            tag = "unknown"
        try:
            text = await _recognize_png_with_engine(data, engine)
        except Exception:  # noqa: BLE001
            continue
        stripped = (text or "").strip()
        if stripped:
            variants.append((tag, stripped))
    return variants


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Return (left, top, right, bottom) in screen pixels, preferring DWM frame bounds."""
    rect = wintypes.RECT()
    try:
        ok = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            _DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if ok == 0 and rect.right > rect.left and rect.bottom > rect.top:
            return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception:  # noqa: BLE001
        pass
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def find_war_thunder_hwnd() -> int | None:
    """Largest visible top-level window owned by aces.exe (War Thunder client)."""
    pids = set(war_thunder_pids())
    if not pids:
        return None

    best: tuple[int, int] | None = None  # (area, hwnd)

    @_WNDENUMPROC
    def _enum(hwnd: int, _lparam: int) -> bool:
        nonlocal best
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) not in pids:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = (buf.value or "").strip().lower()
        if not title:
            return True
        # Prefer the real game window; skip launcher/helper titles when possible.
        if "launcher" in title and "war thunder" not in title and "warthunder" not in title:
            return True
        bounds = _window_rect(int(hwnd))
        if bounds is None:
            return True
        left, top, right, bottom = bounds
        area = max(0, right - left) * max(0, bottom - top)
        if area < 400 * 300:
            return True
        if best is None or area > best[0]:
            best = (area, int(hwnd))
        return True

    user32.EnumWindows(_enum, 0)
    return None if best is None else best[1]


def _png_from_image(image: Image.Image, max_width: int = 1920) -> bytes:
    # Prefer a readable width for small Cyrillic reward digits — upscale narrow crops.
    if image.width < 1400:
        ratio = 1400 / float(image.width)
        image = image.resize(
            (1400, max(1, int(image.height * ratio))),
            Image.Resampling.LANCZOS,
        )
    elif image.width > max_width:
        ratio = max_width / float(image.width)
        image = image.resize(
            (max_width, max(1, int(image.height * ratio))),
            Image.Resampling.LANCZOS,
        )

    image = ImageOps.autocontrast(image, cutoff=1)
    image = ImageEnhance.Contrast(image).enhance(1.35)
    image = ImageEnhance.Sharpness(image).enhance(1.2)

    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _crop_results_rois(image: Image.Image) -> list[tuple[str, Image.Image]]:
    """
    Focus OCR on the post-battle rewards panel, not the whole HUD.

    WT results: left/center table with Без преміума / Всього; bottom bar has FPS/CPU
    noise that confuses Tesseract when the full window is scanned.
    """
    width, height = image.size
    if width < 100 or height < 100:
        return [("full", image)]

    rois: list[tuple[str, Image.Image]] = []
    # Main results card — drop top chrome and bottom status strip.
    primary = image.crop(
        (
            int(width * 0.03),
            int(height * 0.07),
            int(width * 0.78),
            int(height * 0.86),
        )
    )
    rois.append(("panel", primary))

    # Tighter band around without-premium / total RP·SL columns.
    band = image.crop(
        (
            int(width * 0.06),
            int(height * 0.26),
            int(width * 0.70),
            int(height * 0.70),
        )
    )
    if band.width >= 200 and band.height >= 120:
        rois.append(("totals", band))
    return rois


def grab_region_png(
    left: int,
    top: int,
    right: int,
    bottom: int,
    *,
    max_width: int = 1920,
) -> bytes:
    width = max(1, right - left)
    height = max(1, bottom - top)
    with mss.MSS() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    # Prefer the rewards panel crop for a single-PNG dump / primary OCR frame.
    rois = _crop_results_rois(image)
    return _png_from_image(rois[0][1], max_width=max_width)


def grab_region_png_variants(
    left: int,
    top: int,
    right: int,
    bottom: int,
    *,
    max_width: int = 1920,
) -> list[tuple[str, bytes]]:
    """Return [(roi_tag, png_bytes), ...] for multi-crop OCR."""
    width = max(1, right - left)
    height = max(1, bottom - top)
    with mss.MSS() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    out: list[tuple[str, bytes]] = []
    for tag, crop in _crop_results_rois(image):
        out.append((tag, _png_from_image(crop, max_width=max_width)))
    return out


def grab_war_thunder_png(max_width: int = 1920) -> bytes | None:
    """Capture the War Thunder client window when it is running and visible."""
    hwnd = find_war_thunder_hwnd()
    if hwnd is None:
        return None
    bounds = _window_rect(hwnd)
    if bounds is None:
        return None
    left, top, right, bottom = bounds
    try:
        # Bring WT forward so exclusive/fullscreen content is what mss sees.
        user32.ShowWindow(wintypes.HWND(hwnd), 9)  # SW_RESTORE
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
    except Exception:  # noqa: BLE001
        pass
    try:
        return grab_region_png(left, top, right, bottom, max_width=max_width)
    except Exception as exc:  # noqa: BLE001
        log.debug("WT window grab failed: %s", exc)
        return None


def grab_war_thunder_png_variants(max_width: int = 1920) -> list[tuple[str, bytes]] | None:
    hwnd = find_war_thunder_hwnd()
    if hwnd is None:
        return None
    bounds = _window_rect(hwnd)
    if bounds is None:
        return None
    left, top, right, bottom = bounds
    try:
        user32.ShowWindow(wintypes.HWND(hwnd), 9)
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
    except Exception:  # noqa: BLE001
        pass
    try:
        return grab_region_png_variants(left, top, right, bottom, max_width=max_width)
    except Exception as exc:  # noqa: BLE001
        log.debug("WT window multi-crop failed: %s", exc)
        return None


def grab_primary_monitor_png(max_width: int = 1920) -> bytes:
    """
    Grab nearly the full primary monitor.

    Prefer this only when the WT window cannot be found. Keep the whole results
    area for future parsing; reward extraction currently uses only RP/SL.
    """
    with mss.MSS() as sct:
        monitor = sct.monitors[1]
        shot = sct.grab(monitor)
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    width, height = image.size
    # Tiny margins only — avoid taskbar clock / desktop icons when possible.
    image = image.crop(
        (
            int(width * 0.01),
            int(height * 0.01),
            int(width * 0.99),
            int(height * 0.97),
        )
    )
    rois = _crop_results_rois(image)
    return _png_from_image(rois[0][1], max_width=max_width)


def grab_for_ocr_png(max_width: int = 1920) -> bytes:
    """Prefer the War Thunder window; fall back to the primary monitor."""
    wt = grab_war_thunder_png(max_width=max_width)
    if wt is not None:
        log.info("OCR capture: War Thunder window (results panel)")
        return wt
    log.info("OCR capture: primary monitor (WT window not found)")
    return grab_primary_monitor_png(max_width=max_width)


def grab_for_ocr_png_variants(max_width: int = 1920) -> list[tuple[str, bytes]]:
    """Multi-ROI capture for better reward-digit OCR."""
    wt = grab_war_thunder_png_variants(max_width=max_width)
    if wt:
        log.info("OCR capture: War Thunder window (%s ROI)", len(wt))
        return wt
    log.info("OCR capture: primary monitor single ROI (WT window not found)")
    return [("panel", grab_primary_monitor_png(max_width=max_width))]


def ocr_png_variants_windows(png: bytes) -> list[tuple[str, str]]:
    """Windows.Media.Ocr only — one entry per installed language pack."""
    return asyncio.run(_recognize_png_variants(png))


def ocr_png_variants(
    png: bytes,
    *,
    wt_ui_language: str = "uk",
    backend: str = "auto",
) -> list[tuple[str, str]]:
    from .ocr_backends import ocr_all_backends

    return ocr_all_backends(png, wt_ui_language=wt_ui_language, backend=backend)


def ocr_screen_variants(
    *,
    wt_ui_language: str | None = None,
    backend: str | None = None,
) -> list[tuple[str, str]]:
    """[(engine_id, text), ...] — Windows and/or Tesseract, never merged into one blob."""
    from .settings import load_settings

    settings = load_settings()
    lang = wt_ui_language or settings.wt_ui_language or settings.language
    mode = backend or getattr(settings, "ocr_backend", "auto") or "auto"
    variants: list[tuple[str, str]] = []
    for roi_tag, png in grab_for_ocr_png_variants():
        for eng_tag, text in ocr_png_variants(png, wt_ui_language=lang, backend=mode):
            variants.append((f"{eng_tag}/{roi_tag}", text))
    return variants


def ocr_screen_capture(
    *,
    wt_ui_language: str | None = None,
    backend: str | None = None,
) -> tuple[bytes, list[tuple[str, str]]]:
    """Return (primary_png, variants) so callers can dump the frame that was OCR'd."""
    from .settings import load_settings

    settings = load_settings()
    lang = wt_ui_language or settings.wt_ui_language or settings.language
    mode = backend or getattr(settings, "ocr_backend", "auto") or "auto"
    crops = grab_for_ocr_png_variants()
    primary = crops[0][1] if crops else grab_for_ocr_png()
    variants: list[tuple[str, str]] = []
    for roi_tag, png in crops:
        for eng_tag, text in ocr_png_variants(png, wt_ui_language=lang, backend=mode):
            variants.append((f"{eng_tag}/{roi_tag}", text))
    return primary, variants


def ocr_screen() -> str:
    variants = ocr_screen_variants()
    if not variants:
        return ""
    return "\n\n---OCR---\n\n".join(f"[{tag}]\n{text}" for tag, text in variants)
