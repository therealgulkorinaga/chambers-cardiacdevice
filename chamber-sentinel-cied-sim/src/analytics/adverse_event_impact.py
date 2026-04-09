"""
Adverse event data-loss impact analyzer.

Given a CIED adverse event (lead fracture, inappropriate shock, patient
death, etc.), determines what clinical data is available vs. lost under
the burn-by-default architecture.  Key outputs:

- **data_available**: records still on the relay or device at event time
- **data_lost**: records already burned before the event was detected
- **data_on_device**: records retrievable from the implanted device
- **loss_rate**: fraction of total generated data that is irrecoverable
- **investigation_adequacy**: subjective adequacy score (0-1)

Also provides burn-window sweep matrices and critical-scenario analysis
(e.g. patient death followed by delayed discovery).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.stats import lognorm


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SECONDS_PER_HOUR = 3600.0
_SECONDS_PER_DAY = 86_400.0

# Investigation adequacy thresholds -- the fraction of pre-event data that
# must be available to support an adequate investigation.
_ADEQUACY_THRESHOLDS: dict[str, float] = {
    "lead_fracture": 0.60,
    "lead_dislodgement": 0.50,
    "insulation_breach": 0.70,       # need trend data
    "generator_malfunction": 0.80,   # high bar
    "unexpected_battery_eol": 0.50,
    "inappropriate_shock": 0.90,     # critical -- need full episode data
    "patient_death_device": 0.95,    # highest bar
}

# Data that remains on the implanted device (not relay-dependent)
_ON_DEVICE_DATA_TYPES: set[str] = {
    "iegm",
    "episode",
    "therapy",
    "device_status",
    "trends",
}

# Weight of each data type for investigation adequacy
_INVESTIGATION_WEIGHTS: dict[str, float] = {
    "iegm": 0.30,
    "episode": 0.25,
    "therapy": 0.20,
    "trends": 0.10,
    "device_status": 0.10,
    "activity": 0.05,
}


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class AdverseEventImpactAnalyzer:
    """Analyzes data availability around adverse events under burn-by-default.

    For each event, the analyzer partitions pre-event data into:

    1. **On relay** -- still present because the burn window has not elapsed
    2. **On device** -- stored in device memory regardless of relay state
    3. **Burned** -- destroyed by the relay before the event was detected
    4. **Lost** -- burned AND not on the device

    The *investigation_adequacy* score reflects how well the available data
    supports a regulatory/clinical investigation, weighted by data-type
    importance.
    """

    def __init__(self, device_memory_window_s: float = 90 * _SECONDS_PER_DAY) -> None:
        """
        Parameters
        ----------
        device_memory_window_s:
            How far back the implanted device retains data in its limited
            on-board memory.  Default 90 days.
        """
        self._device_memory_window_s = device_memory_window_s
        self._analyses: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Single-event analysis
    # ------------------------------------------------------------------

    def analyze_event(
        self,
        event_type: str,
        event_time_s: float,
        detection_delay_s: float,
        burn_window_s: float,
        data_generated_before_event: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Analyze data availability after an adverse event.

        Parameters
        ----------
        event_type:
            Type of adverse event (e.g. ``'lead_fracture'``).
        event_time_s:
            Absolute time of the event in seconds.
        detection_delay_s:
            Delay between event occurrence and detection/investigation
            initiation (seconds).
        burn_window_s:
            Active burn window in seconds.
        data_generated_before_event:
            List of data records that were generated before the event.
            Each dict must have at minimum:
            ``{'timestamp_s': float, 'data_type': str, 'size_bytes': int}``.
            May also include ``'on_device': bool``.

        Returns
        -------
        dict
            ``data_available``, ``data_lost``, ``data_on_device``,
            ``loss_rate``, ``investigation_adequacy``, ``details``.
        """
        detection_time_s = event_time_s + detection_delay_s

        # Partition data records
        data_available: list[dict[str, Any]] = []
        data_on_device: list[dict[str, Any]] = []
        data_lost: list[dict[str, Any]] = []
        data_burned_but_on_device: list[dict[str, Any]] = []

        total_bytes = 0
        available_bytes = 0
        lost_bytes = 0
        on_device_bytes = 0

        for record in data_generated_before_event:
            ts = record["timestamp_s"]
            size = record.get("size_bytes", 100)
            dtype = record.get("data_type", "unknown")
            total_bytes += size

            # Was this record on the device at detection time?
            is_on_device = (
                record.get("on_device", dtype in _ON_DEVICE_DATA_TYPES)
                and (detection_time_s - ts) <= self._device_memory_window_s
            )

            # Was this record still on the relay at detection time?
            # A record is on the relay if it was ingested less than
            # burn_window_s ago at the time of detection.
            age_at_detection = detection_time_s - ts
            is_on_relay = age_at_detection <= burn_window_s

            if is_on_relay:
                data_available.append(record)
                available_bytes += size
            elif is_on_device:
                # Burned from relay but recoverable from device
                data_burned_but_on_device.append(record)
                data_on_device.append(record)
                on_device_bytes += size
                available_bytes += size  # still accessible
            else:
                # Burned from relay AND not on device => lost
                data_lost.append(record)
                lost_bytes += size

            if is_on_device and is_on_relay:
                data_on_device.append(record)
                on_device_bytes += size

        loss_rate = lost_bytes / total_bytes if total_bytes > 0 else 0.0

        # Investigation adequacy: weighted score of available data types
        adequacy = self._compute_adequacy(
            event_type=event_type,
            available_records=data_available + data_burned_but_on_device,
            total_records=data_generated_before_event,
        )

        result = {
            "event_type": event_type,
            "event_time_s": event_time_s,
            "detection_delay_s": detection_delay_s,
            "detection_time_s": detection_time_s,
            "burn_window_s": burn_window_s,
            "total_records": len(data_generated_before_event),
            "total_bytes": total_bytes,
            "available_records": len(data_available) + len(data_burned_but_on_device),
            "available_bytes": available_bytes,
            "lost_records": len(data_lost),
            "lost_bytes": lost_bytes,
            "on_device_records": len(data_on_device),
            "on_device_bytes": on_device_bytes,
            "loss_rate": round(loss_rate, 6),
            "investigation_adequacy": round(adequacy, 4),
            "data_available": data_available + data_burned_but_on_device,
            "data_lost": data_lost,
            "data_on_device": data_on_device,
            "details": {
                "records_on_relay": len(data_available),
                "records_burned_but_on_device": len(data_burned_but_on_device),
                "records_irrecoverable": len(data_lost),
                "adequacy_threshold": _ADEQUACY_THRESHOLDS.get(event_type, 0.70),
                "meets_adequacy": adequacy >= _ADEQUACY_THRESHOLDS.get(event_type, 0.70),
            },
        }

        self._analyses.append(result)
        return result

    # ------------------------------------------------------------------
    # Burn-window sweep
    # ------------------------------------------------------------------

    def sweep_burn_windows(
        self,
        event_type: str,
        event_time_s: float,
        detection_delays: list[float],
        burn_windows: list[float],
        data_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Generate a loss-rate matrix over detection delays and burn windows.

        Parameters
        ----------
        event_type:
            Adverse event type.
        event_time_s:
            Time of the event.
        detection_delays:
            List of detection delay values in seconds.
        burn_windows:
            List of burn-window values in seconds.
        data_state:
            Must contain ``'records'``: list of data-record dicts (same
            format as ``data_generated_before_event``).

        Returns
        -------
        list[dict]
            One entry per (detection_delay, burn_window) pair with
            ``detection_delay_s``, ``burn_window_s``, ``loss_rate``,
            ``investigation_adequacy``, ``available_bytes``, ``lost_bytes``.
        """
        records: list[dict[str, Any]] = data_state.get("records", [])
        # Filter to records before the event
        pre_event = [r for r in records if r.get("timestamp_s", 0) <= event_time_s]

        results: list[dict[str, Any]] = []

        for delay_s in detection_delays:
            for window_s in burn_windows:
                analysis = self.analyze_event(
                    event_type=event_type,
                    event_time_s=event_time_s,
                    detection_delay_s=delay_s,
                    burn_window_s=window_s,
                    data_generated_before_event=pre_event,
                )
                results.append({
                    "detection_delay_s": delay_s,
                    "detection_delay_hours": delay_s / _SECONDS_PER_HOUR,
                    "burn_window_s": window_s,
                    "burn_window_hours": window_s / _SECONDS_PER_HOUR,
                    "loss_rate": analysis["loss_rate"],
                    "investigation_adequacy": analysis["investigation_adequacy"],
                    "available_bytes": analysis["available_bytes"],
                    "lost_bytes": analysis["lost_bytes"],
                    "meets_adequacy": analysis["details"]["meets_adequacy"],
                })

        return results

    # ------------------------------------------------------------------
    # Critical scenario: patient death
    # ------------------------------------------------------------------

    def get_critical_scenario(
        self,
        patient_death_time_s: float,
        discovery_delay_distribution: dict[str, float],
        burn_window_s: float,
        data_state: dict[str, Any],
        n_simulations: int = 5_000,
        rng_seed: int | None = None,
    ) -> dict[str, Any]:
        """Analyze the worst-case scenario: patient death with delayed discovery.

        Models the discovery delay as a log-normal distribution (coroner
        referral, weekend effects, etc.) and computes the distribution of
        data loss rates across Monte-Carlo samples.

        Parameters
        ----------
        patient_death_time_s:
            Simulation time of patient death.
        discovery_delay_distribution:
            ``{'median_hours': float, 'sigma': float}``
            for a log-normal discovery delay.
        burn_window_s:
            Active burn window in seconds.
        data_state:
            Must contain ``'records'``: list of data-record dicts.
        n_simulations:
            Monte-Carlo sample count.
        rng_seed:
            Optional seed.

        Returns
        -------
        dict
            ``mean_loss_rate``, ``median_loss_rate``, ``p95_loss_rate``,
            ``worst_case_loss_rate``, ``mean_adequacy``,
            ``fraction_meeting_adequacy``, ``loss_rate_distribution``.
        """
        rng = np.random.default_rng(rng_seed)

        median_hours = discovery_delay_distribution.get("median_hours", 24.0)
        sigma = discovery_delay_distribution.get("sigma", 1.0)
        median_s = median_hours * _SECONDS_PER_HOUR

        # Sample discovery delays
        delays = lognorm.rvs(
            s=sigma,
            scale=median_s,
            size=n_simulations,
            random_state=rng,
        )

        records: list[dict[str, Any]] = data_state.get("records", [])
        pre_event = [r for r in records if r.get("timestamp_s", 0) <= patient_death_time_s]

        loss_rates: list[float] = []
        adequacies: list[float] = []
        meets_adequacy_count = 0

        for delay_s in delays:
            detection_time = patient_death_time_s + float(delay_s)

            # Compute loss inline for performance (avoid full analyze_event overhead)
            total_bytes = 0
            lost_bytes = 0
            available_by_type: dict[str, int] = {}
            total_by_type: dict[str, int] = {}

            for record in pre_event:
                ts = record["timestamp_s"]
                size = record.get("size_bytes", 100)
                dtype = record.get("data_type", "unknown")
                total_bytes += size
                total_by_type[dtype] = total_by_type.get(dtype, 0) + size

                age_at_detection = detection_time - ts
                on_relay = age_at_detection <= burn_window_s
                on_device = (
                    record.get("on_device", dtype in _ON_DEVICE_DATA_TYPES)
                    and age_at_detection <= self._device_memory_window_s
                )

                if on_relay or on_device:
                    available_by_type[dtype] = available_by_type.get(dtype, 0) + size
                else:
                    lost_bytes += size

            lr = lost_bytes / total_bytes if total_bytes > 0 else 0.0
            loss_rates.append(lr)

            # Adequacy
            adeq = self._compute_adequacy_from_type_dicts(
                "patient_death_device",
                available_by_type,
                total_by_type,
            )
            adequacies.append(adeq)
            threshold = _ADEQUACY_THRESHOLDS.get("patient_death_device", 0.95)
            if adeq >= threshold:
                meets_adequacy_count += 1

        loss_arr = np.array(loss_rates)
        adeq_arr = np.array(adequacies)

        return {
            "event_type": "patient_death_device",
            "patient_death_time_s": patient_death_time_s,
            "burn_window_s": burn_window_s,
            "burn_window_hours": burn_window_s / _SECONDS_PER_HOUR,
            "n_simulations": n_simulations,
            "discovery_delay_median_hours": median_hours,
            "discovery_delay_sigma": sigma,
            "mean_loss_rate": round(float(np.mean(loss_arr)), 6),
            "median_loss_rate": round(float(np.median(loss_arr)), 6),
            "p05_loss_rate": round(float(np.percentile(loss_arr, 5)), 6),
            "p95_loss_rate": round(float(np.percentile(loss_arr, 95)), 6),
            "worst_case_loss_rate": round(float(np.max(loss_arr)), 6),
            "best_case_loss_rate": round(float(np.min(loss_arr)), 6),
            "mean_adequacy": round(float(np.mean(adeq_arr)), 4),
            "median_adequacy": round(float(np.median(adeq_arr)), 4),
            "fraction_meeting_adequacy": round(meets_adequacy_count / n_simulations, 4),
            "adequacy_threshold": _ADEQUACY_THRESHOLDS.get("patient_death_device", 0.95),
        }

    # ------------------------------------------------------------------
    # Analysis history
    # ------------------------------------------------------------------

    def get_analyses(self) -> list[dict[str, Any]]:
        """Return all event analyses (without the full record lists)."""
        summaries: list[dict[str, Any]] = []
        for a in self._analyses:
            summary = {k: v for k, v in a.items()
                       if k not in ("data_available", "data_lost", "data_on_device")}
            summaries.append(summary)
        return summaries

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_adequacy(
        self,
        event_type: str,
        available_records: list[dict[str, Any]],
        total_records: list[dict[str, Any]],
    ) -> float:
        """Compute weighted investigation-adequacy score."""
        # Aggregate by data type
        available_by_type: dict[str, int] = {}
        total_by_type: dict[str, int] = {}

        for r in available_records:
            dtype = r.get("data_type", "unknown")
            size = r.get("size_bytes", 100)
            available_by_type[dtype] = available_by_type.get(dtype, 0) + size

        for r in total_records:
            dtype = r.get("data_type", "unknown")
            size = r.get("size_bytes", 100)
            total_by_type[dtype] = total_by_type.get(dtype, 0) + size

        return self._compute_adequacy_from_type_dicts(
            event_type, available_by_type, total_by_type,
        )

    @staticmethod
    def _compute_adequacy_from_type_dicts(
        event_type: str,
        available_by_type: dict[str, int],
        total_by_type: dict[str, int],
    ) -> float:
        """Compute adequacy from pre-aggregated type dicts."""
        weighted_sum = 0.0
        weight_sum = 0.0

        for dtype, weight in _INVESTIGATION_WEIGHTS.items():
            total = total_by_type.get(dtype, 0)
            if total == 0:
                # If this type was never generated, it doesn't affect adequacy
                continue
            available = available_by_type.get(dtype, 0)
            fraction = min(1.0, available / total)
            weighted_sum += weight * fraction
            weight_sum += weight

        if weight_sum == 0:
            return 0.0
        return weighted_sum / weight_sum
