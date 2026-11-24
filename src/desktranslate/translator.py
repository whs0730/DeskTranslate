"""Translation primitives for DeskTranslate.

The default provider is MyMemory's public HTTPS endpoint, so the desktop app
works without storing an API key.  MyMemory limits the UTF-8 size of the ``q``
parameter; this module therefore splits large inputs without cutting through a
Unicode code point and combines the translated pieces in request order.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import html
import json
import re
import socket
import time
from typing import Any
from urllib import error, parse, request

from .network import NetworkClient


MYMEMORY_ENDPOINT = "https://api.mymemory.translated.net/get"
MAX_QUERY_BYTES = 500
MAX_DICTIONARY_ENTRIES = 5
MAX_MEANINGS_PER_POS = 4
MAX_ALTERNATIVES = 5

# MyMemory wraps matched segments in ``<g id="...">`` tags (a translation-memory
# alignment artifact).  They are not part of the translation itself and would be
# shown as literal text because the UI renders results with textContent.
_MYMEMORY_ALIGNMENT_MARKUP = re.compile(r"</?g(?:\s[^<>]*)?>", re.IGNORECASE)
_ENGLISH_WORD_RE = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")
_EC_POS_PREFIX_RE = re.compile(r"^([A-Za-z]+\.(?:/[A-Za-z]+\.)*)\s+(.+)$")
_STRIP_PUNCT = " .,!?;:，。！？；：\"'()[]（）【】"

_POS_ABBREVIATIONS = {
    "noun": "n.",
    "verb": "v.",
    "adjective": "adj.",
    "adverb": "adv.",
    "preposition": "prep.",
    "conjunction": "conj.",
    "pronoun": "pron.",
    "interjection": "interj.",
    "determiner": "det.",
    "numeral": "num.",
    "auxiliary": "aux.",
    "article": "art.",
    "exclamation": "interj.",
    "int.": "interj.",
    "a.": "adj.",
    "ad.": "adv.",
}


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
class DictionaryEntry:
    """One part-of-speech group and its senses for a single-word lookup."""

    pos: str
    meanings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DictionaryDetails:
    """Optional phonetic and part-of-speech details returned by a dictionary."""

    phonetic: str | None = None
    entries: tuple[DictionaryEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class TranslationResult:
    """A completed translation and the provider metadata used for it."""

    text: str
    source_language: str
    target_language: str
    provider: str
    corrected_source: str | None = None
    phonetic: str | None = None
    phonetic_unavailable: bool = False
    entries: tuple[DictionaryEntry, ...] = ()
    alternatives: tuple[str, ...] = ()


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


def is_cjk_char(character: str) -> bool:
    code = ord(character)
    return any(start <= code <= end for start, end in _CJK_RANGES)


def contains_chinese(text: str) -> bool:
    """Return whether *text* contains a CJK ideograph used by Chinese text."""

    return any(is_cjk_char(character) for character in text)


def extract_english_word(text: str) -> str | None:
    """Return a clean single English word when *text* is a single-word query."""

    cleaned = text.strip().strip(_STRIP_PUNCT).strip()
    if not cleaned or len(cleaned) > 32:
        return None
    return cleaned if _ENGLISH_WORD_RE.fullmatch(cleaned) else None


def is_chinese_word(text: str) -> bool:
    """Return whether *text* is a short 1-4 character pure Chinese word/term."""

    cleaned = text.strip()
    if not 1 <= len(cleaned) <= 4:
        return False
    return all(is_cjk_char(character) for character in cleaned)


def abbreviate_pos(raw_pos: str) -> str:
    """Normalize common English part-of-speech labels into concise abbreviations."""

    cleaned = raw_pos.strip()
    if not cleaned:
        return ""
    return _POS_ABBREVIATIONS.get(cleaned.lower(), cleaned)


def format_phonetic(raw: str | None) -> str | None:
    """Normalize an IPA pronunciation string into ``/ipa/`` form."""

    if not isinstance(raw, str):
        return None
    ipa = raw.strip().strip("/[]").strip()
    return f"/{ipa}/" if ipa else None


def detect_direction(text: str) -> LanguageDirection:
    """Choose Chinese-to-English when Chinese text is present, else reverse."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if contains_chinese(text):
        return LanguageDirection.ZH_TO_EN
    return LanguageDirection.EN_TO_ZH


