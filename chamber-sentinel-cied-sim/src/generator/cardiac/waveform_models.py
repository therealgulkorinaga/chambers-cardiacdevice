"""Parameterized waveform component models for EGM synthesis.

Provides functions to generate individual cardiac waveform components (P wave,
QRS complex, T wave, pacing artifact) as NumPy arrays.  Each component is
parameterized by duration, amplitude, sample rate, and morphology variant.
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np


def generate_p_wave(
    duration_ms: float,
    amplitude_mv: float,
    sample_rate_hz: int,
    morphology: Literal["normal", "peaked", "bifid", "inverted", "absent"] = "normal",
) -> np.ndarray:
    """Generate a P-wave waveform using a Gaussian envelope.

    Parameters:
        duration_ms:   Duration of the P wave in milliseconds (typically 80-120 ms).
        amplitude_mv:  Peak amplitude in millivolts (typically 0.1-0.25 mV).
        sample_rate_hz: Sampling rate in Hz.
        morphology:    Waveform shape variant.

    Returns:
        1-D NumPy array of voltage samples (mV).
    """
    n_samples = max(1, int(round(duration_ms * sample_rate_hz / 1000.0)))
    t = np.linspace(-1.0, 1.0, n_samples, dtype=np.float64)

    if morphology == "absent":
        return np.zeros(n_samples, dtype=np.float64)

    if morphology == "normal":
        # Single symmetric Gaussian
        sigma = 0.35
        wave = amplitude_mv * np.exp(-0.5 * (t / sigma) ** 2)

    elif morphology == "peaked":
        # Narrower, taller Gaussian (P-pulmonale pattern)
        sigma = 0.25
        wave = amplitude_mv * 1.3 * np.exp(-0.5 * (t / sigma) ** 2)

    elif morphology == "bifid":
        # Two overlapping Gaussians (P-mitrale pattern)
        sigma = 0.3
        sep = 0.35
        g1 = np.exp(-0.5 * ((t + sep) / sigma) ** 2)
        g2 = np.exp(-0.5 * ((t - sep) / sigma) ** 2)
        wave = amplitude_mv * 0.8 * (g1 + g2)

    elif morphology == "inverted":
        # Retrograde P wave (junctional or low-atrial origin)
        sigma = 0.35
        wave = -amplitude_mv * np.exp(-0.5 * (t / sigma) ** 2)

    else:
        raise ValueError(f"Unknown P-wave morphology: {morphology!r}")

    return wave


def generate_qrs_complex(
    duration_ms: float,
    amplitude_mv: float,
    sample_rate_hz: int,
    morphology: Literal["narrow", "wide", "paced", "rbbb", "lbbb"] = "narrow",
) -> np.ndarray:
    """Generate a QRS complex using a sum of Gaussian components.

    The QRS is modeled as a superposition of multiple Gaussians to replicate
    the Q, R, and S deflections.

    Parameters:
        duration_ms:   QRS duration in milliseconds (narrow: 60-100, wide: 120-200).
        amplitude_mv:  Peak R-wave amplitude in millivolts.
        sample_rate_hz: Sampling rate in Hz.
        morphology:    Shape variant controlling widths and component amplitudes.

    Returns:
        1-D NumPy array of voltage samples (mV).
    """
    n_samples = max(1, int(round(duration_ms * sample_rate_hz / 1000.0)))
    t = np.linspace(-1.0, 1.0, n_samples, dtype=np.float64)

    if morphology == "narrow":
        # Normal narrow QRS: small Q, tall R, small S
        q_wave = -0.1 * amplitude_mv * np.exp(-0.5 * ((t + 0.35) / 0.12) ** 2)
        r_wave = amplitude_mv * np.exp(-0.5 * ((t + 0.0) / 0.15) ** 2)
        s_wave = -0.25 * amplitude_mv * np.exp(-0.5 * ((t - 0.30) / 0.10) ** 2)
        wave = q_wave + r_wave + s_wave

    elif morphology == "wide":
        # Wide QRS (e.g., ventricular origin): broader components, notched
        q_wave = -0.15 * amplitude_mv * np.exp(-0.5 * ((t + 0.40) / 0.18) ** 2)
        r_wave = amplitude_mv * np.exp(-0.5 * ((t - 0.05) / 0.25) ** 2)
        s_wave = -0.35 * amplitude_mv * np.exp(-0.5 * ((t - 0.45) / 0.18) ** 2)
        # Add notching
        notch = 0.1 * amplitude_mv * np.exp(-0.5 * ((t - 0.20) / 0.08) ** 2)
        wave = q_wave + r_wave + s_wave - notch

    elif morphology == "paced":
        # Paced QRS: wide with initial pacing artifact influence, dominant S wave
        r_wave = 0.7 * amplitude_mv * np.exp(-0.5 * ((t + 0.10) / 0.22) ** 2)
        s_wave = -0.6 * amplitude_mv * np.exp(-0.5 * ((t - 0.30) / 0.25) ** 2)
        # Slurred upstroke
        slur = 0.3 * amplitude_mv * np.exp(-0.5 * ((t + 0.45) / 0.15) ** 2)
        wave = slur + r_wave + s_wave

    elif morphology == "rbbb":
        # Right bundle branch block: rSR' pattern
        r1 = 0.4 * amplitude_mv * np.exp(-0.5 * ((t + 0.30) / 0.12) ** 2)
        s_wave = -0.5 * amplitude_mv * np.exp(-0.5 * ((t + 0.0) / 0.12) ** 2)
        r_prime = amplitude_mv * np.exp(-0.5 * ((t - 0.30) / 0.18) ** 2)
        wave = r1 + s_wave + r_prime

    elif morphology == "lbbb":
        # Left bundle branch block: broad notched R
        r1 = 0.6 * amplitude_mv * np.exp(-0.5 * ((t + 0.15) / 0.20) ** 2)
        notch = -0.15 * amplitude_mv * np.exp(-0.5 * ((t + 0.0) / 0.08) ** 2)
        r2 = 0.8 * amplitude_mv * np.exp(-0.5 * ((t - 0.20) / 0.20) ** 2)
        s_wave = -0.2 * amplitude_mv * np.exp(-0.5 * ((t - 0.55) / 0.12) ** 2)
        wave = r1 + notch + r2 + s_wave

    else:
        raise ValueError(f"Unknown QRS morphology: {morphology!r}")

    return wave


def generate_t_wave(
    duration_ms: float,
    amplitude_mv: float,
    sample_rate_hz: int,
    morphology: Literal["normal", "inverted", "peaked", "flattened", "biphasic"] = "normal",
) -> np.ndarray:
    """Generate a T-wave waveform using an asymmetric Gaussian.

    The T wave is modeled with a slower upstroke and faster downstroke to
    reflect physiological repolarization.

    Parameters:
        duration_ms:   T-wave duration in milliseconds (typically 120-200 ms).
        amplitude_mv:  Peak amplitude in millivolts (typically 0.1-0.5 mV).
        sample_rate_hz: Sampling rate in Hz.
        morphology:    Shape variant.

    Returns:
        1-D NumPy array of voltage samples (mV).
    """
    n_samples = max(1, int(round(duration_ms * sample_rate_hz / 1000.0)))
    t = np.linspace(-1.0, 1.0, n_samples, dtype=np.float64)

    if morphology == "normal":
        # Asymmetric Gaussian: wider left (upstroke), narrower right (downstroke)
        sigma_left = 0.45
        sigma_right = 0.30
        sigma = np.where(t < 0.0, sigma_left, sigma_right)
        wave = amplitude_mv * np.exp(-0.5 * (t / sigma) ** 2)

    elif morphology == "inverted":
        sigma_left = 0.45
        sigma_right = 0.30
        sigma = np.where(t < 0.0, sigma_left, sigma_right)
        wave = -amplitude_mv * np.exp(-0.5 * (t / sigma) ** 2)

    elif morphology == "peaked":
        # Tall, narrow T wave (hyperkalemia pattern)
        sigma = 0.22
        wave = amplitude_mv * 1.5 * np.exp(-0.5 * (t / sigma) ** 2)

    elif morphology == "flattened":
        # Very low amplitude, broad T wave
        sigma = 0.55
        wave = amplitude_mv * 0.2 * np.exp(-0.5 * (t / sigma) ** 2)

    elif morphology == "biphasic":
        # Initial positive deflection followed by negative
        sigma = 0.30
        g1 = np.exp(-0.5 * ((t + 0.30) / sigma) ** 2)
        g2 = np.exp(-0.5 * ((t - 0.35) / sigma) ** 2)
        wave = amplitude_mv * (0.6 * g1 - 0.4 * g2)

    else:
        raise ValueError(f"Unknown T-wave morphology: {morphology!r}")

    return wave


def generate_pacing_artifact(
    amplitude_mv: float,
    sample_rate_hz: int,
) -> np.ndarray:
    """Generate a sharp biphasic pacing artifact spike.

    The pacing artifact is a brief (~0.5 ms) biphasic deflection with a sharp
    initial spike followed by a smaller opposite-polarity rebound.

    Parameters:
        amplitude_mv:  Peak artifact amplitude in millivolts (typically 2-5 mV).
        sample_rate_hz: Sampling rate in Hz.

    Returns:
        1-D NumPy array of voltage samples (mV).
    """
    # Target duration: 0.5 ms, minimum 3 samples for biphasic shape
    duration_ms = 0.5
    n_samples = max(3, int(round(duration_ms * sample_rate_hz / 1000.0)))

    # Ensure odd number of samples for symmetry of construction
    if n_samples % 2 == 0:
        n_samples += 1

    t = np.linspace(0.0, 1.0, n_samples, dtype=np.float64)

    # Sharp initial positive spike (narrow Gaussian)
    spike_pos = amplitude_mv * np.exp(-0.5 * ((t - 0.25) / 0.08) ** 2)

    # Smaller negative rebound
    spike_neg = -0.3 * amplitude_mv * np.exp(-0.5 * ((t - 0.65) / 0.12) ** 2)

    artifact = spike_pos + spike_neg
    return artifact
