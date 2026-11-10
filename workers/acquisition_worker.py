"""Worker for five-item or dual-piezo plus sample-aligned FIO3 streams."""

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from drivers.base_acquisition import AcquisitionSource
from models.piezo_balance import PiezoSettings, PiezoSpectrumAnalysis, SIDES
from models.sensors import sensor_profiles_from_config, sensor_profiles_to_dict
from services.piezo_balance import DualPiezoAnalyzer
from services.spectrum import extract_target_tone, spectrum


class AcquisitionWorker(QObject):
    connected = Signal(dict)
    data_ready = Signal(object)
    piezo_analysis_ready = Signal(object)
    piezo_one_x_ready = Signal(object)
    stream_started = Signal(float, str)
    stream_stopped = Signal()
    error = Signal(str)

    def __init__(
        self,
        source: AcquisitionSource,
        fft_size: int,
        target_frequency: float = 154.0,
        target_window_seconds: float = 1.5,
        analysis_interval_ms: int = 200,
        piezo_settings: dict = None,
        sensor_profiles: dict = None,
    ) -> None:
        super().__init__()
        self.source = source
        self.fft_size = fft_size
        self.target_frequency = float(target_frequency)
        self.target_window_seconds = max(0.25, float(target_window_seconds))
        self.analysis_interval_ms = max(50, int(analysis_interval_ms))
        self.timer = None
        self.running = False
        self.interval_ms = 50
        self._analysis_every = 1
        self.piezo_settings = PiezoSettings.from_dict(piezo_settings or {})
        self.sensor_profiles = sensor_profiles_from_config(sensor_profiles)
        self.piezo_analyzer = DualPiezoAnalyzer(
            self.piezo_settings,
            sensor_profiles=sensor_profiles_to_dict(self.sensor_profiles),
        )
        self._piezo_buffers = {side: np.array([], dtype=float) for side in SIDES}
        self._piezo_total_samples = 0
        self._piezo_counter = 0
        self._last_piezo_one_x = None

    @Slot(object)
    def set_piezo_settings(self, settings: dict) -> None:
        try:
            candidate = PiezoSettings.from_dict(settings)
            candidate.validate()
            self.piezo_settings = candidate
            self.piezo_analyzer = DualPiezoAnalyzer(
                candidate,
                sensor_profiles=sensor_profiles_to_dict(self.sensor_profiles),
            )
            self._piezo_buffers = {side: np.array([], dtype=float) for side in SIDES}
            self._piezo_counter = 0
            self._last_piezo_one_x = None
        except ValueError as exc:
            self.error.emit(str(exc))

    @Slot(object)
    def set_sensor_profiles(self, profiles: dict) -> None:
        try:
            candidate = sensor_profiles_from_config(profiles)
            source_setter = getattr(self.source, "set_sensor_profiles", None)
            if source_setter is not None:
                source_setter(sensor_profiles_to_dict(candidate))
            self.sensor_profiles = candidate
            self.piezo_analyzer = DualPiezoAnalyzer(
                self.piezo_settings,
                sensor_profiles=sensor_profiles_to_dict(candidate),
            )
            self._piezo_buffers = {side: np.array([], dtype=float) for side in SIDES}
            self._piezo_counter = 0
            self._last_piezo_one_x = None
        except ValueError as exc:
            self.error.emit(str(exc))

    @Slot(str)
    def start(self, mode: str = "balancing") -> None:
        if self.running:
            return
        self.running = True
        self._piezo_buffers = {side: np.array([], dtype=float) for side in SIDES}
        self._piezo_total_samples = 0
        self._piezo_counter = 0
        self._last_piezo_one_x = None
        self.piezo_analyzer.reset()
        try:
            info = self.source.open(mode=mode)
            self.connected.emit(info)
            scans_per_read = int(getattr(self.source, "scans_per_read", 800))
            scan_rate = float(getattr(self.source, "scan_rate", 16_000.0))
            self.interval_ms = max(20, int(scans_per_read / scan_rate * 1000))
            self._analysis_every = max(
                1, int(round(self.analysis_interval_ms / self.interval_ms))
            )
            self.piezo_analyzer.input_range = float(
                getattr(self.source, "config", {}).get("ain_range", 10.0)
            )
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._read)
            self.timer.start(self.interval_ms)
            self.stream_started.emit(scan_rate, mode)
        except Exception as exc:
            self.running = False
            self.error.emit("振动/转速采集启动失败：%s" % exc)
            self.stream_stopped.emit()

    @Slot()
    def _read(self) -> None:
        if not self.running:
            return
        try:
            chunk = self.source.read()
            self.data_ready.emit(chunk)
            if chunk.piezo is not None:
                self._process_piezo(chunk)
        except Exception as exc:
            self.error.emit("LabJack T7 采集或频谱处理失败：%s" % exc)
            self.stop()

    def _process_piezo(self, chunk: object) -> None:
        data = chunk.piezo
        self._piezo_total_samples = data.start_sample + len(data.channels["left"])
        result = self.piezo_analyzer.update(
            data, float(chunk.hardware_frequencies.get("motor_hall", 0.0))
        )
        if result is not None:
            self._last_piezo_one_x = result
            self.piezo_one_x_ready.emit(result)
        if (data.dropped_samples or any(
            not np.all(np.isfinite(data.channels[side])) or
            np.any(data.channels[side] == -9999.0) for side in SIDES
        )):
            self._piezo_buffers = {side: np.array([], dtype=float) for side in SIDES}
            self._piezo_counter = 0
            return
        # The analyzer already tolerates a very small number of isolated
        # full-scale samples. Apply the same repair to the FFT display copy so
        # one EMI/contact spike does not flatten the useful 1X spectrum. Raw
        # waveform/export data are intentionally left untouched.
        fft_channels = {
            side: self._repair_isolated_fft_spikes(data.channels[side])
            for side in SIDES
        }
        limit = max(self.fft_size, int(data.scan_rate * self.target_window_seconds))
        for side in SIDES:
            self._piezo_buffers[side] = np.concatenate(
                (self._piezo_buffers[side], fft_channels[side])
            )[-limit:]
        self._piezo_counter += 1
        if self._piezo_counter % self._analysis_every:
            return
        spectra = {}
        metrics = {}
        frequencies = np.array([], dtype=float)
        for side in SIDES:
            frequencies, spectra[side], metrics[side] = spectrum(
                self._piezo_buffers[side][-self.fft_size:], data.scan_rate,
                fft_size=self.fft_size, window="Hann", remove_dc=True
            )
        manual_tones = {}
        count = max(3, int(data.scan_rate * self.target_window_seconds))
        if self.piezo_settings.analysis_mode == "manual" and all(
            len(self._piezo_buffers[side]) >= count for side in SIDES
        ):
            for side in SIDES:
                manual_tones[side] = extract_target_tone(
                    self._piezo_buffers[side][-count:], data.scan_rate,
                    self.target_frequency,
                    start_sample_index=self._piezo_total_samples - count,
                )
        self.piezo_analysis_ready.emit(PiezoSpectrumAnalysis(
            frequencies=frequencies, amplitudes=spectra, metrics=metrics,
            frequency_resolution=(data.scan_rate / max(1, len(self._piezo_buffers["left"][-self.fft_size:]))),
            one_x=self._last_piezo_one_x, manual_tones=manual_tones,
        ))

    def _repair_isolated_fft_spikes(self, values: np.ndarray) -> np.ndarray:
        """Repair only isolated near-full-scale points in the FFT copy."""
        samples = np.asarray(values, dtype=float)
        input_range = float(
            getattr(self.source, "config", {}).get("ain_range", 10.0)
        )
        clipped = np.abs(samples) >= input_range * 0.98
        clipped_indices = np.flatnonzero(clipped)
        if not len(clipped_indices):
            return samples
        allowed = max(1, int(np.ceil(
            len(samples) * self.piezo_settings.saturation_spike_fraction_limit
        )))
        # Use an explicit run calculation; sustained clipping must remain
        # visible and will be rejected by the main 1X analyzer.
        run = 1
        longest_run = 1
        for previous, current in zip(clipped_indices[:-1], clipped_indices[1:]):
            run = run + 1 if current == previous + 1 else 1
            longest_run = max(longest_run, run)
        if (
            len(samples) < 100
            or len(clipped_indices) > allowed
            or longest_run > self.piezo_settings.saturation_run_limit_samples
        ):
            return samples
        valid = np.flatnonzero(~clipped)
        if len(valid) < 2:
            return samples
        repaired = samples.copy()
        repaired[clipped_indices] = np.interp(
            clipped_indices, valid, samples[valid]
        )
        return repaired

    @Slot(float)
    def set_target_frequency(self, frequency: float) -> None:
        self.target_frequency = max(0.01, float(frequency))

    @Slot()
    def stop(self) -> None:
        self.running = False
        if self.timer is not None:
            self.timer.stop()
            self.timer.deleteLater()
            self.timer = None
        try:
            self.source.close()
        except Exception as exc:
            self.error.emit("LabJack T7 关闭失败：%s" % exc)
        self.stream_stopped.emit()
