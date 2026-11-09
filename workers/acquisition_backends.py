from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from models.data import AcquisitionConfig, StreamChunk


class AcquisitionError(RuntimeError):
    pass


SENSOR_TYPES = {"piezo", "iepe_accel", "none"}


def validate_acquisition_config(config: AcquisitionConfig) -> tuple[bool, str]:
    if config.scan_rate <= 0 or config.samples_per_read < 2:
        return False, "扫描率和每次读取点数必须大于 0。"
    if config.left_sensor_type not in SENSOR_TYPES or config.right_sensor_type not in SENSOR_TYPES:
        return False, "左右传感器类型设置无效。"
    if config.left_sensor_type == config.right_sensor_type == "none":
        return False, "左右传感器不能同时设为未安装。"
    if not config.labjack_tach_channel.strip():
        return False, "LabJack 光电转速通道不能为空。"

    piezo_count = sum(kind == "piezo" for kind in (config.left_sensor_type, config.right_sensor_type))
    lj_total = config.scan_rate * (piezo_count + 1)  # installed piezo channels + tach
    if lj_total > 100_000:
        return False, f"LabJack 总样本率 {lj_total:,.0f} samples/s 超过 100,000。"
    if config.left_sensor_type == "piezo" and not config.labjack_left_channel.strip():
        return False, "左压电传感器的 LabJack 通道不能为空。"
    if config.right_sensor_type == "piezo" and not config.labjack_right_channel.strip():
        return False, "右压电传感器的 LabJack 通道不能为空。"

    accel_count = sum(kind == "iepe_accel" for kind in (config.left_sensor_type, config.right_sensor_type))
    if accel_count:
        if config.backend != "ni9234":
            return False, "选择 IEPE 加速度传感器后必须启用 NI-9234。"
        if not (1_652 <= config.scan_rate <= 51_200):
            return False, "NI-9234 扫描率必须在 1,652 到 51,200 S/s/ch 之间。"
        if not config.ni_module.strip():
            return False, "NI 模块名称不能为空，例如 cDAQ1Mod1。"
        if config.left_sensor_type == "iepe_accel" and config.ni_left_sensitivity_mv_g <= 0:
            return False, "左加速度传感器灵敏度必须大于 0。"
        if config.right_sensor_type == "iepe_accel" and config.ni_right_sensitivity_mv_g <= 0:
            return False, "右加速度传感器灵敏度必须大于 0。"
        if abs(config.ni_iepe_current_ma - 2.0) > 1e-9:
            return False, "NI-9234 的 IEPE 恒流激励固定使用 2.0 mA。"
        mode = "NI-9234 加速度 + LabJack 转速"
        if piezo_count:
            mode += "/压电"
        return True, f"自动采集：{mode}。"

    if not config.backend.startswith("labjack"):
        return False, "未选择加速度传感器时应只使用 LabJack。"
    return True, "自动采集：仅 LabJack（压电 + 转速）。"


def _physical_channel(module: str, channel: str) -> str:
    value = channel.strip()
    return value if "/" in value else f"{module.strip()}/{value}"


def _resample(values: np.ndarray, count: int) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    valid = np.isfinite(values) & (values != -9999.0)
    if np.count_nonzero(valid) < 2:
        return np.zeros(count, dtype=float)
    if not np.all(valid):
        positions = np.arange(len(values), dtype=float)
        values = np.interp(positions, positions[valid], values[valid])
    if len(values) == count:
        return values
    if len(values) < 2:
        return np.zeros(count, dtype=float)
    old = np.linspace(0.0, 1.0, len(values), endpoint=True)
    new = np.linspace(0.0, 1.0, count, endpoint=True)
    return np.interp(new, old, values)


def _repair_skipped_samples(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float).copy()
    valid = np.isfinite(values) & (values != -9999.0)
    if np.all(valid):
        return values
    if not np.any(valid):
        return np.zeros_like(values)
    positions = np.arange(len(values), dtype=float)
    values[~valid] = np.interp(positions[~valid], positions[valid], values[valid])
    return values


