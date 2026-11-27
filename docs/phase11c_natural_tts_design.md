# Phase 11C：自然本地 TTS 评估与接入设计

核查日期：2026-09-14，收尾验收：2026-09-15。范围：用户本轮要求的 A（实际代码核查）与 B（兼容性评估、接入设计），以及不接入生产链路的配置/预检契约。

**这不是“神经 TTS 已安装完成”的验收报告。当前能听到的仍是 Windows 系统声音。**
本轮没有下载模型、安装依赖、创建语音 Python 环境、改动驱动、切换运行后端、进行克隆或重打包 EXE。

## 1. 当前项目的真实状态

| 检查对象 | 当前实现和边界 |
| --- | --- |
| `voice/voice_service.py` | QObject 协调，QRunnable/QThreadPool 后台合成；generation、request ID、playback ID 隔离过期结果。单段播放、最多预合成下一段，队列限制 64 句/12000 字。 |
| `voice/backends.py` | 可替换的 `TTSBackend.synthesize(text, reference_audio_path, speech_speed) -> SpeechAudio`，没有模型 SDK。 |
| `voice/tts_service.py` | 延迟创建、串行合成、关闭资源；目前工厂没有自动回退，缓存身份仅为模型路径。 |
| `voice/windows_tts.py` | 真实离线 System.Speech/SAPI，隐藏 PowerShell 子进程复用，JSON 管道返回内存 WAV，再解码为 PCM。明确不支持参考音色。 |
| `voice/audio_player.py`、`voice/windows_audio_player.py` | 统一 PCM 播放接口，Windows WinMM 异步播放，不依赖 QtMultimedia；音量仅调整本段 PCM。 |
| `voice/runtime.py` | 桌面工厂目前仅创建 Windows 后端，默认语音关闭；不点试听/不启用回复则不启动语音进程或音频设备。 |
| `chat_window.py` | 同一个聊天窗口已有开关、试听、停止朗读。`_on_chunk` 送入分句器，`_on_completed` 只补尾句，不再重复读全文。新消息/清空/关闭会停止旧语音。 |
| `voice/voice_models.py` | 不可变 VoiceSettings，语速/音量/路径配置；本轮不改它，设置仍仅内存生效。 |
| `voice/text.py`、`voice/streaming_text.py` | 独立朗读文本清理，处理 Markdown、代码、链接、隐藏生词数据及跨 chunk 分句；不改聊天显示或历史。 |
| STT | 仍为服务接口及假后端；未实现麦克风采集、实际本地识别或全双工对话。 |
| 测试 | 本轮修改前普通套件共 380 项：379 通过，1 项真实 Windows TTS 合成默认跳过。不是旧提示中的 320 项。 |
| requirements / pyproject | 主项目只有 PySide6/openai 依赖，没有项目 pyproject，没有神经 TTS 依赖。 |
| `AIPetNew.spec` | 现有打包只显式收集 assets/config 等；保留 `icuuc.dll`、`icudt78.dll` 排除修复，本轮不改。 |

Windows TTS **现在是唯一实际运行后端**。设计目标是将它作为 Auto 的 fallback；还没有把“设计目标”冒充成已上线功能。

## 2. 本机条件与选择结论

本机只读核查结果：Windows、Python 3.13.14、PySide6 6.11.2；RTX 5060 **Laptop** GPU，8151 MiB 显存（约 8GB），物理内存约 15.40 GiB（标称 16GB），驱动 616.64。
当前 Python 找不到 torch、transformers、qwen_tts、cosyvoice、numpy。没有为本轮安装它们。

推荐 **Qwen3-TTS CustomVoice 路线**，先使用现成中文女声 Serena，不需要参考音频。
按本机资源，建议首先批准 **0.6B CustomVoice 的自然度/延迟试用**；它不是情绪功能完成方案。
如果下一步必须直接检验“温柔、开心等情绪指令”，则选择 **1.7B CustomVoice**，接受更高资源风险并先做短句试验。

