package com.whs0730.desktranslate

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.graphics.Typeface
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.animation.OvershootInterpolator
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.isVisible
import androidx.core.widget.NestedScrollView
import androidx.core.widget.doAfterTextChanged
import com.google.android.material.progressindicator.CircularProgressIndicator
import com.whs0730.desktranslate.TranslationViewModel.InputValidationError
import com.whs0730.desktranslate.TranslationViewModel.UiState
import com.whs0730.desktranslate.ui.LiquidBackdropView
import com.whs0730.desktranslate.ui.LiquidGlassFrameLayout
import com.whs0730.desktranslate.ui.LiquidGlassHost
import com.whs0730.desktranslate.ui.LiquidGlassLinearLayout
import com.whs0730.desktranslate.ui.LiquidGlassPillView
import com.whs0730.desktranslate.ui.LiquidGlassPreset

class MainActivity : AppCompatActivity() {

    private val viewModel: TranslationViewModel by viewModels()
    private lateinit var settingsRepository: TranslationSettingsRepository

    private lateinit var liquidBackdrop: LiquidBackdropView
    private lateinit var rootScroll: NestedScrollView
    private lateinit var providerBadge: LiquidGlassLinearLayout
    private lateinit var providerBadgeText: TextView
    private lateinit var settingsButton: LiquidGlassFrameLayout
    private lateinit var sourcePanel: LiquidGlassLinearLayout
    private lateinit var sourceInput: EditText
    private lateinit var clearButton: TextView
    private lateinit var byteCounter: TextView
    private lateinit var directionTrack: LiquidGlassFrameLayout
    private lateinit var directionThumb: LiquidGlassPillView
    private lateinit var directionAuto: TextView
    private lateinit var directionZhToEn: TextView
    private lateinit var directionEnToZh: TextView
    private lateinit var translateButton: LiquidGlassLinearLayout
    private lateinit var translateButtonText: TextView
    private lateinit var translateButtonIcon: ImageView
    private lateinit var loadingIndicator: CircularProgressIndicator
    private lateinit var resultPanel: LiquidGlassLinearLayout
    private lateinit var resultTitle: TextView
    private lateinit var resultText: TextView
    private lateinit var resultPhonetic: TextView
    private lateinit var resultAlternatives: TextView
    private lateinit var resultDictDivider: View
    private lateinit var resultDictionaryContainer: LinearLayout
    private lateinit var copyButton: TextView
    private lateinit var statusText: TextView

    private lateinit var glassSurfaces: List<LiquidGlassHost>

    private var selectedDirection = TranslationDirection.AUTO
    private var currentSettings = TranslationSettingsSnapshot()
    private var directionEnabled = true
    private var lastRenderedResult: String? = null
    private var suppressInputInvalidation = false

    private val stateObserver: (UiState) -> Unit = { state -> render(state) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_main)

        settingsRepository = TranslationSettingsRepository(this)
        bindViews()
        setupLiquidGlass()
        applyWindowInsets()
        restoreDirection(savedInstanceState)
        setupDirectionToggle()
        setupInput()
        setupActions()

