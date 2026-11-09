from __future__ import annotations
from dataclasses import dataclass, field
import time
import numpy as np


@dataclass(frozen=True)
class AcquisitionConfig:
    """Hardware acquisition settings passed from the UI to the worker.

    Logical channel names stay AIN0/AIN1/TACH throughout the application so
    the plotting and balancing code does not depend on a vendor's channel
    naming scheme.
    """

    backend: str = "labjack_usb"
    scan_rate: float = 30_000.0
    samples_per_read: int = 6_000

    labjack_left_channel: str = "AIN0"
    labjack_right_channel: str = "AIN3"
    labjack_tach_channel: str = "FIO3"
    left_sensor_type: str = "piezo"
    right_sensor_type: str = "piezo"
    tach_ppr: int = 1
    tach_edge: str = "falling"

    ni_module: str = "cDAQ1Mod1"
    ni_left_channel: str = "ai0"
    ni_right_channel: str = "ai1"
    ni_left_sensitivity_mv_g: float = 10.0
    ni_right_sensitivity_mv_g: float = 10.0
    ni_iepe_current_ma: float = 2.0
    ni_tach_connection: str = "USB"

@dataclass
class ChannelCalibration:
    channel: str
    hardware_gain: float = 1.0
    offset_v: float = 0.0
    sensitivity: float = 1.0
    unit: str = "V"

    def convert(self, output: np.ndarray) -> np.ndarray:
        return (output / self.hardware_gain - self.offset_v) * self.sensitivity

@dataclass
class StreamChunk:
    timestamp: float = field(default_factory=time.time)
    channels: dict[str, np.ndarray] = field(default_factory=dict)
    scan_rate: float = 100_000.0
    device_backlog: int = 0
    ljm_backlog: int = 0
    dropped_samples: int = 0
    channel_units: dict[str, str] = field(default_factory=dict)
    channel_sources: dict[str, str] = field(default_factory=dict)
    acquisition_backend: str = ""
    reference_ppr: int = 1
    reference_scan_rate: float = 0.0
    channels_synchronized: bool = True

@dataclass
class Metrics:
    rms: float = 0.0
    vpp: float = 0.0
    minimum: float = 0.0
    maximum: float = 0.0
    mean: float = 0.0
    dominant_frequency: float = 0.0
    dominant_amplitude: float = 0.0
