# 改动日记（CHANGELOG）

本文件记录 **AI 桌宠（ai_pet_new）** 的重要功能演进、模块调整与架构优化。

---

## [2026-11-28] Phase 14 · 头顶灵动状态胶囊、主动陪聊气泡、前台感知与右键菜单黑屏修复

- **修复 Windows 11 透明桌宠右键菜单黑底黑字问题（`pet_window.py`、`companion_bubble.py`）**：
  - 将桌宠主窗口的 `background: transparent` 样式严格限定到 `QLabel#petWindow`，避免透传给子级 `QMenu` 导致黑底无字。
  - 为右键菜单、所有子菜单及系统托盘菜单统一应用深色磨砂主题（`CONTEXT_MENU_STYLESHEET`），浅色/深色系统下均清晰可读。
- **新增头顶悬浮三段式「灵动状态胶囊」（`pet_status_pill.py`）**：
  - 居中悬浮在桌宠头顶、不抢占窗口焦点（`WindowDoesNotAcceptFocus` + `WA_ShowWithoutActivating`），随拖拽/自主漫步/托盘显隐/全屏让位同步跟随。
  - 左侧圆形气泡按钮：一键弹出「问她一句」快捷浮窗；中间深色胶囊：实时展示待办倒计时、超时/提醒状态或陪聊状态（点击打开待办）；右侧圆形进度环：实时呈现待办完成百分比（点击打开 AI 聊天）。
- **新增肥鱼式主动陪聊气泡与独立短会话（`companion_bubble.py`、`companion_service.py`）**：
  - 贴身轻量气泡支持悬停暂停自动消失与一键「接着聊」跳入主聊天窗；左键连戳自动合并防刷屏（`PokeCoalescer`）；未配置 API Key 时也提供自然的本地短句兜底。
  - 维护独立 30 轮短会话（`CompanionSession`），与主聊天记录及长期记忆完全隔离。
- **新增克制的前台窗口感知（`window_observer.py`）**：
  - 识别写代码、看 B 站视频（自动提取视频标题）、看文档学习等场景，内置停留时长过滤（20s）与防打扰冷却（5 分钟），全屏看片/游戏或待办提醒时自动静音避让。
  - 右键「外观与行为」及托盘菜单新增「显示灵动胶囊」「主动陪聊气泡」「前台窗口感知」开关并持久化至 `pet_state.json`。

---

## [2026-11-28] 24fps 高清渲染清晰度优化与素材管线增强

- **24fps 原生流式动画 1:1 像素级锐利绘制（`pet_window.py`、`pet_motion.py`、`animation_manager.py`）**：
  - 播放原生 24fps 多帧透明 WebP 动图时自动暂停程序化微呼吸缩放（仅在戳一下 Q 弹、拖拽晃动或转身翻转时启用形变），消除持续非整数重采样带来的线条发虚，并降低待机重绘开销。
- **素材转换管线增强（`scripts/build_pet_webp.py`）**：
  - 新增抗明暗渐变绿幕色度抠图（`--green-screen`）、闭合发缝蓝底去除与角落 AI 水印连通域清理。
  - 新增单首帧视频无缝循环模式（`--loop-mode pingpong|crossfade`）与起止时间裁剪（`--start-sec` / `--end-sec`）。
  - 新增动漫线条轻量锐化选项（`--sharpen`），配合 `--size 720 --lossless` 输出高清晰度透明动图。

---

## [2026-11-27] Phase 13 · 流畅动画引擎、物理微动效、自主漫步与角色包体系

参考「白龙桌宠增强版（`codex-bailong-desktop-pet`）」与「肥鱼陪聊（`feiyu-chat-companion`）」的动画与交互架构，全面升级桌宠渲染与动画系统：

### 24fps 流式动画引擎与角色包热切换
- **新增 24fps 透明动态 WebP 流式解码器（`stream_player.py`）**：后台线程 6 帧有界队列流式解码、`time.perf_counter()` 累积帧时钟（消除 41/42ms 舍入漂移）与 `zlib` 压缩循环缓存，显著降低内存占用并支持丝滑 24fps 动画。
- **新增角色包与 `manifest.json` 规范（`character_pack.py`、`docs/character_pack_guide.md`）**：
  - 同时兼容传统 PNG 序列帧与 24fps 透明 WebP/GIF 动图；支持 `idle`（多段轮播）、`variant`（随机小动作）、`walk`、`turn`、`move`/`drag`、`poke`、`wait`、`remind`、`think`、`sleep` 等丰富动作槽位与安全回退链。
  - 桌宠右键与托盘菜单新增 **外观与行为 → 角色形象** 子菜单，支持热切换角色包与一键打开用户角色包目录（`%LOCALAPPDATA%\ai_pet_new\characters\`），选中角色持久化到 `pet_state.json`。

### 60fps 物理手感、自主漫步与全屏自动让位
- **60fps 程序化微动效（`pet_motion.py`、`pet_window.py`）**：
  - 自然呼吸微起伏、左键轻点「戳一下」Q 弹阻尼挤压回弹（同时发射 `pet_poked` 信号，为后续 Phase 14 主动气泡预留）、鼠标拖拽速度驱动的物理摆锤晃动与松手回弹。
- **桌面自主漫步与水平镜像转身（`WanderController`）**：
  - 闲置时在当前屏幕工作区内短距离自然散步、自动水平翻转朝向并偶发播放 `variant` 小动作；拖动、提醒或隐藏时自动让步暂停；支持在菜单随时开关并持久化。
- **视频/游戏全屏自动让位（`video_yield.py`）**：
  - 检测到其他应用全屏覆盖屏幕时临时取消置顶不遮挡画面，退出全屏自动恢复；未读待办提醒仍保持更高优先级；支持菜单随时开关并持久化。

### 一键素材转换工具
- **新增 `scripts/build_pet_webp.py`**：支持将纯色底（如 `#C9E2F5` 浅天蓝、`#00FF00` 绿幕）MP4 视频、图片帧目录或单张立绘自动执行边界连通抠图 + 去白边/去溢色 + 统一脚底锚点对齐，输出 24fps 透明 WebP 并自动注册到角色包 `manifest.json`。

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
