"""Per-side vibration sensor configuration and engineering-unit conversion."""

from dataclasses import asdict, dataclass
from typing import Dict, Mapping

import numpy as np


SENSOR_TYPES = ("piezo", "accelerometer", "disabled")
DISPLAY_UNITS = ("mV", "g", "m/s²")
STANDARD_GRAVITY = 9.80665


@dataclass(frozen=True)
class SensorProfile:
    """Configuration for one analog vibration input.

    Raw acquisition and exports always remain volts.  Sensitivity is used only
    to derive engineering units from a voltage-output accelerometer; leaving it
    at zero deliberately keeps the display in mV instead of implying a physical
    calibration that has not been entered.
    """

    sensor_type: str = "piezo"
    model: str = "压电陶瓷 + 现有放大板"
    sensitivity_mv_per_g: float = 0.0
    display_unit: str = "mV"
    polarity: int = 1

    @classmethod
    def from_dict(cls, values: Mapping[str, object]) -> "SensorProfile":
        source = dict(values or {})
        return cls(**{
            key: source[key]
            for key in cls.__dataclass_fields__
            if key in source
        })

    @property
    def enabled(self) -> bool:
        return self.sensor_type != "disabled"

    @property
    def type_label(self) -> str:
        return {
            "piezo": "压电陶瓷",
            "accelerometer": "加速度传感器",
            "disabled": "未安装",
        }[self.sensor_type]

    @property
    def effective_unit(self) -> str:
        if self.sensor_type != "accelerometer":
            return "mV"
        if self.display_unit in ("g", "m/s²") and self.sensitivity_mv_per_g > 0:
            return self.display_unit
        return "mV"

    @property
    def algorithm_label(self) -> str:
        if self.sensor_type == "accelerometer":
            return "加速度 1X 阶次跟踪"
        if self.sensor_type == "piezo":
            return "压电电压 1X 阶次跟踪"
        return "不参与分析"

    def validate(self) -> None:
        if self.sensor_type not in SENSOR_TYPES:
            raise ValueError("传感器类型无效")
        if self.display_unit not in DISPLAY_UNITS:
            raise ValueError("传感器显示单位无效")
        if self.polarity not in (-1, 1):
            raise ValueError("传感器极性只能是 +1 或 -1")
        if not np.isfinite(self.sensitivity_mv_per_g) or self.sensitivity_mv_per_g < 0:
            raise ValueError("加速度传感器灵敏度必须是非负有限数")
        if self.sensor_type == "accelerometer" and self.sensitivity_mv_per_g <= 0:
            raise ValueError("NI-9234 的 IEPE 加速度传感器必须填写灵敏度 mV/g")

    def analysis_samples(self, voltage_v: np.ndarray) -> np.ndarray:
        """Return polarity-normalized volts for phase/order analysis."""
        return np.asarray(voltage_v, dtype=float) * float(self.polarity)

    def convert_voltage(self, voltage_v: np.ndarray) -> np.ndarray:
        """Convert voltage samples or amplitudes to the configured display unit."""
        millivolts = np.asarray(voltage_v, dtype=float) * 1000.0 * float(self.polarity)
        if self.effective_unit == "mV":
            return millivolts
        acceleration_g = millivolts / self.sensitivity_mv_per_g
        if self.effective_unit == "g":
            return acceleration_g
        return acceleration_g * STANDARD_GRAVITY

    def convert_amplitude(self, amplitude_v: float) -> float:
        converted = self.convert_voltage(np.asarray([abs(float(amplitude_v))]))
        return abs(float(converted[0]))

    def to_dict(self) -> dict:
        return asdict(self)


def sensor_profiles_from_config(values: Mapping[str, object] = None) -> Dict[str, SensorProfile]:
    defaults = {
        "left": SensorProfile(),
        "right": SensorProfile(),
    }
    source = dict(values or {})
    result = {}
    for side, default in defaults.items():
        raw_profile = source.get(side, asdict(default))
        profile = (
            raw_profile
            if isinstance(raw_profile, SensorProfile)
            else SensorProfile.from_dict(raw_profile)
        )
        profile.validate()
        result[side] = profile
    if not any(profile.enabled for profile in result.values()):
        raise ValueError("左右至少要启用一个振动传感器")
    return result


def sensor_profiles_to_dict(profiles: Mapping[str, SensorProfile]) -> dict:
    return {side: profiles[side].to_dict() for side in ("left", "right")}
