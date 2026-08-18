import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from desktranslate.config import AppSettings, SettingsStore
from desktranslate.ui import ShortcutDialog, TranslationWindow


class WindowBehaviorTests(unittest.TestCase):
    def test_shortcut_corner_resize_changes_both_axes(self) -> None:
        fake = SimpleNamespace(
            _corner_resize_pointer_x=100,
            _corner_resize_pointer_y=100,
            _corner_resize_origin_x=50,
            _corner_resize_origin_y=60,
            _corner_resize_width=340,
            _corner_resize_height=180,
            _corner_resize_edges="nw",
            minsize=Mock(return_value=(340, 180)),
            geometry=Mock(),
        )

        ShortcutDialog._resize_from_corner(
            fake, SimpleNamespace(x_root=60, y_root=40)
        )

        fake.geometry.assert_called_once_with("380x240+10+0")

    def test_enter_translates_and_ctrl_enter_inserts_newline(self) -> None:
        fake = SimpleNamespace(translate=Mock(), input_text=Mock())

        self.assertEqual(TranslationWindow._translate_from_enter(fake, None), "break")
        fake.translate.assert_called_once_with()

        self.assertEqual(TranslationWindow._insert_newline(fake, None), "break")
        fake.input_text.insert.assert_called_once_with("insert", "\n")

    def test_resize_respects_minimum_and_can_grow(self) -> None:
        fake = SimpleNamespace(
            _resize_pointer_x=100,
            _resize_pointer_y=100,
            _resize_origin_x=50,
            _resize_origin_y=60,
            _resize_width=480,
            _resize_height=320,
            _resize_edges="se",
            geometry=Mock(),
        )
        TranslationWindow._resize_window(fake, SimpleNamespace(x_root=20, y_root=20))
        fake.geometry.assert_called_with("400x260+50+60")

        TranslationWindow._resize_window(fake, SimpleNamespace(x_root=220, y_root=180))
        fake.geometry.assert_called_with("600x400+50+60")

    def test_resize_from_top_left_keeps_opposite_corner_fixed(self) -> None:
        fake = SimpleNamespace(
            _resize_pointer_x=100,
            _resize_pointer_y=100,
            _resize_origin_x=50,
            _resize_origin_y=60,
            _resize_width=480,
            _resize_height=320,
            _resize_edges="nw",
            geometry=Mock(),
        )

        TranslationWindow._resize_window(fake, SimpleNamespace(x_root=300, y_root=300))

        fake.geometry.assert_called_once_with("400x260+130+120")

    def test_resize_from_left_edge_only_changes_horizontal_geometry(self) -> None:
        fake = SimpleNamespace(
            _resize_pointer_x=100,
            _resize_pointer_y=100,
            _resize_origin_x=50,
            _resize_origin_y=60,
            _resize_width=480,
            _resize_height=320,
            _resize_edges="w",
            geometry=Mock(),
        )

        TranslationWindow._resize_window(fake, SimpleNamespace(x_root=0, y_root=250))

        fake.geometry.assert_called_once_with("580x320-50+60")

    def test_geometry_formatter_supports_negative_screen_coordinates(self) -> None:
        self.assertEqual(
            TranslationWindow._format_geometry(580, 320, -50, -25),
            "580x320-50-25",
        )

    def test_window_size_round_trips_through_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            expected = AppSettings(window_width=720, window_height=480)
            store.save(expected)
            self.assertEqual(store.load(), expected)


if __name__ == "__main__":
    unittest.main()
