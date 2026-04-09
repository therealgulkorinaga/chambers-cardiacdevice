"""
Persistence volume tracker for architecture comparison.

Tracks V_current(T) and V_chambers(T) -- the cumulative data-at-rest volume
over simulated time for the current (persist-by-default) and Chambers
(burn-by-default) architectures respectively.

Provides:
- Time-series snapshots with breakdowns by layer/world, patient, data type
- Point-in-time V_current / V_chambers ratio
- Comparison tables at arbitrary day-marks
- Linear extrapolation to 10-year projections
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


# ---------------------------------------------------------------------------
# Snapshot data classes
# ---------------------------------------------------------------------------


@dataclass
class CurrentArchSnapshot:
    """A single point-in-time snapshot of current-arch data volume."""

    timestamp_s: float
    total_bytes: int
    by_layer: dict[str, int] = field(default_factory=dict)
    by_patient: dict[str, int] = field(default_factory=dict)
    by_type: dict[str, int] = field(default_factory=dict)


@dataclass
class ChambersArchSnapshot:
    """A single point-in-time snapshot of Chambers-arch data volume."""

    timestamp_s: float
    total_bytes: int
    by_world: dict[str, int] = field(default_factory=dict)
    by_patient: dict[str, int] = field(default_factory=dict)
    by_type: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SECONDS_PER_DAY = 86_400.0
_SECONDS_PER_YEAR = 365.25 * _SECONDS_PER_DAY
_TEN_YEARS_S = 10.0 * _SECONDS_PER_YEAR


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


class PersistenceTracker:
    """Tracks V_current(T) and V_chambers(T) over time.

    Each ``record_*`` call appends a snapshot with a timestamp and
    a breakdown of persisted bytes.  Analytical methods operate on
    the accumulated snapshot lists.
    """

    def __init__(self) -> None:
        self._current_snapshots: list[CurrentArchSnapshot] = []
        self._chambers_snapshots: list[ChambersArchSnapshot] = []

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_current_arch(
        self,
        timestamp_s: float,
        total_bytes: int,
        by_layer: dict[str, int],
        by_patient: dict[str, int],
        by_type: dict[str, int],
    ) -> None:
        """Record a current-architecture volume snapshot.

        Parameters
        ----------
        timestamp_s:
            Simulation clock time in seconds.
        total_bytes:
            Total persisted bytes at this instant.
        by_layer:
            Bytes broken down by architectural layer
            (``device``, ``transmitter``, ``cloud``, ``portal``, ``aggregate``).
        by_patient:
            Bytes broken down by patient ID.
        by_type:
            Bytes broken down by data type
            (``iegm``, ``episode``, ``therapy``, ``trends``, etc.).
        """
        self._current_snapshots.append(
            CurrentArchSnapshot(
                timestamp_s=timestamp_s,
                total_bytes=total_bytes,
                by_layer=dict(by_layer),
                by_patient=dict(by_patient),
                by_type=dict(by_type),
            )
        )

    def record_chambers_arch(
        self,
        timestamp_s: float,
        total_bytes: int,
        by_world: dict[str, int],
        by_patient: dict[str, int],
        by_type: dict[str, int],
    ) -> None:
        """Record a Chambers-architecture volume snapshot.

        Parameters
        ----------
        timestamp_s:
            Simulation clock time in seconds.
        total_bytes:
            Total persisted bytes at this instant (relay + device).
        by_world:
            Bytes broken down by typed world
            (``clinical``, ``patient``, ``research``, ``device_maintenance``,
            ``safety_investigation``).
        by_patient:
            Bytes broken down by patient ID.
        by_type:
            Bytes broken down by data type.
        """
        self._chambers_snapshots.append(
            ChambersArchSnapshot(
                timestamp_s=timestamp_s,
                total_bytes=total_bytes,
                by_world=dict(by_world),
                by_patient=dict(by_patient),
                by_type=dict(by_type),
            )
        )

    # ------------------------------------------------------------------
    # Time-series retrieval
    # ------------------------------------------------------------------

    def get_time_series(self) -> dict[str, list[dict[str, Any]]]:
        """Return both time-series as dicts suitable for charting.

        Returns
        -------
        dict
            ``{'current': [...], 'chambers': [...]}`` where each entry has
            ``timestamp_s``, ``total_bytes``, ``total_mb``, ``total_gb``,
            and breakdown dicts.
        """
        current_series: list[dict[str, Any]] = []
        for snap in self._current_snapshots:
            current_series.append({
                "timestamp_s": snap.timestamp_s,
                "timestamp_days": snap.timestamp_s / _SECONDS_PER_DAY,
                "total_bytes": snap.total_bytes,
                "total_mb": snap.total_bytes / (1024 * 1024),
                "total_gb": snap.total_bytes / (1024 ** 3),
                "by_layer": snap.by_layer,
                "by_patient": snap.by_patient,
                "by_type": snap.by_type,
            })

        chambers_series: list[dict[str, Any]] = []
        for snap in self._chambers_snapshots:
            chambers_series.append({
                "timestamp_s": snap.timestamp_s,
                "timestamp_days": snap.timestamp_s / _SECONDS_PER_DAY,
                "total_bytes": snap.total_bytes,
                "total_mb": snap.total_bytes / (1024 * 1024),
                "total_gb": snap.total_bytes / (1024 ** 3),
                "by_world": snap.by_world,
                "by_patient": snap.by_patient,
                "by_type": snap.by_type,
            })

        return {"current": current_series, "chambers": chambers_series}

    # ------------------------------------------------------------------
    # Ratio
    # ------------------------------------------------------------------

    def get_ratio(self, timestamp_s: float | None = None) -> float:
        """Return V_current / V_chambers at the given time.

        If *timestamp_s* is ``None``, uses the latest available snapshots.
        Uses the nearest snapshot at or before *timestamp_s* for each
        architecture.  Returns ``float('inf')`` if V_chambers is zero,
        and ``0.0`` if no data is recorded for either architecture.

        Parameters
        ----------
        timestamp_s:
            Point in simulated time.  ``None`` for latest.

        Returns
        -------
        float
        """
        v_current = self._interpolate_bytes(self._current_snapshots, timestamp_s)
        v_chambers = self._interpolate_bytes_chambers(self._chambers_snapshots, timestamp_s)

        if v_chambers == 0:
            return float("inf") if v_current > 0 else 0.0
        return v_current / v_chambers

    # ------------------------------------------------------------------
    # Comparison at specified day-marks
    # ------------------------------------------------------------------

    def get_comparison_at_times(self, times_days: list[int]) -> list[dict[str, Any]]:
        """Compare volumes at specified day marks.

        Parameters
        ----------
        times_days:
            List of day values (e.g. ``[30, 90, 180, 365]``).

        Returns
        -------
        list[dict]
            One dict per day with keys: ``day``, ``current_bytes``,
            ``chambers_bytes``, ``ratio``, ``savings_pct``,
            ``current_mb``, ``chambers_mb``.
        """
        results: list[dict[str, Any]] = []
        for day in times_days:
            ts = day * _SECONDS_PER_DAY
            v_cur = self._interpolate_bytes(self._current_snapshots, ts)
            v_ch = self._interpolate_bytes_chambers(self._chambers_snapshots, ts)
            ratio = (v_cur / v_ch) if v_ch > 0 else (float("inf") if v_cur > 0 else 0.0)
            savings = (1.0 - v_ch / v_cur) * 100.0 if v_cur > 0 else 0.0
            results.append({
                "day": day,
                "current_bytes": v_cur,
                "chambers_bytes": v_ch,
                "current_mb": v_cur / (1024 * 1024),
                "chambers_mb": v_ch / (1024 * 1024),
                "ratio": round(ratio, 4),
                "savings_pct": round(savings, 2),
            })
        return results

    # ------------------------------------------------------------------
    # 10-year projection
    # ------------------------------------------------------------------

    def get_projected_10_year(self) -> dict[str, Any]:
        """Linearly extrapolate current trends to 10 years.

        Fits a least-squares line to each architecture's ``(time, bytes)``
        series and projects to ``10 * 365.25 * 86400`` seconds.

        Returns
        -------
        dict
            ``current_10yr_bytes``, ``chambers_10yr_bytes``, ``ratio``,
            ``current_slope_bytes_per_day``, ``chambers_slope_bytes_per_day``,
            ``current_10yr_gb``, ``chambers_10yr_gb``.
        """
        cur_slope, cur_intercept = self._fit_linear(self._current_snapshots)
        ch_slope, ch_intercept = self._fit_linear_chambers(self._chambers_snapshots)

        cur_10yr = max(0.0, cur_slope * _TEN_YEARS_S + cur_intercept)
        ch_10yr = max(0.0, ch_slope * _TEN_YEARS_S + ch_intercept)

        ratio = (cur_10yr / ch_10yr) if ch_10yr > 0 else (float("inf") if cur_10yr > 0 else 0.0)

        return {
            "current_10yr_bytes": int(cur_10yr),
            "chambers_10yr_bytes": int(ch_10yr),
            "current_10yr_gb": cur_10yr / (1024 ** 3),
            "chambers_10yr_gb": ch_10yr / (1024 ** 3),
            "ratio": round(ratio, 4),
            "savings_pct": round((1.0 - ch_10yr / cur_10yr) * 100.0, 2) if cur_10yr > 0 else 0.0,
            "current_slope_bytes_per_day": cur_slope * _SECONDS_PER_DAY,
            "chambers_slope_bytes_per_day": ch_slope * _SECONDS_PER_DAY,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _interpolate_bytes(
        snapshots: list[CurrentArchSnapshot],
        timestamp_s: float | None,
    ) -> int:
        """Find the total_bytes at *timestamp_s* via linear interpolation."""
        if not snapshots:
            return 0
        if timestamp_s is None:
            return snapshots[-1].total_bytes

        # Find bracketing snapshots
        if timestamp_s <= snapshots[0].timestamp_s:
            return snapshots[0].total_bytes
        if timestamp_s >= snapshots[-1].timestamp_s:
            return snapshots[-1].total_bytes

        # Binary search for the interval
        lo, hi = 0, len(snapshots) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if snapshots[mid].timestamp_s <= timestamp_s:
                lo = mid
            else:
                hi = mid

        s0, s1 = snapshots[lo], snapshots[hi]
        dt = s1.timestamp_s - s0.timestamp_s
        if dt == 0:
            return s0.total_bytes
        frac = (timestamp_s - s0.timestamp_s) / dt
        return int(s0.total_bytes + frac * (s1.total_bytes - s0.total_bytes))

    @staticmethod
    def _interpolate_bytes_chambers(
        snapshots: list[ChambersArchSnapshot],
        timestamp_s: float | None,
    ) -> int:
        """Find the total_bytes at *timestamp_s* via linear interpolation (Chambers)."""
        if not snapshots:
            return 0
        if timestamp_s is None:
            return snapshots[-1].total_bytes

        if timestamp_s <= snapshots[0].timestamp_s:
            return snapshots[0].total_bytes
        if timestamp_s >= snapshots[-1].timestamp_s:
            return snapshots[-1].total_bytes

        lo, hi = 0, len(snapshots) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if snapshots[mid].timestamp_s <= timestamp_s:
                lo = mid
            else:
                hi = mid

        s0, s1 = snapshots[lo], snapshots[hi]
        dt = s1.timestamp_s - s0.timestamp_s
        if dt == 0:
            return s0.total_bytes
        frac = (timestamp_s - s0.timestamp_s) / dt
        return int(s0.total_bytes + frac * (s1.total_bytes - s0.total_bytes))

    @staticmethod
    def _fit_linear(
        snapshots: list[CurrentArchSnapshot],
    ) -> tuple[float, float]:
        """Least-squares linear fit on ``(timestamp_s, total_bytes)``."""
        if len(snapshots) < 2:
            if snapshots:
                return 0.0, float(snapshots[0].total_bytes)
            return 0.0, 0.0

        times = np.array([s.timestamp_s for s in snapshots], dtype=np.float64)
        values = np.array([s.total_bytes for s in snapshots], dtype=np.float64)

        # np.polyfit returns [slope, intercept] for degree 1
        coeffs = np.polyfit(times, values, 1)
        return float(coeffs[0]), float(coeffs[1])

    @staticmethod
    def _fit_linear_chambers(
        snapshots: list[ChambersArchSnapshot],
    ) -> tuple[float, float]:
        """Least-squares linear fit on ``(timestamp_s, total_bytes)`` for Chambers."""
        if len(snapshots) < 2:
            if snapshots:
                return 0.0, float(snapshots[0].total_bytes)
            return 0.0, 0.0

        times = np.array([s.timestamp_s for s in snapshots], dtype=np.float64)
        values = np.array([s.total_bytes for s in snapshots], dtype=np.float64)

        coeffs = np.polyfit(times, values, 1)
        return float(coeffs[0]), float(coeffs[1])
