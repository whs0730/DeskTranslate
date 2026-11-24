from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()


def native_glass_supported() -> bool:
    """DWM 材质（暗色 + 圆角 + acrylic/Mica）是否可用。

    首帧 HTML 也要靠这个结果决定是否加 `native-glass`，所以判定必须和
    apply_window_material 用的是同一个来源，否则首帧与加载后的外观会不一致。
    """
    if sys.platform != "win32":
        return False
    try:
        return sys.getwindowsversion().build >= 22621
    except Exception:
        return False


def apply_window_material(form, accent_alpha: int | None = None, liquid: bool = False, opacity: float = 1.0, transparent: bool = False) -> bool:
    if not native_glass_supported():
        return False
    from System.Drawing import Color

    form.Opacity = opacity
    hwnd_value = int(form.Handle.ToInt64())
    hwnd = wintypes.HWND(hwnd_value)
    dwm = ctypes.windll.dwmapi
    dark = ctypes.c_int(1)
    corner = ctypes.c_int(2)
    acrylic = ctypes.c_int(3)
    dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
    dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))

    material = accent_alpha is not None and set_window_accent(form, accent_alpha, liquid, transparent)
    if transparent or material:
        # 显式关闭 DWMWA_SYSTEMBACKDROP_TYPE，避免深色主题下 pywebview 残留的 Mica 在失焦时回落变色。
        no_backdrop = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(no_backdrop), ctypes.sizeof(no_backdrop))
    else:
        result = dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(acrylic), ctypes.sizeof(acrylic))
        if result != 0:
            return False

    # 将 DWM 材质延伸到客户区；仅网页背景透明，文字和控件仍保持不透明。
    margins = (ctypes.c_int * 4)(*([0] * 4 if transparent else [-1] * 4))
    result = dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
    if result != 0:
        return False
    form.BackColor = Color.FromArgb(255, 34, 40, 43)
    form.webview.DefaultBackgroundColor = Color.Transparent
    _install_keep_active_glass_hook(hwnd_value)
    return True


