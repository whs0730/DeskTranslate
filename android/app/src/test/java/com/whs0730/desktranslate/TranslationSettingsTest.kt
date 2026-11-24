package com.whs0730.desktranslate

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TranslationSettingsTest {

    private class InMemoryKeyValueStore : KeyValueStore {
        val data = mutableMapOf<String, String>()
        override fun getString(key: String): String? = data[key]
        override fun putString(key: String, value: String) {
            data[key] = value
        }
    }

    private class InMemorySecretStorage : SecretStorage {
        val secrets = mutableMapOf<String, String>()
        override fun read(configId: String): String? = secrets[configId]
        override fun save(configId: String, secret: String) {
            secrets[configId] = secret
        }
        override fun delete(configId: String) {
            secrets.remove(configId)
        }
    }

    @Test
    fun masksKeysLikeDesktopWithoutReturningPlaintext() {
        assertEquals("sk-…7890", maskApiKey("sk-abcdef1234567890"))
        assertEquals("••••", maskApiKey("short"))
        assertEquals("", maskApiKey("   "))
    }

    @Test
    fun validatesApiKeyCharactersAndLength() {
        assertNull(validateNewApiKey("sk-validKey_123456"))
        assertNotNull(validateNewApiKey(""))
        assertNotNull(validateNewApiKey("key\nheader"))
        assertNotNull(validateNewApiKey("key with space"))
        assertNotNull(validateNewApiKey("a".repeat(MAX_API_KEY_LENGTH + 1)))
    }

    @Test
    fun managesMultipleProfilesKeepingSecretsOutOfSettingsStore() {
        val store = InMemoryKeyValueStore()
        val secrets = InMemorySecretStorage()
        val repo = TranslationSettingsRepository(store, secrets)

        assertEquals(TranslationProvider.MYMEMORY, repo.load().provider)

        val firstSnap = repo.saveApiConfig(null, "工作账号", "deepseek-flash", "sk-abcdef1234567890")
        assertEquals(1, firstSnap.configs.size)
        val first = firstSnap.configs[0]
        assertEquals("工作账号", first.name)
        assertEquals("deepseek-flash", first.model)
        assertEquals("sk-…7890", first.keyPreview)
        assertTrue(first.hasKey)
        assertTrue(first.active)
        assertFalse(store.data.values.joinToString().contains("sk-abcdef1234567890"))

        val secondSnap = repo.saveApiConfig(null, "备用账号", "deepseek-v4-pro", "sk-9999888877776666")
        assertEquals(2, secondSnap.configs.size)
        val second = secondSnap.configs[1]
        assertTrue(second.active)

        // Editing with blank key preserves existing secret.
        val editedSnap = repo.saveApiConfig(second.id, "主力 V4", "deepseek-v4-pro", "")
        val edited = editedSnap.configs.first { it.id == second.id }
        assertEquals("主力 V4", edited.name)
        assertEquals("sk-…6666", edited.keyPreview)
        assertEquals("sk-9999888877776666", secrets.read(second.id))

        // Deleting active profile falls back to first remaining and wipes its secret.
        val afterDelete = repo.deleteApiConfig(second.id)
        assertEquals(1, afterDelete.configs.size)
        assertEquals(first.id, afterDelete.activeConfigId)
        assertNull(secrets.read(second.id))

        // Switching to DeepSeek resolves active key and model in request config.
        repo.setProvider(TranslationProvider.DEEPSEEK)
        val reqConfig = repo.buildRequestConfig()
        assertEquals(TranslationProvider.DEEPSEEK, reqConfig.provider)
        assertEquals("deepseek-flash", reqConfig.deepSeekModel)
        assertEquals("sk-abcdef1234567890", reqConfig.deepSeekApiKey)
    }

    @Test
    fun rejectsDeletingLastRemainingProfile() {
        val repo = TranslationSettingsRepository(InMemoryKeyValueStore(), InMemorySecretStorage())
        val only = repo.saveApiConfig(null, "唯一", "deepseek-flash", "sk-123456789012").configs[0]

        val thrown = runCatching { repo.deleteApiConfig(only.id) }.exceptionOrNull()
        assertTrue(thrown is IllegalArgumentException)
        assertEquals(1, repo.load().configs.size)
    }
}
