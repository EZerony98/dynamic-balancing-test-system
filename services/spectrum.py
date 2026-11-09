from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from models.data import Metrics

WINDOWS = {"Rectangular": lambda n: np.ones(n), "Hann": np.hanning,
           "Hamming": np.hamming, "Blackman": np.blackman}


@dataclass
class FrequencyComponent:
    frequency: float = 0.0
    amplitude: float = 0.0
    phase_deg: float = 0.0


def pulse_frequency(values: np.ndarray, scan_rate: float) -> float:
    """Estimate one-pulse-per-revolution reference frequency from rising edges."""
    x = np.asarray(values, dtype=float)
    if len(x) < 4 or not np.isfinite(x).all():
        return 0.0
    threshold = (float(np.min(x)) + float(np.max(x))) / 2.0
    edges = np.flatnonzero((x[:-1] <= threshold) & (x[1:] > threshold)) + 1
    if len(edges) < 2:
        return 0.0
    periods = np.diff(edges) / float(scan_rate)
    periods = periods[periods > 0]
    return float(1.0 / np.median(periods)) if len(periods) else 0.0


def frequency_component(values: np.ndarray, scan_rate: float, frequency: float) -> FrequencyComponent:
    """Return the diagnostic single-frequency voltage component.

    It is intentionally not converted into mass or correction angle. Those
    results require the confirmed tach reference and trial-weight calibration.
    """
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x) & (x != -9999.0)]
    if len(x) < 8 or frequency <= 0 or frequency >= scan_rate / 2:
        return FrequencyComponent(frequency=frequency)
    x = x - np.mean(x)
    t = np.arange(len(x), dtype=float) / float(scan_rate)
    w = np.hanning(len(x))
    gain = max(float(np.sum(w)), 1e-12)
    coefficient = (2.0 / gain) * np.sum(x * w * np.exp(-2j * np.pi * frequency * t))
    return FrequencyComponent(
        frequency=float(frequency),
        amplitude=float(abs(coefficient)),
        phase_deg=float(np.degrees(np.angle(coefficient)) % 360.0),
    )


def strongest_peaks(freq: np.ndarray, amplitude: np.ndarray, upper: float, count: int = 5) -> list[tuple[float, float]]:
    if len(freq) < 3 or len(amplitude) != len(freq):
        return []
    mask = (freq > 0) & (freq <= upper)
    f, a = freq[mask], amplitude[mask]
    if len(f) < 3:
        return []
    local = np.flatnonzero((a[1:-1] > a[:-2]) & (a[1:-1] >= a[2:])) + 1
    if not len(local):
        return []
    chosen = local[np.argsort(a[local])[-count:]][::-1]
    return [(float(f[i]), float(a[i])) for i in chosen]

def spectrum(values: np.ndarray, scan_rate: float, fft_size: int = 4096,
             window: str = "Hann", remove_dc: bool = True) -> tuple[np.ndarray, np.ndarray, Metrics]:
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x) & (x != -9999.0)]
    if not len(x):
        return np.array([]), np.array([]), Metrics()
    raw = x.copy()
    n = min(len(x), fft_size)
    x = x[-n:]
    if remove_dc:
        x = x - np.mean(x)
    w = WINDOWS.get(window, np.hanning)(n)
    gain = max(float(np.sum(w)), 1e-12)
    amp = 2.0 * np.abs(np.fft.rfft(x * w)) / gain
    if len(amp): amp[0] *= .5
    freq = np.fft.rfftfreq(n, 1.0 / scan_rate)
    idx = int(np.argmax(amp[1:]) + 1) if len(amp) > 1 else 0
    m = Metrics(float(np.sqrt(np.mean(raw ** 2))), float(np.ptp(raw)),
                float(np.min(raw)), float(np.max(raw)), float(np.mean(raw)),
                float(freq[idx]), float(amp[idx]))
    return freq, amp, m

def sampling_budget(scan_rate: float, analog: int, digital: int) -> tuple[bool, str]:
    total = scan_rate * (analog + digital)
    return (total <= 100_000, f"总样本率 {total:,.0f} samples/s（上限 100,000）")
