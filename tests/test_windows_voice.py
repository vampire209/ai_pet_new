"""Local speech safety/PCM/playback tests; no test opens an audio output device."""

from __future__ import annotations

from array import array
import base64
import ctypes
import io
import json
import os
from pathlib import Path
from queue import Queue
import time
import unittest
from unittest.mock import patch
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from voice.voice_models import SpeechAudio, VoiceError, VoiceUnavailableError
from voice.windows_audio_player import WindowsAudioPlayer, _OPEN_CLIPS, _WHDR_DONE, _scale_pcm
from voice.windows_tts import WindowsTTSBackend, _decode_wave


def encoded_wave(data: bytes = b"\x01\x00\xff\x7f") -> str:
    memory = io.BytesIO()
    with wave.open(memory, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(data)
    return base64.b64encode(memory.getvalue()).decode("ascii")


class FakeWinMM:
    def __init__(self):
        self.calls = []
        self.written = []
        self.fail_write = False
        self.fail_unprepare = False

    def open(self, audio):
        self.calls.append("open")
        return ctypes.c_void_p(1)

    def prepare(self, clip):
        self.calls.append("prepare")

    def write(self, clip):
        self.calls.append("write")
        if self.fail_write:
            raise VoiceUnavailableError("fake device write failure")
        self.written.append(ctypes.string_at(clip.header.lpData, clip.header.dwBufferLength))

    def reset(self, clip):
        self.calls.append("reset")
        clip.header.dwFlags |= _WHDR_DONE

    def unprepare(self, clip):
        self.calls.append("unprepare")
        if self.fail_unprepare:
            raise VoiceUnavailableError("fake driver still owns buffer")

    def close(self, clip):
        self.calls.append("close")


class WindowsAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["windows-voice-tests"])

    def setUp(self):
        self.api = FakeWinMM()
        self.patch = patch("voice.windows_audio_player._WinMM", return_value=self.api)
        self.factory = self.patch.start()
        self.player = WindowsAudioPlayer()
        self.done, self.errors = [], []
        self.player.finished.connect(self.done.append)
        self.player.error.connect(lambda playback_id, message: self.errors.append((playback_id, message)))
        self.audio = SpeechAudio(array("h", [-2000, 1000, 0, 2000]).tobytes(), 22050)

    def tearDown(self):
        self.api.fail_unprepare = False
        self.player.close()
        self.patch.stop()
        self.assertFalse(_OPEN_CLIPS)

    def test_initialization_is_lazy_and_play_is_asynchronous(self):
        self.factory.assert_not_called()
        self.player.play(self.audio, playback_id=10)
        self.factory.assert_not_called()
        self.assertTrue(self.player.is_playing())
        self.player._poll()
        self.assertEqual(self.api.calls, ["open", "prepare", "write"])
        self.assertEqual(self.api.written, [self.audio.data])
        self.assertEqual(self.done, [])

    def test_done_releases_header_and_emits_original_id_once(self):
        self.player.play(self.audio, playback_id=42)
        self.player._poll()
        self.player._clip.header.dwFlags |= _WHDR_DONE
        self.player._poll()
        self.player._poll()
        self.assertEqual(self.done, [42])
        self.assertEqual(self.errors, [])
        self.assertFalse(self.player.is_playing())
        self.assertEqual(self.api.calls[-3:], ["reset", "unprepare", "close"])

    def test_stop_and_replacement_ignore_old_completion(self):
        self.player.play(self.audio, playback_id=1)
        self.player._poll()
        old_clip = self.player._clip
        self.player.play(self.audio, playback_id=2)
        self.player._poll()
        old_clip.header.dwFlags |= _WHDR_DONE
        self.player._poll()
        self.assertEqual(self.done, [])
        self.player.stop()
        self.player._poll()
        self.assertEqual(self.done, [])
        self.assertFalse(self.player.is_playing())

    def test_cancel_before_pending_play_does_not_open_device(self):
        self.player.play(self.audio, playback_id=3, volume=0.5)
        self.player.stop()
        self.player._poll()
        self.factory.assert_not_called()

    def test_volume_only_scales_current_pcm(self):
        self.player.play(self.audio, playback_id=4, volume=0.5)
        self.player._poll()
        self.assertEqual(self.api.written[0], array("h", [-1000, 500, 0, 1000]).tobytes())
        self.assertEqual(self.audio.data, array("h", [-2000, 1000, 0, 2000]).tobytes())
        self.assertEqual(_scale_pcm(bytes([0, 128, 255]), 1, 0), b"\x80\x80\x80")

    def test_large_volume_preparation_yields_between_ticks(self):
        audio = SpeechAudio(self.audio.data * 20000, 22050)
        self.player.play(audio, playback_id=5, volume=0.5)
        self.player._poll()
        self.factory.assert_not_called()
        self.assertGreater(self.player._offset, 0)
        self.player.stop()
        self.player._poll()
        self.factory.assert_not_called()

    def test_write_error_is_signaled_and_all_resources_released(self):
        self.api.fail_write = True
        self.player.play(self.audio, playback_id=6)
        self.player._poll()
        self.assertEqual(self.errors, [(6, "fake device write failure")])
        self.assertFalse(self.player.is_playing())
        self.assertEqual(self.api.calls[-3:], ["reset", "unprepare", "close"])

    def test_failed_unprepare_retains_native_buffer_and_prevents_overlap(self):
        self.player.play(self.audio, playback_id=7)
        self.player._poll()
        clip = self.player._clip
        self.api.fail_unprepare = True
        with self.assertRaises(VoiceUnavailableError):
            self.player.play(self.audio, playback_id=8)
        self.assertIs(self.player._clip, clip)
        self.assertIs(_OPEN_CLIPS[id(clip)], clip)
        self.assertEqual(self.api.calls.count("write"), 1)
        self.api.fail_unprepare = False
        self.player._poll()
        self.assertFalse(self.player.is_playing())
        self.assertEqual(self.done, [])

    def test_unsupported_pcm_and_invalid_volume_do_not_open_device(self):
        with self.assertRaises(VoiceUnavailableError):
            self.player.play(SpeechAudio(b"\x00" * 3, sample_width=3), playback_id=9)
        with self.assertRaises(VoiceError):
            self.player.play(self.audio, playback_id=9, volume=float("nan"))
        self.factory.assert_not_called()


