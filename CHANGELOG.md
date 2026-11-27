# 改动日记（CHANGELOG）

本文件记录 **AI 桌宠（ai_pet_new）** 的重要功能演进、模块调整与架构优化。

---

## [2026-11-27] 精简主分支并完成核心体验与持久化优化

### 模块归档与移除
- **英语学习与英语老师模式已归档为 GitHub 可选分支**：
  - 完整包含背单词/单词本（`word_learning/`）与“英语老师能力模式”（`ai/ability_modes.py`）的版本已永久保存在 GitHub 分支 [`optional/english-learning`](https://github.com/vampire209/ai_pet_new/tree/optional/english-learning) 与标签 `archive-english-learning`。
  - 如需使用背单词与考研英语老师功能，可随时执行：
    ```powershell
    git fetch origin optional/english-learning
    git checkout optional/english-learning
    ```
- **本地主分支（`master`）瘦身**：
  - 移除 `word_learning/`、`ai/ability_modes.py` 及相关聊天拦截、生词卡片与测试代码。
  - 保持旧版 `profile.json` 向下兼容（自动忽略遗留的 `ability_mode` 字段，不会报错）。

### 新增与优化
- **系统托盘与桌宠窗口控制（`pet_window.py`、`pet_state.py`）**：
  - 新增系统托盘图标与菜单（支持显示/隐藏桌宠、始终置顶开关、快捷打开各功能窗口、退出）。
  - 桌宠右键菜单新增「始终置顶」切换与「隐藏到托盘」。
  - 拖动桌宠后自动记住位置，下次启动自动恢复（若外接屏幕移除导致坐标超出可见区域，则安全回退到主屏右下角）。
- **统一用户数据存储目录（`app_paths.py`）**：
  - 源码运行与打包运行统一使用用户配置目录 `ai_pet_new/` 存放 `tasks.json`、`profile.json`、`chat_ui.json`、`chat_history.json`、`voice_settings.json`、`ai_settings.json`、`pet_state.json`、`memory.db` 及可选用户级 `config.json`。
  - 首次启动时若检测到旧版 `data/tasks.json` 且新目录尚无 `tasks.json`，会自动单次迁移待办数据，避免丢失。
- **聊天记录与语音设置持久化（`ai/chat_history_store.py`、`voice/settings_store.py`）**：
  - AI 聊天上下文自动原子落盘到 `chat_history.json`，重开桌宠自动恢复历史气泡；点击「清空聊天」同步清空落盘记录。
  - 语音回复开关与声音来源（自动 / 本地声音 / 系统声音）自动保存到 `voice_settings.json`，重启后自动恢复。
- **GUI 可视化配置 DeepSeek 接口（`ai/ai_settings_service.py`、`profile_window.py`）**：
  - 「桌宠设置」升级为双标签页：**角色与人格** + **AI 接口设置**（支持图形化填写 API Key、Base URL、模型名称，密码框支持显隐切换）。
  - 聊天窗口右上角菜单新增「AI 接口设置」快捷入口；保存后立即热生效，无需重启桌宠；留空 API Key 时仍自动回退读取 `DEEPSEEK_API_KEY` 环境变量。

---

## 历史里程碑（Phase 1 – Phase 11C）

- **Phase 11C**：接入独立隔离环境的 Qwen3-TTS 0.6B Serena 本地语音（支持自动/本地/系统三种声音来源与异常自动回退）及对话逐句流式朗读。
- **Phase 10A – 10C**：独立桌宠人格与关系模式（Profile & Persona）、SQLite 手动与自动长期记忆、Windows 单文件打包流程。
- **Phase 8 – 9**：DeepSeek 流式 AI 聊天界面、聊天内自然语言识别并生成待办确认卡。
- **Phase 1 – 7**：无边框透明高 DPI 桌宠动画、待办与多节点提醒、每日/每周循环任务、本地中文自然语言快速添加。
