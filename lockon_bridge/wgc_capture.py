"""Grab one window via Windows Graphics Capture.

GDI BitBlt, PrintWindow and DXGI desktop duplication read the game through a
path that turns off independent flip. Windows then leaves the slower composed
present on until the game is focused again (Alt+Tab). Graphics Capture copies
the window's own swapchain and does not do that.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes

from PIL import Image

log = logging.getLogger("lockon_bridge.wgc")
_LOCK = threading.RLock()

_BGRA = 87
_BGRA_SRGB = 91
_RGBA = 28


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_uint8 * 8),
    ]


class _TexDesc(ctypes.Structure):
    _fields_ = [
        ("Width", ctypes.c_uint),
        ("Height", ctypes.c_uint),
        ("MipLevels", ctypes.c_uint),
        ("ArraySize", ctypes.c_uint),
        ("Format", ctypes.c_uint),
        ("SampleCount", ctypes.c_uint),
        ("SampleQuality", ctypes.c_uint),
        ("Usage", ctypes.c_uint),
        ("BindFlags", ctypes.c_uint),
        ("CPUAccessFlags", ctypes.c_uint),
        ("MiscFlags", ctypes.c_uint),
    ]


class _Mapped(ctypes.Structure):
    _fields_ = [
        ("pData", ctypes.c_void_p),
        ("RowPitch", ctypes.c_uint),
        ("DepthPitch", ctypes.c_uint),
    ]


def _guid(text: str) -> _GUID:
    import uuid

    raw = uuid.UUID(text).bytes_le
    return _GUID.from_buffer_copy(raw)


_IID_TEXTURE2D = _guid("6f15aaf2-d208-4e89-9ab4-489535d34f9c")


def _vtbl(ptr: int):
    return ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]


def _com(ptr: int, index: int, restype, argtypes, *args):
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(_vtbl(ptr)[index])
    return fn(ptr, *args)


def _release(ptr: int | None) -> None:
    if not ptr:
        return
    _com(int(ptr), 2, ctypes.c_ulong, ())


def _query(ptr: int, iid: _GUID) -> int:
    out = ctypes.c_void_p()
    hr = _com(
        ptr,
        0,
        ctypes.c_long,
        (ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)),
        ctypes.byref(iid),
        ctypes.byref(out),
    )
    if hr < 0 or not out.value:
        raise OSError(f"QueryInterface failed ({hr:#x})")
    return int(out.value)


def client_crop_box(
    frame_size: tuple[int, int],
    window_bounds: tuple[int, int, int, int],
    client_origin: tuple[int, int],
    client_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Client rectangle inside a window capture, in frame pixels.

    ``window_bounds`` and ``client_origin`` are screen pixels. The capture is
    the window, while reward regions are calibrated against the client area.
    """
    frame_w, frame_h = int(frame_size[0]), int(frame_size[1])
    if frame_w < 1 or frame_h < 1:
        return 0, 0, max(frame_w, 0), max(frame_h, 0)
    left, top, right, bottom = window_bounds
    bounds_w = int(right - left)
    bounds_h = int(bottom - top)
    client_w, client_h = int(client_size[0]), int(client_size[1])
    if bounds_w < 1 or bounds_h < 1 or client_w < 1 or client_h < 1:
        return 0, 0, frame_w, frame_h
    scale_x = frame_w / float(bounds_w)
    scale_y = frame_h / float(bounds_h)
    x = int(round((int(client_origin[0]) - int(left)) * scale_x))
    y = int(round((int(client_origin[1]) - int(top)) * scale_y))
    w = int(round(client_w * scale_x))
    h = int(round(client_h * scale_y))
    x = max(0, min(x, frame_w - 1))
    y = max(0, min(y, frame_h - 1))
    w = max(1, min(w, frame_w - x))
    h = max(1, min(h, frame_h - y))
    return x, y, w, h


