"""Operator-facing dual-piezo 1X status without uncalibrated force claims."""

from dataclasses import asdict

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout,
)

from models.piezo_balance import Piezo1XResult, PiezoSettings
from models.sensors import SensorProfile, sensor_profiles_from_config


class PiezoBalancePanel(QFrame):
    settings_changed = Signal(object)

    def __init__(
        self, settings: PiezoSettings, sensor_profiles: dict = None
    ) -> None:
        super().__init__()
        self.setObjectName("dataCard")
        self.settings = settings
        self.sensor_profiles = sensor_profiles_from_config(sensor_profiles)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        top = QHBoxLayout()
        self.summary_title = QLabel("振动传感器 · 转子 1X")
        top.addWidget(self.summary_title)
        self.left_label = QLabel("左 -- mV")
        self.right_label = QLabel("右 -- mV")
        self.relative_phase_label = QLabel("右相对左电气相位 --°")
        for label in (self.left_label, self.right_label, self.relative_phase_label):
            label.setStyleSheet("color:#075985;font-size:17px;font-weight:700")
            top.addWidget(label)
        self.settings_button = QPushButton("1X 测量参数")
        self.settings_button.clicked.connect(self.edit_settings)
        top.addWidget(self.settings_button)
        layout.addLayout(top)
        angles = QHBoxLayout()
        angles.addWidget(QLabel("FIO3 光电下降沿 = 0°"))
        self.left_angle_label = QLabel("A 同步角 --°")
        self.right_angle_label = QLabel("B 同步角 --°")
        for label in (self.left_angle_label, self.right_angle_label):
            label.setStyleSheet("color:#7c3aed;font-size:16px;font-weight:700")
            angles.addWidget(label)
        angles.addStretch(1)
        layout.addLayout(angles)
        self.status_label = QLabel(
            "等待采集；仅显示放大板输出电压。未做双面试重标定，不输出 g·mm 或配重角度。"
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.set_sensor_profiles(self.sensor_profiles)

    def set_sensor_profiles(self, profiles: dict) -> None:
        self.sensor_profiles = sensor_profiles_from_config(profiles)
        active = [
            ("左" if side == "left" else "右") + profile.type_label
            for side, profile in self.sensor_profiles.items() if profile.enabled
        ]
        self.summary_title.setText(" / ".join(active) + " · 转子 1X")
        self.clear("传感器设置已应用，等待新分析窗")

    def set_running(self, running: bool) -> None:
        # Analysis parameters can be hot-applied. The worker discards the
        # partial analysis window and rebuilds it with the new settings.
        self.settings_button.setEnabled(True)
        if not running:
            self.clear("已停止；不保留旧 1X 幅值和相位")

    def clear(self, status: str) -> None:
        self.left_label.setText("左 -- %s" % self.sensor_profiles["left"].effective_unit)
        self.right_label.setText("右 -- %s" % self.sensor_profiles["right"].effective_unit)
        self.relative_phase_label.setText("右相对左电气相位 --°")
        self.left_angle_label.setText("A 同步角 --°")
        self.right_angle_label.setText("B 同步角 --°")
        self.status_label.setText(status)

    def update_result(self, result: Piezo1XResult) -> None:
        left_profile = self.sensor_profiles["left"]
        right_profile = self.sensor_profiles["right"]
        self.left_label.setText(
            "左 %.3f %s%s" % (
                left_profile.convert_amplitude(result.left.amplitude_v),
                left_profile.effective_unit,
                "（预览）" if result.left.preview and not result.left.valid else "",
            )
            if result.left.valid or result.left.preview
            else "左 -- %s" % left_profile.effective_unit
        )
        self.right_label.setText(
            "右 %.3f %s%s" % (
                right_profile.convert_amplitude(result.right.amplitude_v),
                right_profile.effective_unit,
                "（预览）" if result.right.preview and not result.right.valid else "",
            )
            if result.right.valid or result.right.preview
            else "右 -- %s" % right_profile.effective_unit
        )
        self.relative_phase_label.setText(
            "右相对左电气相位 %+.1f°" % result.relative_phase_degrees
            if result.relative_phase_degrees is not None else "右相对左电气相位 --°"
        )
        self.left_angle_label.setText(
            "A 同步角 %.1f°" % result.left.shaft_phase_degrees
            if result.left.shaft_phase_degrees is not None else "A 同步角 --°"
        )
        self.right_angle_label.setText(
            "B 同步角 %.1f°" % result.right.shaft_phase_degrees
            if result.right.shaft_phase_degrees is not None else "B 同步角 --°"
        )
        details = result.status
        if result.frequency_hz:
            label = "候选峰预览" if result.preview_only else "候选 1X"
            sync = (
                " | 同步FIO3 %.3f Hz/%d边沿" % (
                    result.synchronous_reference_hz, result.reference_edge_count
                ) if result.synchronous_reference_hz else " | 同步FIO3未通过质量门"
            )
            details = ("%s %.3f Hz | 光电 %.1fs窗 最低/中位/最高 "
                       "%.3f / %.3f / %.3f Hz（%.0f RPM） | "
                       "左 SNR %.1f | 右 SNR %.1f%s | %s") % (
                label, result.frequency_hz, self.settings.window_seconds,
                result.reference_min_hz, result.reference_hz,
                result.reference_max_hz, result.rpm,
                result.left.snr, result.right.snr, sync, result.status
            )
        self.status_label.setText(details)

    def edit_settings(self) -> None:
        if not self.settings_button.isEnabled():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("双路压电 1X 分析参数")
        form = QFormLayout(dialog)
        ppr = QSpinBox()
        ppr.setRange(1, 100)
        ppr.setValue(self.settings.ppr)
        form.addRow("FIO3 光电每转脉冲数", ppr)
        fields = {}

        def number(key: str, label: str, low: float, high: float, decimals: int) -> None:
            box = QDoubleSpinBox()
            box.setDecimals(decimals)
            box.setRange(low, high)
            box.setValue(getattr(self.settings, key))
            box.setKeyboardTracking(False)
            form.addRow(label, box)
            fields[key] = box

        number("search_min_hz", "1X 搜索下限 Hz", 0.01, 20000, 2)
        number("search_max_hz", "1X 搜索上限 Hz", 0.02, 25000, 2)
        number("window_seconds", "独立分析窗 s", 0.5, 10, 1)
        number("rpm_variation_limit", "窗内转频波动上限（0.02=2%）", 0.0001, 0.5, 4)
        bad_updates = QSpinBox()
        bad_updates.setRange(1, 50)
        bad_updates.setValue(self.settings.max_bad_reference_updates)
        form.addRow("光电连续异常容忍次数", bad_updates)
        number("reference_jump_limit", "瞬时转频跳变门限（0.05=5%）", 0.001, 0.5, 3)
        confirm_updates = QSpinBox()
        confirm_updates.setRange(2, 50)
        confirm_updates.setValue(self.settings.reference_change_confirm_updates)
        form.addRow("新转速连续确认次数", confirm_updates)
        number("max_reference_held_fraction", "异常保持占比上限（0.10=10%）", 0, 0.9, 3)
        number("reference_tolerance", "峰频偏离光电参考上限（0.15=15%）", 0.001, 0.9, 3)
        number("minimum_snr", "最小峰值/频带噪声比", 1, 1000, 1)
        number("interchannel_delay_us", "右通道相对左通道采样延迟 μs", 0, 1000, 2)
        number("reference_channel_delay_us", "FIO3 相对左通道采样延迟 μs", 0, 1000, 2)
        number("synchronous_frequency_tolerance", "同步边沿与测频容差（0.05=5%）", 0.001, 0.9, 3)
        number("synchronous_period_variation_limit", "同步周期波动上限（0.05=5%）", 0.001, 0.5, 3)
        number("saturation_spike_fraction_limit", "孤立饱和尖峰容忍占比（0.002=0.2%）", 0, 0.05, 4)
        saturation_run = QSpinBox()
        saturation_run.setRange(0, 100)
        saturation_run.setValue(self.settings.saturation_run_limit_samples)
        form.addRow("连续饱和样本上限", saturation_run)
        direction = QComboBox()
        direction.addItem("正向（角度增加）", 1)
        direction.addItem("反向（角度减少）", -1)
        direction.setCurrentIndex(0 if self.settings.angle_direction == 1 else 1)
        form.addRow("角度方向", direction)
        number("left_angle_offset_degrees", "A 同步角偏置 °", -360, 360, 2)
        number("right_angle_offset_degrees", "B 同步角偏置 °", -360, 360, 2)
        note = QLabel(
            "AIN0/AIN3/FIO3 在同一数据流中按固定顺序采样。A/B 同步角是"
            "1X 电压相位相对 FIO3 下降沿的角度；设置页延迟用于补偿通道轮询时差。"
            "它还不是最终配重角，旋转方向、光电零位、传感器/放大板相移需用试重标定。"
        )
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        form.addRow(buttons)
        buttons.rejected.connect(dialog.reject)

        def save() -> None:
            # Commit text that is still being edited before reading values.
            # With keyboardTracking disabled, value() can otherwise expose the
            # previous committed number when Save is clicked directly.
            ppr.interpretText()
            bad_updates.interpretText()
            confirm_updates.interpretText()
            saturation_run.interpretText()
            for box in fields.values():
                box.interpretText()
            values = asdict(self.settings)
            values.update({key: box.value() for key, box in fields.items()})
            values["ppr"] = ppr.value()
            values["max_bad_reference_updates"] = bad_updates.value()
            values["reference_change_confirm_updates"] = confirm_updates.value()
            values["saturation_run_limit_samples"] = saturation_run.value()
            values["angle_direction"] = int(direction.currentData())
            try:
                settings = PiezoSettings.from_dict(values)
                settings.validate()
            except ValueError as exc:
                self.status_label.setText(str(exc))
                return
            if settings != self.settings:
                self.settings = settings
                self.settings_changed.emit(asdict(settings))
            dialog.accept()

        buttons.accepted.connect(save)
        dialog.exec()
