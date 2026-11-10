"""FFT, target-tone extraction and sampling-budget calculations."""

from typing import Tuple

import numpy as np

from models.data import Metrics, TargetToneAnalysis


WINDOWS = {
    "Rectangular": lambda size: np.ones(size),
    "Hann": np.hanning,
    "Hamming": np.hamming,
    "Blackman": np.blackman,
}


def spectrum(
    values: np.ndarray,
    scan_rate: float,
    fft_size: int = 4096,
    window: str = "Hann",
    remove_dc: bool = True,
) -> Tuple[np.ndarray, np.ndarray, Metrics]:
    """Return a normalized single-sided spectrum and raw-signal metrics."""
    x = np.asarray(values, dtype=float)
    if not np.all(np.isfinite(x)) or np.any(x == -9999.0):
        raise ValueError("FFT 分析窗含无效/丢失样本，不能删除样本后拼接时间轴")
    if not len(x):
        return np.array([]), np.array([]), Metrics()
    raw = x.copy()
    size = min(len(x), fft_size)
    x = x[-size:]
    if remove_dc:
        x = x - np.mean(x)
    window_values = WINDOWS.get(window, np.hanning)(size)
    gain = max(float(np.sum(window_values)), 1e-12)
    amplitudes = 2.0 * np.abs(np.fft.rfft(x * window_values)) / gain
    if len(amplitudes):
        amplitudes[0] *= 0.5
    frequencies = np.fft.rfftfreq(size, 1.0 / scan_rate)
    peak_index = int(np.argmax(amplitudes[1:]) + 1) if len(amplitudes) > 1 else 0
    metrics = Metrics(
        rms=float(np.sqrt(np.mean(raw ** 2))),
        vpp=float(np.ptp(raw)),
        minimum=float(np.min(raw)),
        maximum=float(np.max(raw)),
        mean=float(np.mean(raw)),
        dominant_frequency=float(frequencies[peak_index]),
        dominant_amplitude=float(amplitudes[peak_index]),
    )
    return frequencies, amplitudes, metrics


def extract_target_tone(
    values: np.ndarray,
    scan_rate: float,
    target_frequency: float,
    display_seconds: float = 0.05,
    start_sample_index: int = 0,
) -> TargetToneAnalysis:
    """Fit and reconstruct only the sine component at ``target_frequency``.

    A Hann-weighted least-squares fit at the exact requested frequency avoids
    restricting the result to the nearest FFT bin.  The returned waveform is
    therefore the measured contribution of that frequency, not a generated
    sine wave with an arbitrary amplitude.
    """
    samples = np.asarray(values, dtype=float)
    if not np.all(np.isfinite(samples)) or np.any(samples == -9999.0):
        raise ValueError("分量分析窗含无效/丢失样本")
    frequency = float(target_frequency)
    if samples.size < 3 or scan_rate <= 0 or not 0 < frequency < scan_rate / 2:
        return TargetToneAnalysis(
            frequency=frequency,
            amplitude=0.0,
            rms=0.0,
            phase_degrees=0.0,
            time_seconds=np.array([], dtype=float),
            waveform=np.array([], dtype=float),
            absolute_start_time_seconds=0.0,
        )

    time_axis = (
        int(start_sample_index) + np.arange(samples.size, dtype=float)
    ) / float(scan_rate)
    omega_time = 2.0 * np.pi * frequency * time_axis
    cosine = np.cos(omega_time)
    sine = np.sin(omega_time)
    weights = np.hanning(samples.size)
    if not np.any(weights):
        weights = np.ones(samples.size)
    # Solve the three weighted normal equations directly.  This avoids
    # allocating a large N x 3 matrix for the longer stabilizing window.
    normal_matrix = np.array(
        [
            [
                np.sum(weights * cosine * cosine),
                np.sum(weights * cosine * sine),
                np.sum(weights * cosine),
            ],
            [
                np.sum(weights * cosine * sine),
                np.sum(weights * sine * sine),
                np.sum(weights * sine),
            ],
            [np.sum(weights * cosine), np.sum(weights * sine), np.sum(weights)],
        ],
        dtype=float,
    )
    normal_vector = np.array(
        [
            np.sum(weights * samples * cosine),
            np.sum(weights * samples * sine),
            np.sum(weights * samples),
        ],
        dtype=float,
    )
    try:
        coefficients = np.linalg.solve(normal_matrix, normal_vector)
    except np.linalg.LinAlgError:
        coefficients = np.zeros(3, dtype=float)
    cosine_coefficient = float(coefficients[0])
    sine_coefficient = float(coefficients[1])
    amplitude = float(np.hypot(cosine_coefficient, sine_coefficient))
    phase_degrees = float(
        np.rad2deg(np.arctan2(-sine_coefficient, cosine_coefficient)) % 360.0
    )
    fitted = cosine_coefficient * np.cos(omega_time) + sine_coefficient * np.sin(
        omega_time
    )
    display_count = min(
        samples.size, max(3, int(float(scan_rate) * float(display_seconds)))
    )
    display_waveform = fitted[-display_count:]
    display_time = np.arange(display_count, dtype=float) / float(scan_rate)
    absolute_start = (
        int(start_sample_index) + samples.size - display_count
    ) / float(scan_rate)
    return TargetToneAnalysis(
        frequency=frequency,
        amplitude=amplitude,
        rms=amplitude / np.sqrt(2.0),
        phase_degrees=phase_degrees,
        time_seconds=display_time,
        waveform=display_waveform,
        absolute_start_time_seconds=absolute_start,
    )


class TargetToneStabilizer:
    """Low-pass the fitted tone as a complex phasor without angle wrap jumps."""

    def __init__(self, alpha: float = 0.08) -> None:
        self.alpha = min(1.0, max(0.001, float(alpha)))
        self._phasor = None

    def reset(self) -> None:
        self._phasor = None

    def update(self, tone: TargetToneAnalysis) -> TargetToneAnalysis:
        if tone.waveform.size == 0:
            return tone
        phase_radians = np.deg2rad(tone.phase_degrees)
        measured = tone.amplitude * np.exp(1j * phase_radians)
        if self._phasor is None:
            self._phasor = measured
        else:
            self._phasor += self.alpha * (measured - self._phasor)
        amplitude = float(abs(self._phasor))
        phase = float(np.rad2deg(np.angle(self._phasor)) % 360.0)
        absolute_time = tone.absolute_start_time_seconds + tone.time_seconds
        waveform = amplitude * np.cos(
            2.0 * np.pi * tone.frequency * absolute_time + np.deg2rad(phase)
        )
        return TargetToneAnalysis(
            frequency=tone.frequency,
            amplitude=amplitude,
            rms=amplitude / np.sqrt(2.0),
            phase_degrees=phase,
            time_seconds=tone.time_seconds,
            waveform=waveform,
            absolute_start_time_seconds=tone.absolute_start_time_seconds,
        )


def sampling_budget(scan_rate: float, analog: int, digital: int) -> Tuple[bool, str]:
    """Validate one T7 stream against its total-sample-rate limit."""
    total = scan_rate * (analog + digital)
    return total <= 100_000, "总样本率 %s samples/s（上限 100,000）" % format(total, ",.0f")
