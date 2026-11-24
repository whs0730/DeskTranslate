from __future__ import annotations

import queue
import re
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from .translator import LanguageDirection, TranslationError


MODEL_NAME = "sherpa-onnx-x-asr-480ms-streaming-zipformer-transducer-zh-en-punct-int8-2026-06-05"
MODEL_FILES = {
    "encoder": "encoder.int8.onnx",
    "decoder": "decoder.onnx",
    "joiner": "joiner.int8.onnx",
    "tokens": "tokens.txt",
}


def create_recognizer(model_dir: Path):
    import sherpa_onnx

    files = {key: str(model_dir / name) for key, name in MODEL_FILES.items()}
    if any(not Path(path).is_file() for path in files.values()):
        raise FileNotFoundError("找不到字幕识别模型，请重新构建或检查 models 目录")
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        **files, num_threads=2, sample_rate=16000, enable_endpoint_detection=True,
        rule1_min_trailing_silence=2.4, rule2_min_trailing_silence=1.0,
        rule3_min_utterance_length=15,
    )


def list_loopback_devices() -> list[dict]:
    import pyaudiowpatch as pyaudio

    with pyaudio.PyAudio() as audio:
        try:
            default = audio.get_default_wasapi_loopback()["index"]
        except (OSError, LookupError):
            default = None
        return [
            {"id": info["index"], "name": info["name"], "default": info["index"] == default}
            for info in audio.get_loopback_device_info_generator()
        ]


def readable_english(text: str) -> str:
    if not text or not text.isupper():
        return text
    text = text.lower()
    text = re.sub(r"\bi(?=\b|'[a-z])", "I", text)
    return text[0].upper() + text[1:]


# 正文稳定即定稿：正文（去掉标点空白）够长、且上一帧已经一字不差出现过，就当这句说完了。
# 标点会漂移（模型这一帧给逗号、下一帧改成句号），所以稳定性只按正文判，不按标点判。
EARLY_COMMIT_MIN_CHARS = 4
# 未定稿句子的增量翻译门槛：太短的碎片翻出来是噪声，还白花钱。
PARTIAL_TRANSLATE_MIN_CHARS = 6
# 增量翻译的最小间隔（云端通道够用了；本地通道可以更密，但收益不大）。
PARTIAL_TRANSLATE_INTERVAL = 1.2
# 句末翻译失败后快速重试一次，别让用户直接看到「翻译暂不可用」。
RETRY_DELAY = 0.15
# 低于这个 RMS 认为没有声音：只拦「太短 + 太静」的 partial，避免幻觉在静音里刷屏。
SILENCE_RMS = 0.0012

_ABBREVIATIONS = re.compile(r"^(ph\.d|m\.d|b\.a|m\.a|ll\.c|e\.g|i\.e|etc|mr|mrs|ms|dr|vs|st|no|vol)$", re.IGNORECASE)
_INITIALS = re.compile(r"^([a-z]\.)+$", re.IGNORECASE)
_TERMINATORS = re.compile(r"[.?!;。？！；]")
_CONTENT = re.compile(r"[^\w\u4e00-\u9fff]")
_WORD = re.compile(r"[\w\u4e00-\u9fff]")
_HALLUCINATIONS = frozenset({
    "thank you", "thanks for watching", "thank you for watching",
    "字幕由amara.org社区提供", "请不吝点赞订阅转发打赏支持明镜与点点栏目",
    "谢谢观看", "谢谢收看",
})


def content_text(text: str) -> str:
    """正文归一化：去掉空白与标点，只留字/数字/汉字。"""
    return _CONTENT.sub("", text or "").lower()


def is_noise(text: str) -> bool:
    """空串 / 纯标点 / 单字母碎片 / 常见静音幻觉。"""
    body = content_text(text)
    if not body:
        return True
    stripped = text.strip().lower().strip(" .,!?;:。！？；：…\"'")
    if stripped in _HALLUCINATIONS:
        return True
    if len(body) == 1 and body.isascii() and body.isalpha():
        return True
    return False


