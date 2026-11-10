import json
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from models.data import LaserChunk
from models.laser_balance import BalanceMeasurement, BalanceSettings
from services.config_service import ConfigService
from services.laser_balance import FourRunBalanceSession, LaserBalanceAnalyzer, STAGES
from services.laser_record_service import LaserRecordService


class LaserBalanceAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.settings = BalanceSettings(reference="manual", manual_rpm=9180, rotor_id="rotor1/left")
        self.rate = 10000

    def analyze(self, values, settings=None, hall_hz=153.0):
        analyzer = LaserBalanceAnalyzer(settings or self.settings)
        result = None
        for start in range(0, len(values), 500):
            result = analyzer.update(LaserChunk(
                values=values[start:start + 500], scan_rate=self.rate, start_sample=start
            ), hall_hz)
        return result

    def test_search_recovers_153hz_amplitude_without_fixed_154hz_cancellation(self):
        axis = np.arange(2 * self.rate) / self.rate
        values = 0.001 * np.cos(2 * np.pi * 153.17 * axis + 0.4)
        values += 0.08 * np.cos(2 * np.pi * 3982.0 * axis) + 0.6
        result = self.analyze(values)
        self.assertTrue(result.valid, result.reason)
        self.assertAlmostEqual(result.frequency_hz, 153.17, delta=0.03)
        self.assertAlmostEqual(result.amplitude_v, 0.001, delta=0.000002)
        self.assertEqual(result.start_sample, 0)
        self.assertEqual(result.end_sample, len(values))

    def test_nearest_qualified_peak_wins_even_when_another_peak_is_stronger(self):
        axis = np.arange(2 * self.rate) / self.rate
        values = 0.02 * np.cos(2 * np.pi * 153.17 * axis) + 0.1 * np.cos(2 * np.pi * 170 * axis)
        result = self.analyze(values)
        self.assertTrue(result.valid, result.reason)
        self.assertAlmostEqual(result.frequency_hz, 153.17, delta=0.03)
        self.assertAlmostEqual(result.amplitude_v, 0.02, delta=0.00004)
        # Same signal, changed reference: selection follows frequency distance.
        result = self.analyze(values, replace(self.settings, manual_rpm=170 * 60))
        self.assertTrue(result.valid, result.reason)
        self.assertAlmostEqual(result.frequency_hz, 170, delta=0.03)

    def test_nearby_noise_is_not_preferred_to_a_qualified_peak(self):
        axis = np.arange(2 * self.rate) / self.rate
        values = np.random.default_rng(42).normal(0, 0.02, len(axis))
        noise_only = self.analyze(values)
        self.assertFalse(noise_only.valid)
        self.assertIsNotNone(noise_only.tone)
        self.assertIn("仅诊断预览", noise_only.reason)
        self.assertIn("信噪比不足", noise_only.reason)
        values += 0.00005 * np.cos(2 * np.pi * 153 * axis)
        values += 0.02 * np.cos(2 * np.pi * 146 * axis)
        result = self.analyze(values)
        self.assertTrue(result.valid, result.reason)
        self.assertAlmostEqual(result.frequency_hz, 146, delta=0.03)

    def test_out_of_tolerance_peak_is_previewed_but_never_used_for_calibration(self):
        axis = np.arange(2 * self.rate) / self.rate
        settings = replace(self.settings, manual_rpm=6000)
        result = self.analyze(0.02 * np.cos(2 * np.pi * 153 * axis), settings)
        self.assertFalse(result.valid)
        self.assertIn("偏离", result.reason)
        self.assertIn("仅诊断预览", result.reason)
        self.assertIsNotNone(result.tone)
        self.assertAlmostEqual(result.frequency_hz, 153, delta=0.03)
        self.assertAlmostEqual(result.amplitude_v, 0.02, delta=0.00004)
        self.assertAlmostEqual(result.reference_rpm / 60, 100)
        session = FourRunBalanceSession(settings)
        session.start_run("preview")
        for index in range(3):
            session.update(replace(result, start_sample=index * 20000, end_sample=(index + 1) * 20000))
        self.assertEqual(len(session.windows), 0)
        with self.assertRaisesRegex(ValueError, "有效"):
            session.capture("baseline", 0.5, 10)
        self.assertFalse(result.record()["valid"])
        self.assertNotIn("tone", result.record())

    def test_weak_nearest_peak_is_previewed_instead_of_stronger_distant_peak(self):
        axis = np.arange(2 * self.rate) / self.rate
        values = np.random.default_rng(42).normal(0, 0.02, len(axis))
        values += 0.00005 * np.cos(2 * np.pi * 153 * axis)
        values += 0.02 * np.cos(2 * np.pi * 190 * axis)
        result = self.analyze(values, replace(self.settings, reference_tolerance=0.05))
        self.assertFalse(result.valid)
        self.assertIsNotNone(result.tone)
        self.assertLess(abs(result.frequency_hz - 153), 2)
        self.assertIn("信噪比不足", result.reason)

    def test_boundary_peak_does_not_hide_a_valid_interior_peak(self):
        axis = np.arange(2 * self.rate) / self.rate
        result = self.analyze(0.1 * np.cos(2 * np.pi * 100 * axis))
        self.assertFalse(result.valid)
        self.assertIn("边界", result.reason)
        values = np.cos(2 * np.pi * 100 * axis) + 0.02 * np.cos(2 * np.pi * 153 * axis)
        result = self.analyze(values)
        self.assertTrue(result.valid, result.reason)
        self.assertAlmostEqual(result.frequency_hz, 153, delta=0.03)

    def test_hall_ppr_and_ratio_are_used_before_reference_frequency_check(self):
        settings = replace(self.settings, reference="hall", hall_ppr=2, rotor_motor_ratio=0.5)
        axis = np.arange(2 * self.rate) / self.rate
        result = self.analyze(0.1 * np.cos(2 * np.pi * 153 * axis), settings, hall_hz=612)
        self.assertTrue(result.valid, result.reason)
        self.assertAlmostEqual(result.reference_rpm, 9180)
        result = self.analyze(0.1 * np.cos(2 * np.pi * 153 * axis), settings, hall_hz=0)
        self.assertFalse(result.valid)

    def test_optical_reference_measures_rotor_directly_without_motor_ratio(self):
        settings = replace(self.settings, reference="photoelectric", hall_ppr=1,
                           rotor_motor_ratio=0.25, manual_rpm=6000)
        settings.validate()
        axis = np.arange(2 * self.rate) / self.rate
        values = 0.01 * np.cos(2 * np.pi * 153 * axis)
        result = self.analyze(values, settings, hall_hz=153)
        self.assertTrue(result.valid, result.reason)
        self.assertAlmostEqual(result.reference_rpm, 9180)
        self.assertIn("光电转频", result.reason)
        self.assertNotEqual(settings.signature, replace(settings, reference="hall").signature)
        result = self.analyze(values, replace(settings, hall_ppr=2), hall_hz=306)
        self.assertTrue(result.valid, result.reason)
        self.assertAlmostEqual(result.reference_rpm, 9180)
        result = self.analyze(values, settings, hall_hz=0)
        self.assertFalse(result.valid)
        self.assertIsNone(result.tone)
        self.assertIn("无有效光电参考", result.reason)

    def test_bad_samples_flush_window_without_compressing_time(self):
        analyzer = LaserBalanceAnalyzer(self.settings)
        chunk = LaserChunk(values=np.ones(10000), scan_rate=self.rate)
        self.assertIsNone(analyzer.update(chunk, 153))
        result = analyzer.update(LaserChunk(values=np.array([0, -9999.0]), scan_rate=self.rate), 153)
        self.assertFalse(result.valid)
        self.assertEqual(analyzer._next_sample, 10002)
        self.assertEqual(len(analyzer._values), 0)
        self.assertIsNone(analyzer.update(chunk, 153))
        self.assertEqual(analyzer._next_sample, 20002)

    def test_missing_clipped_low_snr_and_out_of_range_are_not_measurements(self):
        result = self.analyze(np.full(20000, 10.0))
        self.assertFalse(result.valid)
        self.assertIsNone(result.tone)
        self.assertIn("量程", result.reason)
        result = self.analyze(np.zeros(20000))
        self.assertFalse(result.valid)
        self.assertIsNone(result.tone)
        axis = np.arange(20000) / self.rate
        settings = replace(self.settings, manual_rpm=6000)
        result = self.analyze(np.cos(2 * np.pi * 153 * axis), settings)
        self.assertFalse(result.valid)
        self.assertIn("偏离", result.reason)
        result = self.analyze(np.zeros(20000), replace(self.settings, search_max_hz=6000))
        self.assertFalse(result.valid)
        self.assertIn("奈奎斯特", result.reason)

    def test_unstable_rpm_rejected_and_recovery_uses_new_window(self):
        analyzer = LaserBalanceAnalyzer(replace(self.settings, reference="hall"))
        values = np.sin(2 * np.pi * 153 * np.arange(10000) / self.rate)
        self.assertIsNone(analyzer.update(LaserChunk(values=values, scan_rate=self.rate), 150))
        result = analyzer.update(LaserChunk(values=values, scan_rate=self.rate), 154)
        self.assertFalse(result.valid)
        self.assertIn("未稳定", result.reason)
        self.assertIsNotNone(result.tone)
        self.assertIn("仅诊断预览", result.reason)
        self.assertIsNone(analyzer.update(LaserChunk(values=values, scan_rate=self.rate), 153))
        result = analyzer.update(LaserChunk(values=values, scan_rate=self.rate), 153)
        self.assertTrue(result.valid, result.reason)
        self.assertEqual(result.start_sample, 20000)

    def test_unit_scaling_is_explicit_and_does_not_change_voltage_measurement(self):
        for quantity, unit in (("displacement", "μm"), ("velocity", "mm/s"), ("acceleration", "m/s²")):
            settings = replace(self.settings, quantity=quantity, units_per_volt=20)
            self.assertEqual(settings.display_unit, "mV")
            self.assertEqual(settings.display_factor, 1000)
            settings = replace(settings, scale_confirmed=True)
            self.assertEqual(settings.display_unit, unit)
            self.assertEqual(settings.display_factor, 20)
        with self.assertRaises(ValueError):
            replace(self.settings, units_per_volt=float("nan")).validate()


