"""Single-T7 streams with FIO3 rotor photoelectric state and pulse readings."""

import time
from typing import Any, Dict, Optional

import numpy as np

from drivers.base_acquisition import AcquisitionSource
from models.data import (
    AcquisitionChunk,
    BalancingChunk,
    HardwarePulseMeasurement,
    PiezoChunk,
)

try:
    from labjack import ljm
except ImportError:
    ljm = None


class LabJackT7Source(AcquisitionSource):
    """Share one handle between a stream and DIO-EF diagnostic snapshots."""

    BALANCING_NAMES = (
        "speed",
        "position_a",
        "position_b",
        "force_a",
        "force_b",
    )
    PULSE_DIO = {
        "speed": "DIO0",
        "position_a": "DIO1",
        "position_b": "DIO2",
        "motor_hall": "DIO3",  # Historical wire-format key; now also optical.
    }

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.logical_names = list(self.BALANCING_NAMES)
        self.channel_names = []
        self.scan_rate = float(config["balancing_scan_rate"])
        self.scans_per_read = int(config["balancing_scans_per_read"])
        self.mode = "balancing"
        self.handle: Optional[int] = None
        self.streaming = False
        self._sample_offset = 0
        self._active_pulse_dio: Dict[str, str] = {}
        self._pulse_last_timestamp: Dict[str, int] = {}
        self._pulse_last_change_at: Dict[str, float] = {}
        self._last_pulse_measurements: Dict[str, HardwarePulseMeasurement] = {}

    def open(self, mode: str = "balancing") -> dict:
        if ljm is None:
            raise RuntimeError(
                "未安装 LabJack LJM Python 包，请安装 LJM 驱动后执行 pip install labjack-ljm"
            )
        connection = str(self.config.get("connection", "USB"))
        identifier = str(self.config.get("serial", "AUTO"))
        if identifier.upper() == "AUTO":
            identifier = self._automatic_identifier(connection)
        self.handle = ljm.openS("T7", connection, identifier)
        try:
            self.mode = str(mode)
            if self.mode == "piezo":
                self.logical_names = [
                    "piezo_left", "piezo_right", "piezo_reference"
                ]
                self._active_pulse_dio = {"motor_hall": self.PULSE_DIO["motor_hall"]}
                self.scan_rate = float(self.config["piezo_scan_rate"])
                self.scans_per_read = int(self.config["piezo_scans_per_read"])
            elif self.mode == "balancing":
                self.logical_names = list(self.BALANCING_NAMES)
                self._active_pulse_dio = dict(self.PULSE_DIO)
                self.scan_rate = float(self.config["balancing_scan_rate"])
                self.scans_per_read = int(self.config["balancing_scans_per_read"])
            else:
                raise ValueError("未知采集模式：%s" % self.mode)
            self.channel_names = [
                self.config["channels"][name] for name in self.logical_names
            ]
            if len(set(self.channel_names)) != len(self.channel_names):
                raise ValueError("压电和转子参考通道不能映射到同一接口")
            self._pulse_last_timestamp.clear()
            self._sample_offset = 0
            self._pulse_last_change_at.clear()
            timestamp_clock_hz = float(
                self.config.get("core_timer_frequency_hz", 40_000_000.0)
            )
            self._last_pulse_measurements = {
                name: HardwarePulseMeasurement(
                    timestamp_clock_hz=timestamp_clock_hz
                )
                for name in self._active_pulse_dio
            }
            self._configure_interrupt_pulse_inputs()
            self._configure_analog_inputs()
            addresses, _ = ljm.namesToAddresses(
                len(self.channel_names), self.channel_names
            )
            self.scan_rate = float(
                ljm.eStreamStart(
                    self.handle,
                    self.scans_per_read,
                    len(addresses),
                    addresses,
                    self.scan_rate,
                )
            )
            self.streaming = True
            info = ljm.getHandleInfo(self.handle)
            return {
                "name": str(self.config.get("name", "LabJack T7")),
                "serial": str(info[2]),
                "connection": connection,
                "mode": self.mode,
                "scan_rate": self.scan_rate,
            }
        except Exception:
            self.close()
            raise

    def _automatic_identifier(self, connection: str) -> str:
        device_index = int(self.config.get("device_index", 0))
        found, _, _, serial_numbers, _ = ljm.listAllS("T7", connection)
        if found <= device_index:
            raise RuntimeError(
                "未发现 LabJack T7，请检查 USB、供电和 LJM 驱动"
            )
        return str(serial_numbers[device_index])

    def _configure_analog_inputs(self) -> None:
        if self.handle is None:
            raise RuntimeError("LabJack T7 尚未打开")
        analog_channels = (
            (self.config["channels"]["piezo_left"], self.config["channels"]["piezo_right"])
            if self.mode == "piezo"
            else (
                self.config["channels"]["force_a"],
                self.config["channels"]["force_b"],
            )
        )
        input_range = float(self.config.get("ain_range", 10.0))
        for channel in analog_channels:
            ljm.eWriteName(self.handle, channel + "_RANGE", input_range)
        ljm.eWriteName(
            self.handle,
            "STREAM_RESOLUTION_INDEX",
            int(self.config.get("resolution_index", 0)),
        )

    def _configure_interrupt_pulse_inputs(self) -> None:
        """Enable only the pulse inputs needed by the selected stream mode."""
        if self.handle is None:
            raise RuntimeError("LabJack T7 尚未打开")
        for dio_name in self.PULSE_DIO.values():
            ljm.eWriteName(self.handle, dio_name + "_EF_ENABLE", 0)
        # Remove the clock used by the previous Frequency In (index 3)
        # configuration.  Interrupt Frequency In (index 11) uses Core_Timer.
        ljm.eWriteName(self.handle, "DIO_EF_CLOCK0_ENABLE", 0)
        for logical_name, dio_name in self._active_pulse_dio.items():
            ljm.eWriteName(self.handle, dio_name + "_EF_INDEX", 11)
            # Keep the legacy FIO0/1/2 signals on rising edges. The LV-N11N
            # waveform measured on FIO3 has a much faster/cleaner falling edge
            # than rising edge, so use continuous falling-edge mode there.
            # CONFIG_A: 2 = continuous falling, 3 = continuous rising.
            optical_edge = str(
                self.config.get("optical_edge", "falling")
            ).strip().lower()
            continuous_mode = (
                2 if logical_name == "motor_hall" and optical_edge == "falling"
                else 3
            )
            ljm.eWriteName(
                self.handle, dio_name + "_EF_CONFIG_A", continuous_mode
            )
            # The auxiliary FIO3 input can average several periods without
            # adding a stream channel. This average count is NOT the number of
            # pulses per mechanical turn.
            average_periods = (
                int(self.config.get(
                    "optical_average_periods",
                    self.config.get("motor_hall_average_periods", 4),
                ))
                if logical_name == "motor_hall"
                else 1
            )
            ljm.eWriteName(
                self.handle,
                dio_name + "_EF_CONFIG_B",
                max(1, average_periods),
            )
            ljm.eWriteName(self.handle, dio_name + "_EF_ENABLE", 1)

    def _read_hardware_pulses(self) -> Dict[str, HardwarePulseMeasurement]:
        if self.handle is None or not self._active_pulse_dio:
            return {}
        try:
            register_names = []
            logical_names = []
            for logical_name, dio_name in self._active_pulse_dio.items():
                # READ_A must be read before READ_B to latch a valid timestamp.
                register_names.extend(
                    (dio_name + "_EF_READ_A_F", dio_name + "_EF_READ_B")
                )
                logical_names.append(logical_name)
            values = ljm.eReadNames(
                self.handle, len(register_names), register_names
            )
            timestamp_clock_hz = float(
                self.config.get("core_timer_frequency_hz", 40_000_000.0)
            )
            stale_seconds = float(self.config.get("pulse_stale_seconds", 0.25))
            now = time.monotonic()
            measurements = {}
            for index, logical_name in enumerate(logical_names):
                period = float(values[index * 2])
                timestamp = int(values[index * 2 + 1]) & 0xFFFFFFFF
                frequency = 1.0 / period if period > 0 else 0.0
                if timestamp != self._pulse_last_timestamp.get(logical_name, 0):
                    self._pulse_last_timestamp[logical_name] = timestamp
                    self._pulse_last_change_at[logical_name] = now
                last_change = self._pulse_last_change_at.get(logical_name)
                is_stale = (
                    timestamp == 0
                    or last_change is None
                    or now - last_change > stale_seconds
                )
                if is_stale:
                    frequency = 0.0
                    period = 0.0
                measurements[logical_name] = HardwarePulseMeasurement(
                    frequency_hz=(
                        frequency
                        if np.isfinite(frequency) and frequency > 0
                        else 0.0
                    ),
                    period_seconds=(
                        period if np.isfinite(period) and period > 0 else 0.0
                    ),
                    edge_timestamp_ticks=timestamp,
                    timestamp_clock_hz=timestamp_clock_hz,
                )
            self._last_pulse_measurements = measurements
            return measurements
        except Exception:
            return self._stale_hardware_pulse_snapshot()

    def _stale_hardware_pulse_snapshot(
        self,
    ) -> Dict[str, HardwarePulseMeasurement]:
        """Keep hardware mode selected when a command read fails transiently."""
        stale_seconds = float(self.config.get("pulse_stale_seconds", 0.25))
        now = time.monotonic()
        result = {}
        for logical_name in self._active_pulse_dio:
            previous = self._last_pulse_measurements.get(
                logical_name, HardwarePulseMeasurement()
            )
            changed_at = self._pulse_last_change_at.get(logical_name)
            if changed_at is not None and now - changed_at <= stale_seconds:
                result[logical_name] = previous
            else:
                result[logical_name] = HardwarePulseMeasurement(
                    timestamp_clock_hz=previous.timestamp_clock_hz
                )
        return result

    def read(self) -> AcquisitionChunk:
        if self.handle is None or not self.streaming:
            raise RuntimeError("LabJack T7 流采集尚未启动")
        raw, device_backlog, ljm_backlog = ljm.eStreamRead(self.handle)
        matrix = np.asarray(raw, dtype=float).reshape(-1, len(self.logical_names))
        dropped = int(np.count_nonzero(matrix == -9999.0))
        columns = {
            name: matrix[:, index]
            for index, name in enumerate(self.logical_names)
        }
        common = {
            "scan_rate": self.scan_rate,
            "device_backlog": int(device_backlog),
            "ljm_backlog": int(ljm_backlog),
            "dropped_samples": dropped,
        }
        # Command-response pulse reads share the stream handle. In piezo mode
        # DIO3 is additionally present in the scan list; this snapshot remains
        # useful for display/diagnostics but is not the shaft-phase source.
        hardware_pulses = self._read_hardware_pulses()
        raw_frequencies = {
            name: measurement.frequency_hz
            for name, measurement in hardware_pulses.items()
        }
        optical_divisor = max(
            1.0, float(self.config.get("optical_frequency_divisor", 1.0))
        )
        pulse_data = {
            "hardware_frequencies": {
                name: (frequency / optical_divisor
                       if name == "motor_hall" else frequency)
                for name, frequency in raw_frequencies.items()
            },
            "hardware_raw_frequencies": raw_frequencies,
            "hardware_pulses": hardware_pulses,
        }
        if self.mode == "piezo":
            start_sample = self._sample_offset
            self._sample_offset += len(columns["piezo_left"])
            return AcquisitionChunk(
                piezo=PiezoChunk(
                    channels={"left": columns["piezo_left"],
                              "right": columns["piezo_right"]},
                    reference_state=columns["piezo_reference"],
                    start_sample=start_sample, **common
                ),
                **pulse_data
            )
        return AcquisitionChunk(
            balancing=BalancingChunk(
                channels={name: columns[name] for name in self.BALANCING_NAMES},
                **common
            ),
            **pulse_data
        )

    def close(self) -> None:
        if self.handle is None:
            return
        if self.streaming:
            try:
                ljm.eStreamStop(self.handle)
            finally:
                self.streaming = False
        try:
            for dio_name in self.PULSE_DIO.values():
                ljm.eWriteName(self.handle, dio_name + "_EF_ENABLE", 0)
        except Exception:
            pass
        ljm.close(self.handle)
        self.handle = None
        self._active_pulse_dio.clear()
        self._pulse_last_timestamp.clear()
        self._pulse_last_change_at.clear()
        self._last_pulse_measurements.clear()
