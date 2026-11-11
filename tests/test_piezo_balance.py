"""Dual amplified-piezo analysis, independent from retired laser calibration."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from models.data import Metrics, PiezoChunk
from models.piezo_balance import PiezoSettings, PiezoSpectrumAnalysis
from models.sensors import SensorProfile, sensor_profiles_from_config
from services.piezo_balance import DualPiezoAnalyzer
from services.export_service import ExportService
from services.piezo_record_service import PiezoRecordService


class DualPiezoAnalyzerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rate = 4000
        self.settings = PiezoSettings(window_seconds=2, interchannel_delay_us=8)
        self.analyzer = DualPiezoAnalyzer(self.settings)

    def run_window(
        self, left: np.ndarray, right: np.ndarray, pulse_hz=150,
        reference: np.ndarray = None,
    ):
        result = None
        frequencies = (np.repeat(float(pulse_hz), len(left) // 200)
                       if np.isscalar(pulse_hz) else pulse_hz)
        for index, start in enumerate(range(0, len(left), 200)):
            result = self.analyzer.update(PiezoChunk(
                channels={"left": left[start:start + 200],
                          "right": right[start:start + 200]},
                reference_state=(
                    reference[start:start + 200]
                    if reference is not None else np.array([])
                ),
                scan_rate=self.rate, start_sample=start), frequencies[index])
        return result

    def test_common_one_x_amplitudes_and_relative_electrical_phase(self) -> None:
        t = np.arange(self.rate * 2) / self.rate
        left = .02 * np.cos(2 * np.pi * 150 * t) + .4 * np.cos(2 * np.pi * 50 * t)
        right = .03 * np.cos(2 * np.pi * 150 * (t + 8e-6) + np.deg2rad(60))
        result = self.run_window(left, right)
        self.assertAlmostEqual(result.frequency_hz, 150, delta=.03)
        self.assertAlmostEqual(result.left.amplitude_v, .02, delta=.0002)
        self.assertAlmostEqual(result.right.amplitude_v, .03, delta=.0002)
        self.assertAlmostEqual(result.relative_phase_degrees, 60, delta=.2)
        self.assertNotIn("g·mm", result.status)
        self.assertIn("未建立同步转子零位", result.status)

    def test_sample_aligned_fio3_produces_two_optical_relative_angles(self) -> None:
        frequency = 125.0
        count = self.rate * 2
        period_samples = int(self.rate / frequency)
        first_edge = 16
        edge_time = (first_edge - 0.5) / self.rate
        time_axis = np.arange(count) / self.rate
        left_phase = 32.0
        right_phase = 107.0
        left = .02 * np.cos(
            2 * np.pi * frequency * (time_axis - edge_time)
            + np.deg2rad(left_phase)
        )
        right = .03 * np.cos(
            2 * np.pi * frequency * (time_axis - edge_time)
            + np.deg2rad(right_phase)
        )
        reference = np.ones(count)
        for edge in range(first_edge, count, period_samples):
            reference[edge:edge + 4] = 0.0
        result = self.run_window(
            left, right, pulse_hz=frequency, reference=reference
        )
        self.assertTrue(result.shaft_angle_synchronous)
        self.assertAlmostEqual(result.synchronous_reference_hz, frequency, places=6)
        self.assertGreater(result.reference_edge_count, 200)
        # Test fixture has no sequential-channel delay. Remove production
        # defaults so the known phases are recovered exactly.
        # (Re-run through an analyzer configured for zero delay.)
        zero_delay = PiezoSettings(
            window_seconds=2, interchannel_delay_us=0,
            reference_channel_delay_us=0,
        )
        self.analyzer = DualPiezoAnalyzer(zero_delay)
        result = self.run_window(
            left, right, pulse_hz=frequency, reference=reference
        )
        self.assertAlmostEqual(result.left.shaft_phase_degrees, left_phase, delta=.3)
        self.assertAlmostEqual(result.right.shaft_phase_degrees, right_phase, delta=.3)
        self.assertAlmostEqual(result.relative_phase_degrees, 75.0, delta=.3)
        self.assertIn("FIO3 下降沿为 0°", result.status)

    def test_synchronous_edges_take_priority_over_bad_ef_snapshot(self) -> None:
        frequency = 125.0
        count = self.rate * 2
        axis = np.arange(count) / self.rate
        wave = .02 * np.cos(2 * np.pi * frequency * axis)
        reference = np.ones(count)
        for edge in range(16, count, int(self.rate / frequency)):
            reference[edge:edge + 4] = 0.0
        # The command-response snapshot is deliberately outside the configured
        # search range. Sample-aligned FIO3 is the authoritative reference.
        result = self.run_window(
            wave, wave, pulse_hz=500.0, reference=reference
        )
        self.assertTrue(result.left.valid)
        self.assertTrue(result.shaft_angle_synchronous)
        self.assertAlmostEqual(result.reference_hz, frequency, places=6)
        self.assertAlmostEqual(result.rpm, 7500.0, places=3)

    def test_one_missing_reference_is_held_but_continuous_loss_clears_result(self) -> None:
        t = np.arange(self.rate * 2) / self.rate
        wave = .02 * np.cos(2 * np.pi * 150 * t)
        self.assertTrue(self.run_window(wave, wave).left.valid)
        for index in range(self.settings.max_bad_reference_updates):
            held = self.analyzer.update(PiezoChunk(
                channels={"left": wave[:200], "right": wave[:200]},
                scan_rate=self.rate, start_sample=len(wave) + index * 200), 0)
            self.assertIsNone(held)
        lost = self.analyzer.update(PiezoChunk(
            channels={"left": wave[:200], "right": wave[:200]},
            scan_rate=self.rate,
            start_sample=len(wave) + self.settings.max_bad_reference_updates * 200), 0)
        self.assertFalse(lost.left.valid)
        self.assertIsNone(lost.relative_phase_degrees)
        self.assertIn("连续", lost.status)
        self.assertIn("作废", lost.status)

    def test_saturation_and_missing_samples_invalidate_both_channels(self) -> None:
        for invalid in (9.9, -9999.0, float("nan")):
            result = self.analyzer.update(PiezoChunk(
                channels={"left": np.array([0.0, invalid]),
                          "right": np.zeros(2)}, scan_rate=self.rate,
                start_sample=0), 150)
            self.assertFalse(result.left.valid)
            self.assertFalse(result.right.valid)

    def test_one_isolated_saturation_spike_is_repaired_without_erasing_window(self) -> None:
        time_axis = np.arange(self.rate * 2) / self.rate
        left = .02 * np.cos(2 * np.pi * 150 * time_axis)
        right = .03 * np.cos(2 * np.pi * 150 * time_axis)
        left[731] = 9.9
        result = self.run_window(left, right)
        self.assertTrue(result.left.valid)
        self.assertTrue(result.right.valid)
        self.assertEqual(result.repaired_saturation_samples, 1)
        self.assertIn("插值修复 1 个孤立饱和尖峰", result.status)

    def test_consecutive_saturation_is_still_rejected(self) -> None:
        time_axis = np.arange(200) / self.rate
        left = .02 * np.cos(2 * np.pi * 150 * time_axis)
        left[70:73] = 9.9
        result = self.analyzer.update(PiezoChunk(
            channels={"left": left, "right": left.copy()},
            scan_rate=self.rate, start_sample=0,
        ), 150.0)
        self.assertFalse(result.left.valid)
        self.assertIn("连续或过多", result.status)

    def test_one_weak_side_does_not_create_relative_phase(self) -> None:
        t = np.arange(self.rate * 2) / self.rate
        left = .02 * np.cos(2 * np.pi * 150 * t)
        result = self.run_window(left, np.zeros_like(left))
        self.assertTrue(result.left.valid)
        self.assertFalse(result.right.valid)
        self.assertIsNone(result.relative_phase_degrees)

    def test_single_accelerometer_ignores_disabled_side_and_keeps_sync_angle(self) -> None:
        profiles = {
            "left": {
                "sensor_type": "accelerometer",
                "model": "test accelerometer",
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
        self.analyzer = DualPiezoAnalyzer(self.settings, sensor_profiles=profiles)
        frequency = 125.0
        count = self.rate * 2
        axis = np.arange(count) / self.rate
        left = .1 * np.cos(2 * np.pi * frequency * axis)
        # An unconnected/disabled input may be saturated or noisy; it must not
        # invalidate the installed accelerometer side.
        right = np.full(count, 9.9)
        reference = np.ones(count)
        for edge in range(16, count, int(self.rate / frequency)):
            reference[edge:edge + 4] = 0.0
        result = self.run_window(left, right, pulse_hz=frequency, reference=reference)
        self.assertTrue(result.left.valid)
        self.assertFalse(result.right.valid)
        self.assertTrue(result.shaft_angle_synchronous)
        self.assertIsNotNone(result.left.shaft_phase_degrees)
        self.assertIsNone(result.relative_phase_degrees)
        self.assertIn("单侧同步 1X 有效", result.status)
        profile = sensor_profiles_from_config(profiles)["left"]
        self.assertAlmostEqual(profile.convert_amplitude(result.left.amplitude_v), 1.0, places=3)

    def test_accelerometer_requires_sensitivity_for_physical_units(self) -> None:
        with self.assertRaises(ValueError):
            SensorProfile(
                sensor_type="accelerometer",
                display_unit="g",
                sensitivity_mv_per_g=0.0,
            ).validate()
        with self.assertRaises(ValueError):
            SensorProfile(
                sensor_type="accelerometer",
                display_unit="mV",
                sensitivity_mv_per_g=0.0,
            ).validate()

    def test_mixed_device_channels_do_not_claim_relative_or_shaft_phase(self) -> None:
        frequency = 125.0
        count = self.rate * 2
        axis = np.arange(count) / self.rate
        left = .02 * np.cos(2 * np.pi * frequency * axis)
        right = .03 * np.cos(2 * np.pi * frequency * axis + np.deg2rad(70))
        reference = np.ones(count)
        for edge in range(16, count, int(self.rate / frequency)):
            reference[edge:edge + 4] = 0.0
        result = None
        for start in range(0, count, 200):
            result = self.analyzer.update(PiezoChunk(
                channels={"left": left[start:start + 200],
                          "right": right[start:start + 200]},
                reference_state=reference[start:start + 200],
                scan_rate=self.rate,
                start_sample=start,
                channels_synchronized=False,
                reference_synchronized=False,
                acquisition_backend="ni9234+labjack",
            ), frequency)
        self.assertTrue(result.left.valid)
        self.assertTrue(result.right.valid)
        self.assertIsNone(result.relative_phase_degrees)
        self.assertFalse(result.shaft_angle_synchronous)
        self.assertIn("不同时钟", result.status)
        self.assertIn("不输出转子绝对角", result.status)

    def test_one_bad_optical_snapshot_does_not_reject_stable_window(self) -> None:
        t = np.arange(self.rate * 2) / self.rate
        wave = .02 * np.cos(2 * np.pi * 150 * t)
        optical = np.full(40, 150.0)
        optical[18] = 195.0
        result = self.run_window(wave, wave, optical)
        self.assertTrue(result.left.valid)
        self.assertAlmostEqual(result.rpm, 9000.0)
        self.assertAlmostEqual(result.rpm_variation, 0.0)
        self.assertAlmostEqual(result.reference_min_hz, 150.0)
        self.assertAlmostEqual(result.reference_max_hz, 150.0)
        self.assertGreater(result.reference_held_fraction, 0.0)
        self.assertIn("异常跳变读数已滤波保持", result.status)

    def test_one_out_of_range_snapshot_holds_last_reference_and_reports_range(self) -> None:
        t = np.arange(self.rate * 2) / self.rate
        wave = .02 * np.cos(2 * np.pi * 150 * t)
        optical = np.full(40, 150.0)
        optical[18] = 260.0
        result = self.run_window(wave, wave, optical)
        self.assertTrue(result.left.valid)
        self.assertAlmostEqual(result.reference_min_hz, 150.0)
        self.assertAlmostEqual(result.reference_hz, 150.0)
        self.assertAlmostEqual(result.reference_max_hz, 150.0)
        self.assertGreater(result.reference_held_fraction, 0.0)
        self.assertIn("异常跳变读数已滤波保持", result.status)

    def test_consistent_frequency_step_is_accepted_after_confirmation(self) -> None:
        analyzer = DualPiezoAnalyzer(self.settings)
        self.assertEqual(analyzer._filter_reference_jump(150.0), (150.0, False, False))
        for _ in range(self.settings.reference_change_confirm_updates - 1):
            filtered, held, changed = analyzer._filter_reference_jump(168.0)
            self.assertAlmostEqual(filtered, 150.0)
            self.assertTrue(held)
            self.assertFalse(changed)
        filtered, held, changed = analyzer._filter_reference_jump(168.0)
        self.assertAlmostEqual(filtered, 168.0)
        self.assertFalse(held)
        self.assertTrue(changed)

    def test_sustained_speed_drift_still_rejects_one_x(self) -> None:
        t = np.arange(self.rate * 2) / self.rate
        wave = .02 * np.cos(2 * np.pi * 150 * t)
        optical = np.linspace(150.0, 165.0, 40)
        result = self.run_window(wave, wave, optical)
        self.assertFalse(result.left.valid)
        self.assertTrue(result.left.preview)
        self.assertTrue(result.preview_only)
        self.assertIsNotNone(result.left.tone)
        self.assertGreater(result.left.amplitude_v, 0)
        self.assertGreater(result.rpm_variation, self.settings.rpm_variation_limit)
        self.assertIn("候选峰预览", result.status)
        self.assertIn("非有效 1X", result.status)
        self.assertAlmostEqual(result.reference_min_hz, 150.0)
        self.assertAlmostEqual(result.reference_max_hz, 165.0)

    def test_weak_one_x_remains_visible_as_reference_locked_preview(self) -> None:
        settings = PiezoSettings(
            window_seconds=2,
            interchannel_delay_us=8,
            minimum_snr=1000.0,
        )
        self.analyzer = DualPiezoAnalyzer(settings)
        rng = np.random.default_rng(7)
        time_axis = np.arange(self.rate * 2) / self.rate
        left = 2e-5 * np.cos(2 * np.pi * 150 * time_axis) + rng.normal(
            0.0, 2e-4, len(time_axis)
        )
        right = rng.normal(0.0, 2e-4, len(time_axis))
        result = self.run_window(left, right, pulse_hz=150.0)
        self.assertAlmostEqual(result.frequency_hz, 150.0, places=6)
        self.assertTrue(result.preview_only)
        self.assertTrue(result.left.preview)
        self.assertIsNotNone(result.left.tone)
        self.assertGreater(result.left.amplitude_v, 0.0)
        self.assertIn("按光电转频直接提取", result.status)
        self.assertIn("不用于动平衡标定", result.status)


class PiezoRecordTest(unittest.TestCase):
    def test_record_contains_both_voltages_and_explicit_limitations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = PiezoRecordService(Path(directory))
            path = recorder.start({"operator": "test"})
            recorder.append_waveform(PiezoChunk(
                channels={"left": np.array([.01, .02]),
                          "right": np.array([-.03, -.04])},
                reference_state=np.array([1.0, 0.0]),
                scan_rate=1000, start_sample=10), 150)
            recorder.stop()
            metadata = json.loads((path / "measurement_metadata.json").read_text())
            self.assertEqual(metadata["source"], "amplified_piezo_left_right")
            self.assertTrue(metadata["shaft_reference_streamed"])
            self.assertTrue(metadata["shaft_angle_synchronous"])
            self.assertFalse(metadata["shaft_angle_calibrated"])
            with (path / "piezo_left_right_raw.csv").open(encoding="utf-8-sig") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows[0][2:], [
                "left_voltage_v", "left_piezo_voltage_mv",
                "right_voltage_v", "right_piezo_voltage_mv", "fio3_state"
            ])
            self.assertEqual(rows[1][2:], [
                "0.01", "10.0", "-0.03", "-30.0", "1.0"
            ])

    def test_mixed_sensor_record_exports_voltage_and_engineering_units(self) -> None:
        sensors = {
            "left": {
                "sensor_type": "accelerometer", "model": "PCB 353B15",
                "sensitivity_mv_per_g": 10.0, "display_unit": "g", "polarity": 1,
            },
            "right": {
                "sensor_type": "piezo", "model": "piezo",
                "sensitivity_mv_per_g": 0.0, "display_unit": "mV", "polarity": 1,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            recorder = PiezoRecordService(Path(directory))
            path = recorder.start({"sensors": sensors})
            recorder.append_waveform(PiezoChunk(
                channels={"left": np.array([.002]), "right": np.array([-.03])},
                reference_state=np.array([1.0]), scan_rate=25_600,
            ), 180.0)
            recorder.stop()
            with (path / "piezo_left_right_raw.csv").open(
                encoding="utf-8-sig"
            ) as handle:
                rows = list(csv.reader(handle))
            self.assertIn("left_acceleration_g", rows[0])
            self.assertIn("left_acceleration_m_s2", rows[0])
            self.assertIn("right_piezo_voltage_mv", rows[0])
            self.assertAlmostEqual(float(rows[1][3]), 0.2)
            self.assertAlmostEqual(float(rows[1][4]), 0.2 * 9.80665)
            metadata = json.loads(
                (path / "measurement_metadata.json").read_text()
            )
            self.assertFalse(metadata["shaft_angle_synchronous"])

    def test_current_export_includes_mixed_sensor_engineering_units(self) -> None:
        sensors = {
            "left": {
                "sensor_type": "accelerometer", "model": "PCB 353B15",
                "sensitivity_mv_per_g": 10.0, "display_unit": "g", "polarity": 1,
            },
            "right": {
                "sensor_type": "piezo", "model": "piezo",
                "sensitivity_mv_per_g": 0.0, "display_unit": "mV", "polarity": 1,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            chunk = PiezoChunk(
                channels={"left": np.array([.002]), "right": np.array([-.03])},
                reference_state=np.array([1.0]), scan_rate=25_600,
                acquisition_backend="ni9234+labjack",
                channels_synchronized=False, reference_synchronized=False,
            )
            analysis = PiezoSpectrumAnalysis(
                frequencies=np.array([180.0]),
                amplitudes={"left": np.array([.00024]),
                            "right": np.array([.058])},
                metrics={"left": Metrics(), "right": Metrics()},
                frequency_resolution=0.390625,
            )
            paths = ExportService(Path(directory)).export_piezo(
                chunk, analysis, sensors=sensors,
                device={
                    "channels": {"piezo_left": "AIN0", "piezo_right": "AIN3",
                                 "piezo_reference": "DIO3"},
                    "ni9234": {"module": "cDAQ1Mod1", "left_channel": "ai0"},
                },
            )
            with paths["spectrum"].open(encoding="utf-8-sig") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows[0], [
                "frequency_hz", "left_amplitude_v", "left_amplitude_g",
                "left_amplitude_m_s2", "right_amplitude_v",
                "right_amplitude_mv",
            ])
            self.assertAlmostEqual(float(rows[1][2]), 0.024)
            self.assertAlmostEqual(float(rows[1][5]), 58.0)
            metadata = json.loads(
                (paths["raw"].parent / "measurement_metadata.json").read_text()
            )
            self.assertEqual(metadata["source"], "ni9234+labjack")
            self.assertFalse(metadata["reference_synchronized"])


if __name__ == "__main__":
    unittest.main()