class _Device:
    __slots__ = ("d3d", "context", "winrt_device")

    def __init__(self) -> None:
        d3d11 = ctypes.WinDLL("d3d11")
        d3d11.D3D11CreateDevice.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        d3d11.D3D11CreateDevice.restype = ctypes.c_long
        device = ctypes.c_void_p()
        context = ctypes.c_void_p()
        level = ctypes.c_uint()
        hr = d3d11.D3D11CreateDevice(
            None,
            1,  # D3D_DRIVER_TYPE_HARDWARE
            None,
            0x20,  # D3D11_CREATE_DEVICE_BGRA_SUPPORT
            None,
            0,
            7,  # D3D11_SDK_VERSION
            ctypes.byref(device),
            ctypes.byref(level),
            ctypes.byref(context),
        )
        if hr < 0 or not device.value or not context.value:
            raise OSError(f"D3D11CreateDevice failed ({hr:#x})")
        self.d3d = int(device.value)
        self.context = int(context.value)
        dxgi = _query(self.d3d, _guid("54ec77fa-1377-44e6-8c32-88fd5f44c84c"))
        try:
            from winrt.windows.graphics.directx.direct3d11.interop import (
                create_direct3d11_device_from_dxgi_device,
            )

            self.winrt_device = create_direct3d11_device_from_dxgi_device(dxgi)
        finally:
            _release(dxgi)


_DEVICE: _Device | None = None


def _device() -> _Device:
    global _DEVICE
    with _LOCK:
        if _DEVICE is None:
            _DEVICE = _Device()
        return _DEVICE


