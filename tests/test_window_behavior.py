import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from desktranslate.config import AppSettings, SettingsStore
from desktranslate.translator import LanguageDirection, TranslationResult, TranslationTimeoutError
from desktranslate.ui import DesktopApi, TranslationWindow, MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT, MIN_SUBTITLE_HEIGHT
from desktranslate.windows import start_native_move, start_native_resize


class WindowBehaviorTests(unittest.TestCase):
    def test_translation_preserves_result_and_optional_phonetics(self) -> None:
        translator = Mock()
        translator.translate.return_value = TranslationResult(
            "apple", "zh-CN", "en", "MyMemory", phonetic="/ˈæpəl/"
        )
        response = DesktopApi(SimpleNamespace(translator=translator, settings=AppSettings())).translate(" 苹果 ", "auto")
        self.assertTrue(response["ok"])
        self.assertEqual(response["text"], "apple")
        self.assertEqual(response["phonetic"], "/ˈæpəl/")
        translator.translate.assert_called_once_with("苹果", LanguageDirection.AUTO)

    def test_boundary_rejects_invalid_text_and_direction_before_network(self) -> None:
        translator = Mock()
        api = DesktopApi(SimpleNamespace(translator=translator, settings=AppSettings()))
        for text, direction in ((" ", "auto"), ("a" * 3001, "auto"), ("apple", "unknown"), (42, "auto")):
            with self.subTest(text_type=type(text), direction=direction):
                with self.assertRaises(ValueError):
                    api.translate(text, direction)
        translator.translate.assert_not_called()

    def test_network_failure_is_visible_and_not_a_successful_result(self) -> None:
        translator = Mock()
        translator.translate.side_effect = TranslationTimeoutError("请求超时")
        response = DesktopApi(SimpleNamespace(translator=translator, settings=AppSettings())).translate("hello", "auto")
        self.assertFalse(response["ok"])
        self.assertIn("请求超时", response["text"])
        self.assertIn("网络", response["message"])

    @patch("desktranslate.ui.webview.create_window")
    def test_previous_geometry_and_direction_load_into_offline_window(self, create_window: Mock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            store.save(AppSettings(window_x=-30, window_y=25, window_width=720, window_height=480, direction="zh_to_en"))
            window = TranslationWindow(Mock(), store)
            args = create_window.call_args.kwargs
            self.assertEqual((args["width"], args["height"], args["x"], args["y"]), (720, 480, -30, 25))
            self.assertEqual(window.direction, LanguageDirection.ZH_TO_EN)
            self.assertNotIn("url", args)
            self.assertIn("DeskTranslate", args["html"])
            self.assertNotIn("__DESKTRANSLATE_NONCE__", args["html"])

    @patch("desktranslate.ui.webview.create_window")
    def test_resize_and_position_persist_without_writing_on_each_frame(self, create_window: Mock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            window = TranslationWindow(Mock(), store)
            window._remember_size(880, 420)
            window._remember_position(-50, 60)
            self.assertFalse(path.exists())
            window.hide()
            saved = store.load()
            self.assertEqual((saved.window_width, saved.window_height, saved.window_x, saved.window_y), (880, 420, -50, 60))
            create_window.return_value.hide.assert_called_once()
            create_window.return_value.destroy.assert_not_called()

    @patch("desktranslate.windows._post_system_command")
    def test_all_eight_resize_directions_use_native_system_sizing(self, post: Mock) -> None:
        form = object()
        for edge, code in {"w": 1, "e": 2, "n": 3, "nw": 4, "ne": 5, "s": 6, "sw": 7, "se": 8}.items():
            with self.subTest(edge=edge):
                start_native_resize(form, edge)
                post.assert_called_with(form, 0xF000 + code)

    def test_move_command_ignores_a_released_mouse(self) -> None:
        user32 = Mock()
        form = SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda: 123), BeginInvoke=lambda action: action())
        with patch.dict("sys.modules", {"System": SimpleNamespace(Action=lambda fn: fn)}), patch("desktranslate.windows.ctypes.windll", SimpleNamespace(user32=user32)):
            user32.GetAsyncKeyState.return_value = 0
            start_native_move(form)
            user32.PostMessageW.assert_not_called()
            user32.GetAsyncKeyState.return_value = 0x8000
            start_native_move(form)
            user32.PostMessageW.assert_called_once()

    def test_mode_changes_only_main_window_minimum(self) -> None:
        host = SimpleNamespace(window=object(), _set_minimum_content_size=Mock())
        api = DesktopApi(host)
        api.set_window_mode("subtitles")
        host._set_minimum_content_size.assert_called_with(host.window, MIN_WINDOW_WIDTH, MIN_SUBTITLE_HEIGHT)
        api.set_window_mode("translation")
        host._set_minimum_content_size.assert_called_with(host.window, MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        with self.assertRaises(ValueError):
            api.set_window_mode("unknown")

    def test_unexpected_translation_bug_is_not_masked_as_network_failure(self) -> None:
        translator = Mock()
        translator.translate.side_effect = RuntimeError("bug")
        with self.assertRaisesRegex(RuntimeError, "bug"):
            DesktopApi(SimpleNamespace(translator=translator, settings=AppSettings())).translate("hello", "auto")

    @patch("desktranslate.ui.show_window_no_activate")
    @patch("desktranslate.ui.apply_window_material")
    @patch("desktranslate.ui.activate_window")
    @patch("desktranslate.ui.webview.create_window")
    def test_settings_uses_a_single_independent_resizable_window(
        self,
        create_window: Mock,
        activate_window: Mock,
        apply_material: Mock,
        show_no_activate: Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = TranslationWindow(Mock(), SettingsStore(Path(directory) / "settings.json"))
            parent = window.window
            child = MagicMock()
            create_window.reset_mock()
            create_window.return_value = child
            window._on_ui = lambda callback: callback()
            window._set_minimum_content_size = Mock()
            window.open_settings()
            window.open_settings()
            create_window.assert_called_once()
            args = create_window.call_args.kwargs
            self.assertTrue(args["resizable"])
            self.assertEqual(args["min_size"], (360, 320))
            self.assertNotIn("parent", args)
            # 设置窗和主窗一样没有系统标题栏：系统 caption 会是深灰/黑色，和玻璃外观冲突。
            self.assertTrue(args["frameless"])
            self.assertFalse(args["focus"])
            self.assertFalse(args["easy_drag"])
            parent.resize.assert_not_called()
            apply_material.assert_called_once()
            show_no_activate.assert_called_once_with(child.native)
            child.show.assert_not_called()
            window._on_ui_later = lambda callback: callback()
            window.api.show_settings_window()
            child.show.assert_not_called()
            self.assertEqual(child.native.Opacity, window.window_opacity())
            self.assertTrue(child.focus)
            activate_window.assert_called_once_with(child.native)
            window.open_settings()
            child.show.assert_called_once()
            window.close_settings()
            child.destroy.assert_called_once()
            parent.destroy.assert_not_called()
            self.assertFalse(window.is_exiting)

    def test_keep_active_glass_hook_rewrites_inactive_ncactivate_and_reapplies_accent(self) -> None:
        from desktranslate import windows as winmod

        hwnd_value = 987654
        installed = []
        removed = []
        forwarded = []
        reapplied = []

        fake_set = Mock(side_effect=lambda hwnd, cb, sid, ref: installed.append((hwnd.value, cb, sid, ref)) or 1)
        fake_remove = Mock(side_effect=lambda hwnd, cb, sid: removed.append((hwnd, cb, sid)) or 1)
        fake_def = Mock(side_effect=lambda hwnd, msg, wparam, lparam: (forwarded.append((hwnd, msg, wparam, lparam)), 1)[1])

        policy = winmod._AccentPolicy()
        policy.AccentState = winmod._ACCENT_ENABLE_BLURBEHIND
        winmod._window_accent_policies[hwnd_value] = policy
        try:
            with patch("desktranslate.windows._comctl32_subclass_api", return_value=(fake_set, fake_remove, fake_def)), \
                 patch("desktranslate.windows._apply_accent_policy", side_effect=lambda h, p: reapplied.append((h, p.AccentState)) or True):
                winmod._install_keep_active_glass_hook(hwnd_value)
                self.assertEqual(len(installed), 1)
                self.assertEqual(installed[0][0], hwnd_value)
                self.assertEqual(installed[0][2], winmod._GLASS_SUBCLASS_ID)
                # 失焦时的 WM_NCACTIVATE(0) 改为 wParam=1 转发给默认过程，保持 DWM 玻璃不切非活动态。
                self.assertEqual(winmod._glass_subclass_proc(hwnd_value, winmod._WM_NCACTIVATE, 0, 77, winmod._GLASS_SUBCLASS_ID, 0), 1)
                self.assertEqual(forwarded[-1], (hwnd_value, winmod._WM_NCACTIVATE, 1, 77))
                # 获焦时的 WM_NCACTIVATE(1) 原样透传。
                self.assertEqual(winmod._glass_subclass_proc(hwnd_value, winmod._WM_NCACTIVATE, 1, 88, winmod._GLASS_SUBCLASS_ID, 0), 1)
                self.assertEqual(forwarded[-1], (hwnd_value, winmod._WM_NCACTIVATE, 1, 88))
                # WM_ACTIVATE 后重新下发缓存的玻璃策略。
                winmod._glass_subclass_proc(hwnd_value, winmod._WM_ACTIVATE, 0, 0, winmod._GLASS_SUBCLASS_ID, 0)
                self.assertEqual(reapplied, [(hwnd_value, winmod._ACCENT_ENABLE_BLURBEHIND)])
                # 窗口销毁时卸载子类化并清理策略缓存。
                winmod._glass_subclass_proc(hwnd_value, winmod._WM_NCDESTROY, 0, 0, winmod._GLASS_SUBCLASS_ID, 0)
                self.assertNotIn(hwnd_value, winmod._window_accent_policies)
                self.assertEqual(len(removed), 1)
                self.assertEqual(removed[0][0], hwnd_value)
                self.assertEqual(removed[0][2], winmod._GLASS_SUBCLASS_ID)
        finally:
            winmod._window_accent_policies.pop(hwnd_value, None)

    def test_apply_window_material_clears_system_backdrop_and_installs_hook(self) -> None:
        import ctypes
        from desktranslate import windows as winmod

        dwm_calls = []

        class FakeDwm:
            def DwmSetWindowAttribute(self, hwnd, attr, value_ptr, size):
                val = ctypes.cast(value_ptr, ctypes.POINTER(ctypes.c_int)).contents.value
                dwm_calls.append((attr, val))
                return 0

            def DwmExtendFrameIntoClientArea(self, hwnd, margins_ptr):
                return 0

        form = SimpleNamespace(
            Handle=SimpleNamespace(ToInt64=lambda: 456789),
            Opacity=1.0,
            BackColor=None,
            webview=SimpleNamespace(DefaultBackgroundColor=None),
        )
        fake_color = SimpleNamespace(FromArgb=lambda *args: args, Transparent="transparent")
        with patch.dict("sys.modules", {"System.Drawing": SimpleNamespace(Color=fake_color)}), \
             patch("desktranslate.windows.native_glass_supported", return_value=True), \
             patch("desktranslate.windows.ctypes.windll", SimpleNamespace(dwmapi=FakeDwm())), \
             patch("desktranslate.windows.set_window_accent", return_value=True), \
             patch("desktranslate.windows._install_keep_active_glass_hook") as install_hook:
            self.assertTrue(winmod.apply_window_material(form, accent_alpha=0x60, liquid=True, opacity=0.88))
            self.assertIn((38, 1), dwm_calls)
            install_hook.assert_called_once_with(456789)

    def test_show_window_no_activate_uses_sw_shownoactivate(self) -> None:
        from desktranslate.windows import _SW_SHOWNOACTIVATE, show_window_no_activate

        user32 = Mock()
        form = SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda: 321))
        with patch("desktranslate.windows.ctypes.windll", SimpleNamespace(user32=user32)):
            show_window_no_activate(form)
            user32.ShowWindow.assert_called_once()
            args = user32.ShowWindow.call_args.args
            self.assertEqual(args[0].value, 321)
            self.assertEqual(args[1], _SW_SHOWNOACTIVATE)

    @patch("desktranslate.ui.DesktopApi.get_settings", return_value={"translation_provider": "deepseek"})
    def test_saving_settings_notifies_main_window_and_keeps_settings_open(self, get_settings: Mock) -> None:
        host = SimpleNamespace(window=Mock(), close_settings=Mock())
        DesktopApi(host).settings_saved()
        host.window.evaluate_js.assert_called_once_with(
            'window.desktranslate.applySettings({"translation_provider": "deepseek"}, true)'
        )
        host.close_settings.assert_not_called()

    def test_appearance_preview_updates_windows_before_persisting(self) -> None:
        host = SimpleNamespace(settings=AppSettings(), save_settings=Mock(), refresh_glass=Mock(), window=Mock())
        api = DesktopApi(host)
        api._push_appearance = Mock()

        api.set_appearance_options(40, 80, False)
        self.assertEqual((host.settings.frosted_amount, host.settings.liquid_transparency), (40, 80))
        api._push_appearance.assert_called_once()
        host.refresh_glass.assert_called_once()
        host.save_settings.assert_not_called()

        api.set_appearance_options(40, 80, True)
        host.save_settings.assert_called_once()

    @patch("desktranslate.ui.DesktopApi.get_settings", return_value={})
    @patch("desktranslate.ui.apply_window_material")
    def test_preview_updates_both_windows_without_saving(self, apply_material: Mock, get_settings: Mock) -> None:
        child = SimpleNamespace(uid="settings-1", native=SimpleNamespace(IsDisposed=False))
        host = SimpleNamespace(settings=AppSettings(), _settings_window=child, window=Mock(), save_settings=Mock())
        host.glass_tint_alpha = lambda settings: TranslationWindow.glass_tint_alpha(host, settings)
        host.window_opacity = lambda settings: TranslationWindow.window_opacity(host, settings)
        host._on_ui = lambda callback: callback()
        # 真实宿主把材质应用排在桥调用之后（改窗口尺寸会堵桥），测试里同步执行即可。
        host._on_ui_later = host._on_ui
        api = DesktopApi(host)
        api.preview_appearance("settings-1", "transparent", 40, 80, 60, "#315b78")
        self.assertEqual(apply_material.call_count, 2)
        apply_material.assert_any_call(child.native, 0x2A + int(0.4 * 0x66), False, 0.73, True)
        self.assertEqual(host.settings.appearance, "frosted")
        host.window.evaluate_js.assert_called_once()
        host.save_settings.assert_not_called()
        api.preview_appearance("old-window", "liquid", 40, 80, 60, "#315b78")
        self.assertEqual(apply_material.call_count, 2)

    def test_appearance_color_is_validated_before_use(self) -> None:
        host = SimpleNamespace(settings=AppSettings(), save_settings=Mock(), refresh_glass=Mock(), window=Mock())
        api = DesktopApi(host)
        api._push_appearance = Mock()
        api.set_appearance_options(None, None, False, "#AABBCC")
        self.assertEqual(host.settings.liquid_color, "#aabbcc")
        with self.assertRaises(ValueError):
            api.set_appearance_options(None, None, False, "red; background: red")

    def test_transparent_mode_uses_its_own_opacity(self) -> None:
        host = SimpleNamespace(settings=AppSettings(appearance="transparent", transparent_transparency=80, liquid_transparency=20))
        self.assertAlmostEqual(TranslationWindow.window_opacity(host), 0.64)
        host.settings.appearance = "liquid"
        self.assertAlmostEqual(TranslationWindow.window_opacity(host), 0.91)
        host.settings.appearance = "frosted"
        self.assertEqual(TranslationWindow.window_opacity(host), 1.0)


if __name__ == "__main__":
    unittest.main()
