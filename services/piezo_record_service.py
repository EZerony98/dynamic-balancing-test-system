"""Incremental two-channel piezo recorder; saved values remain raw volts."""

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from models.data import PiezoChunk
from models.piezo_balance import Piezo1XResult, PiezoSpectrumAnalysis


class PiezoRecordService:
    def __init__(self, export_directory: Path) -> None:
        self.export_directory = Path(export_directory)
        self.directory: Optional[Path] = None
        self._handles = []
        self._raw = None
        self._fft = None
        self._tach = None
        self._one_x = None
        self._frame = 0

    @property
    def recording(self) -> bool:
        return self._raw is not None

    def start(self, metadata: dict) -> Path:
        if self.recording and self.directory is not None:
            return self.directory
        product_id = str(metadata.get("product_id", "")).strip()
        safe_product_id = re.sub(r"[^0-9A-Za-z._-]+", "_", product_id).strip("._-")
        prefix = (safe_product_id + "_") if safe_product_id else ""
        name = prefix + datetime.now().strftime("piezo_%Y%m%d_%H%M%S_%f") + "_" + uuid4().hex[:6]
        self.directory = self.export_directory / name
        self.directory.mkdir(parents=True, exist_ok=False)
        try:
            def writer(filename: str):
                handle = (self.directory / filename).open("w", newline="", encoding="utf-8-sig")
                self._handles.append(handle)
                return csv.writer(handle)

            self._raw = writer("piezo_left_right_raw.csv")
            self._fft = writer("piezo_left_right_fft_frames.csv")
            self._tach = writer("rotor_photoelectric_snapshots.csv")
            self._one_x = (self.directory / "piezo_one_x.jsonl").open("w", encoding="utf-8")
            self._handles.append(self._one_x)
            self._raw.writerow(("sample_index", "time_s", "left_voltage_v",
                                "right_voltage_v", "fio3_state"))
            self._fft.writerow(("frame", "frequency_hz", "left_amplitude_v", "right_amplitude_v"))
            self._tach.writerow(("start_sample", "sample_count", "scan_rate_hz",
                                 "received_timestamp", "fio3_corrected_frequency_hz",
                                 "fio3_raw_frequency_hz", "dropped_samples"))
            metadata = dict(metadata)
            source = (
                "configurable_vibration_sensor_left_right"
                if metadata.get("sensors") else "amplified_piezo_left_right"
            )
            metadata.update(source=source, voltage_unit="V",
                            raw_voltage_preserved=True,
                            physical_force_calibrated=False,
                            shaft_reference_streamed=True,
                            shaft_angle_synchronous=True,
                            shaft_angle_calibrated=False,
                            dual_plane_calibrated=False)
            with (self.directory / "measurement_metadata.json").open("w", encoding="utf-8") as output:
                json.dump(metadata, output, ensure_ascii=False, indent=2, allow_nan=False)
            self._frame = 0
            return self.directory
        except Exception:
            self.stop()
            raise

    def append_waveform(
        self, chunk: PiezoChunk, pulse_hz: float, raw_pulse_hz: Optional[float] = None
    ) -> None:
        if not self.recording:
            return
        reference = chunk.reference_state
        self._raw.writerows((
            chunk.start_sample + index,
            (chunk.start_sample + index) / chunk.scan_rate,
            float(left), float(right),
            float(reference[index]) if len(reference) > index else "",
        ) for index, (left, right) in enumerate(zip(
            chunk.channels["left"], chunk.channels["right"])))
        self._tach.writerow((chunk.start_sample, len(chunk.channels["left"]), chunk.scan_rate,
                             chunk.timestamp, float(pulse_hz),
                             float(pulse_hz if raw_pulse_hz is None else raw_pulse_hz),
                             chunk.dropped_samples))

    def append_spectrum(self, analysis: PiezoSpectrumAnalysis) -> None:
        if not self.recording:
            return
        self._fft.writerows((self._frame, float(frequency), float(left), float(right))
                            for frequency, left, right in zip(
                                analysis.frequencies, analysis.amplitudes["left"],
                                analysis.amplitudes["right"]))
        self._frame += 1

    def append_one_x(self, result: Piezo1XResult) -> None:
        if not self.recording:
            return
        record = {
            "frequency_hz": result.frequency_hz,
            "reference_hz": result.reference_hz,
            "rpm": result.rpm,
            "rpm_variation": result.rpm_variation,
            "reference_min_hz": result.reference_min_hz,
            "reference_max_hz": result.reference_max_hz,
            "reference_held_fraction": result.reference_held_fraction,
            "synchronous_reference_hz": result.synchronous_reference_hz,
            "synchronous_period_variation": result.synchronous_period_variation,
            "reference_edge_count": result.reference_edge_count,
            "repaired_saturation_samples": result.repaired_saturation_samples,
            "shaft_angle_synchronous": result.shaft_angle_synchronous,
            "preview_only": result.preview_only,
            "left": {"valid": result.left.valid, "preview": result.left.preview,
                     "amplitude_v": result.left.amplitude_v,
                     "snr": result.left.snr, "reason": result.left.reason,
                     "fio3_relative_phase_degrees": result.left.shaft_phase_degrees},
            "right": {"valid": result.right.valid, "preview": result.right.preview,
                      "amplitude_v": result.right.amplitude_v,
                      "snr": result.right.snr, "reason": result.right.reason,
                      "fio3_relative_phase_degrees": result.right.shaft_phase_degrees},
            "relative_electrical_phase_degrees": result.relative_phase_degrees,
            "status": result.status,
        }
        self._one_x.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")

    def stop(self) -> Optional[Path]:
        directory = self.directory
        failure = None
        for handle in self._handles:
            try:
                handle.close()
            except OSError as exc:
                failure = exc
        self._handles = []
        self._raw = self._fft = self._tach = self._one_x = None
        self.directory = None
        if failure:
            raise failure
        return directory
