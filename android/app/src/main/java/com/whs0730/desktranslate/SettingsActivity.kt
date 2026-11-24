package com.whs0730.desktranslate

import android.graphics.Typeface
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.animation.OvershootInterpolator
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.isVisible
import androidx.core.widget.NestedScrollView
import com.whs0730.desktranslate.ui.LiquidBackdropView
import com.whs0730.desktranslate.ui.LiquidGlassFrameLayout
import com.whs0730.desktranslate.ui.LiquidGlassHost
import com.whs0730.desktranslate.ui.LiquidGlassLinearLayout
import com.whs0730.desktranslate.ui.LiquidGlassPillView
import com.whs0730.desktranslate.ui.LiquidGlassPreset

/**
 * Liquid Glass settings screen mirroring desktop `settings.html`'s Translation Options:
 * choose between free MyMemory and DeepSeek (multi-profile API key management).
 */
class SettingsActivity : AppCompatActivity() {

    private lateinit var repository: TranslationSettingsRepository

    private lateinit var backdrop: LiquidBackdropView
    private lateinit var settingsRoot: View
    private lateinit var settingsScroll: NestedScrollView
    private lateinit var settingsFooter: View
    private lateinit var closeSettingsButton: LiquidGlassFrameLayout
    private lateinit var tabTranslationPill: LiquidGlassFrameLayout
    private lateinit var providerCard: LiquidGlassLinearLayout
    private lateinit var providerMyMemoryRow: LiquidGlassLinearLayout
    private lateinit var providerMyMemoryRadio: View
    private lateinit var providerDeepSeekRow: LiquidGlassLinearLayout
    private lateinit var providerDeepSeekRadio: View
    private lateinit var deepseekSection: LiquidGlassLinearLayout
    private lateinit var addConfigButton: TextView
    private lateinit var configListContainer: LinearLayout
    private lateinit var configEmptyText: TextView
    private lateinit var configEditorCard: LiquidGlassLinearLayout
    private lateinit var editorTitle: TextView
    private lateinit var editorHint: TextView
    private lateinit var configNameField: LiquidGlassFrameLayout
    private lateinit var configNameInput: EditText
    private lateinit var modelTrack: LiquidGlassFrameLayout
    private lateinit var modelThumb: LiquidGlassPillView
    private lateinit var modelFlashButton: TextView
    private lateinit var modelV4ProButton: TextView
    private lateinit var apiKeyField: LiquidGlassFrameLayout
    private lateinit var apiKeyInput: EditText
    private lateinit var editorCancelButton: TextView
    private lateinit var editorSaveButton: LiquidGlassFrameLayout
    private lateinit var settingsError: TextView
    private lateinit var settingsStatus: TextView
    private lateinit var cancelSettingsButton: TextView
    private lateinit var saveSettingsButton: LiquidGlassFrameLayout

    private val staticGlassSurfaces = mutableListOf<LiquidGlassHost>()
    private val dynamicRowSurfaces = mutableListOf<LiquidGlassHost>()

