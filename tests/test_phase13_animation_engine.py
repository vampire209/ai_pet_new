"""Phase 13 tests: 24fps WebP StreamPlayer, CharacterPack, 60fps physics, wander, fullscreen yield, and WebP builder."""

from __future__ import annotations

import json
import os
from pathlib import Path
import random
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image, ImageDraw
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from animation_manager import AnimationManager
from animation_state import AnimationStateController
from character_pack import (
    DEFAULT_PACK_ID,
    discover_character_packs,
    load_character_pack,
)
from config import AppConfig
from pet_motion import PetMotionEngine, WanderController
from pet_state import PetStateStore, PetWindowState
from pet_window import PetWindow
from scripts.build_pet_webp import convert_to_pet_webp, register_in_manifest
from stream_player import StreamPlayer
from todo_manager import TodoManager
from video_yield import FullscreenYieldWatcher, is_rect_fullscreen


def make_animated_webp(path: Path, *, frames: int = 5, duration_ms: int = 42, size: int = 96) -> Path:
    images: list[Image.Image] = []
    for i in range(frames):
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((12 + i * 3, 18, 62 + i * 3, 86), fill=(240, 120, 40, 255))
        images.append(img)
    images[0].save(
        path,
        format="WEBP",
        save_all=True,
        append_images=images[1:],
        duration=duration_ms,
        loop=0,
        disposal=2,
        background=(0, 0, 0, 0),
    )
    return path


