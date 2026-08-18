"""Windows global hotkey registration for DeskTranslate."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import queue
import threading
from collections.abc import Callable


class HotkeyError(RuntimeError):
    pass


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012


def normalize_hotkey(shortcut: str) -> tuple[str, int, int]:
    parts = [part.strip() for part in shortcut.split("+") if part.strip()]
    if len(parts) < 2:
        raise HotkeyError("快捷键至少需要一个修饰键和一个按键")

    aliases = {"CONTROL": "CTRL", "OPTION": "ALT"}
    parts = [aliases.get(part.upper(), part.upper()) for part in parts]
    key = parts[-1]
    modifiers = set(parts[:-1])
    if not modifiers or not modifiers.issubset({"CTRL", "ALT", "SHIFT"}):
        raise HotkeyError("仅支持 Ctrl、Alt、Shift 组合")

    if len(key) == 1 and key in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        virtual_key = ord(key)
    elif key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        virtual_key = 0x70 + int(key[1:]) - 1
    else:
        raise HotkeyError("按键仅支持 A-Z、0-9 或 F1-F12")

    flags = MOD_NOREPEAT
    labels: list[str] = []
    for name, label, flag in (
        ("CTRL", "Ctrl", MOD_CONTROL),
        ("ALT", "Alt", MOD_ALT),
        ("SHIFT", "Shift", MOD_SHIFT),
    ):
        if name in modifiers:
            labels.append(label)
            flags |= flag
    labels.append(key)
    return "+".join(labels), flags, virtual_key


class GlobalHotkey:
    def __init__(self, callback: Callable[[], None], dispatch: Callable[[Callable[[], None]], object]) -> None:
        self.callback = callback
        self.dispatch = dispatch
        self.shortcut: str | None = None
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._startup: queue.Queue[Exception | None] = queue.Queue(maxsize=1)

    def bind(self, shortcut: str) -> str:
        normalized, modifiers, virtual_key = normalize_hotkey(shortcut)
        previous = self.shortcut
        self.stop()
        try:
            self._start(normalized, modifiers, virtual_key)
        except Exception:
            if previous:
                old_normalized, old_modifiers, old_key = normalize_hotkey(previous)
                self._start(old_normalized, old_modifiers, old_key)
            raise
        return normalized

    def _start(self, shortcut: str, modifiers: int, virtual_key: int) -> None:
        if os.name != "nt":
            self.shortcut = shortcut
            return
        self._startup = queue.Queue(maxsize=1)
        self._thread = threading.Thread(
            target=self._run,
            args=(shortcut, modifiers, virtual_key),
            name="DeskTranslateHotkey",
            daemon=True,
        )
        self._thread.start()
        result = self._startup.get(timeout=3)
        if result:
            self._thread.join(timeout=1)
            self._thread = None
            self._thread_id = None
            raise result
        self.shortcut = shortcut

    def stop(self) -> None:
        if self._thread_id and os.name == "nt":
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None
        self._thread_id = None
        self.shortcut = None

    def _run(self, shortcut: str, modifiers: int, virtual_key: int) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, modifiers, virtual_key):
            code = kernel32.GetLastError()
            message = "快捷键已被其他程序占用" if code == 1409 else f"快捷键注册失败（{code}）"
            self._startup.put(HotkeyError(message))
            return
        self._startup.put(None)
        message = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY:
                    self.dispatch(self.callback)
        finally:
            user32.UnregisterHotKey(None, 1)

