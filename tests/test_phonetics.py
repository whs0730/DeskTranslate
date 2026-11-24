import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib import error

from desktranslate.config import AppSettings

from desktranslate.translator import (
    DictionaryDetails,
    DictionaryEntry,
    DictionaryPhonetics,
    LanguageDirection,
    MyMemoryTranslator,
    SmartTranslator,
    TranslationResult,
)
from desktranslate.ui import DesktopApi
from tests.test_translator import FakeResponse


class DictionaryPhoneticsTests(unittest.TestCase):
    @patch("desktranslate.network.request.OpenerDirector.open")
    def test_reads_pronunciation_and_limits_timeout(self, urlopen: Mock) -> None:
        urlopen.return_value = FakeResponse(
            json.dumps([{"word": "apple", "phonetic": "/ˈæpəl/"}]).encode()
        )
        self.assertEqual(DictionaryPhonetics().lookup("Apple", 2.0), "/ˈæpəl/")
        api_request = urlopen.call_args.args[0]
        self.assertEqual(api_request.full_url, DictionaryPhonetics.endpoint + "apple")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 1.0)

    @patch("desktranslate.network.request.OpenerDirector.open")
    def test_falls_back_to_phonetics_text(self, urlopen: Mock) -> None:
        urlopen.return_value = FakeResponse(json.dumps([
            {"phonetic": "", "phonetics": [{"audio": "audio.mp3"}, {"text": "ˈæpəl"}]}
        ]).encode())
        self.assertEqual(DictionaryPhonetics().lookup("apple"), "/ˈæpəl/")

    @patch("desktranslate.network.request.OpenerDirector.open")
    def test_missing_or_malformed_pronunciation_is_optional(self, urlopen: Mock) -> None:
        for payload in (
            b"not-json", b"\xff", b"[]", b'{}',
            b'[{"phonetics": null}]',
            b'[null, {"phonetic": 42, "phonetics": [null, {"text": " "}]}]',
            b'[{"phonetic": "//", "phonetics": {"text": "bad"}}]',
        ):
            with self.subTest(payload=payload):
                urlopen.return_value = FakeResponse(payload)
                self.assertIsNone(DictionaryPhonetics().lookup("apple"))

    @patch("desktranslate.network.request.OpenerDirector.open")
    def test_network_errors_do_not_raise(self, urlopen: Mock) -> None:
        for failure in (TimeoutError(), error.URLError("offline"), OSError("offline")):
            with self.subTest(failure=type(failure).__name__):
                urlopen.side_effect = failure
                self.assertIsNone(DictionaryPhonetics().lookup("apple"))

    @patch("desktranslate.network.request.OpenerDirector.open")
    def test_dictionary_not_found_does_not_raise(self, urlopen: Mock) -> None:
        urlopen.side_effect = error.HTTPError("https://example.com", 404, "Not Found", {}, None)
        self.assertIsNone(DictionaryPhonetics().lookup("unknownword"))


