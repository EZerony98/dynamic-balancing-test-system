import unittest

import numpy as np

from models.data import ChannelCalibration, TargetToneAnalysis
from services.spectrum import (
    TargetToneStabilizer,
    extract_target_tone,
    sampling_budget,
    spectrum,
)


class SpectrumTest(unittest.TestCase):
    def test_missing_samples_are_rejected_instead_of_closing_time_gaps(self) -> None:
        for bad in (float("nan"), float("inf"), -9999.0):
            with self.assertRaises(ValueError):
                spectrum(np.array([0, 1, bad, 0]), 1000)
            with self.assertRaises(ValueError):
                extract_target_tone(np.array([0, 1, bad, 0]), 1000, 150)

    def test_voltage_frequency_is_within_one_fft_bin(self) -> None:
        scan_rate = 100_000
        fft_size = 8192
        expected_frequency = 1234.0
        time_axis = np.arange(fft_size) / scan_rate
        voltage = 2.5 + np.sin(2.0 * np.pi * expected_frequency * time_axis)
        _, _, metrics = spectrum(voltage, scan_rate, fft_size=fft_size)
        self.assertLessEqual(
            abs(metrics.dominant_frequency - expected_frequency),
            scan_rate / fft_size,
        )

    def test_hardware_gains(self) -> None:
        for gain in (1.0, 1.99, 4.98, 10.0, 25.7):
            calibration = ChannelCalibration("AIN0", hardware_gain=gain)
            self.assertTrue(np.allclose(calibration.convert(np.array([gain])), [1.0]))

    def test_separate_stream_modes_use_full_t7_budget(self) -> None:
        balancing_ok, _ = sampling_budget(20_000, analog=2, digital=3)
        piezo_ok, _ = sampling_budget(30_000, analog=2, digital=1)
        over_limit, _ = sampling_budget(33_334, analog=2, digital=1)
        self.assertTrue(balancing_ok)
        self.assertTrue(piezo_ok)
        self.assertFalse(over_limit)

    def test_exact_target_tone_is_extracted_from_mixed_signal(self) -> None:
        scan_rate = 16_000.0
        size = 8192
        target_frequency = 154.0
        expected_amplitude = 0.08
        expected_phase = 37.0
        time_axis = np.arange(size) / scan_rate
        values = (
            expected_amplitude
            * np.cos(
                2.0 * np.pi * target_frequency * time_axis
                + np.deg2rad(expected_phase)
            )
            + 0.35 * np.sin(2.0 * np.pi * 3982.0 * time_axis)
            + 0.4
        )
        tone = extract_target_tone(values, scan_rate, target_frequency)
        self.assertAlmostEqual(tone.frequency, target_frequency)
        self.assertAlmostEqual(tone.amplitude, expected_amplitude, delta=0.0002)
        self.assertAlmostEqual(tone.rms, expected_amplitude / np.sqrt(2), delta=0.0002)
        self.assertAlmostEqual(tone.phase_degrees, expected_phase, delta=0.2)
        self.assertGreater(len(tone.waveform), 0)

    def test_target_tone_does_not_copy_other_frequency_peak(self) -> None:
        scan_rate = 16_000.0
        size = 8192
        time_axis = np.arange(size) / scan_rate
        values = 0.5 * np.sin(2.0 * np.pi * 3982.0 * time_axis)
        tone = extract_target_tone(values, scan_rate, 154.0)
        self.assertLess(tone.amplitude, 0.001)

    def test_target_tone_stabilizer_reduces_single_update_jump(self) -> None:
        stabilizer = TargetToneStabilizer(alpha=0.08)
        time_axis = np.linspace(0.0, 0.05, 100)

        def tone(amplitude: float) -> TargetToneAnalysis:
            return TargetToneAnalysis(
                frequency=154.0,
                amplitude=amplitude,
                rms=amplitude / np.sqrt(2.0),
                phase_degrees=30.0,
                time_seconds=time_axis,
                waveform=amplitude * np.cos(2.0 * np.pi * 154.0 * time_axis),
            )

        first = stabilizer.update(tone(0.001))
        second = stabilizer.update(tone(0.010))
        self.assertAlmostEqual(first.amplitude, 0.001)
        self.assertLess(second.amplitude, 0.003)


if __name__ == "__main__":
    unittest.main()
