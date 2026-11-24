package com.whs0730.desktranslate

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TranslationDirectionTest {

    @Test
    fun autoPicksChineseToEnglishForHanText() {
        assertEquals(
            TranslationDirection.ZH_TO_EN,
            TranslationDirection.resolve("你好，世界", TranslationDirection.AUTO),
        )
    }

    @Test
    fun autoPicksEnglishToChineseForLatinText() {
        assertEquals(
            TranslationDirection.EN_TO_ZH,
            TranslationDirection.resolve("Hello, world", TranslationDirection.AUTO),
        )
    }

    @Test
    fun manualDirectionWinsOverAutoDetection() {
        assertEquals(
            TranslationDirection.EN_TO_ZH,
            TranslationDirection.resolve("你好", TranslationDirection.EN_TO_ZH),
        )
        assertEquals(
            TranslationDirection.ZH_TO_EN,
            TranslationDirection.resolve("Hello", TranslationDirection.ZH_TO_EN),
        )
    }

    @Test
    fun flagsPureEnglishWithChineseToEnglishManualDirection() {
        assertEquals(
            DirectionMismatch.USE_EN_TO_ZH,
            validateManualDirection("Hello, world! 2026", TranslationDirection.ZH_TO_EN),
        )
        assertEquals(
            DirectionMismatch.USE_EN_TO_ZH,
            validateManualDirection("café don't stop", TranslationDirection.ZH_TO_EN),
        )
    }

    @Test
    fun flagsPureChineseWithEnglishToChineseManualDirection() {
        assertEquals(
            DirectionMismatch.USE_ZH_TO_EN,
            validateManualDirection("今天是2026年，你好！", TranslationDirection.EN_TO_ZH),
        )
        assertEquals(
            DirectionMismatch.USE_ZH_TO_EN,
            validateManualDirection("\uD840\uDC00你好", TranslationDirection.EN_TO_ZH),
        )
    }

    @Test
    fun allowsMatchingManualDirectionMixedTextAndAutoMode() {
        assertNull(validateManualDirection("Hello, world!", TranslationDirection.EN_TO_ZH))
        assertNull(validateManualDirection("你好，世界！", TranslationDirection.ZH_TO_EN))

        // Mixed Chinese and English continues with the manual direction.
        assertNull(validateManualDirection("我在用 Android 手机", TranslationDirection.ZH_TO_EN))
        assertNull(validateManualDirection("我在用 Android 手机", TranslationDirection.EN_TO_ZH))

        // Auto mode never triggers manual mismatch warnings.
        assertNull(validateManualDirection("Hello, world!", TranslationDirection.AUTO))
        assertNull(validateManualDirection("你好，世界！", TranslationDirection.AUTO))

        // Pure digits/punctuation or non-ZH/EN scripts are not flagged as inverted.
        assertNull(validateManualDirection("12345 !!!", TranslationDirection.ZH_TO_EN))
        assertNull(validateManualDirection("12345 !!!", TranslationDirection.EN_TO_ZH))
        assertNull(validateManualDirection("こんにちは", TranslationDirection.ZH_TO_EN))
        assertNull(validateManualDirection("Hello こんにちは", TranslationDirection.ZH_TO_EN))
    }

    @Test
    fun recognisesSupplementaryHanCodePoints() {
        // U+20000, a CJK Unified Ideograph in Extension B.
        assertTrue(containsChinese("\uD840\uDC00"))
        assertFalse(containsChinese("こんにちは"))
    }

    @Test
    fun countsUtf8BytesNotCharacters() {
        assertEquals(3, utf8ByteCount("abc"))
        assertEquals(6, utf8ByteCount("你好"))
        assertEquals(4, utf8ByteCount("😀"))
    }

    @Test
    fun enforcesTheFiveHundredByteLimit() {
        assertTrue(isWithinQueryLimit("a".repeat(500)))
        assertFalse(isWithinQueryLimit("a".repeat(501)))
        assertTrue(isWithinQueryLimit("你".repeat(166)))
        assertFalse(isWithinQueryLimit("你".repeat(167)))
    }

    @Test
    fun usesTheDesktopLanguagePairs() {
        assertEquals(null, TranslationDirection.AUTO.myMemoryPair)
        assertEquals("zh-CN|en", TranslationDirection.ZH_TO_EN.myMemoryPair)
        assertEquals("en|zh-CN", TranslationDirection.EN_TO_ZH.myMemoryPair)
    }
}