class PhoneticTranslationTests(unittest.TestCase):
    def test_chinese_word_gets_phonetics_in_auto_and_manual_modes(self) -> None:
        for direction in (LanguageDirection.AUTO, LanguageDirection.ZH_TO_EN):
            with self.subTest(direction=direction):
                primary = Mock()
                primary.translate.return_value = TranslationResult("Apple.", "zh-CN", "en", "MyMemory")
                phonetics = Mock()
                phonetics.lookup.return_value = "/ˈæpəl/"
                result = SmartTranslator(primary, Mock(), phonetics).translate("苹果", direction, 2.0)
                self.assertEqual(result.text, "Apple.")
                self.assertEqual(result.phonetic, "/ˈæpəl/")
                self.assertFalse(result.phonetic_unavailable)
                phonetics.lookup.assert_called_once_with("Apple", 2.0)

    def test_phrases_sentences_and_non_words_skip_lookup(self) -> None:
        for translated in ("an apple", "I like apples.", "apple/pear", "苹果", "123", "apple\npear"):
            with self.subTest(translated=translated):
                primary = Mock()
                original = TranslationResult(translated, "zh-CN", "en", "MyMemory")
                primary.translate.return_value = original
                phonetics = Mock()
                result = SmartTranslator(primary, Mock(), phonetics).translate("水果")
                self.assertIs(result, original)
                phonetics.lookup.assert_not_called()

    def test_chinese_word_gets_only_phonetics_not_unrelated_english_definitions(self) -> None:
        primary = Mock()
        primary.translate.return_value = TranslationResult(
            "run", "zh-CN", "en", "MyMemory", alternatives=("operate", "execute")
        )
        phonetics = Mock()
        phonetics.lookup_details.return_value = DictionaryDetails(
            phonetic="/rʌn/",
            entries=(DictionaryEntry("v.", ("To move swiftly on foot.",)),),
        )
        result = SmartTranslator(primary, Mock(), phonetics).translate("运行", LanguageDirection.ZH_TO_EN)
        self.assertEqual(result.text, "run")
        self.assertEqual(result.phonetic, "/rʌn/")
        self.assertEqual(result.entries, ())
        self.assertEqual(result.alternatives, ("operate", "execute"))

    def test_english_word_to_chinese_enriches_dictionary_details(self) -> None:
        primary = Mock()
        primary.translate.return_value = TranslationResult(
            "苹果", "en", "zh-CN", "MyMemory", alternatives=("苹果公司",)
        )
        phonetics = Mock()
        phonetics.lookup_details.return_value = DictionaryDetails(
            phonetic="/ˈæpəl/",
            entries=(DictionaryEntry("n.", ("A round fruit.",)),),
        )
        result = SmartTranslator(primary, Mock(), phonetics).translate("apple")
        self.assertEqual(result.text, "苹果")
        self.assertEqual(result.phonetic, "/ˈæpəl/")
        self.assertEqual(result.entries[0].pos, "n.")
        self.assertEqual(result.alternatives, ("苹果公司",))
        phonetics.lookup_details.assert_called_once_with("apple", 3.0)

    def test_english_sentence_to_chinese_skips_lookup(self) -> None:
        primary = Mock()
        primary.translate.return_value = TranslationResult("我喜欢苹果。", "en", "zh-CN", "MyMemory")
        phonetics = Mock()
        SmartTranslator(primary, Mock(), phonetics).translate("I like apples.")
        phonetics.lookup_details.assert_not_called()
        phonetics.lookup.assert_not_called()

    @patch("desktranslate.network.request.OpenerDirector.open")
    def test_mymemory_extracts_deduplicated_alternatives_for_word(self, urlopen: Mock) -> None:
        payload = {
            "responseStatus": 200,
            "responseData": {"translatedText": "运行"},
            "matches": [
                {"segment": "run", "translation": "运行"},
                {"segment": "run", "translation": "跑"},
                {"segment": "run", "translation": "经营"},
                {"segment": "run", "translation": "run"},
                {"segment": "other phrase", "translation": "无关短语"},
            ],
        }
        urlopen.return_value = FakeResponse(json.dumps(payload).encode())
        result = MyMemoryTranslator().translate("run", LanguageDirection.EN_TO_ZH)
        self.assertEqual(result.text, "运行")
        self.assertEqual(result.alternatives, ("跑", "经营"))

    @patch("desktranslate.network.request.OpenerDirector.open")
    def test_lookup_details_extracts_youdao_ec_phonetic_and_chinese_entries(self, urlopen: Mock) -> None:
        payload = {
            "ec": {
                "word": [
                    {
                        "usphone": "ɡʊd",
                        "ukphone": "ɡʊd",
                        "trs": [
                            {"tr": [{"l": {"i": ["adj. 优良的；能干的，擅长的；令人愉快的；合适的；有益的"]}}]},
                            {"tr": [{"l": {"i": ["n. 善，正义；好事；（用于回应；确认）好处"]}}]},
                            {"tr": [{"l": {"i": ["int. 哇，太好了"]}}]},
                            {"tr": [{"l": {"i": ["【名】 （Good）（英）古德（人名）"]}}]},
                        ],
                    }
                ]
            }
        }
        urlopen.return_value = FakeResponse(json.dumps(payload).encode())
        details = DictionaryPhonetics().lookup_details("good")
        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(details.phonetic, "/ɡʊd/")
        self.assertEqual([e.pos for e in details.entries], ["adj.", "n.", "interj."])
        self.assertEqual(
            details.entries[0].meanings,
            ("优良的", "能干的，擅长的", "令人愉快的", "合适的"),
        )
        self.assertEqual(
            details.entries[1].meanings,
            ("善，正义", "好事", "（用于回应；确认）好处"),
        )
        self.assertEqual(details.entries[2].meanings, ("哇，太好了",))
        self.assertEqual(urlopen.call_count, 1)
        self.assertTrue(urlopen.call_args.args[0].full_url.startswith(DictionaryPhonetics.ec_endpoint))

    @patch("desktranslate.network.request.OpenerDirector.open")
    def test_lookup_details_filters_non_chinese_and_falls_back_to_phonetic_only(self, urlopen: Mock) -> None:
        youdao_no_phonetic = {
            "ec": {
                "word": [
                    {
                        "trs": [
                            {"tr": [{"l": {"i": ["vi. 合作；配合；English only definition"]}}]},
                        ]
                    }
                ]
            }
        }
        fallback_phonetic = [
            {
                "word": "co-operate",
                "phonetic": "/kəʊˈɒpəreɪt/",
                "meanings": [
                    {"partOfSpeech": "verb", "definitions": [{"definition": "To work together."}]}
                ],
            }
        ]
        urlopen.side_effect = [
            FakeResponse(json.dumps(youdao_no_phonetic).encode()),
            FakeResponse(json.dumps(fallback_phonetic).encode()),
        ]
        details = DictionaryPhonetics().lookup_details("co-operate")
        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(details.phonetic, "/kəʊˈɒpəreɪt/")
        self.assertEqual(len(details.entries), 1)
        self.assertEqual(details.entries[0].pos, "vi.")
        self.assertEqual(details.entries[0].meanings, ("合作", "配合"))

    @patch("desktranslate.network.request.OpenerDirector.open")
    def test_lookup_details_never_exposes_english_definitions_when_ec_fails(self, urlopen: Mock) -> None:
        fallback_payload = [
            {
                "word": "run",
                "phonetic": "/rʌn/",
                "meanings": [
                    {"partOfSpeech": "verb", "definitions": [{"definition": "To move swiftly on foot."}]}
                ],
            }
        ]
        urlopen.side_effect = [
            error.HTTPError("https://dict.youdao.com", 503, "Unavailable", {}, None),
            FakeResponse(json.dumps(fallback_payload).encode()),
        ]
        details = DictionaryPhonetics().lookup_details("run")
        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(details.phonetic, "/rʌn/")
        self.assertEqual(details.entries, ())

    def test_missing_phonetics_preserves_translation(self) -> None:
        primary = Mock()
        original = TranslationResult("apple", "zh-CN", "en", "MyMemory")
        primary.translate.return_value = original
        phonetics = Mock()
        phonetics.lookup.return_value = None
        result = SmartTranslator(primary, Mock(), phonetics).translate("苹果")
        self.assertEqual(result.text, original.text)
        self.assertTrue(result.phonetic_unavailable)
        phonetics.lookup.assert_called_once_with("apple", 3.0)

    def test_dictionary_timeout_does_not_lose_translation(self) -> None:
        primary = Mock()
        primary.translate.return_value = TranslationResult("apple", "zh-CN", "en", "MyMemory")
        with patch("desktranslate.network.request.OpenerDirector.open", side_effect=TimeoutError()):
            result = SmartTranslator(primary, Mock()).translate("苹果")
        self.assertEqual(result.text, "apple")
        self.assertIsNone(result.phonetic)
        self.assertTrue(result.phonetic_unavailable)


class PhoneticWindowTests(unittest.TestCase):
    def test_failed_phonetics_explains_missing_pronunciation(self) -> None:
        translator = Mock()
        translator.translate.return_value = TranslationResult(
            "Apple", "zh-CN", "en", "MyMemory", phonetic_unavailable=True
        )
        response = DesktopApi(SimpleNamespace(translator=translator, settings=AppSettings())).translate("苹果", "auto")
        self.assertTrue(response["phonetic_unavailable"])
        self.assertEqual(response["text"], "Apple")
        self.assertTrue(response["ok"])

    def test_displays_phonetics_and_copies_only_translation(self) -> None:
        translator = Mock()
        translator.translate.return_value = TranslationResult("apple", "zh-CN", "en", "MyMemory", phonetic="/ˈæpəl/")
        response = DesktopApi(SimpleNamespace(translator=translator, settings=AppSettings())).translate("苹果", "auto")
        self.assertEqual(response["text"], "apple")
        self.assertEqual(response["phonetic"], "/ˈæpəl/")


if __name__ == "__main__":
    unittest.main()
