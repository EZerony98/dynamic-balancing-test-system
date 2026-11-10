"""Selectable LabJack-only or NI-9234 + LabJack vibration acquisition."""

from typing import Any, Dict, Mapping

import numpy as np

from drivers.base_acquisition import AcquisitionSource
from drivers.labjack_t7 import LabJackT7Source
from models.data import AcquisitionChunk, PiezoChunk
from models.sensors import sensor_profiles_from_config


def _resample(values: np.ndarray, count: int) -> np.ndarray:
    """Resample a diagnostic/asynchronous LabJack stream to an NI chunk size."""
    samples = np.asarray(values, dtype=float)
    if count <= 0:
        return np.array([], dtype=float)
    if len(samples) == count:
        return samples.copy()
    valid = np.isfinite(samples) & (samples != -9999.0)
    if np.count_nonzero(valid) < 2:
        return np.full(count, np.nan, dtype=float)
    positions = np.arange(len(samples), dtype=float)
    repaired = np.interp(positions, positions[valid], samples[valid])
    old_axis = np.linspace(0.0, 1.0, len(repaired), endpoint=True)
    new_axis = np.linspace(0.0, 1.0, count, endpoint=True)
    return np.interp(new_axis, old_axis, repaired)


class VibrationAcquisitionSource(AcquisitionSource):
    """Use NI only for selected IEPE sides; LabJack always supplies FIO3.

    NI-DAQmx returns acceleration in g.  The existing application keeps its
    raw analysis contract in volts, so NI values are converted to the
    sensitivity-equivalent sensor voltage before entering the common 1X
    analyzer.  The UI converts that voltage back to g or m/s² exactly once.
    """

    def __init__(
        self, config: Dict[str, Any], sensor_profiles: Mapping[str, object]
    ) -> None:
        self.config = dict(config)
        self.config["channels"] = dict(config.get("channels", {}))
        self.sensor_profiles = sensor_profiles_from_config(sensor_profiles)
        self.labjack = LabJackT7Source(self.config)
        self.scan_rate = float(self.config["piezo_scan_rate"])
        self.scans_per_read = int(self.config["piezo_scans_per_read"])
        self.mode = "piezo"
        self.task = None
        self._ni_sides = []
        self._sample_offset = 0

    def set_sensor_profiles(self, profiles: Mapping[str, object]) -> None:
        if self.task is not None or self.labjack.streaming:
            raise RuntimeError("切换传感器采集设备前请先停止测量")
        self.sensor_profiles = sensor_profiles_from_config(profiles)

    def _accelerometer_sides(self) -> list:
        return [
            side for side, profile in self.sensor_profiles.items()
            if profile.enabled and profile.sensor_type == "accelerometer"
        ]

    def _open_ni(self) -> None:
        try:
            import nidaqmx
            from nidaqmx.constants import (
                AccelSensitivityUnits,
                AccelUnits,
                AcquisitionType,
                Coupling,
                ExcitationSource,
            )
        except Exception as exc:  # pragma: no cover - Windows target dependency
            raise RuntimeError(
                "已选择 IEPE 加速度传感器，但未安装 NI-DAQmx/nidaqmx"
            ) from exc
        ni = dict(self.config.get("ni9234", {}))
        module = str(ni.get("module", "cDAQ1Mod1")).strip()
        current_a = float(ni.get("iepe_current_ma", 2.0)) / 1000.0
        if not module:
            raise RuntimeError("NI-9234 模块名称不能为空，例如 cDAQ1Mod1")
        if not 1_652 <= self.scan_rate <= 51_200:
            raise RuntimeError("NI-9234 扫描率必须在 1,652 到 51,200 S/s/ch 之间")
        task = nidaqmx.Task()
        try:
            for side in self._ni_sides:
                profile = self.sensor_profiles[side]
                channel_name = str(ni.get(
                    "%s_channel" % side,
                    "ai0" if side == "left" else "ai1",
                )).strip()
                physical = (
                    channel_name if "/" in channel_name
                    else "%s/%s" % (module, channel_name)
                )
                channel = task.ai_channels.add_ai_accel_chan(
                    physical,
                    name_to_assign_to_channel=side,
                    min_val=-500.0,
                    max_val=500.0,
                    units=AccelUnits.G,
                    sensitivity=float(profile.sensitivity_mv_per_g),
                    sensitivity_units=AccelSensitivityUnits.MILLIVOLTS_PER_G,
                    current_excit_source=ExcitationSource.INTERNAL,
                    current_excit_val=current_a,
                )
                channel.ai_coupling = Coupling.AC
            task.timing.cfg_samp_clk_timing(
                rate=self.scan_rate,
                sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=self.scans_per_read * 4,
            )
            self.scan_rate = float(task.timing.samp_clk_rate)
            task.start()
        except Exception:
            task.close()
            raise
        self.task = task

    @staticmethod
    def _ni_matrix(raw: object, channel_count: int) -> np.ndarray:
        matrix = np.asarray(raw, dtype=float)
        if channel_count == 1:
            matrix = matrix.reshape(1, -1)
        elif matrix.ndim != 2 or matrix.shape[0] != channel_count:
            raise RuntimeError("NI-DAQmx 返回的数据通道数与配置不一致")
        return matrix

    def open(self, mode: str = "piezo") -> dict:
        self.mode = str(mode)
        self._sample_offset = 0
        self._ni_sides = self._accelerometer_sides() if self.mode == "piezo" else []
        try:
            if self._ni_sides:
                self._open_ni()
                # NI-9234 may coerce the requested rate. Ask LabJack for the
                # same nominal rate to keep chunk durations close.
                self.labjack.config["piezo_scan_rate"] = self.scan_rate
            info = self.labjack.open(mode=self.mode)
        except Exception:
            self.close()
            raise
        self.scan_rate = (
            float(self.scan_rate) if self._ni_sides else float(self.labjack.scan_rate)
        )
        self.scans_per_read = int(self.config[
            "piezo_scans_per_read" if self.mode == "piezo"
            else "balancing_scans_per_read"
        ])
        if not self._ni_sides:
            return info
        ni = dict(self.config.get("ni9234", {}))
        return {
            "name": "NI-9234 + LabJack T7",
            "serial": "%s / T7 %s" % (ni.get("module", "cDAQ1Mod1"), info["serial"]),
            "connection": "NI IEPE + LabJack %s" % info["connection"],
            "mode": self.mode,
            "scan_rate": self.scan_rate,
        }

    def read(self) -> AcquisitionChunk:
        labjack_chunk = self.labjack.read()
        if not self._ni_sides or self.mode != "piezo":
            return labjack_chunk
        if self.task is None or labjack_chunk.piezo is None:
            raise RuntimeError("NI-9234/LabJack 混合采集尚未启动")
        timeout = max(10.0, self.scans_per_read / self.scan_rate * 4.0)
        raw = self.task.read(
            number_of_samples_per_channel=self.scans_per_read,
            timeout=timeout,
        )
        matrix = self._ni_matrix(raw, len(self._ni_sides))
        count = int(matrix.shape[1])
        channels = {
            side: _resample(labjack_chunk.piezo.channels[side], count)
            for side in ("left", "right")
        }
        for index, side in enumerate(self._ni_sides):
            sensitivity = self.sensor_profiles[side].sensitivity_mv_per_g
            channels[side] = matrix[index] * sensitivity / 1000.0
        reference = _resample(labjack_chunk.piezo.reference_state, count)
        active_sides = [
            side for side, profile in self.sensor_profiles.items() if profile.enabled
        ]
        active_devices = {
            "ni" if side in self._ni_sides else "labjack" for side in active_sides
        }
        chunk = PiezoChunk(
            channels=channels,
            reference_state=reference,
            scan_rate=self.scan_rate,
            device_backlog=labjack_chunk.piezo.device_backlog,
            ljm_backlog=labjack_chunk.piezo.ljm_backlog,
            dropped_samples=labjack_chunk.piezo.dropped_samples,
            start_sample=self._sample_offset,
            channels_synchronized=len(active_devices) <= 1,
            reference_synchronized=False,
            acquisition_backend="ni9234+labjack",
        )
        self._sample_offset += count
        return AcquisitionChunk(
            piezo=chunk,
            hardware_frequencies=labjack_chunk.hardware_frequencies,
            hardware_raw_frequencies=labjack_chunk.hardware_raw_frequencies,
            hardware_pulses=labjack_chunk.hardware_pulses,
        )

    def close(self) -> None:
        self.labjack.close()
        if self.task is not None:
            try:
                self.task.stop()
            except Exception:
                pass
            try:
                self.task.close()
            except Exception:
                pass
        self.task = None
        self._ni_sides = []

