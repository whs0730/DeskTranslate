import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib import error

from desktranslate.config import AppSettings, SettingsStore
from desktranslate.deepseek import DeepSeekTranslator
from desktranslate.translator import LanguageDirection, TranslationHTTPError, TranslationResponseError
from desktranslate.ui import DesktopApi


def response(text="你好", finish="stop"):
    return json.dumps({"choices": [{"finish_reason": finish, "message": {"content": text}}]}).encode()


class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        self.network = Mock()
        self.network.read.return_value = response()
        self.key = Mock(return_value="test-secret")
        self.translator = DeepSeekTranslator(self.key, self.network)

    def test_uses_official_endpoint_bearer_auth_and_translation_only_messages(self):
        result = self.translator.translate("hello")
        outgoing = self.network.read.call_args.args[0]
        payload = json.loads(outgoing.data)
        self.assertEqual(outgoing.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(outgoing.get_header("Authorization"), "Bearer test-secret")
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["messages"][1], {"role": "user", "content": "hello"})
        self.assertIn("简体中文", payload["messages"][0]["content"])
        self.assertEqual(result.text, "你好")
        self.assertFalse(self.network.read.call_args.kwargs["allow_retry"])

    def test_explicit_direction_and_model_are_used(self):
        self.translator.model = "deepseek-v4-pro"
        self.network.read.return_value = response("Good morning.")
        result = self.translator.translate("早上好", LanguageDirection.ZH_TO_EN)
        self.assertEqual(result.target_language, "en")
        payload = json.loads(self.network.read.call_args.args[0].data)
        self.assertEqual(payload["model"], "deepseek-v4-pro")
        self.assertIn("英文", payload["messages"][0]["content"])

    def test_word_lookup_parses_structured_dictionary_json(self):
        dict_json = json.dumps(
            {
                "translation": "运行",
                "phonetic": "rʌn",
                "entries": [
                    {"pos": "verb", "meanings": ["跑；奔跑", "运行；运转", "经营"]},
                    {"pos": "noun", "meanings": ["跑步", "连续演出"]},
                ],
            },
            ensure_ascii=False,
        )
        self.network.read.return_value = response(dict_json)
        result = self.translator.translate("run", LanguageDirection.EN_TO_ZH)
        payload = json.loads(self.network.read.call_args.args[0].data)
        self.assertIn("JSON", payload["messages"][0]["content"])
        self.assertEqual(result.text, "运行")
        self.assertEqual(result.phonetic, "/rʌn/")
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].pos, "v.")
        self.assertEqual(result.entries[0].meanings, ("跑；奔跑", "运行；运转", "经营"))
        self.assertEqual(result.entries[1].pos, "n.")

    def test_sentence_keeps_plain_translation_prompt_without_json(self):
        self.network.read.return_value = response("我每天早上跑步。")
        result = self.translator.translate("I run every morning.", LanguageDirection.EN_TO_ZH)
        payload = json.loads(self.network.read.call_args.args[0].data)
        self.assertNotIn("JSON", payload["messages"][0]["content"])
        self.assertEqual(result.text, "我每天早上跑步。")
        self.assertEqual(result.entries, ())

    def test_chinese_word_lookup_requests_multiple_english_equivalents(self):
        dict_json = json.dumps(
            {
                "translation": "run",
                "phonetic": "/rʌn/",
                "entries": [
                    {"pos": "v.", "meanings": ["run（程序/机器运行）", "operate（运作）", "execute（执行）"]},
                    {"pos": "n.", "meanings": ["operation（运作）", "running（运转）"]},
                ],
            },
            ensure_ascii=False,
        )
        self.network.read.return_value = response(dict_json)
        result = self.translator.translate("运行", LanguageDirection.ZH_TO_EN)
        prompt = json.loads(self.network.read.call_args.args[0].data)["messages"][0]["content"]
        self.assertIn("多个表达该中文意思的常用英文单词或英文短语", prompt)
        self.assertIn("不要反过来去解释某一个英文词的其他无关含义", prompt)
        self.assertEqual(result.text, "run")
        self.assertEqual(result.phonetic, "/rʌn/")
        self.assertEqual(result.entries[0].meanings[0], "run（程序/机器运行）")

    def test_missing_key_never_makes_a_request(self):
        self.key.return_value = None
        with self.assertRaisesRegex(TranslationResponseError, "API Key"):
            self.translator.translate("hello")
        self.network.read.assert_not_called()

    def test_partial_empty_and_malformed_outputs_are_rejected(self):
        for raw in (b"not-json", b"[]", b"{}", b'{"choices":[null]}', response(""), response("partial", "length")):
            with self.subTest(raw=raw):
                self.network.read.return_value = raw
                with self.assertRaises(TranslationResponseError):
                    self.translator.translate("hello")

    def test_authentication_failure_is_clear_and_does_not_include_key(self):
        failure = error.HTTPError(self.translator.endpoint, 401, "Unauthorized", {}, io.BytesIO())
        self.network.read.side_effect = failure
        try:
            with self.assertRaisesRegex(TranslationHTTPError, "API Key 无效") as caught:
                self.translator.translate("hello")
            self.assertNotIn("test-secret", str(caught.exception))
        finally:
            failure.close()

    def test_keys_are_stored_separately_and_not_returned_to_frontend(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            host = SimpleNamespace(
                settings=AppSettings(), credentials=Mock(), deepseek=Mock(),
                direction=LanguageDirection.AUTO, hotkey_error=None, native_glass=False,
            )
            host.credentials.read.return_value = "saved-secret"
            host.save_settings = lambda: store.save(host.settings)
            api = DesktopApi(host)
            api.set_translation_settings("deepseek", "deepseek-flash", "new-secret", False)
            self.assertEqual(host.credentials.save.call_args.args[0], "new-secret")
            self.assertEqual(store.load().translation_provider, "deepseek")
            self.assertNotIn("secret", store.path.read_text(encoding="utf-8"))
            settings = api.get_settings()
            self.assertTrue(settings["has_api_key"])
            self.assertNotIn("secret", json.dumps(settings))
            self.assertEqual(settings["api_configs"][0]["key_preview"], "sav…cret")

    def test_invalid_key_and_missing_key_fail_before_saving_configuration(self):
        host = SimpleNamespace(credentials=Mock(), settings=AppSettings(), save_settings=Mock())
        host.credentials.read.return_value = None
        api = DesktopApi(host)
        for key in ("", "key\nheader", "a" * 513):
            with self.subTest(key_length=len(key)):
                with self.assertRaises(ValueError):
                    api.set_translation_settings("deepseek", "deepseek-flash", key, False)
        host.save_settings.assert_not_called()
        host.credentials.save.assert_not_called()
