"""Lazy, bounded IPC to an isolated Qwen process; no model imports here."""

from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path
from queue import Empty, Full, Queue
import subprocess
from threading import Event, Lock, Thread
import time
import weakref

from voice.neural_contracts import NeuralRuntimeConfig, inspect_neural_setup
from voice.voice_models import SpeechAudio, VoiceCancelledError, VoiceError, VoiceUnavailableError

MAX_RESPONSE = 24 * 1024 * 1024
MAX_PCM = 16 * 1024 * 1024


def _dispose(process: subprocess.Popen) -> None:
    try:
        if process.poll() is None:
            process.kill()
    except OSError:
        pass

    def reap():
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        for pipe in (process.stdin, process.stdout):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass
    Thread(target=reap, name="qwen-cleanup", daemon=True).start()


def _read(process: subprocess.Popen, responses: Queue) -> None:
    try:
        while True:
            raw = process.stdout.readline(MAX_RESPONSE + 1)
            if not raw:
                raise VoiceUnavailableError("本地语音进程已退出。")
            if len(raw) > MAX_RESPONSE or not raw.endswith(b"\n"):
                raise VoiceError("本地语音响应过大或不完整。")
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("version") != 1:
                raise VoiceError("本地语音协议不匹配。")
            responses.put_nowait(data)
    except Exception as exc:
        try:
            responses.put_nowait(exc)
        except Full:
            pass


def decode_audio(response: dict) -> SpeechAudio:
    try:
        encoded = response["pcm"]
        if not isinstance(encoded, str) or len(encoded) > (MAX_PCM + 2) // 3 * 4:
            raise ValueError("invalid PCM length")
        data = base64.b64decode(encoded, validate=True)
        rate = response["sample_rate"]
        if (type(rate) is not int or not 8000 <= rate <= 192000
                or response.get("channels") != 1 or response.get("sample_width") != 2
                or len(data) > MAX_PCM or len(data) > rate * 2 * 180):
            raise ValueError("invalid PCM format")
        return SpeechAudio(data, rate, backend_label="本地声音 · Serena")
    except (KeyError, TypeError, ValueError) as exc:
        raise VoiceError("本地语音返回无效 PCM。") from exc


