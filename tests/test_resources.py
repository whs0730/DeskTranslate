import unittest

from desktranslate.resources import app_icon_path


class ResourceTests(unittest.TestCase):
    def test_application_icon_is_available(self) -> None:
        path = app_icon_path()
        self.assertIsNotNone(path)
        self.assertEqual(path.name, "DeskTranslate.ico")
        self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()

