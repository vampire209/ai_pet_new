# Phase 11C-2：Qwen 0.6B 语音接入与实测

项目：`D:\codex\ai_pet_new`。记录日期：2026-09-24。

## 当前结论

Qwen 0.6B CustomVoice / Serena 已通过独立合成，并接入桌宠源码的逐句朗读链路。
聊天界面提供“自动 / 本地声音 / 系统声音”。本地模型首次使用才加载，正常连续请求复用进程。
语音进程与主 Python 隔离，停止和新消息可取消旧请求；后台异常不改变文字聊天内容。

六段正式试听及两段预热 WAV 已生成，六段正式试听通过真实 Windows 播放器逐一播放。
已实测应用内本地声音播放、同进程复用、新消息取消旧 Qwen 请求、内存不足回退、模型缺失回退、
强制系统模式、系统音频停止与新消息打断。最终完整整合测试 **passed**：取消后重新加载 Serena
成功，实际终止正在处理请求的语音子进程后成功回退，三个记录到的子进程全部退出。

音频数值正常不等于主观听感合格；自然度、中英发音和是否适合角色仍由用户试听判断。
本轮没有进入 1.7B、Base、VoiceDesign、音色克隆、麦克风识别或最终 EXE 打包。

## 使用方法

在项目目录运行：

```powershell
cd D:\codex\ai_pet_new
python main.py
```

右键桌宠 → **AI聊天** → 选择“声音来源” → **试听**。

| 选项 | 实际行为 |
| --- | --- |
| 自动 | 优先 Serena；环境、文件、内存、超时或语音进程失败时改用系统声音，并显示回退原因。 |
| 本地声音 | 仅使用 Serena；失败时明确提示，可手动切换到“自动”或“系统声音”。 |
| 系统声音 | 只使用 Windows 已安装音色，不启动 Qwen。 |

“语音回复”默认关闭；开启后从下一轮开始按句朗读。设置目前仅本次运行生效。
切换来源会停止当前语音，下一轮使用新来源。Auto 失败后会暂时继续使用系统声音，避免每句话
重复加载；释放资源后，先切到“系统声音”再切回“自动”即可重试。
“停止朗读”只停止音频及旧的合成请求，文字生成继续；发送新消息不会让旧音频再次播放。
取消正在推理的 Qwen 会终止该进程，因此下一次本地朗读需要重新加载。

加载前保护门槛仍是 **4GiB 可用系统内存**，它是本项目的保守条件，不是模型官方最低要求。
建议在打开桌宠前留出约 5GiB 余量。重启前先等待已取消的进程退出，预检最多再等 3 秒供系统
回收内存；仍不足则报错或回退，不降低门槛、不关闭用户程序、不自动下载模型。

旧 `dist_latest/AIPetNew.exe` 是之前的打包版，不包含此次接入；本轮验收使用源码。

## 六段试听

以下文件已实际生成并通过 Windows 播放器播放。均为 Serena、Chinese、单声道 24kHz PCM16。
“温柔 / 开心”只表示文本类型，不表示 0.6B 支持情绪指令。

| 文本类型 | 已生成文件 | 音频时长 | 合成耗时 |
| --- | --- | ---: | ---: |
| 普通 | [01_normal.wav](D:/codex/ai_pet_new/runtime/cache/voice_demo_20260923/01_normal.wav) | 3.52秒 | 8.44秒 |
| 安慰 | [02_soft.wav](D:/codex/ai_pet_new/runtime/cache/voice_demo_20260923/02_soft.wav) | 3.36秒 | 7.06秒 |
| 开心 | [03_happy.wav](D:/codex/ai_pet_new/runtime/cache/voice_demo_20260923/03_happy.wav) | 3.04秒 | 7.93秒 |
| 长回复 | [04_long.wav](D:/codex/ai_pet_new/runtime/cache/voice_demo_20260923/04_long.wav) | 9.36秒 | 21.24秒 |
| 中英混读 | [05_mixed.wav](D:/codex/ai_pet_new/runtime/cache/voice_demo_20260923/05_mixed.wav) | 5.60秒 | 13.50秒 |
| 时间与数字 | [06_number.wav](D:/codex/ai_pet_new/runtime/cache/voice_demo_20260923/06_number.wav) | 4.32秒 | 10.52秒 |

