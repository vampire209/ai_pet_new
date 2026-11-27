"""Desktop controls and bounded/cancellable sentence delivery without a model."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ai.chat_service import ChatService
from ai.fake_client import FakeDeepSeekClient
from ai.chat_ui_config import ChatUiSettings
from ai.profile_service import ProfileService
from chat_window import ChatWindow
from voice.fake_backends import FakeAudioPlayer, FakeTTSBackend
from voice.runtime import create_desktop_voice_service
from voice.streaming_text import split_for_speech
from voice.tts_service import TTSService
from voice.voice_models import SpeechAudio, VoiceCancelledError, VoiceSettings
from voice.voice_service import VoiceService


class Interruptible(FakeTTSBackend):
    def __init__(self):
        super().__init__()
        self.started = Event()
        self.cancel_count = 0

    def synthesize_cancellable(self, text, *, cancelled, **kwargs):
        self.calls.append({'text': text})
        self.started.set()
        if text == '旧语音。':
            if not cancelled.wait(3):
                raise TimeoutError('test cancellation not received')
            raise VoiceCancelledError('cancelled')
        return SpeechAudio(text.encode('utf-16-le'))

    def cancel_current(self):
        self.cancel_count += 1


class VoicePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(['voice-pipeline-test'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pool = QThreadPool()
        self.voices, self.windows = [], []

    def tearDown(self):
        for window in self.windows: window.shutdown()
        for voice in self.voices: voice.shutdown()
        self.assertTrue(self.pool.waitForDone(5000))
        self.app.processEvents()
        self.temp.cleanup()

    def wait(self, predicate):
        until = time.monotonic() + 3
        while time.monotonic() < until:
            self.app.processEvents()
            if predicate(): return
            QTest.qWait(5)
        self.fail('Voice state timed out')

    def make(self, backend=None):
        backend = backend or FakeTTSBackend()
        player = FakeAudioPlayer()
        voice = VoiceService(settings=VoiceSettings(enable_voice_reply=True, stream_sentences=True),
                    tts_service=TTSService(lambda settings: backend), audio_player=player, thread_pool=self.pool)
        self.voices.append(voice)
        return voice, backend, player

    def window(self, voice, fake=None):
        window = ChatWindow(ChatService(client=fake or FakeDeepSeekClient(())), voice_service=voice,
                            profile_service=ProfileService(self.root / 'profile.json'),
                            ui_settings=ChatUiSettings(self.root / 'chat.json'), thread_pool=self.pool)
        self.windows.append(window)
        return window

    def test_desktop_factory_off_never_spawns_model(self):
        with patch('subprocess.Popen') as spawn:
            voice = create_desktop_voice_service()
            self.voices.append(voice)
            self.assertFalse(voice.settings.enable_voice_reply)
            self.assertEqual(voice.settings.tts_backend, 'auto')
            self.assertFalse(voice.speak('不会朗读'))
            spawn.assert_not_called()

    def test_full_long_reply_stays_in_one_bounded_sentence_queue(self):
        voice, backend, player = self.make()
        text = '很长的内容' * 110 + '。结尾。'
        expected = split_for_speech(text)
        self.assertTrue(all(len(sentence) <= 240 for sentence in expected))
        voice.speak(text)
        for index in range(len(expected)):
            self.wait(lambda: len(player.play_history) >= index + 1)
            player.finish()
        self.wait(lambda: not voice.is_busy and not voice.is_speaking)
        self.assertEqual([call['text'] for call in backend.calls], expected)

    def test_replacement_cancels_real_backend_token_and_discards_old(self):
        voice, backend, player = self.make(Interruptible())
        voice.speak('旧语音。')
        self.wait(backend.started.is_set)
        voice.speak('新语音。')
        self.wait(lambda: player.is_playing() and not voice.is_busy)
        self.assertEqual([a.data.decode('utf-16-le') for a in player.play_history], ['新语音。'])
        self.assertGreater(backend.cancel_count, 0)

    def test_new_chat_message_cancels_pending_inference_keeps_text(self):
        voice, backend, player = self.make(Interruptible())
        window = self.window(voice, FakeDeepSeekClient((('新回复。',),)))
        voice.preview('旧语音。')
        self.wait(backend.started.is_set)
        window.input.setPlainText('说一句话')
        window.send_message()
        self.wait(lambda: player.is_playing() and not window.is_generating)
        self.assertEqual(window.service.history[-1]['content'], '新回复。')
        self.assertEqual([a.data.decode('utf-16-le') for a in player.play_history], ['新回复。'])

    def test_provider_controls_stop_old_voice_and_change_next_request(self):
        voice, backend, player = self.make()
        window = self.window(voice)
        voice.preview('一段正在播放的语音。')
        self.wait(player.is_playing)
        window.voice_backend_combo.setCurrentIndex(window.voice_backend_combo.findData('local_neural'))
        self.assertFalse(player.is_playing())
        self.assertEqual(voice.settings.tts_backend, 'local_neural')
        self.assertIn('Serena', window.voice_name_label.text())
        window.voice_backend_combo.setCurrentIndex(window.voice_backend_combo.findData('system'))
        self.assertIn('Windows', window.voice_name_label.text())

    def test_cleanup_failure_does_not_break_text_or_stop(self):
        voice, backend, player = self.make()
        backend.cancel_current = lambda: (_ for _ in ()).throw(RuntimeError('cleanup failed'))
        voice.preview('这句话可以停止。')
        self.wait(player.is_playing)
        # Exercise the service's public cancellation even if worker already ended.
        voice._tts.cancel_current()
        self.assertTrue(voice.stop())
        self.assertFalse(player.is_playing())


if __name__ == '__main__': unittest.main()
