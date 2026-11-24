from __future__ import annotations

import ctypes
import sys
import traceback
from pathlib import Path

from .config import SettingsStore
from .hotkey import GlobalHotkey, HotkeyError
from .single_instance import SingleInstance
from .translator import SmartTranslator
from .tray import TrayController
from .ui import TranslationWindow
from .windows import enable_dpi_awareness


def _write_crash_log() -> Path | None:
    details = traceback.format_exc()
    app_directory = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
    # 用户目录不可写时，在便携目录保留诊断，不能报告一个并未写出的日志。
    for directory in (Path.home(), app_directory):
        path = directory / "DeskTranslate-crash.log"
        try:
            path.write_text(details, encoding="utf-8")
        except OSError:
            continue
        return path
    return None


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--self-test-subtitles" in arguments:
        from .selftest import subtitle_self_test

        return subtitle_self_test()

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
            dispatch=window.dispatch,
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
            on_toggle=window.toggle_visibility,
            on_translate_clipboard=window.translate_clipboard,
            on_quit=window.request_exit,
            dispatch=window.dispatch,
        )
        window.on_visibility_changed = tray.set_window_visible
        tray.set_window_visible(True)
        tray.start()

        poll_activation = getattr(instance, "poll_activation", None)

        def check_activation() -> None:
            if not callable(poll_activation):
                return
            while not window.stopped.wait(0.25):
                if poll_activation():
                    window.show()

        window.run(check_activation)
        return 0
    except Exception:
        log_path = _write_crash_log()
        message = (
            f"程序启动失败。错误信息已写入：\n{log_path}"
            if log_path is not None
            else f"程序启动失败，且无法写入日志。\n{traceback.format_exc().splitlines()[-1]}"
        )
        try:
            ctypes.windll.user32.MessageBoxW(
                None, message, "DeskTranslate", 0x10
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