独立合成时每段均未出现 NaN/Inf，削波采样比例均为 0；这不能替代对杂音、自然度、停顿和发音的试听。
生成速度约为音频时长的 2.10–2.61 倍，尚不能宣传为实时语音对话。

同一句“你好呀，今天过得怎么样？”另有
[Windows 系统声音对照](D:/codex/ai_pet_new/runtime/cache/voice_demo_20260923/07_system_comparison.wav)，
可与 `01_normal.wav` 比较。对照文件由系统 TTS 真实合成，未做音色加工。

## 20 项验收

| 项目 | 实测结果或验证边界 |
| --- | --- |
| 1. 语音 Python | 独立 Python 3.12.14；主桌宠仍使用原 Python 3.13。 |
| 2. 实际依赖 | torch 2.8.0+cu128、transformers 4.57.3、qwen-tts 0.1.1、accelerate 1.12.0、numpy 2.2.6、soundfile 0.13.1、librosa 0.11.0、soxr 1.1.0、psutil 7.0.0；完整版本见 `voice/requirements-qwen-lock.txt`。 |
| 3. 模型实际大小 | 2,498,389,792 bytes，约 2.50GB / 2.33GiB，含下载元数据；模型文件本身为 2,498,388,392 bytes。 |
| 4. Serena | 独立合成及应用内真实播放成功；听感待用户评价。 |
| 5. 六段试听 | 全部生成，并通过真实 Windows 播放器播放。另有两段相同短句预热。 |
| 6. 首次冷加载 | 独立脚本 41.012秒，包含导入和模型加载；另一次应用内加载 35.861秒。最终复验加载 7.979秒（此前已多次加载，不能当作冷启动指标）。 |
| 7. 第一次生成 | 独立脚本 24.021秒，生成 2.96秒音频。 |
| 8. 第二次生成 | 相同短句 6.168秒，生成 2.80秒音频。 |
| 9. GPU 峰值 | 整卡采样峰值 5.06GiB，含桌面和其他应用；Qwen 进程 PyTorch 峰值 allocated 2.29GiB、reserved 2.44GiB。没有 CUDA OOM。 |
| 10. 系统内存峰值 | 整机已用峰值 13.72GiB；独立语音进程 RSS 峰值 2.41GiB。两者不能混为模型占用。 |
| 11. 桌宠动画/UI | 最终真实 Qwen 测试中有 365 次动画帧更新，最大帧间隔 346ms；20ms Qt 计时器触发 4245 次，最大间隔 160ms，两次超过 100ms。未见长时间事件循环冻结；人工拖动和主观流畅度仍由用户确认。 |
| 12. AI 聊天 | 使用 FakeDeepSeekClient 走真实 ChatWindow/VoiceService 路径，文字回复保留；未调用付费聊天 API。已有聊天、人格、记忆、待办回归通过。 |
| 13. 停止朗读 | 真实播放器停止约 20.1ms；独立进程取消、关闭和超时清理的测试通过。 |
| 14. 新消息打断 | 最终应用内取消旧 Qwen 请求约 15.6ms，旧进程退出，旧音频未播放；新文字保留，重新加载后使用 Serena 播放新回复成功。 |
| 15. Fallback | 模型缺失、实际内存不足、实际 Qwen 进程被终止、强制 System 均实测通过；超时和取消不触发旧回复回退由自动测试覆盖。 |
| 16. 新增文件 | 见文件清单。 |
| 17. 修改文件 | 见文件清单；原工作区其他修改保留。 |
| 18. 自动测试 | 最终 447 项：445 通过、2 跳过，无失败，22.313秒；其中新增语音运行时/界面测试 28 项。两个跳过为原有可选真实系统语音测试、权限不足的符号链接测试。 |
| 19. Smoke Test | 独立真实模型合成通过；六 WAV 播放及 System 路径通过；最终 Qwen 整合测试 passed，记录的 3 个子进程全部退出。 |
| 20. 下一步判断 | 继续用 0.6B 做听感验收。当前延迟和内存余量不支持直接推荐升级 1.7B；本轮停止于 0.6B。 |

### 独立模型资源记录

