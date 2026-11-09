from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.spectrum import frequency_component, pulse_frequency, spectrum, strongest_peaks
from ui.theme import AMBER, BORDER, MUTED, RED
from ui.widgets.balance_widgets import MetricCard, PolarBalanceWidget, StatusPill


pg.setConfigOption("background", "w")
pg.setConfigOption("foreground", "#59606b")
pg.setConfigOptions(antialias=True)


def plot(title: str, y: str = "V", x: str = "时间 (s)") -> pg.PlotWidget:
    widget = pg.PlotWidget(title=title)
    widget.setLabel("left", y)
    widget.setLabel("bottom", x)
    widget.showGrid(x=True, y=True, alpha=0.12)
    widget.setMenuEnabled(False)
    widget.setStyleSheet(f"border:1px solid {BORDER};border-radius:8px;background:white")
    return widget


def page_heading(title: str, description: str) -> QVBoxLayout:
    box = QVBoxLayout()
    heading = QLabel(title)
    heading.setObjectName("PageTitle")
    hint = QLabel(description)
    hint.setObjectName("Hint")
    hint.setWordWrap(True)
    box.addWidget(heading)
    box.addWidget(hint)
    return box


@dataclass
class DiagnosticValues:
    reference_hz: float = 0.0
    rpm: float = float("nan")
    left_value: float = 0.0
    right_value: float = 0.0
    left_phase: float = 0.0
    right_phase: float = 0.0
    left_unit: str = "mV"
    right_unit: str = "mV"
    left_active: bool = True
    right_active: bool = True
    has_tach_reference: bool = False
    channels_synchronized: bool = True


def display_unit(chunk, channel: str) -> tuple[float, str]:
    unit = chunk.channel_units.get(channel, "V")
    if unit == "V":
        return 1000.0, "mV"
    return 1.0, unit


def diagnostics(chunk) -> DiagnosticValues:
    tach_rate = float(getattr(chunk, "reference_scan_rate", 0.0) or chunk.scan_rate)
    tach_hz = pulse_frequency(chunk.channels.get("TACH", np.array([])), tach_rate)
    ppr = max(1, int(getattr(chunk, "reference_ppr", 1)))
    reference = tach_hz / ppr if tach_hz > 0 else 0.0
    has_tach = reference > 0
    left_active = chunk.channel_sources.get("AIN0", "") != "未安装"
    right_active = chunk.channel_sources.get("AIN1", "") != "未安装"
    if not has_tach:
        candidates = []
        for channel, active in (("AIN0", left_active), ("AIN1", right_active)):
            if active:
                _f, _a, metrics = spectrum(chunk.channels[channel], chunk.scan_rate)
                if metrics.dominant_frequency > 0:
                    candidates.append(metrics.dominant_frequency)
        reference = candidates[0] if candidates else 0.0
    left = frequency_component(chunk.channels["AIN0"], chunk.scan_rate, reference)
    right = frequency_component(chunk.channels["AIN1"], chunk.scan_rate, reference)
    left_scale, left_unit = display_unit(chunk, "AIN0")
    right_scale, right_unit = display_unit(chunk, "AIN1")
    return DiagnosticValues(
        reference_hz=reference,
        rpm=reference * 60.0 if has_tach else float("nan"),
        left_value=left.amplitude * left_scale,
        right_value=right.amplitude * right_scale,
        left_phase=left.phase_deg,
        right_phase=right.phase_deg,
        left_unit=left_unit,
        right_unit=right_unit,
        left_active=left_active,
        right_active=right_active,
        has_tach_reference=has_tach,
        channels_synchronized=bool(getattr(chunk, "channels_synchronized", True)),
    )


