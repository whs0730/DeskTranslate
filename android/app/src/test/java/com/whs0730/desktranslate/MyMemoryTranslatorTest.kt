package com.whs0730.desktranslate

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MyMemoryTranslatorTest {

    @Test
    fun encodesQueryAndLanguagePairAsUtf8() {
        val url = MyMemoryTranslator.buildRequestUrl("Hello world & 你好", "en|zh-CN")

        assertTrue(url.startsWith(MyMemoryTranslator.ENDPOINT + "?"))
        assertTrue(url.contains("q=Hello+world+%26+"))
        assertTrue(url.contains("%E4%BD%A0%E5%A5%BD"))
        assertTrue(url.contains("langpair=en%7Czh-CN"))
        assertFalse(url.contains(" "))
    }

    @Test
    fun stripsAlignmentTagsButKeepsOtherMarkup() {
        assertEquals(
            "你好世界",
            MyMemoryTranslator.stripAlignmentMarkup("<g id=\"1\">你好</g><g id=\"2\">世界</g>"),
        )
        assertEquals("<b>你好</b>", MyMemoryTranslator.stripAlignmentMarkup("<b>你好</b>"))
    }

    @Test
    fun decodesNumericAndNamedEntities() {
        assertEquals(
            "A&B <C> 你",
            MyMemoryTranslator.decodeHtmlEntities("A&amp;B &lt;C&gt; &#20320;"),
        )
        assertEquals("😀", MyMemoryTranslator.decodeHtmlEntities("&#x1F600;"))
        assertEquals("&unknown;", MyMemoryTranslator.decodeHtmlEntities("&unknown;"))
    }

    @Test
    fun parsesAndCleansTranslatedText() {
        val payload =
            """{"responseStatus":200,"responseData":{"translatedText":"<g id=\"1\">你好，世界！</g>"}}"""

        assertEquals(
            "你好，世界！",
            MyMemoryTranslator.parseTranslatedText(payload, "Hello, world!"),
        )
    }

    @Test
    fun acceptsStringStatus() {
        val payload =
            """{"responseStatus":"200.0","responseData":{"translatedText":"早上好"}}"""

        assertEquals("早上好", MyMemoryTranslator.parseTranslatedText(payload, "Good morning"))
    }

    @Test
    fun extractsDeduplicatedAlternativesForWordQueries() {
        val payload = """
            {
              "responseStatus": 200,
              "responseData": {"translatedText": "运行"},
              "matches": [
                {"segment": "run", "translation": "运行"},
                {"segment": "run", "translation": "跑"},
                {"segment": "run", "translation": "经营"},
                {"segment": "run", "translation": "run"},
                {"segment": "another phrase", "translation": "忽略此条"}
              ]
            }
        """.trimIndent()

        val (primary, alts) = MyMemoryTranslator.parseTranslatedResponse(payload, "run", includeAlternatives = true)
        assertEquals("运行", primary)
        assertEquals(listOf("跑", "经营"), alts)
    }

    @Test
    fun rejectsAnEchoedSourceText() {
        val payload =
            """{"responseStatus":200,"responseData":{"translatedText":"Hello,  world!"}}"""

        assertEquals(
            TranslationFailureKind.EMPTY_RESULT,
            failureKind { MyMemoryTranslator.parseTranslatedText(payload, "Hello, world!") },
        )
    }

    @Test
    fun rejectsBlankTranslations() {
        val payload = """{"responseStatus":200,"responseData":{"translatedText":"   "}}"""

        assertEquals(
            TranslationFailureKind.EMPTY_RESULT,
            failureKind { MyMemoryTranslator.parseTranslatedText(payload, "Hello") },
        )
    }

    @Test
    fun reportsProviderStatusErrors() {
        val payload = """{"responseStatus":403,"responseData":{"translatedText":""}}"""

        assertEquals(
            TranslationFailureKind.SERVICE,
            failureKind { MyMemoryTranslator.parseTranslatedText(payload, "Hello") },
        )
    }

    @Test
    fun reportsMalformedOrIncompleteResponses() {
        assertEquals(
            TranslationFailureKind.INVALID_RESPONSE,
            failureKind { MyMemoryTranslator.parseTranslatedText("not json", "Hello") },
        )
        assertEquals(
            TranslationFailureKind.INVALID_RESPONSE,
            failureKind { MyMemoryTranslator.parseTranslatedText("""{"responseStatus":200}""", "Hello") },
        )
    }

    private fun failureKind(block: () -> Unit): TranslationFailureKind {
        return try {
            block()
            throw AssertionError("Expected a TranslationFailure")
        } catch (error: TranslationFailure) {
            error.kind
        }
    }
}