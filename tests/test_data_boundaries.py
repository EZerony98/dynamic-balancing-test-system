import unittest

import numpy as np

from drivers.labjack_t7 import LabJackT7Source
from models.data import AcquisitionChunk, BalancingChunk, PiezoChunk


class IndependentDataModelsTest(unittest.TestCase):
    def test_balancing_chunk_has_only_five_device_outputs(self) -> None:
        chunk = BalancingChunk(
            channels={
                "speed": np.zeros(10),
                "position_a": np.zeros(10),
                "position_b": np.zeros(10),
                "force_a": np.zeros(10),
                "force_b": np.zeros(10),
            }
        )
        self.assertEqual(len(chunk.channels), 5)
        self.assertNotIn("piezo_left", chunk.channels)

    def test_piezo_chunk_keeps_two_amplifier_outputs_and_reference(self) -> None:
        chunk = PiezoChunk(
            channels={"left": np.ones(10), "right": np.zeros(10)},
            reference_state=np.ones(10),
        )
        self.assertEqual(set(chunk.channels), {"left", "right"})
        self.assertEqual(len(chunk.reference_state), 10)

    def test_piezo_stream_does_not_require_balancing_data(self) -> None:
        piezo = PiezoChunk(channels={"left": np.zeros(4), "right": np.zeros(4)})
        acquisition = AcquisitionChunk(piezo=piezo)
        self.assertIsNone(acquisition.balancing)
        self.assertIs(acquisition.piezo, piezo)

    def test_motor_hall_uses_fio3_for_frequency_and_synchronous_stream(self) -> None:
        self.assertEqual(LabJackT7Source.PULSE_DIO["motor_hall"], "DIO3")
        self.assertNotIn("motor_hall", LabJackT7Source.BALANCING_NAMES)


if __name__ == "__main__":
    unittest.main()
