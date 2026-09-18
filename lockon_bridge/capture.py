from __future__ import annotations

import asyncio
from io import BytesIO

import mss
from PIL import Image
from winrt.windows.graphics.imaging import BitmapDecoder
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream


async def _recognize_png(data: bytes) -> str:
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(data)
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError(
            "Windows OCR engine unavailable. Install an OCR language pack "
            "(Settings → Time & language → Language & region)."
        )
    result = await engine.recognize_async(bitmap)
    return result.text or ""


def grab_primary_monitor_png(max_width: int = 1920) -> bytes:
    with mss.MSS() as sct:
        monitor = sct.monitors[1]
        shot = sct.grab(monitor)
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    # Mild upscale helps OCR on fine UI text when the capture is already small.
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
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def ocr_screen() -> str:
    png = grab_primary_monitor_png()
    return asyncio.run(_recognize_png(png))
