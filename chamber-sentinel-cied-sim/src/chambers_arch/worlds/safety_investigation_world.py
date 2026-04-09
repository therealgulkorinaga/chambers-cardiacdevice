"""Safety Investigation World — adverse event holds that suspend burn schedules."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from src.generator.stream import World, EventType
from src.chambers_arch.worlds.base_world import BaseWorld, WorldRecord


@dataclass
class SafetyHold:
    """A safety investigation hold that freezes burn schedules."""
    hold_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    patient_id: str = ""
    device_serial: str = ""
    trigger_type: str = ""  # 'manufacturer_report', 'fda_request', 'clinician_report', 'auto_detect'
    triggered_at_s: float = 0.0
    triggered_by: str = ""
    reason: str = ""
    investigation_status: str = "active"  # 'active', 'closed', 'released'
    investigation_closed_at_s: float | None = None
    buffer_expires_at_s: float | None = None  # closed_at + 12 months
    burn_executed_at_s: float | None = None
    held_record_ids: list[str] = field(default_factory=list)
    data_already_burned_at_trigger: list[str] = field(default_factory=list)  # IDs that were already gone
    relay_snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.investigation_status == "active"

    @property
    def buffer_expired(self) -> bool:
        if self.buffer_expires_at_s is None:
            return False
        # We check against the current time externally
        return False  # Must be checked with current timestamp


class SafetyInvestigationWorld(BaseWorld):
    """Safety Investigation World: Preserve data for adverse event investigations.

    Hold mechanism:
    - Triggered by: manufacturer report, FDA request, clinician report, auto-detect
    - Freezes all burn schedules for the affected patient
    - Captures snapshot of all data currently in relay
    - Duration: investigation period + 12-month buffer
    - Access: investigating parties only (FDA, manufacturer safety, clinician)

    Critical trade-off:
    - Data that has ALREADY been burned is NOT recoverable
    - This is an accepted cost of burn-by-default semantics
    """

    ALLOWED_EVENTS = {
        EventType.ADVERSE_EVENT,
        EventType.ALERT,
        EventType.EPISODE_START,
        EventType.EPISODE_END,
        EventType.DEVICE_STATUS,
        EventType.LEAD_MEASUREMENT,
        EventType.HEARTBEAT,
        EventType.PACING,
        EventType.TRANSMISSION,
        EventType.THRESHOLD_TEST,
    }

    AUTHORIZED_ACTORS = {"investigator", "fda", "manufacturer_safety", "clinician", "system"}

    BUFFER_DURATION_S = 365 * 86400  # 12-month buffer after investigation closure

    def __init__(self, buffer_months: int = 12) -> None:
        super().__init__(
            world_type=World.SAFETY_INVESTIGATION,
            allowed_event_types=self.ALLOWED_EVENTS,
            authorized_actors=self.AUTHORIZED_ACTORS,
        )
        self.buffer_duration_s = buffer_months * 30 * 86400

        # Hold registry
        self._holds: dict[str, SafetyHold] = {}  # hold_id -> SafetyHold
        self._holds_by_patient: dict[str, list[str]] = {}  # patient_id -> [hold_ids]

    def _on_accept(self, record: WorldRecord) -> None:
        """Associate record with any active holds for this patient."""
        patient_id = record.patient_id
        if patient_id in self._holds_by_patient:
            for hold_id in self._holds_by_patient[patient_id]:
                hold = self._holds.get(hold_id)
                if hold and hold.is_active:
                    hold.held_record_ids.append(record.record_id)
                    record.held = True
                    record.hold_id = hold_id

    def _on_burn(self, record: WorldRecord) -> None:
        """Remove from hold tracking on burn."""
        for hold in self._holds.values():
            if record.record_id in hold.held_record_ids:
                hold.held_record_ids.remove(record.record_id)

    def create_hold(
        self,
        patient_id: str,
        device_serial: str,
        trigger_type: str,
        triggered_by: str,
        reason: str,
        timestamp_s: float,
        relay_snapshot: dict[str, Any] | None = None,
        other_worlds: list[BaseWorld] | None = None,
    ) -> SafetyHold:
        """Create a safety investigation hold.

        This:
        1. Freezes burn schedules for the affected patient in ALL worlds
        2. Captures a snapshot of data currently in relay
        3. Logs the hold initiation
        """
        hold = SafetyHold(
            patient_id=patient_id,
            device_serial=device_serial,
            trigger_type=trigger_type,
            triggered_at_s=timestamp_s,
            triggered_by=triggered_by,
            reason=reason,
            relay_snapshot=relay_snapshot or {},
        )

        self._holds[hold.hold_id] = hold
        if patient_id not in self._holds_by_patient:
            self._holds_by_patient[patient_id] = []
        self._holds_by_patient[patient_id].append(hold.hold_id)

        # Apply hold to all existing records for this patient in this world
        held_count = self.apply_hold(patient_id, hold.hold_id, timestamp_s)
        hold.held_record_ids = [
            r.record_id for r in self._records.get(patient_id, []) if r.held
        ]

        # Apply hold to other worlds
        if other_worlds:
            for world in other_worlds:
                world.apply_hold(patient_id, hold.hold_id, timestamp_s)

        self._audit(
            timestamp_s, "hold_created", triggered_by,
            patient_id=patient_id,
            details={
                "hold_id": hold.hold_id,
                "trigger_type": trigger_type,
                "reason": reason,
                "records_held_this_world": held_count,
                "relay_snapshot_size": len(relay_snapshot) if relay_snapshot else 0,
            },
        )

        return hold

    def close_investigation(self, hold_id: str, timestamp_s: float) -> SafetyHold | None:
        """Close an investigation. Starts the buffer countdown."""
        hold = self._holds.get(hold_id)
        if hold is None or hold.investigation_status != "active":
            return None

        hold.investigation_status = "closed"
        hold.investigation_closed_at_s = timestamp_s
        hold.buffer_expires_at_s = timestamp_s + self.buffer_duration_s

        self._audit(
            timestamp_s, "investigation_closed", "investigator",
            patient_id=hold.patient_id,
            details={
                "hold_id": hold_id,
                "buffer_expires_at_s": hold.buffer_expires_at_s,
                "buffer_days": self.buffer_duration_s / 86400,
            },
        )
        return hold

    def release_hold(self, hold_id: str, timestamp_s: float,
                     other_worlds: list[BaseWorld] | None = None) -> int:
        """Release a hold after buffer expiry. Allows burns to resume.
        Returns total records released across all worlds.
        """
        hold = self._holds.get(hold_id)
        if hold is None:
            return 0

        hold.investigation_status = "released"
        hold.burn_executed_at_s = timestamp_s
        total_released = 0

        # Release in this world
        released = self.release_hold_internal(hold.patient_id, timestamp_s)
        total_released += released

        # Release in other worlds
        if other_worlds:
            for world in other_worlds:
                released = world.release_hold(hold.patient_id, timestamp_s)
                total_released += released

        self._audit(
            timestamp_s, "hold_released", "system",
            patient_id=hold.patient_id,
            details={"hold_id": hold_id, "total_released": total_released},
        )
        return total_released

    def release_hold_internal(self, patient_id: str, timestamp_s: float) -> int:
        """Release hold for records in this world specifically."""
        return super().release_hold(patient_id, timestamp_s)

    def get_hold(self, hold_id: str) -> SafetyHold | None:
        """Get a specific hold by ID."""
        return self._holds.get(hold_id)

    def get_active_holds(self, patient_id: str | None = None) -> list[SafetyHold]:
        """Get all active holds, optionally filtered by patient."""
        holds = [h for h in self._holds.values() if h.is_active]
        if patient_id:
            holds = [h for h in holds if h.patient_id == patient_id]
        return holds

    def get_holds_needing_release(self, timestamp_s: float) -> list[SafetyHold]:
        """Get holds whose buffer period has expired and can be released."""
        return [
            h for h in self._holds.values()
            if h.investigation_status == "closed"
            and h.buffer_expires_at_s is not None
            and timestamp_s >= h.buffer_expires_at_s
        ]

    def get_burn_candidates(self, timestamp_s: float) -> list[str]:
        """Return records from released holds that can now be burned."""
        candidates: list[str] = []
        released_patient_ids = set()

        for hold in self._holds.values():
            if hold.investigation_status == "released":
                released_patient_ids.add(hold.patient_id)

        for record_id, record in list(self._all_records.items()):
            if record.held:
                continue
            if record.patient_id in released_patient_ids:
                candidates.append(record_id)

        return candidates

    def get_data_loss_assessment(self, hold_id: str) -> dict[str, Any]:
        """Assess what data was already burned before the hold was triggered.

        This is the honest accounting of the cost of burn-by-default.
        """
        hold = self._holds.get(hold_id)
        if hold is None:
            return {}

        return {
            "hold_id": hold_id,
            "patient_id": hold.patient_id,
            "triggered_at_s": hold.triggered_at_s,
            "records_preserved": len(hold.held_record_ids),
            "records_already_burned": len(hold.data_already_burned_at_trigger),
            "relay_snapshot_available": bool(hold.relay_snapshot),
            "data_loss_acknowledged": True,
            "note": "Data that burned before hold trigger is irrecoverable. This is an accepted trade-off.",
        }

    def get_status(self) -> dict[str, Any]:
        status = super().get_status()
        status.update({
            "total_holds": len(self._holds),
            "active_holds": len([h for h in self._holds.values() if h.is_active]),
            "closed_holds": len([h for h in self._holds.values() if h.investigation_status == "closed"]),
            "released_holds": len([h for h in self._holds.values() if h.investigation_status == "released"]),
            "buffer_duration_months": self.buffer_duration_s / (30 * 86400),
        })
        return status
