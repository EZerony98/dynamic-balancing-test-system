"""Main window controller for one-T7 dynamic balancing acquisition."""

from collections import deque
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
from PySide6.QtCore import QMetaObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QMainWindow, QMessageBox, QSpinBox

from drivers.vibration_source import VibrationAcquisitionSource
from models.data import (
    AcquisitionChunk,
    BalancingChunk,
    HardwarePulseMeasurement,
    PiezoChunk,
    TargetToneAnalysis,
)
from services.export_service import ExportService
from services.config_service import ConfigService
from models.piezo_balance import Piezo1XResult, PiezoSettings, PiezoSpectrumAnalysis, SIDES
from models.sensors import (
    SensorProfile,
    sensor_profiles_from_config,
    sensor_profiles_to_dict,
)
from services.signal_processing import (
    HardwarePulsePhaseEstimator,
    PulseFrequencyEstimator,
    PulsePhaseEstimator,
    ac_rms,
)
from services.spectrum import sampling_budget
from ui.ui_main import Ui_MainWindow
from ui.pages.production_pages import (
    AccessPage,
    DualPlaneBalancePage,
    HistoryPage,
    LogPage,
    SystemSettingsPage,
    TemplateQcPage,
    TrialCalibrationPage,
)
from ui.widgets.measurement_widgets import BalancingDataCard, PlotPanel
from ui.widgets.piezo_balance_panel import PiezoBalancePanel
from workers.acquisition_worker import AcquisitionWorker
from workers.piezo_record_worker import PiezoRecordWorker


