import unittest

from desktranslate.hotkey import HotkeyError, normalize_hotkey


class HotkeyParsingTests(unittest.TestCase):
    def test_normalizes_modifier_order_and_function_keys(self) -> None:
        self.assertEqual(normalize_hotkey("alt + ctrl + t")[0], "Ctrl+Alt+T")
        self.assertEqual(normalize_hotkey("shift+ctrl+f12")[0], "Ctrl+Shift+F12")

    def test_rejects_unmodified_or_unsupported_keys(self) -> None:
        with self.assertRaises(HotkeyError):
            normalize_hotkey("T")
        with self.assertRaises(HotkeyError):
            normalize_hotkey("Ctrl+Space")


if __name__ == "__main__":
    unittest.main()

