"""Acquisition data models for two independent measurement systems."""

from dataclasses import dataclass, field
import time
from typing import Dict, Optional

import numpy as np


@dataclass
class ChannelCalibration:
    channel: str
    hardware_gain: float = 1.0
    offset_v: float = 0.0
    sensitivity: float = 1.0
    unit: str = "V"

    def convert(self, output_voltage: np.ndarray) -> np.ndarray:
        input_voltage = output_voltage / self.hardware_gain
        return (input_voltage - self.offset_v) * self.sensitivity


@dataclass
class BalancingChunk:
    """Five electrical outputs from the dynamic-balancing device."""

    timestamp: float = field(default_factory=time.time)
    channels: Dict[str, np.ndarray] = field(default_factory=dict)
    scan_rate: float = 20_000.0
    device_backlog: int = 0
    ljm_backlog: int = 0
    dropped_samples: int = 0


@dataclass
class LaserChunk:
    """Independent laser measurement voltage signal."""

    timestamp: float = field(default_factory=time.time)
    values: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    scan_rate: float = 100_000.0
    device_backlog: int = 0
    ljm_backlog: int = 0
    dropped_samples: int = 0
    start_sample: int = 0


@dataclass
class PiezoChunk:
    """Two piezo voltages and an optional sample-aligned rotor reference."""

    timestamp: float = field(default_factory=time.time)
    channels: Dict[str, np.ndarray] = field(default_factory=dict)
    reference_state: np.ndarray = field(default_factory=lambda: np.array([], dtype=float))
    scan_rate: float = 30_000.0
    device_backlog: int = 0
    ljm_backlog: int = 0
    dropped_samples: int = 0
    start_sample: int = 0
    # NI channels share a clock with each other, while a mixed NI/LabJack pair
    # does not. FIO3 is sample-aligned only in pure LabJack mode.
    channels_synchronized: bool = True
    reference_synchronized: bool = True
    acquisition_backend: str = "labjack"


@dataclass
class Metrics:
    rms: float = 0.0
    vpp: float = 0.0
    minimum: float = 0.0
    maximum: float = 0.0
    mean: float = 0.0
    dominant_frequency: float = 0.0
    dominant_amplitude: float = 0.0


@dataclass
class TargetToneAnalysis:
    """Sine component synchronously extracted at one requested frequency."""

    frequency: float
    amplitude: float
    rms: float
    phase_degrees: float
    time_seconds: np.ndarray
    waveform: np.ndarray
    absolute_start_time_seconds: float = 0.0


@dataclass
class LaserAnalysis:
    frequencies: np.ndarray
    amplitudes: np.ndarray
    metrics: Metrics
    frequency_resolution: float
    target_tone: Optional[TargetToneAnalysis] = None
    target_status: str = ""
    # A fitted component may be displayed without being accepted as rotor 1X.
    target_preview: bool = False
    target_reference_hz: float = 0.0
    target_snr: float = 0.0


@dataclass(frozen=True)
class HardwarePulseMeasurement:
    """Period and edge timestamp captured by a T7 DIO-EF input."""

    frequency_hz: float = 0.0
    period_seconds: float = 0.0
    edge_timestamp_ticks: int = 0
    timestamp_clock_hz: float = 40_000_000.0


@dataclass
class AcquisitionChunk:
    """Stream chunk plus DIO-EF snapshots; these are not sample-aligned."""

    balancing: Optional[BalancingChunk] = None
    laser: Optional[LaserChunk] = None
    piezo: Optional[PiezoChunk] = None
    hardware_frequencies: Dict[str, float] = field(default_factory=dict)
    # Uncorrected DIO-EF readback retained for wiring/trigger diagnostics.
    hardware_raw_frequencies: Dict[str, float] = field(default_factory=dict)
    hardware_pulses: Dict[str, HardwarePulseMeasurement] = field(default_factory=dict)
