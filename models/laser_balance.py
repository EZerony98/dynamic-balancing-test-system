"""Laser balance measurements. Voltages are the canonical stored amplitude."""

from dataclasses import asdict, dataclass, field
import json
from typing import Optional

from models.data import TargetToneAnalysis


UNITS = {"displacement": "μm", "velocity": "mm/s", "acceleration": "m/s²"}
PEAK_SELECTION = "nearest_reference_v1"


@dataclass(frozen=True)
class BalanceSettings:
    analysis_mode: str = "search"
    quantity: str = "acceleration"
    units_per_volt: float = 1.0
    scale_confirmed: bool = False
    reference: str = "hall"
    hall_ppr: int = 1  # Legacy key: pulses per turn of the FIO3 source.
    rotor_motor_ratio: float = 1.0
    manual_rpm: float = 9000.0
    search_min_hz: float = 100.0
    search_max_hz: float = 200.0
    window_seconds: float = 2.0
    reference_tolerance: float = 0.15
    rpm_variation_limit: float = 0.005
    amplitude_cv_limit: float = 0.05
    calibration_speed_tolerance: float = 0.01
    minimum_snr: float = 5.0
    rotor_id: str = ""

    @classmethod
    def from_dict(cls, values: dict) -> "BalanceSettings":
        return cls(**{key: value for key, value in values.items() if key in cls.__dataclass_fields__})

    def validate(self) -> None:
        import math
        numeric = (self.units_per_volt, self.rotor_motor_ratio, self.manual_rpm,
                   self.search_min_hz, self.search_max_hz, self.window_seconds,
                   self.reference_tolerance, self.rpm_variation_limit,
                   self.amplitude_cv_limit, self.calibration_speed_tolerance, self.minimum_snr)
        if not all(math.isfinite(value) and value > 0 for value in numeric):
            raise ValueError("测量参数必须为有限正数")
        if (self.quantity not in UNITS or self.reference not in ("hall", "photoelectric", "manual")
                or self.analysis_mode not in ("search", "manual")):
            raise ValueError("未知激光输出类型或转速来源")
        if not 1 <= self.hall_ppr <= 100 or self.search_min_hz >= self.search_max_hz:
            raise ValueError("PPR 或搜索频率范围无效")
        if not 0.5 <= self.window_seconds <= 10.0:
            raise ValueError("分析窗应为 0.5–10 秒")

    @property
    def signature(self) -> str:
        # A calibration obtained using a different selected peak must not be
        # silently reused after the selection algorithm changes.
        return json.dumps(dict(asdict(self), peak_selection=PEAK_SELECTION),
                          sort_keys=True, ensure_ascii=False)

    @property
    def display_unit(self) -> str:
        return UNITS[self.quantity] if self.scale_confirmed else "mV"

    @property
    def display_factor(self) -> float:
        return self.units_per_volt if self.scale_confirmed else 1000.0

    @property
    def pulse_label(self) -> str:
        return {"hall": "霍尔", "photoelectric": "光电", "manual": "脉冲"}[self.reference]

    @property
    def pulse_speed_label(self) -> str:
        return {"hall": "电机霍尔转速", "photoelectric": "转子光电转速",
                "manual": "FIO3 脉冲转速"}[self.reference]

    def reference_rpm_from_pulse(self, pulse_hz: float) -> float:
        if self.reference == "manual":
            return self.manual_rpm
        rpm = pulse_hz * 60.0 / self.hall_ppr
        # Optical pulses come directly from the measured rotor. A saved motor
        # transmission ratio must never rescale this measurement.
        return rpm * self.rotor_motor_ratio if self.reference == "hall" else rpm


@dataclass
class BalanceMeasurement:
    valid: bool
    reason: str
    timestamp: float
    start_sample: int
    end_sample: int
    scan_rate: float
    settings_signature: str
    frequency_hz: float = 0.0
    reference_rpm: float = 0.0
    rpm_variation: float = 0.0
    amplitude_v: float = 0.0
    noise_v: float = 0.0
    snr: float = 0.0
    tone: Optional[TargetToneAnalysis] = field(default=None, repr=False)

    def record(self) -> dict:
        return {key: value for key, value in self.__dict__.items() if key != "tone"}
