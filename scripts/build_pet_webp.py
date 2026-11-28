"""Convert solid-background videos, frame folders, or images into 24fps transparent WebP.

Usage examples:
  # 1) Convert a solid-background MP4 video (requires ffmpeg on PATH) to 24fps transparent WebP:
  python scripts/build_pet_webp.py --input idle.mp4 --output characters/claude_chan/idle.webp --fps 24 --size 512

  # 2) Convert a folder of PNG/JPG frames into 24fps transparent WebP and register in manifest.json:
  python scripts/build_pet_webp.py --input frames_dir/ --pack-dir characters/claude_chan --action idle --pack-name "克劳德娘"

  # 3) Convert a single solid-background character image into a clean transparent PNG/WebP:
  python scripts/build_pet_webp.py --input master.jpg --output characters/claude_chan/idle.webp --size 512
"""

from __future__ import annotations

import argparse
from collections import deque
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable

from PIL import Image, ImageFilter


IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".webm", ".mkv", ".avi"})


def parse_hex_color(value: str) -> tuple[int, int, int]:
    cleaned = value.strip().lstrip("#")
    if len(cleaned) != 6:
        raise ValueError(f"颜色必须是 6 位十六进制，例如 #C9E2F5：{value}")
    return int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16)


def sample_border_color(image: Image.Image) -> tuple[int, int, int]:
    """Estimate solid background color from the four image corners."""
    rgb = image.convert("RGB")
    w, h = rgb.size
    points = (
        (0, 0),
        (w - 1, 0),
        (0, h - 1),
        (w - 1, h - 1),
        (w // 2, 0),
        (0, h // 2),
        (w - 1, h // 2),
    )
    rs, gs, bs = [], [], []
    for px, py in points:
        r, g, b = rgb.getpixel((px, py))
        rs.append(r)
        gs.append(g)
        bs.append(b)
    rs.sort()
    gs.sort()
    bs.sort()
    mid = len(rs) // 2
    return rs[mid], gs[mid], bs[mid]


def _color_distance(c1: tuple[int, int, int], c2: tuple[int, int, int]) -> float:
    dr = c1[0] - c2[0]
    dg = c1[1] - c2[1]
    db = c1[2] - c2[2]
    return math.sqrt(dr * dr + dg * dg + db * db)


def key_out_solid_background(
    image: Image.Image,
    *,
    bg_color: tuple[int, int, int] | None = None,
    tolerance: float = 42.0,
    feather: float = 28.0,
    remove_interior_holes: bool = True,
) -> Image.Image:
    """Chroma keying with border flood-fill, enclosed chroma-hole removal, and dehalo."""
    rgba = image.convert("RGBA")
    w, h = rgba.size
    if w == 0 or h == 0:
        return rgba

    # If the source already has real transparency on borders, keep it untouched.
    pixels = rgba.load()
    corner_alphas = (
        pixels[0, 0][3],
        pixels[w - 1, 0][3],
        pixels[0, h - 1][3],
        pixels[w - 1, h - 1][3],
    )
    if all(a < 16 for a in corner_alphas):
        return rgba

    key = bg_color or sample_border_color(rgba)
    hard_limit = max(1.0, float(tolerance))
    soft_limit = hard_limit + max(1.0, float(feather))

    # BFS flood-fill from outer border (and optional enclosed chroma-colored hair gaps).
    visited = bytearray(w * h)
    bg_mask = bytearray(w * h)
    queue: deque[tuple[int, int]] = deque()

    def try_seed(x: int, y: int) -> None:
        idx = y * w + x
        if visited[idx]:
            return
        r, g, b, _a = pixels[x, y]
        if _color_distance((r, g, b), key) <= soft_limit:
            visited[idx] = 1
            queue.append((x, y))

    for x in range(w):
        try_seed(x, 0)
        try_seed(x, h - 1)
    for y in range(h):
        try_seed(0, y)
        try_seed(w - 1, y)

    # For clearly colored screens (green/blue screen), also seed enclosed background holes
    # such as gaps between hair strands or arms.
    key_chroma = max(key) - min(key)
    if remove_interior_holes and key_chroma >= 28:
        hole_seed_limit = min(hard_limit * 0.72, 28.0)
        for y in range(1, h - 1):
            row = y * w
            for x in range(1, w - 1):
                if visited[row + x]:
                    continue
                r, g, b, _a = pixels[x, y]
                if _color_distance((r, g, b), key) <= hole_seed_limit:
                    visited[row + x] = 1
                    queue.append((x, y))

    while queue:
        x, y = queue.popleft()
        idx = y * w + x
        bg_mask[idx] = 1
        r, g, b, _a = pixels[x, y]
        dist = _color_distance((r, g, b), key)
        if dist > hard_limit:
            # Soft boundary pixel: stop flood expansion here so we don't leak inside.
            continue
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < w and 0 <= ny < h:
                nidx = ny * w + nx
                if not visited[nidx]:
                    nr, ng, nb, _na = pixels[nx, ny]
                    if _color_distance((nr, ng, nb), key) <= soft_limit:
                        visited[nidx] = 1
                        queue.append((nx, ny))

    # Apply feathered alpha + color decontamination (Feiyu-inspired dehalo)
    kr, kg, kb = key
    for y in range(h):
        row = y * w
        for x in range(w):
            if not bg_mask[row + x]:
                continue
            r, g, b, a = pixels[x, y]
            dist = _color_distance((r, g, b), key)
            if dist <= hard_limit:
                pixels[x, y] = (0, 0, 0, 0)
            else:
                alpha_ratio = max(0.0, min(1.0, (dist - hard_limit) / (soft_limit - hard_limit)))
                new_a = int(round(a * alpha_ratio))
                if new_a < 18:
                    pixels[x, y] = (0, 0, 0, 0)
                else:
                    # Un-blend background spill from semi-transparent edge pixels.
                    inv = 1.0 - alpha_ratio
                    dr = max(0, min(255, int(round((r - kr * inv) / max(0.2, alpha_ratio)))))
                    dg = max(0, min(255, int(round((g - kg * inv) / max(0.2, alpha_ratio)))))
                    db = max(0, min(255, int(round((b - kb * inv) / max(0.2, alpha_ratio)))))
                    pixels[x, y] = (dr, dg, db, new_a)
    return rgba


def align_frames_to_canvas(
    frames: list[Image.Image],
    *,
    canvas_size: int = 512,
    padding_ratio: float = 0.06,
) -> list[Image.Image]:
    """Compute union non-transparent bbox and place all frames with unified foot anchor."""
    if not frames:
        return []

    bboxes = [frame.getbbox() for frame in frames]
    valid = [box for box in bboxes if box is not None]
    if not valid:
        return [
            frame.resize((canvas_size, canvas_size), Image.Resampling.LANCZOS)
            for frame in frames
        ]

    left = min(b[0] for b in valid)
    top = min(b[1] for b in valid)
    right = max(b[2] for b in valid)
    bottom = max(b[3] for b in valid)
    crop_w = max(1, right - left)
    crop_h = max(1, bottom - top)

    pad = int(round(canvas_size * max(0.0, min(0.2, padding_ratio))))
    usable = max(1, canvas_size - pad * 2)
    scale = min(usable / float(crop_w), usable / float(crop_h))
    scaled_w = max(1, int(round(crop_w * scale)))
    scaled_h = max(1, int(round(crop_h * scale)))

    paste_x = (canvas_size - scaled_w) // 2
    paste_y = canvas_size - pad - scaled_h  # Foot-anchored!

    aligned: list[Image.Image] = []
    for frame in frames:
        cropped = frame.crop((left, top, right, bottom))
        resized = cropped.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        canvas.paste(resized, (paste_x, paste_y), resized)
        aligned.append(canvas)
    return aligned


def _remove_detached_corner_watermarks(rgba: Image.Image) -> Image.Image:
    """Keep the main character foreground and strip tiny detached corner watermark blobs."""
    w, h = rgba.size
    if w < 16 or h < 16:
        return rgba
    pixels = rgba.load()
    visited = bytearray(w * h)
    components: list[tuple[int, list[int]]] = []

    for y in range(h):
        row = y * w
        for x in range(w):
            idx = row + x
            if visited[idx] or pixels[x, y][3] < 20:
                continue
            queue: deque[tuple[int, int]] = deque([(x, y)])
            visited[idx] = 1
            cells: list[int] = []
            while queue:
                cx, cy = queue.popleft()
                cidx = cy * w + cx
                cells.append(cidx)
                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if 0 <= nx < w and 0 <= ny < h:
                        nidx = ny * w + nx
                        if not visited[nidx] and pixels[nx, ny][3] >= 20:
                            visited[nidx] = 1
                            queue.append((nx, ny))
            components.append((len(cells), cells))

    if len(components) <= 1:
        return rgba
    largest = max(size for size, _ in components)
    # Keep the main body plus any substantial accessory parts (>= 1.5% of main body)
    threshold = max(40, int(largest * 0.015))
    for size, cells in components:
        if size >= threshold:
            continue
        for cidx in cells:
            cy, cx = divmod(cidx, w)
            pixels[cx, cy] = (0, 0, 0, 0)
    return rgba


def key_out_green_screen(
    image: Image.Image,
    *,
    hard_excess: float = 34.0,
    soft_excess: float = 14.0,
    strip_watermarks: bool = True,
) -> Image.Image:
    """Gradient-proof green-screen chroma key with edge despill and corner watermark removal."""
    rgba = image.convert("RGBA")
    w, h = rgba.size
    if w == 0 or h == 0:
        return rgba
    pixels = rgba.load()

    # Clear subtle corner watermark zones first if they are low-contrast green-tinted badges,
    # then run green-dominance keying across the entire frame.
    hard = max(2.0, float(hard_excess))
    soft = max(0.0, min(hard - 1.0, float(soft_excess)))
    span = max(1.0, hard - soft)

    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            max_rb = r if r >= b else b
            excess = g - max_rb
            if excess >= hard and g >= 60:
                pixels[x, y] = (0, 0, 0, 0)
            elif excess > soft and g >= 50:
                # Smooth edge feather + green spill neutralization (clamp G toward max(R, B))
                ratio = 1.0 - (excess - soft) / span
                new_a = int(round(a * ratio))
                if new_a < 18:
                    pixels[x, y] = (0, 0, 0, 0)
                else:
                    despilled_g = int(round(max_rb + (g - max_rb) * max(0.0, ratio * 0.35)))
                    pixels[x, y] = (r, min(255, despilled_g), b, new_a)
            elif excess > 6 and g >= 50:
                # Subtle despill on opaque fringe pixels next to green screen
                despilled_g = max_rb + int((g - max_rb) * 0.5)
                pixels[x, y] = (r, despilled_g, b, a)

    if strip_watermarks:
        rgba = _remove_detached_corner_watermarks(rgba)
    return rgba


def _find_ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    local_app = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
    if local_app.is_dir():
        for candidate in local_app.rglob("ffmpeg.exe"):
            if candidate.is_file():
                return str(candidate)
    return None


def extract_frames_from_video(
    video_path: Path,
    fps: float,
    work_dir: Path,
    *,
    start_sec: float | None = None,
    end_sec: float | None = None,
) -> list[Path]:
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(
            "未检测到 ffmpeg。请安装 ffmpeg（例如 winget install Gyan.FFmpeg），或先将视频导出为 PNG 帧目录后再运行本脚本。"
        )
    out_pattern = work_dir / "frame_%05d.png"
    command = [ffmpeg, "-y"]
    if start_sec is not None and start_sec > 0:
        command.extend(["-ss", f"{start_sec:.3f}"])
    if end_sec is not None and end_sec > 0:
        command.extend(["-to", f"{end_sec:.3f}"])
    command.extend([
        "-i",
        str(video_path),
        "-vf",
        f"fps={fps}",
        str(out_pattern),
    ])
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 抽帧失败：{result.stderr[-400:]}")
    return sorted(work_dir.glob("frame_*.png"))


def load_source_frames(
    source: Path,
    *,
    fps: float,
    temp_dir: Path,
    start_sec: float | None = None,
    end_sec: float | None = None,
) -> list[Image.Image]:
    if source.is_dir():
        paths = sorted(
            p for p in source.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
        )
        if not paths:
            raise ValueError(f"目录中没有找到图片帧：{source}")
        return [Image.open(p).convert("RGBA") for p in paths]

    if not source.is_file():
        raise FileNotFoundError(f"输入路径不存在：{source}")

    suffix = source.suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        frame_paths = extract_frames_from_video(
            source,
            fps,
            temp_dir,
            start_sec=start_sec,
            end_sec=end_sec,
        )
        return [Image.open(p).convert("RGBA") for p in frame_paths]

    if suffix in IMAGE_SUFFIXES:
        with Image.open(source) as img:
            n_frames = max(1, int(getattr(img, "n_frames", 1)))
            result: list[Image.Image] = []
            for idx in range(n_frames):
                img.seek(idx)
                result.append(img.convert("RGBA").copy())
            return result

    raise ValueError(f"不支持的输入格式：{source.suffix}")


def save_transparent_webp(
    frames: list[Image.Image],
    output_path: Path,
    *,
    fps: float = 24.0,
    quality: int = 86,
    lossless: bool = False,
) -> Path:
    if not frames:
        raise ValueError("没有可保存的帧。")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration_ms = max(16, int(round(1000.0 / max(1.0, fps))))
    if len(frames) == 1:
        frames[0].save(
            output_path,
            format="WEBP",
            lossless=lossless,
            quality=quality,
            method=4,
        )
    else:
        frames[0].save(
            output_path,
            format="WEBP",
            save_all=True,
            append_images=frames[1:],
            duration=duration_ms,
            loop=0,
            disposal=2,
            background=(0, 0, 0, 0),
            lossless=lossless,
            quality=quality,
            method=4,
        )
    return output_path


def register_in_manifest(
    pack_dir: Path,
    *,
    action: str,
    relative_file: str,
    fps: float,
    pack_name: str | None = None,
    loop: bool | None = None,
) -> Path:
    manifest_path = pack_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "id": pack_dir.name,
            "name": pack_name or pack_dir.name,
            "default_height": 290,
            "actions": {},
        }
    if pack_name:
        manifest["name"] = pack_name
    actions = manifest.setdefault("actions", {})
    existing = actions.get(action, [])
    if not isinstance(existing, list):
        existing = [existing]
    entry: dict[str, object] = {"file": relative_file, "fps": round(fps, 2)}
    if loop is not None:
        entry["loop"] = loop
    existing = [
        item for item in existing
        if not (isinstance(item, dict) and item.get("file") == relative_file)
    ]
    existing.append(entry)
    actions[action] = existing
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def apply_loop_mode(
    frames: list[Image.Image],
    *,
    loop_mode: str = "none",
    crossfade_frames: int = 8,
) -> list[Image.Image]:
    """Make single-first-frame clips loop smoothly without jump cuts."""
    if len(frames) <= 2 or loop_mode == "none":
        return frames
    if loop_mode == "pingpong":
        # Forward 0..N-1 then reverse N-2..1 so frame 0 connects seamlessly.
        return list(frames) + [frame.copy() for frame in reversed(frames[1:-1])]
    if loop_mode == "crossfade":
        blend_n = max(1, min(int(crossfade_frames), len(frames) // 3))
        body = list(frames[blend_n:-blend_n])
        blended: list[Image.Image] = []
        tail = frames[-blend_n:]
        head = frames[:blend_n]
        for idx in range(blend_n):
            alpha = (idx + 1) / float(blend_n + 1)
            blended.append(Image.blend(tail[idx], head[idx], alpha))
        return body + blended
    raise ValueError(f"不支持的循环模式：{loop_mode}")


def sharpen_rgba(image: Image.Image) -> Image.Image:
    """Gently sharpen anime lineart on RGB channels while preserving clean alpha."""
    rgba = image.convert("RGBA")
    r, g, b, a = rgba.split()
    rgb = Image.merge("RGB", (r, g, b)).filter(
        ImageFilter.UnsharpMask(radius=1.1, percent=120, threshold=3)
    )
    sr, sg, sb = rgb.split()
    return Image.merge("RGBA", (sr, sg, sb, a))


def convert_to_pet_webp(
    source: Path,
    output_path: Path,
    *,
    fps: float = 24.0,
    canvas_size: int = 512,
    bg_color: tuple[int, int, int] | None = None,
    green_screen: bool = False,
    skip_keying: bool = False,
    tolerance: float = 42.0,
    feather: float = 28.0,
    quality: int = 86,
    lossless: bool = False,
    sharpen: bool = False,
    loop_mode: str = "none",
    crossfade_frames: int = 8,
    start_sec: float | None = None,
    end_sec: float | None = None,
) -> tuple[Path, int]:
    with tempfile.TemporaryDirectory() as tmp:
        raw_frames = load_source_frames(
            source,
            fps=fps,
            temp_dir=Path(tmp),
            start_sec=start_sec,
            end_sec=end_sec,
        )
    if skip_keying:
        processed = raw_frames
    elif green_screen:
        processed = [key_out_green_screen(frame) for frame in raw_frames]
    else:
        processed = [
            key_out_solid_background(
                frame,
                bg_color=bg_color,
                tolerance=tolerance,
                feather=feather,
            )
            for frame in raw_frames
        ]
    aligned = align_frames_to_canvas(processed, canvas_size=canvas_size)
    if sharpen:
        aligned = [sharpen_rgba(frame) for frame in aligned]
    looped = apply_loop_mode(
        aligned,
        loop_mode=loop_mode,
        crossfade_frames=crossfade_frames,
    )
    save_transparent_webp(
        looped,
        output_path,
        fps=fps,
        quality=quality,
        lossless=lossless,
    )
    return output_path, len(looped)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "-i", type=Path, required=True, help="输入视频、图片帧目录或单张图片")
    parser.add_argument("--output", "-o", type=Path, help="输出透明 WebP 路径")
    parser.add_argument("--pack-dir", type=Path, help="直接写入并注册到指定角色包目录")
    parser.add_argument("--action", type=str, default="idle", help="动作类型（idle/variant/walk/drag/poke/remind/think/sleep）")
    parser.add_argument("--pack-name", type=str, help="角色包显示名称")
    parser.add_argument("--fps", type=float, default=24.0, help="目标帧率，默认 24")
    parser.add_argument("--size", type=int, default=512, help="输出透明画布边长（512 或 720）")
    parser.add_argument("--bg-color", type=str, help="纯色背景十六进制，如 #C9E2F5 或 #00FF00（默认自动采样四角）")
    parser.add_argument("--green-screen", action="store_true", help="使用抗明暗渐变绿幕色度抠图并自动去除角落水印")
    parser.add_argument("--tolerance", type=float, default=42.0, help="背景抠图容差，默认 42")
    parser.add_argument("--feather", type=float, default=28.0, help="边缘羽化宽度，默认 28")
    parser.add_argument("--no-key", action="store_true", help="源素材已透明，跳过纯色抠图")
    parser.add_argument("--quality", type=int, default=86, help="WebP 质量 1–100，默认 86")
    parser.add_argument("--lossless", action="store_true", help="使用无损透明 WebP")
    parser.add_argument("--sharpen", action="store_true", help="对动漫线条做轻微锐化（抵消 MP4 压缩发虚）")
    parser.add_argument("--oneshot", action="store_true", help="标记为单次播放动作（不循环）")
    parser.add_argument("--start-sec", type=float, help="裁剪起始秒数")
    parser.add_argument("--end-sec", type=float, help="裁剪结束秒数")
    parser.add_argument(
        "--loop-mode",
        choices=("none", "pingpong", "crossfade"),
        default="none",
        help="单首帧视频无缝循环处理：none（原样）、pingpong（往返无缝）、crossfade（首尾融合）",
    )
    parser.add_argument("--crossfade-frames", type=int, default=8, help="crossfade 模式融合帧数，默认 8")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.output is None and args.pack_dir is None:
        parser.error("必须指定 --output 或 --pack-dir 之一。")

    output_path = args.output or (args.pack_dir / f"{args.action}_{args.input.stem}.webp")
    bg_color = parse_hex_color(args.bg_color) if args.bg_color else None
    saved, count = convert_to_pet_webp(
        args.input,
        output_path,
        fps=args.fps,
        canvas_size=args.size,
        bg_color=bg_color,
        green_screen=args.green_screen,
        skip_keying=args.no_key,
        tolerance=args.tolerance,
        feather=args.feather,
        quality=args.quality,
        lossless=args.lossless,
        sharpen=args.sharpen,
        loop_mode=args.loop_mode,
        crossfade_frames=args.crossfade_frames,
        start_sec=args.start_sec,
        end_sec=args.end_sec,
    )
    print(f"已生成透明 WebP：{saved}（{count} 帧 @ {args.fps:g} fps，{args.size}x{args.size}）")

    if args.pack_dir is not None:
        rel = saved.resolve().relative_to(args.pack_dir.resolve()).as_posix()
        manifest = register_in_manifest(
            args.pack_dir,
            action=args.action,
            relative_file=rel,
            fps=args.fps,
            pack_name=args.pack_name,
            loop=False if args.oneshot else None,
        )
        print(f"已更新角色包清单：{manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
