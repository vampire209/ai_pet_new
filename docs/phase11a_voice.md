# Phase 11A — Voice 架构骨架验收

这是 11A 完成时的历史记录。用户随后确认优先做对话中逐句朗读，当前运行行为
请看 [逐句朗读说明](voice_sentence_streaming.md)；下文“无真实发声”等仅描述当时的 11A。

本次只完成 11A。语音是现有聊天的输入输出适配层，不是第二套聊天系统。
正常启动时 STT、Voice Reply 都关闭，不打开麦克风，不加载模型，不发声。
此阶段验收方式是 Fake 自动化测试，而不是实际麦克风/听觉测试。

## 文件与职责

| 文件 | 职责 |
| --- | --- |
| `voice/voice_service.py` | Qt 主线程协调状态、异步推理、停止、关闭和过期结果过滤 |
| `voice/backends.py` | `STTBackend`、`TTSBackend` 可替换协议 |
| `voice/stt_service.py` | 工作线程校验输入/模型路径、延迟创建并复用 STT 后端 |
| `voice/tts_service.py` | 工作线程校验模型/参考路径、复用 TTS 后端并传入语速 |
| `voice/audio_player.py` | 非阻塞播放接口 `play/stop/is_playing` 与安全缺省实现 |
| `voice/voice_models.py` | 不可变设置、PCM 音频、状态和语音专属异常 |
| `voice/text.py` | `prepare_text_for_speech`，仅清理用于朗读的副本 |
| `voice/fake_backends.py` | 假识别、假合成和手动结束的无声播放器 |
| `voice/__init__.py` | 公共入口导出，不导入任何真实语音 SDK |
| `tests/test_phase11a_voice.py` | 23 项组件/线程/取消/错误/清理测试 |
| `tests/test_phase11a_chat.py` | 14 项真实 Qt 工作线程聊天集成测试 |
| `data/voice/reference/README.md` | 参考语音目录占位，不附带音频 |

修改既有文件：`chat_window.py`、`README.md`、`.gitignore`，以及
`tests/test_phase6.py`、`tests/test_phase7.py`、`tests/test_phase9.py` 中各一项
旧测试的重载时钟。没有改变旧测试的断言或 Todo 业务逻辑。

现有未提交的能力模式、学习模块及 UI 改动均保留。本次没有修改
`ChatService`、`ChatActionRouter`、`PersonaBuilder`、`MemoryService`、
`TodoManager`、Profile 数据结构、`main.py`、`pet_window.py` 或依赖清单。

## 调用流程

```text
调用者提供音频路径（11A 使用临时测试文件）
  → VoiceService.transcribe
  → Qt Worker → STTService → STTBackend.transcribe
  → 主线程 text_recognized
  → ChatWindow.submit_recognized_text → 原 send_message
  → 现有单词命令 / ChatActionRouter / 待办确认卡
  → 原 ChatService / Memory / Persona / DeepSeek 流式回复
  → _on_completed：只提取可见正文，保存原来的聊天历史
  → VoiceService.speak → prepare_text_for_speech
  → Qt Worker → TTSService → TTSBackend.synthesize
  → 主线程 AudioPlayer.play（非阻塞接口）
```

TTS 不处理 chunk、错误消息、停止后的半句、待办卡或阅读建议隐藏 JSON。
原有聊天格式清理和历史保存保持不变，TTS 只获得独立的文字副本。
识别为空时提示“没有听清，再说一次吧。”；聊天正在生成或有未发送草稿时，
不覆盖输入框、不隐式追加聊天，而是提示先处理当前输入。

## 状态、线程及生命周期

- STT：Idle → Processing → Idle；TTS：Idle → Synthesizing → Playing → Idle。
  Recording 枚举仅为 11B 预留，没有实现录音。
- 后端工厂和推理都在 Qt Worker 中执行，模型首次使用才创建，模型路径
  不变则复用。UI 和播放器控制通过 QObject 槽回到主线程。
