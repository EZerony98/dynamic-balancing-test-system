"""Voltage-domain dual-piezo measurements; no force or unbalance calibration implied."""

from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np

from models.data import Metrics, TargetToneAnalysis


SIDES = ("left", "right")


@dataclass(frozen=True)
class PiezoSettings:
    analysis_mode: str = "search"
    ppr: int = 1
    search_min_hz: float = 120.0
    search_max_hz: float = 200.0
    window_seconds: float = 2.0
    rpm_variation_limit: float = 0.02
    max_bad_reference_updates: int = 3
    reference_jump_limit: float = 0.05
    reference_change_confirm_updates: int = 5
    max_reference_held_fraction: float = 0.10
    reference_tolerance: float = 0.15
    minimum_snr: float = 5.0
    interchannel_delay_us: float = 8.0
    reference_channel_delay_us: float = 16.0
    synchronous_frequency_tolerance: float = 0.05
    synchronous_period_variation_limit: float = 0.05
    saturation_spike_fraction_limit: float = 0.002
    saturation_run_limit_samples: int = 2
    angle_direction: int = 1
    left_angle_offset_degrees: float = 0.0
    right_angle_offset_degrees: float = 0.0

    @classmethod
    def from_dict(cls, values: dict) -> "PiezoSettings":
        # Old settings files may still contain laser-only keys. They do not
        # become physical calibration parameters for the piezo channels.
        values = dict(values)
        if "hall_ppr" in values and "ppr" not in values:
            values["ppr"] = values["hall_ppr"]
        return cls(**{key: values[key] for key in cls.__dataclass_fields__ if key in values})

    def validate(self) -> None:
        numbers = (self.search_min_hz, self.search_max_hz, self.window_seconds,
                   self.rpm_variation_limit, self.reference_jump_limit,
                   self.max_reference_held_fraction, self.reference_tolerance,
                   self.minimum_snr, self.interchannel_delay_us,
                   self.reference_channel_delay_us,
                   self.synchronous_frequency_tolerance,
                   self.synchronous_period_variation_limit,
                   self.saturation_spike_fraction_limit,
                   self.left_angle_offset_degrees,
                   self.right_angle_offset_degrees)
        if not all(np.isfinite(value) for value in numbers):
            raise ValueError("压电分析参数必须是有限数字")
        if self.analysis_mode not in ("search", "manual") or not 1 <= self.ppr <= 100:
            raise ValueError("分析方式或每转脉冲数无效")
        if not 1 <= self.max_bad_reference_updates <= 50:
            raise ValueError("光电连续异常容忍次数无效")
        if not 2 <= self.reference_change_confirm_updates <= 50:
            raise ValueError("新转速连续确认次数无效")
        if not 0 < self.search_min_hz < self.search_max_hz:
            raise ValueError("1X 搜索频率范围无效")
        if not 0.5 <= self.window_seconds <= 10 or not 0 < self.rpm_variation_limit < 1:
            raise ValueError("分析窗或转速波动上限无效")
        if not 0 < self.reference_jump_limit < 1 or not 0 <= self.max_reference_held_fraction < 1:
            raise ValueError("光电跳变或异常占比门限无效")
        if not 0 < self.reference_tolerance < 1 or self.minimum_snr < 1:
            raise ValueError("参考频率容差或信噪比无效")
        if not 0 <= self.interchannel_delay_us <= 1000:
            raise ValueError("通道采样时间差无效")
        if not 0 <= self.reference_channel_delay_us <= 1000:
            raise ValueError("光电同步通道采样延迟无效")
        if not 0 < self.synchronous_frequency_tolerance < 1:
            raise ValueError("同步光电转频容差无效")
        if not 0 < self.synchronous_period_variation_limit < 1:
            raise ValueError("同步光电周期波动上限无效")
        if not 0 <= self.saturation_spike_fraction_limit < 0.1:
            raise ValueError("饱和尖峰容忍占比无效")
        if not 0 <= self.saturation_run_limit_samples <= 100:
            raise ValueError("连续饱和样本上限无效")
        if self.angle_direction not in (-1, 1):
            raise ValueError("转子角度方向只能为 +1 或 -1")
        if not (-360 <= self.left_angle_offset_degrees <= 360 and
                -360 <= self.right_angle_offset_degrees <= 360):
            raise ValueError("左右同步角度偏置必须在 ±360° 内")


@dataclass
class PiezoSideResult:
    valid: bool = False
    preview: bool = False
    reason: str = "等待分析"
    amplitude_v: float = 0.0
    snr: float = 0.0
    tone: Optional[TargetToneAnalysis] = None
    shaft_phase_degrees: Optional[float] = None


@dataclass
class Piezo1XResult:
    frequency_hz: float = 0.0
    reference_hz: float = 0.0
    rpm: float = 0.0
    rpm_variation: float = 0.0
    reference_min_hz: float = 0.0
    reference_max_hz: float = 0.0
    reference_held_fraction: float = 0.0
    synchronous_reference_hz: float = 0.0
    synchronous_period_variation: float = 0.0
    reference_edge_count: int = 0
    repaired_saturation_samples: int = 0
    shaft_angle_synchronous: bool = False
    preview_only: bool = False
    left: PiezoSideResult = field(default_factory=PiezoSideResult)
    right: PiezoSideResult = field(default_factory=PiezoSideResult)
    relative_phase_degrees: Optional[float] = None
    status: str = "等待连续分析窗"


@dataclass
class PiezoSpectrumAnalysis:
    frequencies: np.ndarray
    amplitudes: Dict[str, np.ndarray]
    metrics: Dict[str, Metrics]
    frequency_resolution: float
    one_x: Optional[Piezo1XResult] = None
    manual_tones: Dict[str, TargetToneAnalysis] = field(default_factory=dict)
