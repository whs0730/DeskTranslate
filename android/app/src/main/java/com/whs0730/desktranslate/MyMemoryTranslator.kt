package com.whs0730.desktranslate

import java.io.IOException
import java.net.ConnectException
import java.net.HttpURLConnection
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.URL
import java.net.URLEncoder
import java.net.UnknownHostException
import java.util.Locale
import org.json.JSONException
import org.json.JSONObject

/** Failure categories that the UI can turn into short Chinese messages. */
enum class TranslationFailureKind {
    NETWORK,
    TIMEOUT,
    SERVICE,
    EMPTY_RESULT,
    INVALID_RESPONSE,
    MISSING_API_KEY,
    AUTH_INVALID,
    INSUFFICIENT_BALANCE,
    RATE_LIMITED,
    INCOMPLETE_RESULT,
    UNKNOWN,
}

/** A translation failure that is safe to show as a user-facing message. */
class TranslationFailure(
    val kind: TranslationFailureKind,
    message: String,
    cause: Throwable? = null,
) : Exception(message, cause)

/**
 * Small synchronous client for the key-free MyMemory HTTPS endpoint.
 *
 * The request logic mirrors src/desktranslate/translator.py: trim on the caller
 * side, send `q` and `langpair` as UTF-8 query parameters, strip the provider's
 * `<g>` alignment markup, and decode HTML entities in the translated text.
 */
object MyMemoryTranslator {
    const val ENDPOINT = "https://api.mymemory.translated.net/get"
    const val CONNECT_TIMEOUT_MS = 8_000
    const val READ_TIMEOUT_MS = 10_000

    data class Result(
        val text: String,
        val direction: TranslationDirection,
        val sourceLanguage: String,
        val targetLanguage: String,
        val providerLabel: String = "MyMemory",
        val phonetic: String? = null,
        val entries: List<DictionaryEntry> = emptyList(),
        val alternatives: List<String> = emptyList(),
    )

    fun translate(text: String, direction: TranslationDirection): Result {
        val resolved = TranslationDirection.resolve(text, direction)
        val languagePair = requireNotNull(resolved.myMemoryPair) {
            "AUTO must be resolved before building a MyMemory request"
        }
        val languages = languagePair.split('|')
        val isWordQuery = extractEnglishWord(text) != null || isChineseWord(text)
        val rawPayload = request(buildRequestUrl(text, languagePair))
        val (translated, alternatives) = parseTranslatedResponse(
            rawPayload = rawPayload,
            sourceText = text,
            includeAlternatives = isWordQuery,
        )

        val (phonetic, entries) = when (resolved) {
            TranslationDirection.EN_TO_ZH -> {
                val details = extractEnglishWord(text)?.let { DictionaryLookup.lookupDetails(it) }
                details?.phonetic to details?.entries.orEmpty()
            }
            TranslationDirection.ZH_TO_EN -> {
                // For ZH -> EN, only attach pronunciation for the main English word; never pull
                // unrelated dictionary senses of that single translation.
                val ipa = extractEnglishWord(translated)?.let { DictionaryLookup.lookupPhonetic(it) }
                ipa to emptyList()
            }
            TranslationDirection.AUTO -> null to emptyList()
        }

        return Result(
            text = translated,
            direction = resolved,
            sourceLanguage = languages[0],
            targetLanguage = languages[1],
            phonetic = phonetic,
            entries = entries,
            alternatives = alternatives,
        )
    }

    /** Build the GET URL with both query values encoded as UTF-8. */
    fun buildRequestUrl(text: String, languagePair: String): String {
        val query = "q=" + URLEncoder.encode(text, "UTF-8") +
            "&langpair=" + URLEncoder.encode(languagePair, "UTF-8")
        return "$ENDPOINT?$query"
    }

