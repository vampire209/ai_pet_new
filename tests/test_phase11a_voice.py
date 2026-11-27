"""Voice skeleton tests: silent PCM, lazy fakes, cancellation and error isolation."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
from threading import Event, get_ident
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from voice.fake_backends import FakeAudioPlayer, FakeSTTBackend, FakeTTSBackend
from voice.stt_service import STTService
from voice.text import prepare_text_for_speech
from voice.tts_service import TTSService
from voice.voice_models import SpeechAudio, VoiceSettings, VoiceState
from voice.voice_service import VoiceService


class Phase11AVoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["phase11a-voice-tests"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "caller-owned.wav"
        self.source.touch()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(2)
        self.voices = []
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        for voice in self.voices:
            voice.shutdown()
        self.assertTrue(self.pool.waitForDone(4000))
        self.app.processEvents()
        self.temp.cleanup()

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(5)
        self.fail("Fake voice operation timed out")

    def gate(self):
        event = Event()
        self.gates.append(event)
        return event

    def voice(self, **kwargs):
        voice = VoiceService(thread_pool=self.pool, **kwargs)
        self.voices.append(voice)
        return voice

    def fake_voice(self, *, stt=None, tts=None, player=None, settings=None):
        stt = stt or FakeSTTBackend()
        tts = tts or FakeTTSBackend()
        player = player or FakeAudioPlayer()
        voice = self.voice(
            settings=settings or VoiceSettings(enable_stt=True, enable_voice_reply=True),
            stt_service=STTService(lambda _settings: stt),
            tts_service=TTSService(lambda _settings: tts),
            audio_player=player,
        )
        return voice, stt, tts, player

    def test_default_off_does_not_load_or_open_devices(self):
        loads = []
        voice = self.voice(
            stt_service=STTService(lambda settings: loads.append("stt")),
            tts_service=TTSService(lambda settings: loads.append("tts")),
        )
        self.assertFalse(voice.transcribe(self.source))
        self.assertFalse(voice.speak("你好"))
        self.assertEqual(loads, [])
        self.assertFalse(voice.is_busy)
        self.assertEqual(voice.state, VoiceState.IDLE)

    def test_stt_state_and_callback_on_ui_thread(self):
        threads = []
        class Backend(FakeSTTBackend):
            def transcribe(self, path):
                threads.append(get_ident())
                return super().transcribe(path)
        voice, _, _, _ = self.fake_voice(stt=Backend("  测试文本  "))
        results, states = [], []
        voice.text_recognized.connect(lambda text: results.append((text, get_ident())))
        voice.state_changed.connect(states.append)
        self.assertTrue(voice.transcribe(self.source))
        self.assertEqual(voice.state, VoiceState.PROCESSING)
        self.wait(lambda: not voice.is_busy)
        self.assertEqual(results, [("测试文本", get_ident())])
        self.assertNotEqual(threads[0], get_ident())
        self.assertEqual(states, [VoiceState.PROCESSING, VoiceState.IDLE])
        self.assertTrue(self.source.exists())

    def test_tts_states_and_manual_playback_finish(self):
        voice, _, tts, player = self.fake_voice()
        states = []
        voice.state_changed.connect(states.append)
        self.assertTrue(voice.speak("**你好！** 😊"))
        self.assertEqual(voice.state, VoiceState.SYNTHESIZING)
        self.wait(player.is_playing)
        player.finish()
        self.assertEqual(states, [VoiceState.SYNTHESIZING, VoiceState.PLAYING, VoiceState.IDLE])
        self.assertEqual(tts.calls[0]["text"], "你好。")
        self.assertEqual(list(self.root.iterdir()), [self.source])

    def test_lazily_reuses_models_and_passes_speed_reference_volume(self):
        stt, tts, player = FakeSTTBackend(), FakeTTSBackend(), FakeAudioPlayer()
        loads = []
        settings = VoiceSettings(enable_stt=True, enable_voice_reply=True,
                                 reference_audio_path=str(self.source),
                                 speech_speed=1.2, volume=0.4)
        def stt_factory(settings):
            loads.append(("stt", get_ident()))
            return stt
        def tts_factory(settings):
            loads.append(("tts", get_ident()))
            return tts
        voice = self.voice(settings=settings, stt_service=STTService(stt_factory),
                           tts_service=TTSService(tts_factory), audio_player=player)
        self.assertEqual(loads, [])
        for _ in range(2):
            voice.transcribe(self.source)
            self.wait(lambda: not voice.is_busy)
            voice.speak("测试")
            self.wait(lambda: player.is_playing() and not voice.is_busy)
            player.finish()
        self.assertEqual([kind for kind, _ in loads], ["stt", "tts"])
        self.assertTrue(all(thread != get_ident() for _, thread in loads))
        self.assertEqual(tts.calls[-1]["reference_audio_path"], self.source)
        self.assertEqual(tts.calls[-1]["speech_speed"], 1.2)
        self.assertEqual(player.volumes, [0.4, 0.4])
        voice.configure(replace(settings, tts_model_path=str(self.root)))
        voice.speak("新模型")
        self.wait(lambda: player.is_playing() and not voice.is_busy)
        self.assertEqual([kind for kind, _ in loads], ["stt", "tts", "tts"])

    def test_model_load_failure_can_retry(self):
        attempts = []
        def factory(settings):
            attempts.append(settings)
            if len(attempts) == 1:
                raise RuntimeError("load failed")
            return FakeTTSBackend()
        player = FakeAudioPlayer()
        voice = self.voice(settings=VoiceSettings(enable_voice_reply=True),
                           tts_service=TTSService(factory), audio_player=player)
        errors = []
        voice.error.connect(errors.append)
        voice.speak("首次失败")
        self.wait(lambda: not voice.is_busy)
        self.assertEqual(len(errors), 1)
        voice.speak("第二次重试")
        self.wait(player.is_playing)
        self.assertEqual(len(attempts), 2)

    def test_missing_tts_model_disables_voice_without_loading(self):
        voice, _, tts, player = self.fake_voice(settings=VoiceSettings(
            enable_voice_reply=True, tts_model_path=str(self.root / "absent-model")))
        errors = []
        voice.error.connect(errors.append)
        voice.speak("模型缺失")
        self.wait(lambda: not voice.is_busy)
        self.assertFalse(voice.settings.enable_voice_reply)
        self.assertEqual(tts.calls, [])
        self.assertFalse(player.is_playing())
        self.assertIn("路径不存在", errors[0])

    def test_missing_reference_disables_tts_without_touching_files(self):
        voice, _, tts, _ = self.fake_voice(settings=VoiceSettings(
            enable_voice_reply=True, reference_audio_path=str(self.root / "missing.wav")))
        voice.speak("参考素材缺失")
        self.wait(lambda: not voice.is_busy)
        self.assertFalse(voice.settings.enable_voice_reply)
        self.assertEqual(tts.calls, [])
        self.assertTrue(self.source.exists())

    def test_missing_stt_model_disables_only_stt(self):
        voice, stt, _, _ = self.fake_voice(settings=VoiceSettings(
            enable_stt=True, enable_voice_reply=True,
            stt_model_path=str(self.root / "missing")))
        voice.transcribe(self.source)
        self.wait(lambda: not voice.is_busy)
        self.assertFalse(voice.settings.enable_stt)
        self.assertTrue(voice.settings.enable_voice_reply)
        self.assertEqual(stt.calls, [])

    def test_missing_recording_is_recoverable(self):
        voice, stt, _, _ = self.fake_voice()
        errors = []
        voice.error.connect(errors.append)
        voice.transcribe(self.root / "missing.wav")
        self.wait(lambda: not voice.is_busy)
        self.assertEqual(len(errors), 1)
        self.assertTrue(voice.settings.enable_stt)
        voice.transcribe(self.source)
        self.wait(lambda: not voice.is_busy)
        self.assertEqual(stt.calls, [self.source])

    def test_unconfigured_optional_backends_fail_safely(self):
        voice = self.voice(settings=VoiceSettings(enable_stt=True, enable_voice_reply=True))
        errors = []
        voice.error.connect(errors.append)
        voice.transcribe(self.source)
        self.wait(lambda: not voice.is_busy)
        voice.speak("没有后端")
        self.wait(lambda: not voice.is_busy)
        self.assertEqual(len(errors), 2)
        self.assertFalse(voice.settings.enable_stt)
        self.assertFalse(voice.settings.enable_voice_reply)
        self.assertTrue(all("尚未配置" in error for error in errors))

    def test_empty_recognition_is_not_emitted(self):
        voice, _, _, _ = self.fake_voice(stt=FakeSTTBackend(" \n "))
        texts, notices = [], []
        voice.text_recognized.connect(texts.append)
        voice.notice.connect(notices.append)
        voice.transcribe(self.source)
        self.wait(lambda: not voice.is_busy)
        self.assertEqual(texts, [])
        self.assertIn("没有听清，再说一次吧。", notices)

    def test_stt_error_is_contained_and_retry_possible(self):
        voice, stt, _, _ = self.fake_voice(stt=FakeSTTBackend(error=RuntimeError("fake")))
        errors, texts = [], []
        voice.error.connect(errors.append)
        voice.text_recognized.connect(texts.append)
        voice.transcribe(self.source)
        self.wait(lambda: not voice.is_busy)
        self.assertEqual(voice.state, VoiceState.IDLE)
        self.assertEqual(len(errors), 1)
        self.assertEqual(texts, [])
        stt.error = None
        voice.transcribe(self.source)
        self.wait(lambda: not voice.is_busy)
        self.assertEqual(texts, [stt.text])

    def test_out_of_memory_has_clear_message(self):
        for error in (MemoryError(), RuntimeError("CUDA out of memory")):
            with self.subTest(error=type(error).__name__):
                voice, _, _, _ = self.fake_voice(tts=FakeTTSBackend(error=error))
                errors = []
                voice.error.connect(errors.append)
                voice.speak("内存不足")
                self.wait(lambda: not voice.is_busy)
                self.assertIn("内存或显存不足", errors[0])
                self.assertEqual(voice.state, VoiceState.IDLE)

    def test_playback_failure_is_contained(self):
        player = FakeAudioPlayer(play_error=RuntimeError("device failure"))
        voice, _, _, _ = self.fake_voice(player=player)
        errors = []
        voice.error.connect(errors.append)
        voice.speak("播放失败")
        self.wait(lambda: not voice.is_busy)
        self.assertEqual(len(errors), 1)
        self.assertFalse(player.is_playing())
        self.assertEqual(voice.state, VoiceState.IDLE)

    def test_stop_failure_disables_further_audio_not_chat(self):
        class BrokenPlayer(FakeAudioPlayer):
            def stop(self):
                raise RuntimeError("device cannot stop")
        voice, _, tts, _ = self.fake_voice(player=BrokenPlayer())
        errors = []
        voice.error.connect(errors.append)
        self.assertFalse(voice.speak("不能重叠"))
        self.assertEqual(tts.calls, [])
        self.assertFalse(voice.settings.enable_voice_reply)
        self.assertEqual(len(errors), 1)

    def test_new_reply_stops_previous_audio_and_ignores_old_player_events(self):
        voice, _, _, player = self.fake_voice()
        voice.speak("第一段")
        self.wait(lambda: player.is_playing() and not voice.is_busy)
        old_id = player.playback_ids[-1]
        voice.speak("第二段")
        self.assertFalse(player.is_playing())
        self.wait(player.is_playing)
        player.finished.emit(old_id)
        player.error.emit(old_id, "late error")
        self.assertEqual(voice.state, VoiceState.PLAYING)
        self.assertTrue(player.is_playing())
        player.fail()
        self.assertEqual(voice.state, VoiceState.IDLE)

    def test_only_latest_pending_synthesis_runs(self):
        gate, started = self.gate(), Event()
        class SlowTTS(FakeTTSBackend):
            def synthesize(self, text, **kwargs):
                if text == "old":
                    started.set()
                    if not gate.wait(3):
                        raise RuntimeError("gate timed out")
                return super().synthesize(text, **kwargs)
        voice, _, tts, player = self.fake_voice(tts=SlowTTS())
        voice.speak("old")
        self.wait(started.is_set)
        for text in ("discard1", "discard2", "latest"):
            voice.speak(text)
        gate.set()
        self.wait(lambda: not voice.is_busy)
        self.assertEqual([call["text"] for call in tts.calls], ["old", "latest"])
        self.assertEqual(len(player.play_history), 1)

    def test_stopped_stt_result_never_submits(self):
        gate, started = self.gate(), Event()
        class SlowSTT(FakeSTTBackend):
            def transcribe(self, path):
                started.set()
                if not gate.wait(3):
                    raise RuntimeError("gate timed out")
                return super().transcribe(path)
        voice, _, _, _ = self.fake_voice(stt=SlowSTT())
        texts = []
        voice.text_recognized.connect(texts.append)
        voice.transcribe(self.source)
        self.wait(started.is_set)
        voice.stop()
        gate.set()
        self.wait(lambda: not voice.is_busy)
        self.assertEqual(texts, [])
        self.assertEqual(voice.state, VoiceState.IDLE)

    def test_configure_off_cancels_audio_and_shutdown_is_final(self):
        voice, _, _, player = self.fake_voice()
        voice.speak("先播放")
        self.wait(player.is_playing)
        voice.configure(VoiceSettings())
        self.assertFalse(player.is_playing())
        self.assertFalse(voice.speak("关闭后"))
        voice.shutdown()
        voice.shutdown()
        voice.configure(VoiceSettings(enable_voice_reply=True, enable_stt=True))
        self.assertEqual(voice.state, VoiceState.CLOSED)
        self.assertFalse(voice.speak("退出后"))
        self.assertFalse(voice.transcribe(self.source))

    def test_markup_only_does_not_synthesize(self):
        voice, _, tts, _ = self.fake_voice()
        self.assertFalse(voice.speak("```python\nprint('private')\n```\n😊"))
        self.assertEqual(tts.calls, [])
        self.assertEqual(voice.state, VoiceState.IDLE)

    def test_text_cleaner_preserves_spoken_words_not_formatting(self):
        raw = "# 标题\n**当然可以！** 😊\n\n*今天*学习 `Fourier Transform`。\n[参考](https://example.com)\n```python\nsecret=1\n```"
        self.assertEqual(prepare_text_for_speech(raw),
                         "标题 当然可以。 今天学习 Fourier Transform。 参考")
        self.assertIn("secret=1", raw)
        self.assertIn("**", raw)

    def test_text_cleaner_handles_emoji_fences_and_lists(self):
        cases = {
            "👨‍👩‍👧‍👦 🇨🇳 1️⃣ 👍🏽 ✨": "",
            "文字\n~~~python\n隐藏\n~~~\n后文": "文字 后文",
            "前文\n```python\n不完整代码块": "前文",
            "> **引用**\n- 项目\n1. 第二项": "引用 项目 第二项",
            "![不要朗读](image.png) [链接][id]\n[id]: https://example.com": "链接",
            "血糖 5.6，速度 1.2，Fourier_transform。": "血糖 5.6，速度 1.2，Fourier_transform。",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(prepare_text_for_speech(raw), expected)

    def test_settings_and_pcm_validation(self):
        for kwargs in ({"speech_speed": 0}, {"speech_speed": float("nan")},
                       {"volume": 2}, {"volume": True}, {"enable_stt": "yes"},
                       {"tts_model_path": 5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                VoiceSettings(**kwargs)
        with self.assertRaises(ValueError):
            SpeechAudio(b"odd")
        with self.assertRaises(ValueError):
            SpeechAudio(b"")
        self.assertEqual(SpeechAudio(b"\0\0").sample_rate, 24000)


if __name__ == "__main__":
    unittest.main()
