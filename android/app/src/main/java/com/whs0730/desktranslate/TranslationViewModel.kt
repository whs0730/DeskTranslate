package com.whs0730.desktranslate

import android.os.Handler
import android.os.Looper
import androidx.lifecycle.ViewModel
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.Future
import java.util.concurrent.atomic.AtomicInteger

/**
 * Owns the background request so a rotation during translation does not lose
 * the in-flight call or leak the Activity. All state changes are delivered on
 * the main thread.
 */
class TranslationViewModel internal constructor(
    private val translator: (String, TranslationDirection, TranslationRequestConfig) -> MyMemoryTranslator.Result,
    private val executor: ExecutorService,
    private val dispatchToMain: (() -> Unit) -> Unit,
) : ViewModel() {

    constructor() : this(
        translator = { text, direction, config ->
            when (config.provider) {
                TranslationProvider.MYMEMORY -> MyMemoryTranslator.translate(text, direction)
                TranslationProvider.DEEPSEEK -> DeepSeekTranslator.translate(
                    text = text,
                    direction = direction,
                    apiKey = config.deepSeekApiKey,
                    model = config.deepSeekModel,
                )
            }
        },
        executor = Executors.newCachedThreadPool(),
        dispatchToMain = defaultMainDispatcher(),
    )

    enum class InputValidationError {
        EMPTY,
        TOO_LONG_MYMEMORY,
        TOO_LONG_DEEPSEEK,
        USE_EN_TO_ZH,
        USE_ZH_TO_EN,
        MISSING_DEEPSEEK_KEY,
    }

    data class UiState(
        val isLoading: Boolean = false,
        val result: String? = null,
        val direction: TranslationDirection? = null,
        val phonetic: String? = null,
        val entries: List<DictionaryEntry> = emptyList(),
        val alternatives: List<String> = emptyList(),
        val error: TranslationFailureKind? = null,
        val validationError: InputValidationError? = null,
    )

    private val generation = AtomicInteger(0)

    private var activeTask: Future<*>? = null
    private var listener: ((UiState) -> Unit)? = null

    var state: UiState = UiState()
        private set

    fun attach(observer: (UiState) -> Unit) {
        listener = observer
        observer(state)
    }

    fun detach(observer: (UiState) -> Unit) {
        if (listener === observer) listener = null
    }

    /**
     * Cancel any in-flight request and drop stale results/errors immediately.
     * Used when the user edits the source text or taps Clear.
     */
    fun invalidate() {
        generation.incrementAndGet()
        activeTask?.cancel(true)
        activeTask = null
        if (state != UiState()) {
            publish(UiState())
        }
    }

    /**
     * Present a pre-submission validation message, cancelling any stale result
     * or in-flight request so old translations cannot stay copyable.
     */
    fun showValidationError(validationError: InputValidationError) {
        generation.incrementAndGet()
        activeTask?.cancel(true)
        activeTask = null
        publish(UiState(validationError = validationError))
    }

    /** Dismiss a pre-submission validation message (e.g. after switching direction). */
    fun clearValidationError() {
        if (state.validationError != null) {
            publish(state.copy(validationError = null))
        }
    }

    fun translate(
        text: String,
        direction: TranslationDirection,
        config: TranslationRequestConfig = TranslationRequestConfig(),
    ) {
        if (state.isLoading) return

        val requestId = generation.incrementAndGet()
        publish(UiState(isLoading = true))

        activeTask = executor.submit {
            val next = try {
                val result = translator(text, direction, config)
                UiState(
                    result = result.text,
                    direction = result.direction,
                    phonetic = result.phonetic,
                    entries = result.entries,
                    alternatives = result.alternatives,
                )
            } catch (error: TranslationFailure) {
                UiState(error = error.kind)
            } catch (error: Exception) {
                UiState(error = TranslationFailureKind.UNKNOWN)
            }

            dispatchToMain {
                if (requestId == generation.get()) {
                    activeTask = null
                    publish(next)
                }
            }
        }
    }

    private fun publish(next: UiState) {
        state = next
        val current = listener ?: return
        dispatchToMain {
            if (listener === current) current(next)
        }
    }

    override fun onCleared() {
        generation.incrementAndGet()
        activeTask?.cancel(true)
        activeTask = null
        listener = null
        executor.shutdownNow()
        super.onCleared()
    }

    private companion object {
        fun defaultMainDispatcher(): (() -> Unit) -> Unit {
            val mainHandler = Handler(Looper.getMainLooper())
            return { block ->
                if (Looper.myLooper() == Looper.getMainLooper()) {
                    block()
                } else {
                    mainHandler.post(block)
                }
            }
        }
    }
}
