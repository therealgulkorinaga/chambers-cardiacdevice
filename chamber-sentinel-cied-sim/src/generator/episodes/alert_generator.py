"""
Device alert condition detection engine.

Evaluates arrhythmia episodes, lead impedance, battery state, pacing
percentages, and capture thresholds against configurable thresholds to
produce clinically-relevant device alerts across all 11 PRD alert types.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.generator.episodes.arrhythmia_generator import ArrhythmiaEpisode


# ---------------------------------------------------------------------------
# Alert types (11 from PRD)
# ---------------------------------------------------------------------------


class AlertType(Enum):
    """All supported device alert types."""

    AT_AF = "AT_AF"
    VT_ATP = "VT_ATP"
    VT_VF_SHOCK = "VT_VF_SHOCK"
    LEAD_IMPEDANCE = "LEAD_IMPEDANCE"
    THRESHOLD_INCREASE = "THRESHOLD_INCREASE"
    BATTERY_ERI = "BATTERY_ERI"
    BATTERY_EOS = "BATTERY_EOS"
    PACING_PCT_CHANGE = "PACING_PCT_CHANGE"
    MAGNET_APPLICATION = "MAGNET_APPLICATION"
    DEVICE_RESET = "DEVICE_RESET"
    TELEMETRY_FAILURE = "TELEMETRY_FAILURE"


# Alert type -> priority mapping
_ALERT_PRIORITIES: dict[str, str] = {
    AlertType.AT_AF.value: "medium",
    AlertType.VT_ATP.value: "high",
    AlertType.VT_VF_SHOCK.value: "critical",
    AlertType.LEAD_IMPEDANCE.value: "high",
    AlertType.THRESHOLD_INCREASE.value: "medium",
    AlertType.BATTERY_ERI.value: "high",
    AlertType.BATTERY_EOS.value: "critical",
    AlertType.PACING_PCT_CHANGE.value: "medium",
    AlertType.MAGNET_APPLICATION.value: "low",
    AlertType.DEVICE_RESET.value: "critical",
    AlertType.TELEMETRY_FAILURE.value: "medium",
}


# ---------------------------------------------------------------------------
# Public data-model types
# ---------------------------------------------------------------------------


@dataclass
class DeviceAlert:
    """A single device alert with metadata and payload."""

    alert_id: str
    alert_type: str
    priority: str  # 'low', 'medium', 'high', 'critical'
    timestamp_s: float
    data: dict[str, Any]
    acknowledged: bool = False
    acknowledged_at: float | None = None

    def acknowledge(self, timestamp_s: float) -> None:
        """Mark this alert as acknowledged."""
        self.acknowledged = True
        self.acknowledged_at = timestamp_s


# ---------------------------------------------------------------------------
# Default detection thresholds
# ---------------------------------------------------------------------------

_DEFAULT_THRESHOLDS: dict[str, Any] = {
    # Lead impedance bounds (ohms)
    "lead_impedance_low": 200.0,
    "lead_impedance_high": 2000.0,
    # Battery
    "battery_eri_voltage": 2.6,
    "battery_eos_voltage": 2.4,
    # Pacing percentage change (absolute %)
    "pacing_pct_change": 20.0,
    # Capture threshold increase (volts)
    "threshold_increase_v": 1.0,
    # AF episode duration threshold for alert (seconds)
    "af_alert_duration_s": 30.0,
    # VT sustained threshold (seconds)
    "vt_sustained_s": 30.0,
}


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class AlertGenerator:
    """
    Evaluates device telemetry data against configurable thresholds
    and emits :class:`DeviceAlert` instances.

    Parameters
    ----------
    detection_thresholds:
        Optional dict overriding any of the default thresholds. Keys
        match those in ``_DEFAULT_THRESHOLDS``.
    """

    def __init__(self, detection_thresholds: dict[str, Any] | None = None) -> None:
        self._thresholds = dict(_DEFAULT_THRESHOLDS)
        if detection_thresholds:
            self._thresholds.update(detection_thresholds)

        # Alert history for de-duplication and audit
        self._alert_history: list[DeviceAlert] = []

        # Cooldown tracking: avoid repeated alerts for the same condition
        # within a short window (seconds).
        self._last_alert_time: dict[str, float] = {}
        self._cooldown_s: float = 300.0  # 5-minute cooldown

    # -- Helpers -----------------------------------------------------------

    def _make_alert(
        self,
        alert_type: str,
        timestamp_s: float,
        data: dict[str, Any],
        priority_override: str | None = None,
    ) -> DeviceAlert:
        """Create and register a new alert."""
        priority = priority_override or _ALERT_PRIORITIES.get(alert_type, "medium")
        alert = DeviceAlert(
            alert_id=uuid.uuid4().hex[:12],
            alert_type=alert_type,
            priority=priority,
            timestamp_s=timestamp_s,
            data=data,
        )
        self._alert_history.append(alert)
        self._last_alert_time[alert_type] = timestamp_s
        return alert

    def _in_cooldown(self, alert_type: str, timestamp_s: float) -> bool:
        """Return ``True`` if the alert type is still in cooldown."""
        last = self._last_alert_time.get(alert_type)
        if last is None:
            return False
        return (timestamp_s - last) < self._cooldown_s

    # -- Episode checks ----------------------------------------------------

    def check_episode(self, episode: ArrhythmiaEpisode) -> DeviceAlert | None:
        """
        Check an arrhythmia episode for alert-worthy conditions.

        Returns
        -------
        DeviceAlert or None
        """
        ep_type = episode.episode_type

        # --- AT/AF alert ---
        if ep_type in ("AF", "AFL", "SVT"):
            if episode.duration_s >= self._thresholds["af_alert_duration_s"]:
                alert_type = AlertType.AT_AF.value
                if self._in_cooldown(alert_type, episode.onset_time_s):
                    return None
                return self._make_alert(
                    alert_type=alert_type,
                    timestamp_s=episode.onset_time_s,
                    data={
                        "episode_id": episode.episode_id,
                        "episode_type": ep_type,
                        "duration_s": episode.duration_s,
                        "max_rate_bpm": episode.max_rate_bpm,
                        "is_sustained": episode.is_sustained,
                    },
                )

        # --- VT treated with ATP ---
        if ep_type == "VT" and episode.terminated_by == "atp":
            alert_type = AlertType.VT_ATP.value
            if self._in_cooldown(alert_type, episode.onset_time_s):
                return None
            return self._make_alert(
                alert_type=alert_type,
                timestamp_s=episode.onset_time_s,
                data={
                    "episode_id": episode.episode_id,
                    "duration_s": episode.duration_s,
                    "max_rate_bpm": episode.max_rate_bpm,
                    "morphology": episode.morphology,
                    "is_sustained": episode.is_sustained,
                },
            )

        # --- VT/VF treated with shock ---
        if ep_type in ("VT", "VF") and episode.terminated_by == "shock":
            alert_type = AlertType.VT_VF_SHOCK.value
            if self._in_cooldown(alert_type, episode.onset_time_s):
                return None
            return self._make_alert(
                alert_type=alert_type,
                timestamp_s=episode.onset_time_s,
                data={
                    "episode_id": episode.episode_id,
                    "episode_type": ep_type,
                    "duration_s": episode.duration_s,
                    "max_rate_bpm": episode.max_rate_bpm,
                    "morphology": episode.morphology,
                },
            )

        return None

    # -- Lead checks -------------------------------------------------------

    def check_lead(self, lead_id: str, impedance: float) -> DeviceAlert | None:
        """
        Check lead impedance for out-of-range values.

        Parameters
        ----------
        lead_id:
            Identifier of the lead being checked.
        impedance:
            Measured impedance in ohms.

        Returns
        -------
        DeviceAlert or None
        """
        low = self._thresholds["lead_impedance_low"]
        high = self._thresholds["lead_impedance_high"]

        if impedance < low or impedance > high:
            alert_type = AlertType.LEAD_IMPEDANCE.value
            # Use lead_id as a sub-key for cooldown to allow per-lead alerts
            cooldown_key = f"{alert_type}_{lead_id}"
            last = self._last_alert_time.get(cooldown_key)
            if last is not None and (impedance - last) < self._cooldown_s:
                # Use a simple time-based proxy; callers must supply monotonic timestamps.
                pass  # fall through

            condition = "high" if impedance > high else "low"
            alert = self._make_alert(
                alert_type=alert_type,
                timestamp_s=0.0,  # caller should set via data or external timestamp
                data={
                    "lead_id": lead_id,
                    "impedance_ohms": round(impedance, 1),
                    "condition": condition,
                    "threshold_low": low,
                    "threshold_high": high,
                },
                priority_override="critical" if condition == "low" else "high",
            )
            self._last_alert_time[cooldown_key] = 0.0
            return alert

        return None

    # -- Battery checks ----------------------------------------------------

    def check_battery(self, battery_state: Any) -> DeviceAlert | None:
        """
        Check battery state for ERI or EOS conditions.

        Parameters
        ----------
        battery_state:
            An object with ``voltage_v`` and ``stage`` attributes
            (e.g. :class:`BatteryState`).

        Returns
        -------
        DeviceAlert or None
        """
        voltage = battery_state.voltage_v
        stage = battery_state.stage

        if stage == "EOS" and voltage <= self._thresholds["battery_eos_voltage"]:
            alert_type = AlertType.BATTERY_EOS.value
            if self._in_cooldown(alert_type, battery_state.elapsed_hours * 3600.0):
                return None
            return self._make_alert(
                alert_type=alert_type,
                timestamp_s=battery_state.elapsed_hours * 3600.0,
                data={
                    "voltage_v": voltage,
                    "impedance_ohms": battery_state.impedance_ohms,
                    "stage": stage,
                    "projected_longevity_days": battery_state.projected_longevity_days,
                },
            )

        if stage == "ERI" and voltage <= self._thresholds["battery_eri_voltage"]:
            alert_type = AlertType.BATTERY_ERI.value
            if self._in_cooldown(alert_type, battery_state.elapsed_hours * 3600.0):
                return None
            return self._make_alert(
                alert_type=alert_type,
                timestamp_s=battery_state.elapsed_hours * 3600.0,
                data={
                    "voltage_v": voltage,
                    "impedance_ohms": battery_state.impedance_ohms,
                    "stage": stage,
                    "projected_longevity_days": battery_state.projected_longevity_days,
                },
            )

        return None

    # -- Pacing percentage change ------------------------------------------

    def check_pacing_change(
        self,
        current_pct: float,
        prev_pct: float,
        timestamp_s: float = 0.0,
    ) -> DeviceAlert | None:
        """
        Check for a significant change in pacing percentage.

        Parameters
        ----------
        current_pct:
            Current pacing percentage (0-100).
        prev_pct:
            Previous pacing percentage (0-100).
        timestamp_s:
            Timestamp of the measurement.

        Returns
        -------
        DeviceAlert or None
        """
        delta = abs(current_pct - prev_pct)
        threshold = self._thresholds["pacing_pct_change"]

        if delta >= threshold:
            alert_type = AlertType.PACING_PCT_CHANGE.value
            if self._in_cooldown(alert_type, timestamp_s):
                return None
            direction = "increase" if current_pct > prev_pct else "decrease"
            return self._make_alert(
                alert_type=alert_type,
                timestamp_s=timestamp_s,
                data={
                    "current_pct": round(current_pct, 1),
                    "previous_pct": round(prev_pct, 1),
                    "delta_pct": round(delta, 1),
                    "direction": direction,
                },
            )

        return None

    # -- Capture threshold change ------------------------------------------

    def check_threshold_change(
        self,
        current_v: float,
        baseline_v: float,
        timestamp_s: float = 0.0,
    ) -> DeviceAlert | None:
        """
        Check for a significant increase in capture threshold.

        Parameters
        ----------
        current_v:
            Current capture threshold in volts.
        baseline_v:
            Baseline capture threshold in volts.
        timestamp_s:
            Timestamp of the measurement.

        Returns
        -------
        DeviceAlert or None
        """
        increase = current_v - baseline_v

        if increase >= self._thresholds["threshold_increase_v"]:
            alert_type = AlertType.THRESHOLD_INCREASE.value
            if self._in_cooldown(alert_type, timestamp_s):
                return None
            return self._make_alert(
                alert_type=alert_type,
                timestamp_s=timestamp_s,
                data={
                    "current_v": round(current_v, 3),
                    "baseline_v": round(baseline_v, 3),
                    "increase_v": round(increase, 3),
                },
            )

        return None

    # -- Magnet / reset / telemetry (event-driven) -------------------------

    def report_magnet_application(self, timestamp_s: float) -> DeviceAlert:
        """Generate a magnet-application alert."""
        return self._make_alert(
            alert_type=AlertType.MAGNET_APPLICATION.value,
            timestamp_s=timestamp_s,
            data={"event": "magnet_applied"},
        )

    def report_device_reset(
        self,
        timestamp_s: float,
        reason: str = "unknown",
    ) -> DeviceAlert:
        """Generate a device-reset alert."""
        return self._make_alert(
            alert_type=AlertType.DEVICE_RESET.value,
            timestamp_s=timestamp_s,
            data={"reason": reason},
        )

    def report_telemetry_failure(
        self,
        timestamp_s: float,
        details: str = "",
    ) -> DeviceAlert:
        """Generate a telemetry-failure alert."""
        return self._make_alert(
            alert_type=AlertType.TELEMETRY_FAILURE.value,
            timestamp_s=timestamp_s,
            data={"details": details},
        )

    # -- History -----------------------------------------------------------

    def get_alert_history(self) -> list[DeviceAlert]:
        """Return the full alert history."""
        return list(self._alert_history)

    def get_unacknowledged_alerts(self) -> list[DeviceAlert]:
        """Return only un-acknowledged alerts."""
        return [a for a in self._alert_history if not a.acknowledged]

    def get_alert_counts(self) -> dict[str, int]:
        """Return a count of alerts by type."""
        counts: dict[str, int] = {}
        for alert in self._alert_history:
            counts[alert.alert_type] = counts.get(alert.alert_type, 0) + 1
        return counts
