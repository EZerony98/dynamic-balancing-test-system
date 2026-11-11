"""Explicit raw-voltage and engineering-unit columns for sensor exports."""

from typing import Dict, List, Mapping, Tuple

from models.sensors import STANDARD_GRAVITY, SensorProfile, sensor_profiles_from_config


def export_profiles(values: Mapping[str, object] = None) -> Dict[str, SensorProfile]:
    """Return validated profiles, defaulting legacy recordings to two piezos."""
    return sensor_profiles_from_config(values or {})


def waveform_engineering_columns(
    side: str, profile: SensorProfile, voltage_v: float
) -> List[Tuple[str, object]]:
    """Return non-destructive engineering-unit columns for one waveform sample."""
    voltage = float(voltage_v)
    if profile.sensor_type == "accelerometer":
        acceleration_g = (
            voltage * 1000.0 / profile.sensitivity_mv_per_g * profile.polarity
        )
        return [
            ("%s_acceleration_g" % side, acceleration_g),
            ("%s_acceleration_m_s2" % side, acceleration_g * STANDARD_GRAVITY),
        ]
    if profile.sensor_type == "piezo":
        return [("%s_piezo_voltage_mv" % side,
                 voltage * 1000.0 * profile.polarity)]
    return []


def amplitude_engineering_columns(
    side: str, profile: SensorProfile, amplitude_v: float
) -> List[Tuple[str, object]]:
    """Return engineering-unit columns for a positive spectrum/1X amplitude."""
    amplitude = abs(float(amplitude_v))
    if profile.sensor_type == "accelerometer":
        acceleration_g = amplitude * 1000.0 / profile.sensitivity_mv_per_g
        return [
            ("%s_amplitude_g" % side, acceleration_g),
            ("%s_amplitude_m_s2" % side, acceleration_g * STANDARD_GRAVITY),
        ]
    if profile.sensor_type == "piezo":
        return [("%s_amplitude_mv" % side, amplitude * 1000.0)]
    return []


def engineering_units(profiles: Mapping[str, SensorProfile]) -> dict:
    result = {}
    for side, profile in profiles.items():
        if profile.sensor_type == "accelerometer":
            result[side] = ["V", "g", "m/s^2"]
        elif profile.sensor_type == "piezo":
            result[side] = ["V", "mV"]
        else:
            result[side] = ["V"]
    return result
