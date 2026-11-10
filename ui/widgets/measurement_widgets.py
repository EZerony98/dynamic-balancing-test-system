"""Reusable widgets for dual-signal plots and balancing data cards."""

from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class PlotPanel(QFrame):
    def __init__(
        self,
        title: str,
        left_label: str,
        bottom_label: str,
        color: str,
        with_metrics: bool = False,
    ) -> None:
        super().__init__()
        self.setObjectName("plotPanel")
        self._curve_color = color
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 6, 9, 8)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("panelTitle")
        self.header_layout = QHBoxLayout()
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.addWidget(self.title_label)
        self.header_layout.addStretch()
        layout.addLayout(self.header_layout)
        self.metrics_label: Optional[QLabel] = None
        self.metrics_detail_label: Optional[QLabel] = None
        if with_metrics:
            self.metrics_label = QLabel(
                "频率 -- Hz   峰值 -- mV   峰峰值 -- mV   RMS -- mV   相位 --°   周期 -- ms"
            )
            self.metrics_label.setWordWrap(True)
            self.metrics_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.metrics_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
            self.metrics_label.setStyleSheet(
                "background:#eef6ff; border:1px solid #bfd8ee; "
                "border-radius:3px; padding:5px 8px; color:#075985; "
                "font-size:13px; font-weight:700;"
            )
            layout.addWidget(self.metrics_label)
            self.metrics_detail_label = QLabel()
            self.metrics_detail_label.setWordWrap(True)
            self.metrics_detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.metrics_detail_label.setSizePolicy(
                QSizePolicy.Preferred, QSizePolicy.Minimum
            )
            self.metrics_detail_label.setStyleSheet(
                "padding:0 6px 2px 6px; color:#475569; font-size:11px;"
            )
            self.metrics_detail_label.hide()
            layout.addWidget(self.metrics_detail_label)
        self.plot = pg.PlotWidget()
        self.plot.setBackground("white")
        self.plot.showGrid(x=True, y=True, alpha=0.22)
        self.plot.setLabel("left", left_label)
        self.plot.setLabel("bottom", bottom_label)
        self.plot.getAxis("left").setPen("#777777")
        self.plot.getAxis("bottom").setPen("#777777")
        self.plot.getAxis("left").setTextPen("#555555")
        self.plot.getAxis("bottom").setTextPen("#555555")
        self.curve = self.plot.plot(pen=pg.mkPen(color, width=1.4))
        self.second_curve = None
        self.frequency_marker: Optional[pg.InfiniteLine] = None
        layout.addWidget(self.plot, 1)

    def set_data(self, x_values: np.ndarray, y_values: np.ndarray) -> None:
        self.curve.setData(x_values, y_values)

    def enable_second_curve(self, color: str = "#c24156") -> None:
        if self.second_curve is None:
            self.second_curve = self.plot.plot(pen=pg.mkPen(color, width=1.4))

    def set_second_data(self, x_values: np.ndarray, y_values: np.ndarray) -> None:
        if self.second_curve is None:
            self.enable_second_curve()
        self.second_curve.setData(x_values, y_values)

    def set_frequency_marker(self, frequency: float, preview: bool = False) -> None:
        if self.frequency_marker is None:
            self.frequency_marker = pg.InfiniteLine(
                angle=90,
                movable=False,
                pen=pg.mkPen("#7c3aed", width=1.5, style=Qt.DashLine),
                label="目标 {value:.2f} Hz",
                labelOpts={"color": "#7c3aed", "position": 0.92},
            )
            self.plot.addItem(self.frequency_marker)
        self.frequency_marker.setValue(float(frequency))
        self.frequency_marker.show()
        # pyqtgraph skips label updates while the line is hidden. Refresh it
        # after showing so the first recovered frame cannot retain old text.
        color = "#b45309" if preview else "#7c3aed"
        self.frequency_marker.setPen(pg.mkPen(color, width=1.5, style=Qt.DashLine))
        self.frequency_marker.label.setColor(color)
        self.frequency_marker.label.setFormat(("预览" if preview else "目标") + " {value:.2f} Hz")

    def clear_frequency_marker(self) -> None:
        if self.frequency_marker is not None:
            self.frequency_marker.hide()

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def add_header_control(self, label: str, widget: QWidget) -> None:
        control_label = QLabel(label)
        control_label.setObjectName("plotControlLabel")
        self.header_layout.addWidget(control_label)
        self.header_layout.addWidget(widget)

    def set_metrics(
        self, text: str, warning: bool = False, details: Optional[str] = None
    ) -> None:
        if self.metrics_label is not None:
            self.metrics_label.setText(text)
            # A numeric summary normally fits on one line.  Waiting/error text
            # has no separate details row, so keep wrapping enabled for it.
            self.metrics_label.setWordWrap(details is None)
            full_text = text if not details else "%s\n%s" % (text, details)
            self.metrics_label.setToolTip(full_text)
            self.metrics_label.setStyleSheet(
                "background:%s; border:1px solid %s; border-radius:3px; "
                "padding:5px 8px; color:%s; font-size:13px; font-weight:700;"
                % (("#fff7ed", "#fdba74", "#9a3412") if warning
                   else ("#eef6ff", "#bfd8ee", "#075985")))
            self.metrics_label.updateGeometry()
        if self.metrics_detail_label is not None:
            if details:
                self.metrics_detail_label.setText(details)
                self.metrics_detail_label.setToolTip(details)
                self.metrics_detail_label.show()
            else:
                self.metrics_detail_label.clear()
                self.metrics_detail_label.setToolTip("")
                self.metrics_detail_label.hide()
            self.metrics_detail_label.updateGeometry()
        self.curve.setPen(pg.mkPen("#b45309" if warning else self._curve_color, width=1.4))


