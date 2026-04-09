"""Adapter layer between openCARP template output and the EGM synthesizer.

Provides time-stretching, resampling, and amplitude scaling so that
pre-computed beat templates (generated at a fixed sample rate and duration)
can be aligned to any target RR interval and sampling rate required by the
downstream :class:`~egm_synthesizer.EGMSynthesizer`.

All signal processing uses :mod:`scipy.signal` for high-quality
anti-aliased resampling and :mod:`numpy` for array operations.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from scipy import signal as scipy_signal

logger = logging.getLogger(__name__)

# Default channel gain factors (relative to ventricular near-field)
_DEFAULT_CHANNEL_GAINS: dict[str, float] = {
    "atrial": 3.0,      # Near-field atrial lead — highest gain for P wave
    "ventricular": 1.0,  # Near-field ventricular lead — reference
    "shock": 0.6,        # Far-field (shock/can) lead — attenuated
}


class IonicAdapter:
    """Adapts openCARP template waveforms to the EGM synthesizer interface.

    The adapter handles three concerns:

    1. **Time-stretching** a template beat to match a target RR interval.
    2. **Resampling** from the template's native sample rate (typically 1 kHz)
       to the synthesizer's sample rate (typically 256 Hz).
    3. **Amplitude scaling** per-channel with configurable gain factors.

    Parameters:
        source_rate_hz: Sample rate of the stored templates (default 1000 Hz).
        target_rate_hz: Sample rate of the synthesizer output (default 256 Hz).
    """

    def __init__(
        self,
        source_rate_hz: int = 1000,
        target_rate_hz: int = 256,
    ) -> None:
        self._source_rate = source_rate_hz
        self._target_rate = target_rate_hz

    @property
    def source_rate_hz(self) -> int:
        """Return the source (template) sample rate in Hz."""
        return self._source_rate

    @property
    def target_rate_hz(self) -> int:
        """Return the target (synthesizer) sample rate in Hz."""
        return self._target_rate

    # ------------------------------------------------------------------
    # Single-channel adaptation
    # ------------------------------------------------------------------

    def adapt_beat(
        self,
        template: np.ndarray,
        target_rr_ms: float,
        amplitude_scale: float = 1.0,
    ) -> np.ndarray:
        """Adapt a single-channel beat template to a target RR interval.

        Processing pipeline:
        1. Compute the source duration from the template length and source rate.
        2. Time-stretch (or compress) the template to match ``target_rr_ms``.
        3. Resample from ``source_rate_hz`` to ``target_rate_hz``.
        4. Apply ``amplitude_scale``.

        Parameters:
            template: 1-D array of voltage samples at ``source_rate_hz``.
            target_rr_ms: Desired beat duration in milliseconds.
            amplitude_scale: Multiplicative gain (default 1.0).

        Returns:
            1-D array of voltage samples at ``target_rate_hz``.
        """
        if template.ndim != 1:
            raise ValueError(
                f"Expected 1-D template array, got shape {template.shape}"
            )

        if len(template) == 0:
            return np.array([], dtype=np.float64)

        # Step 1: source duration
        source_duration_ms = (len(template) / self._source_rate) * 1000.0

        # Step 2: time-stretch to target RR
        if abs(source_duration_ms - target_rr_ms) > 0.5:
            stretched = self.time_stretch(template, source_duration_ms, target_rr_ms)
        else:
            stretched = template.copy()

        # Step 3: resample to target rate
        if self._source_rate != self._target_rate:
            resampled = self.resample(stretched, self._source_rate, self._target_rate)
        else:
            resampled = stretched

        # Step 4: amplitude scaling
        if amplitude_scale != 1.0:
            resampled = resampled * amplitude_scale

        return resampled.astype(np.float64)

    # ------------------------------------------------------------------
    # Multi-channel adaptation
    # ------------------------------------------------------------------

    def adapt_multichannel(
        self,
        templates: dict[str, np.ndarray],
        target_rr_ms: float,
        target_rate_hz: int | None = None,
        channel_gains: dict[str, float] | None = None,
    ) -> dict[str, np.ndarray]:
        """Adapt all channels with consistent timing and per-channel gains.

        Each channel is time-stretched to the same ``target_rr_ms`` and
        resampled to the same target rate, ensuring temporal alignment
        across channels.

        Parameters:
            templates: Dict mapping channel name to 1-D template array.
            target_rr_ms: Desired beat duration in milliseconds.
            target_rate_hz: Override target sample rate for this call only.
                Defaults to ``self.target_rate_hz``.
            channel_gains: Per-channel amplitude multipliers.  Missing
                channels use the default gains defined in
                :data:`_DEFAULT_CHANNEL_GAINS`.

        Returns:
            Dict mapping channel name to adapted 1-D array.
        """
        gains = dict(_DEFAULT_CHANNEL_GAINS)
        if channel_gains is not None:
            gains.update(channel_gains)

        effective_target_rate = target_rate_hz if target_rate_hz is not None else self._target_rate

        # Compute target number of samples for consistency
        target_n_samples = max(1, int(round(target_rr_ms * effective_target_rate / 1000.0)))

        result: dict[str, np.ndarray] = {}

        for ch_name, template in templates.items():
            gain = gains.get(ch_name, 1.0)

            if template.ndim != 1:
                logger.warning(
                    "Channel '%s' template has shape %s; expected 1-D. Flattening.",
                    ch_name, template.shape,
                )
                template = template.ravel()

            if len(template) == 0:
                result[ch_name] = np.zeros(target_n_samples, dtype=np.float64)
                continue

            # Time-stretch
            source_duration_ms = (len(template) / self._source_rate) * 1000.0
            if abs(source_duration_ms - target_rr_ms) > 0.5:
                stretched = self.time_stretch(template, source_duration_ms, target_rr_ms)
            else:
                stretched = template.copy()

            # Resample
            if self._source_rate != effective_target_rate:
                resampled = self.resample(stretched, self._source_rate, effective_target_rate)
            else:
                resampled = stretched

            # Enforce exact target length (handle rounding)
            if len(resampled) != target_n_samples:
                if len(resampled) > target_n_samples:
                    resampled = resampled[:target_n_samples]
                else:
                    resampled = np.pad(
                        resampled,
                        (0, target_n_samples - len(resampled)),
                        mode="constant",
                    )

            # Apply gain
            resampled = resampled * gain

            result[ch_name] = resampled.astype(np.float64)

        return result

    # ------------------------------------------------------------------
    # Resampling
    # ------------------------------------------------------------------

    def resample(
        self,
        signal_data: np.ndarray,
        source_rate: int,
        target_rate: int,
    ) -> np.ndarray:
        """Resample a signal from ``source_rate`` to ``target_rate``.

        Uses :func:`scipy.signal.resample` which applies an FFT-based
        method with proper anti-aliasing.

        Parameters:
            signal_data: 1-D input signal.
            source_rate: Original sample rate in Hz.
            target_rate: Desired sample rate in Hz.

        Returns:
            Resampled 1-D array.
        """
        if source_rate == target_rate:
            return signal_data.copy()

        if len(signal_data) == 0:
            return np.array([], dtype=np.float64)

        # Compute target number of samples
        duration_s = len(signal_data) / source_rate
        target_n = max(1, int(round(duration_s * target_rate)))

        resampled = scipy_signal.resample(signal_data.astype(np.float64), target_n)
        return resampled

    # ------------------------------------------------------------------
    # Time stretching
    # ------------------------------------------------------------------

    def time_stretch(
        self,
        signal_data: np.ndarray,
        source_duration_ms: float,
        target_duration_ms: float,
    ) -> np.ndarray:
        """Stretch or compress a signal while preserving morphology.

        For moderate stretch ratios (0.5x - 2.0x), linear interpolation
        preserves the waveform shape well.  For extreme ratios, a
        phase-vocoder-like approach with overlap-add could be used, but
        for cardiac EGM templates the ratios are typically modest.

        The output has ``len(signal_data) * (target / source)`` samples,
        keeping the same effective sample rate as the input.

        Parameters:
            signal_data: 1-D input signal.
            source_duration_ms: Original duration in milliseconds.
            target_duration_ms: Desired duration in milliseconds.

        Returns:
            Time-stretched 1-D array (same effective sample rate).
        """
        if len(signal_data) == 0:
            return np.array([], dtype=np.float64)

        if abs(source_duration_ms - target_duration_ms) < 0.1:
            return signal_data.copy()

        ratio = target_duration_ms / max(source_duration_ms, 0.01)
        target_n = max(1, int(round(len(signal_data) * ratio)))

        # Use scipy.signal.resample for high-quality interpolation that
        # preserves the spectral content of the waveform
        if target_n == len(signal_data):
            return signal_data.copy()

        stretched = scipy_signal.resample(signal_data.astype(np.float64), target_n)
        return stretched

    # ------------------------------------------------------------------
    # Convenience: adapt from library to synthesizer format
    # ------------------------------------------------------------------

    def library_to_synthesizer(
        self,
        multichannel_beats: dict[str, np.ndarray],
        target_rr_ms: float,
        target_rate_hz: int | None = None,
        channel_gains: dict[str, float] | None = None,
    ) -> dict[str, np.ndarray]:
        """End-to-end convenience method: library output -> synthesizer input.

        Renames channels from the template naming convention
        (``"atrial"``, ``"ventricular"``, ``"shock"``) to the EGM
        synthesizer naming convention (``"atrial_egm"``,
        ``"ventricular_egm"``, ``"shock_channel"``).

        Parameters:
            multichannel_beats: Output of
                :meth:`TemplateLibrary.get_beat_multichannel`.
            target_rr_ms: Target RR interval in ms.
            target_rate_hz: Target sample rate (overrides instance default).
            channel_gains: Per-channel gain overrides.

        Returns:
            Dict with keys ``"atrial_egm"``, ``"ventricular_egm"``,
            ``"shock_channel"``.
        """
        adapted = self.adapt_multichannel(
            templates=multichannel_beats,
            target_rr_ms=target_rr_ms,
            target_rate_hz=target_rate_hz,
            channel_gains=channel_gains,
        )

        # Map to synthesizer channel names
        name_map = {
            "atrial": "atrial_egm",
            "ventricular": "ventricular_egm",
            "shock": "shock_channel",
        }

        result: dict[str, np.ndarray] = {}
        for src_name, dst_name in name_map.items():
            if src_name in adapted:
                result[dst_name] = adapted[src_name]

        return result
