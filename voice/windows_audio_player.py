"""Windows PCM playback without QtMultimedia or any extra dependency.

waveOutWrite queues memory asynchronously. A Qt timer observes WHDR_DONE;
waveOutReset interrupts immediately. PCM memory stays alive until Windows has
successfully unprepared its header. Volume scales this clip's samples only.
"""

from __future__ import annotations

from array import array
import ctypes
from dataclasses import dataclass
import math
import os
import time

from PySide6.QtCore import QObject, QTimer

from voice.audio_player import AudioPlayer
from voice.voice_models import SpeechAudio, VoiceError, VoiceUnavailableError


_WHDR_DONE = 0x00000001
_MAX_PCM_BYTES = 16 * 1024 * 1024
_VOLUME_CHUNK_BYTES = 32768


class _WaveFormat(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("wFormatTag", ctypes.c_uint16), ("nChannels", ctypes.c_uint16),
        ("nSamplesPerSec", ctypes.c_uint32), ("nAvgBytesPerSec", ctypes.c_uint32),
        ("nBlockAlign", ctypes.c_uint16), ("wBitsPerSample", ctypes.c_uint16),
        ("cbSize", ctypes.c_uint16),
    ]


class _WaveHeader(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p), ("dwBufferLength", ctypes.c_uint32),
        ("dwBytesRecorded", ctypes.c_uint32), ("dwUser", ctypes.c_size_t),
        ("dwFlags", ctypes.c_uint32), ("dwLoops", ctypes.c_uint32),
        ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t),
    ]


@dataclass
class _Clip:
    handle: ctypes.c_void_p
    header: _WaveHeader
    buffer: object
    prepared: bool = False


# A failed driver cleanup must never release a buffer that native code owns,
# even if the Python/Qt player is destroyed. Normally this dictionary is empty
# between clips. The OS reclaims failed driver handles when the process exits.
_OPEN_CLIPS: dict[int, _Clip] = {}


def _wave_format(audio: SpeechAudio) -> _WaveFormat:
    alignment = audio.channels * audio.sample_width
    return _WaveFormat(1, audio.channels, audio.sample_rate,
                       audio.sample_rate * alignment, alignment,
                       audio.sample_width * 8, 0)


class _WinMM:
    def __init__(self) -> None:
        if os.name != "nt":
            raise VoiceUnavailableError("系统语音播放器仅支持 Windows。")
        try:
            self.dll = ctypes.WinDLL("winmm.dll")
        except OSError as exc:
            raise VoiceUnavailableError("无法加载 Windows 音频组件。") from exc
        self.dll.waveOutOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint,
                                        ctypes.POINTER(_WaveFormat), ctypes.c_size_t,
                                        ctypes.c_size_t, ctypes.c_uint32]
        self.dll.waveOutOpen.restype = ctypes.c_uint
        for name in ("waveOutPrepareHeader", "waveOutWrite", "waveOutUnprepareHeader"):
            function = getattr(self.dll, name)
            function.argtypes = [ctypes.c_void_p, ctypes.POINTER(_WaveHeader), ctypes.c_uint]
            function.restype = ctypes.c_uint
        for name in ("waveOutReset", "waveOutClose"):
            function = getattr(self.dll, name)
            function.argtypes = [ctypes.c_void_p]
            function.restype = ctypes.c_uint
        self.dll.waveOutGetErrorTextW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
        self.dll.waveOutGetErrorTextW.restype = ctypes.c_uint

    def _check(self, result: int, operation: str) -> None:
        if result:
            message = ctypes.create_unicode_buffer(256)
            self.dll.waveOutGetErrorTextW(result, message, len(message))
            raise VoiceUnavailableError(f"Windows 音频{operation}失败（{result}）：{message.value or '请检查输出设备。'}")

    def supports(self, audio: SpeechAudio) -> bool:
        """Query format capability without opening the device or playing audio."""
        audio_format = _wave_format(audio)
        return self.dll.waveOutOpen(None, 0xFFFFFFFF, ctypes.byref(audio_format), 0, 0, 1) == 0

    def open(self, audio: SpeechAudio) -> ctypes.c_void_p:
        handle = ctypes.c_void_p()
        audio_format = _wave_format(audio)
        # CALLBACK_NULL; omitting WAVE_ALLOWSYNC rejects synchronous drivers.
        self._check(self.dll.waveOutOpen(ctypes.byref(handle), 0xFFFFFFFF,
                                        ctypes.byref(audio_format), 0, 0, 0), "打开")
        return handle

    def prepare(self, clip: _Clip) -> None:
        self._check(self.dll.waveOutPrepareHeader(clip.handle, ctypes.byref(clip.header), ctypes.sizeof(_WaveHeader)), "准备")

    def write(self, clip: _Clip) -> None:
        self._check(self.dll.waveOutWrite(clip.handle, ctypes.byref(clip.header), ctypes.sizeof(_WaveHeader)), "播放")

    def reset(self, clip: _Clip) -> None:
        self._check(self.dll.waveOutReset(clip.handle), "停止")

    def unprepare(self, clip: _Clip) -> None:
        self._check(self.dll.waveOutUnprepareHeader(clip.handle, ctypes.byref(clip.header), ctypes.sizeof(_WaveHeader)), "释放缓冲区")

    def close(self, clip: _Clip) -> None:
        self._check(self.dll.waveOutClose(clip.handle), "关闭")


