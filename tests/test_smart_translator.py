import json
import unittest
from unittest.mock import Mock, patch

from desktranslate.translator import (
    LanguageDirection,
    LanguageToolCorrector,
    SmartTranslator,
    TranslationNoResultError,
    TranslationResult,
)
from tests.test_translator import FakeResponse


class LanguageToolCorrectorTests(unittest.TestCase):
    @patch("desktranslate.translator.request.urlopen")
    def test_applies_spelling_replacements_from_right_to_left(self, urlopen: Mock) -> None:
        payload = {
            "matches": [
                {
                    "offset": 0,
                    "length": 7,
                    "replacements": [{"value": "dynamic"}],
                    "rule": {"issueType": "misspelling"},
                }
            ]
        }
        urlopen.return_value = FakeResponse(json.dumps(payload).encode("utf-8"))
        self.assertEqual(LanguageToolCorrector().correct("danamic"), "dynamic")


class SmartTranslatorTests(unittest.TestCase):
    def test_retries_unchanged_english_after_spelling_correction(self) -> None:
        primary = Mock()
        primary.translate.side_effect = [
            TranslationResult("danamic", "en", "zh-CN", "MyMemory"),
            TranslationResult("动态", "en", "zh-CN", "MyMemory"),
        ]
        corrector = Mock()
        corrector.correct.return_value = "dynamic"

        result = SmartTranslator(primary, corrector).translate("danamic")

        self.assertEqual(result.text, "动态")
        self.assertEqual(result.corrected_source, "dynamic")
        self.assertEqual(primary.translate.call_count, 2)

    def test_does_not_correct_a_successful_translation(self) -> None:
        primary = Mock()
        primary.translate.return_value = TranslationResult(
            "桌面翻译", "en", "zh-CN", "MyMemory"
        )
        corrector = Mock()

        result = SmartTranslator(primary, corrector).translate("desktop translation")

        self.assertEqual(result.text, "桌面翻译")
        corrector.correct.assert_not_called()

    def test_unchanged_text_without_suggestion_is_an_error(self) -> None:
        primary = Mock()
        primary.translate.return_value = TranslationResult(
            "unknownword", "en", "zh-CN", "MyMemory"
        )
        corrector = Mock()
        corrector.correct.return_value = None

        with self.assertRaises(TranslationNoResultError):
            SmartTranslator(primary, corrector).translate(
                "unknownword", LanguageDirection.EN_TO_ZH
            )


if __name__ == "__main__":
    unittest.main()

