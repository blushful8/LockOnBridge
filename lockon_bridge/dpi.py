"""Windows DPI awareness for sharp Tk UI on HiDPI displays.

Without this, Tk often renders at 96 DPI and Windows bitmap-scales the window
(blurry). Opening a native messagebox can make it look sharper afterward —
that matches the "Test OCR → suddenly crisp" report.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger("lockon_bridge")

_applied = False


def enable_windows_dpi_awareness() -> None:
    """Call once, before creating tk.Tk()."""
    global _applied
    if _applied or sys.platform != "win32":
        return
    _applied = True
    try:
        import ctypes

        # Per-monitor V2 (Win10 1703+)
        try:
            awareness_ctx = ctypes.c_void_p(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            if ctypes.windll.user32.SetProcessDpiAwarenessContext(awareness_ctx):
                log.debug("DPI: PerMonitorV2 via SetProcessDpiAwarenessContext")
                return
        except (AttributeError, OSError):
            pass

        try:
            # 2 = PROCESS_PER_MONITOR_DPI_AWARE
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            log.debug("DPI: PerMonitor via SetProcessDpiAwareness")
            return
        except (AttributeError, OSError):
            pass

        try:
            ctypes.windll.user32.SetProcessDPIAware()
            log.debug("DPI: SetProcessDPIAware")
        except (AttributeError, OSError):
            pass
    except Exception as exc:  # noqa: BLE001
        log.debug("DPI awareness skipped: %s", exc)


def sync_tk_scaling(root) -> None:
    """Align Tcl/Tk point scaling with the actual window DPI."""
    if sys.platform != "win32":
        return
    try:
        root.update_idletasks()
        pixels_per_inch = float(root.winfo_fpixels("1i"))
        if pixels_per_inch > 0:
            # Tk scaling = pixels per point (1 point = 1/72 inch)
            root.tk.call("tk", "scaling", pixels_per_inch / 72.0)
    except Exception as exc:  # noqa: BLE001
        log.debug("tk scaling sync skipped: %s", exc)


def fit_toplevel(
    win,
    *,
    min_w: int = 360,
    min_h: int = 240,
    pad_w: int = 24,
    pad_h: int = 36,
    x: int | None = None,
    y: int | None = None,
) -> None:
    """
    Grow a Toplevel so packed children (buttons, wrapped labels) all fit.

    Does not shrink an already-larger window the user resized.
    """
    try:
        win.update_idletasks()
    except Exception:  # noqa: BLE001
        return
    try:
        req_w = int(win.winfo_reqwidth()) + pad_w
        req_h = int(win.winfo_reqheight()) + pad_h
        screen_w = int(win.winfo_screenwidth())
        screen_h = int(win.winfo_screenheight())
        cur_w = int(win.winfo_width())
        cur_h = int(win.winfo_height())
    except Exception:  # noqa: BLE001
        return
    need_w = max(min_w, req_w)
    need_h = max(min_h, req_h)
    max_w = max(min_w, screen_w - 48)
    max_h = max(min_h, screen_h - 96)
    final_w = min(need_w, max_w)
    final_h = min(need_h, max_h)
    # Keep user-enlarged size, but always expand if content overflows.
    if cur_w >= 50 and cur_h >= 50:
        final_w = max(cur_w, final_w) if cur_w >= min_w else final_w
        final_h = max(cur_h, final_h) if cur_h >= min_h else final_h
        if cur_w < need_w or cur_h < need_h:
            final_w = max(cur_w, min(need_w, max_w))
            final_h = max(cur_h, min(need_h, max_h))
    try:
        win.minsize(min(min_w, final_w), min(min_h, final_h))
        if x is not None and y is not None:
            win.geometry(f"{final_w}x{final_h}+{x}+{y}")
        else:
            win.geometry(f"{final_w}x{final_h}")
    except Exception as exc:  # noqa: BLE001
        log.debug("fit_toplevel skipped: %s", exc)
