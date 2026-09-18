from __future__ import annotations

import asyncio
from io import BytesIO

import mss
from PIL import Image, ImageEnhance, ImageOps
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapDecoder
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream


def _ocr_engines() -> list:
    engines: list = []
    seen: set[str] = set()

    def add(engine) -> None:
        if engine is None:
            return
        try:
            tag = str(engine.recognizer_language.language_tag)
        except Exception:  # noqa: BLE001
            tag = str(id(engine))
        if tag in seen:
            return
        seen.add(tag)
        engines.append(engine)

    add(OcrEngine.try_create_from_user_profile_languages())
    for tag in ("uk-UA", "uk", "ru-RU", "ru", "en-US", "en-GB", "en"):
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


async def _recognize_png(data: bytes) -> str:
    engines = _ocr_engines()
    if not engines:
        raise RuntimeError(
            "Windows OCR engine unavailable. Install an OCR language pack "
            "(Settings → Time & language → Language & region)."
        )
    texts: list[str] = []
    for engine in engines:
        try:
            text = await _recognize_png_with_engine(data, engine)
        except Exception:  # noqa: BLE001
            continue
        stripped = (text or "").strip()
        if stripped and stripped not in texts:
            texts.append(stripped)
    return "\n\n".join(texts)


def grab_primary_monitor_png(max_width: int = 1920) -> bytes:
    with mss.MSS() as sct:
        monitor = sct.monitors[1]
        shot = sct.grab(monitor)
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    # Keep the central game UI; skip taskbar / far edges that confuse OCR.
    width, height = image.size
    image = image.crop(
        (
            int(width * 0.06),
            int(height * 0.05),
            int(width * 0.94),
            int(height * 0.90),
        )
    )

    if image.width < 1280:
        ratio = 1280 / float(image.width)
        image = image.resize(
            (1280, max(1, int(image.height * ratio))),
            Image.Resampling.LANCZOS,
        )
    elif image.width > max_width:
        ratio = max_width / float(image.width)
        image = image.resize(
            (max_width, max(1, int(image.height * ratio))),
            Image.Resampling.LANCZOS,
        )

    # Mild contrast boost helps thin War Thunder UI fonts.
    image = ImageOps.autocontrast(image, cutoff=1)
    image = ImageEnhance.Contrast(image).enhance(1.25)

    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def ocr_screen() -> str:
    png = grab_primary_monitor_png()
    return asyncio.run(_recognize_png(png))
