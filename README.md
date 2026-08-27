# AI 桌宠（新项目）

这是与 `ai_pet_codex` 完全独立的 Windows / PySide6 新实现。当前完成至
Phase 5，运行时不会读取旧项目路径。

## 桌宠功能

- 透明、无边框、始终置顶，默认逻辑高度 270px
- 启动时预加载 PNG 序列，并按屏幕 DPR 高质量缩放
- 支持 `idle`、`move`、`wait`、`remind` 状态与动画回退
- 同一动画被重复请求时不会重置到第 1 帧
- 状态优先级为 `remind > move > wait > idle`
- 真正超过系统拖动阈值后才进入 move，释放后恢复 idle
- 双击左键打开单实例“我的待办”；单击和拖动不会误触发
- 右键菜单提供“打开待办”和“退出”

## 待办与多提醒

- 添加、编辑、完成/取消完成、删除任务
- 可选本地事件日期与时间，不进行 UTC 转换
- 可多选提前 5/30 分钟、1/3/12 小时、1/2/3 天、1 周
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

## 本地保存与兼容

- 使用 `data/tasks.json` 本地持久化
- 保存采用同目录临时文件原子替换
- 缺失、空文件、损坏 JSON、缺少字段和旧空时间均安全处理
- Phase 3 的 `reminder_triggered` / `reminder_acknowledged` 会自动迁移为 final Reminder
- Phase 4 Reminder 缺少 `cancelled` 时自动按 `false` 加载
- 时间保存为电脑本地的无时区 ISO 字符串

## 安装与运行

```powershell
cd D:\codex\ai_pet_new
python -m pip install -r requirements.txt
python main.py
```

自动退出的启动冒烟检查：

```powershell
python main.py --smoke-test
```

运行 Phase 1–5 全部自动化测试：

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

## Phase 5 数据格式

```json
[
  {
    "id": "task_xxx",
    "text": "高等数学考试",
    "completed": false,
    "created_at": "2026-08-27T09:00:00",
    "completed_at": null,
    "event_at": "2026-09-10T09:00:00",
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
wait 状态会安全显示 idle。每日/每周任务、AI/API 和云同步尚未实现。