    private fun request(url: String): String {
        var connection: HttpURLConnection? = null
        try {
            val http = (URL(url).openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = CONNECT_TIMEOUT_MS
                readTimeout = READ_TIMEOUT_MS
                instanceFollowRedirects = true
                setRequestProperty("Accept", "application/json")
                setRequestProperty("User-Agent", "DeskTranslate-Android/0.1")
            }
            connection = http

            val statusCode = http.responseCode
            val stream = if (statusCode in 200..299) {
                http.inputStream
            } else {
                http.errorStream
            }
            val body = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()

            if (statusCode !in 200..299) {
                throw TranslationFailure(
                    TranslationFailureKind.SERVICE,
                    "MyMemory returned HTTP $statusCode",
                )
            }
            if (body.isBlank()) {
                throw TranslationFailure(
                    TranslationFailureKind.INVALID_RESPONSE,
                    "MyMemory returned an empty response",
                )
            }
            return body
        } catch (error: TranslationFailure) {
            throw error
        } catch (error: SocketTimeoutException) {
            throw TranslationFailure(TranslationFailureKind.TIMEOUT, "MyMemory timed out", error)
        } catch (error: UnknownHostException) {
            throw TranslationFailure(TranslationFailureKind.NETWORK, "Host lookup failed", error)
        } catch (error: ConnectException) {
            throw TranslationFailure(TranslationFailureKind.NETWORK, "Connection failed", error)
        } catch (error: NoRouteToHostException) {
            throw TranslationFailure(TranslationFailureKind.NETWORK, "No route to host", error)
        } catch (error: IOException) {
            throw TranslationFailure(TranslationFailureKind.NETWORK, "Network I/O failed", error)
        } finally {
            connection?.disconnect()
        }
    }

    /**
     * Extract `responseData.translatedText`, rejecting provider errors and
     * untranslated echoes. Visible for unit tests.
     */
    internal fun parseTranslatedText(rawPayload: String, sourceText: String): String =
        parseTranslatedResponse(rawPayload, sourceText, includeAlternatives = false).first

    /** Parse primary translation plus optional deduplicated alternatives from `matches`. */
    internal fun parseTranslatedResponse(
        rawPayload: String,
        sourceText: String,
        includeAlternatives: Boolean,
    ): Pair<String, List<String>> {
        val payload = try {
            JSONObject(rawPayload.trimStart('\uFEFF'))
        } catch (error: JSONException) {
            throw TranslationFailure(
                TranslationFailureKind.INVALID_RESPONSE,
                "MyMemory returned malformed JSON",
                error,
            )
        }

        val responseStatus = payload.opt("responseStatus")?.toString()?.trim().orEmpty()
        if (responseStatus.isNotEmpty() &&
            responseStatus != "null" &&
            responseStatus != "200" &&
            responseStatus != "200.0"
        ) {
            throw TranslationFailure(
                TranslationFailureKind.SERVICE,
                "MyMemory rejected the request ($responseStatus)",
            )
        }

        val responseData = payload.optJSONObject("responseData")
            ?: throw TranslationFailure(
                TranslationFailureKind.INVALID_RESPONSE,
                "MyMemory response is missing responseData",
            )
        if (!responseData.has("translatedText")) {
            throw TranslationFailure(
                TranslationFailureKind.INVALID_RESPONSE,
                "MyMemory response is missing translatedText",
            )
        }

        val translated = decodeHtmlEntities(
            stripAlignmentMarkup(responseData.optString("translatedText", "")),
        ).trim()

        if (translated.isEmpty()) {
            throw TranslationFailure(
                TranslationFailureKind.EMPTY_RESULT,
                "MyMemory returned an empty translation",
            )
        }
        if (normalizeForComparison(translated) == normalizeForComparison(sourceText)) {
            throw TranslationFailure(
                TranslationFailureKind.EMPTY_RESULT,
                "MyMemory echoed the source text",
            )
        }
        val alternatives = if (includeAlternatives) {
            extractAlternatives(payload, sourceText, translated)
        } else {
            emptyList()
        }
        return translated to alternatives
    }

