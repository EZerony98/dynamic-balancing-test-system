from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui.theme import BORDER, MUTED, RED, SURFACE, TEXT


class MetricCard(QFrame):
    def __init__(self, title: str, value: str = "--", unit: str = "", accent: str = RED):
        super().__init__()
        self.setObjectName("Card")
        self.setMinimumHeight(104)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        title_label = QLabel(title)
        title_label.setObjectName("Hint")
        row = QHBoxLayout()
        self.value = QLabel(value)
        self.value.setObjectName("MetricValue")
        self.value.setStyleSheet(f"color:{accent}")
        self.unit = QLabel(unit)
        self.unit.setObjectName("MetricUnit")
        row.addWidget(self.value)
        row.addWidget(self.unit, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch()
        layout.addWidget(title_label)
        layout.addLayout(row)

    def set_value(self, value: str, unit: str | None = None) -> None:
        self.value.setText(value)
        if unit is not None:
            self.unit.setText(unit)


class StatusPill(QLabel):
    COLORS = {
        "ready": ("#eef8f3", "#138a5b"),
        "warning": ("#fff7e8", "#a76100"),
        "danger": ("#fff0f2", RED),
        "neutral": ("#f1f3f5", "#59606b"),
    }

    def __init__(self, text: str, state: str = "neutral"):
        super().__init__(text)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_state(state)

    def set_state(self, state: str, text: str | None = None) -> None:
        bg, fg = self.COLORS.get(state, self.COLORS["neutral"])
        if text is not None:
            self.setText(text)
        self.setStyleSheet(
            f"background:{bg};color:{fg};border:1px solid {fg}33;"
            "border-radius:10px;padding:4px 10px;font-weight:650;"
        )


class PolarBalanceWidget(QWidget):
    def __init__(self, title: str, color: str = RED):
        super().__init__()
        self.title = title
        self.color = QColor(color)
        self.amplitude: float | None = None
        self.angle: float | None = None
        self.unit = "mV"
        self.setMinimumSize(280, 280)

    def set_vector(self, amplitude: float | None, angle: float | None, unit: str = "mV") -> None:
        self.amplitude, self.angle, self.unit = amplitude, angle, unit
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(SURFACE))
        margin = 42
        size = min(self.width(), self.height()) - margin * 2
        center = QPointF(self.width() / 2, self.height() / 2 + 8)
        radius = max(30.0, size / 2)
        painter.setPen(QPen(QColor(BORDER), 1))
        for ratio in (0.25, 0.5, 0.75, 1.0):
            r = radius * ratio
            painter.drawEllipse(center, r, r)
        for angle in range(0, 360, 45):
            a = math.radians(angle)
            end = QPointF(center.x() + radius * math.cos(a), center.y() - radius * math.sin(a))
            painter.drawLine(center, end)
        painter.setFont(QFont("PingFang SC", 10))
        painter.setPen(QColor(MUTED))
        for text, dx, dy in (("0°", radius + 12, 4), ("90°", -14, -radius - 10),
                             ("180°", -radius - 42, 4), ("270°", -18, radius + 22)):
            painter.drawText(QPointF(center.x() + dx, center.y() + dy), text)
        painter.setFont(QFont("PingFang SC", 13, QFont.Weight.DemiBold))
        painter.setPen(QColor(TEXT))
        painter.drawText(QRectF(0, 8, self.width(), 24), Qt.AlignmentFlag.AlignCenter, self.title)
        if self.amplitude is None or self.angle is None:
            painter.setPen(QColor(MUTED))
            painter.drawText(QRectF(0, center.y() - 12, self.width(), 24), Qt.AlignmentFlag.AlignCenter, "等待有效 1X / 标定")
            return
        a = math.radians(self.angle)
        length = radius * 0.82
        tip = QPointF(center.x() + length * math.cos(a), center.y() - length * math.sin(a))
        painter.setPen(QPen(self.color, 3))
        painter.drawLine(center, tip)
        head = QPolygonF()
        for delta in (math.radians(155), math.radians(205)):
            head.append(QPointF(tip.x() + 12 * math.cos(a + delta), tip.y() - 12 * math.sin(a + delta)))
        head.append(tip)
        painter.setBrush(self.color)
        painter.drawPolygon(head)
        painter.setPen(self.color)
        painter.setFont(QFont("PingFang SC", 12, QFont.Weight.DemiBold))
        painter.drawText(QRectF(0, self.height() - 30, self.width(), 24), Qt.AlignmentFlag.AlignCenter,
                         f"{self.amplitude:.3f} {self.unit}  /  {self.angle:.1f}°")