class Phase13AnimationEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["phase13-tests"])
        cls.config = AppConfig.load()

    def test_01_character_pack_manifest_loading_and_traversal_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pack_dir = root / "claude_demo"
            pack_dir.mkdir()
            make_animated_webp(pack_dir / "idle.webp")
            make_animated_webp(pack_dir / "poke.webp", frames=3)
            manifest = {
                "id": "claude_demo",
                "name": "克劳德演示",
                "default_height": 280,
                "actions": {
                    "idle": {"file": "idle.webp", "fps": 24, "loop": True},
                    "poke": {"file": "poke.webp", "fps": 24, "loop": False},
                    "move": {"file": "../outside.webp", "fps": 24},
                },
            }
            (pack_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )

            loaded = load_character_pack(pack_dir)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.pack_id, "claude_demo")
            self.assertEqual(loaded.name, "克劳德演示")
            self.assertEqual(loaded.default_height, 280)
            self.assertEqual(loaded.resolve_action("idle"), ("idle", True))
            self.assertEqual(loaded.resolve_action("poke"), ("poke", True))
            # Path traversal entry was ignored; move falls back safely to idle
            self.assertEqual(loaded.resolve_action("move"), ("idle", False))
            self.assertEqual(loaded.resolve_action("walk"), ("idle", False))

            packs = discover_character_packs(
                animations_dir=self.config.animations_dir,
                builtin_characters_dir=root / "none",
                user_characters_dir=root,
            )
            pack_ids = [p.pack_id for p in packs]
            self.assertIn(DEFAULT_PACK_ID, pack_ids)
            self.assertIn("claude_demo", pack_ids)

    def test_02_stream_player_decodes_24fps_webp_oneshot_and_loop_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            webp_path = make_animated_webp(Path(temp_dir) / "clip.webp", frames=4, duration_ms=40)
            player = StreamPlayer()
            first = player.start(
                webp_path,
                target_pixel_height=120,
                dpr=1.5,
                fps=24.0,
                loop=False,
                cache_loop=True,
            )
            self.assertFalse(first.isNull())
            self.assertEqual(round(first.deviceIndependentSize().height()), 80)

            # Wait briefly for worker thread to fill queue and step through all frames
            seen_frames = 1
            ended = False
            clock = time.perf_counter()
            for _ in range(40):
                time.sleep(0.015)
                clock += 0.05
                pixmap, is_end = player.advance(speed=1.0, now=clock)
                if pixmap is not None:
                    seen_frames += 1
                if is_end:
                    ended = True
                    break
            self.assertTrue(ended)
            self.assertGreaterEqual(seen_frames, 2)
            player.close()

    def test_03_animation_manager_hot_switch_and_oneshot_returns_to_idle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pack_dir = Path(temp_dir) / "pack_a"
            pack_dir.mkdir()
            make_animated_webp(pack_dir / "idle.webp", frames=3, duration_ms=30)
            make_animated_webp(pack_dir / "poke.webp", frames=3, duration_ms=25)
            (pack_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "id": "pack_a",
                        "name": "测试角色包",
                        "actions": {
                            "idle": {"file": "idle.webp", "fps": 24, "loop": True},
                            "poke": {"file": "poke.webp", "fps": 24, "loop": False},
                        },
                    }
                ),
                encoding="utf-8",
            )
            pack = load_character_pack(pack_dir)
            manager = AnimationManager(self.config)
            controller = AnimationStateController(manager)
            self.assertTrue(manager.load_character_pack(pack))
            self.assertEqual(manager.character_pack.pack_id, "pack_a")

            controller.trigger_oneshot("poke")
            self.assertEqual(controller.current_state, "poke")
            self.assertEqual(manager.current_animation, "poke")

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and controller.current_state == "poke":
                self.app.processEvents()
                time.sleep(0.015)
            self.assertEqual(controller.current_state, "idle")
            self.assertEqual(manager.current_animation, "idle")
            manager.stop()

    def test_04_pet_motion_engine_breathing_poke_bounce_and_drag_sway(self) -> None:
        engine = PetMotionEngine(start_time=0.0)
        idle_s = engine.sample(0.65)
        self.assertGreater(idle_s.scale_y, 1.0)
        self.assertLess(idle_s.scale_x, 1.0)
        self.assertFalse(engine.needs_high_fps(0.65))

        engine.trigger_poke(1.0)
        self.assertTrue(engine.needs_high_fps(1.08))
        poked = engine.sample(1.08)
        self.assertGreater(abs(poked.scale_x - 1.0), 0.015)
        self.assertFalse(engine.needs_high_fps(2.0))

        engine.begin_drag(2.0)
        self.assertTrue(engine.needs_high_fps(2.01))
        for _ in range(10):
            engine.update_drag(dx=40.0, dt=0.016)
        dragging = engine.sample(2.1)
        self.assertLess(dragging.rotation_deg, -2.0)
        self.assertGreaterEqual(dragging.rotation_deg, -engine.MAX_SWAY_DEG)

        engine.end_drag(2.2)
        self.assertTrue(engine.needs_high_fps(2.25))
        settled = engine.sample(3.0)
        self.assertAlmostEqual(settled.rotation_deg, 0.0, places=3)

    def test_05_wander_controller_strolls_within_bounds_and_flips_facing(self) -> None:
        controller = WanderController(
            enabled=True,
            min_wait_s=1.0,
            max_wait_s=1.0,
            rng=random.Random(7),
        )
        controller.start_walk_to(current_x=500, target_x=350, now=10.0)
        self.assertTrue(controller.walking)
        self.assertEqual(controller.facing, -1)

        x = 500
        now = 10.0
        arrived = False
        for _ in range(200):
            now += 0.03
            step = controller.tick(
                now=now,
                current_x=x,
                min_x=100,
                max_x=900,
                allowed=True,
            )
            self.assertIsNotNone(step)
            x = step.x
            self.assertGreaterEqual(x, 100)
            self.assertLessEqual(x, 900)
            if step.arrived:
                arrived = True
                break
        self.assertTrue(arrived)
        self.assertEqual(x, 350)
        self.assertFalse(controller.walking)

    def test_06_fullscreen_yield_drops_and_restores_topmost(self) -> None:
        self.assertTrue(is_rect_fullscreen((0, 0, 1920, 1080), (0, 0, 1920, 1080)))
        self.assertFalse(is_rect_fullscreen((100, 100, 1200, 800), (0, 0, 1920, 1080)))

        fullscreen_state = {"active": False}
        watcher = FullscreenYieldWatcher(
            hwnd_provider=lambda: 123,
            detector=lambda _hwnd: fullscreen_state["active"],
        )
        seen: list[bool] = []
        watcher.fullscreen_changed.connect(seen.append)
        watcher.check_now()
        fullscreen_state["active"] = True
        watcher.check_now()
        fullscreen_state["active"] = False
        watcher.check_now()
        self.assertEqual(seen, [True, False])

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = PetStateStore(root / "pet_state.json")
            pet = PetWindow(
                self.config,
                TodoManager(root / "tasks.json"),
                state_store=store,
                enable_fullscreen_watcher=False,
            )
            self.assertTrue(bool(pet.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
            pet._on_fullscreen_changed(True)
            self.assertTrue(pet.fullscreen_yielded)
            self.assertFalse(bool(pet.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
            pet._on_fullscreen_changed(False)
            self.assertFalse(pet.fullscreen_yielded)
            self.assertTrue(bool(pet.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
            pet.close()

    def test_07_pet_window_poke_click_and_pack_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pack_dir = root / "custom_pet"
            pack_dir.mkdir()
            make_animated_webp(pack_dir / "idle.webp", frames=3, duration_ms=40)
            (pack_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "id": "custom_pet",
                        "name": "自定义角色",
                        "actions": {"idle": {"file": "idle.webp", "fps": 24}},
                    }
                ),
                encoding="utf-8",
            )
            pack = load_character_pack(pack_dir)
            store = PetStateStore(root / "pet_state.json")
            pet = PetWindow(
                self.config,
                TodoManager(root / "tasks.json"),
                state_store=store,
                character_packs=(pack,),
                enable_fullscreen_watcher=False,
            )
            pokes: list[bool] = []
            pet.pet_poked.connect(lambda: pokes.append(True))

            pos = QPointF(200, 200)
            pet.mousePressEvent(
                QMouseEvent(
                    QEvent.Type.MouseButtonPress,
                    QPointF(50, 50),
                    pos,
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                )
            )
            pet.mouseReleaseEvent(
                QMouseEvent(
                    QEvent.Type.MouseButtonRelease,
                    QPointF(50, 50),
                    pos,
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                )
            )
            self.assertEqual(len(pokes), 1)

            pet.set_wandering_enabled(False)
            pet.set_fullscreen_yield_enabled(False)
            self.assertTrue(pet.select_character_pack("custom_pet"))
            saved = store.load()
            self.assertFalse(saved.wandering_enabled)
            self.assertFalse(saved.fullscreen_yield_enabled)
            self.assertEqual(saved.character_pack, "custom_pet")
            pet.close()

    def test_08_build_pet_webp_keys_solid_background_aligns_and_registers_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frames_dir = root / "raw_frames"
            frames_dir.mkdir()
            bg = (201, 226, 245)  # #C9E2F5 sky-blue
            for idx in range(3):
                img = Image.new("RGB", (200, 200), bg)
                draw = ImageDraw.Draw(img)
                # Warm orange circle + cream rectangle in the center
                draw.ellipse((60, 30 + idx * 2, 140, 110 + idx * 2), fill=(235, 118, 45))
                draw.rectangle((70, 110, 130, 180), fill=(248, 240, 225))
                img.save(frames_dir / f"frame_{idx:03d}.png")

            pack_dir = root / "claude_chan"
            pack_dir.mkdir()
            out_webp = pack_dir / "idle.webp"
            saved_path, frame_count = convert_to_pet_webp(
                frames_dir,
                out_webp,
                fps=24.0,
                canvas_size=256,
            )
            self.assertEqual(frame_count, 3)
            self.assertTrue(saved_path.is_file())

            with Image.open(saved_path) as result:
                self.assertEqual(result.size, (256, 256))
                self.assertEqual(getattr(result, "n_frames", 1), 3)
                rgba = result.convert("RGBA")
                # Outer border is keyed to transparent
                self.assertEqual(rgba.getpixel((2, 2))[3], 0)
                # Center character pixels stay opaque
                self.assertEqual(rgba.getpixel((128, 128))[3], 255)

            manifest_path = register_in_manifest(
                pack_dir,
                action="idle",
                relative_file="idle.webp",
                fps=24.0,
                pack_name="克劳德大小姐",
            )
            self.assertTrue(manifest_path.is_file())
            loaded = load_character_pack(pack_dir)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.name, "克劳德大小姐")


if __name__ == "__main__":
    unittest.main()
