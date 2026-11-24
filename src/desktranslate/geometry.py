"""窗口几何的校验。

Windows 在窗口最小化时会把位置报告成 ``-32000`` 之类的哨兵坐标，并把尺寸压到
接近 0；如果把这些值存进设置，下次启动就会把窗口还原到屏幕外。这里只放两个
纯函数（可单独测试）和一个读取屏幕工作区的辅助函数。
"""

from __future__ import annotations

Rect = tuple[int, int, int, int]  # left, top, right, bottom

# 窗口与屏幕至少要有这么多像素重叠，才算“用户还能看见”。
MIN_VISIBLE_WIDTH = 80
MIN_VISIBLE_HEIGHT = 40


def work_areas() -> list[Rect]:
    """返回所有显示器的可用区域（排除任务栏）；失败时退回主屏尺寸。"""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
    except (AttributeError, ImportError, OSError):
        return []

    areas: list[Rect] = []

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    MonitorEnumProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )

    def collect(monitor, _hdc, _rect, _data) -> bool:
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            work = info.rcWork
            areas.append((work.left, work.top, work.right, work.bottom))
        return True

    callback = MonitorEnumProc(collect)
    user32.EnumDisplayMonitors(None, None, callback, 0)
    if not areas:
        width = user32.GetSystemMetrics(0) or 1024
        height = user32.GetSystemMetrics(1) or 768
        areas.append((0, 0, width, height))
    return areas


def intersects_visible_area(x: int, y: int, width: int, height: int, areas: list[Rect]) -> bool:
    """窗口是否与至少一个屏幕工作区有足够重叠。"""
    for left, top, right, bottom in areas:
        overlap_width = min(x + width, right) - max(x, left)
        overlap_height = min(y + height, bottom) - max(y, top)
        if overlap_width >= MIN_VISIBLE_WIDTH and overlap_height >= MIN_VISIBLE_HEIGHT:
            return True
    return False


def _union_bounds(areas: list[Rect]) -> Rect:
    left = min(area[0] for area in areas)
    top = min(area[1] for area in areas)
    right = max(area[2] for area in areas)
    bottom = max(area[3] for area in areas)
    return left, top, right, bottom


def sanitize_window_geometry(
    x: int | None,
    y: int | None,
    width: int | None,
    height: int | None,
    areas: list[Rect],
    *,
    min_width: int,
    min_height: int,
    default_width: int,
    default_height: int,
) -> tuple[int | None, int | None, int, int]:
    """把保存的几何修正到看得见的位置。

    - 没有保存过位置时返回 ``None``，交给 webview 自己居中。
    - 尺寸小于最小尺寸（最小化时存下的是 158×26 之类的值）时退回默认尺寸，
      而不是仅仅撑到最小尺寸。
    - 位置不可见（屏幕外、哨兵坐标、拔掉了副屏）时，放到工作区中央。
    - 只是部分越界的窗口保持原样：Windows 允许用户把窗口拖出边缘一点，
      这里不替用户改位置。
    """
    width = int(width or default_width)
    height = int(height or default_height)
    if width < min_width:
        width = default_width
    if height < min_height:
        height = default_height
    if not areas or x is None or y is None:
        return None, None, width, height

    x, y = int(x), int(y)
    if intersects_visible_area(x, y, width, height, areas):
        return x, y, width, height

    left, top, right, bottom = _union_bounds(areas)
    return (
        left + max((right - left - width) // 2, 0),
        top + max((bottom - top - height) // 3, 0),
        width,
        height,
    )