def committed_rest(text: str, committed: str) -> str:
    """裁掉已经提前定稿的正文前缀。

    committed 是已定稿句子的正文（content_text 归一化后拼在一起）。逐字对到相同的
    正文长度，就切在原文的同一个位置。对不齐时原样返回：宁可重复一遍，也不丢内容。
    """
    if not committed or not text:
        return text
    seen = 0
    for index, char in enumerate(text):
        if not _WORD.match(char):
            continue
        if seen >= len(committed) or committed[seen] != char.lower():
            return text
        seen += 1
        if seen == len(committed):
            return text[index + 1:]
    return text


def _abbreviation_before(text: str, index: int) -> bool:
    """index 指向终止符本身；判断它是否只是缩写或人名首字母里的点。"""
    word = text[:index].rsplit(" ", 1)[-1].strip().lower()
    if not word:
        return False
    if _ABBREVIATIONS.match(word):
        return True
    return bool(_INITIALS.match(word + "."))


def stable_prefix(text: str, previous: str, committed: str) -> str:
    """本轮 partial 里已经稳定下来的那句原文；没有就返回空串。

    稳定性判据：这一句（连同结尾强标点）的正文，在上一帧 partial 里已经完整出现过。
    流式模型只在没改口时才会把同样的正文和标点连着吐两帧，所以这是很强的信号。
    """
    rest = committed_rest(text, committed)
    if not rest:
        return ""
    if committed and rest == text:
        # 前缀对不齐（模型把已经定稿的那半句改写了）：这一帧不判稳定，免得同一句重复上屏。
        return ""
    previous_body = content_text(committed_rest(previous, committed))
    if not previous_body:
        return ""
    for match in _TERMINATORS.finditer(rest):
        if _abbreviation_before(rest, match.start()):
            continue
        piece = rest[: match.end()]
        body = content_text(piece)
        if len(body) < EARLY_COMMIT_MIN_CHARS or not previous_body.startswith(body):
            continue
        return piece
    return ""


