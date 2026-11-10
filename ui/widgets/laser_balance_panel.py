"""Operator-facing single-plane laser measurement and four-run calibration."""

from dataclasses import asdict
from uuid import uuid4
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QFileDialog, QSpinBox, QVBoxLayout, QWidget,
)

from models.laser_balance import BalanceMeasurement, BalanceSettings, UNITS
from services.laser_balance import FourRunBalanceSession


STAGE_NAMES = {
    "baseline": "① 基线：无试重", "trial_0": "② 同一试重 0°",
    "trial_120": "③ 同一试重 120°", "trial_240": "④ 同一试重 240°",
    "measure": "⑤ 移除试重后测量",
}


class LaserBalancePanel(QFrame):
    settings_changed = Signal(object)
    report_requested = Signal(object)
    record_ready = Signal(object)

    def __init__(self, settings: BalanceSettings) -> None:
        super().__init__()
        self.setObjectName("dataCard")
        self.settings = settings
        self.session = FourRunBalanceSession(settings)
        self.mass_g = 0.0
        self.radius_mm = 0.0
        self.running = False
        self.laser_running = False
        self.latest_estimate = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        header = QHBoxLayout()
        header.addWidget(QLabel("激光不平衡量 · 单面等效"))
        self.result_label = QLabel("-- g·mm（未标定）")
        self.result_label.setWordWrap(True)
        self.result_label.setStyleSheet("color:#075985;font-size:19px;font-weight:700")
        header.addWidget(self.result_label, 1)
        self.settings_button = QPushButton("测量参数 / 激光单位")
        self.settings_button.clicked.connect(self.edit_settings)
        header.addWidget(self.settings_button)
        self.load_button = QPushButton("加载标定")
        self.load_button.clicked.connect(self.load_calibration)
        header.addWidget(self.load_button)
        self.report_button = QPushButton("导出标定报告")
        self.report_button.clicked.connect(self.export_report)
        header.addWidget(self.report_button)
        layout.addLayout(header)
        self.metrics_label = QLabel("候选 1X：-- | 请核对转速来源、每转脉冲数、搜索范围与试重参数")
        self.metrics_label.setWordWrap(True)
        layout.addWidget(self.metrics_label)
        controls = QHBoxLayout()
        self.stage = QComboBox()
        for key, name in STAGE_NAMES.items():
            self.stage.addItem(name, key)
        self.stage.currentIndexChanged.connect(self._stage_changed)
        controls.addWidget(self.stage)
        self.prepared = QCheckBox("工况/试重及候选 1X 已确认")
        self.prepared.setToolTip(
            "停止采集不等于电机停转！实际停机后安装/移动/移除试重并可靠固定。\n"
            "确认峰为转子 1X，转子、测点、弹片、夹紧、激光输出及增益保持一致。"
        )
        controls.addWidget(self.prepared)
        self.capture_button = QPushButton("记录当前工况")
        self.capture_button.clicked.connect(self.capture)
        controls.addWidget(self.capture_button)
        self.solve_button = QPushButton("完成四次标定")
        self.solve_button.clicked.connect(self.solve)
        controls.addWidget(self.solve_button)
        self.reset_button = QPushButton("重新标定")
        self.reset_button.clicked.connect(self.reset_calibration)
        controls.addWidget(self.reset_button)
        self.progress_label = QLabel("已记录 0/4；试重未设置")
        controls.addWidget(self.progress_label)
        controls.addStretch()
        layout.addLayout(controls)
        self.set_running(False)

    def _stage_changed(self) -> None:
        self.prepared.setChecked(False)
        self.latest_estimate = None
        self.result_label.setText("-- g·mm（工况已变化）")

    def start_run(self) -> None:
        self.session.start_run(str(uuid4()))
        self.latest_estimate = None
        self.result_label.setText("-- g·mm（等待有效测量）")
        self.metrics_label.setText(
            "正在建立独立分析窗；光电直接测转子转速，候选峰仍需确认"
            if self.settings.reference == "photoelectric" else
            "正在建立独立分析窗；参考转速暂按设定参数，候选 1X 需确认")

    def set_running(self, running: bool, laser: bool = False) -> None:
        self.running = running
        self.laser_running = running and laser
        for control in (self.stage, self.prepared, self.settings_button, self.reset_button, self.load_button):
            control.setEnabled(not running)
        self.capture_button.setEnabled(self.laser_running)
        self.solve_button.setEnabled(not running)
        if not running:
            self.session.windows.clear()
            self.latest_estimate = None
            self.result_label.setText("-- g·mm（未采集/已停止）")

    def update_measurement(self, measurement: BalanceMeasurement) -> None:
        self.session.update(measurement)
        self.latest_estimate = None
        self.result_label.setText("-- g·mm")
        if not measurement.valid:
            self.metrics_label.setText(measurement.reason)
            if measurement.tone is not None:
                self.result_label.setText("-- g·mm（仅诊断预览，不用于标定）")
                self.metrics_label.setText(
                    "预览 %.3f Hz | 参考 %.3f Hz | 偏差 %+.3f Hz | 信噪比 %.1f | %s" % (
                        measurement.frequency_hz, measurement.reference_rpm / 60.0,
                        measurement.frequency_hz - measurement.reference_rpm / 60.0,
                        measurement.snr, measurement.reason))
        else:
            factor = self.settings.display_factor
            unit = self.settings.display_unit
            self.metrics_label.setText(
                "就近候选 %.3f Hz (%.1f RPM) | 参考 %.3f Hz | 偏差 %+.3f Hz | 峰值 %.5g %s | "
                "信噪比 %.1f | 窗口 %d/3 | %s"
                % (measurement.frequency_hz, measurement.frequency_hz * 60,
                   measurement.reference_rpm / 60,
                   measurement.frequency_hz - measurement.reference_rpm / 60,
                   measurement.amplitude_v * factor, unit,
                   measurement.snr, len(self.session.windows),
                   "已设激光比例" if self.settings.scale_confirmed else "激光物理量比例未确认，保留 mV")
            )
            try:
                summary = self.session.summary()
                self.metrics_label.setText(self.metrics_label.text() +
                    " | 均值 %.5g / 标准差 %.3g %s (CV %.2f%%)" % (
                        summary["amplitude_v"] * factor, summary["std_v"] * factor,
                        unit, summary["cv"] * 100))
                if self.stage.currentData() == "measure" and self.prepared.isChecked():
                    self.latest_estimate = self.session.estimate()
                    self.result_label.setText("%.4g g·mm" % self.latest_estimate["equivalent_unbalance_gmm"])
                else:
                    self.result_label.setText("-- g·mm（先完成标定，再移除试重复测）")
            except ValueError as exc:
                self.result_label.setText("-- g·mm（%s）" % exc)
        self.record_ready.emit({
            "kind": "measurement", "run_id": self.session.run_id,
            "stage": self.stage.currentData(), "operator_confirmed": self.prepared.isChecked(),
            "measurement": measurement.record(), "estimate": self.latest_estimate,
        })

    def capture(self) -> None:
        try:
            if not self.laser_running or not self.prepared.isChecked():
                raise ValueError("请停采集并实际停机确认工况，再勾选确认、重新启动激光")
            self.session.capture(self.stage.currentData(), self.mass_g, self.radius_mm)
            self._refresh_progress()
            self.record_ready.emit({"kind": "calibration_capture", "report": self.session.report()})
            self.export_report()
            self.result_label.setText("工况已记录；停采集并实际停机后换下一工况")
        except ValueError as exc:
            QMessageBox.warning(self, "未记录", str(exc))

    def solve(self) -> None:
        try:
            if self.running:
                raise ValueError("请先停止采集")
            result = self.session.solve()
            self.result_label.setText("标定完成；基线等效 %.4g g·mm" % result["baseline_equivalent_gmm"])
            self.metrics_label.setText(
                "请实际停机移除试重，选择⑤并确认，再开启激光复测。"
                "仅同转子/测点/装夹/输出/转速有效；不输出双面量或配重角度。"
            )
            self.export_report()
        except ValueError as exc:
            QMessageBox.warning(self, "无法标定", str(exc))

    def export_report(self) -> None:
        report = self.session.report()
        report["latest_estimate"] = self.latest_estimate
        self.report_requested.emit(report)

    def load_calibration(self) -> None:
        if self.running:
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择已保存的四次标定报告", "", "JSON 标定报告 (*.json)")
        if not path:
            return
        try:
            restored = FourRunBalanceSession.load_report(Path(path), self.settings)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            QMessageBox.warning(self, "不能加载", str(exc))
            return
        if QMessageBox.question(self, "确认沿用工况",
            "确认与报告使用相同转子、测点、弹片、装夹、激光输出/增益和转速？\n"
            "若任何条件已变，请重新标定。加载后还需移除试重并采集新数据。") != QMessageBox.Yes:
            return
        self.session = restored
        self.mass_g = restored.calibration["trial_mass_g"]
        self.radius_mm = restored.calibration["trial_radius_mm"]
        self.stage.setCurrentIndex(self.stage.findData("measure"))
        self.prepared.setChecked(False)
        self.latest_estimate = None
        self._refresh_progress()
        self.result_label.setText("-- g·mm（已加载标定，等待无试重复测）")

    def _refresh_progress(self) -> None:
        labels = [STAGE_NAMES[key].split("：")[0] for key in self.session.runs]
        self.progress_label.setText("已记录 %d/4 | %.4g g × %.4g mm" %
                                    (len(self.session.runs), self.mass_g, self.radius_mm))
        self.progress_label.setToolTip("\n".join(labels))

    def reset_calibration(self) -> None:
        if self.running:
            return
        if self.session.runs and QMessageBox.question(
            self, "重新标定", "清空本次内存中的四次记录和系数？已导出的文件不会删除。"
        ) != QMessageBox.Yes:
            return
        self.session = FourRunBalanceSession(self.settings)
        self.prepared.setChecked(False)
        self._refresh_progress()
        self.result_label.setText("-- g·mm（未标定）")

    def edit_settings(self) -> None:
        if self.running:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("激光单面测量参数与试重设置")
        dialog.resize(660, 740)
        outer = QVBoxLayout(dialog)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        fields = {}
        def number(key, label, low, high, decimals=3):
            widget = QDoubleSpinBox()
            widget.setRange(low, high)
            widget.setDecimals(decimals)
            widget.setKeyboardTracking(False)
            widget.setValue(getattr(self.settings, key))
            form.addRow(label, widget)
            fields[key] = widget
        quantity = QComboBox()
        for key, label in (("displacement", "位移 μm"), ("velocity", "速度 mm/s"),
                           ("acceleration", "加速度 m/s²")):
            quantity.addItem(label, key)
        quantity.setCurrentIndex(quantity.findData(self.settings.quantity))
        form.addRow("激光控制器实际输出类型", quantity)
        number("units_per_volt", "每 1 V 对应的所选物理量（单位/V）", 0.000001, 1e9, 6)
        confirmed = QCheckBox("已核对控制器输出类型、比例及带宽")
        confirmed.setChecked(self.settings.scale_confirmed)
        form.addRow(confirmed)
        note = QLabel("未确认比例时保留 mV；四次试重系数直接用 V/(g·mm)，不臆测物理量。\n"
                      "选择类型不会遥控激光控制器切换输出。若手册为 mm/s²/V，换成 m/s²/V 时除以 1000。")
        note.setWordWrap(True)
        form.addRow(note)
        reference = QComboBox()
        reference.addItem("FIO3 光电（直接测被测转子）", "photoelectric")
        reference.addItem("FIO3 霍尔（临时转速参考）", "hall")
        reference.addItem("手动输入实际/临时转子转速", "manual")
        reference.setCurrentIndex(reference.findData(self.settings.reference))
        form.addRow("转速来源", reference)
        ppr = QSpinBox()
        ppr.setRange(1, 100)
        ppr.setValue(self.settings.hall_ppr)
        form.addRow("FIO3 每转脉冲数 PPR", ppr)
        number("rotor_motor_ratio", "转子 RPM / 电机 RPM（仅霍尔参考使用）", 0.0001, 1000, 4)
        number("manual_rpm", "手动转子 RPM（不会检测实际停转）", 1, 300000, 1)
        def update_reference_fields():
            fields["rotor_motor_ratio"].setEnabled(reference.currentData() == "hall")
            fields["manual_rpm"].setEnabled(reference.currentData() == "manual")
        reference.currentIndexChanged.connect(update_reference_fields)
        update_reference_fields()
        number("search_min_hz", "候选 1X 搜索下限 Hz", 0.01, 49999, 2)
        number("search_max_hz", "候选 1X 搜索上限 Hz", 0.02, 50000, 2)
        selection_note = QLabel("频带内优先选择最接近参考转频的有效峰；\n"
                               "无合格峰时显示最近局部峰的橙色诊断预览，不用于标定。\n"
                               "停转/无参考、丢样、过量程或无局部峰时仍清空右图。")
        selection_note.setWordWrap(True)
        form.addRow(selection_note)
        number("window_seconds", "独立分析窗 秒（连续 3 窗供标定）", 0.5, 10, 1)
        number("reference_tolerance", "峰频偏离参考转频上限（0.15=15%）", 0.001, 1, 3)
        number("rpm_variation_limit", "转频波动上限（0.005=0.5%）", 0.0001, 0.1, 4)
        number("amplitude_cv_limit", "幅值 CV 上限（0.05=5%）", 0.001, 0.5, 3)
        number("calibration_speed_tolerance", "沿用标定的转频偏差（0.01=1%）", 0.0001, 0.1, 4)
        number("minimum_snr", "最低峰值/邻近频带噪声比", 2, 1000, 1)
        identifier = QLineEdit(self.settings.rotor_id)
        identifier.setPlaceholderText("例如：转子01 / 左支撑铝块 / 测点1 / 装夹版本1")
        form.addRow("转子 / 测点 / 工装标识", identifier)
        mass, radius = QDoubleSpinBox(), QDoubleSpinBox()
        for widget, value, suffix in ((mass, self.mass_g, " g"), (radius, self.radius_mm, " mm")):
            widget.setDecimals(5)
            widget.setRange(0, 100000)
            widget.setValue(value)
            widget.setSuffix(suffix)
            widget.setKeyboardTracking(False)
        form.addRow("试重质量（须自行核定安全范围）", mass)
        form.addRow("试重半径（四次运行保持一致）", radius)
        warning = QLabel("停止采集 ≠ 电机停转。实际停机后移动试重并可靠固定，使用防护。\n"
                         "适用单面等效量；不同转子/弹片/测点/装夹/输出或增益必须重新标定。")
        warning.setWordWrap(True)
        form.addRow(warning)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        outer.addWidget(buttons)
        buttons.rejected.connect(dialog.reject)
        def save():
            values = asdict(self.settings)
            values.update({key: widget.value() for key, widget in fields.items()})
            values.update(quantity=quantity.currentData(), scale_confirmed=confirmed.isChecked(),
                          reference=reference.currentData(), hall_ppr=ppr.value(), rotor_id=identifier.text().strip())
            try:
                settings = BalanceSettings.from_dict(values)
                settings.validate()
            except ValueError as exc:
                QMessageBox.warning(dialog, "参数无效", str(exc))
                return
            changed = (settings != self.settings or mass.value() != self.mass_g or radius.value() != self.radius_mm)
            if changed and self.session.runs and QMessageBox.question(
                dialog, "参数变化", "修改参数将清空当前标定，请先导出需要保留的报告。继续？"
            ) != QMessageBox.Yes:
                return
            if changed:
                self.settings = settings
                self.mass_g, self.radius_mm = mass.value(), radius.value()
                self.session = FourRunBalanceSession(settings)
                self.prepared.setChecked(False)
                self._refresh_progress()
                self.result_label.setText("-- g·mm（参数已改变，需重新标定）")
                self.settings_changed.emit(asdict(settings))
            dialog.accept()
        buttons.accepted.connect(save)
        dialog.exec()
