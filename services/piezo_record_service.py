"""Incremental two-channel piezo recorder; saved values remain raw volts."""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from models.data import PiezoChunk
from models.piezo_balance import Piezo1XResult, PiezoSpectrumAnalysis
from services.export_dictionary import safe_product_filename, write_field_dictionary
from services.sensor_export import (
    amplitude_engineering_columns,
    engineering_units,
    export_profiles,
    waveform_engineering_columns,
)


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
        self._profiles = export_profiles()
        self._metadata = {}
        self._metadata_path: Optional[Path] = None
        self._duration_seconds = 2.0
        self._target_samples: Optional[int] = None
        self._recorded_samples = 0
        self._capture_scan_rate: Optional[float] = None
        self._capture_complete = False

    @property
    def recording(self) -> bool:
        return self._raw is not None

    def start(self, metadata: dict) -> Path:
        if self.recording and self.directory is not None:
            return self.directory
        product_id = str(metadata.get("product_id", "")).strip()
        safe_product_id = safe_product_filename(product_id)
        prefix = safe_product_id + "_"
        name = prefix + datetime.now().strftime("piezo_%Y%m%d_%H%M%S_%f") + "_" + uuid4().hex[:6]
        self.directory = self.export_directory / name
        self.directory.mkdir(parents=True, exist_ok=False)
        try:
            def writer(filename: str):
                handle = (self.directory / filename).open("w", newline="", encoding="utf-8-sig")
                self._handles.append(handle)
                return csv.writer(handle)

            filenames = {
                "raw": "%spiezo_left_right_raw.csv" % prefix,
                "spectrum": "%spiezo_left_right_fft_frames.csv" % prefix,
                "tach": "%srotor_photoelectric_snapshots.csv" % prefix,
                "one_x": "%spiezo_one_x.jsonl" % prefix,
                "field_dictionary": "%sfield_dictionary.csv" % prefix,
            }
            self._raw = writer(filenames["raw"])
            self._fft = writer(filenames["spectrum"])
            self._tach = writer(filenames["tach"])
            self._one_x = (self.directory / filenames["one_x"]).open(
                "w", encoding="utf-8"
            )
            self._handles.append(self._one_x)
            self._profiles = export_profiles(metadata.get("sensors", {}))
            raw_headers = []
            fft_headers = []
            for side in ("left", "right"):
                raw_headers.append("%s_voltage_v" % side)
                raw_headers.extend(
                    name for name, _value in waveform_engineering_columns(
                        side, self._profiles[side], 0.0
                    )
                )
                fft_headers.append("%s_amplitude_v" % side)
                fft_headers.extend(
                    name for name, _value in amplitude_engineering_columns(
                        side, self._profiles[side], 0.0
                    )
                )
            self._raw.writerow(("sample_index", "time_s", *raw_headers,
                                "fio3_state"))
            self._fft.writerow(("frame", "frequency_hz", *fft_headers))
            self._tach.writerow(("start_sample", "sample_count", "scan_rate_hz",
                                 "received_timestamp", "fio3_corrected_frequency_hz",
                                 "fio3_raw_frequency_hz", "dropped_samples"))
            write_field_dictionary(
                self.directory / filenames["field_dictionary"],
                self._profiles,
                filenames,
                include_recording_streams=True,
            )
            metadata = dict(metadata)
            has_accelerometer = any(
                profile.sensor_type == "accelerometer"
                for profile in self._profiles.values() if profile.enabled
            )
            source = (
                "configurable_vibration_sensor_left_right"
                if metadata.get("sensors") else "amplified_piezo_left_right"
            )
            self._duration_seconds = max(
                0.1, float(metadata.get("record_duration_seconds", 2.0))
            )
            self._target_samples = None
            self._recorded_samples = 0
            self._capture_scan_rate = None
            self._capture_complete = False
            metadata.update(record_type="fixed_duration_recording",
                            source=source, voltage_unit="V",
                            engineering_units=engineering_units(self._profiles),
                            unit_note=(
                                "*_voltage_v/*_amplitude_v 保留传感器电压；"
                                "加速度侧同时给出 g、m/s^2，压电侧同时给出 mV"
                            ),
                            raw_voltage_preserved=True,
                            physical_force_calibrated=False,
                            shaft_reference_streamed=True,
                            shaft_angle_synchronous=not has_accelerometer,
                            shaft_angle_calibrated=False,
                            dual_plane_calibrated=False,
                            requested_duration_seconds=self._duration_seconds,
                            capture_complete=False,
                            recorded_sample_count=0,
                            files=filenames)
            self._metadata = metadata
            self._metadata_path = self.directory / "measurement_metadata.json"
            self._write_metadata()
            self._frame = 0
            return self.directory
        except Exception:
            self.stop()
            raise

    def append_waveform(
        self, chunk: PiezoChunk, pulse_hz: float, raw_pulse_hz: Optional[float] = None
    ) -> bool:
        if not self.recording:
            return False
        if self._capture_scan_rate is None:
            self._capture_scan_rate = float(chunk.scan_rate)
            self._target_samples = max(
                1, int(round(self._capture_scan_rate * self._duration_seconds))
            )
        elif abs(float(chunk.scan_rate) - self._capture_scan_rate) > 1e-6:
            raise ValueError("固定记录期间采样率发生变化")
        available = min(
            len(chunk.channels["left"]), len(chunk.channels["right"])
        )
        remaining = max(0, int(self._target_samples or 0) - self._recorded_samples)
        count = min(available, remaining)
        if count <= 0:
            self._capture_complete = bool(self._target_samples)
            return self._capture_complete
        reference = chunk.reference_state
        recorded_before = self._recorded_samples
        for index, (left, right) in enumerate(zip(
            chunk.channels["left"][:count], chunk.channels["right"][:count]
        )):
            row = [
                chunk.start_sample + index,
                (recorded_before + index) / chunk.scan_rate,
            ]
            for side, value in (("left", left), ("right", right)):
                row.append(float(value))
                row.extend(
                    engineering for _name, engineering
                    in waveform_engineering_columns(
                        side, self._profiles[side], float(value)
                    )
                )
            row.append(float(reference[index]) if len(reference) > index else "")
            self._raw.writerow(row)
        self._tach.writerow((chunk.start_sample, count, chunk.scan_rate,
                             chunk.timestamp, float(pulse_hz),
                             float(pulse_hz if raw_pulse_hz is None else raw_pulse_hz),
                             chunk.dropped_samples))
        self._recorded_samples += count
        self._capture_complete = self._recorded_samples >= int(self._target_samples or 0)
        return self._capture_complete

    def append_spectrum(self, analysis: PiezoSpectrumAnalysis) -> None:
        if not self.recording:
            return
        for frequency, left, right in zip(
            analysis.frequencies,
            analysis.amplitudes["left"],
            analysis.amplitudes["right"],
        ):
            row = [self._frame, float(frequency)]
            for side, value in (("left", left), ("right", right)):
                row.append(float(value))
                row.extend(
                    engineering for _name, engineering
                    in amplitude_engineering_columns(
                        side, self._profiles[side], float(value)
                    )
                )
            self._fft.writerow(row)
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
        for side in ("left", "right"):
            side_result = getattr(result, side)
            record[side].update(amplitude_engineering_columns(
                side, self._profiles[side], side_result.amplitude_v
            ))
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
        if self._metadata_path is not None:
            self._metadata.update(
                capture_complete=self._capture_complete,
                recorded_sample_count=self._recorded_samples,
                target_sample_count=self._target_samples,
                actual_duration_seconds=(
                    self._recorded_samples / self._capture_scan_rate
                    if self._capture_scan_rate else 0.0
                ),
                scan_rate_hz=self._capture_scan_rate,
            )
            try:
                self._write_metadata()
            except OSError as exc:
                failure = failure or exc
        self._profiles = export_profiles()
        self._metadata = {}
        self._metadata_path = None
        self._target_samples = None
        self._recorded_samples = 0
        self._capture_scan_rate = None
        self._capture_complete = False
        self.directory = None
        if failure:
            raise failure
        return directory

    def _write_metadata(self) -> None:
        if self._metadata_path is None:
            return
        with self._metadata_path.open("w", encoding="utf-8") as output:
            json.dump(
                self._metadata, output, ensure_ascii=False, indent=2, allow_nan=False
            )