class _AccentPolicy(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class _WindowCompositionAttributeData(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


_ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
_ACCENT_ENABLE_BLURBEHIND = 3
_WCA_ACCENT_POLICY = 19
_WM_ACTIVATE = 0x0006
_WM_NCDESTROY = 0x0082
_WM_NCACTIVATE = 0x0086
_SW_SHOWNOACTIVATE = 4
_GLASS_SUBCLASS_ID = 0x4454474C  # 'DTGL'

_LRESULT = ctypes.c_ssize_t
_UINT_PTR = ctypes.c_size_t
_DWORD_PTR = ctypes.c_size_t
_SUBCLASSPROC = ctypes.WINFUNCTYPE(
    _LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
    _UINT_PTR,
    _DWORD_PTR,
)

# 仅使用单个模块级 SUBCLASSPROC 回调（通过 comctl32 SetWindowSubclass 挂载，不覆盖 WinForms 的 GWLP_WNDPROC），
# 既避免释放正在执行中的 ctypes 回调，又在 WM_NCDESTROY 时自动安全卸载。
_window_accent_policies: dict[int, _AccentPolicy] = {}
_subclass_callback: object | None = None


def _apply_accent_policy(hwnd_value: int, policy: _AccentPolicy) -> bool:
    try:
        setter = ctypes.windll.user32.SetWindowCompositionAttribute
    except (AttributeError, OSError):
        return False
    data = _WindowCompositionAttributeData()
    data.Attribute = _WCA_ACCENT_POLICY
    data.Data = ctypes.cast(ctypes.byref(policy), ctypes.c_void_p)
    data.SizeOfData = ctypes.sizeof(policy)
    return bool(setter(wintypes.HWND(hwnd_value), ctypes.byref(data)))


def _comctl32_subclass_api():
    comctl32 = ctypes.windll.comctl32
    set_subclass = comctl32.SetWindowSubclass
    set_subclass.argtypes = [wintypes.HWND, _SUBCLASSPROC, _UINT_PTR, _DWORD_PTR]
    set_subclass.restype = wintypes.BOOL
    remove_subclass = comctl32.RemoveWindowSubclass
    remove_subclass.argtypes = [wintypes.HWND, _SUBCLASSPROC, _UINT_PTR]
    remove_subclass.restype = wintypes.BOOL
    def_subclass = comctl32.DefSubclassProc
    def_subclass.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    def_subclass.restype = _LRESULT
    return set_subclass, remove_subclass, def_subclass


def _glass_subclass_proc(hwnd, msg, wparam, lparam, subclass_id, ref_data):
    hwnd_value = int(hwnd or 0)
    _, remove_subclass, def_subclass = _comctl32_subclass_api()
    if msg == _WM_NCACTIVATE:
        # 失焦时以 wParam=1 转发给默认过程：返回 1 允许系统正常失焦，同时避免 DWM 将玻璃帧切入非活动回落色。
        return def_subclass(hwnd, msg, 1 if wparam == 0 else wparam, lparam)
    if msg == _WM_ACTIVATE:
        result = def_subclass(hwnd, msg, wparam, lparam)
        policy = _window_accent_policies.get(hwnd_value)
        if policy is not None and policy.AccentState != 0:
            _apply_accent_policy(hwnd_value, policy)
        return result
    if msg == _WM_NCDESTROY:
        _window_accent_policies.pop(hwnd_value, None)
        if _subclass_callback is not None:
            remove_subclass(hwnd, _subclass_callback, subclass_id)
        return def_subclass(hwnd, msg, wparam, lparam)
    return def_subclass(hwnd, msg, wparam, lparam)


def _install_keep_active_glass_hook(hwnd_value: int) -> None:
    """保持无边框玻璃窗在失焦时维持活动态 DWM 材质，避免点击设置或切窗时整窗明暗跳变。"""
    global _subclass_callback
    if sys.platform != "win32" or not hwnd_value:
        return
    try:
        set_subclass, _, _ = _comctl32_subclass_api()
    except (AttributeError, OSError):
        return
    if _subclass_callback is None:
        _subclass_callback = _SUBCLASSPROC(_glass_subclass_proc)
    set_subclass(wintypes.HWND(hwnd_value), _subclass_callback, _GLASS_SUBCLASS_ID, 0)


def set_window_accent(form, alpha: int, liquid: bool = False, transparent: bool = False) -> bool:
    if sys.platform != "win32":
        return False
    hwnd_value = int(form.Handle.ToInt64())
    policy = _AccentPolicy()
    policy.AccentState = 0 if transparent else (_ACCENT_ENABLE_BLURBEHIND if liquid else _ACCENT_ENABLE_ACRYLICBLURBEHIND)
    policy.AccentFlags = 0
    # GradientColor 是 0xAABBGGRR：偏冷的深灰底色，浓度由 alpha 决定。
    policy.GradientColor = ((alpha & 0xFF) << 24) | 0x00362E29
    policy.AnimationId = 0
    applied = _apply_accent_policy(hwnd_value, policy)
    if applied:
        if policy.AccentState == 0:
            _window_accent_policies.pop(hwnd_value, None)
        else:
            _window_accent_policies[hwnd_value] = policy
    return applied


def start_native_resize(form, edge: str) -> None:
    directions = {"w": 1, "e": 2, "n": 3, "nw": 4, "ne": 5, "s": 6, "sw": 7, "se": 8}
    code = directions[edge]
    _post_system_command(form, 0xF000 + code)


def start_native_move(form) -> None:
    _post_system_command(form, 0xF012)


def show_window_no_activate(form) -> None:
    """以 SW_SHOWNOACTIVATE 显示窗口：允许 WebView2 立即完成首帧渲染，但不提前抢占主窗焦点。"""
    hwnd = wintypes.HWND(form.Handle.ToInt64())
    ctypes.windll.user32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)


def activate_window(form) -> None:
    hwnd = wintypes.HWND(form.Handle.ToInt64())
    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, -20)
    user32.SetWindowLongW(hwnd, -20, style & ~0x08000000)
    form.Activate()


def _post_system_command(form, command: int) -> None:
    from System import Action

    def begin() -> None:
        # 桥调用可能在鼠标松开后才到达；此时启动系统拖动会让窗口跟着下一次指针移动。
        if command == 0xF012 and not ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000:
            return
        hwnd = wintypes.HWND(form.Handle.ToInt64())
        ctypes.windll.user32.ReleaseCapture()
        # WM_SYSCOMMAND 让 Windows 执行整个交互，不跨 JS/Python 逐帧修改几何尺寸。
        ctypes.windll.user32.PostMessageW(hwnd, 0x0112, command, 0)

    form.BeginInvoke(Action(begin))
