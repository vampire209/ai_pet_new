> 历史记录：本文描述 2026-09-15 的环境搭建状态。后续六段试听、真实后端接入及最终验证已完成，最新结果见 [2026-09-24 验收记录](phase11c2_qwen_integration_20260924.md)。

# Phase 11C-2：Qwen 0.6B 开发机接入进度

日期：2026-09-15。**本阶段尚未完整完成；停在独立 Smoke Test 的内存预检，没有切换桌宠声音。**

## 当前结果

已建立隔离环境、安装依赖、下载并核验唯一获准的 Qwen3-TTS-12Hz-0.6B-CustomVoice。
独立环境的 CUDA 小运算通过，`pip check` 没有损坏/冲突依赖。
首次真实试听命令在模型导入前被可用内存保护拦截：当时只有 **3.36 GiB** 可用系统内存，而本次脚本默认预留门槛为 **4 GiB**。

4 GiB 是本次测试的保守启动条件，**不是官方最低内存要求**；这次没有发生模型 CUDA OOM，也不能据此认定 0.6B 在本机不能运行。
没有降低主项目配置、强关用户程序、调整驱动、下载 1.7B/Base/VoiceDesign 或进行克隆。

遵守“独立 Smoke Test 成功后再接桌宠”的顺序，因此尚未实现/接通真实 IPC 后端、Auto/Local/System 选择框与神经回退。
下一步需要先保存工作、关闭不用的应用，释放一些内存，再继续测试。无需重新下载。

## 环境、来源与磁盘实测

- 主项目仍是 Python **3.13.14**；主 `requirements.txt`、主 Python、现有 VoiceService/ChatWindow/Persona/Memory/Todo 均未修改。
- 语音 venv：`D:/codex/ai_pet_new/runtime/voice_env/`，Python **3.12.14**。
- venv 使用本机应用已附带的 Python3.12.14 创建，没有运行未知安装器、安装新系统 Python 或改 PATH。
- 基础解释器位于 `C:/Users/33313/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`。这是开发机依赖，若未来该缓存被移走，须按记录重建语音 venv；不作为最终分发方案。
- 显卡：RTX 5060 Laptop GPU，驱动 **616.64**。PyTorch **2.8.0+cu128**，torchaudio **2.8.0+cu128**，实际 torch CUDA runtime **12.8**，CUDA capability **12.0**，BF16 支持查询为 True。
- CUDA 验证只创建了 16 个元素并求和，结果 **16.0**。它证明基础 CUDA 路径可用，不证明 TTS 的性能或音质。
- 已安装 qwen-tts **0.1.1**、transformers **4.57.3**、accelerate **1.12.0**、numpy **2.2.6**、soundfile **0.13.1**、librosa **0.11.0**、onnxruntime **1.23.2**、psutil **7.0.0** 等。
- 主要约束在 `voice/requirements-qwen.txt`；全部 96 个已安装包（含 pip/setuptools）的版本快照在 `voice/requirements-qwen-lock.txt`。这些文件不应安装到主 Python。
- qwen-tts 声明的 Gradio 依赖会带入 FastAPI/uvicorn/WebSocket 库；它们仅在隔离环境中作为上游依赖安装。**本项目没有新增或启动任何 HTTP/FastAPI/WebSocket 服务**，没有使用 Web UI。

模型仅来自[官方 Qwen 0.6B CustomVoice 仓库](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice/tree/main)，固定 revision：

`85e237c12c027371202489a0ec509ded67b5e4b5`

存放位置：`D:/codex/ai_pet_new/models/qwen3_tts/0.6b_custom_voice/`。

| 本机文件长度求和（不是网页估计，也不是 NTFS 分配簇计数） | 实测 bytes |
| --- | ---: |
| 13 个模型仓库文件，不含下载元数据 | 2,498,388,392 |
| 模型目录，包含下载器的本地元数据 | 2,498,389,792 |
| 独立语音 venv | 8,493,124,768 |
| 保留用于复现的 PyTorch 安装 wheel | 3,461,384,651 |

模型内容约 **2.50GB / 2.33GiB**；完整语音 venv 约 **8.49GB / 7.91GiB**。
环境与模型已经排除出 Git。下载器元数据和 pip 缓存与模型大小分开，不混算成权重体积。
为避免重新下载，PyTorch wheel 目前保留在 `runtime/voice_install/`；没有擅自删除用户缓存或文件。

本机计算的两份权重 SHA-256 与官方文件元数据一致：

| 文件 | SHA-256 |
| --- | --- |
| `model.safetensors` | `bc3c7e785eb961179c25450d1acff03f839e0002f2f3a5aeb67b5735c0fa2adb` |
| `speech_tokenizer/model.safetensors` | `836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258` |

