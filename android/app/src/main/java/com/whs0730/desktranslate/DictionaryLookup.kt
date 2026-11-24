package com.whs0730.desktranslate

import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.Locale
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

/** One part-of-speech group and its concise senses for a word lookup. */
data class DictionaryEntry(
    val pos: String,
    val meanings: List<String>,
)

/** Optional pronunciation and POS definitions returned by dictionary lookup. */
data class DictionaryDetails(
    val phonetic: String? = null,
    val entries: List<DictionaryEntry> = emptyList(),
)

const val MAX_DICTIONARY_ENTRIES = 5
const val MAX_MEANINGS_PER_POS = 4
const val MAX_ALTERNATIVES = 5

private val ENGLISH_WORD_REGEX = Regex("^[A-Za-z]+(?:['’-][A-Za-z]+)*$")
private val EC_POS_PREFIX_REGEX = Regex("^([A-Za-z]+\\.(?:/[A-Za-z]+\\.)*)\\s+(.+)$")
private const val STRIP_PUNCT = " .,!?;:，。！？；：\"'()[]（）【】"

private val POS_ABBREVIATIONS = mapOf(
    "noun" to "n.",
    "verb" to "v.",
    "adjective" to "adj.",
    "adverb" to "adv.",
    "preposition" to "prep.",
    "conjunction" to "conj.",
    "pronoun" to "pron.",
    "interjection" to "interj.",
    "determiner" to "det.",
    "numeral" to "num.",
    "auxiliary" to "aux.",
    "article" to "art.",
    "exclamation" to "interj.",
    "int." to "interj.",
    "a." to "adj.",
    "ad." to "adv.",
)

/** Extract a clean single English word when [text] is a single-word query. */
fun extractEnglishWord(text: String): String? {
    val cleaned = text.trim().trim { it in STRIP_PUNCT }.trim()
    if (cleaned.isEmpty() || cleaned.length > 32) return null
    return if (ENGLISH_WORD_REGEX.matches(cleaned)) cleaned else null
}

/** Return whether [text] is a 1–4 character pure Chinese word/term. */
fun isChineseWord(text: String): Boolean {
    val cleaned = text.trim()
    if (cleaned.isEmpty()) return false
    var count = 0
    var index = 0
    while (index < cleaned.length) {
        val cp = cleaned.codePointAt(index)
        if (!containsChinese(String(Character.toChars(cp)))) return false
        count += 1
        if (count > 4) return false
        index += Character.charCount(cp)
    }
    return count in 1..4
}

/** Normalize common English POS labels to concise abbreviations (`n.`, `v.`, etc.). */
fun abbreviatePartOfSpeech(rawPos: String): String {
    val cleaned = rawPos.trim()
    if (cleaned.isEmpty()) return ""
    return POS_ABBREVIATIONS[cleaned.lowercase(Locale.ROOT)] ?: cleaned
}

/** Format raw IPA pronunciation into `/ipa/` form. */
fun formatPhonetic(raw: String?): String? {
    if (raw == null) return null
    val ipa = raw.trim().trim('/', '[', ']').trim()
    return if (ipa.isEmpty()) null else "/$ipa/"
}

/** Split bilingual senses on top-level semicolons (ignoring semicolons inside parentheses). */
internal fun splitEcSenses(raw: String): List<String> {
    val senses = mutableListOf<String>()
    val current = StringBuilder()
    var depth = 0
    for (ch in raw) {
        when (ch) {
            '（', '(' -> {
                depth += 1
                current.append(ch)
            }
            '）', ')' -> {
                if (depth > 0) depth -= 1
                current.append(ch)
            }
            '；', ';' -> {
                if (depth == 0) {
                    val item = current.toString().trim().replace(Regex("\\s+"), " ")
                    if (item.isNotEmpty()) senses.add(item)
                    current.setLength(0)
                } else {
                    current.append(ch)
                }
            }
            else -> current.append(ch)
        }
    }
    val tail = current.toString().trim().replace(Regex("\\s+"), " ")
    if (tail.isNotEmpty()) senses.add(tail)
    return senses
}

/**
 * Best-effort free English-Chinese dictionary lookup via Youdao's public EC JSON endpoint,
 * with `dictionaryapi.dev` kept strictly as an IPA phonetic fallback (never English definitions).
 * Failures, timeouts, or 404s silently return `null` so primary translation never fails.
 */
object DictionaryLookup {
    const val EC_ENDPOINT = "https://dict.youdao.com/jsonapi"
    const val ENDPOINT = "https://api.dictionaryapi.dev/api/v2/entries/en/"
    private const val EC_DICTS_PARAM = """{"count":99,"dicts":[["ec","simple"]]}"""
    private const val CONNECT_TIMEOUT_MS = 2_500
    private const val READ_TIMEOUT_MS = 2_500

    fun lookupDetails(word: String): DictionaryDetails? {
        val cleanWord = extractEnglishWord(word) ?: return null
        val details = lookupEcDetails(cleanWord)
        if (details?.phonetic != null) return details

        val fallbackPhonetic = lookupPhoneticFallback(cleanWord)
        return when {
            fallbackPhonetic != null -> DictionaryDetails(
                phonetic = fallbackPhonetic,
                entries = details?.entries.orEmpty(),
            )
            else -> details
        }
    }

    /** Look up IPA pronunciation only (for ZH -> EN single-word results). */
    fun lookupPhonetic(word: String): String? {
        val cleanWord = extractEnglishWord(word) ?: return null
        return lookupEcDetails(cleanWord)?.phonetic ?: lookupPhoneticFallback(cleanWord)
    }

