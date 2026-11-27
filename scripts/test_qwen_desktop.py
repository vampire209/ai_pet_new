"""Opt-in real Qwen/WinMM desktop test with isolated business data and fake chat."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--show', action='store_true')
    parser.add_argument('--system-only', action='store_true', help='Check real playback and fallback without loading Qwen.')
    parser.add_argument('--skip-demos', action='store_true', help='Do not replay the previously checked six WAV files.')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if not args.smoke:
        parser.error('Requires --smoke; loads local Qwen and plays short test audio.')
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from ai.chat_service import ChatService
    from ai.fake_client import FakeDeepSeekClient
    from ai.chat_ui_config import ChatUiSettings
    from ai.profile_service import ProfileService
    from chat_window import ChatWindow
    from config import AppConfig
    from memory.memory_service import MemoryService
    from pet_window import PetWindow
    from todo_manager import TodoManager
    from voice.runtime import create_desktop_voice_service

    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = {'status': 'running', 'real_chat_api_called': False,
              'auditory_quality': 'User listening required', 'phases': {}}
    app = QApplication.instance() or QApplication(['voice-desktop-smoke'])
    app.setQuitOnLastWindowClosed(False)
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    memory = MemoryService(':memory:')
    profiles, todos = ProfileService(root / 'profile.json'), TodoManager(root / 'tasks.json')
    voice = create_desktop_voice_service()
    voice.configure(replace(voice.settings, enable_voice_reply=True, volume=.35))
    fake = FakeDeepSeekClient((('这是新消息的回复。',),))
    chat = ChatWindow(ChatService(client=fake), voice_service=voice,
                      profile_service=profiles, todo_manager=todos, memory_service=memory,
                      ui_settings=ChatUiSettings(root / 'chat.json'))
    pet = PetWindow(AppConfig.load(), todos, profile_service=profiles,
                    memory_service=memory)
    if args.show:
        pet.show()
        chat.show()
    errors, played, processes, ticks, frames = [], [], [], [], []
    voice.error.connect(errors.append)
    voice._player.error.connect(lambda _id, message: errors.append(message))
    original_play = voice._play
    def record_play(request, audio):
        played.append({'id': request.request_id, 'generation': request.generation,
                       'text': str(request.payload), 'backend': audio.backend_label,
                       'notice': audio.notice, 'at': time.monotonic()})
        original_play(request, audio)
    voice._play = record_play
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    pet._animation_manager.frame_changed.connect(lambda _pixmap: frames.append(time.monotonic()))
    timer.start()

    def wait(predicate, timeout=150):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if errors:
                raise RuntimeError(errors[-1])
            if predicate():
                return
            time.sleep(.003)
        raise TimeoutError('Timed out waiting for desktop speech state')

    def local():
        selecting = voice._tts._backend
        return getattr(selecting, '_local', None)

    def remember_process():
        backend = local()
        process = getattr(backend, '_process', None)
        if process is not None and process not in processes:
            processes.append(process)
        return process

    def complete():
        wait(lambda: not voice.is_busy and not voice._player.is_playing() and not voice.is_speaking, 30)

    def preview(text):
        old_count = len(played)
        start = time.monotonic()
        voice.preview(text)
        wait(lambda: len(played) > old_count)
        remember_process()
        complete()
        return time.monotonic() - start

    try:
        if not args.system_only:
            print('Real desktop test: preview + playback', flush=True)
            elapsed = preview('你好呀，今天也一起加油吧。')
            if not played[-1]['backend'].startswith('本地'):
                raise RuntimeError('Local preview fell back: ' + played[-1]['notice'])
            first = remember_process()
            report['phases']['local_preview'] = {'ok': True, 'elapsed_including_playback': elapsed,
                                                'load_seconds': local().load_seconds}
            elapsed = preview('你好，今天过得怎么样？')
            report['phases']['reuse'] = {'same_process': remember_process() is first,
                                        'elapsed_including_playback': elapsed}
            assert remember_process() is first

            print('Real desktop test: new message interrupts active Qwen inference', flush=True)
            old_text = '这是必须取消的旧朗读，' * 12
            serial = local()._serial
            voice.preview(old_text)
            wait(lambda: local() is not None and local()._serial > serial)
            old_request = voice._worker.request.request_id
            old_process = remember_process()
            started = time.monotonic()
            chat.input.setPlainText('测试新消息打断旧语音')
            chat.send_message()
            dispatch_seconds = time.monotonic() - started
            assert not voice._player.is_playing()
            wait(lambda: not chat.is_generating)
            wait(lambda: any(x['text'] == '这是新消息的回复。' for x in played))
            complete()
            remember_process()
            assert not any(x['id'] == old_request for x in played)
            assert old_process.poll() is not None
            report['phases']['new_message_interrupt'] = {'ok': True, 'send_seconds': dispatch_seconds,
                'old_child_exited': True, 'old_result_played': False, 'new_backend': played[-1]['backend'],
                'text_reply_kept': chat.service.history[-1]['content'] == '这是新消息的回复。'}

            print('Real desktop test: crash during inference falls back', flush=True)
            if local() is None:
                raise RuntimeError('New-message reload fell back; free memory before crash-path real test.')
            serial = local()._serial
            before = len(played)
            voice.preview('这段语音用于验证本地模型意外退出之后，系统声音仍然可以继续朗读。')
            wait(lambda: local()._serial > serial)
            process = remember_process()
            process.kill()
            wait(lambda: len(played) > before)
            assert played[-1]['backend'] == '系统声音'
            assert '回退' in played[-1]['notice']
            wait(lambda: voice._player._clip is not None)
            start = time.monotonic()
            chat.voice_stop_button.click()
            stop_seconds = time.monotonic() - start
            assert not voice._player.is_playing()
            wait(lambda: not voice.is_busy, 5)
            report['phases']['crash_fallback_and_stop'] = {'ok': True, 'stop_seconds': stop_seconds}

        print('Real desktop test: missing-model fallback and forced System', flush=True)
        voice.configure(replace(voice.settings, tts_backend='auto', tts_model_path=str(root / 'missing-model')))
        preview('本地模型不在时，系统声音仍然可以朗读。')
        assert played[-1]['backend'] == '系统声音'
        assert '回退' in played[-1]['notice']
        report['phases']['missing_model_fallback'] = True
        chat.voice_backend_combo.setCurrentIndex(chat.voice_backend_combo.findData('system'))
        preview('系统声音测试完成。')
        assert local() is None
        assert played[-1]['backend'] == '系统声音'
        report['phases']['forced_system'] = True
        if args.system_only:
            print('Real desktop test: stop playback and replace with new chat message', flush=True)
            before = len(played)
            voice.preview('这段系统语音用于验证，点击停止朗读后，声音能立即停止，而文字聊天仍然可以继续。')
            wait(lambda: len(played) > before and voice._player._clip is not None)
            start = time.monotonic()
            chat.voice_stop_button.click()
            stop_seconds = time.monotonic() - start
            assert not voice._player.is_playing()
            complete()
            before = len(played)
            voice.preview('这段旧的朗读应该被下一条新消息打断，打断之后不应该再次播放旧的内容。')
            wait(lambda: len(played) > before and voice._player._clip is not None)
            cutoff = len(played)
            chat.input.setPlainText('测试新消息打断')
            start = time.monotonic()
            chat.send_message()
            send_seconds = time.monotonic() - start
            assert not voice._player.is_playing()
            wait(lambda: not chat.is_generating and len(played) > cutoff)
            complete()
            assert all(x['text'] == '这是新消息的回复。' for x in played[cutoff:])
            assert chat.service.history[-1]['content'] == '这是新消息的回复。'
            report['phases']['system_stop_and_new_message'] = {
                'stop_seconds': stop_seconds, 'send_seconds': send_seconds, 'old_audio_resumed': False,
                'text_reply_kept': True}
        if args.system_only and not args.skip_demos:
            import wave
            from voice.voice_models import SpeechAudio
            demos = ROOT / 'runtime/cache/voice_demo_20260923'
            demo_playback = []
            for index, source in enumerate(sorted(demos.glob('0[1-6]_*.wav')), 1):
                print('Playing existing demo: ' + source.name, flush=True)
                with wave.open(str(source), 'rb') as handle:
                    audio = SpeechAudio(handle.readframes(handle.getnframes()), handle.getframerate(),
                                        handle.getnchannels(), handle.getsampwidth())
                voice._player.play(audio, playback_id=-index, volume=.35)
                wait(lambda: not voice._player.is_playing(), 40)
                demo_playback.append(source.name)
            assert len(demo_playback) == 6
            report['phases']['six_saved_demos_played'] = demo_playback
        chat.grab().save(str(args.output_dir / 'chat-voice-controls.png'))
        pet.grab().save(str(args.output_dir / 'pet-during-voice.png'))
        assert not todos.get_tasks()
        assert not memory.active_memories()
        report['phases']['isolated_business_data_unchanged'] = True
        report['status'] = 'system_checks_passed' if args.system_only else 'passed'
    except Exception as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        print(report['error'], flush=True)
    finally:
        timer.stop()
        chat.grab().save(str(args.output_dir / 'chat-voice-controls.png'))
        pet.grab().save(str(args.output_dir / 'pet-during-voice.png'))
        report['played'] = played
        gaps = [b-a for a, b in zip(ticks, ticks[1:])]
        frame_gaps = [b-a for a, b in zip(frames, frames[1:])]
        report['ui_timer'] = {'tick_count': len(ticks), 'max_gap_seconds': max(gaps, default=0),
                             'gaps_over_100ms': sum(gap > .1 for gap in gaps)}
        report['animation'] = {'frames': len(frames), 'max_frame_gap_seconds': max(frame_gaps, default=0),
                               'human_drag_and_smoothness_review': 'not performed'}
        selecting = voice._tts._backend
        for provider in (getattr(selecting, '_local', None), getattr(selecting, '_system', None)):
            process = getattr(provider, '_process', None)
            if process is not None and process not in processes:
                processes.append(process)
        chat.shutdown()
        voice.shutdown()
        pet.close()
        chat.close()
        for process in processes:
            try: process.wait(timeout=5)
            except Exception: pass
        report['all_recorded_children_exited'] = all(process.poll() is not None for process in processes)
        report['recorded_child_count'] = len(processes)
        memory.close()
        app.processEvents()
        temporary.cleanup()
        (args.output_dir / 'desktop_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k != 'played'}, ensure_ascii=True), flush=True)
    return 0 if report['status'] in ('passed', 'system_checks_passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
