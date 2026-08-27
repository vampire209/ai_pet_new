# Animation assets

`idle` 的 8 帧和 `move` 的 6 帧是从旧项目
`ai_pet_codex/assets/animations/` 原样复制的独立副本。源文件没有被修改。

两组素材均为 1254×1254 的 32 位 ARGB PNG。运行时只读取本项目目录。
`remind` 的 8 帧为用户提供的现有素材，没有重新生成或修改。
`wait` 当前没有素材，播放请求会安全回退到 `idle`。

## 可替换 UI 素材

- `icons/app.ico` 或 `icons/app.png`：应用、窗口、任务栏和 Alt+Tab 图标。
- `ui/avatar/avatar.png`：聊天 Header 与消息头像的高清原图。
- `ui/character/character.png`：聊天区域右下角完整角色原图。

PNG 也可替换为同名 WEBP/JPG/JPEG（应用图标仅支持 ICO/PNG）。专用头像或
立绘不存在时，会分别直接回退到 `animations/idle/frame_01.png` 原图；
显示时始终从原图按当前屏幕 DPR 一次性平滑缩放，不从缩略图继续放大。