PyTorch wheel 从[官方 CUDA12.8 索引](https://download.pytorch.org/whl/cu128/torch/)取得，SHA-256 校验通过：
`0ad925202387f4e7314302a1b4f8860fa824357f9b1466d7992bf276370ebcff`。
最初 pip 下载中途网络超时，后改用可续传下载，校验完成后才安装；没有改用未知来源。

## 独立测试与 20 项验收状态

测试脚本：`scripts/test_qwen_tts.py`，模型引擎：`scripts/qwen_voice_engine.py`。它们不接入现有桌宠后端。
仅支持本阶段的 0b6/custom_voice/12Hz 本地模型，默认 Serena/Chinese，batch=1、BF16/SDPA；进入模型 SDK 前开启离线标志，缺资源不会自动下载。
无 emotion API；“温柔/开心”文件名仅标识测试文本风格，不冒充情绪控制能力。
单次输入上限 240 字；音频统一单声道 PCM16，检查空音频、NaN/Inf、长度、削波比例；数值检查不能代替听感判断。

| 用户要求 | 当前实测或明确状态 |
| --- | --- |
| 1. Python 语音版本 | 3.12.14 |
| 2. 实际依赖 | 已安装并锁定记录；pip check 通过 |
| 3. 模型实际大小 | 文件内容 2,498,388,392 bytes，含下载元数据目录 2,498,389,792 bytes |
| 4. Serena 是否成功 | 尚未合成，不能报告成功 |
| 5. 六个试听文件 | **0/6，尚未生成** |
| 6. 首次模型加载耗时 | 未测，保护在模型导入/加载前停止 |
| 7. 第一次生成耗时 | 未测 |
| 8. 第二次生成耗时 | 未测 |
| 9. 推理峰值 GPU 显存 | 未测。仅记录预检时整卡已用 2,720,006,144 bytes，约 2.53GiB，包含桌面/其他应用 |
| 10. 推理峰值系统内存 | 未测。预检时整机已用 12,883,726,336 bytes，约 12.00GiB；可用 3,613,028,352 bytes，约 3.36GiB |
| 11. 桌宠动画是否流畅 | 没有模型并行运行，尚未做这一项实机验收 |
| 12. AI 聊天是否受影响 | 运行链路未改，自动回归通过；未调用真实聊天 API |
| 13. 停止朗读 | 现有系统语音相关回归通过；真实 Qwen 停止未验收 |
| 14. 新消息打断 | 现有 stale/新回合回归通过；真实 Qwen 打断未验收 |
| 15. fallback | 神经 selector/IPC 尚未接入，不能报告已通过真实回退 |
| 16. 新增文件 | 下方清单 |
| 17. 修改文件 | `.gitignore`、`README.md` |
| 18. 自动测试 | **419 项：417 通过、2 跳过，24.557 秒；没有失败** |
| 19. Smoke Test | CUDA 小测试通过；Qwen 独立 smoke 在 `resource_preflight` 停止，退出码 1，未合成 |
| 20. 下一步建议 | 继续完成已下载的 **0.6B** 实测；没有依据升级/下载 1.7B |

两个跳过仍是默认关闭的真实 Windows TTS 测试，以及本机权限不允许的真实符号链接测试。
本次新增 8 项脚本契约测试全部通过，其中包含“内存不足时不得调用 model.load/synthesize”。
`python main.py --smoke-test` 退出码 0；未重新打包 EXE。

原始记录：`D:/codex/ai_pet_new/runtime/cache/voice_demo/smoke_report.json`。
它的 `status=failed`、`failure_phase=resource_preflight`，明确说明阻断发生在预检，不是模型推理失败。

## 六个试听文件的位置（目前尚不存在）

以下是脚本配置的目标，**不是可点击试听的已生成成果**：

```text
D:/codex/ai_pet_new/runtime/cache/voice_demo/01_normal.wav
D:/codex/ai_pet_new/runtime/cache/voice_demo/02_soft.wav
D:/codex/ai_pet_new/runtime/cache/voice_demo/03_happy.wav
D:/codex/ai_pet_new/runtime/cache/voice_demo/04_long.wav
D:/codex/ai_pet_new/runtime/cache/voice_demo/05_mixed.wav
D:/codex/ai_pet_new/runtime/cache/voice_demo/06_number.wav
```

脚本还会先生成 `00_warmup.wav` 和 `00_warmup_repeat.wav`，用相同短句记录首/第二次耗时；当前也都未生成。

## 文件与续接方式

新增源码/说明：

- `scripts/qwen_voice_engine.py`
- `scripts/test_qwen_tts.py`
- `tests/test_qwen_smoke_contracts.py`
- `voice/requirements-qwen.txt`
- `voice/requirements-qwen-lock.txt`
- 本报告

生成的外部资源：voice_env、固定版本模型、voice_install 安装报告/安装 wheel、voice_demo 的失败预检 JSON；全部不进 Git。
没有修改现有 VoiceService/TTSService/WindowsTTSBackend/AudioPlayer/ChatWindow，也没有修改 AIPetNew.spec 或主 requirements。

保存并关闭不用的 WPS 文档、多余浏览器窗口等，建议留出约 5–6GB 可用内存（至少通过 4GiB 测试门槛）后，再运行：

```powershell
cd D:\codex\ai_pet_new
.\runtime\voice_env\Scripts\python.exe -u .\scripts\test_qwen_tts.py --model-dir D:\codex\ai_pet_new\models\qwen3_tts\0.6b_custom_voice --output-dir D:\codex\ai_pet_new\runtime\cache\voice_demo --device cuda:0 --smoke
```

若需要生成后依次播放，可加 `--play`。不要为绕过保护直接降低门槛；可在了解并接受资源风险后重新评估。
现有目录中的同名试听/报告在重新执行时会被替换，因此要保留某次结果应先复制到不同目录，或传入另一个 output-dir。
后续只有当独立加载与短句/六段生成成功后，才继续 IPC 后端、懒启动复用、三种模式、回退、真实中断和动画并行验收。

**当前无后台下载/推理任务，等待释放内存后继续。不是最终完成验收。**

