import unittest

from PIL import Image

from desktranslate.resources import app_icon_path
from desktranslate.tray import create_tray_icon


class IconUsageTests(unittest.TestCase):
    def test_tray_icon_uses_shared_application_icon(self) -> None:
        icon_path = app_icon_path()
        self.assertIsNotNone(icon_path)
        with Image.open(icon_path) as source:
            expected = source.convert("RGBA").resize(
                (32, 32),
                Image.Resampling.LANCZOS,
            )
        actual = create_tray_icon(32)
        self.assertEqual(actual.mode, "RGBA")
        self.assertEqual(actual.size, (32, 32))
        self.assertEqual(actual.tobytes(), expected.tobytes())


if __name__ == "__main__":
    unittest.main()