    private var selectedProvider = TranslationProvider.MYMEMORY
    private var configs: List<DeepSeekApiConfig> = emptyList()
    private var activeConfigId: String = ""
    private val pendingDeletes = linkedSetOf<String>()
    private var editingConfigId: String? = null
    private var editorModel: String = DEFAULT_DEEPSEEK_MODEL

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_settings)

        repository = TranslationSettingsRepository(this)
        bindViews()
        setupLiquidGlass()
        applyWindowInsets()
        setupActions()
        loadInitialSettings()
    }

    private fun bindViews() {
        backdrop = findViewById(R.id.settingsBackdrop)
        settingsRoot = findViewById(R.id.settingsRoot)
        settingsScroll = findViewById(R.id.settingsScroll)
        settingsFooter = findViewById(R.id.settingsFooter)
        closeSettingsButton = findViewById(R.id.closeSettingsButton)
        tabTranslationPill = findViewById(R.id.tabTranslationPill)
        providerCard = findViewById(R.id.providerCard)
        providerMyMemoryRow = findViewById(R.id.providerMyMemoryRow)
        providerMyMemoryRadio = findViewById(R.id.providerMyMemoryRadio)
        providerDeepSeekRow = findViewById(R.id.providerDeepSeekRow)
        providerDeepSeekRadio = findViewById(R.id.providerDeepSeekRadio)
        deepseekSection = findViewById(R.id.deepseekSection)
        addConfigButton = findViewById(R.id.addConfigButton)
        configListContainer = findViewById(R.id.configListContainer)
        configEmptyText = findViewById(R.id.configEmptyText)
        configEditorCard = findViewById(R.id.configEditorCard)
        editorTitle = findViewById(R.id.editorTitle)
        editorHint = findViewById(R.id.editorHint)
        configNameField = findViewById(R.id.configNameField)
        configNameInput = findViewById(R.id.configNameInput)
        modelTrack = findViewById(R.id.modelTrack)
        modelThumb = findViewById(R.id.modelThumb)
        modelFlashButton = findViewById(R.id.modelFlashButton)
        modelV4ProButton = findViewById(R.id.modelV4ProButton)
        apiKeyField = findViewById(R.id.apiKeyField)
        apiKeyInput = findViewById(R.id.apiKeyInput)
        editorCancelButton = findViewById(R.id.editorCancelButton)
        editorSaveButton = findViewById(R.id.editorSaveButton)
        settingsError = findViewById(R.id.settingsError)
        settingsStatus = findViewById(R.id.settingsStatus)
        cancelSettingsButton = findViewById(R.id.cancelSettingsButton)
        saveSettingsButton = findViewById(R.id.saveSettingsButton)
    }

    private fun setupLiquidGlass() {
        closeSettingsButton.setGlassPreset(LiquidGlassPreset.BUTTON_CLEAR)
        tabTranslationPill.setGlassPreset(LiquidGlassPreset.THUMB)
        providerCard.setGlassPreset(LiquidGlassPreset.PANEL)
        providerMyMemoryRow.setGlassPreset(LiquidGlassPreset.FIELD)
        providerDeepSeekRow.setGlassPreset(LiquidGlassPreset.FIELD)
        deepseekSection.setGlassPreset(LiquidGlassPreset.PANEL)
        configEditorCard.setGlassPreset(LiquidGlassPreset.FIELD)
        configNameField.setGlassPreset(LiquidGlassPreset.FIELD)
        apiKeyField.setGlassPreset(LiquidGlassPreset.FIELD)
        modelTrack.setGlassPreset(LiquidGlassPreset.TRACK)
        modelThumb.setGlassPreset(LiquidGlassPreset.THUMB)
        editorSaveButton.setGlassPreset(LiquidGlassPreset.BUTTON_MINT)
        saveSettingsButton.setGlassPreset(LiquidGlassPreset.BUTTON_MINT)

        staticGlassSurfaces.addAll(
            listOf(
                closeSettingsButton,
                tabTranslationPill,
                providerCard,
                providerMyMemoryRow,
                providerDeepSeekRow,
                deepseekSection,
                configEditorCard,
                configNameField,
                apiKeyField,
                modelTrack,
                modelThumb,
                editorSaveButton,
                saveSettingsButton,
            ),
        )
        staticGlassSurfaces.forEach { it.bindBackdrop(backdrop) }

        settingsScroll.setOnScrollChangeListener { _, _, _, _, _ ->
            refreshAllGlass()
        }
    }

    private fun refreshAllGlass() {
        staticGlassSurfaces.forEach { it.refreshGlass() }
        dynamicRowSurfaces.forEach { it.refreshGlass() }
    }

    private fun applyWindowInsets() {
        val imeBreathingPx = (16 * resources.displayMetrics.density).toInt()
        ViewCompat.setOnApplyWindowInsetsListener(settingsRoot) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            // Keep the bottom Save/Cancel footer permanently anchored at the bottom above nav bars.
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            // Apply IME bottom padding only to the scroll viewport so fields stay scrollable above the keyboard.
            val footerHeight = settingsFooter.height
            val imePadding = if (ime.bottom > bars.bottom) {
                (ime.bottom - bars.bottom - footerHeight + imeBreathingPx).coerceAtLeast(0)
            } else {
                0
            }
            settingsScroll.setPadding(
                settingsScroll.paddingLeft,
                settingsScroll.paddingTop,
                settingsScroll.paddingRight,
                imePadding,
            )
            view.post {
                refreshAllGlass()
                if (imePadding > 0 && configEditorCard.isVisible) {
                    val targetY = (deepseekSection.top + configEditorCard.top - imeBreathingPx)
                        .coerceAtLeast(0)
                    settingsScroll.smoothScrollTo(0, targetY)
                }
            }
            insets
        }
        ViewCompat.requestApplyInsets(settingsRoot)
    }

    private fun setupActions() {
        closeSettingsButton.setOnClickListener { finish() }
        cancelSettingsButton.setOnClickListener { finish() }

        providerMyMemoryRow.setOnClickListener {
            selectedProvider = TranslationProvider.MYMEMORY
            clearFeedback()
            renderProviderSelection()
        }
        providerDeepSeekRow.setOnClickListener {
            selectedProvider = TranslationProvider.DEEPSEEK
            clearFeedback()
            renderProviderSelection()
        }

        addConfigButton.setOnClickListener { openEditor(null) }
        editorCancelButton.setOnClickListener { closeEditor() }
        editorSaveButton.setOnClickListener { saveEditorConfig() }

        modelFlashButton.setOnClickListener { selectEditorModel("deepseek-flash", animate = true) }
        modelV4ProButton.setOnClickListener { selectEditorModel("deepseek-v4-pro", animate = true) }
        modelTrack.addOnLayoutChangeListener { _, left, _, right, _, oldLeft, _, oldRight, _ ->
            if (right - left != oldRight - oldLeft || modelThumb.layoutParams.width <= 0) {
                positionModelThumb(animate = false)
            }
        }

        saveSettingsButton.setOnClickListener { saveAllSettings() }
    }

    private fun loadInitialSettings() {
        val snapshot = repository.load()
        selectedProvider = snapshot.provider
        applySnapshot(snapshot)
        renderProviderSelection()
    }

    private fun applySnapshot(snapshot: TranslationSettingsSnapshot) {
        val filtered = snapshot.configs.filterNot { it.id in pendingDeletes }
        activeConfigId = if (filtered.any { it.id == activeConfigId }) {
            activeConfigId
        } else if (filtered.any { it.id == snapshot.activeConfigId }) {
            snapshot.activeConfigId
        } else {
            filtered.firstOrNull()?.id.orEmpty()
        }
        configs = filtered.map { it.copy(active = it.id == activeConfigId) }
        if (editingConfigId != null && configs.none { it.id == editingConfigId }) {
            closeEditor()
        }
        renderConfigList()
    }

    private fun renderProviderSelection() {
        val isDeepSeek = selectedProvider == TranslationProvider.DEEPSEEK
        providerMyMemoryRadio.isSelected = !isDeepSeek
        providerDeepSeekRadio.isSelected = isDeepSeek
        providerMyMemoryRow.setGlassPreset(
            if (!isDeepSeek) LiquidGlassPreset.THUMB else LiquidGlassPreset.FIELD,
        )
        providerDeepSeekRow.setGlassPreset(
            if (isDeepSeek) LiquidGlassPreset.THUMB else LiquidGlassPreset.FIELD,
        )
        deepseekSection.isVisible = isDeepSeek
        settingsScroll.post { refreshAllGlass() }
    }

    private fun renderConfigList() {
        configListContainer.removeAllViews()
        dynamicRowSurfaces.clear()

        val inflater = LayoutInflater.from(this)
        val allowDelete = configs.size > 1
        for (config in configs) {
            val rowView = inflater.inflate(R.layout.item_api_config, configListContainer, false)
            val rowGlass = rowView.findViewById<LiquidGlassLinearLayout>(R.id.configRowRoot)
            val radio = rowView.findViewById<View>(R.id.configRadio)
            val nameView = rowView.findViewById<TextView>(R.id.configName)
            val metaView = rowView.findViewById<TextView>(R.id.configMeta)
            val editBtn = rowView.findViewById<TextView>(R.id.configEditButton)
            val deleteBtn = rowView.findViewById<TextView>(R.id.configDeleteButton)

            rowGlass.setGlassPreset(
                if (config.active) LiquidGlassPreset.THUMB else LiquidGlassPreset.FIELD,
            )
            rowGlass.setGlassCornerRadiusDp(16f)
            rowGlass.bindBackdrop(backdrop)
            dynamicRowSurfaces.add(rowGlass)

            radio.isSelected = config.active
            nameView.text = config.name
            metaView.text = if (config.hasKey) {
                getString(R.string.api_config_meta_key, config.model, config.keyPreview)
            } else {
                getString(R.string.api_config_meta_missing, config.model)
            }

            rowGlass.setOnClickListener { selectActiveConfig(config.id) }
            editBtn.setOnClickListener { openEditor(config.id) }

            deleteBtn.isEnabled = allowDelete
            deleteBtn.alpha = if (allowDelete) 1f else 0.36f
            deleteBtn.setOnClickListener { queueDeleteConfig(config.id) }

            configListContainer.addView(rowView)
        }

        configEmptyText.isVisible = configs.isEmpty()
        settingsScroll.post { refreshAllGlass() }
    }

    private fun selectActiveConfig(configId: String) {
        activeConfigId = configId
        configs = configs.map { it.copy(active = it.id == configId) }
        clearFeedback()
        renderConfigList()
    }

    private fun queueDeleteConfig(configId: String) {
        if (configs.size <= 1) return
        pendingDeletes.add(configId)
        configs = configs.filterNot { it.id == configId }
        if (activeConfigId == configId) {
            activeConfigId = configs.firstOrNull()?.id.orEmpty()
        }
        configs = configs.map { it.copy(active = it.id == activeConfigId) }
        if (editingConfigId == configId) {
            closeEditor()
        } else {
            renderConfigList()
        }
        showStatus(getString(R.string.api_config_pending_delete))
    }

    private fun openEditor(configId: String?) {
        val existing = configId?.let { id -> configs.firstOrNull { it.id == id } }
        editingConfigId = existing?.id
        clearFeedback()

        editorTitle.setText(if (existing != null) R.string.api_config_editor_edit else R.string.api_config_editor_new)
        editorHint.text = when {
            existing == null -> getString(R.string.api_config_hint_new)
            existing.active -> getString(R.string.api_config_hint_active)
            else -> ""
        }
        configNameInput.setText(existing?.name ?: repository.nextDefaultConfigName(configs))
        apiKeyInput.setText("")
        apiKeyInput.setHint(
            if (existing?.hasKey == true) R.string.api_config_key_hint_keep else R.string.api_config_key_hint_new,
        )
        selectEditorModel(existing?.model ?: activeModelOrDefault(), animate = false)
        configEditorCard.isVisible = true
        configEditorCard.post {
            positionModelThumb(animate = false)
            refreshAllGlass()
            settingsScroll.smoothScrollTo(0, configEditorCard.bottom)
        }
        configNameInput.requestFocus()
    }

    private fun closeEditor() {
        editingConfigId = null
        apiKeyInput.setText("")
        configEditorCard.isVisible = false
        settingsScroll.post { refreshAllGlass() }
    }

    private fun saveEditorConfig() {
        val creating = editingConfigId == null
        clearFeedback()
        try {
            val updated = repository.saveApiConfig(
                configId = editingConfigId,
                rawName = configNameInput.text?.toString().orEmpty(),
                rawModel = editorModel,
                rawKey = apiKeyInput.text?.toString().orEmpty(),
            )
            if (creating) {
                activeConfigId = updated.activeConfigId
            }
            applySnapshot(updated)
            closeEditor()
            showStatus(
                getString(if (creating) R.string.api_config_saved_new else R.string.api_config_saved_edit),
            )
        } catch (error: IllegalArgumentException) {
            showError(error.message ?: getString(R.string.error_unknown))
        }
    }

    private fun saveAllSettings() {
        clearFeedback()
        val active = configs.firstOrNull { it.id == activeConfigId }
        if (selectedProvider == TranslationProvider.DEEPSEEK && active?.hasKey != true) {
            showError(getString(R.string.settings_error_need_key))
            return
        }

        for (id in pendingDeletes.toList()) {
            runCatching { repository.deleteApiConfig(id) }
            pendingDeletes.remove(id)
        }
        if (activeConfigId.isNotEmpty()) {
            repository.activateConfig(activeConfigId)
        }
        repository.setProvider(selectedProvider)

        Toast.makeText(this, R.string.settings_saved, Toast.LENGTH_SHORT).show()
        finish()
    }

    private fun selectEditorModel(model: String, animate: Boolean) {
        editorModel = sanitizeDeepSeekModel(model)
        val activeColor = ContextCompat.getColor(this, R.color.mint_accent_text)
        val inactiveColor = ContextCompat.getColor(this, R.color.glass_text_secondary)
        val isFlash = editorModel == "deepseek-flash"

        modelFlashButton.setTextColor(if (isFlash) activeColor else inactiveColor)
        modelFlashButton.setTypeface(null, if (isFlash) Typeface.BOLD else Typeface.NORMAL)
        modelV4ProButton.setTextColor(if (!isFlash) activeColor else inactiveColor)
        modelV4ProButton.setTypeface(null, if (!isFlash) Typeface.BOLD else Typeface.NORMAL)

        positionModelThumb(animate)
    }

    private fun positionModelThumb(animate: Boolean) {
        val availableWidth = modelTrack.width - modelTrack.paddingLeft - modelTrack.paddingRight
        if (availableWidth <= 0) return

        val segmentWidth = availableWidth / 2f
        val targetWidth = segmentWidth.toInt()
        if (modelThumb.layoutParams.width != targetWidth) {
            modelThumb.layoutParams = modelThumb.layoutParams.apply { width = targetWidth }
        }
        val targetX = if (editorModel == "deepseek-flash") 0f else segmentWidth
        modelThumb.animate().cancel()
        if (animate) {
            modelThumb.scaleX = 0.96f
            modelThumb.scaleY = 0.96f
            modelThumb.animate()
                .translationX(targetX)
                .scaleX(1f)
                .scaleY(1f)
                .setDuration(210L)
                .setInterpolator(OvershootInterpolator(0.7f))
                .setUpdateListener { modelThumb.refreshGlass() }
                .withEndAction { modelThumb.refreshGlass() }
                .start()
        } else {
            modelThumb.translationX = targetX
            modelThumb.scaleX = 1f
            modelThumb.scaleY = 1f
            modelThumb.refreshGlass()
        }
    }

    private fun activeModelOrDefault(): String =
        configs.firstOrNull { it.id == activeConfigId }?.model ?: DEFAULT_DEEPSEEK_MODEL

    private fun showError(message: String) {
        settingsStatus.isVisible = false
        settingsError.text = message
        settingsError.isVisible = true
    }

    private fun showStatus(message: String) {
        settingsError.isVisible = false
        settingsStatus.text = message
        settingsStatus.isVisible = true
    }

    private fun clearFeedback() {
        settingsError.isVisible = false
        settingsStatus.isVisible = false
    }
}