def _normalize_compare(value: str) -> str:
    return " ".join(value.casefold().split()).strip(" .,!?:;，。！？：；")


def _clean_mymemory_text(raw: str) -> str:
    return html.unescape(_MYMEMORY_ALIGNMENT_MARKUP.sub("", raw))


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

    def __init__(self, endpoint: str = MYMEMORY_ENDPOINT, *, network: NetworkClient | None = None) -> None:
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("endpoint must be a non-empty URL")
        self.endpoint = endpoint.rstrip("?")
        self.network = network or NetworkClient()

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
        is_word_query = len(chunks) == 1 and (
            extract_english_word(text) is not None or is_chinese_word(text)
        )
        translated_chunks: list[str] = []
        alternatives: tuple[str, ...] = ()
        for index, chunk in enumerate(chunks, start=1):
            try:
                translated, chunk_alts = self._translate_chunk_with_alternatives(
                    chunk,
                    source_language=source_language,
                    target_language=target_language,
                    timeout=timeout,
                    extract_alternatives=is_word_query,
                )
                translated_chunks.append(translated)
                if is_word_query:
                    alternatives = chunk_alts
            except TranslationError as exc:
                message = f"Translation chunk {index}/{len(chunks)} failed: {exc}"
                raise exc.__class__(message) from exc

        return TranslationResult(
            text="".join(translated_chunks),
            source_language=source_language,
            target_language=target_language,
            provider=self.provider_name,
            alternatives=alternatives,
        )

    def _translate_chunk(
        self,
        chunk: str,
        *,
        source_language: str,
        target_language: str,
        timeout: float,
    ) -> str:
        translated, _ = self._translate_chunk_with_alternatives(
            chunk,
            source_language=source_language,
            target_language=target_language,
            timeout=timeout,
            extract_alternatives=False,
        )
        return translated

    def _translate_chunk_with_alternatives(
        self,
        chunk: str,
        *,
        source_language: str,
        target_language: str,
        timeout: float,
        extract_alternatives: bool,
    ) -> tuple[str, tuple[str, ...]]:
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
            raw_payload = self.network.read(api_request, timeout)
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

        return self._parse_payload_with_alternatives(
            raw_payload,
            chunk,
            include_alternatives=extract_alternatives,
        )

    @staticmethod
    def _parse_payload(raw_payload: bytes) -> str:
        translated, _ = MyMemoryTranslator._parse_payload_with_alternatives(
            raw_payload,
            "",
            include_alternatives=False,
        )
        return translated

    @staticmethod
    def _parse_payload_with_alternatives(
        raw_payload: bytes,
        source_text: str,
        *,
        include_alternatives: bool,
    ) -> tuple[str, tuple[str, ...]]:
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

        # Strip the provider's segment wrappers before unescaping so that a
        # literal ``&lt;g&gt;`` typed by the user survives as text.
        translated_text = _clean_mymemory_text(translated_text)
        alternatives = (
            MyMemoryTranslator.extract_alternatives(payload, source_text, translated_text)
            if include_alternatives
            else ()
        )
        return translated_text, alternatives

    @staticmethod
    def extract_alternatives(
        payload: dict[str, Any],
        source_text: str,
        primary_translation: str,
    ) -> tuple[str, ...]:
        """Extract concise deduplicated alternative senses from MyMemory matches."""

        matches = payload.get("matches")
        if not isinstance(matches, list):
            return ()

        source_norm = _normalize_compare(source_text)
        seen = {_normalize_compare(primary_translation)}
        if source_norm:
            seen.add(source_norm)

        alternatives: list[str] = []
        for match in matches:
            if not isinstance(match, dict):
                continue
            segment = match.get("segment")
            if isinstance(segment, str) and source_norm:
                seg_norm = _normalize_compare(_clean_mymemory_text(segment))
                if seg_norm and seg_norm != source_norm:
                    continue
            raw_alt = match.get("translation")
            if not isinstance(raw_alt, str):
                continue
            cleaned = _clean_mymemory_text(raw_alt).strip()
            if not cleaned or len(cleaned) > 48 or "\n" in cleaned:
                continue
            alt_norm = _normalize_compare(cleaned)
            if not alt_norm or alt_norm in seen:
                continue
            seen.add(alt_norm)
            alternatives.append(cleaned)
            if len(alternatives) >= MAX_ALTERNATIVES:
                break
        return tuple(alternatives)


