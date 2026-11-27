"""Offline Windows speech using installed SAPI voices, with no audio-device access.

Run ``synthesize`` in a worker. A hidden Windows PowerShell process owns one
System.Speech synthesizer and exchanges bounded JSON lines over anonymous pipes.
Only the constant bridge is executable code; all caller text travels as data.
Call ``close`` when replacing the backend or shutting down the voice service.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import math
import os
from pathlib import Path
from queue import Empty, Full, Queue
import subprocess
from threading import Lock, Thread
import wave
import weakref

from voice.voice_models import SpeechAudio, VoiceError, VoiceUnavailableError


_MAX_TEXT_CHARS = 2000
_MAX_WAVE_BYTES = 16 * 1024 * 1024
_MAX_RESPONSE_BYTES = 24 * 1024 * 1024

# SetOutputToWaveStream captures audio without opening a speaker. SetOutputToNull
# releases the stream and finalizes its WAV header before ToArray is returned.
_BRIDGE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$synth = $null
try {
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $voices = @($synth.GetInstalledVoices() | Where-Object { $_.Enabled })
    if ($voices.Count -eq 0) { throw 'No enabled Windows speech voice is installed.' }
    $chosen = $voices | Where-Object { $_.VoiceInfo.Culture.Name -eq 'zh-CN' } | Select-Object -First 1
    if ($null -eq $chosen) {
        $chosen = $voices | Where-Object { $_.VoiceInfo.Culture.TwoLetterISOLanguageName -eq 'zh' } | Select-Object -First 1
    }
    if ($null -eq $chosen) {
        $chosen = $voices | Where-Object { $_.VoiceInfo.Name -eq $synth.Voice.Name } | Select-Object -First 1
    }
    if ($null -eq $chosen) { $chosen = $voices[0] }
    $synth.SelectVoice($chosen.VoiceInfo.Name)
    $synth.SetOutputToNull()
    [Console]::WriteLine((@{ ready = $true; voice = $synth.Voice.Name } | ConvertTo-Json -Compress))
    while ($null -ne ($line = [Console]::ReadLine())) {
        $request = $null
        $stream = $null
        try {
            $request = $line | ConvertFrom-Json
            if ($request.command -ne 'synthesize') { throw 'Unknown speech command.' }
            $text = [string]$request.text
            if ([string]::IsNullOrWhiteSpace($text) -or $text.Length -gt 4000) { throw 'Invalid speech text length.' }
            $synth.Rate = [int]$request.rate
            $stream = New-Object System.IO.MemoryStream
            $synth.SetOutputToWaveStream($stream)
            $synth.Speak($text)
            $synth.SetOutputToNull()
            if ($stream.Length -gt 16777216) { throw 'Speech audio exceeds the size limit.' }
            $reply = @{ id = $request.id; ok = $true; voice = $synth.Voice.Name; wav = [Convert]::ToBase64String($stream.ToArray()) }
            [Console]::WriteLine(($reply | ConvertTo-Json -Compress))
        } catch {
            [Console]::WriteLine((@{ id = $request.id; ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress))
        } finally {
            if ($null -ne $synth) { $synth.SetOutputToNull() }
            if ($null -ne $stream) { $stream.Dispose() }
        }
    }
} catch {
    [Console]::WriteLine((@{ ready = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress))
} finally {
    if ($null -ne $synth) { $synth.Dispose() }
}
"""


def _read_responses(process: subprocess.Popen, responses: Queue) -> None:
    """The sole stdout reader; bounded lines and queue prevent runaway output."""
    try:
        while True:
            line = process.stdout.readline(_MAX_RESPONSE_BYTES + 1)
            if not line:
                raise VoiceUnavailableError("Windows 语音进程已退出，请重试。")
            if len(line) > _MAX_RESPONSE_BYTES or not line.endswith(b"\n"):
                raise VoiceError("Windows 语音返回的数据超过限制或不完整。")
            response = json.loads(line.decode("utf-8-sig"))
            if not isinstance(response, dict):
                raise VoiceError("Windows 语音返回了无效响应。")
            responses.put_nowait(response)
    except Exception as exc:
        try:
            responses.put_nowait(exc)
        except Full:
            pass


def _dispose_process(process: subprocess.Popen) -> None:
    """Interrupt immediately; reap and close pipes outside the calling/UI thread."""
    try:
        if process.poll() is None:
            process.kill()
    except OSError:
        pass

    def reap() -> None:
        try:
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
        for pipe in (process.stdin, process.stdout):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass

    Thread(target=reap, name="windows-tts-cleanup", daemon=True).start()


def _decode_wave(encoded: str) -> SpeechAudio:
    try:
        if not isinstance(encoded, str) or len(encoded) > (_MAX_WAVE_BYTES + 2) // 3 * 4:
            raise ValueError("invalid WAV size")
        wav_bytes = base64.b64decode(encoded, validate=True)
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            if wav.getcomptype() != "NONE":
                raise ValueError("compressed WAV")
            width, channels = wav.getsampwidth(), wav.getnchannels()
            frame_count = wav.getnframes()
            if frame_count * channels * width > _MAX_WAVE_BYTES:
                raise ValueError("invalid PCM size")
            data = wav.readframes(frame_count)
            if len(data) != frame_count * channels * width:
                raise ValueError("truncated PCM")
            return SpeechAudio(data, wav.getframerate(), channels, width)
    except (ValueError, TypeError, wave.Error, EOFError, binascii.Error) as exc:
        raise VoiceError("Windows 语音返回了无效的 WAV 音频。") from exc


