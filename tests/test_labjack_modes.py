"""Acquisition-mode regression tests; no physical T7 is opened."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from drivers.labjack_t7 import LabJackT7Source


class LabJackModesTest(unittest.TestCase):
    def setUp(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "config" / "devices.json"
        config = json.loads(config_path.read_text())["acquisition_device"]
        config["serial"] = "TEST_ONLY"
        self.source = LabJackT7Source(config)
        self.patcher = patch("drivers.labjack_t7.ljm")
        self.ljm = self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.source.close)
        self.ljm.openS.return_value = 123
        self.ljm.getHandleInfo.return_value = (7, 1, 123456, 0, 0, 0)
        addresses = {"FIO0": 2000, "FIO1": 2001, "FIO2": 2002, "DIO3": 2003,
                     "AIN0": 0, "AIN1": 2, "AIN3": 6}
        self.ljm.namesToAddresses.side_effect = lambda count, names: (
            [addresses[name] for name in names], [0] * count
        )
        self.ljm.eStreamStart.side_effect = lambda handle, scans, count, names, rate: rate

    def _enabled_inputs(self) -> set:
        return {
            call.args[1] for call in self.ljm.eWriteName.call_args_list
            if call.args[1].endswith("_EF_ENABLE") and call.args[2] == 1
        }

    def test_piezo_reads_optical_reference_with_two_analog_channels(self) -> None:
        info = self.source.open("piezo")
        self.assertEqual(info["scan_rate"], 30_000.0)
        self.ljm.eStreamStart.assert_called_once_with(
            123, 1500, 3, [0, 6, 2003], 30_000.0
        )
        self.assertEqual(self._enabled_inputs(), {"DIO3_EF_ENABLE"})
        self.ljm.eWriteName.assert_any_call(123, "DIO3_EF_CONFIG_A", 2)
        self.ljm.eWriteName.assert_any_call(123, "DIO3_EF_CONFIG_B", 4)
        self.ljm.eStreamRead.return_value = (np.arange(4500), 2, 3)
        self.ljm.eReadNames.return_value = [1.0 / 600, 100]
        chunk = self.source.read()
        self.ljm.eReadNames.assert_called_once_with(
            123, 2, ["DIO3_EF_READ_A_F", "DIO3_EF_READ_B"]
        )
        self.assertIsNone(chunk.balancing)
        self.assertIsNone(chunk.laser)
        np.testing.assert_array_equal(chunk.piezo.channels["left"], np.arange(0, 4500, 3))
        np.testing.assert_array_equal(chunk.piezo.channels["right"], np.arange(1, 4500, 3))
        np.testing.assert_array_equal(chunk.piezo.reference_state, np.arange(2, 4500, 3))
        self.assertEqual(chunk.piezo.scan_rate, 30_000.0)
        self.assertEqual((chunk.piezo.device_backlog, chunk.piezo.ljm_backlog), (2, 3))
        self.assertEqual(chunk.hardware_frequencies, {"motor_hall": 150.0})
        self.assertEqual(chunk.hardware_raw_frequencies, {"motor_hall": 600.0})
        self.assertEqual(chunk.hardware_pulses["motor_hall"].frequency_hz, 600.0)
        self.assertEqual(chunk.hardware_pulses["motor_hall"].edge_timestamp_ticks, 100)

    def test_balancing_keeps_five_stream_inputs_and_all_pulse_readings(self) -> None:
        self.source.open("balancing")
        self.ljm.eStreamStart.assert_called_once_with(
            123, 1000, 5, [2000, 2001, 2002, 0, 6], 20_000.0
        )
        self.assertEqual(self._enabled_inputs(), {
            "DIO0_EF_ENABLE", "DIO1_EF_ENABLE", "DIO2_EF_ENABLE", "DIO3_EF_ENABLE"
        })
        for dio in ("DIO0", "DIO1", "DIO2"):
            self.ljm.eWriteName.assert_any_call(123, dio + "_EF_CONFIG_A", 3)
        self.ljm.eWriteName.assert_any_call(123, "DIO3_EF_CONFIG_A", 2)
        self.ljm.eStreamRead.return_value = (np.zeros(5000), 0, 0)
        self.ljm.eReadNames.return_value = [1.0 / 150, 100] * 3 + [1.0 / 1200, 200]
        chunk = self.source.read()
        self.assertIsNone(chunk.laser)
        self.assertEqual(set(chunk.balancing.channels), set(self.source.BALANCING_NAMES))
        self.assertEqual(chunk.hardware_frequencies["motor_hall"], 300.0)
        self.assertEqual(chunk.hardware_raw_frequencies["motor_hall"], 1200.0)
        self.assertEqual(chunk.hardware_frequencies["speed"], 150.0)

    def test_mode_switch_clears_cached_hall_and_restores_pulse_inputs(self) -> None:
        for mode in ("balancing", "piezo", "balancing"):
            self.ljm.reset_mock()
            self.source.open(mode)
            expected = {"motor_hall"} if mode == "piezo" else set(self.source.PULSE_DIO)
            self.assertEqual(set(self.source._active_pulse_dio), expected)
            self.assertEqual(self._enabled_inputs(), {
                self.source.PULSE_DIO[name] + "_EF_ENABLE" for name in expected
            })
            self.ljm.eReadNames.side_effect = RuntimeError("temporary read failure")
            self.assertTrue(all(
                pulse.frequency_hz == 0.0
                for pulse in self.source._read_hardware_pulses().values()
            ))
            self.ljm.eReadNames.side_effect = None
            self.ljm.eReadNames.return_value = [1.0 / 150, 100] * len(expected)
            self.assertEqual(self.source._read_hardware_pulses()["motor_hall"].frequency_hz, 150)
            self.source.close()
            self.ljm.eStreamStop.assert_called_once_with(123)
            self.ljm.close.assert_called_once_with(123)
            self.assertFalse(self.source._active_pulse_dio)

    def test_missing_or_stale_reference_does_not_interrupt_piezo(self) -> None:
        self.source.open("piezo")
        self.ljm.eStreamRead.return_value = (np.ones(4500), 0, 0)
        self.ljm.eReadNames.return_value = [1.0 / 600, 100]
        with patch("drivers.labjack_t7.time.monotonic") as now:
            now.return_value = 10.0
            self.assertEqual(self.source.read().hardware_frequencies["motor_hall"], 150)
            # Bounded cached result during a short command-response failure.
            self.ljm.eReadNames.side_effect = RuntimeError("read failed")
            now.return_value = 10.1
            self.assertEqual(self.source.read().hardware_frequencies["motor_hall"], 150)
            now.return_value = 10.3
            chunk = self.source.read()
            self.assertEqual(chunk.hardware_frequencies["motor_hall"], 0)
            self.assertEqual(len(chunk.piezo.channels["left"]), 1500)
            # A successful read of an unchanged timestamp is also stale.
            self.ljm.eReadNames.side_effect = None
            now.return_value = 10.4
            self.assertEqual(self.source.read().hardware_frequencies["motor_hall"], 0)
            self.ljm.eReadNames.return_value = [1.0 / 640, 200]
            self.assertEqual(self.source.read().hardware_frequencies["motor_hall"], 160)


if __name__ == "__main__":
    unittest.main()
