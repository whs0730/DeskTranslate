from __future__ import annotations

import ctypes
import sys
import tkinter as tk


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        # Per-monitor V2 awareness on modern Windows.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            return


def apply_rounded_corners(window: tk.Tk, radius: int = 22) -> None:
    if sys.platform != "win32" or not window.winfo_exists():
        return
    try:
        hwnd = window.winfo_id()
        width = max(window.winfo_width(), 1)
        height = max(window.winfo_height(), 1)
        region = ctypes.windll.gdi32.CreateRoundRectRgn(
            0, 0, width + 1, height + 1, radius, radius
        )
        ctypes.windll.user32.SetWindowRgn(hwnd, region, True)

        # Ask Windows 11 for its native rounded-corner treatment as well.
        preference = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference)
        )
    except (AttributeError, OSError):
        return

