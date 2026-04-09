"""Layer 4: Clinician Portal — physician access to processed reports and alerts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class AlertReview:
    """A clinician's review/acknowledgment of an alert."""
    alert_record_id: str
    patient_id: str
    alert_type: str
    priority: str
    generated_at_s: float
    delivered_at_s: float
    acknowledged_at_s: float | None = None
    reviewed_by: str = ""
    action_taken: str = ""  # 'noted', 'scheduled_followup', 'immediate_action', 'exported_to_emr'


class ClinicianPortal:
    """Layer 4: Simulates clinician access to the manufacturer portal.

    Models:
    - Login frequency (configurable)
    - Alert queue processing with priority-based acknowledgment latency
    - Historical data access (always available — manufacturer cloud retains all)
    - EMR export
    """

    def __init__(
        self,
        clinician_id: str = "DR-001",
        cloud: Any = None,
        rng: np.random.Generator | None = None,
        login_frequency_hours: float = 24.0,
    ) -> None:
        self.clinician_id = clinician_id
        self.cloud = cloud  # Reference to ManufacturerCloud
        self.rng = rng or np.random.default_rng()
        self.login_frequency_hours = login_frequency_hours

        # Alert acknowledgment latency distributions (LogNormal, in hours)
        # (µ, σ) for LogNormal distribution
        self._ack_latency_params: dict[str, tuple[float, float]] = {
            "critical": (2.0, 1.0),
            "high": (8.0, 4.0),
            "medium": (48.0, 24.0),
            "low": (168.0, 72.0),
        }

        # State
        self._last_login_s = 0.0
        self._alert_queue: list[AlertReview] = []
        self._reviews: list[AlertReview] = []
        self._total_logins = 0
        self._total_alerts_reviewed = 0
        self._emr_exports = 0

        # Assigned patients
        self._assigned_patients: set[str] = set()

    def assign_patient(self, patient_id: str) -> None:
        """Assign a patient to this clinician."""
        self._assigned_patients.add(patient_id)

    def unassign_patient(self, patient_id: str) -> None:
        """Remove patient assignment (provider transition)."""
        self._assigned_patients.discard(patient_id)

    def deliver_alert(self, alert_record_id: str, patient_id: str,
                      alert_type: str, priority: str, timestamp_s: float) -> AlertReview:
        """Deliver an alert to the clinician's queue."""
        review = AlertReview(
            alert_record_id=alert_record_id,
            patient_id=patient_id,
            alert_type=alert_type,
            priority=priority,
            generated_at_s=timestamp_s,
            delivered_at_s=timestamp_s,
        )
        self._alert_queue.append(review)
        return review

    def simulate_review_cycle(self, current_time_s: float) -> list[AlertReview]:
        """Simulate the clinician reviewing their alert queue.

        Called periodically based on login_frequency. Processes alerts
        with priority-dependent acknowledgment latency.
        """
        acknowledged: list[AlertReview] = []

        for review in list(self._alert_queue):
            # Calculate expected ack time based on priority
            latency_mean, latency_std = self._ack_latency_params.get(
                review.priority, (48.0, 24.0)
            )
            # LogNormal sample
            ack_latency_hours = self.rng.lognormal(
                np.log(latency_mean), latency_std / latency_mean
            )
            ack_latency_s = ack_latency_hours * 3600

            expected_ack_time = review.delivered_at_s + ack_latency_s

            if current_time_s >= expected_ack_time:
                # Clinician acknowledges this alert
                review.acknowledged_at_s = expected_ack_time
                review.reviewed_by = self.clinician_id
                review.action_taken = self._determine_action(review.priority)

                # Acknowledge in cloud
                if self.cloud is not None:
                    self.cloud.acknowledge_alert(review.alert_record_id, expected_ack_time)

                self._alert_queue.remove(review)
                self._reviews.append(review)
                self._total_alerts_reviewed += 1
                acknowledged.append(review)

        if acknowledged:
            self._total_logins += 1
            self._last_login_s = current_time_s

        return acknowledged

    def _determine_action(self, priority: str) -> str:
        """Determine clinician action based on alert priority."""
        if priority == "critical":
            return "immediate_action"
        elif priority == "high":
            return self.rng.choice(["scheduled_followup", "immediate_action"])
        elif priority == "medium":
            return self.rng.choice(["noted", "scheduled_followup"])
        else:
            return "noted"

    def query_patient_history(self, patient_id: str,
                              start_s: float | None = None,
                              end_s: float | None = None) -> list[Any]:
        """Query historical data for a patient. Always available in current arch."""
        if self.cloud is not None:
            return self.cloud.query_patient(patient_id, start_s=start_s, end_s=end_s)
        return []

    def export_to_emr(self, patient_id: str, timestamp_s: float) -> dict[str, Any]:
        """Simulate export of transmission report to hospital EMR."""
        self._emr_exports += 1
        return {
            "export_id": f"EMR-{self._emr_exports}",
            "patient_id": patient_id,
            "exported_at_s": timestamp_s,
            "exported_by": self.clinician_id,
            "format": "PDF",
        }

    def get_acknowledgment_latency_stats(self) -> dict[str, Any]:
        """Get statistics on alert acknowledgment latency."""
        if not self._reviews:
            return {"total_reviewed": 0}

        latencies_by_priority: dict[str, list[float]] = {}
        for review in self._reviews:
            if review.acknowledged_at_s is not None:
                latency_h = (review.acknowledged_at_s - review.delivered_at_s) / 3600.0
                if review.priority not in latencies_by_priority:
                    latencies_by_priority[review.priority] = []
                latencies_by_priority[review.priority].append(latency_h)

        stats: dict[str, Any] = {"total_reviewed": len(self._reviews)}
        for priority, latencies in latencies_by_priority.items():
            arr = np.array(latencies)
            stats[priority] = {
                "count": len(latencies),
                "mean_hours": float(np.mean(arr)),
                "median_hours": float(np.median(arr)),
                "p95_hours": float(np.percentile(arr, 95)),
                "max_hours": float(np.max(arr)),
            }
        return stats

    @property
    def pending_alert_count(self) -> int:
        return len(self._alert_queue)

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "clinician_id": self.clinician_id,
            "assigned_patients": len(self._assigned_patients),
            "total_logins": self._total_logins,
            "pending_alerts": len(self._alert_queue),
            "total_alerts_reviewed": self._total_alerts_reviewed,
            "emr_exports": self._emr_exports,
            "last_login_s": self._last_login_s,
        }
