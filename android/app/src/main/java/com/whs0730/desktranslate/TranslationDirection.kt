package com.whs0730.desktranslate

/**
 * Translation directions exposed by the Android UI.
 *
 * [AUTO] keeps the desktop rule: any CJK ideograph means Chinese-to-English,
 * otherwise English-to-Chinese. A manually selected direction always wins when
 * resolved directly; UI submission validates obvious pure-language mismatches
 * beforehand via [validateManualDirection].
 */
enum class TranslationDirection(val myMemoryPair: String?) {
    AUTO(null),
    ZH_TO_EN("zh-CN|en"),
    EN_TO_ZH("en|zh-CN");

    companion object {
        fun resolve(text: String, selected: TranslationDirection): TranslationDirection {
            if (selected != AUTO) return selected
            return if (containsChinese(text)) ZH_TO_EN else EN_TO_ZH
        }
    }
}

/**
 * Result of checking whether a manually chosen direction obviously contradicts
 * pure-English or pure-Chinese source text.
 */
enum class DirectionMismatch {
    USE_EN_TO_ZH,
    USE_ZH_TO_EN,
}

/** MyMemory rejects a `q` value whose UTF-8 encoding exceeds this size. */
const val MAX_QUERY_BYTES = 500

// Ranges used by src/desktranslate/translator.py on the desktop app.
private val CJK_CODE_POINT_RANGES = listOf(
    0x3400..0x4DBF,   // CJK Unified Ideographs Extension A
    0x4E00..0x9FFF,   // CJK Unified Ideographs
    0xF900..0xFAFF,   // CJK Compatibility Ideographs
    0x20000..0x2EBEF, // Extensions B-F and I
    0x30000..0x323AF, // Extensions G-H
)

private fun isChineseCodePoint(codePoint: Int): Boolean =
    CJK_CODE_POINT_RANGES.any { codePoint in it }

private fun isLatinLetterCodePoint(codePoint: Int): Boolean {
    if (codePoint in 'A'.code..'Z'.code || codePoint in 'a'.code..'z'.code) return true
    return Character.isLetter(codePoint) &&
        Character.UnicodeScript.of(codePoint) == Character.UnicodeScript.LATIN
}

/** Return whether [text] contains a CJK ideograph used by Chinese text. */
fun containsChinese(text: String): Boolean {
    var index = 0
    while (index < text.length) {
        val codePoint = text.codePointAt(index)
        if (isChineseCodePoint(codePoint)) return true
        index += Character.charCount(codePoint)
    }
    return false
}

/**
 * Reject obviously inverted manual selections before sending a network request:
 * - Pure English with [TranslationDirection.ZH_TO_EN] -> [DirectionMismatch.USE_EN_TO_ZH]
 * - Pure Chinese with [TranslationDirection.EN_TO_ZH] -> [DirectionMismatch.USE_ZH_TO_EN]
 *
 * Mixed Chinese/English text, [TranslationDirection.AUTO], pure numbers/symbols,
 * and inputs containing other scripts are allowed to proceed.
 */
fun validateManualDirection(
    text: String,
    selected: TranslationDirection,
): DirectionMismatch? {
    if (selected == TranslationDirection.AUTO) return null

    var hasChinese = false
    var hasLatin = false
    var hasOtherLetter = false

    var index = 0
    while (index < text.length) {
        val codePoint = text.codePointAt(index)
        when {
            isChineseCodePoint(codePoint) -> hasChinese = true
            isLatinLetterCodePoint(codePoint) -> hasLatin = true
            Character.isLetter(codePoint) -> hasOtherLetter = true
        }
        index += Character.charCount(codePoint)
    }

    return when {
        selected == TranslationDirection.ZH_TO_EN &&
            hasLatin && !hasChinese && !hasOtherLetter -> DirectionMismatch.USE_EN_TO_ZH

        selected == TranslationDirection.EN_TO_ZH &&
            hasChinese && !hasLatin && !hasOtherLetter -> DirectionMismatch.USE_ZH_TO_EN

        else -> null
    }
}

/** Number of bytes [text] occupies when encoded as UTF-8. */
fun utf8ByteCount(text: String): Int = text.toByteArray(Charsets.UTF_8).size

/** Whether [text] fits MyMemory's `q` parameter limit. */
fun isWithinQueryLimit(text: String): Boolean = utf8ByteCount(text) <= MAX_QUERY_BYTES
