import json
import sys
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from desktranslate.selftest import subtitle_self_test
from desktranslate.subtitles import (
    MODEL_FILES,
    MODEL_NAME,
    SubtitleService,
    committed_rest,
    content_text,
    create_recognizer,
    is_noise,
    readable_english,
    stable_prefix,
)
from desktranslate.translator import TranslationResult


class SubtitleTests(unittest.TestCase):
    def test_uppercase_transcript_is_readable_and_mixed_case_is_preserved(self) -> None:
        self.assertEqual(readable_english("HELLO I AM HERE"), "Hello I am here")
        self.assertEqual(readable_english("Hello, OpenAI"), "Hello, OpenAI")

    def test_content_text_ignores_punctuation_and_case(self) -> None:
        self.assertEqual(content_text("Hello, world."), "helloworld")
        self.assertEqual(content_text("你好，世界。"), "你好世界")
        self.assertEqual(content_text("  "), "")

    def test_is_noise_rejects_silence_hallucinations(self) -> None:
        for text in ("", "...", "Thank you.", "Thanks for watching", "谢谢观看", "K"):
            with self.subTest(text=text):
                self.assertTrue(is_noise(text))
        for text in ("我们用这个方案", "next line", "Mr. Smith"):
            with self.subTest(text=text):
                self.assertFalse(is_noise(text))

    def test_committed_rest_cuts_only_matching_prefix(self) -> None:
        committed = content_text("We shipped the early commit.")
        self.assertEqual(committed_rest("We shipped the early commit. Now", committed), ". Now")
        self.assertEqual(committed_rest("Totally different words", committed), "Totally different words")

    def test_stable_prefix_commits_sentence_seen_complete_in_previous_partial(self) -> None:
        text = "We shipped the early commit. Now"
        self.assertEqual(stable_prefix(text, text, ""), "We shipped the early commit.")
        # 上一帧这一句还没说完，不能定稿。
        self.assertEqual(stable_prefix(text, "We shipped the early commi", ""), "")
        # 已经定稿过的句子不重复定稿。
        committed = content_text("We shipped the early commit.")
        self.assertEqual(stable_prefix(text, text, committed), "")

    def test_stable_prefix_keeps_abbreviated_titles_together(self) -> None:
        text = "Mr. Smith arrived. Then"
        self.assertEqual(stable_prefix(text, text, ""), "Mr. Smith arrived.")

    def test_clear_invalidates_inflight_segment_without_stopping(self) -> None:
        events = []
        service = SubtitleService(Path('.'), Mock(), events.append)
        session = service.start(1)
        epoch = service.clear(session)
        service._send(session, {'kind': 'translation', 'segment': 0, 'text': 'old'}, 0)
        service._send(session, {'kind': 'partial', 'segment': 0, 'text': 'new'}, epoch)
        self.assertEqual([event['text'] for event in events], ['new'])
        service.stop(session)

    def test_slow_translations_can_run_in_parallel(self) -> None:
        both_started = threading.Event()
        release = threading.Event()
        closed = threading.Event()
        calls = []
        calls_lock = threading.Lock()

        class Recognizer:
            def __init__(self): self.ready = False; self.segment = 0
            def create_stream(self): return self
            def accept_waveform(self, rate, samples): self.ready = True
            def is_ready(self, stream): return self.ready
            def decode_stream(self, stream): self.ready = False
            def get_result(self, stream): return f'hello {self.segment}'
            def is_endpoint(self, stream): return True
            def reset(self, stream): self.segment += 1

        class Capture:
            def __init__(self, callback): self.callback = callback
            def __enter__(self):
                for _ in range(2): self.callback(np.zeros(1600, dtype=np.int16).tobytes(), 1600, None, 0)
                return self
            def __exit__(self, *args): closed.set()
            def is_active(self): return True

        class Audio:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def get_device_info_by_index(self, index):
                return {'isLoopbackDevice': True, 'defaultSampleRate': 16000, 'maxInputChannels': 1}
            def open(self, **kwargs): return Capture(kwargs['stream_callback'])

        def translate(text, direction, timeout):
            with calls_lock:
                calls.append(text)
                if len(calls) == 2: both_started.set()
            release.wait(3)
            return TranslationResult('测试', 'en', 'zh-CN', 'test')

        audio_module = SimpleNamespace(PyAudio=Audio, paInt16=8, paContinue=0, paComplete=1)
        with patch.dict(sys.modules, {'pyaudiowpatch': audio_module}), patch('desktranslate.subtitles.create_recognizer', return_value=Recognizer()):
            service = SubtitleService(Path('.'), translate, lambda event: None)
            session = service.start(1)
            service.activate(session)
            try:
                self.assertTrue(both_started.wait(3), f'并行翻译未启动: {calls}')
            finally:
                release.set()
                service.stop(session)
                self.assertTrue(closed.wait(3))
    def test_start_requires_activation_and_old_session_cannot_stop_new_one(self) -> None:
        service = SubtitleService(Path('.'), Mock(), Mock())
        with patch('desktranslate.subtitles.create_recognizer') as create:
            old = service.start(1)
            service.stop(old)
            current = service.start(1)
            service.stop(old)
            with self.assertRaises(ValueError):
                service.start(1)
            service.stop(current)
            create.assert_not_called()

    def test_audio_final_translation_and_stop_use_matching_session(self) -> None:
        closed = threading.Event()
        translated = threading.Event()
        events = []

        class Recognizer:
            def __init__(self):
                self.ready = False
                self.endpoint = False

            def create_stream(self):
                return self

            def accept_waveform(self, rate, samples):
                self.ready = True

            def is_ready(self, stream):
                return self.ready

            def decode_stream(self, stream):
                self.ready = False
                self.endpoint = True

            def get_result(self, stream):
                return 'hello world'

            def is_endpoint(self, stream):
                return self.endpoint

            def reset(self, stream):
                self.endpoint = False

        class Capture:
            def __init__(self, callback):
                self.callback = callback

            def __enter__(self):
                self.callback(np.zeros(4800 * 2, dtype=np.int16).tobytes(), 4800, None, 0)
                return self

            def __exit__(self, *args):
                closed.set()

            def is_active(self):
                return True

        class Audio:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def get_device_info_by_index(self, index):
                return {'isLoopbackDevice': True, 'defaultSampleRate': 48000, 'maxInputChannels': 2}

            def open(self, **kwargs):
                return Capture(kwargs['stream_callback'])

        def emit(event):
            events.append(event)
            if event['kind'] == 'translation':
                translated.set()

        audio_module = SimpleNamespace(PyAudio=Audio, paInt16=8, paContinue=0, paComplete=1)
        translate = Mock(return_value=TranslationResult('你好世界', 'en', 'zh-CN', 'test'))
        with patch.dict(sys.modules, {'pyaudiowpatch': audio_module}), patch('desktranslate.subtitles.create_recognizer', return_value=Recognizer()):
            service = SubtitleService(Path('.'), translate, emit)
            session = service.start(1)
            service.activate(session)
            self.assertTrue(translated.wait(3))
            service.stop(session)
            self.assertTrue(closed.wait(3))
        self.assertTrue(any(event['kind'] == 'final' and event['segment'] == 0 for event in events))
        self.assertTrue(any(event['kind'] == 'translation' and event['segment'] == 0 and event['text'] == '你好世界' for event in events))
        before = len(events)
        service._send(session, {'kind': 'translation', 'segment': 0, 'text': 'late'})
        self.assertEqual(len(events), before)

    def test_self_test_reports_missing_model_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
                patch('desktranslate.resources.subtitle_model_path', return_value=Path(directory)):
            report = Path(directory) / 'report.json'
            self.assertEqual(subtitle_self_test(report), 1)
            payload = json.loads(report.read_text(encoding='utf-8'))
        self.assertFalse(payload['ok'])
        self.assertIn('缺少模型文件', payload['error'])

    def test_self_test_loads_bundled_model(self) -> None:
        model = Path(__file__).resolve().parents[1] / 'models' / MODEL_NAME
        if any(not (model / name).is_file() for name in MODEL_FILES.values()):
            self.skipTest('本地字幕模型尚未下载')
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'report.json'
            self.assertEqual(subtitle_self_test(report), 0)
            payload = json.loads(report.read_text(encoding='utf-8'))
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['recognizer'])
        self.assertTrue(payload['decode'])
        self.assertTrue(all(size > 0 for size in payload['files'].values()))

    def test_downloaded_model_recognizes_bundled_sample(self) -> None:
        model = Path(__file__).resolve().parents[1] / 'models' / MODEL_NAME
        sample = model.parent / 'sherpa-onnx-streaming-zipformer-en-20M-2023-02-17' / 'test_wavs' / '0.wav'
        if not sample.is_file() or any(not (model / name).is_file() for name in MODEL_FILES.values()):
            self.skipTest('本地字幕模型或示例音频尚未下载')
        recognizer = create_recognizer(model)
        stream = recognizer.create_stream()
        with wave.open(str(sample), 'rb') as audio:
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16).astype(np.float32) / 32768
            self.assertEqual(audio.getframerate(), 16000)
            stream.accept_waveform(48000, np.repeat(samples, 3))
        stream.input_finished()
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        result = recognizer.get_result(stream)
        self.assertIn('yellow', result.lower())
        self.assertFalse(result.isupper())

    def test_no_more_loopback_frames_finalizes_real_model_segment(self) -> None:
        model = Path(__file__).resolve().parents[1] / 'models' / MODEL_NAME
        sample = model.parent / 'sherpa-onnx-streaming-zipformer-en-20M-2023-02-17' / 'test_wavs' / '0.wav'
        if not sample.is_file() or any(not (model / name).is_file() for name in MODEL_FILES.values()):
            self.skipTest('本地字幕模型或示例音频尚未下载')
        with wave.open(str(sample), 'rb') as audio:
            data = audio.readframes(audio.getnframes())

        class Capture:
            def __init__(self, callback): self.callback = callback
            def __enter__(self):
                self.callback(data, len(data) // 2, None, 0)
                return self
            def __exit__(self, *args): pass
            def is_active(self): return True

        class Audio:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def get_device_info_by_index(self, index):
                return {'isLoopbackDevice': True, 'defaultSampleRate': 16000, 'maxInputChannels': 1}
            def open(self, **kwargs): return Capture(kwargs['stream_callback'])

        finished = threading.Event()
        events = []
        def emit(event):
            events.append(event)
            if event['kind'] == 'final': finished.set()
        audio_module = SimpleNamespace(PyAudio=Audio, paInt16=8, paContinue=0, paComplete=1)
        with patch.dict(sys.modules, {'pyaudiowpatch': audio_module}):
            service = SubtitleService(model, Mock(return_value=TranslationResult('测试', 'en', 'zh-CN', 'test')), emit)
            session = service.start(1)
            service.activate(session)
            self.assertTrue(finished.wait(12), f'没有完成断句：{events}')
            service.stop(session)


if __name__ == '__main__':
    unittest.main()