    internal fun extractAlternatives(
        payload: JSONObject,
        sourceText: String,
        primaryTranslation: String,
    ): List<String> {
        val matches = payload.optJSONArray("matches") ?: return emptyList()
        val sourceNorm = normalizeForComparison(sourceText)
        val seen = mutableSetOf(normalizeForComparison(primaryTranslation))
        if (sourceNorm.isNotEmpty()) seen.add(sourceNorm)

        val alternatives = mutableListOf<String>()
        for (i in 0 until matches.length()) {
            val match = matches.optJSONObject(i) ?: continue
            val segment = match.opt("segment") as? String
            if (segment != null && sourceNorm.isNotEmpty()) {
                val segNorm = normalizeForComparison(decodeHtmlEntities(stripAlignmentMarkup(segment)))
                if (segNorm.isNotEmpty() && segNorm != sourceNorm) continue
            }
            val rawAlt = match.opt("translation") as? String ?: continue
            val cleaned = decodeHtmlEntities(stripAlignmentMarkup(rawAlt)).trim()
            if (cleaned.isEmpty() || cleaned.length > 48 || cleaned.contains('\n')) continue
            val altNorm = normalizeForComparison(cleaned)
            if (altNorm.isEmpty() || !seen.add(altNorm)) continue
            alternatives.add(cleaned)
            if (alternatives.size >= MAX_ALTERNATIVES) break
        }
        return alternatives
    }

    // MyMemory wraps matched segments in <g id="..."> translation-memory tags.
    private val ALIGNMENT_MARKUP = Regex("</?g(?:\\s[^<>]*)?>", RegexOption.IGNORE_CASE)

    /** Remove MyMemory's `<g id="...">` alignment wrappers. */
    fun stripAlignmentMarkup(text: String): String = ALIGNMENT_MARKUP.replace(text, "")

    private val HTML_ENTITY = Regex("&(#[xX][0-9A-Fa-f]+|#[0-9]+|[A-Za-z][A-Za-z0-9]*);")

    // The provider normally returns numeric entities; these named ones cover
    // the punctuation and symbols seen in translations.
    private val NAMED_ENTITIES = mapOf(
        "amp" to "&",
        "apos" to "'",
        "gt" to ">",
        "lt" to "<",
        "quot" to "\"",
        "nbsp" to "\u00A0",
        "iexcl" to "¡",
        "cent" to "¢",
        "pound" to "£",
        "curren" to "¤",
        "yen" to "¥",
        "sect" to "§",
        "uml" to "¨",
        "copy" to "©",
        "ordf" to "ª",
        "laquo" to "«",
        "not" to "¬",
        "shy" to "\u00AD",
        "reg" to "®",
        "macr" to "¯",
        "deg" to "°",
        "plusmn" to "±",
        "sup2" to "²",
        "sup3" to "³",
        "acute" to "´",
        "micro" to "µ",
        "para" to "¶",
        "middot" to "·",
        "cedil" to "¸",
        "ordm" to "º",
        "raquo" to "»",
        "frac14" to "¼",
        "frac12" to "½",
        "frac34" to "¾",
        "iquest" to "¿",
        "ndash" to "–",
        "mdash" to "—",
        "lsquo" to "‘",
        "rsquo" to "’",
        "ldquo" to "“",
        "rdquo" to "”",
        "bull" to "•",
        "hellip" to "…",
        "permil" to "‰",
        "euro" to "€",
        "trade" to "™",
        "larr" to "←",
        "rarr" to "→",
        "times" to "×",
        "divide" to "÷",
    )

    /** Decode numeric and common named HTML entities without dropping markup. */
    fun decodeHtmlEntities(text: String): String {
        if (!text.contains('&')) return text
        return HTML_ENTITY.replace(text) { match ->
            val body = match.groupValues[1]
            val decoded = when {
                body.startsWith("#x", ignoreCase = true) ->
                    body.substring(2).toIntOrNull(16)?.let(::codePointToString)
                body.startsWith("#") ->
                    body.substring(1).toIntOrNull()?.let(::codePointToString)
                else -> NAMED_ENTITIES[body]
                    ?: NAMED_ENTITIES[body.lowercase(Locale.ROOT)]
            }
            decoded ?: match.value
        }
    }

    private fun codePointToString(codePoint: Int): String? {
        if (codePoint !in 0..0x10FFFF) return null
        if (codePoint in 0xD800..0xDFFF) return null
        return String(Character.toChars(codePoint))
    }

    private val WHITESPACE = Regex("\\s+")

    /** Normalize text for the "provider echoed the source" check. */
    fun normalizeForComparison(text: String): String =
        WHITESPACE.replace(text, " ")
            .trim()
            .trim { it in " .,!?:;，。！？：；" }
            .lowercase(Locale.ROOT)
}