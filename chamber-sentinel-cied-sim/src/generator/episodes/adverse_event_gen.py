"""
Rare adverse event generator for safety investigation testing.

Models device-related adverse events at realistic annual incidence rates
drawn from published CIED registries.  Supports both stochastic generation
over a simulation window and deterministic injection for scenario testing.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.random import Generator


# ---------------------------------------------------------------------------
# Public data-model types
# ---------------------------------------------------------------------------


@dataclass
class AdverseEvent:
    """A single adverse device event."""

    event_id: str
    event_type: str
    timestamp_s: float
    severity: str  # 'minor', 'major', 'life_threatening', 'fatal'
    device_state_snapshot: dict[str, Any]
    detected_at: float | None = None  # may be delayed


# ---------------------------------------------------------------------------
# Default annual incidence rates (per device-year)
# ---------------------------------------------------------------------------

_DEFAULT_ANNUAL_RATES: dict[str, float] = {
    "lead_fracture": 0.003,
    "lead_dislodgement": 0.005,
    "insulation_breach": 0.005,
    "generator_malfunction": 0.0005,
    "unexpected_battery_eol": 0.0001,
    "inappropriate_shock": 0.03,
    "patient_death_device": 0.0003,
}

# Event type -> typical severity mapping
_SEVERITY_MAP: dict[str, str] = {
    "lead_fracture": "major",
    "lead_dislodgement": "major",
    "insulation_breach": "major",
    "generator_malfunction": "life_threatening",
    "unexpected_battery_eol": "life_threatening",
    "inappropriate_shock": "major",
    "patient_death_device": "fatal",
}

# Event type -> typical detection delay distribution parameters
# (mean_hours, std_hours) -- some events are detected immediately, others
# only at follow-up.
_DETECTION_DELAY_PARAMS: dict[str, tuple[float, float]] = {
    "lead_fracture": (48.0, 24.0),        # detected at impedance check
    "lead_dislodgement": (4.0, 2.0),       # loss of capture -> quick detection
    "insulation_breach": (72.0, 36.0),      # may be insidious
    "generator_malfunction": (0.5, 0.25),   # usually detected immediately
    "unexpected_battery_eol": (168.0, 72.0), # may not be caught until follow-up
    "inappropriate_shock": (0.0, 0.0),       # patient reports immediately
    "patient_death_device": (0.0, 0.0),      # immediate
}


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class AdverseEventGenerator:
    """
    Generates rare adverse device events at configurable annual incidence
    rates using Poisson processes.

    Parameters
    ----------
    annual_rates:
        Dict mapping event type names to annual incidence rates
        (probability per device-year).  Defaults to published registry
        estimates.
    rng:
        NumPy random generator for reproducibility.
    """

    def __init__(
        self,
        annual_rates: dict[str, float] | None = None,
        rng: Generator | None = None,
    ) -> None:
        self._rates = dict(_DEFAULT_ANNUAL_RATES)
        if annual_rates is not None:
            self._rates.update(annual_rates)
        self._rng: Generator = rng if rng is not None else np.random.default_rng()

        # Event log
        self._events: list[AdverseEvent] = []

    # -- Internal helpers --------------------------------------------------

    def _poisson_count(self, rate_per_year: float, duration_days: float) -> int:
        """
        Draw the number of events from a Poisson distribution given an
        annual rate and a simulation window in days.
        """
        if rate_per_year <= 0 or duration_days <= 0:
            return 0
        expected = rate_per_year * (duration_days / 365.0)
        return int(self._rng.poisson(expected))

    def _detection_delay_s(self, event_type: str) -> float:
        """
        Sample a detection delay (in seconds) for the given event type.
        """
        params = _DETECTION_DELAY_PARAMS.get(event_type, (24.0, 12.0))
        mean_h, std_h = params
        if mean_h <= 0 and std_h <= 0:
            return 0.0
        delay_h = max(0.0, float(self._rng.normal(mean_h, max(std_h, 0.01))))
        return delay_h * 3600.0  # convert to seconds

    def _snapshot_device_state(self, device_state: dict[str, Any]) -> dict[str, Any]:
        """
        Create a shallow copy of the device state dict for inclusion in
        the adverse event record.
        """
        # Shallow copy top-level; nested mutables are referenced (acceptable
        # for a snapshot).
        return dict(device_state)

    # -- Public API --------------------------------------------------------

    def generate_events(
        self,
        duration_days: float,
        device_state: dict[str, Any],
    ) -> list[AdverseEvent]:
        """
        Generate adverse events over the given simulation window.

        Parameters
        ----------
        duration_days:
            Length of the simulation period in days.
        device_state:
            A dict capturing the current device state (battery, leads,
            pacing stats, etc.) at the time of generation.  A snapshot
            is stored with each event.

        Returns
        -------
        list[AdverseEvent]
            Events sorted by timestamp.
        """
        total_seconds = duration_days * 86400.0
        new_events: list[AdverseEvent] = []

        for event_type, annual_rate in self._rates.items():
            n = self._poisson_count(annual_rate, duration_days)
            if n == 0:
                continue

            # Uniformly distribute arrival times within the window
            arrival_times = self._rng.uniform(0.0, total_seconds, size=n)
            arrival_times.sort()

            severity = _SEVERITY_MAP.get(event_type, "major")

            for ts in arrival_times:
                ts_float = float(ts)
                detection_delay = self._detection_delay_s(event_type)
                detected_at = ts_float + detection_delay if detection_delay > 0 else ts_float

                event = AdverseEvent(
                    event_id=uuid.uuid4().hex[:12],
                    event_type=event_type,
                    timestamp_s=ts_float,
                    severity=severity,
                    device_state_snapshot=self._snapshot_device_state(device_state),
                    detected_at=detected_at,
                )
                new_events.append(event)

        new_events.sort(key=lambda e: e.timestamp_s)
        self._events.extend(new_events)
        return new_events

    def inject_event(
        self,
        event_type: str,
        timestamp_s: float,
        severity: str,
        device_state: dict[str, Any] | None = None,
    ) -> AdverseEvent:
        """
        Deterministically inject an adverse event (for scenario testing).

        Parameters
        ----------
        event_type:
            Type of adverse event (e.g. ``'lead_fracture'``).
        timestamp_s:
            Absolute timestamp in seconds.
        severity:
            One of ``'minor'``, ``'major'``, ``'life_threatening'``,
            ``'fatal'``.
        device_state:
            Optional device state snapshot.  Defaults to empty dict.

        Returns
        -------
        AdverseEvent
        """
        detection_delay = self._detection_delay_s(event_type)
        detected_at = timestamp_s + detection_delay if detection_delay > 0 else timestamp_s

        event = AdverseEvent(
            event_id=uuid.uuid4().hex[:12],
            event_type=event_type,
            timestamp_s=timestamp_s,
            severity=severity,
            device_state_snapshot=device_state if device_state is not None else {},
            detected_at=detected_at,
        )
        self._events.append(event)
        return event

    # -- History -----------------------------------------------------------

    def get_event_history(self) -> list[AdverseEvent]:
        """Return the full adverse event log sorted by timestamp."""
        return sorted(self._events, key=lambda e: e.timestamp_s)

    def get_event_counts(self) -> dict[str, int]:
        """Return a count of adverse events by type."""
        counts: dict[str, int] = {}
        for ev in self._events:
            counts[ev.event_type] = counts.get(ev.event_type, 0) + 1
        return counts

    def get_events_by_severity(self, severity: str) -> list[AdverseEvent]:
        """Return all events matching the given severity level."""
        return [ev for ev in self._events if ev.severity == severity]