class LabJackStream:
    def __init__(self, config: AcquisitionConfig, channels: list[tuple[str, str]], connection: str):
        self.config = config
        self.channels = channels
        self.connection = connection.upper()
        self.ljm = None
        self.handle = None
        self.actual_scan_rate = float(config.scan_rate)
        self.requested_scan_rate = float(config.scan_rate)
        self.serial = "--"

    def start(self) -> None:
        try:
            from labjack import ljm
        except Exception as exc:  # pragma: no cover - target PC dependency
            raise AcquisitionError("未安装 LabJack LJM Python 包或 LJM 驱动。") from exc
        self.ljm = ljm
        names = [physical for _logical, physical in self.channels]
        try:
            self.handle = ljm.openS("T7", self.connection, "ANY")
            ljm.eWriteName(self.handle, "STREAM_TRIGGER_INDEX", 0)
            ljm.eWriteName(self.handle, "STREAM_CLOCK_SOURCE", 0)
            addresses, _ = ljm.namesToAddresses(len(names), names)
            self.actual_scan_rate = float(ljm.eStreamStart(
                self.handle,
                self.config.samples_per_read,
                len(addresses),
                addresses,
                self.requested_scan_rate,
            ))
            info = ljm.getHandleInfo(self.handle)
            self.serial = str(info[2]) if len(info) > 2 else "--"
        except Exception as exc:
            self.close()
            raise AcquisitionError(f"LabJack T7 连接或启动采集失败：{exc}") from exc

    def read(self) -> tuple[dict[str, np.ndarray], int, int, int]:
        if self.ljm is None or self.handle is None:
            raise AcquisitionError("LabJack 尚未启动。")
        try:
            flat, device_backlog, ljm_backlog = self.ljm.eStreamRead(self.handle)
        except Exception as exc:
            raise AcquisitionError(f"LabJack 读取失败：{exc}") from exc
        values = np.asarray(flat, dtype=float)
        width = len(self.channels)
        usable = len(values) - len(values) % width
        if usable < width:
            raise AcquisitionError("LabJack 本次返回的数据不足一个完整扫描。")
        matrix = values[:usable].reshape(-1, width)
        dropped = int(np.count_nonzero(matrix == -9999.0))
        result = {
            logical: _repair_skipped_samples(matrix[:, index])
            for index, (logical, _physical) in enumerate(self.channels)
        }
        if self.config.tach_edge == "falling" and "TACH" in result:
            tach = result["TACH"].copy()
            valid = tach != -9999.0
            tach[valid] = 1.0 - tach[valid]
            result["TACH"] = tach
        return result, int(device_backlog), int(ljm_backlog), dropped

    def close(self) -> None:
        if self.ljm is not None and self.handle is not None:
            try:
                self.ljm.eStreamStop(self.handle)
            except Exception:
                pass
            try:
                self.ljm.close(self.handle)
            except Exception:
                pass
        self.handle = None


