"""Export independent balancing and laser datasets."""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Dict

import numpy as np

from models.data import BalancingChunk, LaserAnalysis, LaserChunk, PiezoChunk
from models.piezo_balance import PiezoSpectrumAnalysis
from services.export_dictionary import (
    SUMMARY_FIELDS,
    safe_product_filename,
    write_field_dictionary,
)
from services.sensor_export import (
    amplitude_engineering_columns,
    engineering_units,
    export_profiles,
    waveform_engineering_columns,
)


class ExportService:
    def __init__(self, export_directory: Path) -> None:
        self.export_directory = Path(export_directory)

    def export(
        self,
        balancing: BalancingChunk,
        laser: LaserChunk,
        analysis: LaserAnalysis,
    ) -> Dict[str, Path]:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self.export_directory / timestamp
        target.mkdir(parents=True, exist_ok=True)
        balancing_path = target / "balancing_device_outputs.csv"
        laser_path = target / "laser_raw_signal.csv"
        spectrum_path = target / "laser_spectrum.csv"
        self._write_balancing(balancing_path, balancing)
        self._write_laser(laser_path, laser)
        self._write_spectrum(spectrum_path, analysis)
        return {
            "balancing": balancing_path,
            "laser": laser_path,
            "spectrum": spectrum_path,
        }

    def export_balancing(self, balancing: BalancingChunk) -> Path:
        """Export the priority five-channel dataset without requiring laser data."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self.export_directory / timestamp
        target.mkdir(parents=True, exist_ok=True)
        balancing_path = target / "balancing_device_outputs.csv"
        self._write_balancing(balancing_path, balancing)
        return balancing_path

    def export_laser(
        self, laser: LaserChunk, analysis: LaserAnalysis
    ) -> Dict[str, Path]:
        """Export laser data without requiring a balancing-mode acquisition."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self.export_directory / timestamp
        target.mkdir(parents=True, exist_ok=True)
        laser_path = target / "laser_raw_signal.csv"
        spectrum_path = target / "laser_spectrum.csv"
        self._write_laser(laser_path, laser)
        self._write_spectrum(spectrum_path, analysis)
        return {"laser": laser_path, "spectrum": spectrum_path}

    def export_piezo(
        self,
        chunk: PiezoChunk,
        analysis: PiezoSpectrumAnalysis,
        product_id: str = "",
        measurement_template: str = "",
        sensors: dict = None,
        device: dict = None,
        summary: dict = None,
    ) -> Dict[str, Path]:
        """Export raw volts plus sensor-specific engineering-unit columns."""
        safe_product_id = safe_product_filename(product_id)
        prefix = safe_product_id + "_"
        timestamp = prefix + datetime.now().strftime("piezo_%Y%m%d_%H%M%S_%f")
        target = self.export_directory / timestamp
        target.mkdir(parents=True, exist_ok=True)
        raw_path = target / (prefix + "piezo_left_right_raw.csv")
        fft_path = target / (prefix + "piezo_left_right_fft.csv")
        summary_path = target / (prefix + "measurement_summary.csv")
        dictionary_path = target / (prefix + "field_dictionary.csv")
        profiles = export_profiles(sensors)
        waveform_headers = []
        spectrum_headers = []
        for side in ("left", "right"):
            waveform_headers.append("%s_voltage_v" % side)
            waveform_headers.extend(
                name for name, _value in waveform_engineering_columns(
                    side, profiles[side], 0.0
                )
            )
            spectrum_headers.append("%s_amplitude_v" % side)
            spectrum_headers.extend(
                name for name, _value in amplitude_engineering_columns(
                    side, profiles[side], 0.0
                )
            )
        with raw_path.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output)
            writer.writerow(("time_s", "sample_index", *waveform_headers,
                             "fio3_state"))
            for index, (left, right) in enumerate(zip(
                chunk.channels["left"], chunk.channels["right"]
            )):
                row = [index / chunk.scan_rate, chunk.start_sample + index]
                for side, value in (("left", left), ("right", right)):
                    row.append(float(value))
                    row.extend(
                        engineering for _name, engineering
                        in waveform_engineering_columns(
                            side, profiles[side], float(value)
                        )
                    )
                row.append(
                    float(chunk.reference_state[index])
                    if len(chunk.reference_state) > index else ""
                )
                writer.writerow(row)
        with fft_path.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output)
            writer.writerow(("frequency_hz", *spectrum_headers))
            for frequency, left, right in zip(
                analysis.frequencies,
                analysis.amplitudes["left"],
                analysis.amplitudes["right"],
            ):
                row = [float(frequency)]
                for side, value in (("left", left), ("right", right)):
                    row.append(float(value))
                    row.extend(
                        engineering for _name, engineering
                        in amplitude_engineering_columns(
                            side, profiles[side], float(value)
                        )
                    )
                writer.writerow(row)
        with summary_path.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output)
            writer.writerow(SUMMARY_FIELDS)
            if summary is not None:
                current = dict(summary)
                current["time_s"] = 0.0
                current.setdefault(
                    "sample_index",
                    chunk.start_sample + max(0, len(chunk.channels["left"]) - 1),
                )
                writer.writerow(current.get(field, "") for field in SUMMARY_FIELDS)
        write_field_dictionary(
            dictionary_path,
            profiles,
            {
                "raw": raw_path.name,
                "spectrum": fft_path.name,
                "summary": summary_path.name,
            },
        )
        device = dict(device or {})
        ni = dict(device.get("ni9234", {}))
        channels = dict(device.get("channels", {}))
        channel_sources = {}
        for side, profile in profiles.items():
            if profile.sensor_type == "accelerometer":
                channel_sources[side] = "NI-9234 %s/%s" % (
                    ni.get("module", "auto"),
                    ni.get("%s_channel" % side, "ai0" if side == "left" else "ai1"),
                )
            else:
                channel_sources[side] = channels.get(
                    "piezo_%s" % side, "AIN0" if side == "left" else "AIN3"
                )
        with (target / "measurement_metadata.json").open("w", encoding="utf-8") as output:
            json.dump(
                {
                    "schema_version": 1,
                    "record_type": "current_data_export",
                    "product_id": product_id.strip(),
                    "measurement_template": measurement_template.strip(),
                    "source": chunk.acquisition_backend,
                    "channels": {
                        **channel_sources,
                        "reference": channels.get("piezo_reference", "FIO3"),
                    },
                    "sensors": sensors or {},
                    "engineering_units": engineering_units(profiles),
                    "scan_rate_hz": chunk.scan_rate,
                    "channels_synchronized": chunk.channels_synchronized,
                    "reference_synchronized": chunk.reference_synchronized,
                    "raw_voltage_preserved": True,
                    "unit_note": (
                        "*_voltage_v/*_amplitude_v 保留传感器电压；"
                        "加速度侧同时给出 g、m/s^2，压电侧同时给出 mV"
                    ),
                    "files": {
                        "raw": raw_path.name,
                        "spectrum": fft_path.name,
                        "summary": summary_path.name,
                        "field_dictionary": dictionary_path.name,
                    },
                },
                output,
                ensure_ascii=False,
                indent=2,
            )
        return {
            "raw": raw_path,
            "spectrum": fft_path,
            "summary": summary_path,
            "field_dictionary": dictionary_path,
        }

    @staticmethod
    def _write_balancing(path: Path, chunk: BalancingChunk) -> None:
        names = ("speed", "position_a", "position_b", "force_a", "force_b")
        count = min(len(chunk.channels[name]) for name in names)
        with path.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output)
            writer.writerow(("time_s",) + names)
            for index in range(count):
                writer.writerow(
                    (index / chunk.scan_rate,)
                    + tuple(float(chunk.channels[name][index]) for name in names)
                )

    @staticmethod
    def _write_laser(path: Path, chunk: LaserChunk) -> None:
        with path.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output)
            writer.writerow(("time_s", "laser_voltage_v"))
            for index, value in enumerate(chunk.values):
                writer.writerow((index / chunk.scan_rate, float(value)))

    @staticmethod
    def _write_spectrum(path: Path, analysis: LaserAnalysis) -> None:
        with path.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output)
            writer.writerow(("frequency_hz", "amplitude_v"))
            writer.writerows(
                zip(
                    np.asarray(analysis.frequencies, dtype=float),
                    np.asarray(analysis.amplitudes, dtype=float),
                )
            )
