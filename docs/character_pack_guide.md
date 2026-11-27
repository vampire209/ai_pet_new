# 角色包与 24fps 流畅动画制作指南（Phase 13）

从 Phase 13 起，AI 桌宠支持 **角色包（Character Pack）热切换**，并内置对标白龙桌宠的 **24fps 透明动态 WebP 流式解码引擎**（`StreamPlayer` + `perf_counter` 精确帧时钟 + `zlib` 循环缓存），同时保留对旧版 PNG 序列帧的完全兼容。

---

## 一、角色包存放位置

右键桌宠 → **外观与行为 → 角色形象 → 打开角色包目录…**，或手动放到以下任一目录：

- 用户目录（推荐，升级不丢）：`%LOCALAPPDATA%\ai_pet_new\characters\<角色ID>\`
- 项目内置目录：`assets\characters\<角色ID>\`

每个角色文件夹内放置一个 `manifest.json` 和对应的 `.webp` 动图（或 PNG 帧子目录）即可，在右键菜单中点击即可热切换，无需重启。

---

## 二、`manifest.json` 规范示例

```json
{
  "id": "claude_chan",
  "name": "克劳德大小姐",
  "author": "vampire209",
  "default_height": 300,
  "avatar": "avatar.png",
  "character": "character.png",
  "actions": {
    "idle": [
      { "file": "idle_main.webp", "fps": 24, "loop": true },
      { "file": "idle_blink.webp", "fps": 24, "loop": true }
    ],
    "variant": [
      { "file": "variant_book.webp", "fps": 24, "loop": false },
      { "file": "variant_sigh.webp", "fps": 24, "loop": false }
    ],
    "walk": { "file": "walk.webp", "fps": 24, "loop": true },
    "move": { "file": "drag.webp", "fps": 24, "loop": true },
    "poke": { "file": "poke.webp", "fps": 24, "loop": false },
    "remind": { "file": "remind.webp", "fps": 24, "loop": true }
  }
}
```

### 支持的动作类别与自动回退链
- `idle`（待机，支持配置多段随机轮播）
- `variant`（随机小动作，播完自动回 `idle`）
- `walk`（自主漫步，缺失时回退 `move` → `idle`，程序会自动水平镜像实现左右往返）
- `move` / `drag`（鼠标拖拽，缺失时互相回退 → `idle`）
- `poke`（左键轻点戳一下，缺失时保留 60fps Q 弹挤压回弹）
- `wait` / `remind` / `think` / `sleep` / `turn`（缺失均安全回退 `idle`）

---

## 三、一键把图生视频 / 纯色底图片转成 24fps 透明 WebP

拿到画师或即梦 / 可灵 / Vidu 生成的纯色底（如 `#C9E2F5` 浅天蓝或 `#00FF00` 绿幕）MP4 视频、图片帧目录或单张图后，直接运行：

```powershell
# 示例 1：把 24fps 纯色底视频自动抠图去白边、脚底对齐并写入角色包
python scripts/build_pet_webp.py `
  --input .\idle.mp4 `
  --pack-dir "$env:LOCALAPPDATA\ai_pet_new\characters\claude_chan" `
  --pack-name "克劳德大小姐" `
  --action idle `
  --fps 24 `
  --size 512

# 示例 2：如果是单次互动动作（如 poke / variant），加 --oneshot
python scripts/build_pet_webp.py `
  --input .\poke.mp4 `
  --pack-dir "$env:LOCALAPPDATA\ai_pet_new\characters\claude_chan" `
  --action poke `
  --oneshot
```

> 提示：解析 MP4 视频需要本机安装 `ffmpeg`（例如 `winget install Gyan.FFmpeg`）；若源素材已是透明 PNG 序列目录，可加 `--no-key` 跳过抠图直接对齐打包。
