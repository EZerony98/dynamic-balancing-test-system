"""Incremental recording of laser waveform and FFT results."""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from models.data import LaserAnalysis, LaserChunk


class LaserRecordService:
    def __init__(self, export_directory: Path) -> None:
        self.export_directory = Path(export_directory)
        self.directory: Optional[Path] = None
        self._raw_file = None
        self._raw_writer = None
        self._spectrum_file = None
        self._spectrum_writer = None
        self._sample_offset = 0
        self._frame = 0
        self._pulse_file = None
        self._pulse_writer = None
        self._measurement_file = None

    @property
    def recording(self) -> bool:
        return self._raw_file is not None

    def start(self, metadata: Optional[dict] = None) -> Path:
        if self.recording and self.directory is not None:
            return self.directory
        timestamp = datetime.now().strftime("laser_%Y%m%d_%H%M%S_%f") + "_" + uuid4().hex[:6]
        self.directory = self.export_directory / timestamp
        self.directory.mkdir(parents=True, exist_ok=True)
        self._raw_file = (self.directory / "laser_raw_signal.csv").open(
            "w", newline="", encoding="utf-8-sig"
        )
        self._spectrum_file = (self.directory / "laser_spectrum_frames.csv").open(
            "w", newline="", encoding="utf-8-sig"
        )
        self._raw_writer = csv.writer(self._raw_file)
        self._spectrum_writer = csv.writer(self._spectrum_file)
        self._raw_writer.writerow(("time_s", "laser_voltage_v", "acquisition_sample"))
        self._spectrum_writer.writerow(("frame", "frequency_hz", "amplitude_v"))
        self._sample_offset = 0
        self._frame = 0
        self._pulse_file = (self.directory / "hall_reference_snapshots.csv").open(
            "w", newline="", encoding="utf-8-sig"
        )
        self._pulse_writer = csv.writer(self._pulse_file)
        self._pulse_writer.writerow(("acquisition_start_sample", "sample_count", "scan_rate_hz",
                                     "received_timestamp", "motor_hall_hz", "dropped_samples"))
        self._measurement_file = (self.directory / "balance_measurements.jsonl").open("w", encoding="utf-8")
        with (self.directory / "measurement_metadata.json").open("w", encoding="utf-8") as handle:
            json.dump(metadata or {}, handle, ensure_ascii=False, indent=2, allow_nan=False)
        return self.directory

    def append_waveform(self, chunk: LaserChunk, hardware_frequencies: Optional[dict] = None) -> None:
        if not self.recording or self._raw_writer is None:
            return
        self._raw_writer.writerows(
            (
                (self._sample_offset + index) / chunk.scan_rate,
                float(value),
                chunk.start_sample + index,
            )
            for index, value in enumerate(chunk.values)
        )
        self._sample_offset += len(chunk.values)
        if self._pulse_writer is not None:
            self._pulse_writer.writerow((chunk.start_sample, len(chunk.values), chunk.scan_rate,
                                         chunk.timestamp, (hardware_frequencies or {}).get("motor_hall", 0),
                                         chunk.dropped_samples))

    def append_measurement(self, record: dict) -> None:
        if self._measurement_file is not None:
            self._measurement_file.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")

    def export_balance_report(self, report: dict) -> Path:
        self.export_directory.mkdir(parents=True, exist_ok=True)
        name = datetime.now().strftime("balance_calibration_%Y%m%d_%H%M%S_%f") + "_" + uuid4().hex[:6] + ".json"
        path = self.export_directory / name
        with path.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        return path

    def append_spectrum(self, analysis: LaserAnalysis) -> None:
        if not self.recording or self._spectrum_writer is None:
            return
        self._spectrum_writer.writerows(
            (self._frame, float(frequency), float(amplitude))
            for frequency, amplitude in zip(
                analysis.frequencies, analysis.amplitudes
            )
        )
        self._frame += 1

    def stop(self) -> Optional[Path]:
        directory = self.directory
        failure = None
        for handle in (self._raw_file, self._spectrum_file, self._pulse_file, self._measurement_file):
            if handle is not None:
                try:
                    handle.close()
                except OSError as exc:
                    failure = exc
        self._raw_file = None
        self._raw_writer = None
        self._spectrum_file = None
        self._spectrum_writer = None
        self._pulse_file = None
        self._pulse_writer = None
        self._measurement_file = None
        if failure is not None:
            raise failure
        return directory
