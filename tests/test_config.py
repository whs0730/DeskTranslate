import tempfile
import unittest
from pathlib import Path

from desktranslate.config import AppSettings, SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            expected = AppSettings(window_x=120, window_y=240, direction="zh_to_en")
            store.save(expected)
            self.assertEqual(store.load(), expected)

    def test_invalid_data_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("not-json", encoding="utf-8")
            self.assertEqual(SettingsStore(path).load(), AppSettings())


if __name__ == "__main__":
    unittest.main()