class QwenCustomVoiceBackend:
    def __init__(self, config: NeuralRuntimeConfig, *, voice_id="Serena", language="Chinese",
                 load_timeout: float = 120, synthesis_timeout: float = 120) -> None:
        if any(not math.isfinite(t) or t <= 0 for t in (load_timeout, synthesis_timeout)):
            raise ValueError("语音超时必须为正有限数值。")
        self.config, self.voice_id, self.language = config, voice_id, language
        self.load_timeout, self.synthesis_timeout = load_timeout, synthesis_timeout
        self._lock, self._state = Lock(), Lock()
        self._process = self._responses = self._cleanup = None
        self._closed = False
        self._serial = 0
        self.load_seconds = None
        self._retired: list[subprocess.Popen] = []

    def _receive(self, responses: Queue, timeout: float, cancelled: Event) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            if cancelled.is_set() or self._closed:
                raise VoiceCancelledError("朗读已取消。")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VoiceUnavailableError("本地语音超时，请缩短文本或改用系统声音。")
            try:
                result = responses.get(timeout=min(0.05, remaining))
            except Empty:
                continue
            if cancelled.is_set() or self._closed:
                raise VoiceCancelledError("朗读已取消。")
            if isinstance(result, Exception):
                raise VoiceUnavailableError("本地语音进程异常或响应无效。") from result
            return result

    def _start(self, cancelled: Event):
        with self._state:
            if self._closed or cancelled.is_set():
                raise VoiceCancelledError("朗读已取消。")
            if self._process is not None and self._process.poll() is None:
                return self._process, self._responses
            retired, self._retired = self._retired, []
        # A cancelled model must actually exit before another process allocates
        # RAM/CUDA. This bounded wait runs on the synthesis worker, never Qt.
        deadline = time.monotonic() + 5
        for previous in retired:
            while previous.poll() is None:
                if self._closed or cancelled.is_set():
                    with self._state:
                        self._retired.extend(p for p in retired if p.poll() is None)
                    raise VoiceCancelledError("朗读已取消。")
                if time.monotonic() >= deadline:
                    with self._state:
                        self._retired.extend(p for p in retired if p.poll() is None)
                    raise VoiceUnavailableError("上一次本地语音进程尚未退出，请稍后重试。")
                time.sleep(.02)
        inspection = inspect_neural_setup(self.config)
        if not inspection.local_checks_passed:
            raise VoiceUnavailableError("本地语音未配置或模型文件不完整。")
        worker = Path(__file__).resolve().parents[1] / "scripts/qwen_voice_worker.py"
        if not worker.is_file():
            raise VoiceUnavailableError("未找到本地语音运行脚本。")
        environment = os.environ.copy()
        environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                           HF_HUB_DISABLE_TELEMETRY="1", PYTHONIOENCODING="utf-8")
        with self._state:
            if self._closed or cancelled.is_set():
                raise VoiceCancelledError("朗读已取消。")
            process = subprocess.Popen(
                [self.config.python_executable, "-u", str(worker), "--model-dir", self.config.model_path,
                 "--device", self.config.device], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, shell=False, env=environment, cwd=str(worker.parent),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            responses = Queue(maxsize=2)
            if self._cleanup is not None:
                self._cleanup()
            self._process, self._responses = process, responses
            self._cleanup = weakref.finalize(self, _dispose, process)
            Thread(target=_read, args=(process, responses), name="qwen-reader", daemon=True).start()
        ready = self._receive(responses, self.load_timeout, cancelled)
        if ready.get("ready") is not True:
            raise VoiceUnavailableError("本地声音加载失败：" + str(ready.get("error", "未知错误"))[:600])
        if (ready.get("model") != "0.6b_custom_voice" or self.voice_id not in ready.get("voices", [])
                or self.language not in ready.get("languages", [])):
            raise VoiceUnavailableError("本地声音的模型、音色或语言不匹配。")
        self.load_seconds = ready.get("load_seconds")
        return process, responses

    def synthesize(self, text, *, reference_audio_path=None, speech_speed=1.0):
        return self.synthesize_cancellable(text, reference_audio_path=reference_audio_path,
                                          speech_speed=speech_speed, cancelled=Event())

    def synthesize_cancellable(self, text, *, reference_audio_path=None, speech_speed=1.0, cancelled: Event):
        if reference_audio_path is not None:
            raise VoiceUnavailableError("本地 Serena 音色不支持参考录音。")
        if not isinstance(text, str) or not text.strip() or len(text) > 240:
            raise VoiceError("本地语音需要 1–240 字的分句。")
        if self.voice_id != "Serena" or self.language != "Chinese":
            raise VoiceUnavailableError("本阶段仅启用 Serena 中文音色（可读中英混合文本）。")
        if (isinstance(speech_speed, bool) or not isinstance(speech_speed, (int, float))
                or not math.isfinite(speech_speed) or not .5 <= speech_speed <= 2):
            raise VoiceError("语速必须在 0.5 到 2.0 之间。")
        with self._lock:
            try:
                process, responses = self._start(cancelled)
                if cancelled.is_set():
                    raise VoiceCancelledError("朗读已取消。")
                self._serial += 1
                serial = self._serial
                request = json.dumps({"version": 1, "id": serial, "action": "synthesize", "text": text,
                                      "voice": self.voice_id, "language": self.language, "speed": speech_speed},
                                     ensure_ascii=True).encode("ascii") + b"\n"
                if len(request) > 65536:
                    raise VoiceError("本地语音请求过大。")
                def send():
                    try:
                        process.stdin.write(request)
                        process.stdin.flush()
                    except (OSError, ValueError) as exc:
                        try:
                            responses.put_nowait(exc)
                        except Full:
                            pass
                Thread(target=send, name="qwen-writer", daemon=True).start()
                response = self._receive(responses, self.synthesis_timeout, cancelled)
                if type(response.get("id")) is not int or response["id"] != serial:
                    raise VoiceError("本地语音响应编号不匹配。")
                if response.get("ok") is not True:
                    raise VoiceUnavailableError("本地语音合成失败：" + str(response.get("error", "未知错误"))[:600])
                return decode_audio(response)
            except Exception:
                self.cancel_current()
                raise

    def cancel_current(self):
        with self._state:
            cleanup, self._cleanup = self._cleanup, None
            if self._process is not None:
                self._retired.append(self._process)
            self._process = self._responses = None
        if cleanup is not None:
            cleanup()

    def close(self):
        with self._state:
            self._closed = True
        self.cancel_current()
