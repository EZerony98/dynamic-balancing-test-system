"""Headless UI/worker integration for the active dual-piezo mode."""

import unittest
from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
from PySide6.QtWidgets import QApplication

from main import CONFIG
from models.data import AcquisitionChunk, PiezoChunk, TargetToneAnalysis
from models.piezo_balance import Piezo1XResult, PiezoSideResult
from ui.main_window import MainWindow
from utils.paths import PROJECT_DIR
from workers.acquisition_worker import AcquisitionWorker


class PiezoBalanceUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_two_traces_and_synchronized_response_angles_without_fake_unbalance(self):
        config = CONFIG.load_all()
        config["process"]["piezo_balance"]["ppr"] = 1
        window = MainWindow(config, PROJECT_DIR)
        try:
            self.assertNotIn("激光", window.windowTitle())
            self.assertIn("左右压电", window.ui.laserSectionLabel.text())
            self.assertEqual(window.motor_hall_ppr_spin_box.value(), 1)
            rate = 4000
            axis = np.arange(2 * rate) / rate
            frequency = 125.0
            left = .02 * np.cos(2 * np.pi * frequency * axis)
            right = .03 * np.cos(2 * np.pi * frequency * axis + np.deg2rad(60))
            reference = np.ones(len(axis))
            for edge in range(16, len(axis), int(rate / frequency)):
                reference[edge:edge + 4] = 0.0
            blocks = iter(range(0, len(left), 200))

            def read():
                start = next(blocks)
                return AcquisitionChunk(
                    piezo=PiezoChunk(channels={
                        "left": left[start:start + 200],
                        "right": right[start:start + 200]},
                        reference_state=reference[start:start + 200],
                        scan_rate=rate, start_sample=start),
                    hardware_frequencies={"motor_hall": frequency},
                )

            worker = AcquisitionWorker(SimpleNamespace(read=read), 1024,
                                       piezo_settings=asdict(window.balance_panel.settings))
            worker.running = True
            worker.data_ready.connect(window._update_acquisition)
            worker.piezo_one_x_ready.connect(window._update_piezo_one_x)
            worker.piezo_analysis_ready.connect(window._update_laser_spectrum)
            errors = []
            worker.error.connect(errors.append)
            for _ in range(40):
                worker._read()
            self.assertFalse(errors)
            self.assertEqual(window.motor_hall_speed_card.value_label.text(), "7500")
            self.assertAlmostEqual(float(window.force_a_card.value_label.text()), 14.14, delta=0.1)
            self.assertAlmostEqual(float(window.force_b_card.value_label.text()), 21.21, delta=0.1)
            self.assertIn("原始电压", window.force_a_card.detail_label.text())
            self.assertIn("20.000 mV", window.balance_panel.left_label.text())
            self.assertIn("30.000 mV", window.balance_panel.right_label.text())
            self.assertIn("相位", window.balance_panel.relative_phase_label.text())
            self.assertNotIn("--", window.balance_panel.left_angle_label.text())
            self.assertNotIn("--", window.balance_panel.right_angle_label.text())
            self.assertNotIn("g·mm", window.balance_panel.left_label.text())
            self.assertGreater(len(window.laser_wave.curve.xData), 0)
            self.assertGreater(len(window.right_wave.curve.xData), 0)
            self.assertGreater(len(window.laser_fft.curve.xData), 0)
            self.assertGreater(len(window.right_fft.curve.xData), 0)
            self.assertGreater(len(window.target_tone_plot.curve.xData), 0)
            self.assertGreater(len(window.right_tone_plot.curve.xData), 0)
            self.assertIsNone(window.laser_wave.second_curve)
            self.assertIsNone(window.right_wave.second_curve)
            self.assertIs(window.ui.laserPlotsLayout.itemAtPosition(0, 0).widget(), window.laser_wave)
            self.assertIs(window.ui.laserPlotsLayout.itemAtPosition(1, 0).widget(), window.right_wave)
            self.assertIs(
                window.ui.laserPlotsLayout.itemAtPosition(0, 2).widget(),
                window.target_tone_plot,
            )
            self.assertIs(
                window.ui.laserPlotsLayout.itemAtPosition(1, 2).widget(),
                window.right_tone_plot,
            )
            self.assertFalse(window.target_tone_plot.isHidden())
            self.assertFalse(window.right_tone_plot.isHidden())
            worker.running = False
        finally:
            window.close()
            self.app.processEvents()

    def test_bad_optical_quality_keeps_preview_plot_but_not_valid_values(self):
        config = CONFIG.load_all()
        window = MainWindow(config, PROJECT_DIR)
        try:
            axis = np.linspace(0, .05, 200)
            tone = TargetToneAnalysis(
                frequency=150.0, amplitude=.02, rms=.02 / np.sqrt(2),
                phase_degrees=10.0, time_seconds=axis,
                waveform=.02 * np.cos(2 * np.pi * 150 * axis),
            )
            preview = Piezo1XResult(
                frequency_hz=150.0, reference_hz=151.0, rpm=9060,
                rpm_variation=.03, reference_min_hz=147.0,
                reference_max_hz=155.0, preview_only=True,
                left=PiezoSideResult(
                    preview=True, amplitude_v=.02, snr=12, tone=tone),
                right=PiezoSideResult(
                    preview=True, amplitude_v=.03, snr=10, tone=tone),
                status="候选峰预览（非有效 1X）",
            )
            window._update_piezo_one_x(preview)
            self.assertGreater(len(window.target_tone_plot.curve.xData), 0)
            self.assertGreater(len(window.right_tone_plot.curve.xData), 0)
            self.assertIn("候选峰预览", window.target_tone_plot.title_label.text())
            self.assertIn("峰值", window.target_tone_plot.metrics_label.text())
            self.assertIn(
                "最低/中位/最高",
                window.target_tone_plot.metrics_detail_label.text(),
            )
            self.assertFalse(window.target_tone_plot.metrics_detail_label.isHidden())
            self.assertIn("20.000 mV（预览）", window.balance_panel.left_label.text())
            self.assertIn("30.000 mV（预览）", window.balance_panel.right_label.text())
        finally:
            window.close()
            self.app.processEvents()

    def test_measurement_settings_hot_apply_while_acquiring(self):
        worker = AcquisitionWorker(SimpleNamespace(), 1024)
        worker.running = True
        errors = []
        worker.error.connect(errors.append)
        values = asdict(worker.piezo_settings)
        values["synchronous_period_variation_limit"] = 0.08
        values["minimum_snr"] = 7.0
        worker.set_piezo_settings(values)
        self.assertFalse(errors)
        self.assertAlmostEqual(
            worker.piezo_analyzer.settings.synchronous_period_variation_limit,
            0.08,
        )
        self.assertAlmostEqual(worker.piezo_analyzer.settings.minimum_snr, 7.0)

    def test_left_accelerometer_and_uninstalled_right_are_presented_independently(self):
        config = CONFIG.load_all()
        config["process"]["sensors"] = {
            "left": {
                "sensor_type": "accelerometer",
                "model": "ACC-TEST",
                "sensitivity_mv_per_g": 100.0,
                "display_unit": "g",
                "polarity": 1,
            },
            "right": {
                "sensor_type": "disabled",
                "model": "",
                "sensitivity_mv_per_g": 0.0,
                "display_unit": "mV",
                "polarity": 1,
            },
        }
        window = MainWindow(config, PROJECT_DIR)
        try:
            self.assertEqual(
                window.settings_page.sensor_controls["left"]["type"].currentData(),
                "accelerometer",
            )
            self.assertEqual(
                window.settings_page.sensor_controls["right"]["type"].currentData(),
                "disabled",
            )
            self.assertIn("左侧加速度传感器", window.ui.laserSectionLabel.text())
            self.assertEqual(window.force_a_card.unit_label.text(), "g")
            self.assertIn("未安装", window.force_b_card.detail_label.text())
            result = Piezo1XResult(
                frequency_hz=150.0,
                left=PiezoSideResult(valid=True, amplitude_v=0.1),
                right=PiezoSideResult(reason="本侧传感器未安装"),
            )
            window.balance_panel.update_result(result)
            self.assertEqual(window.balance_panel.left_label.text(), "左 1.000 g")
            self.assertEqual(window.balance_panel.right_label.text(), "右 -- mV")
        finally:
            window.close()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
