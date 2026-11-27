# AI 桌宠（新项目）

这是与 `ai_pet_codex` 完全独立的 Windows / PySide6 新实现。支持透明高 DPI 桌宠动画、系统托盘控制、待办与多节点提醒、DeepSeek 流式 AI 聊天、长期记忆、系统语音与独立环境 Qwen 0.6B Serena 语音。运行时不会读取旧项目路径。

> **更新记录**：详细改动见 [改动日记（CHANGELOG.md）](CHANGELOG.md)。
>
> **可选英语学习版本**：背单词（单词本/复习卡片）与“英语老师能力模式”已从 `master` 主分支移除，并作为可选版本保留在 GitHub 分支 [`optional/english-learning`](https://github.com/vampire209/ai_pet_new/tree/optional/english-learning)（标签 `archive-english-learning`）。如需使用，可随时切换：
> ```powershell
> git fetch origin optional/english-learning
> git checkout optional/english-learning
> ```

## 最新体验与配置说明（2026-11-27）

- **系统托盘与位置记忆**：支持托盘图标与右键切换「始终置顶」「隐藏到托盘」，重启自动恢复上次拖动位置。
- **图形化 AI 接口配置**：右键桌宠 →「桌宠设置 → AI 接口设置」（或聊天窗口右上角 `⋯` 菜单）可直接填写 DeepSeek API Key、Base URL 和模型名称并立即热生效；API Key 留空时仍自动回退读取 `DEEPSEEK_API_KEY` 环境变量。
- **聊天与语音持久化**：多轮聊天记录、语音回复开关与声音来源重启后自动恢复。
- **统一数据目录**：所有个人数据（`tasks.json`、`profile.json`、`memory.db`、`chat_history.json`、`voice_settings.json`、`ai_settings.json`、`pet_state.json`、`chat_ui.json`）统一保存在用户配置目录 `ai_pet_new/` 下，首次运行会自动迁移旧版 `data/tasks.json`。

## Qwen 本地语音（2026-09-24）

源码启动 `python main.py` → 右键桌宠 → AI聊天 → 选择“声音来源”：

- **自动**：优先 Serena；本地环境、模型、内存或进程出现问题时回退到系统声音，并显示原因。
- **本地声音**：仅使用 Serena；失败会提示，可改选“自动”或“系统声音”。
- **系统声音**：直接使用 Windows 已安装音色，不加载 Qwen。

语音回复默认关闭。“试听”不需要聊天 API；打开“语音回复”后，从下一轮开始按句朗读。
首次需要本地声音时才启动独立进程，后续复用。停止、发送新消息或切换来源会丢弃旧声音；
若模型正在合成则终止该进程，下一次需要时重新加载。退出聊天窗口会释放语音进程。
Auto 在一次模型失败后会继续使用系统声音，避免每句话重试；释放内存后可先切到“系统声音”，
再切回“自动”重试。

六段本地试听已经生成并通过 Windows 播放器播放，447 项自动测试中 445 通过、2 跳过。
Qwen 与完整界面并行、进程复用、新消息打断后重启、真实进程崩溃回退，以及内存不足时
回退均已实测通过。保护条件是**加载前至少 4GiB 可用系统内存**，不是官方最低要求。
独立试听冷加载约 41 秒，预热后短句合成约 6–10 秒；还不能承诺实时对话速度或主观音质。

详见[实机接入与验收记录](docs/phase11c2_qwen_integration_20260924.md)，内含六段试听和实测数据。
本阶段仅使用 0.6B CustomVoice/Serena，不提供情绪或音色克隆控件；未重新打包 EXE。
请运行源码使用新增功能，旧 `dist_latest/AIPetNew.exe` 不包含此次接入。

## 对话中的逐句朗读

同一个聊天窗口中，文字生成一句就排队朗读一句，不必等整段结束。
合成在后台运行；系统声音无需 GPU，本地 Serena 使用已下载的 Qwen 模型。
两种方式都不需要收费语音 API。目前还不是参考角色音色或免按键实时通话。

源码启动 `python main.py` → 右键桌宠 → AI聊天：

- “试听”：不需要 DeepSeek API，试听当前所选声音，不写入聊天或记忆。
- “语音回复：开”：下一轮正常 AI 对话开始按句朗读；真实 AI 对话仍需配置
  原有 DeepSeek API。原聊天、人格、记忆及待办流程不变。
- “停止朗读”：只取消本轮音频和排队句子，文字回答继续；“停止”仍停止文字生成。

语音默认关闭，当前开关和声音来源仅在本次运行生效。麦克风输入、参考角色声音、设置持久化
仍待后续阶段；请用源码验收。

详情：[逐句朗读说明](docs/voice_sentence_streaming.md)；
历史记录：[Phase 11A 骨架验收](docs/phase11a_voice.md)。

Phase 11C 已完成[自然本地 TTS 评估与接入设计](docs/phase11c_natural_tts_design.md)，
新增配置/能力声明及离线文件预检骨架。[9 月 15 日的环境搭建记录](docs/phase11c2_qwen_machine_status.md)
是历史状态；9 月 23–24 日的试听和后端接入进展见上面的最新验收记录。

```powershell
python -m unittest discover -s tests -p "test_phase11a*.py" -v
python -m unittest discover -s tests -p "test_streaming*.py" -v
python -m unittest discover -s tests -p "test_*.py"
```

最后一条会同时覆盖既有能力模式、单词学习和阅读生词联动测试，不执行
`live_*.py` 或真实 API 探针。麦克风和本地 STT 等待 Phase 11B。

## 桌宠功能

- 透明、无边框、始终置顶，默认逻辑高度 270px
- 启动时预加载 PNG 序列，并按屏幕 DPR 高质量缩放
- 支持 `idle`、`move`、`wait`、`remind` 状态与动画回退
- 同一动画被重复请求时不会重置到第 1 帧
- 状态优先级为 `remind > move > wait > idle`
- 真正超过系统拖动阈值后才进入 move，释放后恢复 idle
- 双击左键打开单实例“我的待办”；单击和拖动不会误触发
- 右键菜单提供“打开待办”“AI聊天”“桌宠设置”“记忆管理”和“退出”

## 待办与多提醒

- 添加、编辑、完成/取消完成、删除任务
- 可选本地事件日期与时间，不进行 UTC 转换
- 可多选提前 5/10/30 分钟、1/3/12 小时、1/2/3 天、1 周
- 有事件时间的任务始终具有一个最终提醒
- 新建时已错过的提前提醒不会生成
- 每个 Reminder 独立保存 ID、触发时间、触发状态和确认状态
- 修改时间会重新计算未触发提醒
- 修改提前选项会同步未触发部分，并保留已触发历史以防重复
- 有未来提醒时进入 wait；remind 和拖动按集中优先级安全抢占
- 无 wait 素材时逻辑状态仍为 wait，画面安全回退 idle
- 半透明无边框 bubble 在桌宠附近显示，并限制在当前屏幕内
- bubble 支持完成、稍后 5/10/30/60 分钟、查看待办和关闭
- 稍后提醒独立持久化，重启后仍会到点触发
- 完成任务会取消所有尚未触发的提醒
- 多个提醒同时到期时进入队列，一次只显示一个窗口
- 关闭或处理当前窗口后才显示下一项
- bubble 结束后根据未来提醒恢复 wait 或 idle
- 扫描不会重复创建 bubble，也不会反复重置 remind 动画

## 每日与每周循环

- 重复类型支持不重复、每天、每周
- 每周按 ISO 星期编号保存：周一为 1，周日为 7，可多选
- 循环任务完成仅结束当前 occurrence，并自动推进到下一次
- 每日保持原始时刻；每周可靠处理跨周、跨月和跨年
- 每个新周期重新生成 advance 与 final，旧周期提醒不会复活
- snooze 只改变当前 occurrence 的下一次提示，不改变基础计划时间
- 循环任务保存最近一次完成时间 `last_completed_at`
- 启动时保留未完成且已过期的 final，不补发已经错过的 advance
- 删除循环任务会同时移除当前、稍后和未来提醒

## 中文自然语言快速添加

- 采用完全本地的规则解析，不使用 AI、LLM、网络请求或云服务
- 输入后先显示任务、时间、重复规则和 advance 预览，确认后才创建
- 支持今天、明天、后天、今晚、明晚
- 支持数字和中文时间、半点、上午/中午/下午/晚上与 12/24 小时转换
- 支持分钟后、半小时后、小时后的可测试相对时间
- 支持周一至周日、星期一至星期日以及下一自然周
- 支持月日、横线/斜杠日期和中文年月日
- 支持提前 5/10/30/60 分钟、提前半小时及多个提醒去重
- 支持每天、每日和每周多星期，并复用 Phase 6 ISO 星期编号
- 缺少具体时间、明显过去时间和未支持表达会明确报错且不会创建
- 确认创建统一走 `TodoManager`，后续编辑、提醒、snooze 和持久化完全一致

## DeepSeek AI 聊天

- “AI聊天”是独立普通窗口，关闭聊天不会退出桌宠或影响 Todo
- 聊天面板使用浅蓝、淡紫和白色的桌宠风格，复用 idle 角色素材作为头像
- 用户消息右对齐、桌宠消息左对齐，圆角气泡会随窗口宽度调整
- 顶部以桌宠名称和回复状态为主，模型名称作为次要信息显示
- 流式首个分块到达前显示“桌宠正在想……”，之后原位更新同一气泡
- 使用 OpenAI 兼容 Python SDK 的 Chat Completions 流式接口
- 默认地址为 `https://api.deepseek.com`
- 默认模型为 `deepseek-v4-flash`，可切换为 `deepseek-v4-pro`
- 请求显式关闭 thinking，聊天窗口只显示最终回答文字流
- 网络请求在 Qt 后台线程池执行，桌宠动画、Todo 和提醒不会被阻塞
- 每个回答只创建一个助手消息框，分块到达时原位更新
- 支持停止生成；停止后保留已经显示的内容并停止继续追加
- 支持清空聊天，同时清除当前会话上下文并保留系统人格
- 保留最近 20 轮上下文，旧轮次自动丢弃，系统人格始终保留
- 普通聊天由 DeepSeek 处理；明确的创建命令优先使用本地 Phase 7 解析器
- 未配置 Key、认证、网络、超时、限流、额度、模型和服务错误都会安全显示
- API Key 只从 `DEEPSEEK_API_KEY` 环境变量读取，不写入代码、配置或日志

## 聊天中创建待办

- 可以在 AI 聊天中输入“明天下午3点提醒我交作业”
- 支持 Phase 7 已有的时间、提前提醒、每天和每周多星期表达
- 创建命令完全走本地 `ChatActionRouter` 和 `NaturalLanguageParser`，不消耗 API
- 即使没有 `DEEPSEEK_API_KEY` 或网络断开，仍能生成待办确认卡
- 草案阶段不会写入 JSON，也不会生成 Reminder
- 只有点击确认卡中的“确认创建”后，才调用现有 `TodoManager` 保存任务
- 确认成功后正常生成 advance/final，并继续复用 daily/weekly recurrence
- 点击“取消”不会创建任务或修改 JSON
- 缺少具体时间时会提示重新输入完整句子，不会猜测默认时间
- 真实创建成功结果会作为受控上下文加入聊天，模型不会凭空声称已执行
- 清空聊天只清除聊天 UI 和上下文，不会删除已经创建的 Todo
- 当前聊天操作只支持创建 Todo，不支持删除、修改或自动完成 Todo

## 桌宠 Profile 与 Persona

- 右键“桌宠设置”可以修改角色名字、关系模式和六项性格参数
- 默认名字为“桌宠”，默认关系为“普通桌宠”，保持 Phase 9 的聊天风格
- 关系预设包括普通桌宠、好朋友、女友、黏人女友和安全约束下的病娇风
- 温柔度、黏人度、撒娇度、吃醋度、占有欲和活泼度范围均为 0～100
- 手动调整预设参数后自动切换为“自定义”，不会循环重置滑杆
- 保存后，已打开的聊天 Header 会立即更新；下一次请求使用新的 System Persona
- `PersonaBuilder` 将参数转换为简短的自然行为描述，不向模型发送原始配置数字
- 用户当前对语气和回答方式的明确要求始终高于 Profile 默认风格
- Todo 创建确认、不能修改/删除/完成已有 Todo 等能力边界不会被关系模式覆盖
- Profile 与 Todo、聊天历史相互独立；清空聊天不会清除 Profile
- Windows 下配置保存在用户配置目录的 `ai_pet_new\profile.json`，不写入项目目录
- 配置缺失或损坏时安全恢复默认，不影响桌宠启动
- Phase 10A 不包含长期记忆、SQLite、Embedding 或自动记忆

## 手动长期记忆

- 右键“记忆管理”打开单实例 Memory Manager，关闭窗口不会退出桌宠
- 记忆分为核心记忆、普通记忆和归档记忆，三种状态彼此明确分离
- 支持手动新增、编辑、永久删除、设为/取消核心、归档、恢复和关键字搜索
- 核心记忆归档时会自动取消核心；归档记忆恢复后回到普通记忆
- 中文、英文和 Emoji 内容均可保存，空内容不会写入数据库
- 数据使用 SQLite 持久化到 Windows 用户配置目录的 `ai_pet_new\memory.db`
- Memory 与 Profile、Todo、Reminder 和聊天历史相互独立
- Clear Chat 只清聊天记录，不会删除或改变任何 Memory
- Phase 10B 不会自动生成记忆，也不会把记忆注入 DeepSeek Persona 或聊天上下文
- Phase 10B 不包含 Embedding、向量检索、自动归档或自动遗忘规则

## 聊天智能记忆

- Phase 10C 会从“我最近在学习……”“我更喜欢……”“以后……”等少量具有长期价值的用户消息中自动形成活跃记忆
- 普通闲聊、一次性要求、AI 回复和已经进入 Todo 流程的内容不会写入 Memory
- 自动记忆优先匹配并更新同主题内容，避免同一事实不断新增
- 自动生成的记忆始终是活跃记忆；核心记忆仍只能由用户在记忆管理中手动点击“☆ → ★”
- 所有未归档核心记忆默认参与每轮聊天；普通活跃记忆按本地相关度最多选择 5 条
- 当前用户消息和当前会话中的最新要求优先于长期记忆，Memory 不能覆盖 Todo 等应用能力边界
- Clear Chat 仍只清聊天历史，下一次聊天可以重新按相关性取回长期记忆
- 归档记忆默认不参与聊天，恢复到活跃后才会再次参与检索
- 被实际注入的记忆会更新 use_count 与 last_used_at
- 低重要度且至少 90 天未使用的非核心活跃记忆可以保守归档，但不会自动永久删除
- 核心记忆永不自动归档，自动逻辑也不会设置或取消核心
- 检索完全使用本地规则，不使用 Embedding、向量数据库，也不会为 Memory 额外发起 DeepSeek 请求

## 本地保存与兼容

- 使用 `data/tasks.json` 本地持久化
- 保存采用同目录临时文件原子替换
- 缺失、空文件、损坏 JSON、缺少字段和旧空时间均安全处理
- Phase 3 的 `reminder_triggered` / `reminder_acknowledged` 会自动迁移为 final Reminder
- Phase 4 Reminder 缺少 `cancelled` 时自动按 `false` 加载
- Phase 1–5 任务缺少 `repeat_type` 时自动按 `none` 加载
- 缺失的 `repeat_weekdays` 与 `last_completed_at` 使用安全默认值
- 时间保存为电脑本地的无时区 ISO 字符串

## 安装与运行

```powershell
cd D:\codex\ai_pet_new
python -m pip install -r requirements.txt
python main.py
```

首次使用 AI 聊天前，在当前 PowerShell 会话设置 DeepSeek API Key：

```powershell
$env:DEEPSEEK_API_KEY = "你的 DeepSeek API Key"
python main.py
```

如需写入当前 Windows 用户的环境变量，可执行下面的命令，然后重新打开终端或
重启桌宠：

```powershell
[Environment]::SetEnvironmentVariable(
    "DEEPSEEK_API_KEY",
    "你的 DeepSeek API Key",
    "User"
)
```

不要把真实 Key 写进 `.py`、JSON、README 或提交到 Git。未配置 Key 时桌宠、
Todo、提醒和自然语言快速添加仍可正常使用；打开 AI 聊天会显示配置提示。

默认模型无需额外配置。临时切换到 `deepseek-v4-pro`：

```powershell
$env:DEEPSEEK_MODEL = "deepseek-v4-pro"
python main.py
```

恢复默认模型可关闭当前 PowerShell 窗口，或移除本会话中的
`DEEPSEEK_MODEL`。聊天可以在本地生成待办确认卡，但不会静默创建；删除、修改
和完成任务仍需在“我的待办”中操作。

自动退出的启动冒烟检查：

```powershell
python main.py --smoke-test
```

运行 Phase 1–10C 全部自动化测试：

```powershell
python -m unittest discover -s tests -p "test_phase*.py" -v
```

真实本地时间 +5 分钟 advance / +10 分钟 final 测试：

```powershell
python tests\live_phase4_reminders.py
```

真实测试使用临时数据，不修改正式 `data/tasks.json`。

Phase 5 可见交互测试（使用可控本地测试时钟）：

```powershell
python tests\live_phase5_interaction.py
```

Phase 6 可控本地时间循环交互测试：

```powershell
python tests\live_phase6_recurrence.py
```

Phase 7 可控本地时间快速添加交互测试：

```powershell
python tests\live_phase7_quick_add.py
```

Phase 8 本地 Fake 流式聊天联调（不需要 Key，不联网、不产生费用）：

```powershell
python tests\live_phase8_chat.py
```

这个脚本验证流式增量、多轮上下文、停止、异常恢复和清空。只有在你明确希望
发起一次真实请求并已经设置 `DEEPSEEK_API_KEY` 时，才手动运行：

```powershell
python tests\live_deepseek_api.py
```

没有 Key 时该真实探针会打印 `REAL_SKIP` 并正常退出，不会自动读取任何密钥
文件。自动化测试、smoke-test 和 Fake 联调都不会调用真实 API。

Phase 9 聊天创建待办可见联调（A–G 全部使用 Fake client 和临时 JSON）：

```powershell
python tests\live_phase9_chat_todo.py
```

Phase 10A Profile 可见联调（A–E 全部使用 Fake client 和临时配置）：

```powershell
python tests\live_phase10a_profile.py
```

该脚本验证名字即时同步与重启恢复、女友 Persona、黏人女友自定义参数、
安全的高亲密行为描述，以及 Clear Chat 保留 Profile。

Phase 10B 手动长期记忆可见联调（A–F 使用 Fake client 和临时 SQLite）：

```powershell
python tests\live_phase10b_memory.py
```

该脚本验证新增、核心切换、归档/恢复、编辑、重启持久化，以及 Clear Chat
不会清除 Memory。测试不会创建或修改正式用户目录中的 `memory.db`。

Phase 10C 智能记忆 Fake Live（A–H 使用临时 SQLite，不调用真实 API）：

```powershell
python tests\live_phase10c_memory.py
```

该脚本验证钢琴记忆自动提取与去重、Clear 后召回、傅里叶相关检索、当前要求
优先、长期偏好更新、低价值旧记忆归档和核心永不自动归档。

Phase 9 人工验证：

1. 右键桌宠打开“AI聊天”，输入“明天下午3点提醒我交作业，提前30分钟提醒”，
   先检查确认卡，再点击“确认创建”，最后打开 Todo 检查任务及提醒。
2. 输入“每天晚上8点提醒我背单词”，确认卡应显示每天，创建后应进入循环任务。
3. 输入“你觉得今天适合学什么？”，应继续通过 DeepSeek 正常流式回答。
4. 在新 PowerShell 中不设置 `DEEPSEEK_API_KEY` 并启动程序：本地待办命令仍可
   创建确认卡和保存任务，普通聊天则显示未配置 Key。

## Phase 6–9 数据格式

```json
[
  {
    "id": "task_xxx",
    "text": "高等数学考试",
    "completed": false,
    "created_at": "2026-08-27T09:00:00",
    "completed_at": null,
    "event_at": "2026-09-10T09:00:00",
    "repeat_type": "weekly",
    "repeat_weekdays": [1, 3, 5],
    "last_completed_at": null,
    "reminder_offsets": [60, 1440],
    "reminders": [
      {
        "id": "rem_advance",
        "type": "advance",
        "offset_minutes": 1440,
        "trigger_at": "2026-09-09T09:00:00",
        "triggered": false,
        "acknowledged": false,
        "cancelled": false
      },
      {
        "id": "rem_advance_2",
        "type": "advance",
        "offset_minutes": 60,
        "trigger_at": "2026-09-10T08:00:00",
        "triggered": false,
        "acknowledged": false,
        "cancelled": false
      },
      {
        "id": "rem_final",
        "type": "final",
        "offset_minutes": 0,
        "trigger_at": "2026-09-10T09:00:00",
        "triggered": false,
        "acknowledged": false,
        "cancelled": false
      },
      {
        "id": "rem_snooze",
        "type": "snooze",
        "offset_minutes": 10,
        "trigger_at": "2026-09-10T09:10:00",
        "triggered": false,
        "acknowledged": false,
        "cancelled": false
      }
    ]
  }
]
```

## 素材

- `assets/animations/idle/frame_01.png` 至 `frame_08.png`
- `assets/animations/move/frame_01.png` 至 `frame_06.png`
- `assets/animations/remind/frame_01.png` 至 `frame_08.png`

`remind` 使用用户提供的现有素材，没有重新生成。当前没有 `wait` 素材，因此
wait 状态会安全显示 idle。AI 聊天已经独立实现；云同步尚未实现。
