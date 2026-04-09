"""
Attack surface calculation engine per PRD Section 8.2.

Computes composite attack-surface scores for the current (persist-by-default)
and Chambers (burn-by-default) architectures based on:

    AS = SUM_locations  volume(loc) * accessibility(loc) * avg_sensitivity

The Chambers architecture introduces a *temporal factor* that bounds
exposure: data sitting in the relay for *burn_window_s* seconds has its
effective volume scaled by ``min(1.0, age / burn_window)``, reflecting
the fact that burned data contributes zero attack surface.

Also provides breach-impact analysis (how much data an attacker could
exfiltrate given a breach at a specific location and time).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


# ---------------------------------------------------------------------------
# Weight tables
# ---------------------------------------------------------------------------

ACCESSIBILITY_WEIGHTS: dict[str, float] = {
    # Current architecture locations
    "device": 0.1,
    "transmitter": 0.3,
    "cloud": 0.8,
    "portal": 0.6,
    "aggregate": 0.5,
    # Chambers architecture locations
    "relay": 0.5,
    "patient_record": 0.2,
    "research_channel": 0.4,
    "device_maint": 0.3,
}

SENSITIVITY_WEIGHTS: dict[str, float] = {
    "iegm": 1.0,
    "episode": 0.9,
    "therapy": 0.8,
    "trends": 0.6,
    "device_status": 0.3,
    "demographics": 0.5,
    "activity": 0.7,
}

# Mapping from EventType-style names to sensitivity keys
_EVENT_TYPE_TO_SENSITIVITY: dict[str, str] = {
    "heartbeat": "iegm",
    "pacing": "therapy",
    "episode_start": "episode",
    "episode_end": "episode",
    "alert": "episode",
    "transmission": "trends",
    "device_status": "device_status",
    "activity": "activity",
    "adverse_event": "episode",
    "lead_measurement": "device_status",
    "threshold_test": "device_status",
    "firmware_update": "device_status",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _avg_sensitivity(data_types: dict[str, int]) -> float:
    """Weighted-average sensitivity for a mix of data types.

    Parameters
    ----------
    data_types:
        Mapping of data-type name to byte count.

    Returns
    -------
    float
        Volume-weighted average sensitivity in [0, 1].
    """
    total_bytes = 0
    weighted_sum = 0.0
    for dtype, nbytes in data_types.items():
        sens = SENSITIVITY_WEIGHTS.get(dtype, 0.5)
        weighted_sum += sens * nbytes
        total_bytes += nbytes
    if total_bytes == 0:
        return 0.0
    return weighted_sum / total_bytes


# ---------------------------------------------------------------------------
# Snapshot storage
# ---------------------------------------------------------------------------


@dataclass
class _AttackSurfaceSnapshot:
    timestamp_s: float
    architecture: str
    score: float
    breakdown: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Calculator
# ---------------------------------------------------------------------------


class AttackSurfaceCalculator:
    """Computes and tracks attack-surface scores for both architectures.

    Typical usage::

        calc = AttackSurfaceCalculator()
        as_cur = calc.calculate_current(current_volumes)
        as_ch  = calc.calculate_chambers(chambers_volumes, burn_window_s=259200)
        comparison = calc.get_comparison()
    """

    def __init__(self) -> None:
        self._snapshots: list[_AttackSurfaceSnapshot] = []

    # ------------------------------------------------------------------
    # Current architecture
    # ------------------------------------------------------------------

    def calculate_current(
        self,
        data_volumes: dict[str, dict[str, int]],
        timestamp_s: float = 0.0,
    ) -> float:
        """Calculate composite attack surface for the current architecture.

        Parameters
        ----------
        data_volumes:
            ``{location: {data_type: bytes}}``.
            Locations are ``device``, ``transmitter``, ``cloud``, ``portal``,
            ``aggregate``.
        timestamp_s:
            Simulation time of this measurement.

        Returns
        -------
        float
            Composite attack-surface score (unnormalized; higher = worse).
        """
        score = 0.0
        breakdown: dict[str, float] = {}

        for location, type_volumes in data_volumes.items():
            accessibility = ACCESSIBILITY_WEIGHTS.get(location, 0.5)
            loc_bytes = sum(type_volumes.values())
            sensitivity = _avg_sensitivity(type_volumes)

            # AS contribution = volume * accessibility * sensitivity
            contribution = loc_bytes * accessibility * sensitivity
            score += contribution
            breakdown[location] = contribution

        self._snapshots.append(_AttackSurfaceSnapshot(
            timestamp_s=timestamp_s,
            architecture="current",
            score=score,
            breakdown=breakdown,
        ))
        return score

    # ------------------------------------------------------------------
    # Chambers architecture
    # ------------------------------------------------------------------

    def calculate_chambers(
        self,
        data_volumes: dict[str, dict[str, int]],
        burn_window_s: float,
        timestamp_s: float = 0.0,
        data_ages: dict[str, float] | None = None,
    ) -> float:
        """Calculate composite attack surface for the Chambers architecture.

        The temporal factor ensures that data nearing its burn window
        contributes less to the attack surface: ``temporal_factor =
        min(1.0, effective_volume_fraction)``.

        For the relay, effective volume at any instant is bounded by what
        has accumulated within the burn window.  If the relay is operating
        correctly, data older than *burn_window_s* has been destroyed.

        Parameters
        ----------
        data_volumes:
            ``{location: {data_type: bytes}}``.
            Typical locations: ``relay``, ``patient_record``,
            ``research_channel``, ``device_maint``.
        burn_window_s:
            Burn window in seconds (e.g. 259200 for 72 hours).
        timestamp_s:
            Simulation time.
        data_ages:
            Optional mapping of ``location -> average_age_seconds`` for
            data at each location.  Used to refine the temporal factor.
            If not supplied, the relay is assumed to hold exactly one
            burn-window's worth of data.

        Returns
        -------
        float
            Composite attack-surface score.
        """
        if burn_window_s <= 0:
            burn_window_s = 1.0  # prevent division by zero

        score = 0.0
        breakdown: dict[str, float] = {}

        for location, type_volumes in data_volumes.items():
            accessibility = ACCESSIBILITY_WEIGHTS.get(location, 0.5)
            loc_bytes = sum(type_volumes.values())
            sensitivity = _avg_sensitivity(type_volumes)

            # Temporal factor: for the relay and other burn-eligible locations,
            # the effective exposure is scaled by avg_age / burn_window.
            # If data_ages are provided, use the actual age; otherwise assume
            # the average age is half the burn window (uniform arrival).
            if location in ("relay", "research_channel"):
                if data_ages and location in data_ages:
                    avg_age = data_ages[location]
                else:
                    avg_age = burn_window_s / 2.0
                temporal_factor = min(1.0, avg_age / burn_window_s)
            elif location == "device_maint":
                # Device maintenance data has a longer window but is
                # still bounded.
                temporal_factor = 0.8
            elif location == "patient_record":
                # Patient-held record: accessible only to the patient.
                temporal_factor = 1.0
            else:
                temporal_factor = 1.0

            contribution = loc_bytes * accessibility * sensitivity * temporal_factor
            score += contribution
            breakdown[location] = contribution

        self._snapshots.append(_AttackSurfaceSnapshot(
            timestamp_s=timestamp_s,
            architecture="chambers",
            score=score,
            breakdown=breakdown,
        ))
        return score

    # ------------------------------------------------------------------
    # Breach impact analysis
    # ------------------------------------------------------------------

    def calculate_breach_impact(
        self,
        architecture: str,
        breach_location: str,
        breach_time_s: float,
        data_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Estimate data exposure from a breach at a specific location and time.

        Parameters
        ----------
        architecture:
            ``'current'`` or ``'chambers'``.
        breach_location:
            Location of the breach (e.g. ``'cloud'``, ``'relay'``).
        breach_time_s:
            Simulation time of the breach.
        data_state:
            Full data-state dict:
            ``{location: {data_type: bytes}}`` and optionally
            ``burn_window_s`` and ``data_ages``.

        Returns
        -------
        dict
            ``exposed_bytes``, ``exposed_types``, ``sensitivity_score``,
            ``accessibility_score``, ``impact_score``,
            ``patients_affected`` (count estimate).
        """
        volumes: dict[str, dict[str, int]] = data_state.get("volumes", {})
        burn_window_s: float = data_state.get("burn_window_s", 259_200.0)
        patients_per_location: dict[str, int] = data_state.get("patients_per_location", {})

        location_data = volumes.get(breach_location, {})
        exposed_bytes = sum(location_data.values())
        sensitivity = _avg_sensitivity(location_data)
        accessibility = ACCESSIBILITY_WEIGHTS.get(breach_location, 0.5)

        # For Chambers, the relay only holds at most burn_window worth of data.
        if architecture == "chambers" and breach_location in ("relay", "research_channel"):
            # The attacker can only get data that hasn't been burned yet.
            # If the relay is running correctly, this is bounded.
            temporal_bound = 1.0  # already reflected in the stored volumes
        else:
            temporal_bound = 1.0

        impact_score = exposed_bytes * sensitivity * accessibility * temporal_bound
        patients_affected = patients_per_location.get(breach_location, 0)

        return {
            "architecture": architecture,
            "breach_location": breach_location,
            "breach_time_s": breach_time_s,
            "exposed_bytes": exposed_bytes,
            "exposed_mb": exposed_bytes / (1024 * 1024),
            "exposed_types": list(location_data.keys()),
            "sensitivity_score": round(sensitivity, 4),
            "accessibility_score": accessibility,
            "impact_score": round(impact_score, 2),
            "patients_affected": patients_affected,
        }

    # ------------------------------------------------------------------
    # Comparison over time
    # ------------------------------------------------------------------

    def get_comparison(self) -> dict[str, Any]:
        """Return AS_current / AS_chambers ratio over time.

        Returns
        -------
        dict
            ``time_series``: list of ``{timestamp_s, current_as, chambers_as, ratio}``
            ``latest_ratio``: float
            ``average_ratio``: float
        """
        # Group snapshots by timestamp
        current_by_time: dict[float, float] = {}
        chambers_by_time: dict[float, float] = {}

        for snap in self._snapshots:
            if snap.architecture == "current":
                current_by_time[snap.timestamp_s] = snap.score
            else:
                chambers_by_time[snap.timestamp_s] = snap.score

        # Build time series at all timestamps
        all_times = sorted(set(current_by_time.keys()) | set(chambers_by_time.keys()))
        series: list[dict[str, Any]] = []

        last_current = 0.0
        last_chambers = 0.0

        for ts in all_times:
            cur = current_by_time.get(ts, last_current)
            ch = chambers_by_time.get(ts, last_chambers)
            last_current = cur
            last_chambers = ch
            ratio = (cur / ch) if ch > 0 else (float("inf") if cur > 0 else 0.0)
            series.append({
                "timestamp_s": ts,
                "current_as": round(cur, 2),
                "chambers_as": round(ch, 2),
                "ratio": round(ratio, 4),
            })

        ratios = [s["ratio"] for s in series if s["ratio"] != float("inf")]
        avg_ratio = float(np.mean(ratios)) if ratios else 0.0

        return {
            "time_series": series,
            "latest_ratio": series[-1]["ratio"] if series else 0.0,
            "average_ratio": round(avg_ratio, 4),
            "snapshot_count": len(self._snapshots),
        }

    # ------------------------------------------------------------------
    # Raw snapshot access
    # ------------------------------------------------------------------

    def get_snapshots(self, architecture: str | None = None) -> list[dict[str, Any]]:
        """Return raw snapshots, optionally filtered by architecture.

        Parameters
        ----------
        architecture:
            ``'current'``, ``'chambers'``, or ``None`` for all.
        """
        result: list[dict[str, Any]] = []
        for snap in self._snapshots:
            if architecture is not None and snap.architecture != architecture:
                continue
            result.append({
                "timestamp_s": snap.timestamp_s,
                "architecture": snap.architecture,
                "score": round(snap.score, 2),
                "breakdown": {k: round(v, 2) for k, v in snap.breakdown.items()},
            })
        return result
