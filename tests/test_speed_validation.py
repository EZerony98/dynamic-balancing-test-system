"""FIO3 rotor photoelectric speed is shared by the two real acquisition modes."""

import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from main import CONFIG
from models.data import AcquisitionChunk, BalancingChunk, PiezoChunk
from ui.main_window import MainWindow
from utils.paths import PROJECT_DIR


class SpeedValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.save_patch = patch("ui.main_window.ConfigService.save_section")
        self.save_patch.start()
        config = CONFIG.load_all()
        config["process"]["piezo_balance"]["ppr"] = 1
        self.window = MainWindow(config, Path(PROJECT_DIR))

    def tearDown(self) -> None:
        self.window.close()
        self.application.processEvents()
        self.save_patch.stop()

    def piezo(self) -> PiezoChunk:
        return PiezoChunk(channels={"left": np.zeros(2500), "right": np.zeros(2500)})

    def test_short_dropout_is_held_and_reported(self) -> None:
        frequency, quality = self.window._validated_speed(151.3)
        self.assertAlmostEqual(frequency, 151.3)
        for _ in range(5):
            frequency, quality = self.window._validated_speed(97.0)
        self.assertAlmostEqual(frequency, 151.3)
        self.assertEqual(quality, "脉冲异常/保持")

    def test_persistent_change_is_eventually_accepted(self) -> None:
        self.window._validated_speed(151.3)
        for _ in range(12):
            frequency, quality = self.window._validated_speed(100.0)
        self.assertAlmostEqual(frequency, 100.0)
        self.assertEqual(quality, "已确认转速变化")

    def test_optical_frequency_is_converted_with_editable_ppr(self) -> None:
        self.window.motor_hall_ppr_spin_box.setValue(2)
        zeros = np.zeros(1000, dtype=float)
        self.window._update_acquisition(AcquisitionChunk(
            balancing=BalancingChunk(channels={name: zeros for name in (
                "speed", "position_a", "position_b", "force_a", "force_b")}),
            hardware_frequencies={"speed": 150.0, "position_a": 0.0,
                                  "position_b": 0.0, "motor_hall": 300.0},
        ))
        self.assertEqual(self.window.motor_hall_speed_card.value_label.text(), "9000")
        self.assertIn("PPR 2", self.window.motor_hall_speed_card.detail_label.text())

    def test_ppr_four_is_a_single_explicit_divisor_not_ef_averaging(self) -> None:
        self.window.motor_hall_ppr_spin_box.setValue(4)
        self.window._update_motor_hall_speed(560.0)
        self.assertEqual(self.window.motor_hall_speed_card.value_label.text(), "8400")
        detail = self.window.motor_hall_speed_card.detail_label.text()
        self.assertIn("原始 560", detail)
        self.assertIn("PPR 4", detail)

    def test_optical_card_does_not_call_noisy_raw_frequency_stable(self) -> None:
        self.window._update_motor_hall_speed(150.0)
        self.window._update_motor_hall_speed(168.0)
        detail = self.window.motor_hall_speed_card.detail_label.text()
        self.assertIn("原始 168", detail)
        self.assertIn(
            "瞬时跳变已滤波/保持 1/%d" %
            self.window.balance_panel.settings.reference_change_confirm_updates,
            detail,
        )
        self.assertEqual(self.window.motor_hall_speed_card.value_label.text(), "9000")

    def test_optical_card_accepts_a_consistent_new_speed(self) -> None:
        self.window._update_motor_hall_speed(150.0)
        for _ in range(
            self.window.balance_panel.settings.reference_change_confirm_updates
        ):
            self.window._update_motor_hall_speed(168.0)
        self.assertEqual(self.window.motor_hall_speed_card.value_label.text(), "10080")
        self.assertIn("已连续确认新转速",
                      self.window.motor_hall_speed_card.detail_label.text())

    def test_fft_display_defaults_to_three_hundred_hz(self) -> None:
        self.assertEqual(self.window.fft_limit_spin_box.value(), 300)

    def test_raw_four_x_optical_readback_is_displayed_but_corrected_frequency_drives_rpm(self) -> None:
        piezo = self.piezo()
        self.window._update_acquisition(AcquisitionChunk(
            piezo=piezo,
            hardware_frequencies={"motor_hall": 156.25},
            hardware_raw_frequencies={"motor_hall": 625.0},
        ))
        self.assertEqual(self.window.motor_hall_speed_card.value_label.text(), "9375")
        detail = self.window.motor_hall_speed_card.detail_label.text()
        self.assertIn("原始 625", detail)
        self.assertIn("修正 156.2", detail)
        self.assertIn("PPR 1", detail)

    def test_piezo_updates_speed_without_a_five_item_chunk(self) -> None:
        self.window.motor_hall_ppr_spin_box.setValue(2)
        piezo = self.piezo()
        self.window._update_acquisition(AcquisitionChunk(
            piezo=piezo, hardware_frequencies={"motor_hall": 300.0}))
        self.assertIs(self.window.latest_piezo, piezo)
        self.assertIsNone(self.window.latest_balancing)
        self.assertEqual(self.window.speed_card.value_label.text(), "--")
        self.assertEqual(self.window.motor_hall_speed_card.value_label.text(), "9000")
        self.assertIn("FIO3 光电", self.window.statusBar().currentMessage())

    def test_tach_dropout_does_not_stop_piezo_stream(self) -> None:
        piezo = self.piezo()
        self.window._update_acquisition(AcquisitionChunk(
            piezo=piezo, hardware_frequencies={"motor_hall": 150.0}))
        for _ in range(20):
            self.window._update_acquisition(AcquisitionChunk(piezo=piezo))
        self.assertIs(self.window.latest_piezo, piezo)
        self.assertEqual(self.window.motor_hall_speed_card.value_label.text(), "0")
        self.assertIn("无光电脉冲", self.window.motor_hall_speed_card.detail_label.text())

    def test_piezo_starts_directly_and_resets_old_speed(self) -> None:
        self.window.start_requested.disconnect(self.window.worker.start)
        requested = []
        self.window.start_requested.connect(requested.append)
        self.window._update_motor_hall_speed(150.0)
        self.window.ui.spectrumButton.click()
        self.assertEqual(requested, ["piezo"])
        self.assertEqual(self.window.stable_motor_hall_hz, 0.0)
        self.window._stream_started(50_000.0, "piezo")
        self.assertIn("AIN0/AIN3", self.window.ui.laserDeviceLabel.text())
        self.window._stream_stopped()
        self.assertEqual(self.window.motor_hall_speed_card.value_label.text(), "--")
        self.window._start_mode("balancing")
        self.assertEqual(requested, ["piezo", "balancing"])


if __name__ == "__main__":
    unittest.main()
