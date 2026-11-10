"""Hardware-free tests for selectable NI/LabJack acquisition contracts."""

import json
import unittest
from pathlib import Path

import numpy as np

from drivers.vibration_source import (
    VibrationAcquisitionSource,
    _resample,
    _select_ni9234_module,
)
from models.data import AcquisitionChunk, PiezoChunk


class _FakeNiTask:
    def read(self, number_of_samples_per_channel, timeout):
        return np.ones(number_of_samples_per_channel, dtype=float)


class _FakeLabJack:
    def read(self):
        return AcquisitionChunk(
            piezo=PiezoChunk(
                channels={
                    "left": np.zeros(4),
                    "right": np.array([.1, .2, .3, .4]),
                },
                reference_state=np.array([1.0, 1.0, 0.0, 0.0]),
                scan_rate=4_000.0,
            ),
            hardware_frequencies={"motor_hall": 125.0},
        )


class VibrationSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parents[1] / "config" / "devices.json"
        cls.device = json.loads(path.read_text(encoding="utf-8"))["acquisition_device"]

    def test_resample_keeps_endpoints_and_requested_count(self):
        result = _resample(np.array([0.0, 1.0, 2.0]), 5)
        self.assertEqual(len(result), 5)
        self.assertAlmostEqual(result[0], 0.0)
        self.assertAlmostEqual(result[-1], 2.0)

    def test_configured_ni_module_is_used_when_present(self):
        inventory = [("MyChassisMod2", "NI 9234"), ("Dev1", "USB-6001")]
        self.assertEqual(
            _select_ni9234_module("MyChassisMod2", inventory),
            "MyChassisMod2",
        )

    def test_sole_ni9234_is_auto_selected_when_default_alias_is_wrong(self):
        inventory = [("BalanceRigMod1", "NI 9234"), ("BalanceRig", "cDAQ-9174")]
        self.assertEqual(
            _select_ni9234_module("cDAQ1Mod1", inventory),
            "BalanceRigMod1",
        )

    def test_missing_ni9234_reports_discovered_inventory(self):
        with self.assertRaisesRegex(RuntimeError, "USB6001"):
            _select_ni9234_module(
                "cDAQ1Mod1", [("USB6001", "USB-6001")]
            )

    def test_one_iepe_side_uses_ni_values_and_disables_cross_device_phase(self):
        profiles = {
            "left": {
                "sensor_type": "accelerometer",
                "model": "PCB 353B15",
                "sensitivity_mv_per_g": 10.0,
                "display_unit": "g",
                "polarity": 1,
            },
            "right": {
                "sensor_type": "piezo",
                "model": "piezo",
                "sensitivity_mv_per_g": 0.0,
                "display_unit": "mV",
                "polarity": 1,
            },
        }
        source = VibrationAcquisitionSource(self.device, profiles)
        source.labjack = _FakeLabJack()
        source.task = _FakeNiTask()
        source._ni_sides = ["left"]
        source.scans_per_read = 4
        source.scan_rate = 4_000.0
        chunk = source.read().piezo
        np.testing.assert_allclose(chunk.channels["left"], .01)
        np.testing.assert_allclose(chunk.channels["right"], [.1, .2, .3, .4])
        self.assertFalse(chunk.channels_synchronized)
        self.assertFalse(chunk.reference_synchronized)
        self.assertEqual(chunk.acquisition_backend, "ni9234+labjack")


if __name__ == "__main__":
    unittest.main()

