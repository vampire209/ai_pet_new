"""Sentence-streamed voice chat integration, with silent local Qt test doubles."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
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

from ai.chat_service import ChatService
from ai.chat_ui_config import ChatUiSettings
from ai.fake_client import FakeDeepSeekClient
from chat_window import ChatWindow
from memory.memory_service import MemoryService
from todo_manager import TodoManager
from voice.fake_backends import FakeAudioPlayer, FakeTTSBackend
from voice.tts_service import TTSService
from voice.voice_models import SpeechAudio, VoiceSettings
from voice.voice_service import VoiceService


class TextRecordingTTS(FakeTTSBackend):
    """Encode each requested sentence into distinct silent-player test data."""

    def __init__(self, *, gate: Event | None = None):
        super().__init__()
        self.gate = gate
        self.started = Event()

    def synthesize(self, text, *, reference_audio_path=None, speech_speed=1.0):
        super().synthesize(
            text, reference_audio_path=reference_audio_path,
            speech_speed=speech_speed,
        )
        self.started.set()
        if self.gate is not None and not self.gate.wait(4):
            raise RuntimeError("Test did not release fake synthesis")
        return SpeechAudio(text.encode("utf-16-le"))

    @property
    def texts(self):
        return [call["text"] for call in self.calls]


class OrderedSilentPlayer(FakeAudioPlayer):
    """Report accidental replacement of a clip which has not finished yet."""

    def __init__(self):
        super().__init__()
        self.overlap_count = 0

    def play(self, audio, *, playback_id, volume=1.0):
        if self.is_playing():
            self.overlap_count += 1
        super().play(audio, playback_id=playback_id, volume=volume)

    @property
    def texts(self):
        return [audio.data.decode("utf-16-le") for audio in self.play_history]


class StreamingVoiceChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["streaming-voice-tests"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.windows = []
        self.voices = []
        self.pools = []
        self.gates = []
        self.resources = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        for window in self.windows:
            window.shutdown()
        for voice in self.voices:
            voice.shutdown()
        for pool in self.pools:
            self.assertTrue(pool.waitForDone(5000), "Fake worker did not finish")
        self.app.processEvents()
        for resource in self.resources:
            resource.close()
        self.temp.cleanup()

    def pool(self):
        pool = QThreadPool()
        pool.setMaxThreadCount(2)
        self.pools.append(pool)
        return pool

    def gate(self):
        gate = Event()
        self.gates.append(gate)
        return gate

    def wait(self, predicate, message="Asynchronous fake operation timed out"):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(5)
        self.app.processEvents()
        self.assertTrue(predicate(), message)

    def settle(self):
        # Deliver queued worker/player callbacks before a negative assertion.
        for _ in range(5):
            self.app.processEvents()
            QTest.qWait(5)

    def voice(self, *, enabled=True, tts=None):
        tts = tts or TextRecordingTTS()
        player = OrderedSilentPlayer()
        voice = VoiceService(
            settings=VoiceSettings(
                enable_voice_reply=enabled, stream_sentences=True,
            ),
            tts_service=TTSService(lambda _settings: tts),
            audio_player=player,
            thread_pool=self.pool(),
        )
        self.voices.append(voice)
        return voice, tts, player

    def window(self, fake, *, voice, **kwargs):
        window = ChatWindow(
            ChatService(client=fake), voice_service=voice,
            thread_pool=self.pool(),
            ui_settings=ChatUiSettings(self.root / "chat-ui.json"),
            **kwargs,
        )
        self.windows.append(window)
        return window

    @staticmethod
    def send(window, text="和我聊聊天"):
        window.input.setPlainText(text)
        window.send_message()

    def finish_sentences(self, voice, player, expected):
        for index, sentence in enumerate(expected, 1):
            self.wait(
                lambda: len(player.play_history) >= index and player.is_playing(),
                f"Sentence {index} was never played",
            )
            self.assertEqual(player.texts, expected[:index])
            self.assertEqual(player.texts[-1], sentence)
            player.finish()
        self.wait(lambda: not voice.is_busy and not player.is_playing())
        self.settle()
        self.assertEqual(player.texts, expected)
        self.assertEqual(player.overlap_count, 0)

    def test_first_sentence_plays_while_reply_is_still_streaming(self):
        gate = self.gate()

        def response():
            yield "第一句已经说完。"
            gate.wait(4)
            yield "第二句随后到达。"

        voice, tts, player = self.voice()
        fake = FakeDeepSeekClient((response(),))
        window = self.window(fake, voice=voice)
        self.send(window)
        self.wait(player.is_playing)
        self.assertTrue(window.is_generating)
        self.assertEqual(tts.texts, ["第一句已经说完。"])
        self.assertEqual(window.service.history, ())
        gate.set()
        self.wait(lambda: not window.is_generating)
        self.finish_sentences(voice, player, ["第一句已经说完。", "第二句随后到达。"])
        self.assertEqual(len(fake.requests), 1)

    def test_order_and_unpunctuated_tail_flush_without_full_reply_replay(self):
        voice, tts, player = self.voice()
        chunks = ("第一", "句。第", "二句。", "最后没有", "句号")
        window = self.window(FakeDeepSeekClient((chunks,)), voice=voice)
        self.send(window)
        self.wait(lambda: not window.is_generating)
        expected = ["第一句。", "第二句。", "最后没有句号"]
        self.finish_sentences(voice, player, expected)
        self.assertEqual(tts.texts, expected)
        self.assertEqual(window.service.history[-1]["content"], "".join(chunks))
        self.assertEqual(window.message_texts[-1][1], "".join(chunks))

    def test_slow_playback_keeps_every_sentence_without_overlapping(self):
        voice, tts, player = self.voice()
        expected = [f"这是第{number}句。" for number in range(1, 7)]
        window = self.window(FakeDeepSeekClient((expected,)), voice=voice)
        self.send(window)
        self.wait(lambda: not window.is_generating and player.is_playing())
        self.settle()
        self.assertEqual(player.texts, expected[:1])
        self.assertEqual(player.overlap_count, 0)
        self.finish_sentences(voice, player, expected)
        self.assertEqual(tts.texts, expected)

    def test_fenced_code_split_across_chunks_is_not_spoken(self):
        voice, tts, player = self.voice()
        raw = "先说一句。\n```python\nprint('不应朗读。')\n```\n然后继续解释。"
        window = self.window(FakeDeepSeekClient((list(raw),)), voice=voice)
        self.send(window)
        self.wait(lambda: not window.is_generating)
        expected = ["先说一句。", "然后继续解释。"]
        self.finish_sentences(voice, player, expected)
        self.assertEqual(tts.texts, expected)
        self.assertIn("print", window.service.history[-1]["content"])

    def test_stop_voice_keeps_text_generation_and_suppresses_later_sentences(self):
        gate = self.gate()

        def response():
            yield "已经开始朗读。"
            gate.wait(4)
            yield "文字还会继续。结尾没有标点"

        voice, _tts, player = self.voice()
        fake = FakeDeepSeekClient((response(),))
        window = self.window(fake, voice=voice)
        self.send(window)
        self.wait(player.is_playing)
        window.voice_stop_button.click()
        self.assertFalse(player.is_playing())
        self.assertTrue(window.is_generating)
        self.assertTrue(voice.settings.enable_voice_reply)
        gate.set()
        self.wait(lambda: not window.is_generating and not voice.is_busy)
        self.settle()
        self.assertEqual(player.texts, ["已经开始朗读。"])
        self.assertEqual(window.service.history[-1]["content"],
                         "已经开始朗读。文字还会继续。结尾没有标点")
        self.assertEqual(len(fake.requests), 1)

    def test_off_then_on_does_not_resume_current_turn_but_next_turn_speaks(self):
        gate = self.gate()

        def first_response():
            yield "原先正在朗读。"
            gate.wait(4)
            yield "当前轮剩余内容不追读。"

        voice, _tts, player = self.voice()
        fake = FakeDeepSeekClient((first_response(), ("新一轮正常朗读。",)))
        window = self.window(fake, voice=voice)
        self.assertTrue(window.voice_toggle_button.isChecked())
        self.send(window)
        self.wait(player.is_playing)
        window.voice_toggle_button.click()
        self.assertFalse(voice.settings.enable_voice_reply)
        self.assertFalse(player.is_playing())
        window.voice_toggle_button.click()
        self.assertTrue(voice.settings.enable_voice_reply)
        gate.set()
        self.wait(lambda: not window.is_generating and not voice.is_busy)
        self.settle()
        self.assertEqual(player.texts, ["原先正在朗读。"])
        self.send(window, "继续聊")
        self.wait(lambda: len(player.texts) == 2 and not window.is_generating)
        self.assertEqual(player.texts, ["原先正在朗读。", "新一轮正常朗读。"])
        player.finish()
        self.wait(lambda: not voice.is_busy)

    def test_voice_off_never_starts_synthesis_but_text_completes(self):
        voice, tts, player = self.voice(enabled=False)
        window = self.window(FakeDeepSeekClient((("保持文字聊天。",),)), voice=voice)
        self.assertFalse(window.voice_toggle_button.isChecked())
        self.send(window)
        self.wait(lambda: not window.is_generating)
        self.assertEqual(tts.texts, [])
        self.assertEqual(player.texts, [])
        self.assertEqual(window.service.history[-1]["content"], "保持文字聊天。")

    def test_local_preview_needs_no_chat_api_and_changes_no_chat_or_business_data(self):
        voice, tts, player = self.voice(enabled=False)
        fake = FakeDeepSeekClient(())
        fake.is_configured = False
        manager = TodoManager(self.root / "tasks.json")
        memory = MemoryService(self.root / "memory.db")
        self.resources.append(memory)
        window = self.window(fake, voice=voice, todo_manager=manager,
                             memory_service=memory)
        window.input.setPlainText("保留未发送的草稿")
        window.voice_preview_button.click()
        self.wait(player.is_playing)
        self.assertEqual(fake.requests, [])
        self.assertEqual(window.service.history, ())
        self.assertEqual(window.message_texts, ())
        self.assertEqual(window.input.toPlainText(), "保留未发送的草稿")
        self.assertEqual(manager.get_tasks(), ())
        self.assertEqual(memory.active_memories(), ())
        self.assertEqual(len(tts.texts), 1)
        self.assertTrue(tts.texts[0].strip())
        self.assertTrue(window.voice_status_label.text().strip())
        self.assertFalse(voice.settings.enable_voice_reply)
        self.assertFalse(window.voice_toggle_button.isChecked())

    def test_chat_error_discards_already_queued_audio(self):
        from ai.fake_client import fake_api_error
        gate = self.gate()
        def response():
            yield "正在播放。后面还有一句。"
            gate.wait(4)
            raise fake_api_error("模拟中途失败")
        voice, _tts, player = self.voice()
        window = self.window(FakeDeepSeekClient((response(),)), voice=voice)
        self.send(window)
        self.wait(player.is_playing)
        gate.set()
        self.wait(lambda: not window.is_generating and not voice.is_busy)
        self.assertFalse(player.is_playing())
        self.assertEqual(len(player.texts), 1)
        self.assertIn("模拟中途失败", window.message_texts[-1][1])

    def test_voice_buttons_fit_minimum_window_width(self):
        voice, _tts, _player = self.voice(enabled=False)
        window = self.window(FakeDeepSeekClient(()), voice=voice)
        window.resize(600, 650)
        window.show()
        self.app.processEvents()
        buttons = (window.voice_toggle_button, window.voice_preview_button, window.voice_stop_button)
        for button in buttons:
            self.assertTrue(button.isVisible())
            self.assertTrue(button.parentWidget().rect().contains(button.geometry()))
        for left, right in zip(buttons, buttons[1:]):
            self.assertLess(left.geometry().right(), right.geometry().left())

    def test_close_discards_late_synthesis_and_remaining_stream(self):
        synthesis_gate = self.gate()
        voice, tts, player = self.voice(tts=TextRecordingTTS(gate=synthesis_gate))
        window = self.window(FakeDeepSeekClient((("关闭前的一句。后面还有一句。",),)),
                             voice=voice)
        self.send(window)
        self.wait(tts.started.is_set)
        window.close()
        synthesis_gate.set()
        self.wait(lambda: not voice.is_busy and not window.is_generating)
        self.settle()
        self.assertEqual(player.texts, [])
        self.assertFalse(player.is_playing())


if __name__ == "__main__":
    unittest.main()