    private fun lookupEcDetails(cleanWord: String): DictionaryDetails? {
        val encodedWord = URLEncoder.encode(cleanWord.lowercase(Locale.ROOT), "UTF-8")
        val encodedDicts = URLEncoder.encode(EC_DICTS_PARAM, "UTF-8")
        val body = httpGet("$EC_ENDPOINT?q=$encodedWord&dicts=$encodedDicts") ?: return null
        return parseEcDetails(body)
    }

    private fun lookupPhoneticFallback(cleanWord: String): String? {
        val encoded = URLEncoder.encode(cleanWord.lowercase(Locale.ROOT), "UTF-8").replace("+", "%20")
        val body = httpGet(ENDPOINT + encoded) ?: return null
        return parsePhonetic(body)
    }

    private fun httpGet(url: String): String? {
        var connection: HttpURLConnection? = null
        return try {
            val http = (URL(url).openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = CONNECT_TIMEOUT_MS
                readTimeout = READ_TIMEOUT_MS
                instanceFollowRedirects = true
                setRequestProperty("Accept", "application/json")
                setRequestProperty("User-Agent", "DeskTranslate-Android/0.1")
            }
            connection = http
            if (http.responseCode !in 200..299) return null
            val body = http.inputStream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            body.takeIf { it.isNotBlank() }
        } catch (_: Exception) {
            null
        } finally {
            connection?.disconnect()
        }
    }

    /** Extract IPA pronunciation only from `dictionaryapi.dev` JSON array payloads. */
    internal fun parsePhonetic(rawPayload: String): String? {
        val array = try {
            JSONArray(rawPayload.trimStart('\uFEFF'))
        } catch (_: JSONException) {
            return null
        }

        for (i in 0 until array.length()) {
            val entry = array.optJSONObject(i) ?: continue
            val direct = formatPhonetic(entry.opt("phonetic") as? String)
            if (direct != null) return direct
            val phonetics = entry.optJSONArray("phonetics") ?: continue
            for (j in 0 until phonetics.length()) {
                val pObj = phonetics.optJSONObject(j) ?: continue
                val candidate = formatPhonetic(pObj.opt("text") as? String)
                if (candidate != null) return candidate
            }
        }
        return null
    }

    /** Parse Youdao EC JSON into IPA pronunciation and concise Chinese POS entries. */
    internal fun parseEcDetails(rawPayload: String): DictionaryDetails? {
        val root = try {
            JSONObject(rawPayload.trimStart('\uFEFF'))
        } catch (_: JSONException) {
            return null
        }

        val ecWords = root.optJSONObject("ec")?.optJSONArray("word")
        var phonetic = extractYoudaoPhonetic(ecWords)
        if (phonetic == null) {
            val simpleWords = root.optJSONObject("simple")?.optJSONArray("word")
            phonetic = extractYoudaoPhonetic(simpleWords)
        }

        val grouped = LinkedHashMap<String, MutableList<String>>()
        val seenByPos = HashMap<String, MutableSet<String>>()

        if (ecWords != null) {
            for (wIndex in 0 until ecWords.length()) {
                val wordObj = ecWords.optJSONObject(wIndex) ?: continue
                val trs = wordObj.optJSONArray("trs") ?: continue
                for (tIndex in 0 until trs.length()) {
                    val trWrap = trs.optJSONObject(tIndex) ?: continue
                    val trArray = trWrap.optJSONArray("tr") ?: continue
                    for (innerIndex in 0 until trArray.length()) {
                        val trItem = trArray.optJSONObject(innerIndex) ?: continue
                        val langObj = trItem.optJSONObject("l") ?: continue
                        val rawI = langObj.opt("i") ?: continue
                        val lines = when (rawI) {
                            is String -> listOf(rawI)
                            is JSONArray -> (0 until rawI.length()).mapNotNull { rawI.opt(it) as? String }
                            else -> emptyList()
                        }
                        for (line in lines) {
                            val cleanedLine = line.trim().replace(Regex("\\s+"), " ")
                            if (cleanedLine.isEmpty()) continue
                            val match = EC_POS_PREFIX_REGEX.matchEntire(cleanedLine) ?: continue
                            val pos = abbreviatePartOfSpeech(match.groupValues[1])
                            if (pos.isEmpty()) continue
                            if (!grouped.containsKey(pos) && grouped.size >= MAX_DICTIONARY_ENTRIES) continue

                            val bucket = grouped.getOrPut(pos) { mutableListOf() }
                            val seen = seenByPos.getOrPut(pos) { mutableSetOf() }
                            for (sense in splitEcSenses(match.groupValues[2])) {
                                if (bucket.size >= MAX_MEANINGS_PER_POS) break
                                if (!containsChinese(sense)) continue
                                if (seen.add(sense.lowercase(Locale.ROOT))) {
                                    bucket.add(sense)
                                }
                            }
                        }
                    }
                }
            }
        }

        val entries = grouped.entries
            .filter { it.value.isNotEmpty() }
            .map { DictionaryEntry(pos = it.key, meanings = it.value.toList()) }

        return if (phonetic == null && entries.isEmpty()) {
            null
        } else {
            DictionaryDetails(phonetic = phonetic, entries = entries)
        }
    }

    private fun extractYoudaoPhonetic(words: JSONArray?): String? {
        if (words == null) return null
        for (i in 0 until words.length()) {
            val wordObj = words.optJSONObject(i) ?: continue
            val us = formatPhonetic(wordObj.opt("usphone") as? String)
            if (us != null) return us
            val uk = formatPhonetic(wordObj.opt("ukphone") as? String)
            if (uk != null) return uk
        }
        return null
    }
}