def _copy_texture(device: _Device, texture: int) -> Image.Image | None:
    desc = _TexDesc()
    _com(texture, 10, None, (ctypes.POINTER(_TexDesc),), ctypes.byref(desc))
    width = int(desc.Width)
    height = int(desc.Height)
    if width < 2 or height < 2:
        return None
    if int(desc.SampleCount) not in (0, 1):
        log.warning("WGC frame is multisampled (%s) — skipped", desc.SampleCount)
        return None
    fmt = int(desc.Format)
    if fmt not in (_BGRA, _BGRA_SRGB, _RGBA):
        log.warning("WGC frame format %s is not 8-bit BGRA/RGBA — skipped", fmt)
        return None
    staging_desc = _TexDesc()
    ctypes.memmove(ctypes.byref(staging_desc), ctypes.byref(desc), ctypes.sizeof(_TexDesc))
    staging_desc.Usage = 3  # STAGING
    staging_desc.BindFlags = 0
    staging_desc.CPUAccessFlags = 0x20000  # READ
    staging_desc.MiscFlags = 0
    staging_desc.SampleCount = 1
    staging_desc.SampleQuality = 0
    staging = ctypes.c_void_p()
    hr = _com(
        device.d3d,
        5,
        ctypes.c_long,
        (ctypes.POINTER(_TexDesc), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
        ctypes.byref(staging_desc),
        None,
        ctypes.byref(staging),
    )
    if hr < 0 or not staging.value:
        log.warning("WGC staging texture failed (%s)", hex(hr & 0xFFFFFFFF))
        return None
    staging_ptr = int(staging.value)
    mapped = _Mapped()
    try:
        _com(
            device.context,
            47,
            None,
            (ctypes.c_void_p, ctypes.c_void_p),
            staging_ptr,
            texture,
        )
        hr = _com(
            device.context,
            14,
            ctypes.c_long,
            (ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(_Mapped)),
            staging_ptr,
            0,
            1,  # D3D11_MAP_READ
            0,
            ctypes.byref(mapped),
        )
        if hr < 0 or not mapped.pData:
            log.warning("WGC map failed (%s)", hex(hr & 0xFFFFFFFF))
            return None
        try:
            row_bytes = width * 4
            pitch = int(mapped.RowPitch) or row_bytes
            base = int(mapped.pData)
            raw = bytearray(row_bytes * height)
            for y in range(height):
                raw[y * row_bytes : (y + 1) * row_bytes] = ctypes.string_at(base + y * pitch, row_bytes)
        finally:
            _com(device.context, 15, None, (ctypes.c_void_p, ctypes.c_uint), staging_ptr, 0)
    finally:
        _release(staging_ptr)
    mode = "BGRX" if fmt != _RGBA else "RGBX"
    return Image.frombytes("RGB", (width, height), bytes(raw), "raw", mode)


def _frame_image(device: _Device, frame) -> Image.Image | None:
    from winrt.windows.graphics.directx.direct3d11.interop import get_dxgi_surface_from_object

    surface = int(get_dxgi_surface_from_object(frame.surface) or 0)
    if not surface:
        return None
    texture = 0
    try:
        texture = _query(surface, _IID_TEXTURE2D)
        return _copy_texture(device, texture)
    finally:
        _release(texture)
        _release(surface)


def _usable(image: Image.Image) -> bool:
    from PIL import ImageStat

    try:
        return float(ImageStat.Stat(image.convert("L")).mean[0]) >= 8.0
    except Exception:  # noqa: BLE001
        return True


def grab_window_client(hwnd: int, *, timeout: float = 0.45) -> Image.Image | None:
    """Client pixels of ``hwnd``, or None. The capture session is closed before return."""
    with _LOCK:
        return _grab_locked(int(hwnd), timeout=timeout)


def _grab_locked(hwnd: int, *, timeout: float) -> Image.Image | None:
    try:
        from winrt.windows.graphics.capture import Direct3D11CaptureFramePool, GraphicsCaptureSession
        from winrt.windows.graphics.capture.interop import create_for_window
        from winrt.windows.graphics.directx import DirectXPixelFormat
    except Exception as exc:  # noqa: BLE001
        log.warning("Windows Graphics Capture is unavailable: %s", exc)
        return None
    try:
        if not GraphicsCaptureSession.is_supported():
            log.warning("Windows Graphics Capture is not supported on this PC")
            return None
    except Exception as exc:  # noqa: BLE001
        log.warning("Windows Graphics Capture support check failed: %s", exc)
        return None

    item = None
    pool = None
    session = None
    try:
        device = _device()
        item = create_for_window(hwnd)
        size = item.size
        width, height = int(size.width), int(size.height)
        if width < 2 or height < 2:
            log.info("WGC item has no size")
            return None
        pool = Direct3D11CaptureFramePool.create_free_threaded(
            device.winrt_device,
            DirectXPixelFormat.B8_G8_R8_A8_UINT_NORMALIZED,
            2,
            size,
        )
        session = pool.create_capture_session(item)
        try:
            session.is_cursor_capture_enabled = False
        except Exception:  # noqa: BLE001
            pass
        try:
            session.is_border_required = False
        except Exception:  # noqa: BLE001
            log.debug("WGC border cannot be hidden on this Windows build")
        try:
            session.include_secondary_windows = False
        except Exception:  # noqa: BLE001
            pass
        session.start_capture()
        deadline = time.monotonic() + max(0.05, float(timeout))
        image: Image.Image | None = None
        while time.monotonic() < deadline:
            frame = pool.try_get_next_frame()
            if frame is None:
                time.sleep(0.015)
                continue
            try:
                image = _frame_image(device, frame)
            finally:
                frame.close()
            if image is not None and _usable(image):
                break
            image = None
        if image is None:
            log.info("WGC produced no readable frame")
            return None
        cropped = _crop_to_client(hwnd, image)
        if cropped is None or not _usable(cropped):
            log.info("WGC client crop was empty")
            return None
        return cropped
    except Exception as exc:  # noqa: BLE001
        log.warning("WGC grab failed: %s", exc)
        return None
    finally:
        for obj in (session, pool):
            if obj is None:
                continue
            try:
                obj.close()
            except Exception:  # noqa: BLE001
                pass


def _crop_to_client(hwnd: int, image: Image.Image) -> Image.Image | None:
    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    rect = wintypes.RECT()
    try:
        ok = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            9,  # DWMWA_EXTENDED_FRAME_BOUNDS
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if ok != 0 or rect.right <= rect.left or rect.bottom <= rect.top:
            raise OSError("no extended frame")
    except Exception:  # noqa: BLE001
        if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return image
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return image
    origin = wintypes.POINT(0, 0)
    client = wintypes.RECT()
    if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(origin)):
        return image
    if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(client)):
        return image
    box = client_crop_box(
        image.size,
        (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)),
        (int(origin.x), int(origin.y)),
        (int(client.right - client.left), int(client.bottom - client.top)),
    )
    if box[2] < 400 or box[3] < 300:
        return None
    if box == (0, 0, image.size[0], image.size[1]):
        return image
    return image.crop((box[0], box[1], box[0] + box[2], box[1] + box[3]))
