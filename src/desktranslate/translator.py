"""Translation primitives for DeskTranslate.

The default provider is MyMemory's public HTTPS endpoint, so the desktop app
works without storing an API key.  MyMemory limits the UTF-8 size of the ``q``
parameter; this module therefore splits large inputs without cutting through a
Unicode code point and combines the translated pieces in request order.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import html
import json
import socket
from typing import Any
from urllib import error, parse, request


MYMEMORY_ENDPOINT = "https://api.mymemory.translated.net/get"
MAX_QUERY_BYTES = 500


class TranslationError(RuntimeError):
    """Base class for failures that can be shown safely in the UI."""


class TranslationNetworkError(TranslationError):
    """The translation provider could not be reached."""


class TranslationTimeoutError(TranslationNetworkError):
    """The translation request exceeded its timeout."""


class TranslationHTTPError(TranslationNetworkError):
    """The translation provider returned an unsuccessful HTTP response."""


class TranslationResponseError(TranslationError):
    """The translation provider returned invalid or rejected data."""


class TranslationNoResultError(TranslationError):
    """The provider returned the source text instead of a translation."""


class LanguageDirection(str, Enum):
    """Supported translation directions."""

    AUTO = "auto"
    EN_TO_ZH = "en_to_zh"
    ZH_TO_EN = "zh_to_en"

    @property
    def languages(self) -> tuple[str, str]:
        """Return the MyMemory source and target language codes."""

        if self is LanguageDirection.EN_TO_ZH:
            return "en", "zh-CN"
        if self is LanguageDirection.ZH_TO_EN:
            return "zh-CN", "en"
        raise ValueError("AUTO does not have a fixed language pair")


@dataclass(frozen=True, slots=True)
class TranslationResult:
    """A completed translation and the provider metadata used for it."""

    text: str
    source_language: str
    target_language: str
    provider: str
    corrected_source: str | None = None


_CJK_RANGES = (
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0x20000, 0x2EBEF),  # CJK Unified Ideographs Extensions B-F and I
    (0x30000, 0x323AF),  # CJK Unified Ideographs Extensions G-H
)

# Prefer a nearby natural boundary when possible.  The boundary character is
# kept in the outgoing chunk, so joining chunks never drops source text.
_BREAK_CHARACTERS = frozenset(".,!?;:\n\r\t ，。！？；：、")


def contains_chinese(text: str) -> bool:
    """Return whether *text* contains a CJK ideograph used by Chinese text."""

    return any(
        start <= ord(character) <= end
        for character in text
        for start, end in _CJK_RANGES
    )


def detect_direction(text: str) -> LanguageDirection:
    """Choose Chinese-to-English when Chinese text is present, else reverse."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if contains_chinese(text):
        return LanguageDirection.ZH_TO_EN
    return LanguageDirection.EN_TO_ZH


def _split_utf8_chunks(text: str, max_bytes: int = MAX_QUERY_BYTES) -> list[str]:
    """Split *text* into lossless chunks no larger than *max_bytes* in UTF-8.

    Python strings are traversed by Unicode code point, so a multibyte UTF-8
    sequence is never split.  A natural boundary is preferred when one occurs
    in the latter half of the current chunk; otherwise the largest safe chunk
    is used.
    """

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be greater than zero")
    if not text:
        return []

    chunks: list[str] = []
    start = 0

    while start < len(text):
        byte_count = 0
        cursor = start
        natural_boundary: int | None = None

        while cursor < len(text):
            character_size = len(text[cursor].encode("utf-8"))
            if character_size > max_bytes and cursor == start:
                raise ValueError(
                    "max_bytes is too small to hold one UTF-8 character"
                )
            if byte_count + character_size > max_bytes:
                break

            byte_count += character_size
            cursor += 1
            if text[cursor - 1].isspace() or text[cursor - 1] in _BREAK_CHARACTERS:
                natural_boundary = cursor

        if cursor == len(text):
            end = cursor
        elif (
            natural_boundary is not None
            and natural_boundary > start
            and len(text[start:natural_boundary].encode("utf-8"))
            >= max_bytes // 2
        ):
            end = natural_boundary
        else:
            end = cursor

        # ``cursor == start`` is possible only for the oversized-code-point
        # case handled above, but this guard also prevents future regressions
        # from creating an infinite loop.
        if end <= start:
            raise ValueError("unable to split text within the UTF-8 byte limit")

        chunks.append(text[start:end])
        start = end

    return chunks


