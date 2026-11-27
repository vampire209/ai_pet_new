"""Sentence queue bounds, stale callbacks and backend teardown without devices."""

from dataclasses import replace
import os
from pathlib import Path
import sys
from threading import Event
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from voice.fake_backends import FakeAudioPlayer, FakeTTSBackend
from voice.tts_service import TTSService
from voice.voice_models import VoiceSettings, VoiceState
from voice.voice_service import VoiceService


class ClosingTTS(FakeTTSBackend):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.close_count = 0

    def close(self):
        self.close_count += 1


class StreamingVoiceServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["sentence-queue-tests"])

    def setUp(self):
        self.pool = QThreadPool()
        self.voices = []
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        for voice in self.voices:
            voice.shutdown()
        self.assertTrue(self.pool.waitForDone(4000))
        self.app.processEvents()

    def wait(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            QTest.qWait(5)
        self.fail("Voice condition timed out")

    def make(self, backend=None, *, enabled=True, factory=None):
        backend = backend or ClosingTTS()
        player = FakeAudioPlayer()
        voice = VoiceService(
            settings=VoiceSettings(enable_voice_reply=enabled, stream_sentences=True),
            tts_service=TTSService(factory or (lambda settings: backend)),
            audio_player=player, thread_pool=self.pool,
        )
        self.voices.append(voice)
        return voice, backend, player

    def test_prefetch_stops_at_one_clip_and_playback_ids_are_unique(self):
        voice, backend, player = self.make()
        voice.begin_reply()
        voice.append_reply("第一句。第二句。第三句。")
        voice.end_reply()
        self.wait(lambda: len(backend.calls) == 2 and voice._worker is None)
        self.assertEqual(len(player.play_history), 1)
        old_id = player.playback_ids[0]
        player.finish()
        self.wait(lambda: len(backend.calls) == 3 and voice._worker is None)
        self.assertEqual(len(player.play_history), 2)
        player.finished.emit(old_id)
        self.assertEqual(len(player.play_history), 2)
        self.assertTrue(player.is_playing())
        player.finish()
        self.wait(lambda: len(player.play_history) == 3)
        player.finish()
        self.wait(lambda: not voice.is_speaking)
        self.assertEqual(len(set(player.playback_ids)), 3)
        self.assertEqual(voice.state, VoiceState.IDLE)

    def test_backlog_limit_cancels_instead_of_silently_dropping_middle(self):
        voice, backend, player = self.make()
        notices = []
        voice.notice.connect(notices.append)
        voice.begin_reply()
        voice.append_reply("太长的回复。" * 80)
        voice.end_reply()
        self.wait(lambda: not voice.is_busy)
        self.assertFalse(voice.is_speaking)
        self.assertEqual(player.play_history, [])
        self.assertEqual(backend.calls, [])
        self.assertTrue(any("积压过多" in message for message in notices))

    def test_stream_failure_discards_queued_sentences_then_next_reply_recovers(self):
        voice, backend, player = self.make(ClosingTTS(error=RuntimeError("mock error")))
        errors = []
        voice.error.connect(errors.append)
        voice.begin_reply()
        voice.append_reply("第一句。第二句。")
        voice.end_reply()
        self.wait(lambda: not voice.is_busy)
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(player.play_history, [])
        backend.error = None
        voice.begin_reply()
        voice.append_reply("新回复。")
        voice.end_reply()
        self.wait(player.is_playing)
        self.assertEqual(backend.calls[-1]["text"], "新回复。")

    def test_preview_works_with_auto_reply_off_without_changing_settings(self):
        voice, backend, player = self.make(enabled=False)
        before = voice.settings
        self.assertFalse(voice.begin_reply())
        self.assertTrue(voice.preview("本地试听。"))
        self.wait(player.is_playing)
        self.assertEqual(voice.settings, before)
        self.assertEqual(backend.calls[0]["text"], "本地试听。")

    def test_empty_end_does_not_create_an_audio_job(self):
        voice, backend, player = self.make()
        voice.begin_reply()
        voice.end_reply()
        voice.end_reply()
        self.assertFalse(voice.is_speaking)
        self.assertEqual(backend.calls, [])
        self.assertEqual(player.play_history, [])

    def test_malformed_oversized_markup_fails_only_voice(self):
        voice, backend, player = self.make()
        errors = []
        voice.error.connect(errors.append)
        voice.begin_reply()
        voice.append_reply("`" + "没有闭合" * 5000)
        self.assertEqual(len(errors), 1)
        self.assertFalse(voice.is_speaking)
        self.assertEqual(backend.calls, [])
        self.assertEqual(player.play_history, [])
        self.assertTrue(voice.settings.enable_voice_reply)

    def test_backend_is_closed_once_at_shutdown(self):
        voice, backend, player = self.make()
        voice.preview("关闭测试。")
        self.wait(lambda: player.is_playing() and not voice.is_busy)
        voice.shutdown()
        voice.shutdown()
        self.assertEqual(backend.close_count, 1)
        self.assertEqual(voice.state, VoiceState.CLOSED)
        self.assertFalse(player.is_playing())

    def test_factory_finishing_after_shutdown_is_closed_without_synthesis(self):
        gate, started = Event(), Event()
        self.gates.append(gate)
        backend = ClosingTTS()
        def factory(settings):
            started.set()
            gate.wait(3)
            return backend
        voice, _, player = self.make(backend, factory=factory)
        voice.preview("不会读出来。")
        self.wait(started.is_set)
        voice.shutdown()
        gate.set()
        self.wait(lambda: not voice.is_busy)
        self.assertEqual(backend.close_count, 1)
        self.assertEqual(backend.calls, [])
        self.assertEqual(player.play_history, [])

    def test_new_standalone_speech_replaces_entire_stream_queue(self):
        voice, backend, player = self.make()
        voice.begin_reply()
        voice.append_reply("旧一。旧二。旧三。")
        voice.end_reply()
        self.wait(lambda: len(backend.calls) == 2 and voice._worker is None)
        voice.speak("替换后的单条。")
        self.wait(lambda: player.is_playing() and not voice.is_busy)
        self.assertEqual([call["text"] for call in backend.calls], ["旧一。", "旧二。", "替换后的单条。"])
        self.assertEqual(len(player.play_history), 2)


if __name__ == "__main__":
    unittest.main()
