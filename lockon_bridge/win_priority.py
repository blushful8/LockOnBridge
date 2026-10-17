"""Keep OCR off the CPU the game needs.

The worker is a separate process started below normal priority. The bridge
thread that waits on it also drops for the duration of that wait, then returns
to normal so the UI stays responsive.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager

# CreateProcess dwCreationFlags. Combined with CREATE_NO_WINDOW.
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
_THREAD_PRIORITY_BELOW_NORMAL = -1
_THREAD_PRIORITY_NORMAL = 0


def deprioritize_current_process() -> None:
    """This process should lose the CPU to a foreground game."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    kernel.SetPriorityClass.restype = wintypes.BOOL
    kernel.SetPriorityClass(kernel.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)

    class _Throttle(ctypes.Structure):
        _fields_ = [
            ("Version", wintypes.ULONG),
            ("ControlMask", wintypes.ULONG),
            ("StateMask", wintypes.ULONG),
        ]

    # EcoQoS: prefer efficiency cores and yield execution speed to the game.
    state = _Throttle(1, 0x1, 0x1)
    kernel.SetProcessInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.SetProcessInformation.restype = wintypes.BOOL
    kernel.SetProcessInformation(
        kernel.GetCurrentProcess(),
        4,  # ProcessPowerThrottling
        ctypes.byref(state),
        ctypes.sizeof(state),
    )


@contextmanager
def yield_cpu_to_foreground():
    """Drop this thread below normal until the block ends."""
    if sys.platform != "win32":
        yield
        return
    import ctypes

    kernel = ctypes.windll.kernel32
    kernel.GetCurrentThread.restype = ctypes.c_void_p
    kernel.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
    kernel.SetThreadPriority.restype = ctypes.c_bool
    thread = kernel.GetCurrentThread()
    kernel.SetThreadPriority(thread, _THREAD_PRIORITY_BELOW_NORMAL)
    try:
        yield
    finally:
        kernel.SetThreadPriority(thread, _THREAD_PRIORITY_NORMAL)
