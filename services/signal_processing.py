"""Stable real-time measurements derived from streamed signal chunks."""

from collections import deque
from dataclasses import dataclass
import time
from typing import Deque, Optional, Tuple

import numpy as np

from models.data import HardwarePulseMeasurement


class PulseFrequencyEstimator:
    """Estimate pulse frequency across chunk boundaries using a rolling window."""

    def __init__(self, window_seconds: float = 1.0, smoothing: float = 0.18) -> None:
        self.window_seconds = window_seconds
        self.smoothing = smoothing
        self._samples: Deque[np.ndarray] = deque()
        self._sample_count = 0
        self._frequency = 0.0

    def reset(self) -> None:
        self._samples.clear()
        self._sample_count = 0
        self._frequency = 0.0

    def update(self, values: np.ndarray, scan_rate: float) -> float:
        digital = np.asarray(values, dtype=float) >= 0.5
        if digital.size == 0 or scan_rate <= 0:
            return self._frequency
        self._samples.append(digital)
        self._sample_count += digital.size
        maximum_samples = max(1, int(scan_rate * self.window_seconds))
        while self._samples and self._sample_count - self._samples[0].size >= maximum_samples:
            self._sample_count -= self._samples.popleft().size
        combined = np.concatenate(tuple(self._samples))[-maximum_samples:]
        rising_edges = np.flatnonzero(combined[1:] & ~combined[:-1]) + 1
        if len(rising_edges) < 3:
            if combined.size >= maximum_samples and len(rising_edges) == 0:
                self._frequency = 0.0
            return self._frequency
        periods = np.diff(rising_edges).astype(float)
        median_period = float(np.median(periods))
        if median_period <= 0:
            return self._frequency
        deviations = np.abs(periods - median_period)
        valid = periods[deviations <= max(2.0, median_period * 0.20)]
        if valid.size:
            measured = float(scan_rate / np.median(valid))
            if self._frequency <= 0:
                self._frequency = measured
            else:
                self._frequency += self.smoothing * (measured - self._frequency)
        return self._frequency


@dataclass(frozen=True)
class PulsePhaseEstimate:
    """Result of one target-pulse versus speed-reference phase estimate."""

    angle_degrees: Optional[float]
    valid: bool
    quality: str
    sample_count: int
    concentration: float


