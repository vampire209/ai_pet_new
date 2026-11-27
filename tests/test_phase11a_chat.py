"""Phase 11A chat integration with real Qt workers and zero-network fakes."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import sys
from threading import Event, get_ident
import time
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ai.ability_modes import ABILITY_ENGLISH_TEACHER
from ai.chat_action_router import ChatActionRouter
from ai.chat_service import ChatService
from ai.chat_ui_config import ChatUiSettings
from ai.fake_client import FakeDeepSeekClient, fake_api_error
from ai.persona_builder import PersonaBuilder
from ai.profile import PetProfile, RELATIONSHIP_GIRLFRIEND
from ai.profile_service import ProfileService
from chat_window import ChatWindow, clean_chat_markdown
from memory.memory_service import MemoryService
from natural_language_parser import NaturalLanguageParser
from todo_manager import TodoManager
from voice.fake_backends import FakeAudioPlayer, FakeSTTBackend, FakeTTSBackend
from voice.stt_service import STTService
from voice.text import prepare_text_for_speech
from voice.tts_service import TTSService
from voice.voice_models import VoiceSettings, VoiceState
from voice.voice_service import VoiceService
from word_learning.reading_suggestions import (
    VOCABULARY_BLOCK_END,
    VOCABULARY_BLOCK_START,
)
from word_learning.service import WordLearningService


FIXED_NOW = datetime(2026, 9, 13, 10, 0)


class RecordingTTS(FakeTTSBackend):
    """Record inference text/thread and optionally hold or fail one request."""

    def __init__(self, *, gate: Event | None = None, fail_first: bool = False):
        super().__init__()
        self.requested_texts: list[str] = []
        self.inference_threads: list[int] = []
        self.gate = gate
        self.fail_first = fail_first

    def synthesize(self, text, *, reference_audio_path=None, speech_speed=1.0):
        self.requested_texts.append(text)
        self.inference_threads.append(get_ident())
        if self.gate is not None:
            if not self.gate.wait(3):
                raise RuntimeError("Test did not release the fake TTS gate")
        if self.fail_first and len(self.requested_texts) == 1:
            raise RuntimeError("Deliberate fake TTS failure")
        return super().synthesize(
            text,
            reference_audio_path=reference_audio_path,
            speech_speed=speech_speed,
        )


class RecordingPlayer(FakeAudioPlayer):
    def __init__(self):
        super().__init__()
        self.received_audio = []
        self.playback_threads: list[int] = []

    def play(self, audio, *, playback_id, volume=1.0):
        self.received_audio.append(audio)
        self.playback_threads.append(get_ident())
        return super().play(audio, playback_id=playback_id, volume=volume)


class Phase11AChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["phase11a-chat-tests"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.audio_path = self.root / "fake-input.wav"
        self.audio_path.touch()
        self.windows = []
        self.voices = []
        self.pools = []
        self.resources = []
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        for window in self.windows:
            window.shutdown()
        for voice in self.voices:
            voice.shutdown()
        for pool in self.pools:
            self.assertTrue(pool.waitForDone(4000), "A fake worker failed to stop")
        self.app.processEvents()
        for resource in self.resources:
            resource.close()
        self.temp.cleanup()

    def wait(self, predicate, message="Asynchronous action did not finish"):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(5)
        self.app.processEvents()
        self.assertTrue(predicate(), message)

    def pool(self):
        pool = QThreadPool()
        pool.setMaxThreadCount(2)
        self.pools.append(pool)
        return pool

    def gate(self):
        gate = Event()
        self.gates.append(gate)
        return gate

    def voice(self, *, stt_text="你好", enabled=True, tts=None):
        tts = tts or RecordingTTS()
        player = RecordingPlayer()
        voice = VoiceService(
            settings=VoiceSettings(enable_stt=True, enable_voice_reply=enabled),
            stt_service=STTService(lambda settings: FakeSTTBackend(text=stt_text)),
            tts_service=TTSService(lambda settings: tts),
            audio_player=player,
            thread_pool=self.pool(),
        )
        self.voices.append(voice)
        return voice, tts, player

    def window(self, fake, *, voice=None, **kwargs):
        window = ChatWindow(
            ChatService(client=fake),
            thread_pool=self.pool(),
            voice_service=voice,
            ui_settings=ChatUiSettings(self.root / "chat-ui.json"),
            **kwargs,
        )
        self.windows.append(window)
        return window

    @staticmethod
    def send(window, text):
        window.input.setPlainText(text)
        window.send_message()

    def profiles(self, profile):
        profiles = ProfileService(self.root / "profile.json")
        profiles.save(profile)
        return profiles

    def test_01_default_voice_disabled_keeps_existing_text_chat(self):
        fake = FakeDeepSeekClient((("默认", "文字回复"),))
        window = self.window(fake)
        self.assertFalse(window.voice_service.settings.enable_voice_reply)
        self.assertFalse(window.voice_service.settings.enable_stt)
        self.send(window, "正常聊天")
        self.wait(lambda: not window.is_generating)
        self.assertEqual(window.message_texts[-1], ("assistant", "默认文字回复"))
        self.assertEqual(window.voice_service.state, VoiceState.IDLE)

    def test_02_stt_uses_existing_persona_memory_and_history(self):
        text = "我最近在学傅里叶变换。"
        voice, tts, _player = self.voice(stt_text=text, enabled=False)
        profile = PetProfile(pet_name="小语", relationship_mode=RELATIONSHIP_GIRLFRIEND)
        profiles = self.profiles(profile)
        memory = MemoryService(self.root / "memory.db", now_provider=lambda: FIXED_NOW)
        self.resources.append(memory)
        fake = FakeDeepSeekClient((("我记住了。",),))
        window = self.window(fake, voice=voice, profile_service=profiles, memory_service=memory)
        voice.transcribe(self.audio_path)
        self.wait(lambda: bool(fake.requests) and not window.is_generating)
        self.assertEqual(fake.requests[0][-1], {"role": "user", "content": text})
        self.assertIn(PersonaBuilder().build(profile), fake.requests[0][0]["content"])
        self.assertEqual(window.service.history[0], {"role": "user", "content": text})
        self.assertEqual(
            [item.topic_key for item in memory.active_memories()],
            ["learning:fourier_transform"],
        )
        self.assertEqual(tts.requested_texts, [])

    def test_03_stt_todo_keeps_confirmation_before_write(self):
        voice, _tts, _player = self.voice(stt_text="明天下午3点提醒我交作业")
        manager = TodoManager(self.root / "tasks.json")
        router = ChatActionRouter(NaturalLanguageParser(now_provider=lambda: FIXED_NOW))
        fake = FakeDeepSeekClient(())
        window = self.window(fake, voice=voice, todo_manager=manager, action_router=router)
        voice.transcribe(self.audio_path)
        self.wait(lambda: len(window.todo_cards) == 1)
        self.assertEqual(manager.get_tasks(), ())
        self.assertEqual(fake.requests, [])
        card = window.todo_cards[0]
        self.assertEqual(card.state, "pending")
        card.confirm_button.click()
        self.assertEqual(card.state, "created")
        self.assertEqual(len(manager.get_tasks()), 1)
        self.assertEqual(manager.get_tasks()[0].text, "交作业")

    def test_04_empty_stt_does_not_send_or_replace_input(self):
        voice, tts, _player = self.voice(stt_text=" \n ")
        fake = FakeDeepSeekClient(())
        window = self.window(fake, voice=voice)
        notices = []
        voice.notice.connect(notices.append)
        voice.transcribe(self.audio_path)
        self.wait(lambda: not voice.is_busy)
        self.assertEqual(fake.requests, [])
        self.assertEqual(window.message_texts, ())
        self.assertEqual(window.input.toPlainText(), "")
        self.assertTrue(any("没有听清" in message for message in notices))
        self.assertEqual(tts.requested_texts, [])

    def test_05_stt_does_not_overwrite_user_draft_or_busy_turn(self):
        voice, _tts, _player = self.voice(stt_text="识别文字", enabled=False)
        gate = self.gate()

        def reply():
            yield "等待"
            gate.wait(3)
            yield "完成"

        fake = FakeDeepSeekClient((reply(),))
        window = self.window(fake, voice=voice)
        window.input.setPlainText("尚未发送的草稿")
        voice.transcribe(self.audio_path)
        self.wait(lambda: not voice.is_busy)
        self.assertEqual(window.input.toPlainText(), "尚未发送的草稿")
        self.assertEqual(fake.requests, [])
        self.send(window, "第一轮")
        self.wait(lambda: bool(fake.requests))
        window.input.setPlainText("下一轮草稿")
        self.assertFalse(window.submit_recognized_text("请不要覆盖"))
        self.assertEqual(window.input.toPlainText(), "下一轮草稿")
        self.assertEqual(len(fake.requests), 1)
        gate.set()
        self.wait(lambda: not window.is_generating)

    def test_06_only_complete_reply_speaks_once_and_preserves_chat_text(self):
        voice, tts, player = self.voice()
        gate = self.gate()
        first = "**当然可以！** 😊\n"
        second = "今天学习 `Fourier Transform`。"

        def reply():
            yield first
            gate.wait(3)
            yield second

        window = self.window(FakeDeepSeekClient((reply(),)), voice=voice)
        self.send(window, "讲一讲")
        self.wait(lambda: "当然可以" in window.message_texts[-1][1])
        self.assertTrue(window.is_generating)
        self.assertEqual(tts.requested_texts, [])
        gate.set()
        self.wait(lambda: player.is_playing() and not window.is_generating)
        self.assertEqual(tts.requested_texts, [prepare_text_for_speech(first + second)])
        self.assertEqual(window.service.history[-1]["content"], first + second)
        self.assertEqual(window.message_texts[-1][1], clean_chat_markdown(first + second))
        self.assertNotEqual(tts.inference_threads[0], get_ident())
        self.assertEqual(player.playback_threads, [get_ident()])
        self.assertEqual(len(player.received_audio), 1)

    def test_07_hidden_reading_vocabulary_json_is_never_spoken(self):
        voice, tts, player = self.voice()
        profiles = self.profiles(PetProfile(ability_mode=ABILITY_ENGLISH_TEACHER))
        learning = WordLearningService(self.root / "words.db")
        self.resources.append(learning)
        visible = "**句子分析**\nmaintain 在这里表示坚持认为。"
        payload = {"suggested_vocabulary": [{
            "word": "maintain", "meaning": "坚持认为", "exam_meaning": "主张",
            "part_of_speech": "v.", "source_sentence": "Some researchers maintain it works.",
        }]}
        raw = (visible + "\n" + VOCABULARY_BLOCK_START + "\n"
               + json.dumps(payload, ensure_ascii=False) + "\n" + VOCABULARY_BLOCK_END)
        window = self.window(
            FakeDeepSeekClient(((raw,),)), voice=voice,
            profile_service=profiles, word_learning_service=learning,
        )
        self.send(window, "请分析阅读长难句 Some researchers maintain it works.")
        self.wait(lambda: player.is_playing() and not window.is_generating)
        self.assertEqual(tts.requested_texts, [prepare_text_for_speech(visible)])
        self.assertNotIn("suggested_vocabulary", window.message_texts[-1][1])
        self.assertEqual(window.service.history[-1]["content"], visible)
        self.assertEqual(len(window.suggested_vocabulary_cards), 1)

    def test_08_voice_off_keeps_complete_reply_without_tts(self):
        voice, tts, player = self.voice(enabled=False)
        window = self.window(FakeDeepSeekClient((("完整回答。",),)), voice=voice)
        self.send(window, "继续")
        self.wait(lambda: not window.is_generating)
        self.assertEqual(window.service.history[-1]["content"], "完整回答。")
        self.assertEqual(tts.requested_texts, [])
        self.assertFalse(player.is_playing())

    def test_09_new_local_message_stops_old_voice(self):
        voice, _tts, player = self.voice()
        fake = FakeDeepSeekClient((("正在朗读的第一条回复。",),))
        window = self.window(fake, voice=voice)
        self.send(window, "你好")
        self.wait(player.is_playing)
        self.send(window, "明天提醒我学习")
        self.assertFalse(player.is_playing())
        self.assertEqual(voice.state, VoiceState.IDLE)
        self.assertEqual(len(fake.requests), 1)
        self.assertIn("几点", window.message_texts[-1][1])

    def test_10_new_message_invalidates_slow_previous_synthesis(self):
        gate = self.gate()
        tts = RecordingTTS(gate=gate)
        voice, _tts, player = self.voice(tts=tts)
        window = self.window(FakeDeepSeekClient((("旧语音。",),)), voice=voice)
        self.send(window, "第一条")
        self.wait(lambda: bool(tts.requested_texts) and not window.is_generating)
        self.send(window, "明天提醒我学习")
        gate.set()
        self.wait(lambda: not voice.is_busy)
        self.assertFalse(player.is_playing())
        self.assertEqual(player.received_audio, [])

    def test_11_tts_failure_preserves_completed_chat_and_next_turn(self):
        voice, tts, player = self.voice(tts=RecordingTTS(fail_first=True))
        errors = []
        voice.error.connect(errors.append)
        fake = FakeDeepSeekClient((("第一条完整回复。",), ("第二条正常回复。",)))
        window = self.window(fake, voice=voice)
        self.send(window, "第一条")
        self.wait(lambda: bool(errors) and not window.is_generating and not voice.is_busy)
        self.assertEqual(window.message_texts[-1], ("assistant", "第一条完整回复。"))
        self.assertEqual(window.service.history[-1]["content"], "第一条完整回复。")
        self.assertFalse(window.service.is_busy)
        self.send(window, "第二条")
        self.wait(lambda: player.is_playing() and not window.is_generating)
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(len(tts.requested_texts), 2)
        self.assertEqual(window.message_texts[-1], ("assistant", "第二条正常回复。"))

    def test_12_stopped_or_failed_chat_does_not_speak(self):
        voice, tts, player = self.voice()
        gate = self.gate()

        def reply():
            yield "尚未完成"
            gate.wait(3)
            yield "不会朗读"

        fake = FakeDeepSeekClient((reply(), fake_api_error("模拟文字失败")))
        window = self.window(fake, voice=voice)
        self.send(window, "等待停止")
        self.wait(lambda: "尚未完成" in window.message_texts[-1][1])
        window.stop_generation()
        gate.set()
        self.wait(lambda: not window.is_generating)
        self.assertEqual(tts.requested_texts, [])
        self.send(window, "模拟失败")
        self.wait(lambda: not window.is_generating)
        self.assertEqual(tts.requested_texts, [])
        self.assertFalse(player.is_playing())
        self.assertIn("模拟文字失败", window.message_texts[-1][1])

    def test_13_close_suppresses_already_queued_completed_reply(self):
        voice, tts, player = self.voice()
        gate = self.gate()
        emitted = Event()

        def reply():
            gate.wait(3)
            yield "完成信号已排队。"

        window = self.window(FakeDeepSeekClient((reply(),)), voice=voice)
        self.send(window, "关闭竞态")
        window._worker.signals.completed.connect(
            lambda _text: emitted.set(), Qt.ConnectionType.DirectConnection,
        )
        gate.set()
        self.assertTrue(emitted.wait(2), "Chat worker did not emit completion")
        # The worker finished, but the UI has not yet processed its queued signal.
        window.close()
        self.wait(lambda: not window.is_generating)
        self.assertEqual(tts.requested_texts, [])
        self.assertFalse(player.is_playing())
        self.assertNotEqual(voice.state, VoiceState.CLOSED)
        window.show()
        self.send(window, "再次打开")
        self.wait(lambda: player.is_playing() and not window.is_generating)

    def test_14_clear_stops_playback_and_shutdown_rejects_late_synthesis(self):
        voice, _tts, player = self.voice()
        window = self.window(FakeDeepSeekClient((("准备清空。",),)), voice=voice)
        self.send(window, "第一条")
        self.wait(lambda: player.is_playing() and not window.is_generating)
        window.clear_chat()
        self.assertFalse(player.is_playing())
        self.assertEqual(window.message_texts, ())
        self.assertEqual(window.service.history, ())

        gate = self.gate()
        slow_voice, slow_tts, slow_player = self.voice(tts=RecordingTTS(gate=gate))
        slow_window = self.window(FakeDeepSeekClient((("退出后的旧语音。",),)), voice=slow_voice)
        self.send(slow_window, "准备退出")
        self.wait(lambda: bool(slow_tts.requested_texts))
        slow_window.shutdown()
        slow_window.shutdown()
        gate.set()
        self.wait(lambda: not slow_voice.is_busy)
        self.assertEqual(slow_voice.state, VoiceState.CLOSED)
        self.assertEqual(slow_player.received_audio, [])


if __name__ == "__main__":
    unittest.main()
