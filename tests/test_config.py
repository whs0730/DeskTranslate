import tempfile
import unittest
from pathlib import Path

from desktranslate.config import AppSettings, SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            expected = AppSettings(window_x=120, window_y=240, direction="zh_to_en", network_mode="system")
            store.save(expected)
            self.assertEqual(store.load(), expected)

    def test_close_action_round_trip_and_invalid_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            store.save(AppSettings(close_action="exit"))
            self.assertEqual(store.load().close_action, "exit")
            path.write_text('{"close_action": "quit"}', encoding="utf-8")
            self.assertEqual(store.load().close_action, "tray")

    def test_appearance_round_trip_and_invalid_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            store.save(AppSettings(appearance="liquid"))
            self.assertEqual(store.load().appearance, "liquid")
            store.save(AppSettings(appearance="transparent", transparent_transparency=75))
            self.assertEqual((store.load().appearance, store.load().transparent_transparency), ("transparent", 75))
            path.write_text('{"appearance": "plasma"}', encoding="utf-8")
            self.assertEqual(store.load().appearance, "frosted")

    def test_appearance_tuning_round_trip_and_clamping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            store.save(AppSettings(frosted_amount=95, liquid_transparency=15, liquid_color="#315b78"))
            loaded = store.load()
            self.assertEqual((loaded.frosted_amount, loaded.liquid_transparency), (95, 15))
            self.assertEqual(loaded.liquid_color, "#315b78")
            store.save(AppSettings(liquid_color="#475658"))
            self.assertEqual(store.load().liquid_color, "#475658")
            path.write_text(
                '{"frosted_amount": 240, "liquid_transparency": -30}', encoding="utf-8"
            )
            loaded = store.load()
            self.assertEqual((loaded.frosted_amount, loaded.liquid_transparency), (100, 0))
            path.write_text(
                '{"frosted_amount": "high", "liquid_transparency": true}', encoding="utf-8"
            )
            loaded = store.load()
            self.assertEqual((loaded.frosted_amount, loaded.liquid_transparency), (60, 40))

    def test_invalid_data_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("not-json", encoding="utf-8")
            self.assertEqual(SettingsStore(path).load(), AppSettings())


if __name__ == "__main__":
    unittest.main()