class WindowsTTSTests(unittest.TestCase):
    def test_wave_decode_preserves_pcm_format(self):
        audio = _decode_wave(encoded_wave())
        self.assertEqual(audio, SpeechAudio(b"\x01\x00\xff\x7f", 22050, 1, 2))

    def test_malformed_and_truncated_wave_are_rejected(self):
        for encoded in ("broken!", None, base64.b64encode(b"RIFF").decode("ascii"), encoded_wave()[:-4]):
            with self.subTest(encoded=encoded):
                with self.assertRaises(VoiceError):
                    _decode_wave(encoded)

    def test_reference_audio_is_explicitly_unsupported(self):
        backend = WindowsTTSBackend()
        with patch.object(backend, "_start") as start:
            with self.assertRaisesRegex(VoiceUnavailableError, "角色音色克隆"):
                backend.synthesize("你好", reference_audio_path=Path("reference.wav"), speech_speed=1)
            start.assert_not_called()
        backend.close()

    def test_invalid_input_is_rejected_before_process_start(self):
        backend = WindowsTTSBackend()
        for text, speed in (("", 1), ("a" * 2001, 1), ("你好", float("nan")), ("你好", 0.1)):
            with self.assertRaises(VoiceError):
                backend.synthesize(text, reference_audio_path=None, speech_speed=speed)
        self.assertIsNone(backend._process)
        backend.close()

    def test_text_is_json_data_and_wrong_response_id_rejected(self):
        class Process:
            stdin = io.BytesIO()
        backend = WindowsTTSBackend(timeout=0.1)
        responses = Queue()
        responses.put({"id": 999, "ok": True, "wav": encoded_wave()})
        text = "你好\n'\"; $(Write-Output injected); </speak>"
        with patch.object(backend, "_start", return_value=(Process(), responses)):
            with self.assertRaisesRegex(VoiceError, "编号"):
                backend.synthesize(text, reference_audio_path=None, speech_speed=1)
        self.assertEqual(json.loads(Process.stdin.getvalue())["text"], text)
        backend.close()

    def test_timeout_is_bounded_and_drops_process(self):
        class Process:
            stdin = io.BytesIO()
        backend = WindowsTTSBackend(timeout=0.02)
        started = time.monotonic()
        with patch.object(backend, "_start", return_value=(Process(), Queue())), patch.object(backend, "_drop_process") as drop:
            with self.assertRaisesRegex(VoiceUnavailableError, "超时"):
                backend.synthesize("你好", reference_audio_path=None, speech_speed=1)
            drop.assert_called_once()
        self.assertLess(time.monotonic() - started, 0.5)
        backend.close()

    def test_close_is_idempotent_and_prevents_later_start(self):
        backend = WindowsTTSBackend()
        backend.close()
        backend.close()
        with self.assertRaisesRegex(VoiceUnavailableError, "关闭"):
            backend.synthesize("你好", reference_audio_path=None, speech_speed=1)

    @unittest.skipUnless(os.name == "nt" and os.environ.get("RUN_WINDOWS_TTS_SMOKE") == "1", "opt-in silent installed-voice synthesis")
    def test_installed_voice_synthesis_reuses_process_without_playback(self):
        with WindowsTTSBackend(timeout=15) as backend:
            first = backend.synthesize("你好，这是本地语音测试。", reference_audio_path=None, speech_speed=1)
            pid = backend._process.pid
            second = backend.synthesize("第二句话。", reference_audio_path=None, speech_speed=1.2)
            self.assertEqual(backend._process.pid, pid)
            self.assertTrue(backend.selected_voice_name)
            self.assertTrue(any(first.data))
            self.assertTrue(any(second.data))
            self.assertEqual(first.sample_width, 2)


if __name__ == "__main__":
    unittest.main()