class AcquisitionBackend(ABC):
    def __init__(self, config: AcquisitionConfig):
        self.config = config
        self.actual_scan_rate = float(config.scan_rate)

    @abstractmethod
    def start(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def read(self) -> StreamChunk:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class LabJackBackend(AcquisitionBackend):
    def __init__(self, config: AcquisitionConfig):
        super().__init__(config)
        channels: list[tuple[str, str]] = []
        if config.left_sensor_type == "piezo":
            channels.append(("AIN0", config.labjack_left_channel))
        if config.right_sensor_type == "piezo":
            channels.append(("AIN1", config.labjack_right_channel))
        channels.append(("TACH", config.labjack_tach_channel))
        connection = "USB" if config.backend == "labjack_usb" else "ETHERNET"
        self.stream = LabJackStream(config, channels, connection)

    def start(self) -> dict[str, Any]:
        self.stream.start()
        self.actual_scan_rate = self.stream.actual_scan_rate
        return {
            "model": "LabJack T7",
            "serial": self.stream.serial,
            "connection": self.stream.connection,
            "backend": self.config.backend,
        }

    def read(self) -> StreamChunk:
        data, device_backlog, ljm_backlog, dropped = self.stream.read()
        count = len(data["TACH"])
        zeros = np.zeros(count, dtype=float)
        channels = {"AIN0": zeros.copy(), "AIN1": zeros.copy(), "TACH": data["TACH"]}
        sources = {"AIN0": "未安装", "AIN1": "未安装", "TACH": self.config.labjack_tach_channel}
        units = {"AIN0": "V", "AIN1": "V", "TACH": "logic"}
        if "AIN0" in data:
            channels["AIN0"] = data["AIN0"]
            sources["AIN0"] = self.config.labjack_left_channel
        if "AIN1" in data:
            channels["AIN1"] = data["AIN1"]
            sources["AIN1"] = self.config.labjack_right_channel
        return StreamChunk(
            channels=channels,
            scan_rate=self.actual_scan_rate,
            device_backlog=device_backlog,
            ljm_backlog=ljm_backlog,
            dropped_samples=dropped,
            channel_units=units,
            channel_sources=sources,
            acquisition_backend=self.config.backend,
            reference_ppr=max(1, int(self.config.tach_ppr)),
            reference_scan_rate=self.stream.actual_scan_rate,
            channels_synchronized=True,
        )

    def close(self) -> None:
        self.stream.close()


class NI9234Backend(AcquisitionBackend):
    """Hybrid NI-9234 accelerometer + LabJack piezo/tach backend."""

    def __init__(self, config: AcquisitionConfig):
        super().__init__(config)
        self.task = None
        self.ni_channels: list[tuple[str, str, float]] = []
        lj_channels: list[tuple[str, str]] = []
        if config.left_sensor_type == "iepe_accel":
            self.ni_channels.append(("AIN0", _physical_channel(config.ni_module, config.ni_left_channel), config.ni_left_sensitivity_mv_g))
        elif config.left_sensor_type == "piezo":
            lj_channels.append(("AIN0", config.labjack_left_channel))
        if config.right_sensor_type == "iepe_accel":
            self.ni_channels.append(("AIN1", _physical_channel(config.ni_module, config.ni_right_channel), config.ni_right_sensitivity_mv_g))
        elif config.right_sensor_type == "piezo":
            lj_channels.append(("AIN1", config.labjack_right_channel))
        lj_channels.append(("TACH", config.labjack_tach_channel))
        self.labjack = LabJackStream(config, lj_channels, config.ni_tach_connection)

    def start(self) -> dict[str, Any]:
        try:
            import nidaqmx
            from nidaqmx.constants import AccelSensitivityUnits, AccelUnits, AcquisitionType, Coupling, ExcitationSource
        except Exception as exc:  # pragma: no cover - target PC dependency
            raise AcquisitionError("未安装 NI-DAQmx 或 nidaqmx。请先安装 NI-DAQmx 驱动，再执行 pip install nidaqmx。") from exc
        try:
            self.task = nidaqmx.Task()
            for logical, physical, sensitivity in self.ni_channels:
                channel = self.task.ai_channels.add_ai_accel_chan(
                    physical,
                    name_to_assign_to_channel=logical,
                    min_val=-500.0,
                    max_val=500.0,
                    units=AccelUnits.G,
                    sensitivity=float(sensitivity),
                    sensitivity_units=AccelSensitivityUnits.MILLIVOLTS_PER_G,
                    current_excit_source=ExcitationSource.INTERNAL,
                    current_excit_val=self.config.ni_iepe_current_ma / 1000.0,
                )
                channel.ai_coupling = Coupling.AC
            self.task.timing.cfg_samp_clk_timing(
                rate=float(self.config.scan_rate),
                sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=self.config.samples_per_read * 4,
            )
            self.actual_scan_rate = float(self.task.timing.samp_clk_rate)
            self.task.start()
            self.labjack.requested_scan_rate = self.actual_scan_rate
            self.labjack.start()
        except Exception as exc:
            self.close()
            if isinstance(exc, AcquisitionError):
                raise
            raise AcquisitionError(
                "NI-9234 启动失败。请在 NI MAX 确认模块名、物理通道和自检状态。"
                f"\nDAQmx：{exc}"
            ) from exc
        return {
            "model": "NI-9234 + LabJack T7",
            "serial": f"{self.config.ni_module} / T7 {self.labjack.serial}",
            "connection": f"IEPE 2.0 mA + LabJack {self.labjack.connection} 转速",
            "backend": "ni9234",
        }

    @staticmethod
    def _matrix(raw: Any, channels: int) -> np.ndarray:
        values = np.asarray(raw, dtype=float)
        if channels == 1:
            values = values.reshape(1, -1)
        elif values.ndim != 2 or values.shape[0] != channels:
            raise AcquisitionError("NI-DAQmx 返回的数据通道数与配置不一致。")
        return values

    def read(self) -> StreamChunk:
        if self.task is None:
            raise AcquisitionError("NI-9234 尚未启动。")
        timeout = max(10.0, self.config.samples_per_read / self.actual_scan_rate * 4.0)
        try:
            raw = self.task.read(number_of_samples_per_channel=self.config.samples_per_read, timeout=timeout)
            lj_data, device_backlog, ljm_backlog, dropped = self.labjack.read()
        except Exception as exc:
            if isinstance(exc, AcquisitionError):
                raise
            raise AcquisitionError(f"NI/LabJack 组合采集读取失败：{exc}") from exc
        matrix = self._matrix(raw, len(self.ni_channels))
        count = matrix.shape[1]
        channels = {"AIN0": np.zeros(count), "AIN1": np.zeros(count), "TACH": lj_data["TACH"]}
        sources = {"AIN0": "未安装", "AIN1": "未安装", "TACH": self.config.labjack_tach_channel}
        units = {"AIN0": "V", "AIN1": "V", "TACH": "logic"}
        for index, (logical, physical, _sensitivity) in enumerate(self.ni_channels):
            channels[logical] = matrix[index]
            sources[logical] = physical
            units[logical] = "g"
        for logical in ("AIN0", "AIN1"):
            if logical in lj_data:
                channels[logical] = _resample(lj_data[logical], count)
                sources[logical] = self.config.labjack_left_channel if logical == "AIN0" else self.config.labjack_right_channel
                units[logical] = "V"
        mixed_devices = {units[key] for key in ("AIN0", "AIN1") if sources[key] != "未安装"} == {"V", "g"}
        return StreamChunk(
            channels=channels,
            scan_rate=self.actual_scan_rate,
            device_backlog=device_backlog,
            ljm_backlog=ljm_backlog,
            dropped_samples=dropped,
            channel_units=units,
            channel_sources=sources,
            acquisition_backend="ni9234",
            reference_ppr=max(1, int(self.config.tach_ppr)),
            reference_scan_rate=self.labjack.actual_scan_rate,
            channels_synchronized=not mixed_devices,
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


def create_backend(config: AcquisitionConfig) -> AcquisitionBackend:
    ok, message = validate_acquisition_config(config)
    if not ok:
        raise AcquisitionError(message)
    if config.backend.startswith("labjack"):
        return LabJackBackend(config)
    if config.backend == "ni9234":
        return NI9234Backend(config)
    raise AcquisitionError(f"未知采集后端：{config.backend}")
