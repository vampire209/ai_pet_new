"""Floating dynamic-island status pill modeled after White Dragon's HUD in Claude-chan's warm theme."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QApplication, QWidget

from models.task import Task, parse_local_datetime


@dataclass(frozen=True, slots=True)
class StatusPillSnapshot:
    """Display state rendered on the 3-part floating status island."""

    status_text: str
    dot_color: str
    progress_ratio: float
    progress_label: str
    tooltip: str


def _truncate_text(text: str, max_chars: int = 9) -> str:
    clean = " ".join(text.strip().split())
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 1] + "…"


def _format_countdown(seconds: float) -> str:
    minutes = max(1, int(round(seconds / 60.0)))
    if minutes < 60:
        return f"{minutes}分后"
    hours = minutes // 60
    rem = minutes % 60
    if hours < 24:
        return f"{hours}小时后" if rem == 0 else f"{hours}h{rem}m后"
    days = hours // 24
    return f"{days}天后"


def compute_todo_status_snapshot(
    tasks: Sequence[Task],
    *,
    now: datetime | None = None,
    active_reminder_count: int = 0,
    activity_label: str | None = None,
    pet_name: str = "克劳德娘",
) -> StatusPillSnapshot:
    """Summarize task progress, upcoming schedule, or live companion activity."""
    current = now or datetime.now()
    total = len(tasks)
    completed = sum(1 for t in tasks if t.completed)
    pending = [t for t in tasks if not t.completed]

    if total == 0:
        ratio = 1.0
        progress_label = "100%"
    else:
        ratio = max(0.0, min(1.0, completed / float(total)))
        progress_label = f"{int(round(ratio * 100))}%"

    if active_reminder_count > 0:
        return StatusPillSnapshot(
            status_text=f"提醒中 · {active_reminder_count}项",
            dot_color="#FF9B42",
            progress_ratio=ratio,
            progress_label=progress_label,
            tooltip="有正在提醒的待办事项（点击展开控制面板）",
        )

    if activity_label and activity_label.strip():
        return StatusPillSnapshot(
            status_text=_truncate_text(activity_label.strip(), 11),
            dot_color="#6BE0B8",
            progress_ratio=ratio,
            progress_label=progress_label,
            tooltip=f"{pet_name}当前状态：{activity_label.strip()}",
        )

    upcoming: list[tuple[float, Task]] = []
    overdue: list[Task] = []
    for task in pending:
        event_dt = parse_local_datetime(task.event_at)
        if event_dt is None:
            continue
        delta = (event_dt - current).total_seconds()
        if delta < -60:
            overdue.append(task)
        else:
            upcoming.append((max(0.0, delta), task))

    if overdue:
        first = overdue[0]
        return StatusPillSnapshot(
            status_text=f"超时 · {_truncate_text(first.text, 6)}",
            dot_color="#FF6B6B",
            progress_ratio=ratio,
            progress_label=progress_label,
            tooltip=f"有 {len(overdue)} 项已到期待办：{first.text}（点击展开面板）",
        )

    if upcoming:
        upcoming.sort(key=lambda item: item[0])
        delta_sec, next_task = upcoming[0]
        countdown = _format_countdown(delta_sec)
        return StatusPillSnapshot(
            status_text=f"{countdown} · {_truncate_text(next_task.text, 5)}",
            dot_color="#F59E42",
            progress_ratio=ratio,
            progress_label=progress_label,
            tooltip=f"下一项待办（{countdown}）：{next_task.text}",
        )

    if pending:
        return StatusPillSnapshot(
            status_text=f"待办 · 剩{len(pending)}项",
            dot_color="#F5B041",
            progress_ratio=ratio,
            progress_label=progress_label,
            tooltip=f"当前还有 {len(pending)} 项未完成待办（已完成 {completed}/{total}）",
        )

    return StatusPillSnapshot(
        status_text=f"{_truncate_text(pet_name, 5)} · 待命中",
        dot_color="#6CE5B1",
        progress_ratio=1.0,
        progress_label="100%",
        tooltip="左圆打开 Gemini · 中胶囊展开控制面板 · 右进度环快捷提问",
    )


class PetStatusPill(QWidget):
    """Frameless, non-activating 3-part floating island above the desktop pet.

    Visual layout (Claude Warm Parchment × Terracotta × Espresso):
        [ ✦ Gemini Circle ]  [ ● Status / Panel Capsule ]  [ 100% Ring Gauge ]
    """

    gemini_requested = Signal()
    panel_requested = Signal()
    quick_ask_requested = Signal()
    # Compatibility signals kept for clean wiring & tests
    todo_requested = Signal()
    chat_requested = Signal()
    drag_moved = Signal(int, int)  # delta_x, delta_y when user drags the pill
    drag_finished = Signal()

    _HEIGHT = 38
    _CIRCLE_SIZE = 34
    _GAP = 7
    _PAD_X = 4
    _PAD_Y = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._snapshot = StatusPillSnapshot(
            status_text="待命中",
            dot_color="#6CE5B1",
            progress_ratio=1.0,
            progress_label="100%",
            tooltip="左圆打开 Gemini · 中胶囊展开控制面板 · 右进度环快捷提问",
        )
        self._hovered_part: str | None = None
        self._topmost = True
        self._mouse_through = False
        self._press_global: QPoint | None = None
        self._last_global: QPoint | None = None
        self._press_part: str | None = None
        self._dragging = False

        self.setObjectName("petStatusPill")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self._apply_window_flags(topmost=True, mouse_through=False)
        self._recompute_layout()

    def _apply_window_flags(self, *, topmost: bool, mouse_through: bool) -> None:
        self._topmost = bool(topmost)
        self._mouse_through = bool(mouse_through)
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if self._topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        if self._mouse_through:
            flags |= Qt.WindowType.WindowTransparentForInput
        was_visible = self.isVisible()
        pos = self.pos()
        self.setWindowFlags(flags)
        if was_visible:
            self.show()
            self.move(pos)

    def set_topmost(self, topmost: bool) -> None:
        if bool(topmost) == self._topmost:
            return
        self._apply_window_flags(
            topmost=bool(topmost),
            mouse_through=self._mouse_through,
        )

    def set_mouse_through(self, enabled: bool) -> None:
        if bool(enabled) == self._mouse_through:
            return
        self._apply_window_flags(
            topmost=self._topmost,
            mouse_through=bool(enabled),
        )

    @property
    def snapshot(self) -> StatusPillSnapshot:
        return self._snapshot

    def set_snapshot(self, snapshot: StatusPillSnapshot) -> None:
        if snapshot == self._snapshot:
            return
        self._snapshot = snapshot
        self.setToolTip(snapshot.tooltip)
        self._recompute_layout()
        self.update()

    def _status_font(self) -> QFont:
        font = QFont("Microsoft YaHei UI", 9)
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def _ring_font(self) -> QFont:
        font = QFont("Microsoft YaHei UI", 7)
        font.setWeight(QFont.Weight.Bold)
        return font

    def _recompute_layout(self) -> None:
        fm = QFontMetrics(self._status_font())
        text_w = fm.horizontalAdvance(self._snapshot.status_text)
        capsule_w = max(112, min(182, text_w + 42))

        y = self._PAD_Y
        x = self._PAD_X
        self._left_rect = QRect(x, y, self._CIRCLE_SIZE, self._CIRCLE_SIZE)
        x += self._CIRCLE_SIZE + self._GAP
        self._center_rect = QRect(x, y, capsule_w, self._CIRCLE_SIZE)
        x += capsule_w + self._GAP
        self._right_rect = QRect(x, y, self._CIRCLE_SIZE, self._CIRCLE_SIZE)
        total_w = x + self._CIRCLE_SIZE + self._PAD_X
        self.setFixedSize(QSize(total_w, self._HEIGHT))

    def reposition_above(self, pet_rect: QRect, screen_area: QRect | None) -> None:
        center_x = pet_rect.x() + pet_rect.width() // 2
        x = center_x - self.width() // 2
        y = pet_rect.y() - self.height() - 6
        if screen_area is not None:
            x = max(screen_area.left() + 4, min(x, screen_area.right() - self.width() - 4))
            if y < screen_area.top() + 4:
                y = pet_rect.bottom() + 6
            y = max(screen_area.top() + 4, min(y, screen_area.bottom() - self.height() - 4))
        self.move(x, y)

    def _hit_part(self, pos: QPoint) -> str | None:
        if self._left_rect.contains(pos):
            return "left"
        if self._center_rect.contains(pos):
            return "center"
        if self._right_rect.contains(pos):
            return "right"
        return None

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_global is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            current_global = event.globalPosition().toPoint()
            if not self._dragging:
                if (current_global - self._press_global).manhattanLength() >= QApplication.styleHints().startDragDistance():
                    self._dragging = True
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self._dragging and self._last_global is not None:
                delta = current_global - self._last_global
                self._last_global = current_global
                self.drag_moved.emit(delta.x(), delta.y())
            event.accept()
            return

        part = self._hit_part(event.position().toPoint())
        if part != self._hovered_part:
            self._hovered_part = part
            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if part is not None
                else Qt.CursorShape.ArrowCursor
            )
            if part == "left":
                self.setToolTip("打开 Gemini（左键直达，可在控制面板自定义网址）")
            elif part == "right":
                self.setToolTip(f"待办完成度 {self._snapshot.progress_label}（左键问她一句）")
            else:
                self.setToolTip(f"{self._snapshot.tooltip}（点击展开控制面板 / 按住可拖动）")
            self.update()
        event.accept()

    def leaveEvent(self, event) -> None:
        if self._hovered_part is not None:
            self._hovered_part = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._press_global = event.globalPosition().toPoint()
        self._last_global = self._press_global
        self._press_part = self._hit_part(event.position().toPoint())
        self._dragging = False
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        was_dragging = self._dragging
        part = self._press_part
        self._press_global = None
        self._last_global = None
        self._press_part = None
        self._dragging = False
        self.setCursor(Qt.CursorShape.PointingHandCursor if part else Qt.CursorShape.ArrowCursor)
        if was_dragging:
            self.drag_finished.emit()
        else:
            if part == "left":
                self.gemini_requested.emit()
            elif part == "center":
                self.panel_requested.emit()
                self.todo_requested.emit()
            elif part == "right":
                self.quick_ask_requested.emit()
                self.chat_requested.emit()
        event.accept()

    def _draw_gemini_spark(self, painter: QPainter, cx: float, cy: float, r: float) -> None:
        """Draw a crisp 4-point Gemini sparkle emblem in Claude's warm terracotta."""
        path = QPainterPath()
        path.moveTo(cx, cy - r)
        path.quadTo(cx, cy, cx + r, cy)
        path.quadTo(cx, cy, cx, cy + r)
        path.quadTo(cx, cy, cx - r, cy)
        path.quadTo(cx, cy, cx, cy - r)
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#D96B2F"))
        painter.drawPath(path)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 1. Left circle button: warm parchment circle with Gemini 4-point sparkle
        left_bg = (
            QColor(255, 250, 243, 246)
            if self._hovered_part != "left"
            else QColor(255, 236, 218, 252)
        )
        painter.setPen(QPen(QColor(217, 107, 47, 185), 1.3))
        painter.setBrush(left_bg)
        painter.drawEllipse(QRectF(self._left_rect).adjusted(1, 1, -1, -1))
        self._draw_gemini_spark(
            painter,
            float(self._left_rect.center().x()),
            float(self._left_rect.center().y()),
            8.5,
        )

        # 2. Center warm-espresso capsule: glowing status dot + crisp parchment label
        center_rect = QRectF(self._center_rect).adjusted(0.8, 0.8, -0.8, -0.8)
        capsule_bg = (
            QColor(46, 35, 31, 238)
            if self._hovered_part != "center"
            else QColor(64, 48, 42, 246)
        )
        painter.setPen(QPen(QColor(245, 214, 184, 115), 1.15))
        painter.setBrush(capsule_bg)
        radius = center_rect.height() / 2.0
        painter.drawRoundedRect(center_rect, radius, radius)

        dot_color = QColor(self._snapshot.dot_color)
        dot_cx = center_rect.left() + 14.5
        dot_cy = center_rect.center().y()
        halo = QColor(dot_color)
        halo.setAlpha(74)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(QRectF(dot_cx - 6.0, dot_cy - 6.0, 12.0, 12.0))
        painter.setBrush(dot_color)
        painter.drawEllipse(QRectF(dot_cx - 3.3, dot_cy - 3.3, 6.6, 6.6))

        painter.setFont(self._status_font())
        painter.setPen(QColor("#FFF8F0"))
        text_rect = QRectF(
            center_rect.left() + 25.0,
            center_rect.top(),
            center_rect.width() - 33.0,
            center_rect.height(),
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._snapshot.status_text,
        )

        # 3. Right circular ring gauge: warm ivory fill + terracotta progress arc + %
        ring_outer = QRectF(self._right_rect).adjusted(1.0, 1.0, -1.0, -1.0)
        right_bg = (
            QColor(255, 250, 243, 246)
            if self._hovered_part != "right"
            else QColor(255, 236, 218, 252)
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(right_bg)
        painter.drawEllipse(ring_outer)

        arc_rect = ring_outer.adjusted(2.2, 2.2, -2.2, -2.2)
        track_pen = QPen(QColor(232, 210, 192, 175), 2.8)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(arc_rect, 0, 360 * 16)

        ratio = max(0.0, min(1.0, self._snapshot.progress_ratio))
        if ratio > 0.0:
            arc_pen = QPen(QColor("#D96B2F"), 2.8)
            arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(arc_pen)
            painter.drawArc(arc_rect, 90 * 16, int(round(-360 * 16 * ratio)))

        painter.setFont(self._ring_font())
        painter.setPen(QColor("#3A2922"))
        painter.drawText(
            ring_outer,
            Qt.AlignmentFlag.AlignCenter,
            self._snapshot.progress_label,
        )
        painter.end()
