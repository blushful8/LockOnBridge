"""Load compressed LockOn Bridge UI PNGs (assets/ui)."""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageTk


def _ui_dir() -> Path | None:
    candidates = [
        Path(__file__).resolve().parent.parent / "assets" / "ui",
    ]
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.insert(0, Path(meipass) / "assets" / "ui")
        candidates.insert(0, Path(sys.executable).resolve().parent / "assets" / "ui")
    for path in candidates:
        if path.is_dir():
            return path
    return None


def ui_path(name: str) -> Path | None:
    root = _ui_dir()
    if root is None:
        return None
    path = root / name
    return path if path.is_file() else None


@lru_cache(maxsize=64)
def load_rgba(name: str, size: tuple[int, int] | None = None) -> Image.Image | None:
    path = ui_path(name)
    if path is None:
        return None
    image = Image.open(path).convert("RGBA")
    if size is not None and image.size != size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    return image


def photo(name: str, size: tuple[int, int] | None = None) -> ImageTk.PhotoImage | None:
    image = load_rgba(name, size=size)
    if image is None:
        return None
    return ImageTk.PhotoImage(image)