class MainWindow(QMainWindow):
    start_requested = Signal(str)
    stop_requested = Signal()
    target_frequency_requested = Signal(float)
    record_start_requested = Signal(object)
    record_stop_requested = Signal()
    record_waveform_requested = Signal(object)
    record_spectrum_requested = Signal(object)
    piezo_settings_requested = Signal(object)
    sensor_settings_requested = Signal(object)
    record_one_x_requested = Signal(object)

    def __init__(self, config: Dict[str, Dict[str, Any]], project_dir: Path) -> None:
        super().__init__()
        self.config = config
        self.project_dir = Path(project_dir)
        self.device_config = config["devices"]["acquisition_device"]
        self.spectrum_config = config["process"]["spectrum"]
        self.record_duration_seconds = max(
            0.1, float(self.spectrum_config.get("record_duration_seconds", 2.0))
        )
        self.phase_config = config["process"].get("phase", {})
        self.sensor_profiles = sensor_profiles_from_config(
            config["process"].get("sensors", {})
        )
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.setWindowTitle(config["app"]["application"]["window_title"])
        logo_path = self.project_dir / "assets" / "logo.svg"
        if logo_path.exists():
            self.setWindowIcon(QIcon(str(logo_path)))
            self.ui.brandLogoLabel.setPixmap(QPixmap(str(logo_path)))
        self.ui.targetFrequencySpinBox.setValue(
            float(self.spectrum_config.get("target_frequency", 154.0))
        )
        self.ui.targetFrequencySpinBox.setMaximum(
            float(self.device_config["piezo_scan_rate"]) / 2.0 - 0.01
        )

        self.acquisition_running = False
        self.spectrum_enabled = False
        self.active_mode: Optional[str] = None
        self._pending_mode: Optional[str] = None
        self._last_acquisition_error: Optional[str] = None
        self.latest_balancing: Optional[BalancingChunk] = None
        self.latest_piezo: Optional[PiezoChunk] = None
        self.latest_analysis: Optional[PiezoSpectrumAnalysis] = None
        self.latest_one_x_result: Optional[Piezo1XResult] = None
        self.latest_data_mode: Optional[str] = None
        self.raw_display_seconds = float(
            self.spectrum_config.get("raw_display_seconds", 0.2)
        )
        self.raw_display_max_seconds = float(
            self.spectrum_config.get("raw_display_max_seconds", 5.0)
        )
        self._laser_wave_chunks = deque()
        self._laser_wave_sample_count = 0
        self._laser_wave_scan_rate = 50_000.0
        self._laser_wave_update_counter = 0
        self.rpm_history = np.zeros(120, dtype=float)
        self.motor_hall_rpm_history = np.zeros(120, dtype=float)
        self.smoothed_force_a_rms: Optional[float] = None
        self.smoothed_force_b_rms: Optional[float] = None
        self.speed_estimator = PulseFrequencyEstimator(window_seconds=1.0, smoothing=0.15)
        self.position_a_estimator = PulseFrequencyEstimator(window_seconds=0.5, smoothing=0.22)
        self.position_b_estimator = PulseFrequencyEstimator(window_seconds=0.5, smoothing=0.22)
        phase_window = float(self.phase_config.get("window_seconds", 1.0))
        phase_direction = int(self.phase_config.get("direction", 1))
        reference_edge = str(self.phase_config.get("reference_edge", "rising"))
        target_edge = str(self.phase_config.get("target_edge", "rising"))
        self.position_a_stream_phase_estimator = PulsePhaseEstimator(
            window_seconds=phase_window,
            direction=phase_direction,
            offset_degrees=float(self.phase_config.get("a_offset_degrees", 0.0)),
            reference_edge=reference_edge,
            target_edge=target_edge,
        )
        self.position_b_stream_phase_estimator = PulsePhaseEstimator(
            window_seconds=phase_window,
            direction=phase_direction,
            offset_degrees=float(self.phase_config.get("b_offset_degrees", 0.0)),
            reference_edge=reference_edge,
            target_edge=target_edge,
        )
        phase_hold = float(self.phase_config.get("hold_seconds", 0.6))
        phase_stale = float(self.phase_config.get("stale_seconds", 0.25))
        self.position_a_hardware_phase_estimator = HardwarePulsePhaseEstimator(
            direction=phase_direction,
            offset_degrees=float(self.phase_config.get("a_offset_degrees", 0.0)),
            hold_seconds=phase_hold,
            stale_seconds=phase_stale,
        )
        self.position_b_hardware_phase_estimator = HardwarePulsePhaseEstimator(
            direction=phase_direction,
            offset_degrees=float(self.phase_config.get("b_offset_degrees", 0.0)),
            hold_seconds=phase_hold,
            stale_seconds=phase_stale,
        )
        self.stable_speed_hz = 0.0
        self.speed_bad_updates = 0
        self.stable_motor_hall_hz = 0.0
        self.motor_hall_bad_updates = 0
        self.motor_hall_pending_hz = []
        self.latest_motor_hall_summary = {
            "rpm": 0.0,
            "fio3_raw_frequency_hz": 0.0,
            "fio3_corrected_frequency_hz": 0.0,
            "fio3_smoothed_frequency_hz": 0.0,
            "ppr": 1,
            "speed_quality": "等待光电脉冲",
        }

        export_dir = self.project_dir / config["app"]["paths"]["exports"]
        self.export_service = ExportService(export_dir)
        self.recording_active = False
        self._record_spectrum_counter = 0
        self._build_measurement_widgets()
        settings = PiezoSettings.from_dict(config["process"].get("piezo_balance", {}))
        settings.validate()
        self.balance_panel = PiezoBalancePanel(
            settings, sensor_profiles_to_dict(self.sensor_profiles)
        )
        self.ui.balancePanelLayout.addWidget(self.balance_panel)
        self.motor_hall_ppr_spin_box.setValue(settings.ppr)
        self._update_pulse_source_labels()
        self.analysis_mode_combo = QComboBox()
        self.analysis_mode_combo.addItem("候选 1X（最接近参考转频）", "search")
        self.analysis_mode_combo.addItem("手动指定频率（仅观察）", "manual")
        self.analysis_mode_combo.setCurrentIndex(self.analysis_mode_combo.findData(settings.analysis_mode))
        self.analysis_mode_combo.setMinimumWidth(210)
        self.ui.analysisModeLayout.addWidget(self.analysis_mode_combo)
        self._build_application_pages()
        self._apply_sensor_profiles_to_ui()
        self._update_laser_units()
        self._apply_style()
        self._connect_ui()
        self._start_worker_thread()
        self._start_record_thread(export_dir)
        self._show_ready_state()

    def _build_measurement_widgets(self) -> None:
        self.laser_wave = PlotPanel(
            "左压电 · 实时电压（AIN0）",
            "放大板输出 (mV)",
            "时间 (s)",
            "#1769aa",
        )
        self.right_wave = PlotPanel(
            "右压电 · 实时电压（AIN3）",
            "放大板输出 (mV)",
            "时间 (s)",
            "#c24156",
        )
        self.laser_fft = PlotPanel(
            "左压电 · 完整 FFT",
            "幅值 (mV)",
            "频率 (Hz)",
            "#d97706",
        )
        self.right_fft = PlotPanel(
            "右压电 · 完整 FFT",
            "幅值 (mV)",
            "频率 (Hz)",
            "#c24156",
        )
        self.target_tone_plot = PlotPanel(
            "左压电 · 候选 1X 正弦分量",
            "提取幅值 (mV)",
            "时间 (s)",
            "#7c3aed",
            with_metrics=True,
        )
        self.right_tone_plot = PlotPanel(
            "右压电 · 候选 1X 正弦分量",
            "提取幅值 (mV)",
            "时间 (s)",
            "#c24156",
            with_metrics=True,
        )
        self.raw_duration_spin_box = QDoubleSpinBox()
        self.raw_duration_spin_box.setObjectName("rawDurationSpinBox")
        self.raw_duration_spin_box.setDecimals(2)
        self.raw_duration_spin_box.setRange(0.05, self.raw_display_max_seconds)
        self.raw_duration_spin_box.setSingleStep(0.05)
        self.raw_duration_spin_box.setSuffix(" s")
        self.raw_duration_spin_box.setValue(self.raw_display_seconds)
        self.raw_duration_spin_box.setKeyboardTracking(False)
        self.raw_duration_spin_box.setFixedWidth(88)
        self.laser_wave.add_header_control("显示时长", self.raw_duration_spin_box)

        self.fft_limit_spin_box = QSpinBox()
        self.fft_limit_spin_box.setObjectName("fftLimitSpinBox")
        self.fft_limit_spin_box.setRange(
            100, int(self.spectrum_config.get("maximum_frequency_limit", 50_000))
        )
        self.fft_limit_spin_box.setSingleStep(100)
        self.fft_limit_spin_box.setSuffix(" Hz")
        self.fft_limit_spin_box.setValue(
            int(self.spectrum_config.get("maximum_frequency", 300))
        )
        self.fft_limit_spin_box.setKeyboardTracking(False)
        self.fft_limit_spin_box.setFixedWidth(112)
        self.laser_fft.add_header_control("频率上限", self.fft_limit_spin_box)

        maximum_frequency = float(self.spectrum_config["maximum_frequency"])
        for panel in (self.laser_fft, self.right_fft):
            panel.plot.setXRange(0, maximum_frequency, padding=0)
            panel.set_frequency_marker(self.ui.targetFrequencySpinBox.value())
        # Keep the three views needed to judge a balancing result together:
        # raw waveform, full spectrum, and the fitted rotor-1X sine component.
        for row, panels in enumerate((
            (self.laser_wave, self.laser_fft, self.target_tone_plot),
            (self.right_wave, self.right_fft, self.right_tone_plot),
        )):
            for column, panel in enumerate(panels):
                self.ui.laserPlotsLayout.addWidget(panel, row, column)
            self.ui.laserPlotsLayout.setRowStretch(row, 1)
        for column in range(3):
            self.ui.laserPlotsLayout.setColumnStretch(column, 1)

        self.speed_card = BalancingDataCard("转速", "#0875c1", "RPM", True)
        self.motor_hall_speed_card = BalancingDataCard(
            "FIO3 脉冲转速", "#b45309", "RPM", True
        )
        self.motor_hall_ppr_spin_box = QSpinBox()
        self.motor_hall_ppr_spin_box.setObjectName("motorHallPprSpinBox")
        self.motor_hall_ppr_spin_box.setRange(1, 100)
        self.motor_hall_ppr_spin_box.setValue(1)
        self.motor_hall_ppr_spin_box.setKeyboardTracking(False)
        self.motor_hall_ppr_spin_box.setFixedWidth(56)
        self.motor_hall_speed_card.add_header_control(
            "PPR", self.motor_hall_ppr_spin_box
        )
        self.position_a_card = BalancingDataCard(
            "A 脉冲", "#008b8b", "Hz", False, with_phase=True
        )
        self.position_b_card = BalancingDataCard(
            "B 脉冲", "#7651b5", "Hz", False, with_phase=True
        )
        self.force_a_card = BalancingDataCard("左压电（AC RMS）", "#168653", "mV", True)
        self.force_b_card = BalancingDataCard("右压电（AC RMS）", "#c24156", "mV", True)
        for card in (self.speed_card, self.position_a_card, self.position_b_card):
            card.setParent(self)
            card.hide()
        for card in (
            self.motor_hall_speed_card,
            self.force_a_card,
            self.force_b_card,
        ):
            self.ui.balancingCardsLayout.addWidget(card, 1)
        self.ui.measurementPageLayout.setStretch(5, 1)

    def _build_application_pages(self) -> None:
        """Add the non-duplicated production pages around the real dashboard."""
        self.dual_plane_page = DualPlaneBalancePage()
        self.calibration_page = TrialCalibrationPage(self.project_dir / "data")
        self.template_page = TemplateQcPage(self.project_dir / "data")
        self.history_page = HistoryPage(self.project_dir / "exports")
        self.settings_page = SystemSettingsPage(self.config, self.sensor_profiles)
        self.access_page = AccessPage()
        self.log_page = LogPage(self.project_dir / "logs" / "app.log")
        pages = (
            ("实时测量", self.ui.measurementPage),
            ("双面动平衡", self.dual_plane_page),
            ("试重标定", self.calibration_page),
            ("测量模板与 QC", self.template_page),
            ("测试记录", self.history_page),
            ("系统设置", self.settings_page),
            ("用户与权限", self.access_page),
            ("系统日志", self.log_page),
        )
        self.ui.navigationList.clear()
        for index, (title, page) in enumerate(pages):
            self.ui.navigationList.addItem(title)
            if index:
                self.ui.contentStack.addWidget(page)
        self.ui.navigationList.currentRowChanged.connect(self._navigation_changed)
        self.ui.navigationList.setCurrentRow(0)
        self.settings_page.open_analysis_settings.connect(
            self.balance_panel.edit_settings
        )
        self.settings_page.sensor_settings_changed.connect(
            self._sensor_settings_changed
        )
        self.template_page.templates_changed.connect(self._reload_measurement_templates)
        self._reload_measurement_templates()

    def _reload_measurement_templates(self) -> None:
        current = self.ui.measurementTemplateComboBox.currentText()
        self.ui.measurementTemplateComboBox.clear()
        self.ui.measurementTemplateComboBox.addItem("未选择模板", None)
        for record in self.template_page.records():
            self.ui.measurementTemplateComboBox.addItem(
                str(record.get("name", "未命名模板")), record
            )
        index = self.ui.measurementTemplateComboBox.findText(current)
        self.ui.measurementTemplateComboBox.setCurrentIndex(max(0, index))

    def _sensor_section_title(self, enabled: bool) -> str:
        active = [profile for profile in self.sensor_profiles.values() if profile.enabled]
        if len(active) == 2 and all(profile.sensor_type == "piezo" for profile in active):
            prefix = "左右压电实时测量"
        elif len(active) == 1:
            side = next(
                side for side in SIDES if self.sensor_profiles[side].enabled
            )
            prefix = "%s侧%s单传感器测试" % (
                "左" if side == "left" else "右",
                self.sensor_profiles[side].type_label,
            )
        else:
            prefix = "左右振动传感器实时测量"
        accelerometers = sum(
            profile.sensor_type == "accelerometer" for profile in active
        )
        if not accelerometers:
            source = "AIN0 / AIN3 + FIO3 转子光电"
            suffix = "（同流同步）" if enabled else ""
        elif accelerometers == len(active):
            source = "NI-9234 加速度 + LabJack FIO3 转速"
            suffix = "（NI 双路同步；转子角异步）" if enabled else ""
        else:
            source = "NI-9234 加速度 + LabJack 压电/FIO3"
            suffix = "（跨设备相位禁用）" if enabled else ""
        return "%s · %s%s" % (prefix, source, suffix)

    def _apply_sensor_profiles_to_ui(self) -> None:
        channels = self.device_config["channels"]
        panels = {
            "left": (self.laser_wave, self.laser_fft, self.force_a_card),
            "right": (self.right_wave, self.right_fft, self.force_b_card),
        }
        for side in SIDES:
            profile = self.sensor_profiles[side]
            side_name = "左" if side == "left" else "右"
            channel = (
                "NI-9234/%s" % self.device_config.get("ni9234", {}).get(
                    "%s_channel" % side, "ai0" if side == "left" else "ai1"
                )
                if profile.sensor_type == "accelerometer" else
                channels["piezo_left" if side == "left" else "piezo_right"]
            )
            wave, fft, card = panels[side]
            title = "%s侧%s" % (side_name, profile.type_label)
            wave.set_title("%s · 实时波形（%s）" % (title, channel))
            fft.set_title("%s · 完整 FFT" % title)
            card.title_label.setText("%s（AC RMS）" % title)
            card.unit_label.setText(profile.effective_unit)
            if not profile.enabled:
                card.update_value("--", "未安装 / 不参与 1X 分析", np.zeros(120))
                wave.set_data(np.array([]), np.array([]))
                fft.set_data(np.array([]), np.array([]))
        self.balance_panel.set_sensor_profiles(
            sensor_profiles_to_dict(self.sensor_profiles)
        )
        self.dual_plane_page.set_sensor_profiles(self.sensor_profiles)
        self.ui.laserSectionLabel.setText(
            self._sensor_section_title(self.spectrum_enabled)
        )

    def _sensor_settings_changed(self, values: dict) -> None:
        if self.acquisition_running:
            QMessageBox.warning(
                self, "请先停止测量",
                "传感器类型会切换 LabJack/NI 采集设备，请先停止当前测量再保存。",
            )
            self.settings_page.set_sensor_profiles(self.sensor_profiles)
            return
        try:
            profiles = sensor_profiles_from_config(values)
        except ValueError as exc:
            QMessageBox.warning(self, "传感器设置无效", str(exc))
            return
        self.sensor_profiles = profiles
        serialized = sensor_profiles_to_dict(profiles)
        self.config["process"]["sensors"] = serialized
        self._apply_sensor_profiles_to_ui()
        self._update_laser_units()
        self._reset_laser_display_buffer()
        self.latest_analysis = None
        self.latest_one_x_result = None
        self.sensor_settings_requested.emit(serialized)
        try:
            ConfigService(self.project_dir / "config").save_section(
                "process", "sensors", serialized
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self, "传感器设置保存失败",
                "本次已应用，但不能保存供下次启动：%s" % exc,
            )
            return
        self.statusBar().showMessage(
            "左右传感器设置已保存并应用，正在重建 1X 分析窗"
        )

    def _active_results_valid(self, result: Piezo1XResult) -> bool:
        return all(
            getattr(result, side).valid
            for side in SIDES if self.sensor_profiles[side].enabled
        )

    def _qc_status(self, result: Piezo1XResult) -> Optional[str]:
        template = self.ui.measurementTemplateComboBox.currentData()
        if not isinstance(template, dict):
            return None
        left_limit = float(template.get("left_limit_mv", 0.0))
        right_limit = float(template.get("right_limit_mv", 0.0))
        required_limits = []
        if self.sensor_profiles["left"].enabled:
            required_limits.append(left_limit)
        if self.sensor_profiles["right"].enabled:
            required_limits.append(right_limit)
        if any(limit <= 0 for limit in required_limits):
            return "QC 待设置 · 模板限值尚未填写"
        target_rpm = float(template.get("target_rpm", 0.0))
        tolerance = float(template.get("speed_tolerance_percent", 0.0)) / 100.0
        left_mv = result.left.amplitude_v * 1000.0
        right_mv = result.right.amplitude_v * 1000.0
        speed_ok = target_rpm <= 0 or abs(result.rpm / target_rpm - 1.0) <= tolerance
        left_ok = not self.sensor_profiles["left"].enabled or left_mv <= left_limit
        right_ok = not self.sensor_profiles["right"].enabled or right_mv <= right_limit
        if speed_ok and left_ok and right_ok:
            return "QC 合格 · 可记录或导出"
        failures = []
        if not speed_ok:
            failures.append("转速")
        if not left_ok:
            failures.append("左 1X")
        if not right_ok:
            failures.append("右 1X")
        return "QC 不合格 · " + "、".join(failures) + "超限"

    def _navigation_changed(self, index: int) -> None:
        if index < 0 or index >= self.ui.contentStack.count():
            return
        self.ui.contentStack.setCurrentIndex(index)
        if index == 4:
            self.history_page.refresh()
        elif index == 7:
            self.log_page.refresh()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f5f6f8; color: #15171a; }
            #topFrame, #laserHeaderFrame, #workflowFrame, #summaryFrame,
            #plotPanel, #dataCard, #supportPanel, #supportCard {
                background: #ffffff; border: 1px solid #dfe3e8; border-radius: 8px;
            }
            #topFrame { border-top: 3px solid #d5122d; }
            #titleLabel { font-size: 22px; font-weight: 800; color: #111318; }
            #subtitleLabel { color: #687386; font-size: 12px; }
            #balancingDeviceLabel { color: #008f60; font-weight: 700; }
            #laserDeviceLabel { color: #687386; font-weight: 600; }
            #laserSectionLabel, #balancingSectionLabel, #workflowTitleLabel,
            #panelTitle, #cardTitle {
                font-size: 14px; font-weight: 700;
            }
            #laserSectionLabel { color: #d5122d; font-size: 16px; }
            #navigationFrame { background: #ffffff; border-right: 1px solid #dfe3e8; }
            #navigationTitleLabel { color: #98a2b3; font-weight: 700; padding: 4px 8px; }
            #navigationHintLabel { color: #98a2b3; padding: 8px; }
            #navigationList { background: transparent; border: 0; outline: 0; }
            #navigationList::item { min-height: 38px; border-radius: 7px; padding: 3px 12px; color: #344054; }
            #navigationList::item:hover { background: #fff5f6; color: #ad0d24; }
            #navigationList::item:selected { background: #fff0f2; color: #d5122d; border-left: 4px solid #d5122d; font-weight: 700; }
            #PageTitle { font-size: 24px; font-weight: 800; color: #15171a; }
            #PageHint { color: #667085; font-size: 13px; }
            #supportCardTitle, #supportUnit { color: #667085; }
            #workflowStepsLabel { color: #667085; font-weight: 600; }
            #workflowStatusLabel {
                color: #9f0d23; background: #fff1f3; border: 1px solid #ffc8d0;
                border-radius: 11px; padding: 4px 11px; font-weight: 700;
            }
            #laserMetricsLabel { color: #344054; font-weight: 700; padding-left: 12px; }
            #unitLabel, #detailLabel, #productIdLabel, #targetFrequencyLabel,
            #plotControlLabel, #cardControlLabel { color: #667085; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background: #ffffff; border: 1px solid #cfd5dd; border-radius: 5px;
                min-height: 28px; padding: 0 6px;
            }
            QPushButton { background: #ffffff; border: 1px solid #c6ccd5; border-radius: 6px;
                          padding: 8px 14px; font-weight: 700; }
            QPushButton:hover { background: #fff5f6; border-color: #d5122d; }
            #spectrumButton { background: #d5122d; border-color: #d5122d; color: #ffffff; }
            #Primary { background: #d5122d; border-color: #d5122d; color: #ffffff; }
            #spectrumButton:hover { background: #ad0d24; border-color: #ad0d24; }
            #Primary:hover { background: #ad0d24; border-color: #ad0d24; }
            #stopButton { color: #d5122d; border-color: #f0a0ab; }
            QPushButton:checked { background: #fff1f3; border-color: #d5122d; color: #ad0d24; }
            QPushButton:disabled { background: #eceff3; color: #98a2b3; border-color: #e1e5ea; }
            QStatusBar { background: #ffffff; border-top: 1px solid #dfe3e8; color: #667085; }
            """
        )

    def _connect_ui(self) -> None:
        self.balance_panel.settings_changed.connect(self._balance_settings_changed)
        self.analysis_mode_combo.currentIndexChanged.connect(self._analysis_mode_changed)
        self.motor_hall_ppr_spin_box.valueChanged.connect(self._hall_ppr_changed)
        self.ui.startButton.clicked.connect(self.start_acquisition)
        self.ui.stopButton.clicked.connect(self.stop_acquisition)
        self.ui.spectrumButton.clicked.connect(self.toggle_spectrum)
        self.ui.spectrumRecordButton.clicked.connect(self.save_fixed_recording)
        self.ui.targetFrequencySpinBox.valueChanged.connect(
            self._target_frequency_changed
        )
        self.raw_duration_spin_box.valueChanged.connect(
            self._raw_display_duration_changed
        )
        self.fft_limit_spin_box.valueChanged.connect(
            self._fft_display_limit_changed
        )
        self.ui.exportButton.clicked.connect(self.export_current_data)
        self.ui.productIdEdit.returnPressed.connect(self.toggle_spectrum)
        self._start_shortcut = QShortcut(QKeySequence("F3"), self)
        self._start_shortcut.activated.connect(self.toggle_spectrum)
        self._stop_shortcut = QShortcut(QKeySequence(Qt.Key_Delete), self)
        self._stop_shortcut.activated.connect(self.stop_acquisition)

    def _start_worker_thread(self) -> None:
        self.thread = QThread(self)
        self.worker = AcquisitionWorker(
            VibrationAcquisitionSource(
                self.device_config,
                sensor_profiles_to_dict(self.sensor_profiles),
            ),
            fft_size=int(self.spectrum_config["fft_size"]),
            target_frequency=float(
                self.spectrum_config.get("target_frequency", 154.0)
            ),
            target_window_seconds=float(
                self.spectrum_config.get("target_window_seconds", 1.5)
            ),
            analysis_interval_ms=int(
                self.spectrum_config.get("analysis_interval_ms", 200)
            ),
            piezo_settings=asdict(self.balance_panel.settings),
            sensor_profiles=sensor_profiles_to_dict(self.sensor_profiles),
        )
        self.worker.moveToThread(self.thread)
        self.start_requested.connect(self.worker.start)
        self.stop_requested.connect(self.worker.stop)
        self.target_frequency_requested.connect(self.worker.set_target_frequency)
        self.worker.connected.connect(self._device_connected)
        self.worker.data_ready.connect(self._update_acquisition)
        self.worker.piezo_analysis_ready.connect(self._update_laser_spectrum)
        self.worker.piezo_one_x_ready.connect(self._update_piezo_one_x)
        self.piezo_settings_requested.connect(self.worker.set_piezo_settings)
        self.sensor_settings_requested.connect(self.worker.set_sensor_profiles)
        self.worker.stream_started.connect(self._stream_started)
        self.worker.stream_stopped.connect(self._stream_stopped)
        self.worker.startup_failed.connect(self._acquisition_start_failed)
        self.worker.error.connect(self._show_error)
        self.thread.start()

    def _start_record_thread(self, export_dir: Path) -> None:
        self.record_thread = QThread(self)
        self.record_worker = PiezoRecordWorker(export_dir)
        self.record_worker.moveToThread(self.record_thread)
        self.record_start_requested.connect(self.record_worker.start)
        self.record_stop_requested.connect(self.record_worker.stop)
        self.record_waveform_requested.connect(self.record_worker.append_waveform)
        self.record_spectrum_requested.connect(self.record_worker.append_spectrum)
        self.record_one_x_requested.connect(self.record_worker.append_one_x)
        self.record_worker.started.connect(self._recording_started)
        self.record_worker.stopped.connect(self._recording_stopped)
        self.record_worker.error.connect(self._show_record_error)
        self.record_thread.start()

    def _fio3_label(self) -> str:
        divisor = float(self.device_config.get("optical_frequency_divisor", 1.0))
        edge = str(self.device_config.get("optical_edge", "falling")).lower()
        edge_label = "下降沿" if edge == "falling" else "上升沿"
        base = "FIO3 光电(%s)" % edge_label
        return "%s(原始÷%g)" % (base, divisor) if divisor != 1.0 else base

    def _update_pulse_source_labels(self) -> None:
        label = "转子光电转速"
        self.motor_hall_speed_card.title_label.setText(label)
        self.ui.balancingSectionLabel.setText(
            "实时测量结果"
        )
        self.motor_hall_ppr_spin_box.setToolTip(
            "PPR 是被测转子每转产生的有效光电脉冲数；当前每转触发一次，设为 1。\n"
            "FIO3 当前使用连续下降沿测频，以避免慢上升沿附近的重复触发。\n"
            "现场 FIO3 读数另按设备参数除以 4 修正，与 PPR 和 4 周期平均无关。")

    def _show_ready_state(self) -> None:
        self.ui.balancingDeviceLabel.setText("LabJack T7  ● 未连接")
        self.ui.laserDeviceLabel.setText(
            "AIN0 左 / AIN3 右 + %s | %.1f kScan/s"
            % (self._fio3_label(), float(self.device_config["piezo_scan_rate"]) / 1000.0)
        )
        self.ui.spectrumButton.setText("开始测量  F3")
        self.ui.spectrumButton.setEnabled(True)
        self.ui.stopButton.setText("停止  Del")
        self.ui.stopButton.setEnabled(False)
        self.ui.spectrumRecordButton.setEnabled(False)
        self.ui.spectrumRecordButton.setText(self._record_button_text())
        self.ui.laserMetricsLabel.setText(
            "转速 -- RPM | 左 1X -- %s | 右 1X -- %s | 右相对左相位 --°" % (
                self.sensor_profiles["left"].effective_unit,
                self.sensor_profiles["right"].effective_unit,
            )
        )
        self.ui.workflowStatusLabel.setText("待机 · 输入工件编号后开始")
        self.statusBar().showMessage("真实硬件待机 | AIN0/AIN3 振动传感器 + FIO3 光电同步")

    def start_acquisition(self) -> None:
        self._request_mode("piezo")

    def _request_mode(self, mode: str) -> None:
        if self.acquisition_running:
            if self.active_mode == mode:
                return
            if self.active_mode == "piezo" and self.recording_active:
                self._stop_laser_recording()
            self._pending_mode = mode
            self.ui.startButton.setEnabled(False)
            self.ui.spectrumButton.setEnabled(False)
            self.statusBar().showMessage("正在安全切换 T7 采集模式，请稍候…")
            self.ui.workflowStatusLabel.setText("正在切换采集模式")
            self.stop_requested.emit()
            return
        self._start_mode(mode)

    def _start_mode(self, mode: str) -> None:
        self._last_acquisition_error = None
        spectrum_mode = mode == "piezo"
        if spectrum_mode:
            self.ui.navigationList.setCurrentRow(0)
        scan_rate = float(
            self.device_config[
                "piezo_scan_rate" if spectrum_mode else "balancing_scan_rate"
            ]
        )
        ok, message = sampling_budget(
            scan_rate, analog=2, digital=1 if spectrum_mode else 3
        )
        if not ok:
            QMessageBox.warning(self, "采样预算超限", message)
            return
        if spectrum_mode:
            self.latest_piezo = None
            self.latest_analysis = None
            self.latest_one_x_result = None
            self.smoothed_force_a_rms = None
            self.smoothed_force_b_rms = None
            for card in (self.speed_card, self.position_a_card, self.position_b_card):
                card.update_value("--", "当前流程不采集旧脉冲通道")
            self.position_a_card.update_phase(None, "未启用")
            self.position_b_card.update_phase(None, "未启用")
            self._reset_laser_display_buffer()
            self._update_laser_units()
            for panel in (
                self.laser_wave, self.right_wave,
                self.laser_fft, self.right_fft,
                self.target_tone_plot, self.right_tone_plot,
            ):
                panel.set_data(np.array([]), np.array([]))
            waiting = "正在建立 %.1f s 稳定分析窗…" % (
                self.balance_panel.settings.window_seconds
                if self.balance_panel.settings.analysis_mode == "search"
                else float(self.spectrum_config.get("target_window_seconds", 1.5))
            )
            for panel in (self.target_tone_plot, self.right_tone_plot):
                panel.set_metrics(waiting)
            self.ui.laserMetricsLabel.setText("左右候选 1X 正在建立分析窗…")
        else:
            self._reset_measurement_state()
        self._reset_motor_hall_state()
        self.balance_panel.clear(
            "正在建立已启用传感器的独立分析窗…" if spectrum_mode else
            "兼容采集模式运行中"
        )
        self.balance_panel.set_running(True)
        self.analysis_mode_combo.setEnabled(False)
        self.motor_hall_ppr_spin_box.setEnabled(False)
        self._apply_spectrum_mode(spectrum_mode)
        self.start_requested.emit(mode)
        self.ui.startButton.setEnabled(False)
        self.ui.stopButton.setEnabled(True)
        self.ui.spectrumButton.setEnabled(False)
        self.ui.workflowStatusLabel.setText("正在连接设备…")
        active_accelerometer = spectrum_mode and any(
            profile.enabled and profile.sensor_type == "accelerometer"
            for profile in self.sensor_profiles.values()
        )
        acquisition_label = (
            "NI-9234 IEPE + LabJack FIO3 转速"
            if active_accelerometer else
            ("AIN0/AIN3 振动传感器" if spectrum_mode else "兼容通道")
        )
        self.statusBar().showMessage(
            "正在连接 %s | %s / %.1f kScan/s"
            % (acquisition_label, self._fio3_label(), scan_rate / 1000.0)
        )

    def stop_acquisition(self) -> None:
        self._pending_mode = None
        if self.recording_active:
            self._stop_laser_recording()
        self.stop_requested.emit()

    def toggle_spectrum(self) -> None:
        if self.acquisition_running and self.active_mode == "piezo":
            self._pending_mode = None
            if self.recording_active:
                self._stop_laser_recording()
            self.ui.spectrumButton.setEnabled(False)
            self.ui.workflowStatusLabel.setText("正在停止…")
            self.statusBar().showMessage("正在停止测量…")
            self.stop_requested.emit()
            return
        self._request_mode("piezo")

    def _apply_spectrum_mode(self, enabled: bool) -> None:
        self.spectrum_enabled = enabled
        self.ui.spectrumButton.setText("测量中" if enabled else "开始测量  F3")
        self.ui.spectrumRecordButton.setEnabled(enabled and self.acquisition_running)
        self.ui.laserSectionLabel.setText(self._sensor_section_title(enabled))

    def save_fixed_recording(self) -> None:
        if (
            self.recording_active or not self.spectrum_enabled
            or not self.acquisition_running
        ):
            return
        self.ui.spectrumRecordButton.setEnabled(False)
        has_accelerometer = any(
            profile.enabled and profile.sensor_type == "accelerometer"
            for profile in self.sensor_profiles.values()
        )
        self.record_start_requested.emit({
            "device": self.device_config,
            "spectrum": self.spectrum_config,
            "piezo_settings": asdict(self.balance_panel.settings),
            "sensors": sensor_profiles_to_dict(self.sensor_profiles),
            "product_id": self.ui.productIdEdit.text().strip(),
            "measurement_template": self.ui.measurementTemplateComboBox.currentText(),
            "record_duration_seconds": self.record_duration_seconds,
            "raw_unit": "V",
            "phase_reference": (
                "asynchronous_labjack_fio3_falling_edge"
                if has_accelerometer else
                "sample_aligned_fio3_falling_edge"
            ),
        })

    def _stop_laser_recording(self) -> None:
        if self.recording_active:
            self.record_stop_requested.emit()
        else:
            self._recording_stopped("")

    def _recording_started(self, directory: str) -> None:
        self.recording_active = True
        self._record_spectrum_counter = 0
        self.ui.spectrumRecordButton.setEnabled(False)
        self.ui.spectrumRecordButton.setText(
            "正在保存 %.1f 秒…" % self.record_duration_seconds
        )
        self.ui.workflowStatusLabel.setText("正在保存固定时长测量数据")
        self.statusBar().showMessage(
            "正在截取 %.1f 秒真实测量数据 | %s"
            % (self.record_duration_seconds, directory)
        )

    def _recording_stopped(self, directory: str) -> None:
        self.recording_active = False
        self.ui.spectrumRecordButton.setText(self._record_button_text())
        self.ui.spectrumRecordButton.setEnabled(
            self.acquisition_running and self.spectrum_enabled
        )
        if directory:
            self.ui.workflowStatusLabel.setText("固定时长数据已保存 · 可继续测量")
            self.statusBar().showMessage(
                "%.1f 秒测量记录已保存 | %s"
                % (self.record_duration_seconds, directory)
            )
            self.history_page.refresh()

    def _record_button_text(self) -> str:
        return "保存 %.1f 秒数据" % self.record_duration_seconds

    def _device_connected(self, info: Dict[str, Any]) -> None:
        self.ui.balancingDeviceLabel.setText(
            "%s  %s  ● 已连接" % (info.get("name", "采集设备"), info["serial"])
        )
        self.ui.balancingDeviceLabel.setStyleSheet("color:#008f60;font-weight:700")
        self.ui.workflowStatusLabel.setText("设备已连接 · 等待稳速")

    def _update_acquisition(self, chunk: AcquisitionChunk) -> None:
        if chunk.balancing is not None:
            self.latest_balancing = chunk.balancing
            self.latest_data_mode = "balancing"
            self._update_balancing(
                chunk.balancing,
                chunk.hardware_frequencies,
                chunk.hardware_pulses,
            )
        if chunk.piezo is not None:
            self.latest_piezo = chunk.piezo
            self.latest_data_mode = "piezo"
            self._update_laser_wave(chunk.piezo)
        status = chunk.piezo if chunk.piezo is not None else chunk.balancing
        if status is None:
            return
        self._update_motor_hall_speed(
            float(chunk.hardware_frequencies.get("motor_hall", 0.0)),
            float(chunk.hardware_raw_frequencies.get(
                "motor_hall", chunk.hardware_frequencies.get("motor_hall", 0.0)
            )),
        )
        if chunk.piezo is not None and self.recording_active:
            self.record_waveform_requested.emit({
                "chunk": chunk,
                "summary": self._measurement_summary(chunk.piezo),
            })
        self.statusBar().showMessage(
            "采集中 | %s | Backlog %d/%d | 丢失样本 %d"
            % (
                "AIN0/AIN3 振动通道各 %.1f kScan/s + %s转速" % (status.scan_rate / 1000.0, self._fio3_label())
                if chunk.piezo is not None
                else "兼容采集 + %s转速" % self._fio3_label(),
                status.device_backlog,
                status.ljm_backlog,
                status.dropped_samples,
            )
        )

    def _update_motor_hall_speed(
        self, measured_hz: float, raw_hz: Optional[float] = None
    ) -> None:
        """FIO3 measures the optical rotor or the legacy motor Hall source."""
        raw_hz = measured_hz if raw_hz is None else float(raw_hz)
        frequency, quality = self._validated_motor_hall_speed(measured_hz)
        ppr = max(1, int(self.motor_hall_ppr_spin_box.value()))
        rpm = frequency * 60.0 / ppr
        self.motor_hall_rpm_history = self._append_history(
            self.motor_hall_rpm_history, rpm
        )
        self.motor_hall_speed_card.update_value(
            "%.0f" % rpm,
            "原始 %.0f / 修正 %.1f / 平滑 %.1f Hz | PPR %d | %s" % (
                raw_hz, measured_hz, frequency, ppr, quality),
            self.motor_hall_rpm_history,
        )
        self.latest_motor_hall_summary = {
            "rpm": rpm,
            "fio3_raw_frequency_hz": raw_hz,
            "fio3_corrected_frequency_hz": float(measured_hz),
            "fio3_smoothed_frequency_hz": frequency,
            "ppr": ppr,
            "speed_quality": quality,
        }

    def _update_balancing(
        self,
        chunk: BalancingChunk,
        hardware_frequencies: Dict[str, float],
        hardware_pulses: Optional[Dict[str, HardwarePulseMeasurement]] = None,
    ) -> None:
        use_hardware_pulse_phase = bool(
            hardware_pulses and "speed" in hardware_pulses
        )
        hardware_pulses = hardware_pulses or {}
        ppr = max(1, int(self.device_config.get("ppr", 1)))
        stream_speed_hz = self.speed_estimator.update(
            chunk.channels["speed"], chunk.scan_rate
        )
        speed_has_hardware = "speed" in hardware_frequencies
        hardware_speed_hz = float(hardware_frequencies.get("speed", 0.0))
        measured_speed_hz = hardware_speed_hz if speed_has_hardware else stream_speed_hz
        speed_hz, speed_quality = self._validated_speed(measured_speed_hz)
        rpm = speed_hz * 60.0 / ppr
        position_a_stream_hz = self.position_a_estimator.update(
            chunk.channels["position_a"], chunk.scan_rate
        )
        position_a_has_hardware = "position_a" in hardware_frequencies
        position_a_hardware_hz = float(
            hardware_frequencies.get("position_a", 0.0)
        )
        position_a_hz = (
            position_a_hardware_hz
            if position_a_has_hardware
            else position_a_stream_hz
        )
        position_b_stream_hz = self.position_b_estimator.update(
            chunk.channels["position_b"], chunk.scan_rate
        )
        position_b_has_hardware = "position_b" in hardware_frequencies
        position_b_hardware_hz = float(
            hardware_frequencies.get("position_b", 0.0)
        )
        position_b_hz = (
            position_b_hardware_hz
            if position_b_has_hardware
            else position_b_stream_hz
        )
        if use_hardware_pulse_phase:
            phase_a = self.position_a_hardware_phase_estimator.update(
                hardware_pulses.get("speed"), hardware_pulses.get("position_a")
            )
            phase_b = self.position_b_hardware_phase_estimator.update(
                hardware_pulses.get("speed"), hardware_pulses.get("position_b")
            )
        else:
            phase_a = self.position_a_stream_phase_estimator.update(
                chunk.channels["speed"],
                chunk.channels["position_a"],
                chunk.scan_rate,
            )
            phase_b = self.position_b_stream_phase_estimator.update(
                chunk.channels["speed"],
                chunk.channels["position_b"],
                chunk.scan_rate,
            )
        force_a = self._clean_samples(chunk.channels["force_a"])
        force_b = self._clean_samples(chunk.channels["force_b"])
        force_a_rms = ac_rms(force_a)
        force_b_rms = ac_rms(force_b)
        self.smoothed_force_a_rms = self._smooth_value(
            self.smoothed_force_a_rms, force_a_rms, alpha=0.20
        )
        self.smoothed_force_b_rms = self._smooth_value(
            self.smoothed_force_b_rms, force_b_rms, alpha=0.20
        )

        self.rpm_history = self._append_history(self.rpm_history, rpm)
        self.speed_card.update_value(
            "%.0f" % rpm,
            "%s %.2f Hz | PPR %d | %s"
            % (
                "硬件测频" if speed_has_hardware else "流测频",
                speed_hz,
                ppr,
                speed_quality,
            ),
            self.rpm_history,
        )
        self.position_a_card.update_value(
            "%.2f" % position_a_hz,
            ("硬件测频 | " if position_a_has_hardware else "流测频 | ")
            + ("状态：有脉冲" if position_a_hz > 0 else "状态：无脉冲"),
        )
        self.position_a_card.update_phase(phase_a.angle_degrees, phase_a.quality)
        self.position_b_card.update_value(
            "%.2f" % position_b_hz,
            ("硬件测频 | " if position_b_has_hardware else "流测频 | ")
            + ("状态：有脉冲" if position_b_hz > 0 else "状态：无脉冲"),
        )
        self.position_b_card.update_phase(phase_b.angle_degrees, phase_b.quality)
        self.force_a_card.update_value(
            "%.2f" % (self.smoothed_force_a_rms * 1000),
            "AC RMS | Vpp %.2f mV | DC %.2f mV"
            % (float(np.ptp(force_a) * 1000), float(np.mean(force_a) * 1000)),
            self._display_waveform(force_a * 1000),
        )
        self.force_b_card.update_value(
            "%.2f" % (self.smoothed_force_b_rms * 1000),
            "AC RMS | Vpp %.2f mV | DC %.2f mV"
            % (float(np.ptp(force_b) * 1000), float(np.mean(force_b) * 1000)),
            self._display_waveform(force_b * 1000),
        )

    def _update_laser_wave(self, chunk: PiezoChunk) -> None:
        # Keep missing-sample positions in both time axes.
        values = {}
        for side in SIDES:
            data = np.asarray(chunk.channels[side], dtype=float).copy()
            data[(~np.isfinite(data)) | (data == -9999.0)] = np.nan
            values[side] = data
        left = self._clean_samples(chunk.channels["left"])
        right = self._clean_samples(chunk.channels["right"])
        self.smoothed_force_a_rms = self._smooth_value(
            self.smoothed_force_a_rms, ac_rms(left), alpha=0.20
        )
        self.smoothed_force_b_rms = self._smooth_value(
            self.smoothed_force_b_rms, ac_rms(right), alpha=0.20
        )
        for side, card, samples, rms in (
            ("left", self.force_a_card, left, self.smoothed_force_a_rms),
            ("right", self.force_b_card, right, self.smoothed_force_b_rms),
        ):
            profile = self.sensor_profiles[side]
            if not profile.enabled:
                card.update_value("--", "未安装 / 不参与 1X 分析", np.zeros(120))
                continue
            display_rms = profile.convert_amplitude(rms)
            display_waveform = profile.convert_voltage(samples)
            calibration = (
                "%s | %.6g mV/g" % (profile.algorithm_label, profile.sensitivity_mv_per_g)
                if profile.sensor_type == "accelerometer" and profile.sensitivity_mv_per_g > 0
                else profile.algorithm_label + " | 原始电压"
            )
            card.update_value(
                "%.3f" % display_rms,
                "%s | Vpp %.2f mV | DC %.2f mV" % (
                    calibration,
                    float(np.ptp(samples) * 1000),
                    float(np.mean(samples) * 1000),
                ),
                self._display_waveform(display_waveform),
            )
        self._laser_wave_scan_rate = float(chunk.scan_rate)
        self._laser_wave_chunks.append(values)
        self._laser_wave_sample_count += len(values["left"])
        maximum_samples = max(1, int(self._laser_wave_scan_rate * self.raw_display_max_seconds))
        while self._laser_wave_sample_count > maximum_samples:
            excess = self._laser_wave_sample_count - maximum_samples
            first = self._laser_wave_chunks[0]
            if excess >= len(first["left"]):
                self._laser_wave_chunks.popleft()
                self._laser_wave_sample_count -= len(first["left"])
            else:
                self._laser_wave_chunks[0] = {side: first[side][excess:].copy() for side in SIDES}
                self._laser_wave_sample_count -= excess
        self._laser_wave_update_counter += 1
        if self._laser_wave_update_counter % 2 == 0:
            self._redraw_laser_wave()

    def _redraw_laser_wave(self) -> None:
        remaining = min(max(1, int(self._laser_wave_scan_rate * self.raw_display_seconds)),
                        self._laser_wave_sample_count)
        pieces = []
        for values in reversed(self._laser_wave_chunks):
            if remaining <= 0:
                break
            take = min(remaining, len(values["left"]))
            pieces.append({side: values[side][-take:] for side in SIDES})
            remaining -= take
        if not pieces:
            self.laser_wave.set_data(np.array([]), np.array([]))
            self.right_wave.set_data(np.array([]), np.array([]))
            return
        values = {side: np.concatenate([item[side] for item in reversed(pieces)])
                  for side in SIDES}
        step = max(1, int(np.ceil(len(values["left"]) / 4000)))
        axis = np.arange(len(values["left"][::step]), dtype=float) * step / self._laser_wave_scan_rate
        for side, panel in (("left", self.laser_wave), ("right", self.right_wave)):
            profile = self.sensor_profiles[side]
            if profile.enabled:
                panel.set_data(
                    axis,
                    profile.convert_voltage(values[side][::step]),
                )
            else:
                panel.set_data(np.array([]), np.array([]))
        for panel in (self.laser_wave, self.right_wave):
            panel.plot.setXRange(0.0, self.raw_display_seconds, padding=0)

    def _reset_laser_display_buffer(self) -> None:
        self._laser_wave_chunks.clear()
        self._laser_wave_sample_count = 0
        self._laser_wave_update_counter = 0

    def _raw_display_duration_changed(self, seconds: float) -> None:
        self.raw_display_seconds = float(seconds)
        self._redraw_laser_wave()

    def _fft_display_limit_changed(self, frequency: int) -> None:
        for panel in (self.laser_fft, self.right_fft):
            panel.plot.setXRange(0.0, float(frequency), padding=0.0)
        if self.latest_analysis is not None:
            self._draw_laser_spectrum(self.latest_analysis)

    def _draw_laser_spectrum(self, analysis: PiezoSpectrumAnalysis) -> None:
        maximum_frequency = float(self.fft_limit_spin_box.value())
        mask = analysis.frequencies <= maximum_frequency
        for side, panel in (("left", self.laser_fft), ("right", self.right_fft)):
            profile = self.sensor_profiles[side]
            if profile.enabled:
                converted = np.abs(profile.convert_voltage(
                    analysis.amplitudes[side][mask]
                ))
                panel.set_data(analysis.frequencies[mask], converted)
            else:
                panel.set_data(np.array([]), np.array([]))

    def _update_laser_spectrum(self, analysis: PiezoSpectrumAnalysis) -> None:
        self.latest_analysis = analysis
        if self.recording_active:
            self._record_spectrum_counter += 1
            if self._record_spectrum_counter % 10 == 1:
                self.record_spectrum_requested.emit(analysis)
        self._draw_laser_spectrum(analysis)
        if self.balance_panel.settings.analysis_mode == "manual":
            if analysis.manual_tones:
                self._draw_dual_tones(analysis.manual_tones, "手动指定分量；非转子角度")
            else:
                self._clear_target_display("正在建立手动频率分析窗…")
        elif analysis.one_x is not None:
            self._display_one_x(analysis.one_x)
        one_x = analysis.one_x
        if one_x is not None and one_x.frequency_hz:
            values = []
            for side, side_name in (("left", "左"), ("right", "右")):
                profile = self.sensor_profiles[side]
                side_result = getattr(one_x, side)
                values.append(
                    "%s %.3f %s" % (
                        side_name,
                        profile.convert_amplitude(side_result.amplitude_v),
                        profile.effective_unit,
                    ) if profile.enabled else "%s未安装" % side_name
                )
            one_x_text = "%s %.3f Hz | %s" % (
                "候选峰预览（非有效 1X）"
                if one_x.preview_only else "候选 1X",
                one_x.frequency_hz,
                " | ".join(values),
            )
        else:
            one_x_text = "候选 1X 等待有效分析窗"
        dominant_parts = [
            "%s主频 %.1f Hz" % (
                "左" if side == "left" else "右",
                analysis.metrics[side].dominant_frequency,
            )
            for side in SIDES if self.sensor_profiles[side].enabled
        ]
        self.ui.laserMetricsLabel.setText(
            "%s | %s" % (one_x_text, " / ".join(dominant_parts))
        )

    def _draw_dual_tones(self, tones: Dict[str, TargetToneAnalysis], description: str) -> None:
        left, right = tones.get("left"), tones.get("right")
        for name, tone, panel in (
            ("左", left, self.target_tone_plot),
            ("右", right, self.right_tone_plot),
        ):
            side = "left" if name == "左" else "right"
            profile = self.sensor_profiles[side]
            if tone is None:
                panel.set_data(np.array([]), np.array([]))
                panel.set_title("%s%s · 暂无有效分量" % (name, profile.type_label))
                panel.set_metrics("本侧暂无有效分量 | %s" % description)
            else:
                converted_waveform = profile.convert_voltage(tone.waveform)
                amplitude = profile.convert_amplitude(tone.amplitude)
                panel.set_data(tone.time_seconds, converted_waveform)
                panel.set_title("%s%s · %.3f Hz 正弦分量" % (
                    name, profile.type_label, tone.frequency
                ))
                panel.set_metrics(
                    "峰值 %.4f %s  |  RMS %.4f %s  |  周期 %.3f ms" % (
                        amplitude, profile.effective_unit,
                        amplitude / np.sqrt(2), profile.effective_unit,
                        1000 / tone.frequency if tone.frequency > 0 else 0.0,
                    ),
                    details=description,
                )
        frequency = left.frequency if left is not None else (right.frequency if right is not None else 0.0)
        for panel in (self.laser_fft, self.right_fft):
            if frequency > 0:
                panel.set_frequency_marker(frequency)
            else:
                panel.clear_frequency_marker()

    def _update_laser_units(self) -> None:
        for side, wave, fft, tone in (
            ("left", self.laser_wave, self.laser_fft, self.target_tone_plot),
            ("right", self.right_wave, self.right_fft, self.right_tone_plot),
        ):
            unit = self.sensor_profiles[side].effective_unit
            wave.plot.setLabel("left", "传感器输出 (%s)" % unit)
            fft.plot.setLabel("left", "幅值 (%s)" % unit)
            tone.plot.setLabel("left", "分量峰值 (%s)" % unit)
        manual = self.balance_panel.settings.analysis_mode == "manual"
        self.ui.targetFrequencySpinBox.setVisible(manual)
        self.ui.targetFrequencyLabel.setVisible(manual)
        self._clear_target_display("等待有效分析窗")

    def _clear_target_display(self, reason: str) -> None:
        search_mode = self.balance_panel.settings.analysis_mode == "search"
        for name, panel in (("左", self.target_tone_plot), ("右", self.right_tone_plot)):
            side = "left" if name == "左" else "right"
            profile = self.sensor_profiles[side]
            panel.set_title(
                "%s%s · 候选 1X" % (name, profile.type_label) if search_mode else
                "%s%s · 手动指定频率分量" % (name, profile.type_label)
            )
            panel.set_data(np.array([]), np.array([]))
            panel.set_metrics(reason)
        for panel in (self.laser_fft, self.right_fft):
            if search_mode:
                panel.clear_frequency_marker()
            else:
                panel.set_frequency_marker(self.ui.targetFrequencySpinBox.value())

    def _balance_settings_changed(self, settings: dict, persist: bool = True) -> None:
        new_settings = PiezoSettings.from_dict(settings)
        new_settings.validate()
        self.balance_panel.settings = new_settings
        self.balance_panel.clear("参数已变更，等待新分析窗")
        self.motor_hall_ppr_spin_box.blockSignals(True)
        self.motor_hall_ppr_spin_box.setValue(new_settings.ppr)
        self.motor_hall_ppr_spin_box.blockSignals(False)
        self._update_pulse_source_labels()
        self._reset_motor_hall_state()
        self._apply_spectrum_mode(self.spectrum_enabled)
        self._update_laser_units()
        self._reset_laser_display_buffer()
        for panel in (
            self.laser_wave, self.right_wave,
            self.laser_fft, self.right_fft,
            self.target_tone_plot, self.right_tone_plot,
        ):
            panel.set_data(np.array([]), np.array([]))
        self.latest_analysis = None
        self.latest_one_x_result = None
        self.piezo_settings_requested.emit(asdict(new_settings))
        self.config["process"]["piezo_balance"] = asdict(new_settings)
        if persist:
            try:
                ConfigService(self.project_dir / "config").save_section("process", "piezo_balance", asdict(new_settings))
                self.statusBar().showMessage(
                    "1X 测量参数已保存并应用，正在重建 %.1f s 分析窗" %
                    new_settings.window_seconds
                )
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "参数保存失败", "本次已应用，但不能保存供下次启动：%s" % exc)

    def _analysis_mode_changed(self) -> None:
        self._balance_settings_changed(asdict(replace(
            self.balance_panel.settings, analysis_mode=self.analysis_mode_combo.currentData()
        )), persist=False)

    def _hall_ppr_changed(self, ppr: int) -> None:
        self._balance_settings_changed(asdict(replace(self.balance_panel.settings, ppr=ppr)))

    def _update_piezo_one_x(self, result: Piezo1XResult) -> None:
        self.latest_one_x_result = result
        self.balance_panel.update_result(result)
        self.dual_plane_page.update_result(result)
        self.calibration_page.set_result(result)
        if self.recording_active:
            self.record_one_x_requested.emit(result)
        if self.balance_panel.settings.analysis_mode == "search":
            self._display_one_x(result)
        if result.preview_only:
            self.ui.workflowStatusLabel.setText("候选峰预览 · 等待转速质量通过")
        elif self._active_results_valid(result):
            self.ui.workflowStatusLabel.setText(
                self._qc_status(result) or "测量有效 · 可记录或导出"
            )
        else:
            self.ui.workflowStatusLabel.setText("正在同步分析 · 等待已启用传感器有效")

    def _display_one_x(self, result: Piezo1XResult) -> None:
        tones = {}
        if result.left.valid or result.left.preview:
            tones["left"] = result.left.tone
        if result.right.valid or result.right.preview:
            tones["right"] = result.right.tone
        if not tones:
            self._clear_target_display(result.status)
            self.ui.laserMetricsLabel.setText(result.status)
            return
        range_text = ("光电 %.1fs窗 最低/中位/最高 "
                      "%.3f/%.3f/%.3f Hz" % (
                          self.balance_panel.settings.window_seconds,
                          result.reference_min_hz, result.reference_hz,
                          result.reference_max_hz))
        if result.preview_only:
            description = "候选峰预览（非有效 1X）\n" + range_text
        else:
            if (
                result.shaft_angle_synchronous
                and result.left.shaft_phase_degrees is not None
                and result.right.shaft_phase_degrees is not None
            ):
                description = (
                    "A同步角 %.1f°  |  B同步角 %.1f°  |  右相对左 %+.1f°\n"
                    "%s  |  FIO3下降沿=0°，配重角需试重标定" % (
                        result.left.shaft_phase_degrees,
                        result.right.shaft_phase_degrees,
                        result.relative_phase_degrees,
                        range_text,
                    )
                )
            elif result.relative_phase_degrees is not None:
                description = ("右相对左电气相位 %+.1f°  |  FIO3同步零位未通过质量门\n%s" % (
                    result.relative_phase_degrees, range_text)
                )
            else:
                active_side = next(
                    (side for side in SIDES if getattr(result, side).valid), None
                )
                active_angle = (
                    getattr(result, active_side).shaft_phase_degrees
                    if active_side is not None else None
                )
                description = (
                    "%s侧单传感器同步角 %.1f° | 不计算左右相对相位\n%s" % (
                        "左" if active_side == "left" else "右",
                        active_angle,
                        range_text,
                    )
                    if active_angle is not None else
                    "仅单侧有效 | FIO3 同步角未通过质量门\n%s" % range_text
                )
        self._draw_dual_tones(tones, description)
        if result.preview_only:
            for name, side, panel in (
                ("左", result.left, self.target_tone_plot),
                ("右", result.right, self.right_tone_plot),
            ):
                if side.preview:
                    side_name = "left" if name == "左" else "right"
                    panel.set_title("%s%s · 候选峰预览（非有效 1X）" % (
                        name, self.sensor_profiles[side_name].type_label
                    ))

    def _show_record_error(self, message: str) -> None:
        # A storage/report error must not falsely mark a healthy T7 disconnected.
        self.recording_active = False
        self._recording_stopped("")
        self.ui.spectrumRecordButton.setEnabled(self.acquisition_running and self.spectrum_enabled)
        self.record_stop_requested.emit()
        self.statusBar().showMessage(message)

    def _target_frequency_changed(self, frequency: float) -> None:
        self.target_frequency_requested.emit(float(frequency))
        self.latest_analysis = None
        self.latest_one_x_result = None
        for name, panel in (("左", self.target_tone_plot), ("右", self.right_tone_plot)):
            side = "left" if name == "左" else "right"
            profile = self.sensor_profiles[side]
            panel.set_title("%s%s · %.2f Hz 正弦分量（等待提取）" % (
                name, profile.type_label, frequency
            ))
            panel.set_data(np.array([]), np.array([]))
            panel.set_metrics(
                "频率 %.2f Hz | 峰值 -- %s | 非转子角度 | 周期 %.3f ms" % (
                    frequency, profile.effective_unit,
                    1000.0 / frequency if frequency > 0 else 0.0
                )
            )
        for panel in (self.laser_fft, self.right_fft):
            panel.set_frequency_marker(frequency)
        maximum_frequency = float(self.fft_limit_spin_box.value())
        for panel in (self.laser_fft, self.right_fft):
            panel.plot.setXRange(0.0, maximum_frequency, padding=0.0)

    def _stream_started(self, scan_rate: float, mode: str) -> None:
        self._last_acquisition_error = None
        self.acquisition_running = True
        self.active_mode = mode
        self.spectrum_enabled = mode == "piezo"
        self.ui.startButton.setEnabled(False)
        self.ui.stopButton.setEnabled(True)
        self.ui.stopButton.setText("停止  Del")
        self.ui.spectrumRecordButton.setEnabled(self.spectrum_enabled)
        self.ui.spectrumButton.setEnabled(False)
        self.ui.spectrumButton.setText("测量中")
        self.ui.productIdEdit.setEnabled(False)
        self.ui.measurementTemplateComboBox.setEnabled(False)
        self.ui.workflowStatusLabel.setText("采集中 · 等待稳速与有效分析窗")
        self.ui.laserDeviceLabel.setText(
            "%s | %.1f kScan/s%s"
            % (
                "AIN0/AIN3 振动传感器 + %s(EF)" % self._fio3_label()
                if self.spectrum_enabled
                else "FIO0/FIO1/FIO2/AIN0/AIN3 + %s(EF)" % self._fio3_label(),
                scan_rate / 1000.0,
                " | 真实振动测量" if self.spectrum_enabled else " | 兼容采集",
            )
        )

    def _stream_stopped(self) -> None:
        previous_mode = self.active_mode
        self.acquisition_running = False
        self.active_mode = None
        self.balance_panel.set_running(False)
        self.analysis_mode_combo.setEnabled(True)
        self.motor_hall_ppr_spin_box.setEnabled(True)
        self.ui.startButton.setEnabled(False)
        self.ui.stopButton.setEnabled(False)
        self.ui.stopButton.setText("停止  Del")
        self.ui.spectrumRecordButton.setEnabled(False)
        self.ui.productIdEdit.setEnabled(True)
        self.ui.measurementTemplateComboBox.setEnabled(True)
        self.ui.balancingDeviceLabel.setText("LabJack T7  ● 已停止")
        self._reset_motor_hall_state("已停止")
        if self._pending_mode is not None:
            target_mode = self._pending_mode
            self._pending_mode = None
            QTimer.singleShot(0, lambda: self._start_mode(target_mode))
        else:
            if previous_mode == "piezo" or self.spectrum_enabled:
                self._apply_spectrum_mode(False)
            self.ui.spectrumButton.setEnabled(True)
            if self._last_acquisition_error:
                self.ui.workflowStatusLabel.setText("采集失败 · 请查看错误提示")
                self.statusBar().showMessage(self._last_acquisition_error)
            else:
                self.ui.workflowStatusLabel.setText("已停止 · 可开始下一次测量")
                self.statusBar().showMessage("LabJack T7 | 已安全停止")

    def export_current_data(self) -> None:
        if (
            self.latest_data_mode == "piezo"
            and self.latest_piezo is not None
            and self.latest_analysis is not None
        ):
            paths = self.export_service.export_piezo(
                self.latest_piezo,
                self.latest_analysis,
                product_id=self.ui.productIdEdit.text().strip(),
                measurement_template=self.ui.measurementTemplateComboBox.currentText(),
                sensors=sensor_profiles_to_dict(self.sensor_profiles),
                device=self.device_config,
                summary=self._measurement_summary(self.latest_piezo),
            )
            QMessageBox.information(
                self,
                "导出完成",
                "左右传感器原始电压与频谱已导出到：\n%s"
                % paths["raw"].parent,
            )
            self.history_page.refresh()
            return
        if self.latest_balancing is None and self.latest_piezo is None:
            QMessageBox.information(self, "暂无数据", "请先开始一次真实测量。")
            return
        QMessageBox.information(
            self,
            "数据处理中",
            "请等待当前采集模式生成一组完整数据后再导出。",
        )

    def _measurement_summary(self, chunk: PiezoChunk) -> dict:
        """Return the values shown on the speed, RMS and 1X result cards."""
        summary = dict(self.latest_motor_hall_summary)
        for side, smoothed_rms in (
            ("left", self.smoothed_force_a_rms),
            ("right", self.smoothed_force_b_rms),
        ):
            profile = self.sensor_profiles[side]
            samples = self._clean_samples(chunk.channels[side])
            prefix = "%s_" % side
            if profile.enabled and smoothed_rms is not None:
                summary[prefix + "smoothed_ac_rms_v"] = float(smoothed_rms)
                summary[prefix + "smoothed_ac_rms_value"] = (
                    profile.convert_amplitude(smoothed_rms)
                )
                summary[prefix + "smoothed_ac_rms_unit"] = profile.effective_unit
            else:
                summary[prefix + "smoothed_ac_rms_v"] = ""
                summary[prefix + "smoothed_ac_rms_value"] = ""
                summary[prefix + "smoothed_ac_rms_unit"] = ""
            summary[prefix + "vpp_v"] = float(np.ptp(samples))
            summary[prefix + "dc_v"] = float(np.mean(samples))

        result = self.latest_one_x_result
        if result is None:
            summary.update({
                "one_x_frequency_hz": "",
                "one_x_period_ms": "",
                "one_x_rpm": "",
                "one_x_valid": False,
                "one_x_preview_only": False,
                "relative_electrical_phase_degrees": "",
                "one_x_status": "等待有效 1X 分析窗",
            })
        else:
            summary.update({
                "one_x_frequency_hz": result.frequency_hz,
                "one_x_period_ms": (
                    1000.0 / result.frequency_hz if result.frequency_hz > 0 else ""
                ),
                "one_x_rpm": result.rpm,
                "one_x_valid": (
                    self._active_results_valid(result) and not result.preview_only
                ),
                "one_x_preview_only": result.preview_only,
                "relative_electrical_phase_degrees": (
                    result.relative_phase_degrees
                    if result.relative_phase_degrees is not None else ""
                ),
                "one_x_status": result.status,
            })
        for side in SIDES:
            profile = self.sensor_profiles[side]
            prefix = "%s_one_x_" % side
            side_result = getattr(result, side) if result is not None else None
            if profile.enabled and side_result is not None:
                peak_v = float(side_result.amplitude_v)
                peak_value = profile.convert_amplitude(peak_v)
                summary.update({
                    prefix + "peak_v": peak_v,
                    prefix + "peak_value": peak_value,
                    prefix + "rms_v": peak_v / np.sqrt(2.0),
                    prefix + "rms_value": peak_value / np.sqrt(2.0),
                    prefix + "unit": profile.effective_unit,
                    prefix + "snr": side_result.snr,
                    "%s_fio3_phase_degrees" % side: (
                        side_result.shaft_phase_degrees
                        if side_result.shaft_phase_degrees is not None else ""
                    ),
                })
            else:
                summary.update({
                    prefix + "peak_v": "",
                    prefix + "peak_value": "",
                    prefix + "rms_v": "",
                    prefix + "rms_value": "",
                    prefix + "unit": "",
                    prefix + "snr": "",
                    "%s_fio3_phase_degrees" % side: "",
                })
        return summary

    def _reset_measurement_state(self) -> None:
        self.speed_estimator.reset()
        self.position_a_estimator.reset()
        self.position_b_estimator.reset()
        self.position_a_stream_phase_estimator.reset()
        self.position_b_stream_phase_estimator.reset()
        self.position_a_hardware_phase_estimator.reset()
        self.position_b_hardware_phase_estimator.reset()
        self.rpm_history.fill(0.0)
        self.stable_speed_hz = 0.0
        self.speed_bad_updates = 0
        self.smoothed_force_a_rms = None
        self.smoothed_force_b_rms = None
        self.latest_one_x_result = None

    def _reset_motor_hall_state(self, status: Optional[str] = None) -> None:
        status = status if status is not None else "等待光电脉冲"
        self.motor_hall_rpm_history.fill(0.0)
        self.stable_motor_hall_hz = 0.0
        self.motor_hall_bad_updates = 0
        self.motor_hall_pending_hz = []
        self.latest_motor_hall_summary = {
            "rpm": 0.0,
            "fio3_raw_frequency_hz": 0.0,
            "fio3_corrected_frequency_hz": 0.0,
            "fio3_smoothed_frequency_hz": 0.0,
            "ppr": max(1, int(self.motor_hall_ppr_spin_box.value())),
            "speed_quality": status,
        }
        self.motor_hall_speed_card.update_value(
            "--", "FIO3 | %s" % status, self.motor_hall_rpm_history
        )

    @staticmethod
    def _clean_samples(values: np.ndarray) -> np.ndarray:
        samples = np.asarray(values, dtype=float)
        clean = samples[np.isfinite(samples) & (samples != -9999.0)]
        return clean if clean.size else np.zeros(1)

    @staticmethod
    def _display_waveform(values: np.ndarray, maximum_points: int = 350) -> np.ndarray:
        step = max(1, len(values) // maximum_points)
        return values[::step]

    @staticmethod
    def _append_history(history: np.ndarray, value: float) -> np.ndarray:
        result = np.roll(history, -1)
        result[-1] = value
        return result

    @staticmethod
    def _smooth_value(previous: Optional[float], measured: float, alpha: float) -> float:
        if previous is None:
            return measured
        return previous + alpha * (measured - previous)

    def _validated_speed(self, measured: float) -> tuple:
        """Reject short pulse dropouts while exposing their diagnostic state."""
        return self._validated_frequency(
            measured, "stable_speed_hz", "speed_bad_updates"
        )

    def _validated_motor_hall_speed(self, measured: float) -> tuple:
        """Filter FIO3 snapshots without hiding a sustained speed change.

        A positive reading within the configured jump limit is smoothed for
        display. A larger jump is held temporarily and must form a consistent
        cluster for several consecutive updates before it becomes the new
        operating speed. Missing pulses still time out independently.
        """
        measured = float(measured) if np.isfinite(measured) else 0.0
        stable = float(self.stable_motor_hall_hz)
        if measured <= 0:
            self.motor_hall_pending_hz = []
            self.motor_hall_bad_updates += 1
            if self.motor_hall_bad_updates >= 20:
                self.stable_motor_hall_hz = 0.0
            return (self.stable_motor_hall_hz,
                    "无光电脉冲" if self.stable_motor_hall_hz <= 0
                    else "光电丢脉冲/保持")
        if stable <= 0:
            self.stable_motor_hall_hz = measured
            self.motor_hall_bad_updates = 0
            self.motor_hall_pending_hz = []
            return measured, "稳定"

        limit = self.balance_panel.settings.reference_jump_limit
        if abs(measured - stable) / stable <= limit:
            # Light smoothing affects only the operator-facing speed card;
            # the 1X analyzer uses its own full-window median and quality gate.
            stable += 0.12 * (measured - stable)
            self.stable_motor_hall_hz = stable
            self.motor_hall_bad_updates = 0
            self.motor_hall_pending_hz = []
            return stable, "稳定（低通平滑）"

        pending = self.motor_hall_pending_hz
        if pending:
            center = float(np.median(pending))
            if abs(measured - center) / center <= limit:
                pending.append(measured)
            else:
                pending = [measured]
        else:
            pending = [measured]
        self.motor_hall_pending_hz = pending
        self.motor_hall_bad_updates = len(pending)
        if len(pending) >= self.balance_panel.settings.reference_change_confirm_updates:
            stable = float(np.median(pending))
            self.stable_motor_hall_hz = stable
            self.motor_hall_bad_updates = 0
            self.motor_hall_pending_hz = []
            return stable, "已连续确认新转速"
        return stable, "瞬时跳变已滤波/保持 %d/%d" % (
            len(pending), self.balance_panel.settings.reference_change_confirm_updates
        )

    def _validated_frequency(
        self, measured: float, stable_attribute: str, bad_updates_attribute: str
    ) -> tuple:
        stable = float(getattr(self, stable_attribute))
        bad_updates = int(getattr(self, bad_updates_attribute))
        if measured <= 0:
            bad_updates += 1
            if bad_updates >= 20:
                stable = 0.0
            setattr(self, stable_attribute, stable)
            setattr(self, bad_updates_attribute, bad_updates)
            return stable, "脉冲异常/保持"
        if stable <= 0:
            setattr(self, stable_attribute, measured)
            setattr(self, bad_updates_attribute, 0)
            return measured, "稳定"
        ratio = measured / stable
        if 0.80 <= ratio <= 1.20:
            stable += 0.12 * (measured - stable)
            setattr(self, stable_attribute, stable)
            setattr(self, bad_updates_attribute, 0)
            return stable, "稳定"
        bad_updates += 1
        if bad_updates >= 12:
            setattr(self, stable_attribute, measured)
            setattr(self, bad_updates_attribute, 0)
            return measured, "已确认转速变化"
        setattr(self, bad_updates_attribute, bad_updates)
        return stable, "脉冲异常/保持"

    def _acquisition_start_failed(self, message: str) -> None:
        """Restore the UI first, then present a persistent startup error."""
        self._last_acquisition_error = message
        self._stream_stopped()
        self._show_error(message)

    def _show_error(self, message: str) -> None:
        self._last_acquisition_error = message
        self.statusBar().showMessage(message)
        self.ui.startButton.setEnabled(False)
        self.ui.spectrumButton.setEnabled(True)
        self.ui.stopButton.setEnabled(False)
        self.ui.productIdEdit.setEnabled(True)
        self.ui.measurementTemplateComboBox.setEnabled(True)
        self.ui.workflowStatusLabel.setText("设备异常 · 请查看错误详情")
        self.ui.balancingDeviceLabel.setText("采集设备  ● 连接失败")
        self.ui.balancingDeviceLabel.setStyleSheet("color:#d5122d;font-weight:700")
        QMessageBox.critical(
            self,
            "采集设备启动失败",
            message +
            "\n\n如果当前选择了 IEPE 加速度传感器，请依次检查："
            "\n1. Windows 已安装 NI-DAQmx，NI MAX 能看到 cDAQ-9174 和 NI-9234；"
            "\n2. NI MAX 中 NI-9234 自检通过，通道没有被其他软件占用；"
            "\n3. 运行本程序的 Python 环境已安装 nidaqmx；"
            "\n4. 加速度传感器接在 NI-9234 对应 ai 通道，而不是压电放大板。",
        )

    def closeEvent(self, event: object) -> None:
        if self.record_thread.isRunning():
            QMetaObject.invokeMethod(
                self.record_worker, "stop", Qt.BlockingQueuedConnection
            )
            self.record_thread.quit()
            self.record_thread.wait(2000)
        if self.thread.isRunning():
            QMetaObject.invokeMethod(self.worker, "stop", Qt.BlockingQueuedConnection)
            self.thread.quit()
            self.thread.wait(2000)
        event.accept()
