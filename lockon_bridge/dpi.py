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
