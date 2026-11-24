package com.whs0730.desktranslate

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DictionaryLookupTest {

    @Test
    fun recognisesSingleWordsVsSentences() {
        assertEquals("run", extractEnglishWord("  run! "))
        assertEquals("co-operate", extractEnglishWord("co-operate"))
        assertNull(extractEnglishWord("I run every day."))
        assertNull(extractEnglishWord("123"))

        assertTrue(isChineseWord("运行"))
        assertTrue(isChineseWord("苹果"))
        assertFalse(isChineseWord("今天天气很好"))
        assertFalse(isChineseWord("你好！"))
    }

    @Test
    fun normalisesPartOfSpeechAndPhonetics() {
        assertEquals("n.", abbreviatePartOfSpeech("noun"))
        assertEquals("v.", abbreviatePartOfSpeech("Verb"))
        assertEquals("adj.", abbreviatePartOfSpeech("adjective"))
        assertEquals("adv.", abbreviatePartOfSpeech("adverb"))
        assertEquals("interj.", abbreviatePartOfSpeech("int."))
        assertEquals("/ˈæpəl/", formatPhonetic("ˈæpəl"))
        assertEquals("/rʌn/", formatPhonetic("/rʌn/"))
        assertNull(formatPhonetic("  // "))
    }

    @Test
    fun parsesYoudaoEcJsonWithPhoneticAndChinesePosEntries() {
        val raw = """
            {
              "ec": {
                "word": [
                  {
                    "usphone": "ɡʊd",
                    "ukphone": "ɡʊd",
                    "trs": [
                      {"tr": [{"l": {"i": ["adj. 优良的；能干的，擅长的；令人愉快的；合适的；有益的"]}}]},
                      {"tr": [{"l": {"i": ["n. 善，正义；好事；（用于回应；确认）好处；English only"]}}]},
                      {"tr": [{"l": {"i": ["int. 哇，太好了"]}}]},
                      {"tr": [{"l": {"i": ["【名】 （Good）（英）古德（人名）"]}}]}
                    ]
                  }
                ]
              }
            }
        """.trimIndent()

        val details = DictionaryLookup.parseEcDetails(raw)
        assertNotNull(details)
        assertEquals("/ɡʊd/", details!!.phonetic)
        assertEquals(3, details.entries.size)
        assertEquals("adj.", details.entries[0].pos)
        assertEquals(listOf("优良的", "能干的，擅长的", "令人愉快的", "合适的"), details.entries[0].meanings)
        assertEquals("n.", details.entries[1].pos)
        assertEquals(listOf("善，正义", "好事", "（用于回应；确认）好处"), details.entries[1].meanings)
        assertEquals("interj.", details.entries[2].pos)
        assertEquals(listOf("哇，太好了"), details.entries[2].meanings)
    }

    @Test
    fun parsesFallbackPhoneticOnlyWithoutEnglishDefinitions() {
        val raw = """
            [
              {
                "word": "run",
                "phonetic": "/rʌn/",
                "meanings": [
                  {
                    "partOfSpeech": "verb",
                    "definitions": [
                      {"definition": "To move swiftly on foot."}
                    ]
                  }
                ]
              }
            ]
        """.trimIndent()

        assertEquals("/rʌn/", DictionaryLookup.parsePhonetic(raw))
    }

    @Test
    fun malformedDictionaryPayloadReturnsNullGracefully() {
        assertNull(DictionaryLookup.parseEcDetails("not-json"))
        assertNull(DictionaryLookup.parseEcDetails("{}"))
        assertNull(DictionaryLookup.parseEcDetails("""{"ec":{"word":[]}}"""))
        assertNull(DictionaryLookup.parsePhonetic("not-json"))
        assertNull(DictionaryLookup.parsePhonetic("[]"))
        assertNull(DictionaryLookup.parsePhonetic("""[{"phonetics":[]}]"""))
    }
}