class SubtitleService:
    def __init__(self, model_dir: Path, translate: Callable, emit: Callable) -> None:
        self.model_dir = model_dir
        self.translate = translate
        self.emit = emit
        self._lock = threading.Lock()
        self._session = 0
        self._stop: threading.Event | None = None
        self._ready: threading.Event | None = None
        self._clear: threading.Event | None = None
        self._epoch = 0

    def start(self, device_id: int) -> int:
        if not isinstance(device_id, int) or isinstance(device_id, bool):
            raise ValueError("请选择系统播放设备")
        with self._lock:
            if self._stop is not None:
                raise ValueError("字幕已经启动")
            self._session += 1
            session = self._session
            stop = threading.Event()
            ready = threading.Event()
            clear = threading.Event()
            self._stop = stop
            self._ready = ready
            self._clear = clear
            self._epoch = 0
        threading.Thread(target=self._run, args=(session, stop, ready, clear, device_id), daemon=True).start()
        return session

    def activate(self, session: int) -> None:
        with self._lock:
            if self._session == session and self._ready is not None:
                self._ready.set()

    def stop(self, session: int | None = None) -> None:
        with self._lock:
            if session is not None and session != self._session:
                return
            stop = self._stop
            ready = self._ready
            self._stop = None
            self._ready = None
            self._clear = None
            self._session += 1
        if stop is not None:
            stop.set()
        if ready is not None:
            ready.set()

    def clear(self, session: int) -> int:
        with self._lock:
            if self._session != session or self._stop is None or self._clear is None:
                raise ValueError("字幕已经停止")
            self._epoch += 1
            self._clear.set()
            return self._epoch

    def _send(self, session: int, event: dict, epoch: int | None = None) -> None:
        with self._lock:
            active = self._stop is not None and self._session == session and (epoch is None or epoch == self._epoch)
        if active:
            self.emit({"session": session, **({"epoch": epoch} if epoch is not None else {}), **event})

    def _run(self, session: int, stop: threading.Event, ready: threading.Event, clear: threading.Event, device_id: int) -> None:
        if not ready.wait(10) or stop.is_set():
            if not stop.is_set():
                self._send(session, {"kind": "error", "text": "字幕启动超时，请重试"})
            with self._lock:
                if self._session == session:
                    self._stop = None
                    self._ready = None
                    self._clear = None
            return

        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            self._send(session, {"kind": "error", "text": "音频采集组件不可用，请重新安装程序"})
            with self._lock:
                if self._session == session:
                    self._stop = None
                    self._ready = None
                    self._clear = None
            return

        audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=30)
        translations: queue.Queue[tuple[int, int, str]] = queue.Queue(maxsize=2)
        dropped = threading.Event()
        # 增量翻译的共享状态：预览线程只读这里，主循环负责推进版本号。
        preview: dict[str, object] = {"segment": 0, "text": "", "seq": 0, "epoch": 0}
        finals = {"inflight": 0}
        state_lock = threading.Lock()

        def callback(data, frame_count, time_info, flags):
            if stop.is_set():
                return (None, pyaudio.paComplete)
            if flags:
                dropped.set()
            if data:
                try:
                    audio_queue.put_nowait(data)
                except queue.Full:
                    dropped.set()
            return (None, pyaudio.paContinue)

        def translate_once(text: str):
            """翻译一次；云端偶发失败时快速重试一次，别让用户直接看到「翻译暂不可用」。"""
            try:
                return self.translate(text, LanguageDirection.EN_TO_ZH, timeout=8.0)
            except TranslationError:
                if stop.wait(RETRY_DELAY):
                    raise
                return self.translate(text, LanguageDirection.EN_TO_ZH, timeout=8.0)

        def translate_loop() -> None:
            while not stop.is_set():
                try:
                    epoch, segment, text = translations.get(timeout=0.2)
                except queue.Empty:
                    continue
                with self._lock:
                    if self._session != session or self._stop is not stop or self._epoch != epoch:
                        continue
                with state_lock:
                    finals["inflight"] += 1
                try:
                    result = translate_once(text)
                    self._send(session, {"kind": "translation", "segment": segment, "text": result.text}, epoch)
                except TranslationError:
                    self._send(session, {"kind": "translation", "segment": segment, "text": "翻译暂不可用"}, epoch)
                except Exception:
                    self._send(session, {"kind": "translation", "segment": segment, "text": "字幕翻译发生内部错误"}, epoch)
                finally:
                    with state_lock:
                        finals["inflight"] -= 1

        def preview_loop() -> None:
            """未定稿句子的增量翻译。

            五条护栏都在这里：单飞（本线程是唯一出口）、让路句末（句末在飞或有积压就不发）、
            版本校验（文本变了就丢弃过期译文）、门槛（太短不翻）、只做预览（不进历史、
            覆盖不了定稿译文）。
            """
            last_start = 0.0
            done_seq = -1
            while not stop.wait(0.1):
                with state_lock:
                    segment = int(preview["segment"])
                    text = str(preview["text"])
                    seq = int(preview["seq"])
                    epoch = int(preview["epoch"])
                    busy = finals["inflight"] > 0
                if busy or not translations.empty() or seq == done_seq:
                    continue
                if len(content_text(text)) < PARTIAL_TRANSLATE_MIN_CHARS:
                    continue
                now = time.monotonic()
                if now - last_start < PARTIAL_TRANSLATE_INTERVAL:
                    continue
                last_start = now
                done_seq = seq
                try:
                    result = self.translate(text, LanguageDirection.EN_TO_ZH, timeout=8.0)
                except Exception:
                    continue
                with state_lock:
                    fresh = int(preview["seq"]) == seq and int(preview["segment"]) == segment and int(preview["epoch"]) == epoch
                if fresh and result.text and not is_noise(result.text):
                    self._send(session, {"kind": "partial_translation", "segment": segment, "text": result.text}, epoch)

        try:
            self._send(session, {"kind": "status", "text": "正在加载语音模型…"})
            recognizer = create_recognizer(self.model_dir)
            if stop.is_set():
                return
            stream = recognizer.create_stream()
            with pyaudio.PyAudio() as audio:
                info = audio.get_device_info_by_index(device_id)
                if not info.get("isLoopbackDevice"):
                    raise ValueError("请选择系统播放设备")
                rate = int(info["defaultSampleRate"])
                channels = int(info["maxInputChannels"])
                with audio.open(format=pyaudio.paInt16, channels=channels, rate=rate, input=True,
                                input_device_index=device_id, frames_per_buffer=max(256, rate // 10),
                                stream_callback=callback) as capture:
                    for _ in range(2):
                        threading.Thread(target=translate_loop, daemon=True).start()
                    threading.Thread(target=preview_loop, daemon=True).start()
                    self._send(session, {"kind": "status", "text": "正在识别系统声音"})
                    segment = 0
                    epoch = 0
                    last = ""
                    previous = ""
                    committed = ""

                    def finish(piece: str, epoch: int) -> None:
                        """一句定稿：上屏 → 进翻译队列 → 句号翻篇。"""
                        nonlocal segment
                        clean = readable_english(piece).strip()
                        if not clean or is_noise(clean):
                            return
                        self._send(session, {"kind": "final", "segment": segment, "text": clean}, epoch)
                        try:
                            translations.put_nowait((epoch, segment, clean))
                        except queue.Full:
                            try:
                                old_epoch, old_segment, _ = translations.get_nowait()
                            except queue.Empty:
                                pass
                            else:
                                self._send(session, {"kind": "translation", "segment": old_segment, "text": "翻译队列繁忙，已跳过"}, old_epoch)
                            translations.put_nowait((epoch, segment, clean))
                        segment += 1
                        with state_lock:
                            preview["segment"] = segment
                            preview["text"] = ""
                            preview["seq"] = int(preview["seq"]) + 1

                    while not stop.is_set():
                        try:
                            data = audio_queue.get(timeout=0.2)
                        except queue.Empty:
                            if not capture.is_active():
                                raise OSError("音频设备已断开")
                            data = None
                        if clear.is_set():
                            with self._lock:
                                epoch = self._epoch
                                clear.clear()
                            recognizer.reset(stream)
                            last = ""
                            previous = ""
                            committed = ""
                            segment = 0
                            with state_lock:
                                preview["text"] = ""
                                preview["epoch"] = epoch
                                preview["seq"] = int(preview["seq"]) + 1
                            while True:
                                try:
                                    audio_queue.get_nowait()
                                except queue.Empty:
                                    break
                            while True:
                                try:
                                    translations.get_nowait()
                                except queue.Empty:
                                    break
                            continue
                        if data is None and not last:
                            continue
                        if dropped.is_set():
                            dropped.clear()
                            self._send(session, {"kind": "status", "text": "音频处理跟不上，部分声音可能丢失"})
                        # 回环设备在播放停止时可能不再送帧；补静音以完成当前断句。
                        samples = (np.zeros(rate // 5, dtype=np.float32) if data is None else
                                   np.frombuffer(data, dtype=np.int16).reshape(-1, channels).mean(axis=1).astype(np.float32) / 32768.0)
                        rms = float(np.sqrt(np.mean(np.square(samples)))) if len(samples) else 0.0
                        if len(samples):
                            stream.accept_waveform(rate, samples)
                        while recognizer.is_ready(stream):
                            recognizer.decode_stream(stream)
                        text = recognizer.get_result(stream).strip()
                        # 提前定稿：正文已经稳定就不等端点（端点要等 1~15 秒静音或句长）。
                        piece = stable_prefix(text, previous, committed)
                        if piece:
                            finish(piece, epoch)
                            committed += content_text(piece)
                        previous = text
                        rest = committed_rest(text, committed)
                        if rest and rest != last and not is_noise(rest):
                            # 静音里冒出来的短碎片一律不上屏：那基本都是幻觉。
                            quiet = rms < SILENCE_RMS and len(content_text(rest)) < PARTIAL_TRANSLATE_MIN_CHARS
                            if not quiet:
                                clean = readable_english(rest)
                                self._send(session, {"kind": "partial", "segment": segment, "text": clean}, epoch)
                                last = rest
                                with state_lock:
                                    preview["text"] = clean
                                    preview["segment"] = segment
                                    preview["epoch"] = epoch
                                    preview["seq"] = int(preview["seq"]) + 1
                        if recognizer.is_endpoint(stream):
                            finish(rest, epoch)
                            recognizer.reset(stream)
                            last = ""
                            previous = ""
                            committed = ""
                            with state_lock:
                                preview["text"] = ""
                                preview["seq"] = int(preview["seq"]) + 1
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            self._send(session, {"kind": "error", "text": f"字幕启动或运行失败：{exc}"})
        finally:
            with self._lock:
                if self._session == session:
                    self._stop = None
                    self._ready = None
                    self._clear = None
            stop.set()
