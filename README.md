# AI 桌宠（新项目）

这是与 `ai_pet_codex` 完全独立的 Windows / PySide6 新实现。当前完成至
Phase 8，运行时不会读取旧项目路径。

## 桌宠功能

- 透明、无边框、始终置顶，默认逻辑高度 270px
- 启动时预加载 PNG 序列，并按屏幕 DPR 高质量缩放
- 支持 `idle`、`move`、`wait`、`remind` 状态与动画回退
- 同一动画被重复请求时不会重置到第 1 帧
- 状态优先级为 `remind > move > wait > idle`
- 真正超过系统拖动阈值后才进入 move，释放后恢复 idle
- 双击左键打开单实例“我的待办”；单击和拖动不会误触发
- 右键菜单提供“打开待办”“AI聊天”和“退出”

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
- 使用 OpenAI 兼容 Python SDK 的 Chat Completions 流式接口
- 默认地址为 `https://api.deepseek.com`
- 默认模型为 `deepseek-v4-flash`，可切换为 `deepseek-v4-pro`
- 请求显式关闭 thinking，聊天窗口只显示最终回答文字流
- 网络请求在 Qt 后台线程池执行，桌宠动画、Todo 和提醒不会被阻塞
- 每个回答只创建一个助手消息框，分块到达时原位更新
- 支持停止生成；停止后保留已经显示的内容并停止继续追加
- 支持清空聊天，同时清除当前会话上下文并保留系统人格
- 保留最近 20 轮上下文，旧轮次自动丢弃，系统人格始终保留
- 聊天人格只提供聊天、解释和建议，不会直接修改 Todo 或提醒数据
- 未配置 Key、认证、网络、超时、限流、额度、模型和服务错误都会安全显示
- API Key 只从 `DEEPSEEK_API_KEY` 环境变量读取，不写入代码、配置或日志

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
`DEEPSEEK_MODEL`。聊天不会自动创建、完成或删除待办，需要待办时请使用“我的
待办”或其中的中文快速添加。

自动退出的启动冒烟检查：

```powershell
python main.py --smoke-test
```

运行 Phase 1–8 全部自动化测试：

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

## Phase 6–8 数据格式

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