class PulsePhaseEstimator:
    """Estimate target pulse phase against a one-pulse-per-revolution reference.

    Reference and target samples are buffered together so an edge split across
    two LabJack stream reads is still measured with the correct sample offset.
    Each complete reference revolution must contain exactly one target edge.
    The displayed result is a circular mean, so values around 359/0 degrees do
    not average incorrectly to 180 degrees.
    """

    def __init__(
        self,
        window_seconds: float = 1.0,
        direction: int = 1,
        offset_degrees: float = 0.0,
        reference_edge: str = "rising",
        target_edge: str = "rising",
        minimum_cycles: int = 3,
        minimum_concentration: float = 0.75,
    ) -> None:
        self.window_seconds = max(0.1, float(window_seconds))
        self.direction = 1 if int(direction) >= 0 else -1
        self.offset_degrees = float(offset_degrees)
        self.reference_edge = self._normalize_edge(reference_edge)
        self.target_edge = self._normalize_edge(target_edge)
        self.minimum_cycles = max(2, int(minimum_cycles))
        self.minimum_concentration = float(minimum_concentration)
        self._samples: Deque[Tuple[np.ndarray, np.ndarray]] = deque()
        self._sample_count = 0

    def reset(self) -> None:
        self._samples.clear()
        self._sample_count = 0

    def update(
        self,
        reference_values: np.ndarray,
        target_values: np.ndarray,
        scan_rate: float,
    ) -> PulsePhaseEstimate:
        reference = np.asarray(reference_values, dtype=float) >= 0.5
        target = np.asarray(target_values, dtype=float) >= 0.5
        sample_count = min(reference.size, target.size)
        if sample_count == 0 or scan_rate <= 0:
            return self._invalid("等待同步脉冲")

        reference = reference[:sample_count]
        target = target[:sample_count]
        self._samples.append((reference, target))
        self._sample_count += sample_count
        maximum_samples = max(1, int(scan_rate * self.window_seconds))
        while (
            self._samples
            and self._sample_count - self._samples[0][0].size >= maximum_samples
        ):
            first_reference, _ = self._samples.popleft()
            self._sample_count -= first_reference.size

        combined_reference = np.concatenate(
            tuple(reference_chunk for reference_chunk, _ in self._samples)
        )[-maximum_samples:]
        combined_target = np.concatenate(
            tuple(target_chunk for _, target_chunk in self._samples)
        )[-maximum_samples:]
        reference_edges = self._edges(combined_reference, self.reference_edge)
        target_edges = self._edges(combined_target, self.target_edge)
        if reference_edges.size < self.minimum_cycles + 1:
            return self._invalid("速度参考脉冲不足")
        if target_edges.size == 0:
            return self._invalid("目标脉冲缺失")

        periods = np.diff(reference_edges).astype(float)
        median_period = float(np.median(periods))
        if median_period <= 0:
            return self._invalid("速度参考周期无效")

        phases = []
        phase_interval_indexes = []
        for interval_index, period in enumerate(periods):
            if abs(period - median_period) > max(2.0, median_period * 0.20):
                continue
            start = reference_edges[interval_index]
            end = reference_edges[interval_index + 1]
            candidates = target_edges[(target_edges >= start) & (target_edges < end)]
            if candidates.size != 1:
                continue
            raw_angle = (float(candidates[0] - start) / float(period)) * 360.0
            phases.append(raw_angle)
            phase_interval_indexes.append(interval_index)

        if len(phases) < self.minimum_cycles:
            return self._invalid("有效相位周期不足", len(phases))
        if phase_interval_indexes[-1] < len(periods) - 2:
            return self._invalid("目标脉冲已中断", len(phases))

        recent_phases = np.asarray(phases[-24:], dtype=float)
        vectors = np.exp(1j * np.deg2rad(recent_phases))
        mean_vector = np.mean(vectors)
        concentration = float(abs(mean_vector))
        if concentration < self.minimum_concentration:
            return self._invalid(
                "相位波动/脉冲不同步", len(recent_phases), concentration
            )
        raw_mean = float(np.rad2deg(np.angle(mean_vector)) % 360.0)
        angle = (
            self.direction * raw_mean + self.offset_degrees
        ) % 360.0
        return PulsePhaseEstimate(
            angle_degrees=angle,
            valid=True,
            quality="稳定",
            sample_count=len(recent_phases),
            concentration=concentration,
        )

    @staticmethod
    def _normalize_edge(edge: str) -> str:
        normalized = str(edge).strip().lower()
        if normalized not in ("rising", "falling"):
            raise ValueError("Pulse edge must be 'rising' or 'falling'")
        return normalized

    @staticmethod
    def _edges(values: np.ndarray, edge: str) -> np.ndarray:
        if edge == "falling":
            return np.flatnonzero(~values[1:] & values[:-1]) + 1
        return np.flatnonzero(values[1:] & ~values[:-1]) + 1

    @staticmethod
    def _invalid(
        quality: str, sample_count: int = 0, concentration: float = 0.0
    ) -> PulsePhaseEstimate:
        return PulsePhaseEstimate(
            angle_degrees=None,
            valid=False,
            quality=quality,
            sample_count=sample_count,
            concentration=concentration,
        )