class FourRunCalibrationTest(unittest.TestCase):
    def setUp(self):
        self.settings = BalanceSettings(rotor_id="rotor1/fixtureA/left")
        self.session = FourRunBalanceSession(self.settings)
        self.coefficient = 0.002  # V peak per g*mm
        self.unbalance = 4.0 * np.exp(1j * np.deg2rad(35))
        self.trial_gmm = 5.0

    def window(self, amplitude, start=0, **changes):
        result = BalanceMeasurement(True, "test", time.time(), start, start + 20000,
                                    10000, self.settings.signature, frequency_hz=153.0,
                                    reference_rpm=9180, amplitude_v=amplitude, noise_v=1e-7, snr=10000)
        for key, value in changes.items():
            setattr(result, key, value)
        return result

    def fill(self, amplitude, run_id, frequency=153.0):
        self.session.start_run(run_id)
        for index in range(3):
            self.session.update(self.window(amplitude, start=index * 20000, frequency_hz=frequency))

    def calibrate(self):
        responses = [abs(self.unbalance) * self.coefficient] + [
            abs(self.unbalance + self.trial_gmm * np.exp(1j * np.deg2rad(angle))) * self.coefficient
            for angle in (0, 120, 240)
        ]
        for index, (stage, response) in enumerate(zip(STAGES, responses)):
            self.fill(response, str(index))
            self.session.capture(stage, mass_g=0.5, radius_mm=10)
        return self.session.solve()

    def test_known_trial_calibrates_gmm_and_independent_run_recovers_amount(self):
        calibration = self.calibrate()
        self.assertAlmostEqual(calibration["coefficient_v_per_gmm"], self.coefficient)
        self.assertAlmostEqual(calibration["baseline_equivalent_gmm"], 4)
        self.assertLess(calibration["model_residual"], 1e-12)
        with self.assertRaisesRegex(ValueError, "新运行"):
            self.session.estimate()
        self.fill(self.coefficient * 2.5, "new_measurement")
        self.assertAlmostEqual(self.session.estimate()["equivalent_unbalance_gmm"], 2.5)

    def test_speed_mismatch_missing_and_small_trial_are_rejected(self):
        with self.assertRaises(ValueError):
            self.session.solve()
        self.calibrate()
        self.fill(0.01, "new_measurement", frequency=160)
        with self.assertRaisesRegex(ValueError, "偏离"):
            self.session.estimate()
        for stage in STAGES:
            self.session.runs[stage]["amplitude_v"] = 0.01
        with self.assertRaisesRegex(ValueError, "不足"):
            self.session.solve()
        self.assertIsNone(self.session.calibration)

    def test_inconsistent_model_rejected(self):
        self.calibrate()
        self.session.runs["trial_0"]["amplitude_v"] *= 3
        with self.assertRaisesRegex(ValueError, "单面线性模型"):
            self.session.solve()

    def test_duplicate_run_wrong_mass_or_changed_settings_are_rejected(self):
        self.fill(0.01, "one")
        self.session.capture("baseline", 0.5, 10)
        with self.assertRaisesRegex(ValueError, "同一运行"):
            self.session.capture("trial_0", 0.5, 10)
        self.fill(0.02, "two")
        with self.assertRaisesRegex(ValueError, "同一试重"):
            self.session.capture("trial_0", 1.0, 10)
        self.session.update(self.window(0.02, settings_signature="different"))
        self.assertEqual(len(self.session.windows), 0)

    def test_bad_stale_noisy_or_overlapping_windows_are_not_captured(self):
        self.fill(0.01, "one")
        self.session.update(self.window(0.01, valid=False))
        with self.assertRaises(ValueError):
            self.session.summary()
        self.fill(0.01, "one")
        self.session.last_update = time.monotonic() - 100
        with self.assertRaisesRegex(ValueError, "过期"):
            self.session.summary()
        self.fill(0.01, "two")
        self.session.update(self.window(0.03, start=60000))
        with self.assertRaisesRegex(ValueError, "波动"):
            self.session.summary()
        self.fill(0.01, "three")
        self.session.update(self.window(0.01, start=30000))
        self.assertEqual(len(self.session.windows), 0)

    def test_report_snapshot_is_immutable_for_queued_writer(self):
        self.calibrate()
        report = self.session.report()
        self.session.runs.clear()
        self.assertEqual(len(report["runs"]), 4)

    def test_loading_report_recomputes_coefficient_and_rejects_changed_settings(self):
        self.calibrate()
        with tempfile.TemporaryDirectory() as temporary:
            report = self.session.report()
            report["calibration"]["coefficient_v_per_gmm"] = 12345
            path = Path(temporary) / "calibration.json"
            path.write_text(json.dumps(report))
            restored = FourRunBalanceSession.load_report(path, self.settings)
            self.assertAlmostEqual(restored.calibration["coefficient_v_per_gmm"], self.coefficient)
            self.assertEqual(len(restored.windows), 0)
            with self.assertRaises(ValueError):
                FourRunBalanceSession.load_report(path, replace(self.settings, rotor_id="other"))
            report["runs"]["trial_0"]["frequency_hz"] = 200
            path.write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                FourRunBalanceSession.load_report(path, self.settings)


