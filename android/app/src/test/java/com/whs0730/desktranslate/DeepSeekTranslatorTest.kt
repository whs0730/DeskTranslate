package com.whs0730.desktranslate

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DeepSeekTranslatorTest {

    @Test
    fun buildsOfficialRequestPayloadWithDisabledThinkingAndTargetLanguage() {
        val enToZh = JSONObject(
            DeepSeekTranslator.buildRequestBody("Hello world", TranslationDirection.EN_TO_ZH, "deepseek-flash"),
        )
        assertEquals("deepseek-flash", enToZh.getString("model"))
        assertEquals(false, enToZh.getBoolean("stream"))
        assertEquals(4096, enToZh.getInt("max_tokens"))
        assertEquals("disabled", enToZh.getJSONObject("thinking").getString("type"))
        val messagesZh = enToZh.getJSONArray("messages")
        assertTrue(messagesZh.getJSONObject(0).getString("content").contains("简体中文"))
        assertEquals("Hello world", messagesZh.getJSONObject(1).getString("content"))

        val zhToEn = JSONObject(
            DeepSeekTranslator.buildRequestBody("运行", TranslationDirection.ZH_TO_EN, "deepseek-v4-pro"),
        )
        assertEquals("deepseek-v4-pro", zhToEn.getString("model"))
        val zhPrompt = zhToEn.getJSONArray("messages").getJSONObject(0).getString("content")
        assertTrue(zhPrompt.contains("英文"))
        assertTrue(zhPrompt.contains("JSON"))
        assertTrue(zhPrompt.contains("多个表达该中文意思的常用英文单词或英文短语"))
        assertTrue(zhPrompt.contains("不要反过来去解释某一个英文词的其他无关含义"))

        val sentence = JSONObject(
            DeepSeekTranslator.buildRequestBody("I run every morning.", TranslationDirection.EN_TO_ZH, "deepseek-flash"),
        )
        assertFalse(sentence.getJSONArray("messages").getJSONObject(0).getString("content").contains("JSON"))
    }

    @Test
    fun parsesStructuredDictionaryJsonAndFallsBackForPlainText() {
        val rawJson = """
            ```json
            {
              "translation": "运行",
              "phonetic": "rʌn",
              "entries": [
                {"pos": "verb", "meanings": ["跑；奔跑", "运行；运转", "经营"]},
                {"pos": "noun", "meanings": ["跑步", "连续演出"]}
              ]
            }
            ```
        """.trimIndent()
        val parsed = DeepSeekTranslator.parseDictionaryResponse(rawJson)
        assertEquals("运行", parsed.translation)
        assertEquals("/rʌn/", parsed.phonetic)
        assertEquals(2, parsed.entries.size)
        assertEquals("v.", parsed.entries[0].pos)
        assertEquals(listOf("跑；奔跑", "运行；运转", "经营"), parsed.entries[0].meanings)
        assertEquals("n.", parsed.entries[1].pos)

        val fallback = DeepSeekTranslator.parseDictionaryResponse("你好")
        assertEquals("你好", fallback.translation)
        assertTrue(fallback.entries.isEmpty())
    }

    @Test
    fun parsesValidStopCompletionAndTrimsResult() {
        val raw = """{"choices":[{"finish_reason":"stop","message":{"content":"  你好，世界！ \n"}}]}"""
        assertEquals("你好，世界！", DeepSeekTranslator.parseTranslatedText(raw))
    }

    @Test
    fun rejectsMalformedEmptyAndIncompleteOutputs() {
        assertEquals(
            TranslationFailureKind.INVALID_RESPONSE,
            failureKind { DeepSeekTranslator.parseTranslatedText("not-json") },
        )
        assertEquals(
            TranslationFailureKind.EMPTY_RESULT,
            failureKind { DeepSeekTranslator.parseTranslatedText("{}") },
        )
        assertEquals(
            TranslationFailureKind.INVALID_RESPONSE,
            failureKind { DeepSeekTranslator.parseTranslatedText("""{"choices":[null]}""") },
        )
        assertEquals(
            TranslationFailureKind.EMPTY_RESULT,
            failureKind {
                DeepSeekTranslator.parseTranslatedText(
                    """{"choices":[{"finish_reason":"stop","message":{"content":"   "}}]}""",
                )
            },
        )
        assertEquals(
            TranslationFailureKind.INCOMPLETE_RESULT,
            failureKind {
                DeepSeekTranslator.parseTranslatedText(
                    """{"choices":[{"finish_reason":"length","message":{"content":"partial"}}]}""",
                )
            },
        )
    }

    @Test
    fun missingApiKeyFailsBeforeNetworkCall() {
        val kind = failureKind {
            DeepSeekTranslator.translate("Hello", TranslationDirection.EN_TO_ZH, apiKey = "   ")
        }
        assertEquals(TranslationFailureKind.MISSING_API_KEY, kind)
    }

    @Test
    fun mapsHttpErrorsClearlyWithoutLeakingKey() {
        val secret = "sk-super-secret-value"
        val authFailure = DeepSeekTranslator.mapHttpFailure(401)
        assertEquals(TranslationFailureKind.AUTH_INVALID, authFailure.kind)
        assertFalse(authFailure.message.orEmpty().contains(secret))

        assertEquals(TranslationFailureKind.INSUFFICIENT_BALANCE, DeepSeekTranslator.mapHttpFailure(402).kind)
        assertEquals(TranslationFailureKind.RATE_LIMITED, DeepSeekTranslator.mapHttpFailure(429).kind)
        assertEquals(TranslationFailureKind.SERVICE, DeepSeekTranslator.mapHttpFailure(503).kind)
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
