"""Band-selected candidate 1X and amplitude-only single-plane trial calibration.

The frequency search is NOT proof that a peak is rotor 1X. Calibration requires
operator confirmation. No shaft-referenced angle or two-plane result is emitted.
"""

from collections import deque
from copy import deepcopy
from dataclasses import asdict
import time
import json
from pathlib import Path
from typing import Dict, Optional

import numpy as np

from models.data import LaserChunk
from models.laser_balance import BalanceMeasurement, BalanceSettings, PEAK_SELECTION
from services.spectrum import extract_target_tone


class LaserBalanceAnalyzer:
    """Analyze non-overlapping time windows; never close gaps by deleting samples."""

    def __init__(self, settings: BalanceSettings, input_range: float = 10.0) -> None:
        settings.validate()
        self.settings = settings
        self.input_range = float(input_range)
        self.reset()

    def reset(self) -> None:
        self._values = np.array([], dtype=float)
        self._rpms = np.array([], dtype=float)
        self._next_sample = 0
        self._scan_rate = 0.0

    def _invalid(self, chunk: LaserChunk, reason: str) -> BalanceMeasurement:
        self._values = np.array([], dtype=float)
        self._rpms = np.array([], dtype=float)
        return BalanceMeasurement(False, reason, chunk.timestamp, self._next_sample,
                                  self._next_sample, chunk.scan_rate, self.settings.signature)

    def update(self, chunk: LaserChunk, hall_hz: float) -> Optional[BalanceMeasurement]:
        values = np.asarray(chunk.values, dtype=float)
        self._next_sample += len(values)
        if self.settings.analysis_mode != "search":
            return self._invalid(chunk, "手动指定频率仅观察，不用于不平衡量标定")
        if (not np.isfinite(chunk.scan_rate) or chunk.scan_rate <= 0
                or self.settings.search_max_hz >= chunk.scan_rate / 2):
            return self._invalid(chunk, "频率范围超过当前采样率的奈奎斯特频率")
        if self._scan_rate and self._scan_rate != chunk.scan_rate:
            self._scan_rate = chunk.scan_rate
            return self._invalid(chunk, "采样率变化，重新建立分析窗")
        self._scan_rate = chunk.scan_rate
        if chunk.dropped_samples or not np.all(np.isfinite(values)) or np.any(values == -9999.0):
            return self._invalid(chunk, "丢样/无效样本，本窗作废")
        if np.any(np.abs(values) >= self.input_range * 0.98):
            return self._invalid(chunk, "输入接近采集卡量程，检查饱和/激光输出量程")
        settings = self.settings
        rpm = settings.reference_rpm_from_pulse(hall_hz)
        if not np.isfinite(rpm) or rpm <= 0:
            return self._invalid(chunk, "无有效%s参考，不能确认候选 1X" % settings.pulse_label)
        self._values = np.concatenate((self._values, values))
        self._rpms = np.concatenate((self._rpms, np.full(len(values), rpm)))
        count = int(round(settings.window_seconds * chunk.scan_rate))
        if len(self._values) < count:
            return None
        # A stream read is normally 50 ms. If a future driver returns a whole
        # larger window, use the latest complete contiguous window once.
        samples = self._values[-count:]
        rpms = self._rpms[-count:]
        self._values = np.array([], dtype=float)
        self._rpms = np.array([], dtype=float)
        measurement = BalanceMeasurement(
            False, "等待分析", chunk.timestamp, self._next_sample - count,
            self._next_sample, chunk.scan_rate, settings.signature,
            reference_rpm=float(np.mean(rpms)),
            rpm_variation=float(np.ptp(rpms) / np.mean(rpms)),
        )
        centered = samples - np.mean(samples)
        weights = np.hanning(count)
        fft = 2.0 * np.abs(np.fft.rfft(centered * weights)) / np.sum(weights)
        frequencies = np.fft.rfftfreq(count, 1.0 / chunk.scan_rate)
        candidates = np.flatnonzero(
            (frequencies >= settings.search_min_hz) & (frequencies <= settings.search_max_hz)
        )
        if len(candidates) < 5:
            measurement.reason = "搜索范围过窄，请扩大范围或延长分析窗"
            return measurement
        resolution = chunk.scan_rate / count
        reference_hz = measurement.reference_rpm / 60.0
        local_peaks = [index for index in candidates
                       if candidates[0] < index < candidates[-1] and fft[index] > fft[index - 1]
                       and fft[index] >= fft[index + 1]]
        eligible = []
        observed = []
        has_significant_peak = False
        band_noise = max(float(np.median(fft[candidates])), 1e-12)
        for index in local_peaks:
            # Keep real local peaks for diagnostic display even when they do
            # not qualify for calibration. Never create a tone at an arbitrary
            # reference frequency when no peak was observed.
            if fft[index] <= 1e-12:
                continue
            # Interpolate each peak before comparing its distance
            # from the reference. Equal distances prefer the larger amplitude.
            frequency = float(frequencies[index])
            log_values = np.log(np.maximum(fft[index - 1:index + 2], 1e-30))
            denominator = log_values[0] - 2 * log_values[1] + log_values[2]
            if abs(denominator) > 1e-15:
                frequency += float(np.clip(0.5 * (log_values[0] - log_values[2]) / denominator,
                                           -0.5, 0.5)) * resolution
            distance = abs(frequency - reference_hz)
            observed.append((distance, -fft[index], index, frequency))
            if fft[index] / band_noise < settings.minimum_snr:
                continue
            has_significant_peak = True
            if distance / reference_hz <= settings.reference_tolerance:
                eligible.append((distance, -fft[index], index, frequency))
        failures = []
        if not eligible:
            strongest = int(candidates[np.argmax(fft[candidates])])
            if (not has_significant_peak and strongest in (candidates[0], candidates[-1])
                    and fft[strongest] > 1e-12):
                failures.append("主峰在搜索边界，请调整频率范围")
            if not observed:
                measurement.reason = ("；".join(failures) if failures else
                                      "搜索频带内无可提取的局部峰，请检查信号或搜索范围")
                return measurement
        # Qualified selection remains unchanged. Only its display fallback is
        # relaxed; fallback results can NEVER count as valid calibration windows.
        _, _, peak, frequency = min(eligible or observed)
        neighbors = candidates[np.abs(candidates - peak) > 3]
        noise = max(float(np.median(fft[neighbors])), 1e-12) if len(neighbors) else band_noise
        # Refine the peak using the already-windowed FFT's coherent projection.
        # Only 13 scalar evaluations, with no change to acquisition sample rate.
        axis = np.arange(count, dtype=float) / chunk.scan_rate
        weighted = centered * weights
        def score(freq: float) -> float:
            return float(abs(np.sum(weighted * np.exp(-2j * np.pi * freq * axis))))
        left = max(settings.search_min_hz, frequency - resolution * 0.35)
        right = min(settings.search_max_hz, frequency + resolution * 0.35)
        for _ in range(6):
            one = left + (right - left) / 3
            two = right - (right - left) / 3
            if score(one) < score(two):
                left = one
            else:
                right = two
        frequency = (left + right) / 2
        tone = extract_target_tone(samples, chunk.scan_rate, frequency,
                                   start_sample_index=measurement.start_sample)
        measurement.frequency_hz = frequency
        measurement.amplitude_v = tone.amplitude
        measurement.noise_v = noise
        measurement.snr = min(tone.amplitude / noise, 1e12)
        measurement.tone = tone
        if measurement.rpm_variation > settings.rpm_variation_limit:
            failures.append("参考转速未稳定（波动 %.2f%% > %.2f%%）" % (
                measurement.rpm_variation * 100, settings.rpm_variation_limit * 100))
        if abs(frequency / reference_hz - 1) > settings.reference_tolerance:
            failures.append("峰频偏离参考过大（%.2f%% > %.2f%%）" % (
                abs(frequency / reference_hz - 1) * 100, settings.reference_tolerance * 100))
        if fft[peak] / band_noise < settings.minimum_snr or measurement.snr < settings.minimum_snr:
            failures.append("信噪比不足（需 ≥ %.1f）" % settings.minimum_snr)
        if not eligible and not failures:
            failures.append("寻峰初筛未通过")
        if failures:
            measurement.reason = "；".join(failures) + "；仅诊断预览，不用于标定"
            return measurement
        measurement.valid = True
        measurement.reason = ("已选最接近光电转频的有效峰（候选 1X；转子同步相位未建立）"
                              if settings.reference == "photoelectric" else
                              "已选最接近参考转频的有效峰（候选 1X；尚未光电确认）")
        return measurement


