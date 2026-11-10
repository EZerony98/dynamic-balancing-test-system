"""Synchronous two-channel voltage-domain 1X analysis with optical speed reference."""

from typing import Mapping, Optional

import numpy as np

from models.data import PiezoChunk
from models.piezo_balance import Piezo1XResult, PiezoSettings, PiezoSideResult, SIDES
from models.sensors import SensorProfile, sensor_profiles_from_config
from services.spectrum import extract_target_tone


class DualPiezoAnalyzer:
    """Voltage 1X vectors referenced to a sample-aligned FIO3 optical edge.

    The synchronized angles are electrical response angles at the measured
    optical zero.  They are deliberately kept separate from a calibrated
    correction angle and from an unbalance value in g·mm.
    """

    def __init__(
        self,
        settings: PiezoSettings,
        input_range: float = 10.0,
        sensor_profiles: Optional[Mapping[str, object]] = None,
    ) -> None:
        settings.validate()
        self.settings = settings
        self.input_range = float(input_range)
        self.sensor_profiles = sensor_profiles_from_config(sensor_profiles)
        self.active_sides = tuple(
            side for side in SIDES if self.sensor_profiles[side].enabled
        )
        self.reset()

    def reset(self) -> None:
        self._buffers = {side: np.array([], dtype=float) for side in SIDES}
        self._reference_hz = np.array([], dtype=float)
        self._reference_held = np.array([], dtype=bool)
        self._reference_state = np.array([], dtype=float)
        self._next_sample: Optional[int] = None
        self._scan_rate = 0.0
        self._last_good_reference_hz: Optional[float] = None
        self._bad_reference_updates = 0
        self._pending_reference_hz = []
        self._repaired_saturation_samples = 0
        self._channels_synchronized = True
        self._reference_synchronized = True

    def _clear_window(self) -> None:
        """Discard mixed-speed waveform data while retaining tracker state."""
        self._buffers = {side: np.array([], dtype=float) for side in SIDES}
        self._reference_hz = np.array([], dtype=float)
        self._reference_held = np.array([], dtype=bool)
        self._reference_state = np.array([], dtype=float)
        self._repaired_saturation_samples = 0
        self._channels_synchronized = True
        self._reference_synchronized = True

    @staticmethod
    def _longest_true_run(mask: np.ndarray) -> int:
        """Return the longest consecutive run in a boolean mask."""
        indices = np.flatnonzero(mask)
        if not len(indices):
            return 0
        breaks = np.flatnonzero(np.diff(indices) > 1)
        starts = np.r_[0, breaks + 1]
        ends = np.r_[breaks, len(indices) - 1]
        return int(np.max(indices[ends] - indices[starts] + 1))

    def _filter_reference_jump(self, candidate_hz: float) -> tuple:
        """Hold isolated jumps; accept a stable new speed after confirmation."""
        stable = self._last_good_reference_hz
        if stable is None or stable <= 0:
            self._last_good_reference_hz = candidate_hz
            self._pending_reference_hz = []
            return candidate_hz, False, False
        if abs(candidate_hz - stable) / stable <= self.settings.reference_jump_limit:
            self._last_good_reference_hz = candidate_hz
            self._pending_reference_hz = []
            return candidate_hz, False, False
        if self._pending_reference_hz:
            pending_center = float(np.median(self._pending_reference_hz))
            if abs(candidate_hz - pending_center) / pending_center <= self.settings.reference_jump_limit:
                self._pending_reference_hz.append(candidate_hz)
            else:
                self._pending_reference_hz = [candidate_hz]
        else:
            self._pending_reference_hz = [candidate_hz]
        if len(self._pending_reference_hz) < self.settings.reference_change_confirm_updates:
            return stable, True, False
        confirmed = float(np.median(self._pending_reference_hz))
        self._last_good_reference_hz = confirmed
        self._pending_reference_hz = []
        return confirmed, False, True

    def _invalid(self, reason: str) -> Piezo1XResult:
        self.reset()
        sides = {
            side: PiezoSideResult(
                reason=reason if side in self.active_sides else "本侧传感器未安装"
            )
            for side in SIDES
        }
        return Piezo1XResult(status=reason, left=sides["left"], right=sides["right"])

    @staticmethod
    def _falling_edges(
        reference_state: np.ndarray, rate: float, expected_hz: float
    ) -> np.ndarray:
        """Return debounced high-to-low edge sample indices."""
        high = np.asarray(reference_state, dtype=float) >= 0.5
        edges = np.flatnonzero(high[:-1] & ~high[1:]) + 1
        if len(edges) < 2 or rate <= 0 or expected_hz <= 0:
            return edges.astype(int)
        minimum_separation = max(1, int(rate / expected_hz * 0.5))
        accepted = [int(edges[0])]
        for edge in edges[1:]:
            if int(edge) - accepted[-1] >= minimum_separation:
                accepted.append(int(edge))
        return np.asarray(accepted, dtype=int)

    def _shaft_phase(
        self, samples: np.ndarray, edge_samples: np.ndarray, rate: float,
        channel_delay_us: float, offset_degrees: float,
    ) -> float:
        """Fit the 1X response against angle interpolated between FIO3 edges.

        This is tachometer/order synchronous rather than a fixed-frequency
        extrapolation. Small speed changes therefore do not accumulate into a
        false phase drift over the two-second analysis window.
        """
        edge_times = (
            (edge_samples.astype(float) - 0.5) / rate
            + self.settings.reference_channel_delay_us * 1e-6
        )
        sample_times = (
            np.arange(len(samples), dtype=float) / rate
            + channel_delay_us * 1e-6
        )
        usable = (
            (sample_times >= edge_times[0]) &
            (sample_times <= edge_times[-1])
        )
        if np.count_nonzero(usable) < 8:
            raise ValueError("同步边沿之间的压电样本不足")
        shaft_angle = np.interp(
            sample_times[usable], edge_times,
            np.arange(len(edge_times), dtype=float) * 2.0 * np.pi,
        )
        values = np.asarray(samples, dtype=float)[usable]
        weights = np.hanning(len(values))
        if not np.any(weights):
            weights = np.ones(len(values))
        design = np.column_stack((
            np.cos(shaft_angle), np.sin(shaft_angle), np.ones(len(values)),
        ))
        weighted_design = design * np.sqrt(weights)[:, None]
        weighted_values = values * np.sqrt(weights)
        coefficients, _, _, _ = np.linalg.lstsq(
            weighted_design, weighted_values, rcond=None
        )
        phase = float(np.rad2deg(np.arctan2(
            -coefficients[1], coefficients[0]
        )) % 360.0)
        return float((self.settings.angle_direction * phase + offset_degrees) % 360.0)

    def update(self, chunk: PiezoChunk, optical_hz: float) -> Optional[Piezo1XResult]:
        if self.settings.analysis_mode != "search":
            return self._invalid("手动频率只供观察，不判定转子 1X")
        rate = float(chunk.scan_rate)
        if not np.isfinite(rate) or rate <= 0 or self.settings.search_max_hz >= rate / 2:
            return self._invalid("1X 搜索上限超过当前采样率允许范围")
        if self._scan_rate and self._scan_rate != rate:
            return self._invalid("采样率变化，重新建立分析窗")
        self._scan_rate = rate
        try:
            channels = {
                side: self.sensor_profiles[side].analysis_samples(chunk.channels[side])
                for side in self.active_sides
            }
        except KeyError:
            return self._invalid("缺少已启用传感器的采集通道")
        count = len(channels[self.active_sides[0]])
        if not count or any(len(values) != count for values in channels.values()):
            return self._invalid("已启用传感器的数据长度不一致")
        reference_state = np.asarray(chunk.reference_state, dtype=float)
        if len(reference_state) not in (0, count):
            return self._invalid("同步 FIO3 参考与压电数据长度不一致")
        if self._next_sample is not None and chunk.start_sample != self._next_sample:
            self.reset()
        self._next_sample = chunk.start_sample + count
        if (chunk.dropped_samples or any(not np.all(np.isfinite(values)) or
            np.any(values == -9999.0) for values in channels.values())):
            return self._invalid("丢样或无效数据，本分析窗作废")
        if len(reference_state) and (
            not np.all(np.isfinite(reference_state))
            or np.any(reference_state == -9999.0)
        ):
            return self._invalid("同步 FIO3 参考丢样或无效，本分析窗作废")
        # A single EMI/contact spike near the AIN range must not erase an
        # otherwise clean two-second result. Repair only a very small number
        # of isolated samples; sustained clipping remains a hard failure.
        repaired_in_chunk = 0
        for side in self.active_sides:
            values = channels[side]
            saturated = np.abs(values) >= self.input_range * 0.98
            saturated_count = int(np.count_nonzero(saturated))
            if not saturated_count:
                continue
            allowed = max(
                1,
                int(np.ceil(
                    count * self.settings.saturation_spike_fraction_limit
                )),
            )
            longest_run = self._longest_true_run(saturated)
            if (count < 100 or saturated_count > allowed or
                    longest_run > self.settings.saturation_run_limit_samples):
                return self._invalid(
                    "%s传感器输出连续或过多接近采集卡量程"
                    "（|V|max %.3f V，%d点，最长连续%d点），检查真实饱和" % (
                        "左" if side == "left" else "右",
                        float(np.max(np.abs(values))), saturated_count,
                        longest_run,
                    )
                )
            valid_indices = np.flatnonzero(~saturated)
            if len(valid_indices) < 2:
                return self._invalid("传感器输出饱和，无法修复")
            repaired = values.copy()
            bad_indices = np.flatnonzero(saturated)
            repaired[bad_indices] = np.interp(
                bad_indices, valid_indices, values[valid_indices]
            )
            channels[side] = repaired
            repaired_in_chunk += saturated_count
        snapshot_reference_hz = (
            float(optical_hz) / self.settings.ppr
            if np.isfinite(optical_hz) and optical_hz > 0 else 0.0
        )
        snapshot_is_good = (
            self.settings.search_min_hz <= snapshot_reference_hz <=
            self.settings.search_max_hz
        )
        # In synchronized mode, the state sampled in the same stream as AIN0
        # and AIN3 is the authoritative speed/phase reference.  The old
        # command-response DIO-EF reading is retained only as a startup and
        # diagnostic fallback.  A normal 50 ms chunk contains several edges
        # in the intended 120-200 Hz operating band.
        synchronous_chunk_hz = 0.0
        if len(reference_state):
            expected_hz = (
                snapshot_reference_hz if snapshot_is_good else
                self._last_good_reference_hz if self._last_good_reference_hz else
                (self.settings.search_min_hz + self.settings.search_max_hz) / 2.0
            )
            chunk_edges = self._falling_edges(reference_state, rate, expected_hz)
            if len(chunk_edges) >= 2:
                chunk_period_hz = rate / np.diff(chunk_edges)
                in_range = chunk_period_hz[
                    (chunk_period_hz >= self.settings.search_min_hz) &
                    (chunk_period_hz <= self.settings.search_max_hz)
                ]
                if len(in_range):
                    synchronous_chunk_hz = float(np.median(in_range))
        candidate_reference_hz = (
            synchronous_chunk_hz if synchronous_chunk_hz > 0 else
            snapshot_reference_hz
        )
        reference_is_good = (
            self.settings.search_min_hz <= candidate_reference_hz <=
            self.settings.search_max_hz
        )
        reference_held = False
        if reference_is_good:
            self._bad_reference_updates = 0
            reference_hz, reference_held, speed_changed = (
                self._filter_reference_jump(candidate_reference_hz)
            )
            if speed_changed:
                # Do not mix the old and newly confirmed operating speeds in
                # one FFT/phase window. Start the full window at this chunk.
                self._clear_window()
        else:
            self._bad_reference_updates += 1
            self._pending_reference_hz = []
            if (self._last_good_reference_hz is not None and
                    self._bad_reference_updates <=
                    self.settings.max_bad_reference_updates):
                # A single command-response glitch must not destroy an almost
                # complete analysis window. Keep the last good reference, but
                # mark the samples so the UI and quality gate can disclose it.
                reference_hz = self._last_good_reference_hz
                reference_held = True
            elif self._bad_reference_updates <= self.settings.max_bad_reference_updates:
                # No previous good edge exists yet. Do not attach unreferenced
                # waveform samples to a future analysis window.
                return None
            else:
                reason = ("FIO3 同步流与测频快照连续 %d 次均无有效转频，分析窗已作废"
                          if candidate_reference_hz <= 0 else
                          "FIO3 转频连续 %d 次超出 1X 搜索范围，分析窗已作废")
                return self._invalid(reason % self._bad_reference_updates)
        self._repaired_saturation_samples += repaired_in_chunk
        self._channels_synchronized = (
            self._channels_synchronized and bool(chunk.channels_synchronized)
        )
        self._reference_synchronized = (
            self._reference_synchronized and bool(chunk.reference_synchronized)
        )
        for side in self.active_sides:
            self._buffers[side] = np.concatenate((self._buffers[side], channels[side]))
        self._reference_hz = np.concatenate(
            (self._reference_hz, np.full(count, reference_hz, dtype=float))
        )
        self._reference_held = np.concatenate(
            (self._reference_held, np.full(count, reference_held, dtype=bool))
        )
        self._reference_state = np.concatenate((
            self._reference_state,
            reference_state if len(reference_state) else np.full(count, np.nan),
        ))
        required = int(round(self.settings.window_seconds * rate))
        if len(self._reference_hz) < required:
            return None
        samples = {side: self._buffers[side][-required:] for side in self.active_sides}
        references = self._reference_hz[-required:]
        held = self._reference_held[-required:]
        synchronous_state = self._reference_state[-required:]
        repaired_saturation_samples = self._repaired_saturation_samples
        channels_synchronized = self._channels_synchronized
        reference_synchronized = self._reference_synchronized
        start_sample = self._next_sample - required
        # Analysis windows never overlap and a dropped sample never gets
        # deleted to make the neighboring samples appear time-contiguous.
        self._buffers = {side: np.array([], dtype=float) for side in SIDES}
        self._reference_hz = np.array([], dtype=float)
        self._reference_held = np.array([], dtype=bool)
        self._reference_state = np.array([], dtype=float)
        self._repaired_saturation_samples = 0
        self._channels_synchronized = True
        self._reference_synchronized = True
        # One bad optical period must not invalidate an otherwise stable
        # two-second window.  Use the central 80% of snapshots for the speed
        # gate, while preserving genuine sustained drift or repeated false
        # edges as an invalid 1X reference.
        observed_references = references[~held]
        if not len(observed_references):
            observed_references = references
        low_hz, reference_hz, high_hz = (
            float(value) for value in np.quantile(
                observed_references, (0.1, 0.5, 0.9), method="nearest")
        )
        variation = (high_hz - low_hz) / reference_hz
        raw_variation = float(np.ptp(observed_references) / reference_hz)
        result = Piezo1XResult(reference_hz=reference_hz, rpm=reference_hz * 60,
                               rpm_variation=variation,
                               reference_min_hz=float(np.min(observed_references)),
                               reference_max_hz=float(np.max(observed_references)),
                               reference_held_fraction=float(np.mean(held)),
                               repaired_saturation_samples=repaired_saturation_samples)
        held_fraction = result.reference_held_fraction
        variation_valid = variation <= self.settings.rpm_variation_limit
        held_fraction_valid = held_fraction <= self.settings.max_reference_held_fraction
        reference_quality_valid = variation_valid and held_fraction_valid
        reference_problems = []
        if not variation_valid:
            reference_problems.append("持续转速波动 %.2f%% 超过 %.2f%%" % (
                variation * 100, self.settings.rpm_variation_limit * 100))
        if not held_fraction_valid:
            reference_problems.append("光电异常读数 %.1f%% 超过 %.1f%%" % (
                held_fraction * 100,
                self.settings.max_reference_held_fraction * 100))
        reference_problem = "；".join(reference_problems)
        synchronous_edges = np.array([], dtype=int)
        synchronous_reference_hz = 0.0
        synchronous_variation = 0.0
        synchronous_valid = False
        synchronous_notes = []
        has_synchronous_reference = (
            reference_synchronized and np.all(np.isfinite(synchronous_state))
        )
        if has_synchronous_reference:
            synchronous_edges = self._falling_edges(
                synchronous_state, rate, reference_hz
            )
            if len(synchronous_edges) >= 3:
                edge_frequencies = rate / np.diff(synchronous_edges)
                sync_low, synchronous_reference_hz, sync_high = (
                    float(value) for value in np.quantile(
                        edge_frequencies, (0.1, 0.5, 0.9), method="nearest"
                    )
                )
                synchronous_variation = (
                    (sync_high - sync_low) / synchronous_reference_hz
                    if synchronous_reference_hz > 0 else float("inf")
                )
                sync_in_range = (
                    self.settings.search_min_hz <= synchronous_reference_hz <=
                    self.settings.search_max_hz
                )
                sync_matches_snapshot = (
                    abs(synchronous_reference_hz - reference_hz) / reference_hz <=
                    self.settings.synchronous_frequency_tolerance
                )
                synchronous_valid = (
                    sync_in_range and
                    synchronous_variation <=
                    self.settings.synchronous_period_variation_limit and
                    sync_matches_snapshot
                )
                if not sync_matches_snapshot:
                    reference_problems.append(
                        "同步FIO3 %.3f Hz 与测频快照 %.3f Hz 不一致" %
                        (synchronous_reference_hz, reference_hz)
                    )
                if (synchronous_variation >
                        self.settings.synchronous_period_variation_limit):
                    reference_problems.append(
                        "同步FIO3周期波动 %.2f%% 超过 %.2f%%" % (
                            synchronous_variation * 100,
                            self.settings.synchronous_period_variation_limit * 100,
                        )
                    )
                elif synchronous_variation > self.settings.rpm_variation_limit:
                    synchronous_notes.append(
                        "同步FIO3周期波动 %.2f%%，已用逐圈角度跟踪补偿" %
                        (synchronous_variation * 100)
                    )
                if not sync_in_range:
                    reference_problems.append("同步FIO3转频不在 1X 搜索范围")
            else:
                reference_problems.append("同步FIO3下降沿不足")
        reference_problem = "；".join(reference_problems)
        # Spectral 1X validity and shaft-angle validity are deliberately
        # separate. A noisy edge train may withhold A/B synchronous angles,
        # but it must not demote a strong speed-referenced 1X peak to preview.
        analysis_reference_hz = (
            synchronous_reference_hz if synchronous_reference_hz > 0 else reference_hz
        )
        result.synchronous_reference_hz = synchronous_reference_hz
        result.synchronous_period_variation = synchronous_variation
        result.reference_edge_count = int(len(synchronous_edges))
        if synchronous_reference_hz > 0:
            result.rpm = synchronous_reference_hz * 60.0
        frequencies = np.fft.rfftfreq(required, 1.0 / rate)
        band = np.flatnonzero((frequencies >= self.settings.search_min_hz) &
                             (frequencies <= self.settings.search_max_hz) &
                             (np.abs(frequencies - analysis_reference_hz) <=
                              analysis_reference_hz * self.settings.reference_tolerance))
        if len(band) < 5:
            result.status = "1X 候选频带太窄，请扩大范围或延长分析窗"
            return result
        window = np.hanning(required)
        gain = max(float(np.sum(window)), 1e-12)
        spectra = {side: 2 * np.abs(np.fft.rfft(
            (samples[side] - np.mean(samples[side])) * window)) / gain
            for side in self.active_sides}
        combined = np.sqrt(np.sum(
            np.stack([np.square(spectra[side]) for side in self.active_sides]),
            axis=0,
        ))
        peaks = [index for index in band if 0 < index < len(combined) - 1
                 and combined[index] > combined[index - 1]
                 and combined[index] >= combined[index + 1]]
        # Prefer the closest *credible* joint peak, not a strong unrelated
        # motor harmonic elsewhere in the visible FFT.  When the 1X response
        # is broad or weak enough that it is not a strict local maximum, keep
        # the old tachometer-locked behaviour: fit the component directly at
        # the optical 1X frequency.  Quality gates still decide whether that
        # component is valid or only a diagnostic preview, but the value no
        # longer disappears from the UI merely because peak picking failed.
        def side_snr(side: str, peak: int) -> float:
            others = band[np.abs(band - peak) > 3]
            floor = max(float(np.median(spectra[side][others])), 1e-12) if len(others) else 1e-12
            return float(spectra[side][peak] / floor)
        eligible = [index for index in peaks if any(
            side_snr(side, index) >= self.settings.minimum_snr
            for side in self.active_sides)]
        reference_locked_fallback = not eligible
        if eligible:
            peak = min(eligible, key=lambda index: (
                abs(frequencies[index] - analysis_reference_hz), -combined[index]))
            frequency = float(frequencies[peak])
            # Log-parabola interpolation improves frequency precision without
            # requiring a much longer FFT. Both channels use the same frequency.
            local = np.log(np.maximum(combined[peak - 1:peak + 2], 1e-30))
            curvature = local[0] - 2 * local[1] + local[2]
            if abs(curvature) > 1e-15:
                frequency += float(np.clip(
                    0.5 * (local[0] - local[2]) / curvature, -0.5, 0.5
                )) * rate / required
        else:
            # Optical speed is the physical definition of 1X.  Direct order
            # extraction is therefore a safer fallback than choosing an
            # unrelated stronger harmonic.  It is never promoted to valid
            # unless the exact fitted component also passes the SNR gate.
            peak = int(band[np.argmin(np.abs(
                frequencies[band] - analysis_reference_hz
            ))])
            frequency = float(analysis_reference_hz)
        result.frequency_hz = frequency
        for side in SIDES:
            if side not in self.active_sides:
                setattr(result, side, PiezoSideResult(reason="本侧传感器未安装"))
                continue
            tone = extract_target_tone(samples[side], rate, frequency,
                                       start_sample_index=start_sample)
            noise_bins = band[np.abs(band - peak) > 3]
            noise = (
                max(float(np.median(spectra[side][noise_bins])), 1e-12)
                if len(noise_bins) else 1e-12
            )
            # The exact-frequency least-squares amplitude is more reliable
            # than one FFT bin when the optical frequency lies between bins.
            snr = float(tone.amplitude / noise)
            signal_valid = snr >= self.settings.minimum_snr and tone.amplitude > 0
            valid = signal_valid and reference_quality_valid
            preview = tone.amplitude > 0 and not valid
            setattr(result, side, PiezoSideResult(
                valid=valid, preview=preview,
                reason=("候选 1X 有效" if valid else
                        "候选峰预览（光电质量不足）"
                        if preview and signal_valid else
                        "光电锁定 1X 预览（信噪比不足）" if preview else
                        "1X 信噪比不足"),
                amplitude_v=tone.amplitude if signal_valid or preview else 0.0,
                snr=snr, tone=tone if signal_valid or preview else None))
        result.preview_only = any(
            getattr(result, side).preview for side in self.active_sides
        ) and not any(getattr(result, side).valid for side in self.active_sides)
        if result.left.valid and result.right.valid and channels_synchronized:
            difference = result.right.tone.phase_degrees - result.left.tone.phase_degrees
            difference -= 360.0 * frequency * self.settings.interchannel_delay_us * 1e-6
            result.relative_phase_degrees = float((difference + 180) % 360 - 180)
        if synchronous_valid:
            if result.left.valid and result.left.tone is not None:
                result.left.shaft_phase_degrees = self._shaft_phase(
                    samples["left"], synchronous_edges, rate, 0.0,
                    self.settings.left_angle_offset_degrees,
                )
            if result.right.valid and result.right.tone is not None:
                result.right.shaft_phase_degrees = self._shaft_phase(
                    samples["right"], synchronous_edges, rate,
                    self.settings.interchannel_delay_us,
                    self.settings.right_angle_offset_degrees,
                )
            result.shaft_angle_synchronous = all(
                getattr(result, side).shaft_phase_degrees is not None
                for side in self.active_sides
            )
        if not channels_synchronized:
            reference_problems.append("左右来自 NI/LabJack 不同时钟，不输出左右相位")
            reference_problem = "；".join(reference_problems)
        if not reference_synchronized:
            reference_problems.append("NI 与 FIO3 未硬件同步，不输出转子绝对角")
            reference_problem = "；".join(reference_problems)
        if result.preview_only:
            problems = []
            if reference_locked_fallback:
                problems.append(
                    "未找到通过门限的局部峰，已按光电转频直接提取"
                )
            if reference_problem:
                problems.append(reference_problem)
            if not any(getattr(result, side).valid for side in self.active_sides):
                problems.append("当前仅作诊断预览，不用于动平衡标定")
            result.status = "候选峰预览（非有效 1X）；" + "；".join(problems)
        else:
            both_active_valid = (
                len(self.active_sides) == 2
                and result.left.valid and result.right.valid
            )
            result.status = ("双路 1X 幅值有效；左右来自不同时钟，相位已禁用"
                             if both_active_valid and not channels_synchronized else
                             "单侧同步 1X 有效；可用于当前传感器对比测试，不计算左右相对相位"
                             if len(self.active_sides) == 1 and result.shaft_angle_synchronous else
                             "双路同步 1X 有效；角度以 FIO3 下降沿为 0°，机械配重角仍需偏置/试重标定"
                             if result.shaft_angle_synchronous else
                             "双路候选 1X 有效；仅可比较左右电信号相位，未建立同步转子零位"
                             if result.relative_phase_degrees is not None else
                             "仅一侧候选 1X 有效；不能计算左右相对相位")
            if reference_locked_fallback:
                result.status += "；采用光电转频锁定提取"
        if reference_quality_valid and raw_variation > self.settings.rpm_variation_limit:
            result.status += "；短时测频尖峰已忽略（原始极差 %.1f%%）" % (
                raw_variation * 100)
        if result.reference_held_fraction > 0:
            result.status += "；异常跳变读数已滤波保持（%.1f%% 样本）" % (
                result.reference_held_fraction * 100)
        if (not result.preview_only and not result.shaft_angle_synchronous and
                reference_problem):
            result.status += "；同步角未输出：" + reference_problem
        if synchronous_notes:
            result.status += "；" + "；".join(synchronous_notes)
        if result.repaired_saturation_samples:
            result.status += "；本窗已插值修复 %d 个孤立饱和尖峰" % (
                result.repaired_saturation_samples
            )
        return result
