"""Clinical World — full-fidelity clinical data for the treating physician."""

from __future__ import annotations

from typing import Any

from src.generator.stream import World, EventType
from src.chambers_arch.worlds.base_world import BaseWorld, WorldRecord


class ClinicalWorld(BaseWorld):
    """Clinical World: Full-fidelity IEGMs, therapy logs, diagnostic trends, alerts.

    Burn policy: Burns from relay after BOTH:
    1. Confirmed delivery to patient portable record
    2. Clinician acknowledgment (for alert-bearing transmissions)

    Fallback: If no acknowledgment within max_hold_window, escalate then burn.
    """

    ALLOWED_EVENTS = {
        EventType.HEARTBEAT,
        EventType.PACING,
        EventType.EPISODE_START,
        EventType.EPISODE_END,
        EventType.ALERT,
        EventType.TRANSMISSION,
        EventType.LEAD_MEASUREMENT,
        EventType.THRESHOLD_TEST,
    }

    AUTHORIZED_ACTORS = {"clinician", "system", "patient"}

    def __init__(self, max_hold_window_s: float = 30 * 86400.0) -> None:
        """
        Args:
            max_hold_window_s: Maximum time to wait for clinician ack before forced burn
                             (default: 30 days)
        """
        super().__init__(
            world_type=World.CLINICAL,
            allowed_event_types=self.ALLOWED_EVENTS,
            authorized_actors=self.AUTHORIZED_ACTORS,
        )
        self.max_hold_window_s = max_hold_window_s

        # Delivery tracking
        self._delivered_to_patient: set[str] = set()  # record_ids delivered to patient record
        self._clinician_acknowledged: set[str] = set()  # record_ids acknowledged by clinician
        self._alert_records: set[str] = set()  # record_ids that contain alerts
        self._pending_notifications: list[dict[str, Any]] = []

    def _on_accept(self, record: WorldRecord) -> None:
        """Track alert-bearing records for acknowledgment requirement."""
        if record.event_type == EventType.ALERT.value:
            self._alert_records.add(record.record_id)
            self._pending_notifications.append({
                "record_id": record.record_id,
                "patient_id": record.patient_id,
                "alert_type": record.data.get("alert_type", "unknown"),
                "priority": record.data.get("priority", "medium"),
                "timestamp_s": record.timestamp_s,
            })

    def _on_burn(self, record: WorldRecord) -> None:
        """Clean up tracking sets on burn."""
        self._delivered_to_patient.discard(record.record_id)
        self._clinician_acknowledged.discard(record.record_id)
        self._alert_records.discard(record.record_id)

    def confirm_delivery_to_patient(self, record_id: str, timestamp_s: float) -> bool:
        """Confirm that a record has been delivered to the patient's portable record."""
        if record_id in self._all_records:
            self._delivered_to_patient.add(record_id)
            record = self._all_records[record_id]
            self._audit(timestamp_s, "delivery_confirmed", "patient_world",
                        record_id=record_id, patient_id=record.patient_id)
            return True
        return False

    def confirm_clinician_acknowledgment(self, record_id: str, clinician_id: str,
                                          timestamp_s: float) -> bool:
        """Confirm that a clinician has acknowledged/reviewed a record."""
        if record_id in self._all_records:
            self._clinician_acknowledged.add(record_id)
            record = self._all_records[record_id]
            record.burn_eligible = True
            self._audit(timestamp_s, "clinician_ack", clinician_id,
                        record_id=record_id, patient_id=record.patient_id)
            # Remove from pending notifications
            self._pending_notifications = [
                n for n in self._pending_notifications if n["record_id"] != record_id
            ]
            return True
        return False

    def get_burn_candidates(self, timestamp_s: float) -> list[str]:
        """Return record IDs eligible for burning.

        A record is burn-eligible when:
        - It has been delivered to patient record AND
        - If it's an alert record: clinician has acknowledged it
        - If it's not an alert record: delivery confirmation is sufficient
        - OR: max_hold_window has expired (forced burn with audit note)
        """
        candidates: list[str] = []

        for record_id, record in list(self._all_records.items()):
            if record.held:
                continue  # Safety investigation hold — never burn

            delivered = record_id in self._delivered_to_patient
            is_alert = record_id in self._alert_records
            acknowledged = record_id in self._clinician_acknowledged

            if delivered and (not is_alert or acknowledged):
                candidates.append(record_id)
            elif (timestamp_s - record.ingested_at_s) > self.max_hold_window_s:
                # Forced burn after max hold window — data has been sitting too long
                record.burn_eligible = True
                record.data["forced_burn"] = True
                record.data["forced_burn_reason"] = "max_hold_window_exceeded"
                self._audit(
                    timestamp_s, "forced_burn_eligible", "system",
                    record_id=record_id, patient_id=record.patient_id,
                    reason="Max hold window exceeded without acknowledgment"
                )
                candidates.append(record_id)

        return candidates

    def get_pending_notifications(self) -> list[dict[str, Any]]:
        """Get alerts waiting for clinician review."""
        return list(self._pending_notifications)

    def get_unacknowledged_alerts(self, patient_id: str | None = None) -> list[WorldRecord]:
        """Get alert records that haven't been acknowledged."""
        unacked = [
            self._all_records[rid]
            for rid in self._alert_records
            if rid not in self._clinician_acknowledged and rid in self._all_records
        ]
        if patient_id:
            unacked = [r for r in unacked if r.patient_id == patient_id]
        return unacked

    def get_status(self) -> dict[str, Any]:
        status = super().get_status()
        status.update({
            "delivered_to_patient": len(self._delivered_to_patient),
            "clinician_acknowledged": len(self._clinician_acknowledged),
            "pending_alerts": len(self._alert_records - self._clinician_acknowledged),
            "pending_notifications": len(self._pending_notifications),
            "max_hold_window_days": self.max_hold_window_s / 86400,
        })
        return status