class WindowsTTSBackend:
    """Local installed Windows voice; reference/role voice cloning is unsupported."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self._timeout = timeout
        self._request_lock = Lock()
        self._state_lock = Lock()
        self._process: subprocess.Popen | None = None
        self._responses: Queue | None = None
        self._cleanup: weakref.finalize | None = None
        self._closed = False
        self._next_id = 0
        self.selected_voice_name = ""

    def _start(self) -> tuple[subprocess.Popen, Queue]:
        with self._state_lock:
            if self._closed:
                raise VoiceUnavailableError("Windows 语音后端已关闭。")
            if self._process is not None and self._process.poll() is None:
                return self._process, self._responses
            if os.name != "nt":
                raise VoiceUnavailableError("系统语音后端仅支持 Windows。")
            executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            if not executable.is_file():
                raise VoiceUnavailableError("未找到 Windows PowerShell，无法使用系统语音。")
            encoded_script = base64.b64encode(_BRIDGE_SCRIPT.encode("utf-16-le")).decode("ascii")
            try:
                process = subprocess.Popen(
                    [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_script],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except OSError as exc:
                raise VoiceUnavailableError("无法启动 Windows 系统语音进程。") from exc
            responses: Queue = Queue(maxsize=2)
            self._process, self._responses = process, responses
            if self._cleanup is not None:
                self._cleanup()
            self._cleanup = weakref.finalize(self, _dispose_process, process)
            Thread(target=_read_responses, args=(process, responses), name="windows-tts-reader", daemon=True).start()
        ready = self._response(responses)
        if ready.get("ready") is not True:
            raise VoiceUnavailableError("Windows 系统语音不可用：" + str(ready.get("error", "未找到可用语音。"))[:500])
        self.selected_voice_name = str(ready.get("voice", ""))
        return process, responses

    def _response(self, responses: Queue) -> dict:
        try:
            value = responses.get(timeout=self._timeout)
        except Empty as exc:
            raise VoiceUnavailableError("Windows 语音合成超时，请缩短文本后重试。") from exc
        if isinstance(value, Exception):
            if isinstance(value, VoiceError):
                raise value
            raise VoiceUnavailableError("无法读取 Windows 语音响应。") from value
        return value

    def _drop_process(self) -> None:
        with self._state_lock:
            cleanup = self._cleanup
            self._process = self._responses = self._cleanup = None
        if cleanup is not None:
            cleanup()

    def synthesize(self, text: str, *, reference_audio_path: Path | None, speech_speed: float) -> SpeechAudio:
        if reference_audio_path is not None:
            raise VoiceUnavailableError("Windows 系统语音不支持参考音频或角色音色克隆；请清空参考音频设置后使用系统音色。")
        if not isinstance(text, str) or not text.strip():
            raise VoiceError("没有可朗读的文字。")
        if len(text) > _MAX_TEXT_CHARS:
            raise VoiceError("单次朗读文字过长，请分句朗读。")
        if isinstance(speech_speed, bool) or not isinstance(speech_speed, (int, float)) or not math.isfinite(speech_speed) or not 0.5 <= speech_speed <= 2.0:
            raise VoiceError("语速必须在 0.5 到 2.0 之间。")
        if not self._request_lock.acquire(timeout=self._timeout):
            raise VoiceUnavailableError("Windows 语音后端正忙，请稍后重试。")
        try:
            process, responses = self._start()
            self._next_id += 1
            request_id = self._next_id
            # SAPI rate is an engine-dependent -10..10 scale, not an exact ratio.
            rate = max(-10, min(10, round(5 * math.log2(speech_speed))))
            request = json.dumps({"command": "synthesize", "id": request_id, "text": text, "rate": rate}, ensure_ascii=True).encode("ascii") + b"\n"

            def send() -> None:
                try:
                    process.stdin.write(request)
                    process.stdin.flush()
                except (OSError, ValueError) as exc:
                    try:
                        responses.put_nowait(exc)
                    except Full:
                        pass

            # A stuck pipe writer must not defeat the bounded response timeout.
            Thread(target=send, name="windows-tts-writer", daemon=True).start()
            response = self._response(responses)
            if response.get("id") != request_id:
                raise VoiceError("Windows 语音响应编号不匹配。")
            if response.get("ok") is not True:
                raise VoiceUnavailableError("Windows 语音合成失败：" + str(response.get("error", "未知错误"))[:500])
            audio = _decode_wave(response.get("wav"))
            self.selected_voice_name = str(response.get("voice", self.selected_voice_name))
            return audio
        except Exception:
            self._drop_process()
            raise
        finally:
            self._request_lock.release()

    def close(self) -> None:
        """Permanent shutdown; also interrupts a concurrent synthesis request."""
        with self._state_lock:
            self._closed = True
        self._drop_process()

    def cancel_current(self) -> None:
        """Interrupt one request; a later request may start a fresh helper."""
        self._drop_process()

    def __enter__(self) -> WindowsTTSBackend:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
