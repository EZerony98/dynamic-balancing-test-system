"""Export independent balancing and laser datasets."""

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict

import numpy as np

from models.data import BalancingChunk, LaserAnalysis, LaserChunk, PiezoChunk
from models.piezo_balance import PiezoSpectrumAnalysis


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
    ) -> Dict[str, Path]:
        """Export both voltages and both spectra with explicit source identity."""
        safe_product_id = re.sub(r"[^0-9A-Za-z._-]+", "_", product_id.strip()).strip("._-")
        prefix = (safe_product_id + "_") if safe_product_id else ""
        timestamp = prefix + datetime.now().strftime("piezo_%Y%m%d_%H%M%S_%f")
        target = self.export_directory / timestamp
        target.mkdir(parents=True, exist_ok=True)
        raw_path = target / "piezo_left_right_raw.csv"
        fft_path = target / "piezo_left_right_fft.csv"
        with raw_path.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output)
            writer.writerow(("time_s", "sample_index", "left_voltage_v",
                             "right_voltage_v", "fio3_state"))
            for index, (left, right) in enumerate(zip(
                chunk.channels["left"], chunk.channels["right"]
            )):
                writer.writerow((index / chunk.scan_rate, chunk.start_sample + index,
                                 float(left), float(right),
                                 float(chunk.reference_state[index])
                                 if len(chunk.reference_state) > index else ""))
        with fft_path.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output)
            writer.writerow(("frequency_hz", "left_amplitude_v", "right_amplitude_v"))
            writer.writerows(zip(analysis.frequencies, analysis.amplitudes["left"],
                                 analysis.amplitudes["right"]))
        with (target / "measurement_metadata.json").open("w", encoding="utf-8") as output:
            json.dump(
                {
                    "schema_version": 1,
                    "record_type": "current_data_export",
                    "product_id": product_id.strip(),
                    "measurement_template": measurement_template.strip(),
                    "source": "real_labjack_t7",
                    "channels": {"left": "AIN0", "right": "AIN3", "reference": "FIO3"},
                    "sensors": sensors or {},
                    "raw_voltage_preserved": True,
                },
                output,
                ensure_ascii=False,
                indent=2,
            )
        return {"raw": raw_path, "spectrum": fft_path}

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
