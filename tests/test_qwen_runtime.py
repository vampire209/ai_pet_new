"""Real pipe lifecycle with a tiny fake child; never imports or loads Qwen."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from voice.neural_contracts import NeuralRuntimeConfig
from voice.qwen_backend import QwenCustomVoiceBackend, decode_audio
from voice.selecting_tts import SelectingTTSBackend
from voice.tts_service import TTSService
from voice.voice_models import SpeechAudio, VoiceCancelledError, VoiceError, VoiceSettings, VoiceUnavailableError
from scripts.qwen_voice_worker import wait_for_headroom

CHILD = '''
import base64,json,sys,time
mode = sys.argv[1]
def emit(d):
    print(json.dumps(dict(version=1,**d)),flush=True)
if mode == 'startup_wait': time.sleep(20)
emit(dict(ready=True,model='0.6b_custom_voice',voices=['Serena'],languages=['Chinese'],load_seconds=0))
for line in sys.stdin:
    r=json.loads(line)
    if mode=='crash': sys.exit(1)
    if mode=='hang': time.sleep(20)
    if mode=='failure':
        emit(dict(id=r['id'],ok=False,error='test failure'))
    elif mode=='bad_id':
        emit(dict(id=r['id']+1,ok=True))
    elif mode=='invalid_json':
        print('not json',flush=True)
    else:
        emit(dict(id=r['id'],ok=True,pcm=base64.b64encode(r['text'].encode('utf-16-le')).decode(),sample_rate=24000,channels=1,sample_width=2))
'''


class QwenPipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.worker = Path(self.temp.name) / 'fake.py'
        self.worker.write_text(CHILD, encoding='utf-8')
        self.mode = 'ok'
        self.processes = []
        self.commands = []
        self.spawned = Event()
        real_popen = subprocess.Popen
        def spawn(command, **kwargs):
            self.commands.append((command, kwargs))
            process = real_popen([sys.executable, '-u', str(self.worker), self.mode], **kwargs)
            self.processes.append(process)
            self.spawned.set()
            return process
        self.patches = [patch('voice.qwen_backend.subprocess.Popen', side_effect=spawn),
                        patch('voice.qwen_backend.inspect_neural_setup', return_value=SimpleNamespace(local_checks_passed=True))]
        for item in self.patches: item.start()
        self.backend = QwenCustomVoiceBackend(NeuralRuntimeConfig(
            python_executable=sys.executable, model_path=str(Path(self.temp.name)), device='cuda:0'),
            load_timeout=2, synthesis_timeout=2)

    def tearDown(self):
        self.backend.close()
        for process in self.processes:
            process.wait(timeout=5)
        for item in self.patches: item.stop()
        self.temp.cleanup()

    def test_unicode_is_data_and_one_child_is_reused(self):
        text = '你好，"引号"；$(echo nope)\n第二句。'
        for _ in range(2):
            audio = self.backend.synthesize(text)
            self.assertEqual(audio.data.decode('utf-16-le'), text)
        self.assertEqual(len(self.processes), 1)
        command, kwargs = self.commands[0]
        self.assertNotIn(text, command)
        self.assertFalse(kwargs['shell'])
        self.assertEqual(kwargs['env']['HF_HUB_OFFLINE'], '1')
        self.assertEqual(kwargs['env']['TRANSFORMERS_OFFLINE'], '1')
        self.assertIn('qwen_voice_worker.py', command[2])

    def test_crash_is_reported_and_child_is_reaped(self):
        self.mode = 'crash'
        with self.assertRaises(VoiceUnavailableError): self.backend.synthesize('你好')
        self.processes[0].wait(timeout=3)

    def test_failure_response_and_wrong_id_rejected(self):
        for mode in ('failure', 'bad_id', 'invalid_json'):
            with self.subTest(mode=mode):
                self.mode = mode
                with self.assertRaises(VoiceError): self.backend.synthesize('你好')
                self.processes[-1].wait(timeout=3)

    def test_timeout_kills_child(self):
        self.mode = 'hang'
        self.backend.synthesis_timeout = .1
        with self.assertRaisesRegex(VoiceUnavailableError, '超时'):
            self.backend.synthesize('你好')
        self.processes[0].wait(timeout=3)

    def test_cancel_during_load_is_prompt_and_restartable(self):
        self.mode = 'startup_wait'
        cancelled, errors = Event(), []
        def run():
            try: self.backend.synthesize_cancellable('你好', cancelled=cancelled)
            except Exception as exc: errors.append(exc)
        thread = Thread(target=run)
        thread.start()
        self.assertTrue(self.spawned.wait(3))
        start = time.monotonic()
        cancelled.set()
        self.backend.cancel_current()
        self.assertLess(time.monotonic() - start, .5)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(errors[0], VoiceCancelledError)
        self.mode = 'ok'
        self.assertTrue(self.backend.synthesize('新回复').data)
        self.assertEqual(len(self.processes), 2)

    def test_restart_waits_for_previous_process_exit(self):
        released, started, results = Event(), Event(), []
        def poll():
            started.set()
            return 0 if released.is_set() else None
        self.backend._retired.append(SimpleNamespace(poll=poll))
        thread = Thread(target=lambda: results.append(self.backend.synthesize('新回复')))
        thread.start()
        self.assertTrue(started.wait(1))
        self.assertFalse(self.spawned.is_set())
        released.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)

    def test_close_during_request_does_not_wait_for_inference(self):
        self.mode = 'hang'
        errors = []
        def run():
            try: self.backend.synthesize('你好')
            except Exception as exc: errors.append(exc)
        thread = Thread(target=run)
        thread.start()
        self.assertTrue(self.spawned.wait(3))
        start = time.monotonic()
        self.backend.close()
        self.backend.close()
        self.assertLess(time.monotonic() - start, .5)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(errors[0], VoiceCancelledError)
        with self.assertRaises(VoiceCancelledError): self.backend.synthesize('不能重开')

    def test_missing_model_does_not_spawn(self):
        with patch('voice.qwen_backend.inspect_neural_setup', return_value=SimpleNamespace(local_checks_passed=False)):
            with self.assertRaises(VoiceUnavailableError): self.backend.synthesize('你好')
        self.assertEqual(self.processes, [])

    def test_long_text_and_reference_rejected_before_spawn(self):
        with self.assertRaises(VoiceError): self.backend.synthesize('字' * 241)
        with self.assertRaises(VoiceUnavailableError): self.backend.synthesize('你好', reference_audio_path=Path('x.wav'))
        self.assertEqual(self.processes, [])

    def test_unsupported_voice_and_speed_rejected_without_loading(self):
        self.backend.voice_id = 'unknown'
        with self.assertRaises(VoiceUnavailableError): self.backend.synthesize('你好')
        self.backend.voice_id = 'Serena'
        for speed in (True, float('nan'), 0, 3):
            with self.subTest(speed=speed), self.assertRaises(VoiceError):
                self.backend.synthesize('你好', speech_speed=speed)
        self.assertEqual(self.processes, [])

    def test_pcm_validation(self):
        valid = dict(pcm='AAAAAA==', sample_rate=24000, channels=1, sample_width=2)
        self.assertEqual(len(decode_audio(valid).data), 4)
        for overrides in (dict(pcm='!'), dict(pcm='AA=='), dict(pcm=''), dict(sample_rate=True),
                          dict(sample_rate=1), dict(channels=2), dict(sample_width=4)):
            with self.subTest(overrides=overrides), self.assertRaises(VoiceError):
                decode_audio(dict(valid, **overrides))


class Provider:
    def __init__(self, failure=None):
        self.calls = 0
        self.closed = False
        self.failure = failure
    def synthesize(self, text, **kwargs):
        self.calls += 1
        if self.failure: raise self.failure
        return SpeechAudio(b'\x00\x00')
    def close(self): self.closed = True


class SelectionTests(unittest.TestCase):
    def make(self, choice='auto', failure=None):
        self.local, self.system = Provider(failure), Provider()
        self.local_factory, self.system_factory = Mock(return_value=self.local), Mock(return_value=self.system)
        self.backend = SelectingTTSBackend(VoiceSettings(tts_backend=choice),
                         local_factory=self.local_factory, system_factory=self.system_factory)
        self.addCleanup(self.backend.close)
        return self.backend

    def test_auto_uses_neural(self):
        audio = self.make().synthesize('你好')
        self.assertIn('Serena', audio.backend_label)
        self.system_factory.assert_not_called()

    def test_auto_failure_latches_and_system_continues(self):
        for failure in (VoiceUnavailableError('模型缺失'), RuntimeError('CUDA out of memory'), OSError('crashed')):
            with self.subTest(failure=failure):
                backend = self.make(failure=failure)
                for _ in range(2):
                    audio = backend.synthesize('你好')
                    self.assertEqual(audio.backend_label, '系统声音')
                    self.assertIn('回退', audio.notice)
                self.assertEqual(self.local.calls, 1)
                self.assertEqual(self.system.calls, 2)
                self.assertTrue(self.local.closed)

    def test_system_never_constructs_local(self):
        audio = self.make('system').synthesize('你好')
        self.assertEqual(audio.backend_label, '系统声音')
        self.local_factory.assert_not_called()

    def test_explicit_local_reports_failure_and_offers_auto(self):
        backend = self.make('local_neural', RuntimeError('model failure'))
        with self.assertRaisesRegex(VoiceUnavailableError, '自动'): backend.synthesize('你好')
        self.system_factory.assert_not_called()

    def test_cancellation_is_never_fallback(self):
        backend = self.make(failure=VoiceCancelledError('cancelled'))
        with self.assertRaises(VoiceCancelledError): backend.synthesize('旧回复')
        self.system_factory.assert_not_called()

    def test_cancelled_failure_race_is_never_fallback(self):
        backend = self.make()
        cancelled = Event()
        def synthesize(*args, **kwargs):
            cancelled.set()
            raise RuntimeError('child killed')
        self.local.synthesize = synthesize
        with self.assertRaises(VoiceCancelledError):
            backend.synthesize_cancellable('旧回复', cancelled=cancelled)
        self.system_factory.assert_not_called()

    def test_service_path_validation_does_not_block_auto(self):
        backend = self.make(failure=VoiceUnavailableError('missing'))
        service = TTSService(lambda s: backend, backend_validates_paths=True)
        self.addCleanup(service.close)
        audio = service.synthesize('你好', VoiceSettings(tts_backend='auto', tts_model_path='missing'))
        self.assertEqual(audio.backend_label, '系统声音')

    def test_service_replaces_provider_when_configuration_changes(self):
        constructed = []
        def factory(settings):
            provider = Provider()
            constructed.append((settings, provider))
            return provider
        service = TTSService(factory, backend_validates_paths=True)
        self.addCleanup(service.close)
        settings = VoiceSettings(tts_backend='auto')
        service.synthesize('第一句', settings)
        service.synthesize('第二句', settings)
        self.assertEqual(len(constructed), 1)
        service.synthesize('新声音', replace(settings, tts_backend='system'))
        self.assertEqual(len(constructed), 2)
        self.assertTrue(constructed[0][1].closed)

    def test_settings_invalid_backend(self):
        with self.assertRaises(ValueError): VoiceSettings(tts_backend='unknown')


class MemoryGateTests(unittest.TestCase):
    def test_reclamation_can_recover_but_threshold_is_unchanged(self):
        psutil = SimpleNamespace(virtual_memory=Mock(side_effect=[
            SimpleNamespace(available=3 * 1024**3), SimpleNamespace(available=4 * 1024**3)]))
        with patch.dict(sys.modules, psutil=psutil), patch('scripts.qwen_voice_worker.time.sleep'):
            wait_for_headroom()
        self.assertEqual(psutil.virtual_memory.call_count, 2)

    def test_persistent_low_memory_fails_before_loading(self):
        psutil = SimpleNamespace(virtual_memory=Mock(return_value=SimpleNamespace(available=4095 * 1024**2)))
        with patch.dict(sys.modules, psutil=psutil), self.assertRaisesRegex(RuntimeError, '4095 MiB'):
            wait_for_headroom(grace_seconds=0)


if __name__ == '__main__': unittest.main()