        refreshSettingsUi()
        viewModel.attach(stateObserver)
        updateInputCounter(sourceInput.text?.toString().orEmpty())
    }

    override fun onResume() {
        super.onResume()
        refreshSettingsUi()
        updateInputCounter(sourceInput.text?.toString().orEmpty())
        refreshAllGlassSurfaces()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString(KEY_DIRECTION, selectedDirection.name)
        super.onSaveInstanceState(outState)
    }

    override fun onRestoreInstanceState(savedInstanceState: Bundle) {
        suppressInputInvalidation = true
        try {
            super.onRestoreInstanceState(savedInstanceState)
        } finally {
            suppressInputInvalidation = false
        }
        updateInputCounter(sourceInput.text?.toString().orEmpty())
    }

    override fun onDestroy() {
        viewModel.detach(stateObserver)
        super.onDestroy()
    }

    private fun bindViews() {
        liquidBackdrop = findViewById(R.id.liquidBackdrop)
        rootScroll = findViewById(R.id.rootScroll)
        providerBadge = findViewById(R.id.providerBadge)
        providerBadgeText = findViewById(R.id.providerBadgeText)
        settingsButton = findViewById(R.id.settingsButton)
        sourcePanel = findViewById(R.id.sourcePanel)
        sourceInput = findViewById(R.id.sourceInput)
        clearButton = findViewById(R.id.clearButton)
        byteCounter = findViewById(R.id.byteCounter)
        directionTrack = findViewById(R.id.directionTrack)
        directionThumb = findViewById(R.id.directionThumb)
        directionAuto = findViewById(R.id.directionAuto)
        directionZhToEn = findViewById(R.id.directionZhToEn)
        directionEnToZh = findViewById(R.id.directionEnToZh)
        translateButton = findViewById(R.id.translateButton)
        translateButtonText = findViewById(R.id.translateButtonText)
        translateButtonIcon = findViewById(R.id.translateButtonIcon)
        loadingIndicator = findViewById(R.id.loadingIndicator)
        resultPanel = findViewById(R.id.resultPanel)
        resultTitle = findViewById(R.id.resultTitle)
        resultText = findViewById(R.id.resultText)
        resultPhonetic = findViewById(R.id.resultPhonetic)
        resultAlternatives = findViewById(R.id.resultAlternatives)
        resultDictDivider = findViewById(R.id.resultDictDivider)
        resultDictionaryContainer = findViewById(R.id.resultDictionaryContainer)
        copyButton = findViewById(R.id.copyButton)
        statusText = findViewById(R.id.statusText)
    }

    private fun setupLiquidGlass() {
        providerBadge.setGlassPreset(LiquidGlassPreset.BUTTON_CLEAR)
        settingsButton.setGlassPreset(LiquidGlassPreset.BUTTON_CLEAR)
        directionTrack.setGlassPreset(LiquidGlassPreset.TRACK)
        directionThumb.setGlassPreset(LiquidGlassPreset.THUMB)
        sourcePanel.setGlassPreset(LiquidGlassPreset.PANEL)
        translateButton.setGlassPreset(LiquidGlassPreset.BUTTON_MINT)
        resultPanel.setGlassPreset(LiquidGlassPreset.PANEL)

        glassSurfaces = listOf(
            providerBadge,
            settingsButton,
            directionTrack,
            directionThumb,
            sourcePanel,
            translateButton,
            resultPanel,
        )
        glassSurfaces.forEach { it.bindBackdrop(liquidBackdrop) }

        rootScroll.setOnScrollChangeListener { _, _, _, _, _ ->
            refreshAllGlassSurfaces()
        }
    }

    private fun refreshAllGlassSurfaces() {
        glassSurfaces.forEach { it.refreshGlass() }
    }

    /**
     * Keep the form above the navigation bar and the soft keyboard. The
     * ScrollView absorbs the remaining height so every control stays
     * reachable in portrait with the IME open.
     */
    private fun applyWindowInsets() {
        ViewCompat.setOnApplyWindowInsetsListener(rootScroll) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            view.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, ime.bottom))
            view.post { refreshAllGlassSurfaces() }
            insets
        }
        ViewCompat.requestApplyInsets(rootScroll)
    }

    private fun restoreDirection(savedInstanceState: Bundle?) {
        selectedDirection = savedInstanceState
            ?.getString(KEY_DIRECTION)
            ?.let { name -> runCatching { TranslationDirection.valueOf(name) }.getOrNull() }
            ?: TranslationDirection.AUTO
    }

    private fun refreshSettingsUi() {
        currentSettings = settingsRepository.load()
        providerBadgeText.text = when (currentSettings.provider) {
            TranslationProvider.MYMEMORY -> getString(R.string.provider_badge_mymemory)
            TranslationProvider.DEEPSEEK -> getString(
                R.string.provider_badge_deepseek,
                deepSeekModelLabel(currentSettings.activeModel).removePrefix("DeepSeek "),
            )
        }
    }

    private fun setupDirectionToggle() {
        val options = listOf(
            directionAuto to TranslationDirection.AUTO,
            directionZhToEn to TranslationDirection.ZH_TO_EN,
            directionEnToZh to TranslationDirection.EN_TO_ZH,
        )
        for ((view, direction) in options) {
            view.setOnClickListener {
                if (directionEnabled) {
                    selectDirection(direction, animate = true)
                }
            }
        }
        directionTrack.addOnLayoutChangeListener { _, left, _, right, _, oldLeft, _, oldRight, _ ->
            if (right - left != oldRight - oldLeft || directionThumb.layoutParams.width <= 0) {
                positionDirectionThumb(animate = false)
            }
        }
        updateDirectionStyles()
        directionTrack.post { positionDirectionThumb(animate = false) }
    }

    private fun selectDirection(direction: TranslationDirection, animate: Boolean) {
        val changed = selectedDirection != direction
        selectedDirection = direction
        updateDirectionStyles()
        positionDirectionThumb(animate = animate)
        if (changed) {
            viewModel.clearValidationError()
        }
    }

    private fun updateDirectionStyles() {
        val activeColor = ContextCompat.getColor(this, R.color.mint_accent_text)
        val inactiveColor = ContextCompat.getColor(this, R.color.glass_text_secondary)
        val items = listOf(
            directionAuto to TranslationDirection.AUTO,
            directionZhToEn to TranslationDirection.ZH_TO_EN,
            directionEnToZh to TranslationDirection.EN_TO_ZH,
        )
        for ((view, direction) in items) {
            val active = direction == selectedDirection
            view.isSelected = active
            view.setTextColor(if (active) activeColor else inactiveColor)
            view.setTypeface(null, if (active) Typeface.BOLD else Typeface.NORMAL)
        }
    }

    private fun positionDirectionThumb(animate: Boolean) {
        val availableWidth = directionTrack.width - directionTrack.paddingLeft - directionTrack.paddingRight
        if (availableWidth <= 0) return

        val segmentWidth = availableWidth / 3f
        val targetWidth = segmentWidth.toInt()
        if (directionThumb.layoutParams.width != targetWidth) {
            directionThumb.layoutParams = directionThumb.layoutParams.apply { width = targetWidth }
        }

        val targetX = directionIndex(selectedDirection) * segmentWidth
        directionThumb.animate().cancel()
        if (animate) {
            directionThumb.scaleX = 0.95f
            directionThumb.scaleY = 0.95f
            directionThumb.animate()
                .translationX(targetX)
                .scaleX(1f)
                .scaleY(1f)
                .setDuration(THUMB_ANIMATION_MS)
                .setInterpolator(OvershootInterpolator(0.75f))
                .setUpdateListener { directionThumb.refreshGlass() }
                .withEndAction { directionThumb.refreshGlass() }
                .start()
        } else {
            directionThumb.translationX = targetX
            directionThumb.scaleX = 1f
            directionThumb.scaleY = 1f
            directionThumb.refreshGlass()
        }
    }

    private fun setupInput() {
        sourceInput.doAfterTextChanged { editable ->
            updateInputCounter(editable?.toString().orEmpty())
            if (!suppressInputInvalidation) {
                lastRenderedResult = null
                viewModel.invalidate()
            }
            sourcePanel.post { refreshAllGlassSurfaces() }
        }
    }

    private fun setupActions() {
        settingsButton.setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }
        providerBadge.setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }
        clearButton.setOnClickListener { clearSource() }
        translateButton.setOnClickListener { submitTranslation() }
        copyButton.setOnClickListener { copyTranslation() }
    }

    private fun clearSource() {
        suppressInputInvalidation = true
        try {
            sourceInput.text?.clear()
        } finally {
            suppressInputInvalidation = false
        }
        updateInputCounter("")
        lastRenderedResult = null
        viewModel.invalidate()
    }

    private fun submitTranslation() {
        val text = sourceInput.text?.toString().orEmpty().trim()
        val requestConfig = settingsRepository.buildRequestConfig()
        when {
            text.isEmpty() -> {
                viewModel.showValidationError(InputValidationError.EMPTY)
                sourceInput.requestFocus()
            }

            requestConfig.provider == TranslationProvider.MYMEMORY && !isWithinQueryLimit(text) -> {
                viewModel.showValidationError(InputValidationError.TOO_LONG_MYMEMORY)
                sourceInput.requestFocus()
            }

            requestConfig.provider == TranslationProvider.DEEPSEEK && unicodeCharCount(text) > MAX_DEEPSEEK_CHARS -> {
                viewModel.showValidationError(InputValidationError.TOO_LONG_DEEPSEEK)
                sourceInput.requestFocus()
            }

            else -> {
                when (validateManualDirection(text, selectedDirection)) {
                    DirectionMismatch.USE_EN_TO_ZH -> {
                        viewModel.showValidationError(InputValidationError.USE_EN_TO_ZH)
                    }

                    DirectionMismatch.USE_ZH_TO_EN -> {
                        viewModel.showValidationError(InputValidationError.USE_ZH_TO_EN)
                    }

                    null -> {
                        if (requestConfig.provider == TranslationProvider.DEEPSEEK &&
                            requestConfig.deepSeekApiKey.isNullOrBlank()
                        ) {
                            viewModel.showValidationError(InputValidationError.MISSING_DEEPSEEK_KEY)
                        } else {
                            viewModel.translate(text, selectedDirection, requestConfig)
                        }
                    }
                }
            }
        }
    }

    private fun copyTranslation() {
        val translation = viewModel.state.result ?: return
        val clipboard = getSystemService(ClipboardManager::class.java) ?: return
        clipboard.setPrimaryClip(ClipData.newPlainText(getString(R.string.clip_label), translation))
        Toast.makeText(this, R.string.copied, Toast.LENGTH_SHORT).show()
    }

    private fun render(state: UiState) {
        val loading = state.isLoading
        translateButton.isEnabled = !loading
        translateButton.alpha = if (loading) 0.72f else 1f
        translateButtonText.setText(if (loading) R.string.translating else R.string.translate)
        translateButtonIcon.isVisible = !loading
        loadingIndicator.isVisible = loading
        setDirectionEnabled(!loading)

        val canCopy = !loading && !state.result.isNullOrBlank()
        copyButton.isEnabled = canCopy
        copyButton.alpha = if (canCopy) 1f else 0.38f

        val placeholderColor = ContextCompat.getColor(this, R.color.glass_text_placeholder)
        val contentColor = ContextCompat.getColor(this, R.color.glass_text_primary)

        val defaultMinHeight = (132 * resources.displayMetrics.density).toInt()
        when {
            loading -> {
                resultTitle.setText(R.string.result_title)
                resultText.minHeight = defaultMinHeight
                resultText.setText(R.string.result_loading)
                resultText.setTextColor(placeholderColor)
                renderDictionaryDetails(null, emptyList(), emptyList())
                lastRenderedResult = null
                hideStatus()
            }

            state.validationError != null -> {
                resultTitle.setText(R.string.result_title)
                resultText.minHeight = defaultMinHeight
                resultText.setText(R.string.result_placeholder)
                resultText.setTextColor(placeholderColor)
                renderDictionaryDetails(null, emptyList(), emptyList())
                showStatus(getString(validationMessage(state.validationError)))
                lastRenderedResult = null
            }

            state.error != null -> {
                resultTitle.setText(R.string.result_title)
                resultText.minHeight = defaultMinHeight
                resultText.setText(R.string.result_placeholder)
                resultText.setTextColor(placeholderColor)
                renderDictionaryDetails(null, emptyList(), emptyList())
                showStatus(getString(errorMessage(state.error)))
                lastRenderedResult = null
            }

            state.result != null -> {
                val hasExtras = !state.phonetic.isNullOrBlank() ||
                    state.alternatives.isNotEmpty() ||
                    state.entries.isNotEmpty()
                resultTitle.text = getString(
                    R.string.result_with_direction,
                    directionLabel(state.direction ?: selectedDirection),
                )
                resultText.minHeight = if (hasExtras) 0 else defaultMinHeight
                resultText.text = state.result
                resultText.setTextColor(contentColor)
                renderDictionaryDetails(state.phonetic, state.alternatives, state.entries)
                hideStatus()
                scrollToResultOnce(state.result)
            }

            else -> {
                resultTitle.setText(R.string.result_title)
                resultText.minHeight = defaultMinHeight
                resultText.setText(R.string.result_placeholder)
                resultText.setTextColor(placeholderColor)
                renderDictionaryDetails(null, emptyList(), emptyList())
                hideStatus()
                lastRenderedResult = null
            }
        }
        rootScroll.post { refreshAllGlassSurfaces() }
    }

    private fun renderDictionaryDetails(
        phonetic: String?,
        alternatives: List<String>,
        entries: List<DictionaryEntry>,
    ) {
        val hasPhonetic = !phonetic.isNullOrBlank()
        resultPhonetic.text = phonetic.orEmpty()
        resultPhonetic.isVisible = hasPhonetic

        val cleanAlts = alternatives.filter { it.isNotBlank() }
        resultAlternatives.text = if (cleanAlts.isNotEmpty()) {
            getString(R.string.result_alternatives, cleanAlts.joinToString(" · "))
        } else {
            ""
        }
        resultAlternatives.isVisible = cleanAlts.isNotEmpty()

        resultDictionaryContainer.removeAllViews()
        val cleanEntries = entries.filter { it.pos.isNotBlank() && it.meanings.isNotEmpty() }
        if (cleanEntries.isNotEmpty()) {
            val inflater = LayoutInflater.from(this)
            for (entry in cleanEntries) {
                val row = inflater.inflate(R.layout.item_dictionary_entry, resultDictionaryContainer, false)
                row.findViewById<TextView>(R.id.dictPos).text = entry.pos
                row.findViewById<TextView>(R.id.dictMeanings).text = entry.meanings.joinToString("；")
                resultDictionaryContainer.addView(row)
            }
        }
        resultDictDivider.isVisible = cleanEntries.isNotEmpty()
        resultDictionaryContainer.isVisible = cleanEntries.isNotEmpty()
    }

    private fun scrollToResultOnce(result: String) {
        if (result == lastRenderedResult) return
        lastRenderedResult = result
        rootScroll.post {
            val offset = (16 * resources.displayMetrics.density).toInt()
            rootScroll.smoothScrollTo(0, (resultPanel.top - offset).coerceAtLeast(0))
        }
    }

    private fun setDirectionEnabled(enabled: Boolean) {
        directionEnabled = enabled
        directionTrack.isEnabled = enabled
        directionTrack.alpha = if (enabled) 1f else 0.58f
        directionAuto.isEnabled = enabled
        directionZhToEn.isEnabled = enabled
        directionEnToZh.isEnabled = enabled
    }

    private fun updateInputCounter(text: String) {
        val trimmed = text.trim()
        val overLimit = if (currentSettings.provider == TranslationProvider.DEEPSEEK) {
            val chars = unicodeCharCount(trimmed)
            byteCounter.text = getString(R.string.char_counter, chars)
            chars > MAX_DEEPSEEK_CHARS
        } else {
            val bytes = utf8ByteCount(trimmed)
            byteCounter.text = getString(R.string.byte_counter, bytes)
            bytes > MAX_QUERY_BYTES
        }
        val colorRes = if (overLimit) R.color.glass_error else R.color.glass_text_placeholder
        byteCounter.setTextColor(ContextCompat.getColor(this, colorRes))
    }

    private fun showStatus(message: String) {
        statusText.text = message
        statusText.isVisible = true
    }

    private fun hideStatus() {
        statusText.isVisible = false
    }

    private fun directionIndex(direction: TranslationDirection): Int = when (direction) {
        TranslationDirection.AUTO -> 0
        TranslationDirection.ZH_TO_EN -> 1
        TranslationDirection.EN_TO_ZH -> 2
    }

    private fun directionLabel(direction: TranslationDirection): String = when (direction) {
        TranslationDirection.AUTO -> getString(R.string.direction_auto)
        TranslationDirection.ZH_TO_EN -> getString(R.string.direction_zh_to_en)
        TranslationDirection.EN_TO_ZH -> getString(R.string.direction_en_to_zh)
    }

    private fun validationMessage(error: InputValidationError): Int = when (error) {
        InputValidationError.EMPTY -> R.string.error_empty
        InputValidationError.TOO_LONG_MYMEMORY -> R.string.error_too_long
        InputValidationError.TOO_LONG_DEEPSEEK -> R.string.error_too_long_deepseek
        InputValidationError.USE_EN_TO_ZH -> R.string.error_direction_use_en_to_zh
        InputValidationError.USE_ZH_TO_EN -> R.string.error_direction_use_zh_to_en
        InputValidationError.MISSING_DEEPSEEK_KEY -> R.string.error_missing_api_key
    }

    private fun errorMessage(kind: TranslationFailureKind): Int = when (kind) {
        TranslationFailureKind.NETWORK -> R.string.error_network
        TranslationFailureKind.TIMEOUT -> R.string.error_timeout
        TranslationFailureKind.SERVICE -> R.string.error_service
        TranslationFailureKind.EMPTY_RESULT -> R.string.error_empty_result
        TranslationFailureKind.INVALID_RESPONSE -> R.string.error_invalid_response
        TranslationFailureKind.MISSING_API_KEY -> R.string.error_missing_api_key
        TranslationFailureKind.AUTH_INVALID -> R.string.error_auth_invalid
        TranslationFailureKind.INSUFFICIENT_BALANCE -> R.string.error_insufficient_balance
        TranslationFailureKind.RATE_LIMITED -> R.string.error_rate_limited
        TranslationFailureKind.INCOMPLETE_RESULT -> R.string.error_incomplete_result
        TranslationFailureKind.UNKNOWN -> R.string.error_unknown
    }

    private companion object {
        const val KEY_DIRECTION = "selected_direction"
        const val THUMB_ANIMATION_MS = 230L
    }
}