| 阶段 | 整机已用内存 | 语音进程 RSS | 整卡显存 / PyTorch 显存 |
| --- | ---: | ---: | --- |
| 开始前 | 11.30GiB | 0.03GiB | 整卡 2.48GiB |
| 加载完成采样点 | 12.16GiB | 0.85GiB | PyTorch allocated 2.01GiB、reserved 2.13GiB |
| 全过程采样峰值 | 13.72GiB | 2.41GiB | 整卡 5.06GiB |
| 推理期 PyTorch 峰值 | — | — | allocated 2.29GiB、reserved 2.44GiB |

RAM 采样间隔 0.25秒、整卡 GPU 采样间隔 1秒；采样峰值是观测值，不保证捕捉到每个瞬时峰值。
模型加载阶段也包含内存峰值，不能只用加载完成后的低 RSS 来估计所需内存。

### 最终整合运行

`voice_desktop_20260924_final/desktop_report.json` 记录：首次应用内试听含加载、合成和播放约 34.963秒；
紧接着的第二次试听约 7.173秒，复用同一进程。新消息取消旧请求约 15.6ms，旧音频没有重播，
新回复的实际后端是“本地声音 · Serena”。实际终止 Qwen 子进程后回退到系统，播放器停止约 20.1ms。
模型缺失、强制 System、临时业务数据不被更改等检查通过，关闭后记录的三个子进程均已退出。

这些毫秒指标是本次 UI 调用返回的耗时，不代表模型首次生成延迟，也不是音频设备端到端延迟保证。

## 实现与文件

新增：

- `voice/qwen_backend.py`：独立 Python 的延迟启动、有限 JSON/PCM 管道、超时、取消、复用与退出。
- `voice/selecting_tts.py`：Auto / Local / System，明确的实际声音标识和失败回退。
- `scripts/qwen_voice_worker.py`：离线 Qwen worker；模型 SDK 只在该独立进程导入。
- `scripts/test_qwen_desktop.py`：显式 `--smoke` 才运行的真实界面/播放器/进程测试，业务数据使用临时目录。
- `tests/test_qwen_runtime.py`：真实轻量管道子进程和假后端测试，不加载 Qwen。
- `tests/test_qwen_voice_pipeline.py`：Qt 控件切换、完整长回复分句、旧结果隔离和聊天不中断。
- 本报告。

修改：`voice/voice_models.py`、`voice/tts_service.py`、`voice/voice_service.py`、`voice/runtime.py`、
`voice/windows_tts.py`、`voice/streaming_text.py`、`chat_window.py`、`.gitignore`、`README.md`。

主环境 requirements、Python 版本、模型权重、人格/记忆/待办逻辑以及打包 spec 均未因此次接入修改。
修改前相关源码保存在 `runtime/voice_changes_20260923_before/`，测试音频、报告和备份不进入 Git。

## 原始结果与复跑

- 独立模型：`runtime/cache/voice_demo_20260923/smoke_report.json`
- WAV 播放与系统回退：`runtime/cache/voice_desktop_20260924_system/desktop_report.json`
- 系统停止与新消息打断：`runtime/cache/voice_desktop_20260924_controls/desktop_report.json`
- 首轮真实 Qwen 界面验证：`runtime/cache/voice_desktop_20260924_full/desktop_report.json`
- 最终 Qwen 整合复验：`runtime/cache/voice_desktop_20260924_final/desktop_report.json`
- 自动回归日志：`runtime/voice_regression_20260924.log`

普通自动测试不加载真实 Qwen：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python -m unittest discover -s tests -p 'test_*.py'
Remove-Item Env:QT_QPA_PLATFORM
```

真实测试会显示测试桌宠窗口并播放短音频；不接触原有用户业务数据，不调用聊天 API：

```powershell
python scripts/test_qwen_desktop.py --smoke --show --output-dir runtime/cache/voice_desktop_manual
```

内存暂不足时，可仅检查系统回退、停止、新消息及六个已生成 WAV：

```powershell
python scripts/test_qwen_desktop.py --smoke --show --system-only --output-dir runtime/cache/voice_desktop_system_manual
```

测试结束后关闭测试窗口和其语音进程。音色是否达到预期，需要用户亲耳比较；本轮不继续进入其他模型或克隆阶段。
