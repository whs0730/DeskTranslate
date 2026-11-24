import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktranslate.app import _write_crash_log


class StartupLogTests(unittest.TestCase):
    def test_unwritable_home_uses_the_portable_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            portable = Path(directory)
            with patch("desktranslate.app.Path.home", return_value=portable / "missing-home"), patch("desktranslate.app.Path.cwd", return_value=portable):
                try:
                    raise RuntimeError("startup diagnosis")
                except RuntimeError:
                    path = _write_crash_log()
            self.assertEqual(path, portable / "DeskTranslate-crash.log")
            self.assertIn("RuntimeError: startup diagnosis", path.read_text(encoding="utf-8"))

    def test_does_not_report_a_log_when_both_locations_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("desktranslate.app.Path.home", return_value=root / "missing-home"), patch("desktranslate.app.Path.cwd", return_value=root / "missing-app"):
                self.assertIsNone(_write_crash_log())


if __name__ == "__main__":
    unittest.main()
