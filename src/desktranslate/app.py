from __future__ import annotations

import sys
import traceback
from pathlib import Path
from tkinter import messagebox

from .config import SettingsStore
from .hotkey import GlobalHotkey, HotkeyError
from .single_instance import SingleInstance
from .translator import SmartTranslator
from .tray import TrayController
from .ui import TranslationWindow
from .windows import enable_dpi_awareness


def _write_crash_log() -> Path:
    path = Path.home() / "DeskTranslate-crash.log"
    try:
        path.write_text(traceback.format_exc(), encoding="utf-8")
    except OSError:
        pass
    return path


def main() -> int:
    enable_dpi_awareness()
    instance = SingleInstance("DeskTranslate")
    if not instance.acquire():
        signal = getattr(instance, "signal_existing", None)
        if callable(signal):
            signal()
        return 0

    tray: TrayController | None = None
    hotkey: GlobalHotkey | None = None
    window: TranslationWindow | None = None
    try:
        window = TranslationWindow(SmartTranslator(), SettingsStore())
        hotkey = GlobalHotkey(
            callback=window.toggle_visibility,
            dispatch=lambda callback: window.after(0, callback),
        )

        def change_hotkey(shortcut: str) -> tuple[bool, str]:
            if hotkey is None:
                return False, "快捷键服务尚未启动"
            try:
                normalized = hotkey.bind(shortcut)
            except HotkeyError as exc:
                return False, str(exc)
            except Exception:
                return False, "快捷键注册失败，请换一个组合"
            window.settings.hotkey = normalized
            window.set_hotkey_status(normalized)
            window.save_settings()
            return True, normalized

        window.on_hotkey_change = change_hotkey
        success, message = change_hotkey(window.settings.hotkey)
        if not success:
            window.set_hotkey_status(window.settings.hotkey, error=message)

        tray = TrayController(
            on_toggle=lambda: window.hide() if window.is_visible else window.show(),
            on_translate_clipboard=window.translate_clipboard,
            on_quit=window.request_exit,
            dispatch=lambda callback: window.after(0, callback),
        )
        window.on_visibility_changed = tray.set_window_visible
        tray.set_window_visible(True)
        tray.start()

        poll_activation = getattr(instance, "poll_activation", None)
        if callable(poll_activation):
            def check_activation() -> None:
                if window is None or window.is_exiting:
                    return
                if poll_activation():
                    window.show()
                window.after(250, check_activation)

            window.after(250, check_activation)

        window.mainloop()
        return 0
    except Exception:
        log_path = _write_crash_log()
        try:
            messagebox.showerror(
                "DeskTranslate",
                f"程序启动失败。错误信息已写入：\n{log_path}",
            )
        except Exception:
            pass
        return 1
    finally:
        if hotkey is not None:
            hotkey.stop()
        if tray is not None:
            tray.stop()
        if window is not None and not window.is_exiting:
            try:
                window.request_exit()
            except Exception:
                pass
        instance.release()


if __name__ == "__main__":
    sys.exit(main())