class BalancingDataCard(QFrame):
    def __init__(
        self,
        title: str,
        color: str,
        unit: str,
        with_trend: bool,
        with_phase: bool = False,
    ) -> None:
        super().__init__()
        self.setObjectName("dataCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 7, 11, 7)
        layout.setSpacing(2)
        title_label = self.title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        self.header_layout = QHBoxLayout()
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.addWidget(title_label)
        self.header_layout.addStretch()
        layout.addLayout(self.header_layout)
        value_row = QHBoxLayout()
        self.value_label = QLabel("--")
        self.value_label.setStyleSheet(
            "font-size: 23px; font-weight: 700; color: %s" % color
        )
        unit_label = self.unit_label = QLabel(unit)
        unit_label.setObjectName("unitLabel")
        value_row.addWidget(self.value_label)
        value_row.addWidget(unit_label)
        value_row.addStretch()
        layout.addLayout(value_row)
        self.phase_label: Optional[QLabel] = None
        if with_phase:
            self.phase_label = QLabel("相对速度角度 --")
            self.phase_label.setStyleSheet(
                "font-size: 16px; font-weight: 700; color: #777777"
            )
            layout.addWidget(self.phase_label)
        self.detail_label = QLabel("等待采集")
        self.detail_label.setObjectName("detailLabel")
        layout.addWidget(self.detail_label)
        self.trend: Optional[pg.PlotWidget] = None
        self.curve = None
        if with_trend:
            self.trend = pg.PlotWidget()
            self.trend.setFixedHeight(38)
            self.trend.setBackground("#fbfbfb")
            self.trend.hideAxis("left")
            self.trend.hideAxis("bottom")
            self.curve = self.trend.plot(pen=pg.mkPen(color, width=1.3))
            layout.addWidget(self.trend)

    def update_value(
        self, value: str, detail: str, trend_values: Optional[np.ndarray] = None
    ) -> None:
        self.value_label.setText(value)
        self.detail_label.setText(detail)
        if self.curve is not None and trend_values is not None:
            self.curve.setData(trend_values)

    def add_header_control(self, label: str, widget: QWidget) -> None:
        control_label = QLabel(label)
        control_label.setObjectName("cardControlLabel")
        self.header_layout.addWidget(control_label)
        self.header_layout.addWidget(widget)

    def update_phase(self, angle_degrees: Optional[float], quality: str) -> None:
        if self.phase_label is None:
            return
        if angle_degrees is None:
            self.phase_label.setText("相对速度角度 --  （%s）" % quality)
            self.phase_label.setStyleSheet(
                "font-size: 16px; font-weight: 700; color: #777777"
            )
            return
        self.phase_label.setText("相对速度角度 %.1f°  （%s）" % (angle_degrees, quality))
        self.phase_label.setStyleSheet(
            "font-size: 16px; font-weight: 700; color: #7c3aed"
        )