class LaserBalanceRecordingTest(unittest.TestCase):
    def test_record_voltage_hall_samples_settings_and_measurement(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = LaserRecordService(Path(temporary))
            directory = service.start({"quantity": "acceleration", "scale_confirmed": False})
            service.append_waveform(LaserChunk(values=np.array([0.001, 0.002]), start_sample=100), {"motor_hall": 150})
            service.append_measurement({"kind": "measurement", "frequency_hz": 150})
            service.stop()
            self.assertIn("100", (directory / "laser_raw_signal.csv").read_text(encoding="utf-8-sig"))
            self.assertIn("150", (directory / "hall_reference_snapshots.csv").read_text(encoding="utf-8-sig"))
            self.assertEqual(json.loads((directory / "balance_measurements.jsonl").read_text())["frequency_hz"], 150)
            self.assertFalse(json.loads((directory / "measurement_metadata.json").read_text())["scale_confirmed"])
            report1 = service.export_balance_report({"schema_version": 1})
            report2 = service.export_balance_report({"schema_version": 1})
            self.assertNotEqual(report1, report2)
            self.assertTrue(report1.exists())

    def test_section_save_preserves_other_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "process.json"
            path.write_text(json.dumps({"other": {"value": 12}, "laser_balance": {}}))
            ConfigService(Path(temporary)).save_section("process", "laser_balance", {"quantity": "velocity"})
            result = json.loads(path.read_text())
            self.assertEqual(result["other"], {"value": 12})
            self.assertEqual(result["laser_balance"]["quantity"], "velocity")


if __name__ == "__main__":
    unittest.main()
