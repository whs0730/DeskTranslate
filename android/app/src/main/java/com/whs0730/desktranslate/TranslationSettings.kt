package com.whs0730.desktranslate

/** Supported translation backends, mirroring desktop DeskTranslate. */
enum class TranslationProvider(val wireValue: String) {
    MYMEMORY("mymemory"),
    DEEPSEEK("deepseek");

    companion object {
        fun fromWire(value: String?): TranslationProvider = when (value) {
            DEEPSEEK.wireValue -> DEEPSEEK
            else -> MYMEMORY
        }
    }
}

/** DeepSeek models exposed in Settings, aligned with desktop `deepseek.py`. */
val DEEPSEEK_MODELS: List<Pair<String, String>> = listOf(
    "deepseek-flash" to "DeepSeek Flash",
    "deepseek-v4-pro" to "DeepSeek V4 Pro",
)

const val DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"
const val MAX_API_CONFIGS = 20
const val MAX_CONFIG_NAME_LENGTH = 40
const val MAX_API_KEY_LENGTH = 512
const val MAX_DEEPSEEK_CHARS = 3000

/** One named DeepSeek API profile; the raw key is stored separately in [SecretStorage]. */
data class DeepSeekApiConfig(
    val id: String,
    val name: String,
    val model: String = DEFAULT_DEEPSEEK_MODEL,
    val hasKey: Boolean = false,
    val keyPreview: String = "",
    val active: Boolean = false,
)

/** Snapshot of user translation settings safe to bind in UI (never contains plaintext keys). */
data class TranslationSettingsSnapshot(
    val provider: TranslationProvider = TranslationProvider.MYMEMORY,
    val configs: List<DeepSeekApiConfig> = emptyList(),
    val activeConfigId: String = "",
) {
    val activeConfig: DeepSeekApiConfig?
        get() = configs.firstOrNull { it.id == activeConfigId } ?: configs.firstOrNull()

    val hasActiveApiKey: Boolean
        get() = activeConfig?.hasKey == true

    val activeModel: String
        get() = activeConfig?.model ?: DEFAULT_DEEPSEEK_MODEL
}

/** Resolved runtime request config passed to the translation engine. */
data class TranslationRequestConfig(
    val provider: TranslationProvider = TranslationProvider.MYMEMORY,
    val deepSeekModel: String = DEFAULT_DEEPSEEK_MODEL,
    val deepSeekApiKey: String? = null,
)

/** Normalize a model id to one of the supported desktop DeepSeek models. */
fun sanitizeDeepSeekModel(model: String?): String =
    if (DEEPSEEK_MODELS.any { it.first == model }) model!! else DEFAULT_DEEPSEEK_MODEL

/** Display label for a DeepSeek model id. */
fun deepSeekModelLabel(model: String): String =
    DEEPSEEK_MODELS.firstOrNull { it.first == model }?.second ?: model

/**
 * Mask an API key for UI display using the desktop format (`sk-…7890`).
 * Never returns the full plaintext secret.
 */
fun maskApiKey(rawKey: String?): String {
    val trimmed = rawKey?.trim().orEmpty()
    if (trimmed.isEmpty()) return ""
    if (trimmed.length <= 7) return "••••"
    return "${trimmed.take(3)}…${trimmed.takeLast(4)}"
}

/**
 * Validate a newly entered DeepSeek API key before persisting it.
 * Returns a short Chinese error message, or `null` when valid.
 */
fun validateNewApiKey(rawKey: String): String? {
    val key = rawKey.trim()
    if (key.isEmpty()) return "请填写 DeepSeek API Key。"
    if (key.length > MAX_API_KEY_LENGTH) return "API Key 过长，请检查后重试。"
    if (key.any { ch -> ch.code !in 33..126 }) {
        return "API Key 包含无效字符或空白，请检查后重试。"
    }
    return null
}

/** Number of Unicode code points in [text] for DeepSeek's 3000-character limit. */
fun unicodeCharCount(text: String): Int = text.codePointCount(0, text.length)
