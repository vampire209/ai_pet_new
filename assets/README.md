# Animation assets

`idle` 的 8 帧和 `move` 的 6 帧是从旧项目
`ai_pet_codex/assets/animations/` 原样复制的独立副本。源文件没有被修改。

两组素材均为 1254×1254 的 32 位 ARGB PNG。运行时只读取本项目目录。
`remind` 的 8 帧为用户提供的现有素材，没有重新生成或修改。
`wait` 当前没有素材，播放请求会安全回退到 `idle`。
