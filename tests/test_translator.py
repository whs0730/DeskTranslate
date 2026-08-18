from __future__ import annotations

import json
import socket
import unittest
from unittest.mock import patch
from urllib import error, parse

from desktranslate.translator import (
    LanguageDirection,
    MAX_QUERY_BYTES,
    MyMemoryTranslator,
    TranslationHTTPError,
    TranslationResponseError,
    TranslationTimeoutError,
    _split_utf8_chunks,
    detect_direction,
)


class FakeResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return self._payload

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def response_payload(translated_text: str) -> bytes:
    return json.dumps(
        {
            "responseData": {"translatedText": translated_text},
            "responseStatus": 200,
        },
        ensure_ascii=False,
    ).encode("utf-8")


class DirectionDetectionTests(unittest.TestCase):
    def test_detects_english_and_chinese(self) -> None:
        self.assertIs(
            detect_direction("A quick desktop lookup"),
            LanguageDirection.EN_TO_ZH,
        )
        self.assertIs(
            detect_direction("快速 lookup"),
            LanguageDirection.ZH_TO_EN,
        )
        self.assertIs(
            detect_direction("扩展区字符：𠀀"),
            LanguageDirection.ZH_TO_EN,
        )


class Utf8ChunkingTests(unittest.TestCase):
    def test_chunks_are_lossless_and_never_exceed_utf8_limit(self) -> None:
        source = "中" * 180 + " English words " * 30 + "终"

        chunks = _split_utf8_chunks(source)

        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), source)
        self.assertTrue(
            all(len(chunk.encode("utf-8")) <= MAX_QUERY_BYTES for chunk in chunks)
        )

    def test_multibyte_character_is_not_cut(self) -> None:
        chunks = _split_utf8_chunks("甲乙丙丁", max_bytes=7)

        self.assertEqual(chunks, ["甲乙", "丙丁"])
        self.assertTrue(all(len(chunk.encode("utf-8")) <= 7 for chunk in chunks))


class MyMemoryTranslatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.translator = MyMemoryTranslator()

    @patch("desktranslate.translator.request.urlopen")
    def test_successfully_parses_translation_and_request_direction(
        self, mock_urlopen
    ) -> None:
        mock_urlopen.return_value = FakeResponse(
            response_payload("你好 &amp; 欢迎")
        )

        result = self.translator.translate("hello and welcome", timeout=3.5)

        self.assertEqual(result.text, "你好 & 欢迎")
        self.assertEqual(result.source_language, "en")
        self.assertEqual(result.target_language, "zh-CN")
        self.assertEqual(result.provider, "MyMemory")
        api_request = mock_urlopen.call_args.args[0]
        query = parse.parse_qs(parse.urlsplit(api_request.full_url).query)
        self.assertEqual(query["q"], ["hello and welcome"])
        self.assertEqual(query["langpair"], ["en|zh-CN"])
        self.assertEqual(mock_urlopen.call_args.kwargs["timeout"], 3.5)
        self.assertTrue(api_request.full_url.startswith("https://"))

    @patch("desktranslate.translator.request.urlopen")
    def test_explicit_direction_overrides_detection(self, mock_urlopen) -> None:
        mock_urlopen.return_value = FakeResponse(response_payload("forced"))

        result = self.translator.translate(
            "这段文字包含中文",
            direction=LanguageDirection.EN_TO_ZH,
        )

        self.assertEqual(result.source_language, "en")
        self.assertEqual(result.target_language, "zh-CN")
        api_request = mock_urlopen.call_args.args[0]
        query = parse.parse_qs(parse.urlsplit(api_request.full_url).query)
        self.assertEqual(query["langpair"], ["en|zh-CN"])

    @patch("desktranslate.translator.request.urlopen")
    def test_each_request_stays_under_limit_and_results_keep_order(
        self, mock_urlopen
    ) -> None:
        source = "中英混合 text " * 90
        requested_chunks: list[str] = []

        def echo_chunk(api_request, *, timeout: float) -> FakeResponse:
            query = parse.parse_qs(parse.urlsplit(api_request.full_url).query)
            chunk = query["q"][0]
            requested_chunks.append(chunk)
            self.assertLessEqual(len(chunk.encode("utf-8")), MAX_QUERY_BYTES)
            self.assertEqual(timeout, 10.0)
            return FakeResponse(response_payload(chunk))

        mock_urlopen.side_effect = echo_chunk

        result = self.translator.translate(source)

        self.assertGreater(len(requested_chunks), 1)
        self.assertEqual("".join(requested_chunks), source)
        self.assertEqual(result.text, source)
        self.assertEqual(mock_urlopen.call_count, len(requested_chunks))

    @patch("desktranslate.translator.request.urlopen")
    def test_malformed_json_raises_clear_response_error(
        self, mock_urlopen
    ) -> None:
        mock_urlopen.return_value = FakeResponse(b"not-json")

        with self.assertRaisesRegex(
            TranslationResponseError, "malformed JSON"
        ):
            self.translator.translate("hello")

    @patch("desktranslate.translator.request.urlopen")
    def test_missing_translation_raises_clear_response_error(
        self, mock_urlopen
    ) -> None:
        mock_urlopen.return_value = FakeResponse(
            json.dumps({"responseData": {}, "responseStatus": 200}).encode()
        )

        with self.assertRaisesRegex(
            TranslationResponseError, "missing translatedText"
        ):
            self.translator.translate("hello")

    @patch("desktranslate.translator.request.urlopen")
    def test_http_failure_uses_custom_exception(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = error.HTTPError(
            "https://api.mymemory.translated.net/get",
            429,
            "Too Many Requests",
            hdrs=None,
            fp=None,
        )

        with self.assertRaisesRegex(TranslationHTTPError, "HTTP status 429"):
            self.translator.translate("hello")

    @patch("desktranslate.translator.request.urlopen")
    def test_timeout_uses_custom_exception(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = socket.timeout("timed out")

        with self.assertRaisesRegex(
            TranslationTimeoutError, "within 2 seconds"
        ):
            self.translator.translate("hello", timeout=2)

    @patch("desktranslate.translator.request.urlopen")
    def test_whitespace_does_not_make_a_network_request(
        self, mock_urlopen
    ) -> None:
        result = self.translator.translate(" \n\t")

        self.assertEqual(result.text, " \n\t")
        mock_urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