class ProductionPage(QWidget):
    product_changed = Signal(str)

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(14)
        outer.addLayout(page_heading("生产测量", "扫码或输入产品编号后开始；系统按稳速、测量、判定、保存的顺序执行。"))

        input_panel = QFrame(objectName="Panel")
        input_layout = QHBoxLayout(input_panel)
        input_layout.setContentsMargins(16, 12, 16, 12)
        input_layout.addWidget(QLabel("产品序列号"))
        self.serial = QLineEdit()
        self.serial.setPlaceholderText("支持键盘、二维码枪和条码枪输入")
        self.serial.setMinimumWidth(260)
        self.serial.textChanged.connect(self.product_changed)
        self.template = QComboBox()
        self.template.addItems(["样机默认模板", "电机转子模板", "风机叶轮模板"])
        self.direction = QComboBox()
        self.direction.addItems(["顺时针", "逆时针"])
        input_layout.addWidget(self.serial, 1)
        input_layout.addWidget(QLabel("测量模板"))
        input_layout.addWidget(self.template)
        input_layout.addWidget(self.direction)
        self.ready = StatusPill("等待产品编号", "neutral")
        input_layout.addWidget(self.ready)
        outer.addWidget(input_panel)

        cards = QHBoxLayout()
        self.rpm = MetricCard("转子转速", "--", "RPM", RED)
        self.left = MetricCard("左面 1X 分量", "--", "", "#1b78b4")
        self.right = MetricCard("右面 1X 分量", "--", "", RED)
        self.phase = MetricCard("右相对左相位", "--", "°", AMBER)
        self.result = MetricCard("QC 判定", "待测", "", MUTED)
        for card in (self.rpm, self.left, self.right, self.phase, self.result):
            cards.addWidget(card, 1)
        outer.addLayout(cards)

        grid = QGridLayout()
        self.left_wave = plot("左传感器实时波形", "信号")
        self.right_wave = plot("右传感器实时波形", "信号")
        self.left_fft = plot("左传感器频谱", "幅值", "频率 (Hz)")
        self.right_fft = plot("右传感器频谱", "幅值", "频率 (Hz)")
        grid.addWidget(self.left_wave, 0, 0)
        grid.addWidget(self.right_wave, 0, 1)
        grid.addWidget(self.left_fft, 1, 0)
        grid.addWidget(self.right_fft, 1, 1)
        outer.addLayout(grid, 1)

        workflow = QFrame(objectName="Panel")
        workflow_layout = QHBoxLayout(workflow)
        workflow_layout.setContentsMargins(16, 10, 16, 10)
        workflow_layout.addWidget(QLabel("流程"))
        self.steps = []
        for i, name in enumerate(("1 输入编号", "2 等待稳速", "3 多次测量", "4 QC判定", "5 自动保存")):
            pill = StatusPill(name, "ready" if i == 0 else "neutral")
            self.steps.append(pill)
            workflow_layout.addWidget(pill)
        workflow_layout.addStretch()
        outer.addWidget(workflow)

    def set_running(self, running: bool) -> None:
        if running:
            self.ready.set_state("warning", "采集中")
            for i, step in enumerate(self.steps):
                step.set_state("ready" if i <= 2 else "neutral")
        elif self.serial.text().strip():
            self.ready.set_state("ready", "编号已就绪")
        else:
            self.ready.set_state("neutral", "等待产品编号")

    def update_chunk(self, chunk) -> None:
        d = diagnostics(chunk)
        self.rpm.set_value(f"{d.rpm:,.0f}" if np.isfinite(d.rpm) else "--")
        self.left.set_value(f"{d.left_value:.3f}" if d.left_active else "--", d.left_unit)
        self.right.set_value(f"{d.right_value:.3f}" if d.right_active else "--", d.right_unit)
        delta = ((d.right_phase - d.left_phase + 180) % 360) - 180
        self.phase.set_value(f"{delta:+.1f}" if d.left_active and d.right_active and d.channels_synchronized else "--")
        self.result.set_value("待标定")
        n = len(chunk.channels["AIN0"])
        t = np.arange(n) / chunk.scan_rate
        stride = max(1, n // 2500)
        self.left_wave.setLabel("left", f"信号 ({chunk.channel_units.get('AIN0', 'V')})")
        self.right_wave.setLabel("left", f"信号 ({chunk.channel_units.get('AIN1', 'V')})")
        self.left_wave.plot(t[::stride], chunk.channels["AIN0"][::stride], clear=True, pen="#1b78b4")
        self.right_wave.plot(t[::stride], chunk.channels["AIN1"][::stride], clear=True, pen=RED)
        for channel, widget, color in (("AIN0", self.left_fft, "#1b78b4"), ("AIN1", self.right_fft, RED)):
            f, a, _ = spectrum(chunk.channels[channel], chunk.scan_rate)
            mask = f <= 300
            scale, unit = display_unit(chunk, channel)
            widget.setLabel("left", f"幅值 ({unit})")
            widget.plot(f[mask], a[mask] * scale, clear=True, pen=color)


class BalancePage(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(14)
        heading = page_heading("双面动平衡", "极坐标显示原始 1X 信号矢量；偏重质量和机械配重角在试重标定完成前保持为空。")
        controls = QHBoxLayout()
        controls.addLayout(heading, 1)
        self.mode = QComboBox(); self.mode.addItems(["双面", "单面"])
        self.correction = QComboBox(); self.correction.addItems(["加重修正", "减重修正"])
        self.direction = QComboBox(); self.direction.addItems(["顺时针角度", "逆时针角度"])
        controls.addWidget(self.mode); controls.addWidget(self.correction); controls.addWidget(self.direction)
        outer.addLayout(controls)

        content = QHBoxLayout()
        left_panel = QFrame(objectName="Panel")
        left_layout = QVBoxLayout(left_panel)
        self.left_polar = PolarBalanceWidget("左校正面 A", "#1b78b4")
        self.left_text = QLabel("原始1X：-- mV\n电气相位：--°\n偏重质量：待标定\n配重角度：待标定")
        self.left_text.setObjectName("Hint")
        left_layout.addWidget(self.left_polar, 1); left_layout.addWidget(self.left_text)
        right_panel = QFrame(objectName="Panel")
        right_layout = QVBoxLayout(right_panel)
        self.right_polar = PolarBalanceWidget("右校正面 B", RED)
        self.right_text = QLabel("原始1X：-- mV\n电气相位：--°\n偏重质量：待标定\n配重角度：待标定")
        self.right_text.setObjectName("Hint")
        right_layout.addWidget(self.right_polar, 1); right_layout.addWidget(self.right_text)
        content.addWidget(left_panel, 1); content.addWidget(right_panel, 1)

        summary = QFrame(objectName="Panel")
        summary.setFixedWidth(330)
        summary_layout = QVBoxLayout(summary)
        title = QLabel("测量状态"); title.setObjectName("SectionTitle")
        self.rpm = MetricCard("参考转速", "--", "RPM", RED)
        self.phase = MetricCard("右相对左电气相位", "--", "°", AMBER)
        self.valid = StatusPill("等待数据", "neutral")
        note = QLabel("说明\n• 当前显示的是同步 1X 电压矢量。\n• 机械偏重角需零位定义和试重标定。\n• 质量/角度算法确认前不输出修正量。")
        note.setWordWrap(True); note.setObjectName("Hint")
        summary_layout.addWidget(title); summary_layout.addWidget(self.rpm); summary_layout.addWidget(self.phase)
        summary_layout.addWidget(self.valid); summary_layout.addWidget(note); summary_layout.addStretch()
        content.addWidget(summary)
        outer.addLayout(content, 1)

    def update_chunk(self, chunk) -> None:
        d = diagnostics(chunk)
        delta = ((d.right_phase - d.left_phase + 180) % 360) - 180
        self.left_polar.set_vector(d.left_value if d.left_active else None, d.left_phase if d.left_active else None, d.left_unit)
        self.right_polar.set_vector(d.right_value if d.right_active else None, d.right_phase if d.right_active else None, d.right_unit)
        self.left_text.setText(f"原始1X：{d.left_value:.4f} {d.left_unit}\n电气相位：{d.left_phase:.1f}°\n偏重质量：待标定\n配重角度：待标定" if d.left_active else "左侧传感器未启用")
        self.right_text.setText(f"原始1X：{d.right_value:.4f} {d.right_unit}\n电气相位：{d.right_phase:.1f}°\n偏重质量：待标定\n配重角度：待标定" if d.right_active else "右侧传感器未启用")
        self.rpm.set_value(f"{d.rpm:,.0f}" if np.isfinite(d.rpm) else "--")
        self.phase.set_value(f"{delta:+.1f}" if d.left_active and d.right_active and d.channels_synchronized else "--")
        if not d.channels_synchronized:
            self.valid.set_state("warning", "NI/LabJack 混合通道 · 左右相位不可直接比较")
        elif d.has_tach_reference:
            self.valid.set_state("ready", "同步1X有效 · 修正量待标定")
        else:
            self.valid.set_state("warning", f"无转速参考 · 当前候选峰 {d.reference_hz:.2f} Hz")


class SpectrumPage(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18); outer.setSpacing(14)
        top = QHBoxLayout()
        top.addLayout(page_heading("频谱诊断", "双通道频谱、主要峰值和可配置频段；用于诊断，不直接替代动平衡 1X 判定。"), 1)
        top.addWidget(QLabel("频率上限"))
        self.upper = QSpinBox(); self.upper.setRange(50, 20_000); self.upper.setValue(300); self.upper.setSuffix(" Hz")
        top.addWidget(self.upper); outer.addLayout(top)
        plots = QGridLayout()
        self.left_wave = plot("左传感器时域", "信号")
        self.left_fft = plot("左传感器 FFT", "幅值", "Hz")
        self.right_wave = plot("右传感器时域", "信号")
        self.right_fft = plot("右传感器 FFT", "幅值", "Hz")
        plots.addWidget(self.left_wave, 0, 0); plots.addWidget(self.left_fft, 0, 1)
        plots.addWidget(self.right_wave, 1, 0); plots.addWidget(self.right_fft, 1, 1)
        outer.addLayout(plots, 1)
        footer = QHBoxLayout()
        self.peaks = QTableWidget(0, 5)
        self.peaks.setHorizontalHeaderLabels(["排序", "左频率 Hz", "左幅值 mV", "右频率 Hz", "右幅值 mV"])
        self.peaks.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.peaks.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); self.peaks.setMaximumHeight(170)
        footer.addWidget(self.peaks, 2)
        bands = QTableWidget(4, 4)
        bands.setHorizontalHeaderLabels(["频段", "起始 Hz", "终止 Hz", "QC上限"])
        for r, values in enumerate((("1X附近", "40", "180", "待设置"), ("低频", "0", "300", "待设置"),
                                    ("中频", "300", "3000", "待设置"), ("高频", "3000", "20000", "待设置"))):
            for c, value in enumerate(values): bands.setItem(r, c, QTableWidgetItem(value))
        bands.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); bands.setMaximumHeight(170)
        footer.addWidget(bands, 2); outer.addLayout(footer)

    def update_chunk(self, chunk) -> None:
        n = len(chunk.channels["AIN0"]); t = np.arange(n) / chunk.scan_rate
        stride = max(1, n // 2500); upper = float(self.upper.value()); results = []; units = []
        for channel, wave, fft, color in (("AIN0", self.left_wave, self.left_fft, "#1b78b4"),
                                           ("AIN1", self.right_wave, self.right_fft, RED)):
            values = chunk.channels[channel]; f, a, _ = spectrum(values, chunk.scan_rate)
            scale, unit = display_unit(chunk, channel)
            units.append(unit)
            wave.setLabel("left", unit)
            fft.setLabel("left", unit)
            wave.plot(t[::stride], values[::stride], clear=True, pen=color)
            mask = f <= upper; fft.plot(f[mask], a[mask] * scale, clear=True, pen=color)
            results.append(strongest_peaks(f, a * scale, upper, 5))
        self.peaks.setHorizontalHeaderLabels(["排序", "左频率 Hz", f"左幅值 {units[0]}", "右频率 Hz", f"右幅值 {units[1]}"])
        rows = max(len(results[0]), len(results[1])); self.peaks.setRowCount(rows)
        for r in range(rows):
            left = results[0][r] if r < len(results[0]) else (0.0, 0.0)
            right = results[1][r] if r < len(results[1]) else (0.0, 0.0)
            values = (str(r + 1), f"{left[0]:.2f}", f"{left[1]:.3f}", f"{right[0]:.2f}", f"{right[1]:.3f}")
            for c, value in enumerate(values): self.peaks.setItem(r, c, QTableWidgetItem(value))


class CalibrationPage(QWidget):
    def __init__(self):
        super().__init__(); self.current_step = 0
        outer = QVBoxLayout(self); outer.setContentsMargins(20, 18, 20, 18); outer.setSpacing(14)
        outer.addLayout(page_heading("试重标定", "按基准、左面试重、右面试重依次记录；影响系数和修正量公式待确认后再启用计算。"))
        form_panel = QFrame(objectName="Panel"); form = QHBoxLayout(form_panel); form.setContentsMargins(16, 12, 16, 12)
        self.average = QSpinBox(); self.average.setRange(1, 100); self.average.setValue(5)
        self.left_weight = QDoubleSpinBox(); self.left_weight.setRange(0, 100000); self.left_weight.setSuffix(" mg")
        self.left_radius = QDoubleSpinBox(); self.left_radius.setRange(0, 10000); self.left_radius.setSuffix(" mm")
        self.right_weight = QDoubleSpinBox(); self.right_weight.setRange(0, 100000); self.right_weight.setSuffix(" mg")
        self.right_radius = QDoubleSpinBox(); self.right_radius.setRange(0, 10000); self.right_radius.setSuffix(" mm")
        for label, widget in (("平均次数", self.average), ("左试重", self.left_weight), ("左半径", self.left_radius),
                              ("右试重", self.right_weight), ("右半径", self.right_radius)):
            form.addWidget(QLabel(label)); form.addWidget(widget)
        form.addStretch(); outer.addWidget(form_panel)
        self.table = QTableWidget(4, 6)
        self.table.setHorizontalHeaderLabels(["步骤", "工况", "左幅值", "左相位", "右幅值", "右相位"])
        labels = (("1", "基准转子"), ("2", "左面0°加试重"), ("3", "右面0°加试重"), ("4", "计算并保存"))
        for row, (index, name) in enumerate(labels):
            self.table.setItem(row, 0, QTableWidgetItem(index)); self.table.setItem(row, 1, QTableWidgetItem(name))
            for col in range(2, 6): self.table.setItem(row, col, QTableWidgetItem("--"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.table.setCurrentCell(0, 0)
        outer.addWidget(self.table)
        actions = QHBoxLayout(); self.instruction = QLabel("步骤1：安装基准转子，确认转速稳定后记录。"); self.instruction.setObjectName("SectionTitle")
        self.record = QPushButton("记录当前工况"); self.record.setObjectName("Primary")
        self.reset = QPushButton("重新标定"); self.save = QPushButton("保存标定文件"); self.save.setEnabled(False)
        self.record.clicked.connect(self._record); self.reset.clicked.connect(self._reset)
        actions.addWidget(self.instruction, 1); actions.addWidget(self.record); actions.addWidget(self.reset); actions.addWidget(self.save)
        outer.addLayout(actions)
        result = QFrame(objectName="Panel"); result_layout = QVBoxLayout(result)
        title = QLabel("标定结果"); title.setObjectName("SectionTitle")
        self.result = QLabel("左/右影响系数：--\n质量换算：--\n状态：等待三组有效工况；计算公式待确认。")
        self.result.setObjectName("Hint"); result_layout.addWidget(title); result_layout.addWidget(self.result); outer.addWidget(result)

    def update_chunk(self, chunk) -> None: self.latest = diagnostics(chunk)

    def _record(self) -> None:
        if not hasattr(self, "latest") or self.current_step >= 3: return
        d = self.latest
        values = (f"{d.left_value:.4f} {d.left_unit}", f"{d.left_phase:.1f}°",
                  f"{d.right_value:.4f} {d.right_unit}", f"{d.right_phase:.1f}°")
        for col, value in enumerate(values, 2): self.table.setItem(self.current_step, col, QTableWidgetItem(value))
        self.current_step += 1; self.table.setCurrentCell(min(self.current_step, 3), 0)
        instructions = ("步骤2：左面0°安装试重并输入质量、半径。", "步骤3：移除左试重，右面0°安装试重。",
                        "三组工况已记录。影响系数公式确认后可计算并保存。")
        self.instruction.setText(instructions[self.current_step - 1])
        if self.current_step == 3: self.record.setEnabled(False); self.save.setEnabled(False)

    def _reset(self) -> None:
        self.current_step = 0
        for row in range(4):
            for col in range(2, 6): self.table.setItem(row, col, QTableWidgetItem("--"))
        self.table.setCurrentCell(0, 0); self.instruction.setText("步骤1：安装基准转子，确认转速稳定后记录。")
        self.record.setEnabled(True); self.save.setEnabled(False)


RealtimePage = ProductionPage
