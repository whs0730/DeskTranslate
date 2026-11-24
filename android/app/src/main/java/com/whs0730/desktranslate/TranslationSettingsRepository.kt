package com.whs0730.desktranslate

import android.content.Context
import androidx.core.content.edit
import java.util.UUID
import org.json.JSONArray
import org.json.JSONObject

/** Thin key-value persistence contract for JVM unit testing. */
interface KeyValueStore {
    fun getString(key: String): String?
    fun putString(key: String, value: String)
}

class SharedPreferencesKeyValueStore(context: Context) : KeyValueStore {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    override fun getString(key: String): String? = prefs.getString(key, null)

    override fun putString(key: String, value: String) {
        prefs.edit { putString(key, value) }
    }

    companion object {
        const val PREFS_NAME = "desktranslate_settings"
    }
}

/**
 * Manages translation provider selection and multi-profile DeepSeek API configs.
 * Secrets live only in [SecretStorage]; settings JSON never stores raw API keys.
 */
class TranslationSettingsRepository(
    private val store: KeyValueStore,
    private val secrets: SecretStorage,
) {
    constructor(context: Context) : this(
        store = SharedPreferencesKeyValueStore(context),
        secrets = AndroidKeyStoreSecretStorage(context),
    )

    private data class StoredProfile(
        val id: String,
        val name: String,
        val model: String,
    )

    fun load(): TranslationSettingsSnapshot {
        val provider = TranslationProvider.fromWire(store.getString(KEY_PROVIDER))
        val profiles = parseProfiles(store.getString(KEY_PROFILES))
        val storedActive = store.getString(KEY_ACTIVE_ID).orEmpty()
        val activeId = if (profiles.any { it.id == storedActive }) {
            storedActive
        } else {
            profiles.firstOrNull()?.id.orEmpty()
        }

        val configs = profiles.map { profile ->
            val rawKey = secrets.read(profile.id)?.trim().orEmpty()
            DeepSeekApiConfig(
                id = profile.id,
                name = profile.name,
                model = profile.model,
                hasKey = rawKey.isNotEmpty(),
                keyPreview = maskApiKey(rawKey),
                active = profile.id == activeId,
            )
        }
        return TranslationSettingsSnapshot(
            provider = provider,
            configs = configs,
            activeConfigId = activeId,
        )
    }

    fun buildRequestConfig(): TranslationRequestConfig {
        val snapshot = load()
        if (snapshot.provider == TranslationProvider.MYMEMORY) {
            return TranslationRequestConfig(provider = TranslationProvider.MYMEMORY)
        }
        val active = snapshot.activeConfig
        val key = active?.let { secrets.read(it.id)?.trim() }?.takeIf { it.isNotEmpty() }
        return TranslationRequestConfig(
            provider = TranslationProvider.DEEPSEEK,
            deepSeekModel = active?.model ?: DEFAULT_DEEPSEEK_MODEL,
            deepSeekApiKey = key,
        )
    }

    fun setProvider(provider: TranslationProvider): TranslationSettingsSnapshot {
        store.putString(KEY_PROVIDER, provider.wireValue)
        return load()
    }

    fun activateConfig(configId: String): TranslationSettingsSnapshot {
        val profiles = parseProfiles(store.getString(KEY_PROFILES))
        if (profiles.any { it.id == configId }) {
            store.putString(KEY_ACTIVE_ID, configId)
        }
        return load()
    }

    /**
     * Create a new API profile when [configId] is null/blank, or update an existing one.
     * Leaving [rawKey] blank while editing an existing profile preserves its saved key.
     */
    fun saveApiConfig(
        configId: String?,
        rawName: String,
        rawModel: String,
        rawKey: String,
    ): TranslationSettingsSnapshot {
        val name = rawName.trim().take(MAX_CONFIG_NAME_LENGTH)
        require(name.isNotEmpty()) { "请填写配置名称。" }

        val model = sanitizeDeepSeekModel(rawModel)
        val trimmedKey = rawKey.trim()
        val profiles = parseProfiles(store.getString(KEY_PROFILES)).toMutableList()
        val existingIndex = if (configId.isNullOrBlank()) -1 else profiles.indexOfFirst { it.id == configId }

        if (existingIndex >= 0) {
            val existing = profiles[existingIndex]
            val existingKey = secrets.read(existing.id)?.trim().orEmpty()
            if (trimmedKey.isNotEmpty()) {
                val error = validateNewApiKey(trimmedKey)
                require(error == null) { error!! }
                secrets.save(existing.id, trimmedKey)
            } else if (existingKey.isEmpty()) {
                throw IllegalArgumentException("请填写 DeepSeek API Key。")
            }
            profiles[existingIndex] = existing.copy(name = name, model = model)
            persistProfiles(profiles)
        } else {
            require(profiles.size < MAX_API_CONFIGS) { "最多保存 $MAX_API_CONFIGS 条 API 配置。" }
            val error = validateNewApiKey(trimmedKey)
            require(error == null) { error!! }
            val newId = generateId(profiles)
            secrets.save(newId, trimmedKey)
            profiles.add(StoredProfile(id = newId, name = name, model = model))
            persistProfiles(profiles)
            // Mirror desktop: activate a newly created profile with a valid key.
            store.putString(KEY_ACTIVE_ID, newId)
        }

        return load()
    }

    fun deleteApiConfig(configId: String): TranslationSettingsSnapshot {
        val profiles = parseProfiles(store.getString(KEY_PROFILES)).toMutableList()
        require(profiles.size > 1) { "至少保留一条配置。" }

        val removed = profiles.removeAll { it.id == configId }
        if (removed) {
            secrets.delete(configId)
            persistProfiles(profiles)
            val currentActive = store.getString(KEY_ACTIVE_ID).orEmpty()
            if (currentActive == configId || profiles.none { it.id == currentActive }) {
                store.putString(KEY_ACTIVE_ID, profiles.firstOrNull()?.id.orEmpty())
            }
        }
        return load()
    }

    fun nextDefaultConfigName(configs: List<DeepSeekApiConfig>): String {
        val used = configs.map { it.name }.toSet()
        var index = configs.size + 1
        while ("配置 $index" in used) index += 1
        return "配置 $index"
    }

    private fun generateId(existing: List<StoredProfile>): String {
        val used = existing.map { it.id }.toSet()
        while (true) {
            val candidate = "cfg_" + UUID.randomUUID().toString().replace("-", "").take(12)
            if (candidate !in used) return candidate
        }
    }

    private fun parseProfiles(raw: String?): List<StoredProfile> {
        if (raw.isNullOrBlank()) return emptyList()
        return runCatching {
            val array = JSONArray(raw)
            val result = mutableListOf<StoredProfile>()
            val seen = mutableSetOf<String>()
            for (i in 0 until minOf(array.length(), MAX_API_CONFIGS)) {
                val obj = array.optJSONObject(i) ?: continue
                val id = obj.optString("id").trim()
                if (id.isEmpty() || !seen.add(id)) continue
                val name = obj.optString("name").trim().ifEmpty { "DeepSeek" }.take(MAX_CONFIG_NAME_LENGTH)
                val model = sanitizeDeepSeekModel(obj.optString("model"))
                result.add(StoredProfile(id = id, name = name, model = model))
            }
            result
        }.getOrDefault(emptyList())
    }

    private fun persistProfiles(profiles: List<StoredProfile>) {
        val array = JSONArray()
        for (profile in profiles) {
            array.put(
                JSONObject().apply {
                    put("id", profile.id)
                    put("name", profile.name)
                    put("model", profile.model)
                },
            )
        }
        store.putString(KEY_PROFILES, array.toString())
    }

    private companion object {
        const val KEY_PROVIDER = "translation_provider"
        const val KEY_PROFILES = "api_configs"
        const val KEY_ACTIVE_ID = "active_api_config"
    }
}
