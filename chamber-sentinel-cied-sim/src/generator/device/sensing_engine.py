"""
Sensing-threshold simulation engine for cardiac electrogram signals.

Models the analogue front-end of a CIED: compares inbound signal amplitudes
against programmed sensitivity thresholds while enforcing blanking and
refractory periods.  Supports automatic sensitivity adjustment (auto-gain
control).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np
from numpy.random import Generator


# ---------------------------------------------------------------------------
# Public data-model types
# ---------------------------------------------------------------------------


class SensingResult(Enum):
    """Possible outcomes of a sensing evaluation."""

    SENSED = "SENSED"
    BLANKED = "BLANKED"
    REFRACTORY = "REFRACTORY"
    UNDERSENSED = "UNDERSENSED"
    OVERSENSED = "OVERSENSED"


@dataclass
class SensingParameters:
    """Programmable sensing parameters."""

    atrial_sensitivity_mv: float = 0.5  # 0.2 - 2.0 mV
    ventricular_sensitivity_mv: float = 2.5  # 1.0 - 8.0 mV
    blanking_period_ms: float = 120.0
    refractory_period_ms: float = 250.0


@dataclass
class SensingEvent:
    """Record of a single sensing evaluation."""

    timestamp_ms: float
    channel: str
    amplitude_mv: float
    result: SensingResult
    threshold_mv: float


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class SensingEngine:
    """
    Evaluates inbound electrogram signals against sensitivity thresholds
    while enforcing blanking and refractory windows.

    Supports automatic gain control that adapts sensitivity based on the
    amplitude of the most recently sensed event.
    """

    def __init__(
        self,
        params: SensingParameters,
        rng: Generator | None = None,
    ) -> None:
        self._params = params
        self._rng: Generator = rng if rng is not None else np.random.default_rng()

        # Current effective sensitivity (may be adjusted by auto-gain)
        self._atrial_sensitivity_mv: float = params.atrial_sensitivity_mv
        self._ventricular_sensitivity_mv: float = params.ventricular_sensitivity_mv

        # Timing state per channel
        self._last_sensed_ms: dict[str, float] = {
            "atrial": -1e9,
            "ventricular": -1e9,
            "lv": -1e9,
        }
        self._last_pace_or_sense_ms: dict[str, float] = {
            "atrial": -1e9,
            "ventricular": -1e9,
            "lv": -1e9,
        }

        # Auto-gain state: holds the peak amplitude of the last true sense
        self._last_sensed_amplitude: dict[str, float] = {
            "atrial": params.atrial_sensitivity_mv * 4.0,
            "ventricular": params.ventricular_sensitivity_mv * 4.0,
            "lv": params.ventricular_sensitivity_mv * 4.0,
        }

        # Noise detection window (consecutive oversenses within short window)
        self._noise_window_ms: float = 60.0  # ms
        self._noise_event_timestamps: dict[str, list[float]] = {
            "atrial": [],
            "ventricular": [],
            "lv": [],
        }
        self._noise_threshold_count: int = 4  # events within window = noise

        # Event log
        self._event_log: list[SensingEvent] = []

    # -- Threshold helpers -------------------------------------------------

    def _get_threshold(self, channel: str) -> float:
        """Return the current effective sensitivity threshold for *channel*."""
        if channel == "atrial":
            return self._atrial_sensitivity_mv
        return self._ventricular_sensitivity_mv  # RV and LV share the ventricular setting

    # -- Core public API ---------------------------------------------------

    def process_signal(
        self,
        channel: str,
        amplitude_mv: float,
        timestamp_ms: float,
    ) -> SensingResult:
        """
        Evaluate whether a signal on *channel* at *timestamp_ms* with peak
        *amplitude_mv* is sensed, blanked, refractory, under-sensed, or
        over-sensed.

        Parameters
        ----------
        channel:
            ``'atrial'``, ``'ventricular'``, or ``'lv'``.
        amplitude_mv:
            Peak absolute signal amplitude in millivolts.
        timestamp_ms:
            Absolute timestamp in milliseconds.

        Returns
        -------
        SensingResult
        """
        threshold = self._get_threshold(channel)
        last_event_ms = self._last_pace_or_sense_ms.get(channel, -1e9)
        elapsed = timestamp_ms - last_event_ms

        # ----- Blanking period: amplifier is physically disconnected -----
        if elapsed < self._params.blanking_period_ms:
            result = SensingResult.BLANKED
            self._record(timestamp_ms, channel, amplitude_mv, result, threshold)
            return result

        # ----- Refractory period: signal is seen but not acted on --------
        if elapsed < self._params.refractory_period_ms:
            result = SensingResult.REFRACTORY
            self._record(timestamp_ms, channel, amplitude_mv, result, threshold)
            return result

        # ----- Noise detection: check for rapid over-sensing artefact ----
        if self._is_noise_burst(channel, timestamp_ms, amplitude_mv, threshold):
            result = SensingResult.OVERSENSED
            self._record(timestamp_ms, channel, amplitude_mv, result, threshold)
            return result

        # ----- Amplitude comparison --------------------------------------
        abs_amplitude = abs(amplitude_mv)

        if abs_amplitude >= threshold:
            # True sense
            result = SensingResult.SENSED
            self._last_sensed_ms[channel] = timestamp_ms
            self._last_pace_or_sense_ms[channel] = timestamp_ms
            self._last_sensed_amplitude[channel] = abs_amplitude
            self._record(timestamp_ms, channel, amplitude_mv, result, threshold)
            return result

        # Signal is below threshold -- under-sensing if a real depolarisation
        # is expected (amplitude_mv > 0 indicates a real signal, not noise).
        if amplitude_mv > 0:
            result = SensingResult.UNDERSENSED
        else:
            # Negative or zero amplitude is treated as baseline noise
            result = SensingResult.BLANKED
        self._record(timestamp_ms, channel, amplitude_mv, result, threshold)
        return result

    def register_pace_event(self, channel: str, timestamp_ms: float) -> None:
        """
        Notify the sensing engine that a pace was delivered on *channel*,
        resetting blanking/refractory timers.
        """
        self._last_pace_or_sense_ms[channel] = timestamp_ms

    # -- Auto-gain control -------------------------------------------------

    def update_auto_sensitivity(self, last_event_amplitude_mv: float, channel: str = "ventricular") -> None:
        """
        Automatic gain control: adjusts the sensitivity threshold based on
        the amplitude of the most recently sensed event.

        The algorithm sets the threshold to 75 % of the last sensed
        R-wave / P-wave amplitude, clamped within the programmable range.

        Parameters
        ----------
        last_event_amplitude_mv:
            Peak amplitude of the last sensed event in mV.
        channel:
            Channel to adjust (``'atrial'`` or ``'ventricular'``).
        """
        new_threshold = abs(last_event_amplitude_mv) * 0.75

        if channel == "atrial":
            new_threshold = float(np.clip(new_threshold, 0.2, 2.0))
            self._atrial_sensitivity_mv = new_threshold
        else:
            new_threshold = float(np.clip(new_threshold, 1.0, 8.0))
            self._ventricular_sensitivity_mv = new_threshold

    # -- Noise detection helpers -------------------------------------------

    def _is_noise_burst(
        self,
        channel: str,
        timestamp_ms: float,
        amplitude_mv: float,
        threshold: float,
    ) -> bool:
        """
        Detect rapid noise bursts: multiple threshold crossings within
        a short window suggest electromagnetic interference or lead noise
        rather than true cardiac signals.
        """
        ts_list = self._noise_event_timestamps[channel]

        if abs(amplitude_mv) >= threshold:
            ts_list.append(timestamp_ms)

        # Purge events outside the noise window
        cutoff = timestamp_ms - self._noise_window_ms
        self._noise_event_timestamps[channel] = [
            t for t in ts_list if t >= cutoff
        ]
        ts_list = self._noise_event_timestamps[channel]

        return len(ts_list) >= self._noise_threshold_count

    # -- Internal bookkeeping ----------------------------------------------

    def _record(
        self,
        timestamp_ms: float,
        channel: str,
        amplitude_mv: float,
        result: SensingResult,
        threshold: float,
    ) -> None:
        """Append a sensing event to the internal log."""
        self._event_log.append(
            SensingEvent(
                timestamp_ms=timestamp_ms,
                channel=channel,
                amplitude_mv=amplitude_mv,
                result=result,
                threshold_mv=threshold,
            )
        )

    # -- Accessors ---------------------------------------------------------

    def get_event_log(self) -> list[SensingEvent]:
        """Return the full sensing event log."""
        return list(self._event_log)

    def get_current_thresholds(self) -> dict[str, float]:
        """Return current effective sensitivity thresholds."""
        return {
            "atrial_sensitivity_mv": self._atrial_sensitivity_mv,
            "ventricular_sensitivity_mv": self._ventricular_sensitivity_mv,
        }