def _coerce_direction(direction: LanguageDirection | str) -> LanguageDirection:
    if isinstance(direction, LanguageDirection):
        return direction
    if isinstance(direction, str):
        normalized = direction.strip().lower().replace("-", "_")
        aliases = {
            "en|zh_cn": LanguageDirection.EN_TO_ZH,
            "en|zh": LanguageDirection.EN_TO_ZH,
            "zh_cn|en": LanguageDirection.ZH_TO_EN,
            "zh|en": LanguageDirection.ZH_TO_EN,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            return LanguageDirection(normalized)
        except ValueError:
            pass
    raise ValueError(f"unsupported language direction: {direction!r}")


class MyMemoryTranslator:
    """Small synchronous client for MyMemory's key-free translation API."""

    provider_name = "MyMemory"

    def __init__(self, endpoint: str = MYMEMORY_ENDPOINT) -> None:
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("endpoint must be a non-empty URL")
        self.endpoint = endpoint.rstrip("?")

    def translate(
        self,
        text: str,
        direction: LanguageDirection | str = LanguageDirection.AUTO,
        timeout: float = 10.0,
    ) -> TranslationResult:
        """Translate text, automatically choosing direction unless overridden."""

        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        selected_direction = _coerce_direction(direction)
        if selected_direction is LanguageDirection.AUTO:
            selected_direction = detect_direction(text)
        source_language, target_language = selected_direction.languages

        # Whitespace-only inputs need no remote request and should not be
        # unexpectedly trimmed by the provider.
        if not text or not text.strip():
            return TranslationResult(
                text=text,
                source_language=source_language,
                target_language=target_language,
                provider=self.provider_name,
            )

        chunks = _split_utf8_chunks(text)
        translated_chunks: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            try:
                translated_chunks.append(
                    self._translate_chunk(
                        chunk,
                        source_language=source_language,
                        target_language=target_language,
                        timeout=timeout,
                    )
                )
            except TranslationError as exc:
                message = f"Translation chunk {index}/{len(chunks)} failed: {exc}"
                raise exc.__class__(message) from exc

        return TranslationResult(
            text="".join(translated_chunks),
            source_language=source_language,
            target_language=target_language,
            provider=self.provider_name,
        )

    def _translate_chunk(
        self,
        chunk: str,
        *,
        source_language: str,
        target_language: str,
        timeout: float,
    ) -> str:
        if len(chunk.encode("utf-8")) > MAX_QUERY_BYTES:
            raise ValueError("translation chunk exceeds MyMemory's q byte limit")

        query = parse.urlencode(
            {
                "q": chunk,
                "langpair": f"{source_language}|{target_language}",
            },
            encoding="utf-8",
        )
        separator = "&" if "?" in self.endpoint else "?"
        url = f"{self.endpoint}{separator}{query}"
        api_request = request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "DeskTranslate/1.0",
            },
            method="GET",
        )

        try:
            with request.urlopen(api_request, timeout=timeout) as response:
                status = getattr(response, "status", None)
                if status is None and hasattr(response, "getcode"):
                    status = response.getcode()
                if status is not None and not 200 <= int(status) < 300:
                    raise TranslationHTTPError(
                        f"MyMemory returned HTTP status {status}"
                    )
                raw_payload = response.read()
        except error.HTTPError as exc:
            reason = f" ({exc.reason})" if exc.reason else ""
            raise TranslationHTTPError(
                f"MyMemory returned HTTP status {exc.code}{reason}"
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise TranslationTimeoutError(
                f"MyMemory did not respond within {timeout:g} seconds"
            ) from exc
        except error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise TranslationTimeoutError(
                    f"MyMemory did not respond within {timeout:g} seconds"
                ) from exc
            raise TranslationNetworkError(
                f"Could not connect to MyMemory: {exc.reason}"
            ) from exc
        except OSError as exc:
            raise TranslationNetworkError(
                f"Could not communicate with MyMemory: {exc}"
            ) from exc

        return self._parse_payload(raw_payload)

    @staticmethod
    def _parse_payload(raw_payload: bytes) -> str:
        if not raw_payload:
            raise TranslationResponseError("MyMemory returned an empty response")

        try:
            payload: Any = json.loads(raw_payload.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TranslationResponseError(
                "MyMemory returned malformed JSON"
            ) from exc

        if not isinstance(payload, dict):
            raise TranslationResponseError(
                "MyMemory response must be a JSON object"
            )

        response_status = payload.get("responseStatus")
        if response_status not in (None, "", 200, "200"):
            details = payload.get("responseDetails") or "no details provided"
            raise TranslationResponseError(
                f"MyMemory rejected the request ({response_status}): {details}"
            )

        response_data = payload.get("responseData")
        if not isinstance(response_data, dict):
            raise TranslationResponseError(
                "MyMemory response is missing responseData"
            )

        translated_text = response_data.get("translatedText")
        if not isinstance(translated_text, str):
            raise TranslationResponseError(
                "MyMemory response is missing translatedText"
            )

        return html.unescape(translated_text)


class LanguageToolCorrector:
    """Correct obvious English spelling mistakes through LanguageTool."""

    endpoint = "https://api.languagetool.org/v2/check"

    def correct(self, text: str, timeout: float = 6.0) -> str | None:
        if not text.strip() or len(text) > 500:
            return None
        body = parse.urlencode({"text": text, "language": "en-US"}).encode("utf-8")
        api_request = request.Request(
            self.endpoint,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "DeskTranslate/0.2",
            },
            method="POST",
        )
        try:
            with request.urlopen(api_request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None

        corrections: list[tuple[int, int, str]] = []
        for match in payload.get("matches", []):
            if not isinstance(match, dict):
                continue
            rule = match.get("rule")
            replacements = match.get("replacements")
            if not isinstance(rule, dict) or rule.get("issueType") != "misspelling":
                continue
            if not isinstance(replacements, list) or not replacements:
                continue
            first = replacements[0]
            if not isinstance(first, dict) or not isinstance(first.get("value"), str):
                continue
            offset = match.get("offset")
            length = match.get("length")
            if isinstance(offset, int) and isinstance(length, int):
                corrections.append((offset, length, first["value"]))

        corrected = text
        for offset, length, replacement in sorted(corrections, reverse=True):
            corrected = corrected[:offset] + replacement + corrected[offset + length :]
        return corrected if corrected != text else None


class SmartTranslator:
    """Translate normally, correcting misspelled English when needed."""

    def __init__(
        self,
        primary: MyMemoryTranslator | None = None,
        corrector: LanguageToolCorrector | None = None,
    ) -> None:
        self.primary = primary or MyMemoryTranslator()
        self.corrector = corrector or LanguageToolCorrector()

    def translate(
        self,
        text: str,
        direction: LanguageDirection | str = LanguageDirection.AUTO,
        timeout: float = 10.0,
    ) -> TranslationResult:
        selected = _coerce_direction(direction)
        if selected is LanguageDirection.AUTO:
            selected = detect_direction(text)
        result = self.primary.translate(text, selected, timeout)
        if self._is_translated(text, result.text):
            return result

        if selected is LanguageDirection.EN_TO_ZH:
            corrected = self.corrector.correct(text, min(timeout, 6.0))
            if corrected:
                retried = self.primary.translate(corrected, selected, timeout)
                if self._is_translated(corrected, retried.text):
                    return TranslationResult(
                        text=retried.text,
                        source_language=retried.source_language,
                        target_language=retried.target_language,
                        provider="MyMemory + LanguageTool",
                        corrected_source=corrected,
                    )
                raise TranslationNoResultError(
                    f"未找到有效译文；已尝试按 {corrected!r} 纠正拼写"
                )
            raise TranslationNoResultError("未找到有效译文，请检查英文拼写")
        raise TranslationNoResultError("在线服务未返回有效译文，请换一种表达后重试")

    @staticmethod
    def _is_translated(source: str, translated: str) -> bool:
        def normalize(value: str) -> str:
            return " ".join(value.casefold().split()).strip(
                " .,!?:;，。！？：；"
            )

        return bool(translated.strip()) and normalize(source) != normalize(translated)


__all__ = [
    "LanguageDirection",
    "MAX_QUERY_BYTES",
    "MYMEMORY_ENDPOINT",
    "MyMemoryTranslator",
    "TranslationError",
    "TranslationHTTPError",
    "TranslationNetworkError",
    "TranslationNoResultError",
    "TranslationResponseError",
    "TranslationResult",
    "TranslationTimeoutError",
    "contains_chinese",
    "detect_direction",
    "LanguageToolCorrector",
    "SmartTranslator",
]
