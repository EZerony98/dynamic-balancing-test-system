import unittest

import numpy as np

from models.data import HardwarePulseMeasurement
from services.signal_processing import (
    HardwarePulsePhaseEstimator,
    PulseFrequencyEstimator,
    PulsePhaseEstimator,
    ac_rms,
)


class SignalProcessingTest(unittest.TestCase):
    def test_frequency_is_stable_across_chunk_boundaries(self) -> None:
        scan_rate = 20_000.0
        expected_frequency = 76.0
        time_axis = np.arange(int(scan_rate * 1.5)) / scan_rate
        pulse = (np.sin(2.0 * np.pi * expected_frequency * time_axis) > 0.8).astype(float)
        estimator = PulseFrequencyEstimator(window_seconds=1.0, smoothing=0.15)
        estimates = []
        for start in range(0, len(pulse), 1000):
            estimates.append(estimator.update(pulse[start : start + 1000], scan_rate))
        self.assertAlmostEqual(estimates[-1], expected_frequency, delta=0.5)
        self.assertLess(np.ptp(estimates[-5:]), 0.5)

    def test_ac_rms_ignores_dc_offset(self) -> None:
        time_axis = np.arange(10_000) / 10_000.0
        values = 2.5 + 2.0 * np.sin(2.0 * np.pi * 50.0 * time_axis)
        self.assertAlmostEqual(ac_rms(values), np.sqrt(2.0), places=3)

    def test_phase_is_measured_across_uneven_chunk_boundaries(self) -> None:
        scan_rate = 20_000.0
        period = 200
        sample_count = 30_000
        indexes = np.arange(sample_count)
        reference = ((indexes % period) < 8).astype(float)
        target_a = (((indexes - 50) % period) < 8).astype(float)
        estimator = PulsePhaseEstimator(window_seconds=1.0)
        estimate = None
        for start in range(0, sample_count, 733):
            estimate = estimator.update(
                reference[start : start + 733],
                target_a[start : start + 733],
                scan_rate,
            )
        self.assertIsNotNone(estimate)
        self.assertTrue(estimate.valid)
        self.assertAlmostEqual(estimate.angle_degrees, 90.0, delta=0.2)

    def test_phase_uses_circular_mean_near_zero_degrees(self) -> None:
        scan_rate = 20_000.0
        period = 200
        sample_count = 20_000
        indexes = np.arange(sample_count)
        reference = ((indexes % period) < 6).astype(float)
        target = (((indexes - 199) % period) < 6).astype(float)
        estimator = PulsePhaseEstimator(window_seconds=0.8)
        estimate = estimator.update(reference, target, scan_rate)
        self.assertTrue(estimate.valid)
        self.assertAlmostEqual(estimate.angle_degrees, 358.2, delta=0.2)

    def test_phase_direction_and_offset_are_configurable(self) -> None:
        scan_rate = 20_000.0
        period = 200
        indexes = np.arange(20_000)
        reference = ((indexes % period) < 8).astype(float)
        target = (((indexes - 50) % period) < 8).astype(float)
        estimator = PulsePhaseEstimator(
            window_seconds=0.8, direction=-1, offset_degrees=10.0
        )
        estimate = estimator.update(reference, target, scan_rate)
        self.assertTrue(estimate.valid)
        self.assertAlmostEqual(estimate.angle_degrees, 280.0, delta=0.2)

    def test_phase_is_invalid_when_target_pulse_is_missing(self) -> None:
        scan_rate = 20_000.0
        indexes = np.arange(20_000)
        reference = ((indexes % 200) < 8).astype(float)
        estimator = PulsePhaseEstimator(window_seconds=0.8)
        estimate = estimator.update(reference, np.zeros_like(reference), scan_rate)
        self.assertFalse(estimate.valid)
        self.assertIsNone(estimate.angle_degrees)
        self.assertEqual(estimate.quality, "目标脉冲缺失")

    def test_hardware_timestamp_phase_is_stable(self) -> None:
        estimator = HardwarePulsePhaseEstimator()
        clock_hz = 40_000_000.0
        period_ticks = 264_000
        estimate = None
        for cycle in range(8):
            reference_ticks = 1_000_000 + cycle * period_ticks
            target_ticks = reference_ticks + period_ticks // 3
            reference = HardwarePulseMeasurement(
                frequency_hz=clock_hz / period_ticks,
                period_seconds=period_ticks / clock_hz,
                edge_timestamp_ticks=reference_ticks,
                timestamp_clock_hz=clock_hz,
            )
            target = HardwarePulseMeasurement(
                frequency_hz=clock_hz / period_ticks,
                period_seconds=period_ticks / clock_hz,
                edge_timestamp_ticks=target_ticks,
                timestamp_clock_hz=clock_hz,
            )
            estimate = estimator.update(reference, target, cycle * 0.05)
        self.assertIsNotNone(estimate)
        self.assertTrue(estimate.valid)
        self.assertEqual(estimate.quality, "硬件稳定")
        self.assertAlmostEqual(estimate.angle_degrees, 120.0, delta=0.01)

    def test_hardware_timestamp_phase_handles_timer_wrap(self) -> None:
        estimator = HardwarePulsePhaseEstimator(minimum_samples=2)
        clock_hz = 40_000_000.0
        period_ticks = 240_000
        estimate = None
        for cycle in range(3):
            reference_ticks = (2**32 - 100_000 + cycle * period_ticks) % 2**32
            target_ticks = (reference_ticks + period_ticks // 4) % 2**32
            reference = HardwarePulseMeasurement(
                frequency_hz=clock_hz / period_ticks,
                period_seconds=period_ticks / clock_hz,
                edge_timestamp_ticks=reference_ticks,
                timestamp_clock_hz=clock_hz,
            )
            target = HardwarePulseMeasurement(
                frequency_hz=clock_hz / period_ticks,
                period_seconds=period_ticks / clock_hz,
                edge_timestamp_ticks=target_ticks,
                timestamp_clock_hz=clock_hz,
            )
            estimate = estimator.update(reference, target, cycle * 0.05)
        self.assertTrue(estimate.valid)
        self.assertAlmostEqual(estimate.angle_degrees, 90.0, delta=0.01)

    def test_hardware_phase_holds_one_short_read_anomaly(self) -> None:
        estimator = HardwarePulsePhaseEstimator(
            minimum_samples=2, hold_seconds=0.6
        )
        clock_hz = 40_000_000.0
        period_ticks = 240_000
        reference = target = None
        for cycle in range(3):
            reference_ticks = 2_000_000 + cycle * period_ticks
            reference = HardwarePulseMeasurement(
                frequency_hz=clock_hz / period_ticks,
                period_seconds=period_ticks / clock_hz,
                edge_timestamp_ticks=reference_ticks,
                timestamp_clock_hz=clock_hz,
            )
            target = HardwarePulseMeasurement(
                frequency_hz=clock_hz / period_ticks,
                period_seconds=period_ticks / clock_hz,
                edge_timestamp_ticks=reference_ticks + period_ticks // 2,
                timestamp_clock_hz=clock_hz,
            )
            estimator.update(reference, target, cycle * 0.05)
        estimate = estimator.update(reference, None, 0.20)
        self.assertTrue(estimate.valid)
        self.assertEqual(estimate.quality, "硬件稳定")
        self.assertAlmostEqual(estimate.angle_degrees, 180.0, delta=0.01)

    def test_hardware_phase_reports_persistent_missing_target(self) -> None:
        estimator = HardwarePulsePhaseEstimator(
            minimum_samples=2, hold_seconds=0.2
        )
        clock_hz = 40_000_000.0
        period_ticks = 240_000
        reference = target = None
        for cycle in range(3):
            reference_ticks = 3_000_000 + cycle * period_ticks
            reference = HardwarePulseMeasurement(
                frequency_hz=clock_hz / period_ticks,
                period_seconds=period_ticks / clock_hz,
                edge_timestamp_ticks=reference_ticks,
                timestamp_clock_hz=clock_hz,
            )
            target = HardwarePulseMeasurement(
                frequency_hz=clock_hz / period_ticks,
                period_seconds=period_ticks / clock_hz,
                edge_timestamp_ticks=reference_ticks + period_ticks // 2,
                timestamp_clock_hz=clock_hz,
            )
            estimator.update(reference, target, cycle * 0.05)
        estimate = estimator.update(reference, None, 0.50)
        self.assertFalse(estimate.valid)
        self.assertIsNone(estimate.angle_degrees)
        self.assertEqual(estimate.quality, "目标硬件脉冲缺失")


if __name__ == "__main__":
    unittest.main()