STAGES = ("baseline", "trial_0", "trial_120", "trial_240")


class FourRunBalanceSession:
    """Amplitude-only single-plane calibration, valid at one setup and speed."""

    def __init__(self, settings: BalanceSettings) -> None:
        self.settings = settings
        self.windows = deque(maxlen=3)
        self.runs: Dict[str, dict] = {}
        self.calibration: Optional[dict] = None
        self.run_id = ""
        self.last_update = 0.0

    def start_run(self, run_id: str) -> None:
        self.run_id = run_id
        self.windows.clear()
        self.last_update = 0.0

    def update(self, measurement: BalanceMeasurement) -> None:
        self.last_update = time.monotonic()
        if not measurement.valid or measurement.settings_signature != self.settings.signature:
            self.windows.clear()
            return
        if self.windows and measurement.start_sample < self.windows[-1].end_sample:
            self.windows.clear()
            return
        self.windows.append(measurement)

    def summary(self, require_fresh: bool = True) -> dict:
        if len(self.windows) < 3:
            raise ValueError("需连续 3 个有效、不重叠分析窗（默认约 6 秒）")
        if require_fresh and time.monotonic() - self.last_update > self.settings.window_seconds + 1:
            raise ValueError("测量已过期，请重新采集")
        amplitudes = np.array([item.amplitude_v for item in self.windows])
        mean = float(np.mean(amplitudes))
        std = float(np.std(amplitudes, ddof=1))
        cv = std / max(mean, 1e-12)
        frequencies = np.array([item.frequency_hz for item in self.windows])
        if cv > self.settings.amplitude_cv_limit:
            raise ValueError("连续分析窗幅值波动过大，不能记录/换算")
        if np.ptp(frequencies) / np.mean(frequencies) > self.settings.rpm_variation_limit:
            raise ValueError("连续分析窗候选转频未稳定")
        return {"amplitude_v": mean, "std_v": std, "cv": cv,
                "frequency_hz": float(np.mean(frequencies)),
                "noise_v": max(item.noise_v for item in self.windows),
                "run_id": self.run_id, "timestamp": self.windows[-1].timestamp,
                "windows": [item.record() for item in self.windows]}

    def capture(self, stage: str, mass_g: float, radius_mm: float) -> dict:
        if stage not in STAGES:
            raise ValueError("当前不是基线/试重工况")
        if not self.settings.rotor_id.strip():
            raise ValueError("请填写转子与测点标识，用于限定标定工况")
        if not all(np.isfinite(value) and value > 0 for value in (mass_g, radius_mm)):
            raise ValueError("请填写同一试重的质量(g)及半径(mm)")
        if any(run["run_id"] == self.run_id for run in self.runs.values()):
            raise ValueError("同一运行不能重复记录不同试重；停采集并实际停机后换工况")
        result = self.summary()
        result.update(stage=stage, mass_g=float(mass_g), radius_mm=float(radius_mm),
                      settings_signature=self.settings.signature)
        for other in self.runs.values():
            if other["mass_g"] != mass_g or other["radius_mm"] != radius_mm:
                raise ValueError("四次运行必须使用同一试重质量与半径，请清空旧标定")
            if abs(result["frequency_hz"] / other["frequency_hz"] - 1) > self.settings.calibration_speed_tolerance:
                raise ValueError("与已有工况转频不一致，请恢复标定转速")
        self.runs[stage] = result
        self.calibration = None
        return result

    def solve(self) -> dict:
        self.calibration = None
        if set(self.runs) != set(STAGES):
            raise ValueError("需完成基线、试重 0°、120°、240° 四个独立工况")
        runs = [self.runs[key] for key in STAGES]
        self._validate_runs(runs)
        values = np.array([run["amplitude_v"] for run in runs])
        deviations = np.array([run["std_v"] for run in runs])
        baseline = values[0]
        response_squared = float(np.mean(values[1:] ** 2) - baseline ** 2)
        uncertainty = float(np.sqrt((2 * baseline * deviations[0]) ** 2
                                    + np.sum((2 * values[1:] * deviations[1:] / 3) ** 2)))
        floor = max(run["noise_v"] for run in runs)
        if response_squared <= max(3 * uncertainty, (5 * floor) ** 2, 1e-24):
            raise ValueError("试重响应不足或数据不一致，无法可靠标定")
        response = float(np.sqrt(response_squared))
        angles = np.deg2rad([0.0, 120.0, 240.0])
        differences = values[1:] ** 2 - baseline ** 2 - response_squared
        reconstructed = abs(np.sum(differences * np.exp(1j * angles)) / (3 * response))
        residual = float(abs(reconstructed - baseline) / max(baseline, response, 1e-12))
        if residual > 0.10:
            raise ValueError("四次幅值不符合单面线性模型（残差 > 10%），检查角度/转速/装夹")
        trial_gmm = runs[0]["mass_g"] * runs[0]["radius_mm"]
        coefficient = response / trial_gmm
        self.calibration = {
            "method": "single_plane_four_run_amplitude_only",
            "created_at": time.time(), "settings_signature": self.settings.signature,
            "coefficient_v_per_gmm": coefficient,
            "reference_frequency_hz": float(np.mean([run["frequency_hz"] for run in runs])),
            "trial_gmm": trial_gmm, "trial_mass_g": runs[0]["mass_g"],
            "trial_radius_mm": runs[0]["radius_mm"],
            "baseline_equivalent_gmm": baseline / coefficient,
            "model_residual": residual,
        }
        return self.calibration

    def _validate_runs(self, runs: list) -> None:
        ids = set()
        for run in runs:
            numeric = [float(run[key]) for key in
                       ("amplitude_v", "std_v", "frequency_hz", "noise_v", "mass_g", "radius_mm")]
            if (not all(np.isfinite(value) and value >= 0 for value in numeric)
                    or any(float(run[key]) <= 0 for key in ("frequency_hz", "noise_v", "mass_g", "radius_mm"))):
                raise ValueError("标定记录含无效数字")
            if run["settings_signature"] != self.settings.signature:
                raise ValueError("标定记录与当前参数或选峰方法不同，请重新标定")
            if not run["run_id"] or run["run_id"] in ids:
                raise ValueError("标定必须来自四个独立运行")
            ids.add(run["run_id"])
            if run["std_v"] / max(run["amplitude_v"], 1e-12) > self.settings.amplitude_cv_limit:
                raise ValueError("标定记录幅值波动超限")
            if run["mass_g"] != runs[0]["mass_g"] or run["radius_mm"] != runs[0]["radius_mm"]:
                raise ValueError("标定记录的试重质量/半径不一致")
        frequencies = [run["frequency_hz"] for run in runs]
        if max(frequencies) / min(frequencies) - 1 > self.settings.calibration_speed_tolerance:
            raise ValueError("四次标定转频不一致")

    @classmethod
    def load_report(cls, path: Path, settings: BalanceSettings) -> "FourRunBalanceSession":
        if path.stat().st_size > 5_000_000:
            raise ValueError("标定报告过大，不是支持的报告格式")
        with path.open("r", encoding="utf-8") as handle:
            report = json.load(handle)
        if not isinstance(report, dict) or report.get("schema_version") != 1:
            raise ValueError("不支持的标定报告版本")
        if not isinstance(report.get("settings"), dict):
            raise ValueError("报告缺少有效测量参数")
        if BalanceSettings.from_dict(report["settings"]) != settings:
            raise ValueError("报告参数与当前测量参数不同，请先核对转子标识、搜索范围、PPR、激光输出等设置")
        restored = cls(settings)
        restored.runs = report["runs"]
        if not isinstance(restored.runs, dict):
            raise ValueError("标定运行记录格式错误")
        # Recompute from original measurements; never trust a saved coefficient.
        restored.solve()
        return restored

    def estimate(self) -> dict:
        if self.calibration is None:
            raise ValueError("未完成四次试重标定")
        summary = self.summary()
        calibration = self.calibration
        if abs(summary["frequency_hz"] / calibration["reference_frequency_hz"] - 1) > self.settings.calibration_speed_tolerance:
            raise ValueError("当前转速偏离标定转速，禁止沿用系数")
        if self.run_id in {run["run_id"] for run in self.runs.values()}:
            raise ValueError("请移除试重，在新运行中复测")
        coefficient = calibration["coefficient_v_per_gmm"]
        return {"equivalent_unbalance_gmm": summary["amplitude_v"] / coefficient,
                "repeatability_std_gmm": summary["std_v"] / coefficient,
                "amplitude_v": summary["amplitude_v"],
                "frequency_hz": summary["frequency_hz"],
                "note": "单面等效值；不提供双面分解或配重角度"}

    def report(self) -> dict:
        return {"schema_version": 1, "peak_selection": PEAK_SELECTION,
                "settings": asdict(self.settings),
                "runs": deepcopy(self.runs), "calibration": deepcopy(self.calibration),
                "limitations": "单测点、单面等效；临时转速参考；不输出双面量/配重角度"}