class HardwarePulsePhaseEstimator:
    """Calculate phase from T7 Interrupt Frequency In edge timestamps.

    DIO-EF READ_B contains the 32-bit Core_Timer count captured at the most
    recent selected edge.  Using that timestamp avoids missing narrow pulses
    between ordinary digital stream scans.
    """

    TIMER_MODULUS = 2**32

    def __init__(
        self,
        direction: int = 1,
        offset_degrees: float = 0.0,
        window_size: int = 12,
        hold_seconds: float = 0.6,
        stale_seconds: float = 0.25,
        minimum_samples: int = 3,
        minimum_concentration: float = 0.75,
    ) -> None:
        self.direction = 1 if int(direction) >= 0 else -1
        self.offset_degrees = float(offset_degrees)
        self.window_size = max(3, int(window_size))
        self.hold_seconds = max(0.0, float(hold_seconds))
        self.stale_seconds = max(0.05, float(stale_seconds))
        self.minimum_samples = max(2, int(minimum_samples))
        self.minimum_concentration = float(minimum_concentration)
        self._angles: Deque[float] = deque(maxlen=self.window_size)
        self._last_reference_ticks = 0
        self._last_target_ticks = 0
        self._reference_changed_at: Optional[float] = None
        self._target_changed_at: Optional[float] = None
        self._last_angle: Optional[float] = None
        self._last_valid_at: Optional[float] = None

    def reset(self) -> None:
        self._angles.clear()
        self._last_reference_ticks = 0
        self._last_target_ticks = 0
        self._reference_changed_at = None
        self._target_changed_at = None
        self._last_angle = None
        self._last_valid_at = None

    def update(
        self,
        reference: Optional[HardwarePulseMeasurement],
        target: Optional[HardwarePulseMeasurement],
        monotonic_seconds: Optional[float] = None,
    ) -> PulsePhaseEstimate:
        now = time.monotonic() if monotonic_seconds is None else monotonic_seconds
        if reference is None or reference.frequency_hz <= 0:
            return self._held_or_invalid("速度硬件脉冲缺失", now)
        if target is None or target.frequency_hz <= 0:
            return self._held_or_invalid("目标硬件脉冲缺失", now)
        if reference.edge_timestamp_ticks <= 0 or target.edge_timestamp_ticks <= 0:
            return self._held_or_invalid("等待硬件边沿时间戳", now)

        self._reference_changed_at = self._observe_timestamp(
            reference.edge_timestamp_ticks,
            self._last_reference_ticks,
            self._reference_changed_at,
            now,
        )
        reference_changed = reference.edge_timestamp_ticks != self._last_reference_ticks
        self._last_reference_ticks = reference.edge_timestamp_ticks
        self._target_changed_at = self._observe_timestamp(
            target.edge_timestamp_ticks,
            self._last_target_ticks,
            self._target_changed_at,
            now,
        )
        target_changed = target.edge_timestamp_ticks != self._last_target_ticks
        self._last_target_ticks = target.edge_timestamp_ticks

        reference_stale_limit = max(
            self.stale_seconds, 4.0 / reference.frequency_hz
        )
        target_stale_limit = max(self.stale_seconds, 4.0 / target.frequency_hz)
        if (
            self._reference_changed_at is None
            or now - self._reference_changed_at > reference_stale_limit
        ):
            return self._held_or_invalid("速度硬件脉冲已中断", now)
        if (
            self._target_changed_at is None
            or now - self._target_changed_at > target_stale_limit
        ):
            return self._held_or_invalid("目标硬件脉冲已中断", now)

        frequency_ratio = target.frequency_hz / reference.frequency_hz
        if not 0.75 <= frequency_ratio <= 1.25:
            return self._held_or_invalid("脉冲频率不同步", now)

        if target_changed or not self._angles:
            period_ticks = (
                reference.period_seconds * reference.timestamp_clock_hz
            )
            if period_ticks <= 0:
                period_ticks = reference.timestamp_clock_hz / reference.frequency_hz
            if period_ticks <= 0:
                return self._held_or_invalid("速度周期无效", now)
            signed_delta = self._signed_tick_delta(
                target.edge_timestamp_ticks,
                reference.edge_timestamp_ticks,
            )
            raw_angle = (signed_delta % period_ticks) / period_ticks * 360.0
            self._angles.append(raw_angle)

        if len(self._angles) < self.minimum_samples:
            return self._held_or_invalid("正在建立硬件相位", now)
        vectors = np.exp(1j * np.deg2rad(np.asarray(self._angles, dtype=float)))
        mean_vector = np.mean(vectors)
        concentration = float(abs(mean_vector))
        if concentration < self.minimum_concentration:
            return self._held_or_invalid("硬件相位波动", now, concentration)
        raw_mean = float(np.rad2deg(np.angle(mean_vector)) % 360.0)
        angle = (self.direction * raw_mean + self.offset_degrees) % 360.0
        self._last_angle = angle
        self._last_valid_at = now
        return PulsePhaseEstimate(
            angle_degrees=angle,
            valid=True,
            quality="硬件稳定",
            sample_count=len(self._angles),
            concentration=concentration,
        )

    @staticmethod
    def _observe_timestamp(
        ticks: int,
        previous_ticks: int,
        previous_change_time: Optional[float],
        now: float,
    ) -> Optional[float]:
        if ticks != previous_ticks:
            return now
        return previous_change_time

    @classmethod
    def _signed_tick_delta(cls, target_ticks: int, reference_ticks: int) -> int:
        delta = (int(target_ticks) - int(reference_ticks)) % cls.TIMER_MODULUS
        if delta >= cls.TIMER_MODULUS // 2:
            delta -= cls.TIMER_MODULUS
        return delta

    def _held_or_invalid(
        self, quality: str, now: float, concentration: float = 0.0
    ) -> PulsePhaseEstimate:
        if (
            self._last_angle is not None
            and self._last_valid_at is not None
            and now - self._last_valid_at <= self.hold_seconds
        ):
            return PulsePhaseEstimate(
                angle_degrees=self._last_angle,
                valid=True,
                quality="硬件稳定",
                sample_count=len(self._angles),
                concentration=concentration,
            )
        return PulsePhaseEstimate(
            angle_degrees=None,
            valid=False,
            quality=quality,
            sample_count=len(self._angles),
            concentration=concentration,
        )


def ac_rms(values: np.ndarray) -> float:
    """RMS of the AC component, suitable for a force/vibration signal."""
    samples = np.asarray(values, dtype=float)
    samples = samples[np.isfinite(samples) & (samples != -9999.0)]
    if samples.size == 0:
        return 0.0
    centered = samples - np.mean(samples)
    return float(np.sqrt(np.mean(np.square(centered))))