| 候选 | 中文及音色 | 情绪指令 | 参考声音 | 本项目判断 |
| --- | --- | --- | --- | --- |
| Qwen3-TTS-12Hz-0.6B-CustomVoice | 有预置中文女声 Serena/Vivian | **不支持**，官方实现对 0b6 清空 instruct | 此权重不支持 | 首轮资源保守试用，验证自然度，不声称解决情绪控制 |
| Qwen3-TTS-12Hz-1.7B-CustomVoice | 同系列预置音色 | 支持 instruct；效果要试听 | 此权重不支持 | 更符合情绪目标，但本机 8GB/16GB 必须实测峰值与延迟 |
| Qwen3-TTS Base 0.6B/1.7B | 面向参考语音 | 不应按 CustomVoice 的情绪能力宣传 | 支持，通常还需参考文本 | 留给 11D，需另外一套 Base 权重，不是把 WAV 填入 CustomVoice 就能克隆 |
| Fun-CosyVoice3-0.5B-2512 | 中文能力值得评估 | instruct2 仍需要参考语音 | 支持参考语音流程 | 当前无参考音频条件下不作为首接；依赖、子模块及 Windows 验证成本更高 |

0.6B 情绪限制以[官方推理实现](https://github.com/QwenLM/Qwen3-TTS/blob/main/qwen_tts/inference/qwen3_tts_model.py#L732)为准，不依据模型卡的泛化描述。
1.7B 的预置音色和能力见[官方模型卡](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice)。
CosyVoice 判断依据[官方示例](https://github.com/QwenAudio/CosyVoice/blob/main/example.py)和[模型文件列表](https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512/tree/main)。

自然度预期：上下文驱动的语调和节奏有改善空间，Serena 的官方定位较柔和；但本机尚未合成试听，**没有已验证的听感提升、流畅率或首包延迟**。不保证换模型就自动获得目标角色的表现力。

## 3. Python / Windows / GPU / 打包兼容性

Qwen 包声明 Python >=3.9，且列出 3.13 classifier，因此不能断言“不支持 Python 3.13”。不过它会带入固定版本 transformers/accelerate 及音频依赖，不能据此认定现有桌宠环境可直接兼容。[官方 pyproject](https://github.com/QwenLM/Qwen3-TTS/blob/main/pyproject.toml)

建议保留主程序 3.13，另建 **Python 3.12 64 位 Voice Runtime**，与官方建议一致。模型解释器只通过 stdin/stdout JSON 与主程序交互。隔离目的是控制依赖/DLL/打包风险，不是宣称 3.13 绝对不可用。[官方环境说明](https://github.com/QwenLM/Qwen3-TTS#environment-setup)

CosyVoice 官方环境为 Python 3.10；其 requirements 固定 torch/torchaudio 2.3.1，而该 torch 版本没有 Windows cp313 轮子，不可直接搬到本工程。[官方依赖](https://github.com/QwenAudio/CosyVoice/blob/main/requirements.txt)、[torch 2.3.1 文件](https://pypi.org/project/torch/2.3.1/#files)

首接采用普通 PyTorch + SDPA，不强制 FlashAttention、vLLM、TensorRT 或 torch.compile。
Qwen 的模型实现声明 SDPA 支持；FlashAttention 官方仍说明 Windows 编译需要更多测试。[Qwen 模型实现](https://github.com/QwenLM/Qwen3-TTS/blob/main/qwen_tts/core/models/modeling_qwen3_tts.py)、[FlashAttention 要求](https://github.com/Dao-AILab/flash-attention#installation-and-features)

5060 所属 Blackwell 需要匹配的 CUDA PyTorch 构建，不能照抄旧 CUDA 包。PyTorch 2.7 的 CUDA 12.8 构建已有对应架构支持，后续版本的轮子矩阵也在变化；获准安装后再锁定一组 Windows/Python3.12/torch/torchaudio 组合并实测，不自动安装“最新版”，本轮不改驱动。[PyTorch 官方说明](https://pytorch.org/blog/pytorch-2-7/)、[Windows 版本配对](https://pytorch.org/get-started/previous-versions/)

打包设计：主 EXE 不导入 torch，不把 CUDA DLL 或模型塞入 onefile；Voice Runtime 作为外部目录单独部署。以后增加固定 worker 入口时按 `sys.executable` 所在安装目录定位外部环境，不能把打包后的 `sys.executable` 当 Python，也不能将模型写入 `_MEIPASS`。保留当前 ICU 排除逻辑。尚未进行新的 EXE 验收。

## 4. 下载、磁盘和运行资源预算

以下模型大小为 2026-09-14 官方文件列表元数据求和，GB 是十进制；只读元数据，未下载权重。完整 Qwen 仓库已含 speech_tokenizer，**不重复额外加算 tokenizer**。

| 完整模型仓库 | 文件合计 | 本地存放建议 |
| --- | ---: | --- |
| [0.6B CustomVoice](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice/tree/main) | 2,498,388,392 bytes ≈ 2.50GB | `models/voice/Qwen3-TTS-12Hz-0.6B-CustomVoice/` |
| [1.7B CustomVoice](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice/tree/main) | 4,520,218,951 bytes ≈ 4.52GB | `models/voice/Qwen3-TTS-12Hz-1.7B-CustomVoice/` |
| [CosyVoice3 完整仓库](https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512/tree/main) | 9,747,516,745 bytes ≈ 9.75GB | 本轮不选择；其中包含多套可选推理权重，并非最低安装量 |

**以下是工程规划估计，不是官方最低要求，也不是本机实测。** 短句、batch=1、BF16/FP16、无其他 GPU 重负载下暂按下表预留；长句、加载方式、注意力实现及上下文会改变峰值。

| 预算项 | 0.6B CustomVoice | 1.7B CustomVoice |
| --- | --- | --- |
| GPU 显存预留 | 约 3–5 GiB | 约 5–8 GiB；8GB 卡可能没有足够桌面余量甚至 OOM |
| 语音进程 RAM 预算（含加载峰值余量） | 约 4–8 GiB | 约 6–12 GiB；16GB 整机运行其他应用时可能换页 |
| 模型落盘 | 约 2.50GB，缓存重复另计 | 约 4.52GB，缓存重复另计 |
| 独立 Python + CUDA/PyTorch + 音频包磁盘 | 暂预留 8–15GB | 暂预留 8–15GB |

依赖下载包体积尚未锁定，不冒充精确数字；它与安装后的环境体积不同。若批准安装，先解析目标轮子清单与总下载量，再安装。建议磁盘先有约 25GB 空余以容纳环境、模型和临时下载，不表示一定占满。

本机 8GB 显存不是不可试，但不能承诺同时运行鸣潮/其他 GPU 模型仍顺畅。首轮测试应关闭这些重负载；桌宠动画保持开启，测拖动/响应而不是只看模型能否加载。

Qwen 与 CosyVoice 官方模型卡/代码标注 Apache-2.0。分发时保留相应许可证/NOTICE并核对依赖许可证；本轮未做参考声音采集。未来参考素材须由用户提供并具有使用授权。[Qwen 模型许可证标识](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice)、[CosyVoice 模型卡](https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512)

## 5. 接入设计：沿用现有架构

以下含待实现组件，**不是当前运行图**：

```text
原 ChatWindow / ChatService / Persona / Memory / Todo（保持原链路）
  └─ 朗读副本 → 现有 SpeechSegmenter / VoiceService 队列
       └─ 现有 Qt 后台 worker → TTSService → SelectingTTSBackend（待实现）
            ├─ System → 现有 WindowsTTSBackend
            ├─ Local Neural → LocalNeuralTTSBackend（待实现）
            │    └─ 有界 JSON 管道 → 独立 Python 3.12 → Qwen → PCM16
            └─ Auto → Local Neural；不可用则 System；仍失败只停语音
       └─ SpeechAudio → 现有 WindowsAudioPlayer
```

不把模型调用放入 VoiceService/ChatWindow；保留原 `TTSBackend` 方法兼容已有假后端。
用 factory 闭包传入不可变 VoiceProfile/NeuralRuntimeConfig，先不改 synthesize 的必需参数；需要更多请求参数时增加有明确能力声明的扩展接口，不破坏老接口。

### 配置与能力

本轮 `voice/neural_contracts.py` 是契约和本地文件预检骨架，不是模型执行器，也不接入现有 VoiceSettings/UI。

- BackendChoice：Auto / Local Neural / System。未来 UI 仅呈现后端、可用音色、语速/音量等少量选项；temperature、设备和路径放高级配置。
- VoiceProfile：name/backend/model_path/language/speaker_id/speed/temperature/emotion，加未来 reference_audio_path/reference_text/speaker_embedding_path。Persona 不读取这些字段，音色设置不改变角色人格。
- NeuralRuntimeConfig：外部 python_executable/model_path/device/model_variant。正式接入时由单一配置层将路径复制到调用快照，拒绝冲突配置。
- TTSCapabilities：speed/emotion/reference/speaker/temperature/streaming、语言/音色列表。0.6B emotion=false，CustomVoice reference=false；未支持的选项不悄悄忽略。
- Qwen 没有现有 SAPI 那样的精确数值倍速接口，骨架 speed=false；1.7B 可用文字语速指令但不承诺 1.2x 精确倍速。volume 沿用播放器。将来若加保音高变速要另测音质与耗时。
- 高层 Qwen API 每次返回完整句 waveform，第一版适配器 streaming=false；现有“逐句朗读”继续可用。不能把模型宣传的流式能力当作当前适配器已经输出流式 PCM。[官方接口](https://github.com/QwenLM/Qwen3-TTS/blob/main/qwen_tts/inference/qwen3_tts_model.py#L669)

### 依赖、文件和可用性状态

分四步明确报告，不使用一个含糊的“已就绪”：

1. 主进程静态预检：解释器路径、模型目录、官方 config 类型/变体、权重、文本处理资源、speech_tokenizer；JSON 有界读取，权重只检查非空及相对分片路径。**不验证权重内容完整性**。
2. 外部 `--check`（待实现）：用户明确检测/首次朗读时，后台运行隔离解释器；返回 Python/包版本/torch CUDA/设备能力。不能用主进程的 find_spec 代替隔离环境检测。
3. Load/handshake（待实现）：本地模型成功加载并返回实际 model variant/speakers/languages 才能标记模型已加载。
4. 单句真实 smoke（待批准资源安装后）：有效 PCM、耗时、峰值内存/显存、退出清理全部通过后才标记已验收。

本轮预检始终 `dependencies_checked=False`、`runtime_available=False`。即使本地文件齐全也不会显示“神经语音可用”。缺文件只提示，不下载、不执行安装器。
它要求本地绝对路径，拒绝显式 UNC/URL/设备路径，不跟随分片越出模型目录；文件权限或路径展开异常汇入 issues。网络映射盘及系统级重定向仍应由部署者避免，本函数不是文件系统沙箱。

### 本地通信、关闭与异常

选择 stdin/stdout JSON 行而非 localhost 服务：不开放端口，不引入服务部署/鉴权；固定本地 worker 入口，`shell=False`、隐藏窗口，用户文本仅作为 JSON 数据。
协议拟定 version=1，消息带 request_id/generation、操作、模型身份及参数；响应带同样 ID、实际后端、能力、PCM base64/采样率/声道/宽度。日志去 stderr，不混入协议；不持久记录聊天全文、音频或参考素材。

建议输入单段最多 240 字、协议控制请求 64KiB、PCM 16MiB、响应行 24MiB，上下游均有限制并拒绝未知操作/错 ID/畸形 base64/不完整帧。
模型初始化和推理设置独立有界超时（首轮可用加载 120 秒、单句 60 秒作为可调上限，不代表预计延迟），超时 kill/reap 并回退，不阻塞 Qt 主线程。

`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` 在专用子进程内导入 SDK **之前**设定；仅允许已校验的绝对本地模型目录，不能将不存在的路径解释为远程 model ID，禁用 remote code。
不能只传最外层 local_files_only：官方 processor/tokenizer 内部调用不全部透传，因此还需离线模式及针对未缓存文件的离线失败测试。

首次明确使用神经语音才 spawn/load，以后复用；模型身份/解释器/设备改变才重建。进程崩溃和 OOM 仅成为语音错误。
close 幂等、可并发、立即拒绝新请求；主线程不能 wait 模型锁，kill 后后台回收管道和进程。退出必须清理，不能留下 CUDA 后台常驻。

### 停止、Fallback 与实际后端显示

目前 `stop()` 能立即停播放并丢弃旧结果，但不会主动中断长时间推理。正式接神经后端必须新增可选、非阻塞 `cancel_current()`：停止时 kill 当前不可协作取消的 helper，唤醒旧 worker；下一次按需重启。不得为此重建 ChatService。

保留 generation/request/playback ID 三道验证。取消/关闭/过期结果不是可恢复的模型失败，**不得在这些情况下启动系统回退，避免旧回复复活**。

Auto：每个请求最多 local→system 一次；神经不可用时对当前配置熔断，避免每句重复加载失败。用户重新检测或改变配置才清除熔断。状态显示“系统声音（本地模型未配置/加载失败）”。
显式 Local Neural：失败清楚报错，保留文字聊天，不伪装成神经声音成功。显式 System：不检测模型、不启动外部 Python。用户切换后端先停旧语音，下一轮生效。
系统后端不支持的 emotion/reference 设置不会作为已执行能力宣称；Auto 回退时明确说明这些设置未应用。

必须同步处理现有 TTSService 两处：模型/reference 路径校验现在在 factory 前，需下沉具体后端以免阻断 Auto；缓存 key 目前只有 model_path，需包含 provider/runtime/model variant/device，不能换后端却错误复用旧实例。

### 文本、长回复、PCM 与缓存

沿用现有分句器，不再创建第二个分句系统。后续 `split_for_speech(full_text)` 作为现有 feed+finish 的包装；把旧 `speak()` 完整长回复入口也接同一个有界队列（当前仍一次送整段），避免仅修流式入口。
已有中文句末/英文缩写/小数保护保留；分号可在短句下作为候选边界，但不见逗号就拆。优先保留足够上下文，模型首句耗时高时允许小段合并。

`normalize_tts_text()` 后续只处理朗读副本：重复标点、括号自然停顿、URL/代码跳过等。缩写和数字要有上下文规则及样例，不把小数/日期/电话号码一律逐字改写；中英混排先利用模型能力。保留 !/? 情绪信息需评估现有清理器将感叹号转句号的行为，本轮不偷偷更改听感。

模型 float waveform 在子进程内做有限值检查、裁剪、转 little-endian PCM16；采样率来自模型结果，首接优先单声道，不通过修改采样率“假装调速”。主程序只接 SpeechAudio，不传 numpy/pickle/tensor。
当前内存 PCM 已有界、无音频落盘，本轮保留，不新增无界缓存。未来若缓存短句，在 runtime/cache/voice 内以 text/模型修订/音色/语言/参数/归一化版本为 key，需大小上限、过期清理、临时文件异常删除；volume 可在播放器阶段处理。

## 6. 本轮交付与验收边界

本轮新增：

- `voice/neural_contracts.py`：不可变配置/能力与只读本地预检；不创建真正的 LocalNeuralTTSBackend，不执行模型。
- `tests/test_neural_contracts.py`：小型临时文件及 mock 测试；不加载权重。
- 本文；README 增加本阶段入口和“尚未切换声音”的说明。

本轮未改：VoiceService、TTSService、WindowsTTSBackend、AudioPlayer、ChatWindow、ChatService、Persona、Memory、Todo、requirements、AIPetNew.spec、现有配置文件。旧工作区修改保留，不撤销。

测试最终结果见本节末尾；骨架测试不等于真实推理通过，且未实现的 Auto/Local 切换不能算已测试成功。

正式后端接入后的必测清单：

| 要求 | 本轮/后续验证安排 |
| --- | --- |
| 可插拔、OFF 不初始化、lazy、停止与 stale、播放器失败、语音不伤文字 | 当前已有假后端测试保护；接入 selector 后追加参数化覆盖 |
| capabilities、非法配置、缺路径、错变体、缺分片/空文件/越界路径 | 本轮新契约/预检测试 |
| Auto 回退、System 完全绕过神经、Local 不冒充成功 | selector 待实现，以 MockLocal 的加载/推理/OOM/进程崩溃测试 |
| 长回复切句/清理 | 现有流式分句测试保留；新增 full-text 包装、分号与归一化测试待接入 |
| 取消期间不得 fallback、退出不阻塞、模型只加载一次 | 新 IPC/selector 落地时补竞态测试及可控假子进程 |
| 自然度、情绪、Windows/CUDA、内存峰值、桌宠动画 | 资源批准后单独真实 smoke/试听，不进入普通 unittest |

真实试听至少比较：普通陪伴短句、疑问、数字中英混排、长回复；同一句系统/神经 A/B。1.7B 另比较中性/开心/安慰，0.6B 不显示情绪选项。记录冷加载、首句时间、实时率、每句间隙、RAM/VRAM峰值；期间拖动桌宠、发新消息、停止朗读、退出。只有测过才报告“自然、流畅、能及时中断”。

### 本轮最终测试结果（2026-09-15）

- 新增契约/预检测试 31 项：30 通过、1 跳过。覆盖能力差异、不可变配置、非法参数、缺失/空文件、错模型及 codec 类型、缺文本处理资源、畸形/超大 JSON、分片越界、路径权限异常、不启动进程/联网/读取权重内容。
- 全量普通测试 **411 项：409 通过、2 跳过，22.587 秒**。没有失败。
- 两个跳过分别是：显式选择才执行的真实 Windows TTS 合成；本机不具备创建真实符号链接的权限。分片解析后越界的可移植 mock 测试另已通过。
- `python main.py --smoke-test` 退出码 0；`git diff --check` 无空白错误（有既有 LF/CRLF 提示）。
- 本轮没有真实神经模型 smoke、GPU 推理、试听或新 EXE 验收，因此上述通过数不代表自然度、情绪或显存占用已经验证。

可复跑：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest discover -s tests -p "test_neural_contracts.py"
python -m unittest discover -s tests -p "test_*.py"
python main.py --smoke-test
```

普通启动桌宠时不要保留 `QT_QPA_PLATFORM=offscreen`；该变量仅用于无界面的自动化测试。

## 7. 下一步需要用户批准的内容

推荐下一步只批准一种：0.6B CustomVoice（约 2.50GB，先测自然度），或 1.7B CustomVoice（约 4.52GB，直接测情绪但资源风险更高）。不同时下载两套，不下载 Base，不进入 11D。

批准后才执行：在 `D:/codex/ai_pet_new/runtime/voice_env/` 建独立 Python3.12 环境；锁定适合 Windows/5060 的 torch+torchaudio 及 qwen-tts 音频依赖，核对下载大小；模型下载到 `D:/codex/ai_pet_new/models/voice/` 对应子目录；随后实现/验证 IPC 后端和 Auto fallback，再接现有 UI。

来源仅使用上述官方 Hugging Face/GitHub；国内下载可选官方 ModelScope 的同名 Qwen 模型并核对文件/版本。运行期间保持离线，不让合成调用自动补下载。

**完成本轮后停止，等待用户选择与批准；没有任何后台下载或自动继续任务。**
