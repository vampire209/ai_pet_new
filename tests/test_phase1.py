"""Phase 1 unit and offscreen GUI smoke tests."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication

from animation_manager import AnimationManager
from animation_state import AnimationStateController
from config import AppConfig
from pet_window import PetWindow
from todo_manager import TodoManager


class Phase1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["phase1-tests"])
        cls.config = AppConfig.load()

    def test_assets_are_complete_alpha_png_sequences(self) -> None:
        expected = {"idle": 8, "move": 6, "remind": 8}
        for name, count in expected.items():
            paths = sorted((self.config.animations_dir / name).glob("frame_*.png"))
            self.assertEqual(len(paths), count)
            for path in paths:
                pixmap = QPixmap(str(path))
                self.assertFalse(pixmap.isNull(), path.name)
                self.assertTrue(pixmap.hasAlphaChannel(), path.name)
                self.assertEqual((pixmap.width(), pixmap.height()), (1254, 1254))

    def test_animation_preload_same_state_and_idle_fallback(self) -> None:
        manager = AnimationManager(self.config)
        self.assertEqual(manager.available_animations, ("idle", "move", "remind"))
        self.assertEqual(manager.frame_count("idle"), 8)
        self.assertEqual(manager.frame_count("move"), 6)
        self.assertEqual(manager.frame_count("remind"), 8)

        self.assertTrue(manager.play_animation("idle"))
        manager._advance_frame()
        current_index = manager.current_frame_index
        self.assertTrue(manager.play_animation("idle"))
        self.assertEqual(manager.current_frame_index, current_index)

        self.assertFalse(manager.play_animation("wait"))
        self.assertEqual(manager.current_animation, "idle")
        self.assertEqual(manager.current_frame_index, current_index)
        manager.stop()

    def test_state_priority_is_centralized(self) -> None:
        manager = AnimationManager(self.config)
        controller = AnimationStateController(manager)
        controller.set_waiting(True)
        self.assertEqual(controller.current_state, "wait")
        self.assertEqual(manager.current_animation, "idle")

        controller.set_moving(True)
        self.assertEqual(controller.current_state, "move")
        self.assertEqual(manager.current_animation, "move")

        controller.set_reminding(True)
        self.assertEqual(controller.current_state, "remind")
        self.assertEqual(manager.current_animation, "remind")

        controller.set_reminding(False)
        self.assertEqual(controller.current_state, "move")
        self.assertEqual(manager.current_animation, "move")
        manager.stop()

    def test_window_flags_size_and_real_drag_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            window = PetWindow(self.config, manager)
            self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
            self.assertTrue(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
            self.assertTrue(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            self.assertEqual(window.height(), self.config.pet_height)
            self.assertEqual(window.animation_state, "idle")

            origin = QPointF(500, 500)
            press = QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(100, 100),
                origin,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            window.mousePressEvent(press)
            self.assertEqual(window.animation_state, "idle")

            threshold = QApplication.styleHints().startDragDistance()
            move_global = QPointF(500 + threshold + 5, 500)
            move = QMouseEvent(
                QEvent.Type.MouseMove,
                QPointF(100 + threshold + 5, 100),
                move_global,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            window.mouseMoveEvent(move)
            self.assertEqual(window.animation_state, "move")
            self.assertEqual(window.current_animation, "move")

            release = QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                QPointF(100 + threshold + 5, 100),
                move_global,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            window.mouseReleaseEvent(release)
            self.assertEqual(window.animation_state, "idle")
            self.assertEqual(window.current_animation, "idle")
            window.close()


if __name__ == "__main__":
    unittest.main()