- 每次新输入、朗读、关闭或设置变化更新请求代次；旧识别、旧合成及旧播放
  完成信号均不能触发新一轮操作。一个协调器最多保留一个活动推理和一个
  最新待处理请求，连续中断不会排积大量模型任务。
- 停止立即使结果失效，但不强杀已运行的第三方模型函数。11A 的 Fake
  不运行大型任务；11B/11C 必须按所选引擎补充合作取消、超时/退出策略。
  `shutdown()` 本身不阻塞等待模型。
- 应用现有单实例 ChatWindow 持有一个 VoiceService 和一个播放器；新消息
  （包括本地命令）先停止旧音频。停止失败会禁用进一步朗读，避免重叠。
- 点关闭聊天窗口只 stop，可再打开；桌宠退出已有 `chat.shutdown()` 链路
  才永久关闭语音。已排队的聊天完成信号也不能在关闭后触发朗读。

## 可选性与错误边界

缺少后端、模型或参考音频时，只禁用对应语音输入/回复功能并提示；加载、
推理、播放器、内存/显存异常独立处理。语音失败不调用 `ChatService.fail_turn`，
不覆盖已有回复，不清除历史，不阻止后续打字聊天。日志使用
`[Voice]`、`[STT]`、`[TTS]`、`[Audio]`，不记录完整识别文本或模型内部输出。

`VoiceSettings` 预留启用标志、模型路径、参考路径、语速和音量并校验值域，
当前仅在内存中生效。设置 UI 与本地持久化明确留给 11E，没有提前改 Profile。
Fake TTS 返回内存中的静音 PCM，不创建 wav 缓存、不删除用户文件。
真实缓存上限/清理及设备错误需在后续真实音频阶段实现并验收。

## 打包边界

本次无新第三方依赖、无模型下载。`ChatWindow` 静态导入语音模块，现有
PyInstaller 可发现这些 Python 代码；`AIPetNew.spec` 完全保留，包括
`icuuc.dll`、`icudt78.dll` 的冲突处理。模型与参考素材在 assets 之外且被
Git 忽略。本阶段不重新制作 EXE；实际 EXE 验收属于 11E。

## 验证

2026-09-13 验证结果（Python 3.13.14 / PySide6 6.11.2，离屏模式）：

- 语音新增测试：37/37 通过，1.771 秒。
- 全部普通测试：320/320 通过，20.410 秒；包含原有 283 项和新增 37 项。
- `main.py --smoke-test`：退出码 0。
- `git diff --check`：通过。未运行真实 API 测试、真实音频或 EXE 构建。

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest discover -s tests -p "test_phase11a*.py" -v
python -m unittest discover -s tests -p "test_*.py"
python main.py --smoke-test
```

新测试共 37 项：默认关闭、完整 STT→原聊天→TTS、Todo 确认后才写入、
原 Memory/Persona、草稿保护、完整回复才朗读、阅读 JSON 隔离、关闭竞态、
中断旧语音、最新请求合并、UI/工作线程边界、延迟加载复用、资源缺失、
异常恢复、显存不足、音量/语速传递、Markdown/emoji 清理及无文件副作用。

旧测试最初有 3 项失败，均为 2026-08-27/28 的固定场景在重载时使用真实
当前时间，导致已过时的提前提醒按原逻辑被过滤。现在仅在这三个测试的
重载段模拟同一场景时钟，仍真实读取 JSON、执行反序列化和原比较断言；
不修改系统时钟、不跳过测试、不修改产品提醒规则。

## 下一阶段（需用户确认后开始）

11B：沿用当前 UI 风格加入麦克风按钮和 Idle/Recording/Processing 状态，
接入本地录音及可选 STT 后端，补设备/权限/取消与临时录音管理，最后让用户
亲自测试“录音 → 识别 → 原聊天/Todo 确认卡”。不同时推进本地 TTS、角色音色
或设置页；分别留给 11C、11D、11E。