def _scale_pcm(data: bytes, width: int, volume: float) -> bytes:
    if volume == 1:
        return data
    if width == 1:
        return bytes(round((sample - 128) * volume) + 128 for sample in data)
    samples = array("h")
    samples.frombytes(data)
    return array("h", (round(sample * volume) for sample in samples)).tobytes()


class WindowsAudioPlayer(AudioPlayer):
    """Call play/stop/close on the Qt thread; close before application teardown."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._api: _WinMM | None = None
        self._clip: _Clip | None = None
        self._playback_id: int | None = None
        self._pending: tuple[SpeechAudio, float] | None = None
        self._scaled = bytearray()
        self._offset = 0
        self._deadline = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self._poll)

    def play(self, audio: SpeechAudio, *, playback_id: int, volume: float = 1.0) -> None:
        self.stop()
        if not isinstance(audio, SpeechAudio):
            raise VoiceError("播放器需要有效的 PCM 音频。")
        if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not math.isfinite(volume) or not 0 <= volume <= 1:
            raise VoiceError("播放音量必须在 0 到 1 之间。")
        if audio.sample_width not in (1, 2) or audio.channels not in (1, 2):
            raise VoiceUnavailableError("Windows 播放器需要单声道或双声道的 8/16 位 PCM。")
        if not 8000 <= audio.sample_rate <= 192000 or len(audio.data) > _MAX_PCM_BYTES:
            raise VoiceError("语音音频的采样率或大小超过播放限制。")
        self._playback_id = playback_id
        self._pending = (audio, float(volume))
        self._timer.start()
        # Device open and volume preparation happen on later event-loop ticks,
        # so completion/failure cannot reenter VoiceService during play().

    def _begin_playback(self, audio: SpeechAudio, data: bytes) -> None:
        if self._api is None:
            self._api = _WinMM()
        buffer = ctypes.create_string_buffer(data, len(data))
        header = _WaveHeader()
        header.lpData = ctypes.addressof(buffer)
        header.dwBufferLength = len(data)
        handle = self._api.open(audio)
        clip = _Clip(handle, header, buffer)
        self._clip = clip
        _OPEN_CLIPS[id(clip)] = clip
        self._api.prepare(clip)
        clip.prepared = True
        self._api.write(clip)
        duration = len(data) / (audio.sample_rate * audio.channels * audio.sample_width)
        self._deadline = time.monotonic() + duration + 10

    def _poll(self) -> None:
        playback_id = self._playback_id
        try:
            if self._pending is not None:
                audio, volume = self._pending
                if volume == 1:
                    data = audio.data
                else:
                    end = min(len(audio.data), self._offset + _VOLUME_CHUNK_BYTES)
                    self._scaled.extend(_scale_pcm(audio.data[self._offset:end], audio.sample_width, volume))
                    self._offset = end
                    if end < len(audio.data):
                        return
                    data = bytes(self._scaled)
                self._pending = None
                self._scaled = bytearray()
                self._offset = 0
                self._begin_playback(audio, data)
            elif self._clip is not None:
                if playback_id is None:
                    self.stop()  # Retry a driver cleanup while retaining its memory.
                elif self._clip.header.dwFlags & _WHDR_DONE:
                    self.stop()
                    self.finished.emit(playback_id)
                elif time.monotonic() > self._deadline:
                    raise VoiceUnavailableError("音频设备未完成播放，请检查扬声器或耳机连接。")
        except Exception as exc:
            try:
                self.stop()
            except Exception:
                pass  # Keep clip memory and retry cleanup on a later timer tick.
            if playback_id is not None:
                self.error.emit(playback_id, str(exc))

    def stop(self) -> None:
        self._playback_id = None
        self._pending = None
        self._scaled = bytearray()
        self._offset = 0
        if self._clip is not None:
            clip = self._clip
            self._api.reset(clip)
            if clip.prepared:
                self._api.unprepare(clip)
                clip.prepared = False
            self._api.close(clip)
            self._clip = None
            _OPEN_CLIPS.pop(id(clip), None)
        self._timer.stop()

    def is_playing(self) -> bool:
        return self._pending is not None or self._clip is not None

    def close(self) -> None:
        self.stop()
