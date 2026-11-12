"""Production support pages backed by real measurements and local files.

These pages intentionally contain no synthetic source.  Calculations that still
require a confirmed two-plane influence-coefficient method are marked pending
instead of presenting invented correction values.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import QPointF, QRectF, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from models.piezo_balance import Piezo1XResult
from models.sensors import (
    SensorProfile,
    sensor_profiles_from_config,
    sensor_profiles_to_dict,
)


RED = "#d5122d"
BLUE = "#1769aa"
AMBER = "#b45309"


def _heading(title: str, description: str) -> QVBoxLayout:
    layout = QVBoxLayout()
    title_label = QLabel(title)
    title_label.setObjectName("PageTitle")
    description_label = QLabel(description)
    description_label.setObjectName("PageHint")
    description_label.setWordWrap(True)
    layout.addWidget(title_label)
    layout.addWidget(description_label)
    return layout


def _panel() -> QFrame:
    return QFrame(objectName="supportPanel")


class MetricCard(QFrame):
    def __init__(self, title: str, color: str, unit: str = "") -> None:
        super().__init__(objectName="supportCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        title_label = QLabel(title)
        title_label.setObjectName("supportCardTitle")
        row = QHBoxLayout()
        self.value_label = QLabel("--")
        self.value_label.setStyleSheet(
            "font-size:24px;font-weight:800;color:%s" % color
        )
        unit_label = self.unit_label = QLabel(unit)
        unit_label.setObjectName("supportUnit")
        row.addWidget(self.value_label)
        row.addWidget(unit_label, 0, Qt.AlignBottom)
        row.addStretch(1)
        layout.addWidget(title_label)
        layout.addLayout(row)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class PolarVectorWidget(QWidget):
    def __init__(self, title: str, color: str) -> None:
        super().__init__()
        self.title = title
        self.color = QColor(color)
        self.amplitude: Optional[float] = None
        self.angle: Optional[float] = None
        self.unit = "mV"
        self.setMinimumSize(250, 250)

    def set_vector(self, amplitude: Optional[float], angle: Optional[float]) -> None:
        self.amplitude = amplitude
        self.angle = angle
        self.update()

    def set_unit(self, unit: str) -> None:
        self.unit = unit
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        margin = 44
        radius = max(28.0, (min(self.width(), self.height()) - 2 * margin) / 2)
        center = QPointF(self.width() / 2, self.height() / 2 + 8)
        painter.setPen(QPen(QColor("#dfe3e8"), 1))
        for ratio in (0.25, 0.5, 0.75, 1.0):
            painter.drawEllipse(center, radius * ratio, radius * ratio)
        for degrees in range(0, 360, 45):
            angle = math.radians(degrees)
            painter.drawLine(
                center,
                QPointF(
                    center.x() + radius * math.cos(angle),
                    center.y() - radius * math.sin(angle),
                ),
            )
        painter.setPen(QColor("#667085"))
        painter.setFont(QFont("PingFang SC", 10))
        for text, dx, dy in (
            ("0°", radius + 10, 4),
            ("90°", -14, -radius - 8),
            ("180°", -radius - 42, 4),
            ("270°", -18, radius + 20),
        ):
            painter.drawText(QPointF(center.x() + dx, center.y() + dy), text)
        painter.setPen(QColor("#15171a"))
        painter.setFont(QFont("PingFang SC", 13, QFont.DemiBold))
        painter.drawText(QRectF(0, 8, self.width(), 24), Qt.AlignCenter, self.title)
        if self.amplitude is None or self.angle is None:
            painter.setPen(QColor("#98a2b3"))
            painter.drawText(
                QRectF(0, center.y() - 12, self.width(), 24),
                Qt.AlignCenter,
                "等待有效同步 1X",
            )
            return
        angle = math.radians(self.angle)
        tip = QPointF(
            center.x() + radius * 0.82 * math.cos(angle),
            center.y() - radius * 0.82 * math.sin(angle),
        )
        painter.setPen(QPen(self.color, 3))
        painter.drawLine(center, tip)
        head = QPolygonF([tip])
        for delta in (math.radians(155), math.radians(205)):
            head.append(
                QPointF(
                    tip.x() + 12 * math.cos(angle + delta),
                    tip.y() - 12 * math.sin(angle + delta),
                )
            )
        painter.setBrush(self.color)
        painter.drawPolygon(head)
        painter.setPen(self.color)
        painter.drawText(
            QRectF(0, self.height() - 28, self.width(), 22),
            Qt.AlignCenter,
            "%.3f %s / %.1f°" % (self.amplitude, self.unit, self.angle),
        )


class DualPlaneBalancePage(QWidget):
    """Real synchronized response view; correction stays blank until calibration."""

    def __init__(self) -> None:
        super().__init__()
        self.sensor_profiles = sensor_profiles_from_config()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(14)
        controls = QHBoxLayout()
        controls.addLayout(
            _heading(
                "双面动平衡",
                "显示真实 AIN0/AIN3 同步 1X 响应。影响系数标定完成前不输出 g·mm、配重质量或机械配重角。",
            ),
            1,
        )
        self.correction = QComboBox()
        self.correction.addItems(["加重修正", "减重修正"])
        self.direction = QComboBox()
        self.direction.addItems(["顺时针角度", "逆时针角度"])
        controls.addWidget(self.correction)
        controls.addWidget(self.direction)
        outer.addLayout(controls)

        cards = QHBoxLayout()
        self.rpm = MetricCard("转子转速", RED, "RPM")
        self.left = MetricCard("左面 1X", BLUE, "mV")
        self.right = MetricCard("右面 1X", RED, "mV")
        self.phase = MetricCard("右相对左相位", AMBER, "°")
        for card in (self.rpm, self.left, self.right, self.phase):
            cards.addWidget(card, 1)
        outer.addLayout(cards)

        vectors = QHBoxLayout()
        left_panel = _panel()
        left_layout = QVBoxLayout(left_panel)
        self.left_polar = PolarVectorWidget("左校正面 A", BLUE)
        self.left_note = QLabel("同步角 --°\n校正量：待试重标定")
        self.left_note.setObjectName("PageHint")
        left_layout.addWidget(self.left_polar, 1)
        left_layout.addWidget(self.left_note)
        right_panel = _panel()
        right_layout = QVBoxLayout(right_panel)
        self.right_polar = PolarVectorWidget("右校正面 B", RED)
        self.right_note = QLabel("同步角 --°\n校正量：待试重标定")
        self.right_note.setObjectName("PageHint")
        right_layout.addWidget(self.right_polar, 1)
        right_layout.addWidget(self.right_note)
        vectors.addWidget(left_panel, 1)
        vectors.addWidget(right_panel, 1)
        outer.addLayout(vectors, 1)

    def set_sensor_profiles(self, profiles: Dict[str, SensorProfile]) -> None:
        self.sensor_profiles = profiles
        for side, card, polar in (
            ("left", self.left, self.left_polar),
            ("right", self.right, self.right_polar),
        ):
            profile = profiles[side]
            card.unit_label.setText(profile.effective_unit)
            polar.set_unit(profile.effective_unit)

    def update_result(self, result: Piezo1XResult) -> None:
        self.rpm.set_value("%.0f" % result.rpm if result.rpm else "--")
        self.left.set_value(
            "%.3f" % self.sensor_profiles["left"].convert_amplitude(
                result.left.amplitude_v
            ) if result.left.valid else "--"
        )
        self.right.set_value(
            "%.3f" % self.sensor_profiles["right"].convert_amplitude(
                result.right.amplitude_v
            ) if result.right.valid else "--"
        )
        self.phase.set_value(
            "%+.1f" % result.relative_phase_degrees
            if result.relative_phase_degrees is not None else "--"
        )
        left_angle = result.left.shaft_phase_degrees if result.left.valid else None
        right_angle = result.right.shaft_phase_degrees if result.right.valid else None
        self.left_polar.set_vector(
            self.sensor_profiles["left"].convert_amplitude(result.left.amplitude_v)
            if result.left.valid else None,
            left_angle,
        )
        self.right_polar.set_vector(
            self.sensor_profiles["right"].convert_amplitude(result.right.amplitude_v)
            if result.right.valid else None,
            right_angle,
        )
        self.left_note.setText(
            "同步角 %.1f°\n校正量：待试重标定" % left_angle
            if left_angle is not None else "同步角 --°\n校正量：待试重标定"
        )
        self.right_note.setText(
            "同步角 %.1f°\n校正量：待试重标定" % right_angle
            if right_angle is not None else "同步角 --°\n校正量：待试重标定"
        )


class TrialCalibrationPage(QWidget):
    """Capture real baseline/left-trial/right-trial responses without a fake solve."""

    STAGES = (
        ("baseline", "① 无试重基线"),
        ("left_trial", "② 左面 0° 试重"),
        ("right_trial", "③ 右面 0° 试重"),
        ("verification", "④ 校正后复测"),
    )

    def __init__(self, data_dir: Path) -> None:
        super().__init__()
        self.data_dir = Path(data_dir)
        self.latest_result: Optional[Piezo1XResult] = None
        self.records: Dict[str, dict] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(14)
        outer.addLayout(
            _heading(
                "试重标定",
                "按无试重、左面试重、右面试重和校正后复测顺序保存真实同步 1X 数据。双面影响系数公式确认前只保存原始矢量，不自动给出修正量。",
            )
        )
        setup = _panel()
        form = QFormLayout(setup)
        form.setContentsMargins(18, 16, 18, 16)
        self.name = QLineEdit()
        self.name.setPlaceholderText("转子型号或标定文件名")
        self.stage = QComboBox()
        for key, label in self.STAGES:
            self.stage.addItem(label, key)
        self.mass = QDoubleSpinBox()
        self.mass.setRange(0, 100000)
        self.mass.setDecimals(4)
        self.mass.setSuffix(" g")
        self.radius = QDoubleSpinBox()
        self.radius.setRange(0, 100000)
        self.radius.setDecimals(3)
        self.radius.setSuffix(" mm")
        self.capture = QPushButton("记录当前真实测量")
        self.capture.setObjectName("Primary")
        self.capture.clicked.connect(self.capture_current)
        self.save = QPushButton("保存标定草稿")
        self.save.clicked.connect(self.save_draft)
        buttons = QHBoxLayout()
        buttons.addWidget(self.capture)
        buttons.addWidget(self.save)
        buttons.addStretch(1)
        form.addRow("标定名称", self.name)
        form.addRow("当前步骤", self.stage)
        form.addRow("试重质量", self.mass)
        form.addRow("试重半径", self.radius)
        form.addRow(buttons)
        outer.addWidget(setup)

        self.table = QTableWidget(len(self.STAGES), 7)
        self.table.setHorizontalHeaderLabels(
            ["步骤", "转速 RPM", "左幅值 mV", "左同步角", "右幅值 mV", "右同步角", "记录时间"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for row, (_key, label) in enumerate(self.STAGES):
            self.table.setItem(row, 0, QTableWidgetItem(label))
        outer.addWidget(self.table, 1)
        self.status = QLabel("等待实时测量页面产生双路有效同步 1X。")
        self.status.setObjectName("PageHint")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

    def set_result(self, result: Piezo1XResult) -> None:
        self.latest_result = result
        if result.left.valid and result.right.valid and result.shaft_angle_synchronous:
            self.status.setText("当前真实测量有效，可记录到所选标定步骤。")
        else:
            self.status.setText("当前测量尚未通过双路幅值和 FIO3 同步角质量门。")

    def capture_current(self) -> None:
        result = self.latest_result
        if (
            result is None
            or result.preview_only
            or not result.left.valid
            or not result.right.valid
            or not result.shaft_angle_synchronous
        ):
            QMessageBox.warning(self, "不能记录", "请先获得双路有效且具有 FIO3 同步角的真实 1X 结果。")
            return
        stage = str(self.stage.currentData())
        if stage != "baseline" and (self.mass.value() <= 0 or self.radius.value() <= 0):
            QMessageBox.warning(self, "试重参数缺失", "试重工况必须填写质量和半径。")
            return
        record = {
            "stage": stage,
            "timestamp": time.time(),
            "rpm": result.rpm,
            "frequency_hz": result.frequency_hz,
            "trial_mass_g": self.mass.value() if stage != "baseline" else 0.0,
            "trial_radius_mm": self.radius.value() if stage != "baseline" else 0.0,
            "left_amplitude_v": result.left.amplitude_v,
            "left_phase_degrees": result.left.shaft_phase_degrees,
            "right_amplitude_v": result.right.amplitude_v,
            "right_phase_degrees": result.right.shaft_phase_degrees,
        }
        self.records[stage] = record
        row = [key for key, _label in self.STAGES].index(stage)
        values = (
            "%.0f" % result.rpm,
            "%.4f" % (result.left.amplitude_v * 1000),
            "%.1f°" % result.left.shaft_phase_degrees,
            "%.4f" % (result.right.amplitude_v * 1000),
            "%.1f°" % result.right.shaft_phase_degrees,
            time.strftime("%F %T"),
        )
        for column, value in enumerate(values, 1):
            self.table.setItem(row, column, QTableWidgetItem(value))
        self.status.setText("已记录%s；双面影响系数算法确认前不计算校正量。" % self.stage.currentText())

    def save_draft(self) -> None:
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, "名称缺失", "请填写转子型号或标定文件名。")
            return
        if not self.records:
            QMessageBox.warning(self, "暂无数据", "请先记录至少一个真实测量工况。")
            return
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name).strip("._-")
        target_dir = self.data_dir / "calibration_drafts"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / (safe + ".json")
        payload = {
            "schema_version": 1,
            "name": name,
            "method": "dual_plane_influence_coefficients_pending_confirmation",
            "records": self.records,
        }
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        QMessageBox.information(self, "标定草稿已保存", str(target))


class TemplateQcPage(QWidget):
    templates_changed = Signal()

    def __init__(self, data_dir: Path) -> None:
        super().__init__()
        self.path = Path(data_dir) / "measurement_templates.json"
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(14)
        outer.addLayout(
            _heading(
                "测量模板与 QC",
                "为不同产品保存目标转速、稳定容差、平均次数和两路 1X 电压上限。未完成试重标定前，阈值单位保持 mV。",
            )
        )
        body = QHBoxLayout()
        list_panel = _panel()
        list_layout = QVBoxLayout(list_panel)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["模板", "目标 RPM", "转速容差", "左限值 mV", "右限值 mV", "平均次数"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        list_layout.addWidget(self.table)
        body.addWidget(list_panel, 2)
        edit_panel = _panel()
        form = QFormLayout(edit_panel)
        form.setContentsMargins(18, 18, 18, 18)
        self.name = QLineEdit()
        self.rpm = QSpinBox(); self.rpm.setRange(1, 200000); self.rpm.setValue(9000)
        self.tolerance = QDoubleSpinBox(); self.tolerance.setRange(0.1, 50); self.tolerance.setValue(2); self.tolerance.setSuffix(" %")
        self.left_limit = QDoubleSpinBox(); self.left_limit.setRange(0, 100000); self.left_limit.setDecimals(3); self.left_limit.setSuffix(" mV")
        self.right_limit = QDoubleSpinBox(); self.right_limit.setRange(0, 100000); self.right_limit.setDecimals(3); self.right_limit.setSuffix(" mV")
        self.average = QSpinBox(); self.average.setRange(1, 100); self.average.setValue(5)
        self.delay = QDoubleSpinBox(); self.delay.setRange(0, 120); self.delay.setValue(3); self.delay.setSuffix(" s")
        save = QPushButton("保存模板")
        save.setObjectName("Primary")
        save.clicked.connect(self.save_template)
        for label, widget in (("模板名称", self.name), ("目标转速", self.rpm), ("允许波动", self.tolerance),
                              ("左 1X 上限", self.left_limit), ("右 1X 上限", self.right_limit),
                              ("测量平均次数", self.average), ("启动后等待", self.delay)):
            form.addRow(label, widget)
        form.addRow(save)
        body.addWidget(edit_panel, 1)
        outer.addLayout(body, 1)
        self.refresh()

    def _load(self) -> list:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def records(self) -> list:
        return self._load()

    def save_template(self) -> None:
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, "模板名称缺失", "请填写模板名称。")
            return
        records = [item for item in self._load() if item.get("name") != name]
        records.append({
            "name": name, "target_rpm": self.rpm.value(), "speed_tolerance_percent": self.tolerance.value(),
            "left_limit_mv": self.left_limit.value(), "right_limit_mv": self.right_limit.value(),
            "average_count": self.average.value(), "start_delay_seconds": self.delay.value(),
            "updated_at": time.time(),
        })
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        self.refresh()
        self.templates_changed.emit()
        QMessageBox.information(self, "模板已保存", "模板已保存；只有填写非零阈值后才适合做 QC 判定。")

    def refresh(self) -> None:
        records = self._load()
        self.table.setRowCount(len(records))
        for row, item in enumerate(records):
            values = (item.get("name", ""), item.get("target_rpm", ""),
                      "%s%%" % item.get("speed_tolerance_percent", ""), item.get("left_limit_mv", ""),
                      item.get("right_limit_mv", ""), item.get("average_count", ""))
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))


class HistoryPage(QWidget):
    def __init__(self, exports_dir: Path) -> None:
        super().__init__()
        self.exports_dir = Path(exports_dir)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(14)
        heading = QHBoxLayout()
        heading.addLayout(_heading("测试记录", "读取真实记录目录和元数据，不生成示例记录。"), 1)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        heading.addWidget(refresh)
        outer.addLayout(heading)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["时间", "工件编号", "记录类型", "原始数据", "频谱", "目录"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        outer.addWidget(self.table, 1)
        self.refresh()

    def refresh(self) -> None:
        records = []
        if self.exports_dir.exists():
            for directory in self.exports_dir.iterdir():
                if not directory.is_dir():
                    continue
                metadata_path = directory / "measurement_metadata.json"
                metadata = {}
                if metadata_path.exists():
                    try:
                        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        metadata = {}
                files = metadata.get("files", {})
                raw = directory / str(files.get("raw", "piezo_left_right_raw.csv"))
                fft = directory / str(files.get("spectrum", "piezo_left_right_fft.csv"))
                if not raw.exists():
                    raw = next(iter(directory.glob("*piezo_left_right_raw.csv")), raw)
                if not fft.exists():
                    fft = next(iter(directory.glob("*piezo_left_right_fft*.csv")), fft)
                if not (metadata_path.exists() or raw.exists() or fft.exists()):
                    continue
                record_type = metadata.get("record_type", "continuous_recording")
                kind = (
                    "当前数据导出" if record_type == "current_data_export" else
                    "固定时长记录" if record_type == "fixed_duration_recording" else
                    "连续记录（旧版）"
                )
                records.append((directory.stat().st_mtime, metadata.get("product_id", ""),
                                kind,
                                raw.exists(), fft.exists(), directory))
        records.sort(reverse=True, key=lambda item: item[0])
        self.table.setRowCount(len(records))
        for row, (stamp, product, kind, raw, fft, directory) in enumerate(records):
            values = (time.strftime("%F %T", time.localtime(stamp)), product or "未填写", kind,
                      "有" if raw else "无", "有" if fft else "无", str(directory))
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))


class SystemSettingsPage(QWidget):
    open_analysis_settings = Signal()
    sensor_settings_changed = Signal(object)

    def __init__(self, config: dict, sensor_profiles: Dict[str, SensorProfile] = None) -> None:
        super().__init__()
        self.sensor_profiles = sensor_profiles or sensor_profiles_from_config(
            config.get("process", {}).get("sensors", {})
        )
        self.sensor_controls = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(14)
        outer.addLayout(_heading("系统设置", "当前页面展示真实硬件配置；1X 质量门和同步补偿通过现有参数对话框修改并持久化。"))
        tabs = QTabWidget()
        hardware = QWidget()
        form = QFormLayout(hardware)
        device = config["devices"]["acquisition_device"]
        channels = device["channels"]
        ni = device.get("ni9234", {})
        for label, value in (("采集设备", "LabJack T7；选择加速度时自动增加 NI-9234"), ("左振动输入", channels["piezo_left"]),
                             ("右振动输入", channels["piezo_right"]), ("转子光电", channels["motor_hall"]),
                             ("同步扫描率", "%.1f kScan/s" % (float(device["piezo_scan_rate"]) / 1000.0)),
                             ("光电频率修正", "原始 ÷ %s" % device.get("optical_frequency_divisor", 1)),
                             ("NI 模块", ni.get("module", "cDAQ1Mod1")),
                             ("NI 左/右通道", "%s / %s" % (ni.get("left_channel", "ai0"), ni.get("right_channel", "ai1"))),
                             ("NI IEPE 激励", "%.1f mA" % float(ni.get("iepe_current_ma", 2.0)))):
            field = QLineEdit(str(value)); field.setReadOnly(True); form.addRow(label, field)
        tabs.addTab(hardware, "硬件与通道")

        sensors = QWidget()
        sensors_layout = QVBoxLayout(sensors)
        sensor_note = QLabel(
            "左右两侧可独立选择传感器。加速度传感器未填灵敏度时仍以原始 mV 显示；"
            "选择压电陶瓷时只使用 LabJack；选择加速度传感器时自动启用 NI-9234，"
            "LabJack 仍读取 FIO3 转速。PCB 353B15 标称 10 mV/g，最终以校准证书为准。"
        )
        sensor_note.setObjectName("PageHint")
        sensor_note.setWordWrap(True)
        sensors_layout.addWidget(sensor_note)
        groups = QHBoxLayout()
        channels = device["channels"]
        for side, title, channel in (
            ("left", "左侧传感器", channels["piezo_left"]),
            ("right", "右侧传感器", channels["piezo_right"]),
        ):
            group = QGroupBox("%s · %s" % (title, channel))
            form = QFormLayout(group)
            sensor_type = QComboBox()
            sensor_type.addItem("压电陶瓷（经放大板）", "piezo")
            sensor_type.addItem("IEPE 加速度传感器（NI-9234）", "accelerometer")
            sensor_type.addItem("未安装 / 不参与分析", "disabled")
            model = QComboBox()
            model.setEditable(True)
            model.lineEdit().setPlaceholderText("选择预设或填写厂家与型号")
            sensitivity = QDoubleSpinBox()
            sensitivity.setRange(0.0, 1000000.0)
            sensitivity.setDecimals(6)
            sensitivity.setSuffix(" mV/g")
            sensitivity.setSpecialValueText("未标定（保留 mV）")
            display_unit = QComboBox()
            for unit in ("mV", "g", "m/s²"):
                display_unit.addItem(unit, unit)
            polarity = QComboBox()
            polarity.addItem("正向（相位不变）", 1)
            polarity.addItem("反向（相位 +180°）", -1)
            form.addRow("类型", sensor_type)
            form.addRow("型号", model)
            form.addRow("灵敏度", sensitivity)
            form.addRow("显示单位", display_unit)
            form.addRow("安装/极性", polarity)
            self.sensor_controls[side] = {
                "type": sensor_type,
                "model": model,
                "sensitivity": sensitivity,
                "unit": display_unit,
                "polarity": polarity,
            }
            sensor_type.currentIndexChanged.connect(
                lambda _index, selected=side: self._sensor_type_changed(selected)
            )
            groups.addWidget(group, 1)
        sensors_layout.addLayout(groups)
        action = QHBoxLayout()
        save_sensors = QPushButton("保存并应用传感器设置")
        save_sensors.setObjectName("Primary")
        save_sensors.clicked.connect(self._save_sensor_settings)
        self.sensor_status = QLabel()
        self.sensor_status.setObjectName("PageHint")
        action.addWidget(save_sensors)
        action.addWidget(self.sensor_status, 1)
        sensors_layout.addLayout(action)
        sensors_layout.addStretch(1)
        tabs.addTab(sensors, "左右传感器")
        self.set_sensor_profiles(self.sensor_profiles)

        analysis = QWidget()
        analysis_layout = QVBoxLayout(analysis)
        note = QLabel("候选 1X 搜索范围、PPR、转速质量门、SNR、饱和判定和同步延迟使用真实采集线程中的同一份参数。")
        note.setWordWrap(True)
        button = QPushButton("打开 1X 测量参数")
        button.setObjectName("Primary")
        button.clicked.connect(self.open_analysis_settings)
        analysis_layout.addWidget(note)
        analysis_layout.addWidget(button, 0, Qt.AlignLeft)
        analysis_layout.addStretch(1)
        tabs.addTab(analysis, "分析参数")
        outer.addWidget(tabs, 1)

    def _sensor_type_changed(self, side: str) -> None:
        controls = self.sensor_controls[side]
        sensor_type = str(controls["type"].currentData())
        current_model = controls["model"].currentText().strip()
        presets = {
            "piezo": ["压电陶瓷 + 现有放大板"],
            "accelerometer": [
                "PCB 353B15（IEPE，NI-9234）",
                "通用 IEPE 加速度传感器（NI-9234）",
            ],
            "disabled": [],
        }[sensor_type]
        controls["model"].blockSignals(True)
        controls["model"].clear()
        controls["model"].addItems(presets)
        if current_model and current_model not in presets:
            controls["model"].addItem(current_model)
        controls["model"].setCurrentText(
            current_model or (presets[0] if presets else "")
        )
        controls["model"].blockSignals(False)
        accelerometer = sensor_type == "accelerometer"
        enabled = sensor_type != "disabled"
        controls["model"].setEnabled(enabled)
        controls["sensitivity"].setEnabled(accelerometer)
        controls["unit"].setEnabled(accelerometer)
        controls["polarity"].setEnabled(enabled)
        if accelerometer and controls["sensitivity"].value() <= 0:
            controls["sensitivity"].setValue(10.0)
            controls["unit"].setCurrentIndex(controls["unit"].findData("g"))
        if sensor_type == "piezo":
            controls["unit"].setCurrentIndex(controls["unit"].findData("mV"))
        elif sensor_type == "disabled":
            controls["sensitivity"].setValue(0.0)
            controls["unit"].setCurrentIndex(controls["unit"].findData("mV"))

    def set_sensor_profiles(self, profiles: Dict[str, SensorProfile]) -> None:
        self.sensor_profiles = profiles
        for side, profile in profiles.items():
            controls = self.sensor_controls[side]
            controls["type"].setCurrentIndex(
                controls["type"].findData(profile.sensor_type)
            )
            controls["model"].setCurrentText(profile.model)
            controls["sensitivity"].setValue(profile.sensitivity_mv_per_g)
            controls["unit"].setCurrentIndex(
                controls["unit"].findData(profile.display_unit)
            )
            controls["polarity"].setCurrentIndex(
                controls["polarity"].findData(profile.polarity)
            )
            self._sensor_type_changed(side)

    def _save_sensor_settings(self) -> None:
        values = {}
        try:
            for side, controls in self.sensor_controls.items():
                controls["sensitivity"].interpretText()
                profile = SensorProfile(
                    sensor_type=str(controls["type"].currentData()),
                    model=controls["model"].currentText().strip(),
                    sensitivity_mv_per_g=float(controls["sensitivity"].value()),
                    display_unit=str(controls["unit"].currentData()),
                    polarity=int(controls["polarity"].currentData()),
                )
                profile.validate()
                values[side] = profile.to_dict()
            profiles = sensor_profiles_from_config(values)
        except ValueError as exc:
            self.sensor_status.setText(str(exc))
            return
        self.sensor_profiles = profiles
        self.sensor_settings_changed.emit(sensor_profiles_to_dict(profiles))
        active = [
            ("左" if side == "left" else "右") + "=" + profile.type_label
            for side, profile in profiles.items() if profile.enabled
        ]
        self.sensor_status.setText("已应用：" + "，".join(active))


class AccessPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(14)
        outer.addLayout(_heading("用户与权限", "保存本机操作员身份和角色。密码哈希、登录锁定和服务端权限尚未接入，当前不把角色显示当作安全控制。"))
        table = QTableWidget(3, 5)
        table.setHorizontalHeaderLabels(["角色", "实时测量", "模板与 QC", "硬件设置", "审计要求"])
        rows = (("操作员", "允许", "只读", "禁止", "记录姓名和时间"),
                ("QC 管理员", "允许", "允许", "只读", "记录参数修改"),
                ("系统管理员", "允许", "允许", "允许", "完整记录"))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        outer.addWidget(table)
        panel = _panel()
        form = QFormLayout(panel)
        self.operator = QLineEdit()
        self.role = QComboBox(); self.role.addItems(["操作员", "QC 管理员", "系统管理员"])
        save = QPushButton("保存本机身份")
        save.clicked.connect(self.save)
        form.addRow("当前用户名", self.operator)
        form.addRow("当前角色", self.role)
        form.addRow(save)
        outer.addWidget(panel)
        outer.addStretch(1)
        settings = QSettings("Boreasa", "DynamicBalancingTest")
        self.operator.setText(str(settings.value("operator/name", "调试员")))
        self.role.setCurrentText(str(settings.value("operator/role", "系统管理员")))

    def save(self) -> None:
        settings = QSettings("Boreasa", "DynamicBalancingTest")
        settings.setValue("operator/name", self.operator.text().strip())
        settings.setValue("operator/role", self.role.currentText())
        settings.sync()
        QMessageBox.information(self, "已保存", "本机操作员身份已保存；当前版本尚未启用密码权限强制。")


class LogPage(QWidget):
    def __init__(self, log_path: Path) -> None:
        super().__init__()
        self.log_path = Path(log_path)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(14)
        heading = QHBoxLayout()
        heading.addLayout(_heading("系统日志", "查看真实程序日志；不插入演示事件。"), 1)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        heading.addWidget(refresh)
        outer.addLayout(heading)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        outer.addWidget(self.text, 1)
        self.refresh()

    def refresh(self) -> None:
        try:
            content = self.log_path.read_text(encoding="utf-8") if self.log_path.exists() else "暂无日志。"
        except OSError as exc:
            content = "日志读取失败：%s" % exc
        self.text.setPlainText(content[-200000:])
        cursor = self.text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.text.setTextCursor(cursor)

