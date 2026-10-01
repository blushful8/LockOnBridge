"""Pointer-safe Win32 capture/overlay signatures on 64-bit Python."""
import ctypes
from ctypes import wintypes as w

def bind_graphics_api():
    u, g, k = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32
    signatures = [
        (u.GetDC, [w.HWND], w.HDC),
        (u.ReleaseDC, [w.HWND, w.HDC], ctypes.c_int),
        (u.GetForegroundWindow, [], w.HWND),
        (u.GetClientRect, [w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
        (u.GetWindowRect, [w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
        (u.ClientToScreen, [w.HWND, ctypes.POINTER(w.POINT)], w.BOOL),
        (u.PrintWindow, [w.HWND, w.HDC, w.UINT], w.BOOL),
        (u.BeginPaint, [w.HWND, ctypes.c_void_p], w.HDC),
        (u.EndPaint, [w.HWND, ctypes.c_void_p], w.BOOL),
        (u.CreateWindowExW, [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p], w.HWND),
        (u.SetWindowPos, [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, w.UINT], w.BOOL),
        (u.ShowWindow, [w.HWND, ctypes.c_int], w.BOOL),
        (u.DestroyWindow, [w.HWND], w.BOOL),
        (u.LoadCursorW, [w.HINSTANCE, ctypes.c_void_p], w.HANDLE),
        (u.RegisterClassW, [ctypes.c_void_p], w.WORD),
        (k.GetModuleHandleW, [w.LPCWSTR], w.HMODULE),
        (g.CreateCompatibleDC, [w.HDC], w.HDC),
        (g.CreateCompatibleBitmap, [w.HDC, ctypes.c_int, ctypes.c_int], w.HBITMAP),
        (g.CreateSolidBrush, [w.COLORREF], w.HBRUSH),
        (g.SelectObject, [w.HDC, w.HGDIOBJ], w.HGDIOBJ),
        (g.DeleteObject, [w.HGDIOBJ], w.BOOL),
        (g.DeleteDC, [w.HDC], w.BOOL),
        (g.BitBlt, [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, w.HDC, ctypes.c_int, ctypes.c_int, w.DWORD], w.BOOL),
        (g.GetDIBits, [w.HDC, w.HBITMAP, w.UINT, w.UINT,
            ctypes.c_void_p, ctypes.c_void_p, w.UINT], ctypes.c_int),
    ]
    for fn, args, result in signatures:
        fn.argtypes, fn.restype = args, result
