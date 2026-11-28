"""Floating dynamic-island status pill anchored directly above the desktop pet."""

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
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QWidget

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
    pet_name: str = "桌宠",
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

    # Priority 1: Live reminder active
    if active_reminder_count > 0:
        return StatusPillSnapshot(
            status_text=f"提醒中 · {active_reminder_count}项",
            dot_color="#FF9B42",
            progress_ratio=ratio,
            progress_label=progress_label,
            tooltip="有正在提醒的待办事项（点击查看待办）",
        )

    # Priority 2: Transient companion activity (thinking / watching / poking)
    if activity_label and activity_label.strip():
        return StatusPillSnapshot(
            status_text=_truncate_text(activity_label.strip(), 11),
            dot_color="#6BE0B8",
            progress_ratio=ratio,
            progress_label=progress_label,
            tooltip=f"{pet_name}当前状态：{activity_label.strip()}",
        )

    # Priority 3: Upcoming scheduled pending task
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
            tooltip=f"有 {len(overdue)} 项已到期待办：{first.text}（点击查看）",
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
        tooltip="所有待办已清空！左圆按钮快捷提问，中胶囊看待办，右环打开聊天",
    )


class PetStatusPill(QWidget):
    """Frameless, non-activating 3-part floating island above the desktop pet.

    Visual layout:
        [ 💬 Quick Ask Circle ]  [ ● Status Capsule ]  [ 100% Ring Gauge ]
    """

    quick_ask_requested = Signal()
    todo_requested = Signal()
    chat_requested = Signal()

    _HEIGHT = 36
    _CIRCLE_SIZE = 32
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
            tooltip="左按钮快速提问 · 中胶囊看待办 · 右进度环开聊天",
        )
        self._hovered_part: str | None = None
        self._topmost = True

        self.setObjectName("petStatusPill")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self._apply_window_flags(topmost=True)
        self._recompute_layout()

    def _apply_window_flags(self, *, topmost: bool) -> None:
        self._topmost = bool(topmost)
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if self._topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        was_visible = self.isVisible()
        pos = self.pos()
        self.setWindowFlags(flags)
        if was_visible:
            self.show()
            self.move(pos)

    def set_topmost(self, topmost: bool) -> None:
        if bool(topmost) == self._topmost:
            return
        self._apply_window_flags(topmost=bool(topmost))

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
        capsule_w = max(106, min(176, text_w + 40))

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
        part = self._hit_part(event.position().toPoint())
        if part != self._hovered_part:
            self._hovered_part = part
            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if part is not None
                else Qt.CursorShape.ArrowCursor
            )
            if part == "left":
                self.setToolTip("问她一句（快捷气泡提问）")
            elif part == "right":
                self.setToolTip(f"待办完成度 {self._snapshot.progress_label}（点击打开 AI 聊天）")
            else:
                self.setToolTip(self._snapshot.tooltip)
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
        part = self._hit_part(event.position().toPoint())
        if part == "left":
            self.quick_ask_requested.emit()
        elif part == "center":
            self.todo_requested.emit()
        elif part == "right":
            self.chat_requested.emit()
        event.accept()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 1. Left circle button: warm frosted ivory circle with chat-bubble glyph
        left_bg = QColor(255, 249, 242, 244) if self._hovered_part != "left" else QColor(255, 237, 222, 252)
        painter.setPen(QPen(QColor(224, 122, 66, 175), 1.3))
        painter.setBrush(left_bg)
        painter.drawEllipse(QRectF(self._left_rect).adjusted(1, 1, -1, -1))
        # Tiny chat bubble icon inside left circle
        cx = self._left_rect.center().x()
        cy = self._left_rect.center().y()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#D96B2F"))
        painter.drawRoundedRect(QRectF(cx - 7.5, cy - 5.5, 15.0, 10.5), 4.0, 4.0)
        painter.setBrush(QColor("#FFFDF9"))
        for dx in (-3.6, 0.0, 3.6):
            painter.drawEllipse(QRectF(cx + dx - 1.1, cy - 1.3, 2.2, 2.2))

        # 2. Center dark frosted capsule: glowing status dot + crisp label
        center_rect = QRectF(self._center_rect).adjusted(0.8, 0.8, -0.8, -0.8)
        capsule_bg = QColor(37, 34, 48, 236) if self._hovered_part != "center" else QColor(52, 46, 66, 244)
        painter.setPen(QPen(QColor(255, 228, 205, 85), 1.1))
        painter.setBrush(capsule_bg)
        radius = center_rect.height() / 2.0
        painter.drawRoundedRect(center_rect, radius, radius)

        # Glowing status dot
        dot_color = QColor(self._snapshot.dot_color)
        dot_cx = center_rect.left() + 14.0
        dot_cy = center_rect.center().y()
        halo = QColor(dot_color)
        halo.setAlpha(72)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(QRectF(dot_cx - 6.0, dot_cy - 6.0, 12.0, 12.0))
        painter.setBrush(dot_color)
        painter.drawEllipse(QRectF(dot_cx - 3.3, dot_cy - 3.3, 6.6, 6.6))

        # Status text
        painter.setFont(self._status_font())
        painter.setPen(QColor("#FAF5EF"))
        text_rect = QRectF(
            center_rect.left() + 24.0,
            center_rect.top(),
            center_rect.width() - 32.0,
            center_rect.height(),
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._snapshot.status_text,
        )

        # 3. Right circular ring gauge: progress arc + percentage
        ring_outer = QRectF(self._right_rect).adjusted(1.0, 1.0, -1.0, -1.0)
        right_bg = QColor(255, 250, 245, 242) if self._hovered_part != "right" else QColor(255, 237, 222, 252)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(right_bg)
        painter.drawEllipse(ring_outer)

        arc_rect = ring_outer.adjusted(2.2, 2.2, -2.2, -2.2)
        track_pen = QPen(QColor(225, 206, 192, 165), 2.8)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(arc_rect, 0, 360 * 16)

        ratio = max(0.0, min(1.0, self._snapshot.progress_ratio))
        if ratio > 0.0:
            arc_pen = QPen(QColor("#E07A42"), 2.8)
            arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(arc_pen)
            # Start from 12 o'clock (90 deg) and sweep clockwise
            painter.drawArc(arc_rect, 90 * 16, int(round(-360 * 16 * ratio)))

        painter.setFont(self._ring_font())
        painter.setPen(QColor("#3A2B24"))
        painter.drawText(
            ring_outer,
            Qt.AlignmentFlag.AlignCenter,
            self._snapshot.progress_label,
        )
        painter.end()