class LanguageToolCorrector:
    """Correct obvious English spelling mistakes through LanguageTool."""

    endpoint = "https://api.languagetool.org/v2/check"

    def __init__(self, *, network: NetworkClient | None = None) -> None:
        self.network = network or NetworkClient()

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
            payload = json.loads(self.network.read(api_request, timeout).decode("utf-8-sig"))
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


def _split_ec_senses(raw: str) -> list[str]:
    """Split bilingual dictionary senses on top-level semicolons (outside parentheses)."""

    senses: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in raw:
        if ch in "（(":
            depth += 1
            current.append(ch)
        elif ch in "）)":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch in "；;" and depth == 0:
            item = " ".join("".join(current).split()).strip()
            if item:
                senses.append(item)
            current = []
        else:
            current.append(ch)
    tail = " ".join("".join(current).split()).strip()
    if tail:
        senses.append(tail)
    return senses


class DictionaryPhonetics:
    """Look up optional IPA pronunciation and Chinese POS definitions without failing translation.

    Primary bilingual lookup uses Youdao's key-free EC JSON dictionary endpoint so
    EN -> ZH word queries receive concise Chinese polysemy grouped by part of speech.
    ``dictionaryapi.dev`` is retained strictly as an IPA phonetic fallback and never
    populates English-to-English definitions into ``entries``.
    """

    ec_endpoint = "https://dict.youdao.com/jsonapi"
    endpoint = "https://api.dictionaryapi.dev/api/v2/entries/en/"
    _ec_dicts_param = json.dumps({"count": 99, "dicts": [["ec", "simple"]]}, separators=(",", ":"))

    def __init__(self, *, network: NetworkClient | None = None) -> None:
        self.network = network or NetworkClient()

    def lookup(self, word: str, timeout: float = 3.0) -> str | None:
        """Look up IPA pronunciation from the fallback English phonetic endpoint."""

        api_request = request.Request(
            self.endpoint + parse.quote(word.strip().lower(), safe=""),
            headers={"Accept": "application/json", "User-Agent": "DeskTranslate/0.3"},
        )
        try:
            payload = json.loads(self.network.read(api_request, timeout).decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return self.parse_phonetic(payload)

    def lookup_details(self, word: str, timeout: float = 3.0) -> DictionaryDetails | None:
        """Look up IPA pronunciation and Chinese POS entries via Youdao EC, falling back for IPA only."""

        deadline = time.monotonic() + timeout
        details = self._lookup_ec_details(word, min(timeout, 2.5))
        if details is not None and details.phonetic:
            return details

        remaining = deadline - time.monotonic()
        if remaining > 0.25:
            fallback_phonetic = self.lookup(word, remaining)
            if fallback_phonetic:
                return DictionaryDetails(
                    phonetic=fallback_phonetic,
                    entries=details.entries if details else (),
                )
        return details

    def _lookup_ec_details(self, word: str, timeout: float) -> DictionaryDetails | None:
        query = parse.urlencode(
            {"q": word.strip().lower(), "dicts": self._ec_dicts_param},
            encoding="utf-8",
        )
        api_request = request.Request(
            f"{self.ec_endpoint}?{query}",
            headers={"Accept": "application/json", "User-Agent": "DeskTranslate/0.3"},
        )
        try:
            payload = json.loads(self.network.read(api_request, timeout).decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return self.parse_ec_details(payload)

    @staticmethod
    def parse_phonetic(payload: Any) -> str | None:
        """Extract IPA pronunciation only from ``dictionaryapi.dev`` payloads."""

        if not isinstance(payload, list):
            return None
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            candidates = [entry.get("phonetic")]
            phonetics = entry.get("phonetics")
            if isinstance(phonetics, list):
                candidates.extend(
                    item.get("text") for item in phonetics if isinstance(item, dict)
                )
            for candidate in candidates:
                formatted = format_phonetic(candidate)
                if formatted:
                    return formatted
        return None

    @staticmethod
    def parse_ec_details(payload: Any) -> DictionaryDetails | None:
        """Parse Youdao EC JSON into IPA pronunciation and concise Chinese POS entries."""

        if not isinstance(payload, dict):
            return None

        phonetic: str | None = None
        grouped: dict[str, list[str]] = {}
        seen_meanings: dict[str, set[str]] = {}

        def _extract_word_phonetic(words: Any) -> str | None:
            if not isinstance(words, list):
                return None
            for item in words:
                if not isinstance(item, dict):
                    continue
                for key in ("usphone", "ukphone"):
                    formatted = format_phonetic(item.get(key))
                    if formatted:
                        return formatted
            return None

        ec = payload.get("ec")
        ec_words = ec.get("word") if isinstance(ec, dict) else None
        phonetic = _extract_word_phonetic(ec_words)

        if phonetic is None:
            simple = payload.get("simple")
            simple_words = simple.get("word") if isinstance(simple, dict) else None
            phonetic = _extract_word_phonetic(simple_words)

        if isinstance(ec_words, list):
            for word_obj in ec_words:
                if not isinstance(word_obj, dict):
                    continue
                trs = word_obj.get("trs")
                if not isinstance(trs, list):
                    continue
                for tr_wrap in trs:
                    if not isinstance(tr_wrap, dict):
                        continue
                    tr_list = tr_wrap.get("tr")
                    if not isinstance(tr_list, list):
                        continue
                    for tr_item in tr_list:
                        if not isinstance(tr_item, dict):
                            continue
                        lang_obj = tr_item.get("l")
                        if not isinstance(lang_obj, dict):
                            continue
                        raw_items = lang_obj.get("i")
                        if isinstance(raw_items, str):
                            lines = [raw_items]
                        elif isinstance(raw_items, list):
                            lines = [s for s in raw_items if isinstance(s, str)]
                        else:
                            continue
                        for line in lines:
                            cleaned_line = " ".join(line.split()).strip()
                            if not cleaned_line:
                                continue
                            match = _EC_POS_PREFIX_RE.match(cleaned_line)
                            if not match:
                                continue
                            pos = abbreviate_pos(match.group(1))
                            if not pos:
                                continue
                            if pos not in grouped and len(grouped) >= MAX_DICTIONARY_ENTRIES:
                                continue
                            senses = _split_ec_senses(match.group(2))
                            bucket = grouped.setdefault(pos, [])
                            seen = seen_meanings.setdefault(pos, set())
                            for sense in senses:
                                if len(bucket) >= MAX_MEANINGS_PER_POS:
                                    break
                                if not contains_chinese(sense):
                                    continue
                                key = sense.casefold()
                                if key in seen:
                                    continue
                                seen.add(key)
                                bucket.append(sense)

        entries = tuple(
            DictionaryEntry(pos=pos, meanings=tuple(items))
            for pos, items in grouped.items()
            if items
        )
        if phonetic is None and not entries:
            return None
        return DictionaryDetails(phonetic=phonetic, entries=entries)

    @staticmethod
    def parse_details(payload: Any) -> DictionaryDetails | None:
        """Parse Youdao EC dict payloads (or phonetic-only fallback list payloads)."""

        if isinstance(payload, dict):
            return DictionaryPhonetics.parse_ec_details(payload)
        phonetic = DictionaryPhonetics.parse_phonetic(payload)
        return DictionaryDetails(phonetic=phonetic) if phonetic else None


def _query_dictionary(
    phonetics: DictionaryPhonetics,
    word: str,
    timeout: float,
) -> DictionaryDetails | None:
    """Query details while staying compatible with unit-test mocks that only mock ``lookup``."""

    if isinstance(phonetics, DictionaryPhonetics):
        return phonetics.lookup_details(word, timeout)
    details_fn = getattr(phonetics, "lookup_details", None)
    if callable(details_fn):
        candidate = details_fn(word, timeout)
        if candidate is None or isinstance(candidate, DictionaryDetails):
            return candidate
    phonetic = phonetics.lookup(word, timeout)
    if isinstance(phonetic, str) and phonetic.strip():
        return DictionaryDetails(phonetic=phonetic)
    return None


def add_optional_phonetic(
    result: TranslationResult,
    phonetics: DictionaryPhonetics,
    timeout: float,
    *,
    lookup_word: str | None = None,
    mark_unavailable: bool = True,
    include_entries: bool = True,
) -> TranslationResult:
    word = lookup_word if lookup_word is not None else extract_english_word(result.text)
    if not word:
        return result
    if result.phonetic and (not include_entries or result.entries):
        return result
    details = _query_dictionary(phonetics, word, min(timeout, 3.0))
    next_phonetic = result.phonetic or (details.phonetic if details else None)
    next_entries = (
        result.entries or (details.entries if details else ())
        if include_entries
        else result.entries
    )
    unavailable = (not bool(next_phonetic)) if mark_unavailable else result.phonetic_unavailable
    return replace(
        result,
        phonetic=next_phonetic,
        entries=next_entries,
        phonetic_unavailable=unavailable,
    )


class SmartTranslator:
    """Translate normally, correcting misspelled English when needed."""

    def __init__(
        self,
        primary: MyMemoryTranslator | None = None,
        corrector: LanguageToolCorrector | None = None,
        phonetics: DictionaryPhonetics | None = None,
        *,
        network: NetworkClient | None = None,
    ) -> None:
        self.network = network or NetworkClient()
        self.primary = primary or MyMemoryTranslator(network=self.network)
        self.corrector = corrector or LanguageToolCorrector(network=self.network)
        self.phonetics = phonetics or DictionaryPhonetics(network=self.network)

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
            if selected is LanguageDirection.ZH_TO_EN and contains_chinese(text):
                # 中译英只补音标，不把单个英文词的无关英英释义塞进结果里。
                return add_optional_phonetic(result, self.phonetics, timeout, include_entries=False)
            if selected is LanguageDirection.EN_TO_ZH:
                source_word = extract_english_word(text)
                if source_word:
                    return add_optional_phonetic(
                        result,
                        self.phonetics,
                        timeout,
                        lookup_word=source_word,
                        mark_unavailable=False,
                    )
            return result

        if selected is LanguageDirection.EN_TO_ZH:
            corrected = self.corrector.correct(text, min(timeout, 6.0))
            if corrected:
                retried = self.primary.translate(corrected, selected, timeout)
                if self._is_translated(corrected, retried.text):
                    enriched = TranslationResult(
                        text=retried.text,
                        source_language=retried.source_language,
                        target_language=retried.target_language,
                        provider="MyMemory + LanguageTool",
                        corrected_source=corrected,
                        alternatives=retried.alternatives,
                    )
                    corrected_word = extract_english_word(corrected)
                    if corrected_word:
                        return add_optional_phonetic(
                            enriched,
                            self.phonetics,
                            timeout,
                            lookup_word=corrected_word,
                            mark_unavailable=False,
                        )
                    return enriched
                raise TranslationNoResultError(
                    f"未找到有效译文；已尝试按 {corrected!r} 纠正拼写"
                )
            raise TranslationNoResultError("未找到有效译文，请检查英文拼写")
        raise TranslationNoResultError("在线服务未返回有效译文，请换一种表达后重试")

    @staticmethod
    def _is_translated(source: str, translated: str) -> bool:
        return bool(translated.strip()) and _normalize_compare(source) != _normalize_compare(translated)


__all__ = [
    "DictionaryDetails",
    "DictionaryEntry",
    "DictionaryPhonetics",
    "LanguageDirection",
    "LanguageToolCorrector",
    "MAX_QUERY_BYTES",
    "MYMEMORY_ENDPOINT",
    "MyMemoryTranslator",
    "SmartTranslator",
    "TranslationError",
    "TranslationHTTPError",
    "TranslationNetworkError",
    "TranslationNoResultError",
    "TranslationResponseError",
    "TranslationResult",
    "TranslationTimeoutError",
    "abbreviate_pos",
    "add_optional_phonetic",
    "contains_chinese",
    "detect_direction",
    "extract_english_word",
    "format_phonetic",
    "is_chinese_word",
]
