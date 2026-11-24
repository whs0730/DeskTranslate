package com.whs0730.desktranslate

import com.whs0730.desktranslate.TranslationViewModel.InputValidationError
import com.whs0730.desktranslate.TranslationViewModel.UiState
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TranslationViewModelTest {

    @Test
    fun invalidateClearsPreviousResultImmediately() {
        val executor = Executors.newCachedThreadPool()
        val done = CountDownLatch(1)
        val viewModel = TranslationViewModel(
            translator = { text, _, _ ->
                MyMemoryTranslator.Result(
                    text = "你好",
                    direction = TranslationDirection.EN_TO_ZH,
                    sourceLanguage = "en",
                    targetLanguage = "zh-CN",
                ).also { require(text == "Hello") }
            },
            executor = executor,
            dispatchToMain = { block ->
                block()
                done.countDown()
            },
        )

        try {
            viewModel.translate("Hello", TranslationDirection.EN_TO_ZH)
            assertTrue(done.await(2, TimeUnit.SECONDS))
            assertEquals("你好", viewModel.state.result)

            viewModel.invalidate()

            assertEquals(UiState(), viewModel.state)
            assertNull(viewModel.state.result)
            assertFalse(viewModel.state.isLoading)
        } finally {
            executor.shutdownNow()
        }
    }

    @Test
    fun editingSourceDuringInFlightRequestDropsLateTranslationResult() {
        val executor = Executors.newCachedThreadPool()
        val requestStarted = CountDownLatch(1)
        val releaseRequest = CountDownLatch(1)
        val workerFinished = CountDownLatch(1)
        val observed = mutableListOf<UiState>()

        val viewModel = TranslationViewModel(
            translator = { _, _, _ ->
                requestStarted.countDown()
                while (true) {
                    try {
                        releaseRequest.await(2, TimeUnit.SECONDS)
                        break
                    } catch (_: InterruptedException) {
                        // Continue until released to verify generation guard.
                    }
                }
                try {
                    MyMemoryTranslator.Result(
                        text = "旧译文不应回来",
                        direction = TranslationDirection.EN_TO_ZH,
                        sourceLanguage = "en",
                        targetLanguage = "zh-CN",
                    )
                } finally {
                    workerFinished.countDown()
                }
            },
            executor = executor,
            dispatchToMain = { block -> block() },
        )

        try {
            viewModel.attach { observed.add(it) }
            viewModel.translate("Hello", TranslationDirection.EN_TO_ZH)
            assertTrue(requestStarted.await(2, TimeUnit.SECONDS))
            assertTrue(viewModel.state.isLoading)

            viewModel.invalidate()
            assertFalse(viewModel.state.isLoading)
            assertNull(viewModel.state.result)

            releaseRequest.countDown()
            assertTrue(workerFinished.await(2, TimeUnit.SECONDS))
            Thread.sleep(50)

            assertEquals(UiState(), viewModel.state)
            assertTrue(observed.none { it.result == "旧译文不应回来" })
        } finally {
            executor.shutdownNow()
        }
    }

    @Test
    fun newRequestAfterInvalidateIsNotOverwrittenByStaleSlowRequest() {
        val executor = Executors.newCachedThreadPool()
        val firstStarted = CountDownLatch(1)
        val releaseFirst = CountDownLatch(1)
        val secondDelivered = CountDownLatch(1)
        val firstFinished = CountDownLatch(1)

        val viewModel = TranslationViewModel(
            translator = { text, _, _ ->
                if (text == "old") {
                    firstStarted.countDown()
                    while (true) {
                        try {
                            releaseFirst.await(2, TimeUnit.SECONDS)
                            break
                        } catch (_: InterruptedException) {
                            // Keep waiting so the old request finishes strictly after the new one.
                        }
                    }
                    firstFinished.countDown()
                    MyMemoryTranslator.Result("旧结果", TranslationDirection.EN_TO_ZH, "en", "zh-CN")
                } else {
                    MyMemoryTranslator.Result("新结果", TranslationDirection.EN_TO_ZH, "en", "zh-CN")
                }
            },
            executor = executor,
            dispatchToMain = { block -> block() },
        )

        try {
            viewModel.attach { state ->
                if (state.result == "新结果") secondDelivered.countDown()
            }
            viewModel.translate("old", TranslationDirection.EN_TO_ZH)
            assertTrue(firstStarted.await(2, TimeUnit.SECONDS))

            viewModel.invalidate()
            viewModel.translate("new", TranslationDirection.EN_TO_ZH)
            assertTrue(secondDelivered.await(2, TimeUnit.SECONDS))
            assertEquals("新结果", viewModel.state.result)

            releaseFirst.countDown()
            assertTrue(firstFinished.await(2, TimeUnit.SECONDS))
            Thread.sleep(50)

            assertEquals("新结果", viewModel.state.result)
        } finally {
            executor.shutdownNow()
        }
    }

    @Test
    fun validationErrorClearsStaleResultAndSkipsNetworkCall() {
        val calls = AtomicInteger(0)
        val executor = Executors.newCachedThreadPool()
        val done = CountDownLatch(1)
        val viewModel = TranslationViewModel(
            translator = { _, _, _ ->
                calls.incrementAndGet()
                MyMemoryTranslator.Result("你好", TranslationDirection.EN_TO_ZH, "en", "zh-CN")
            },
            executor = executor,
            dispatchToMain = { block -> block() },
        )

        try {
            viewModel.attach { state ->
                if (state.result != null) done.countDown()
            }
            viewModel.translate("Hello", TranslationDirection.EN_TO_ZH)
            assertTrue(done.await(2, TimeUnit.SECONDS))
            assertEquals(1, calls.get())

            viewModel.showValidationError(InputValidationError.USE_EN_TO_ZH)

            assertEquals(1, calls.get())
            assertNull(viewModel.state.result)
            assertEquals(InputValidationError.USE_EN_TO_ZH, viewModel.state.validationError)

            viewModel.clearValidationError()
            assertNull(viewModel.state.validationError)
        } finally {
            executor.shutdownNow()
        }
    }

    @Test
    fun passesSelectedDeepSeekRequestConfigToTranslator() {
        val executor = Executors.newCachedThreadPool()
        val done = CountDownLatch(1)
        val capturedConfig = AtomicReference<TranslationRequestConfig?>()
        val viewModel = TranslationViewModel(
            translator = { _, _, config ->
                capturedConfig.set(config)
                MyMemoryTranslator.Result("你好", TranslationDirection.EN_TO_ZH, "en", "zh-CN", "DeepSeek")
            },
            executor = executor,
            dispatchToMain = { block -> block() },
        )

        try {
            viewModel.attach { state ->
                if (state.result != null) done.countDown()
            }
            val deepSeekConfig = TranslationRequestConfig(
                provider = TranslationProvider.DEEPSEEK,
                deepSeekModel = "deepseek-v4-pro",
                deepSeekApiKey = "sk-test123456",
            )
            viewModel.translate("Hello", TranslationDirection.EN_TO_ZH, deepSeekConfig)
            assertTrue(done.await(2, TimeUnit.SECONDS))
            assertEquals(deepSeekConfig, capturedConfig.get())
        } finally {
            executor.shutdownNow()
        }
    }

    @Test
    fun reattachingObserverPreservesStateAcrossRotation() {
        val executor = Executors.newCachedThreadPool()
        val done = CountDownLatch(1)
        val viewModel = TranslationViewModel(
            translator = { _, _, _ ->
                MyMemoryTranslator.Result("世界", TranslationDirection.EN_TO_ZH, "en", "zh-CN")
            },
            executor = executor,
            dispatchToMain = { block -> block() },
        )

        try {
            val firstObserver: (UiState) -> Unit = { state ->
                if (state.result == "世界") done.countDown()
            }
            viewModel.attach(firstObserver)
            viewModel.translate("World", TranslationDirection.EN_TO_ZH)
            assertTrue(done.await(2, TimeUnit.SECONDS))

            viewModel.detach(firstObserver)
            var restored: UiState? = null
            viewModel.attach { restored = it }

            assertEquals("世界", restored?.result)
            assertEquals(TranslationDirection.EN_TO_ZH, restored?.direction)
            assertFalse(restored?.isLoading ?: true)
        } finally {
            executor.shutdownNow()
        }
    }
}
