"""Hold manager — coordinates safety investigation holds across all worlds."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HoldRecord:
    """Registry entry for an active hold."""
    hold_id: str
    patient_id: str
    device_serial: str
    trigger_type: str
    triggered_by: str
    reason: str
    triggered_at_s: float
    status: str = "active"  # active, closed, released
    closed_at_s: float | None = None
    buffer_expires_at_s: float | None = None
    released_at_s: float | None = None
    worlds_held: list[str] = field(default_factory=list)
    relay_items_held: int = 0
    total_records_held: int = 0
    data_already_burned: list[str] = field(default_factory=list)


class HoldManager:
    """Manages safety investigation holds across all worlds and the relay.

    Responsibilities:
    - Create holds: freeze burns for affected patient in all worlds + relay
    - Track hold lifecycle: active -> closed -> buffer -> released -> burned
    - Cross-world coordination
    - Audit trail
    """

    def __init__(self, worlds: dict[str, Any] | None = None,
                 relay: Any = None,
                 burn_scheduler: Any = None,
                 buffer_months: int = 12) -> None:
        self.worlds = worlds or {}
        self.relay = relay
        self.burn_scheduler = burn_scheduler
        self.buffer_duration_s = buffer_months * 30 * 86400

        self._holds: dict[str, HoldRecord] = {}
        self._holds_by_patient: dict[str, list[str]] = {}
        self._audit: list[dict[str, Any]] = []

    def create_hold(self, patient_id: str, device_serial: str,
                    trigger_type: str, triggered_by: str,
                    reason: str, timestamp_s: float) -> HoldRecord:
        """Create a safety investigation hold.

        Freezes burns in:
        1. All typed worlds (Clinical, Device Maint, Research, Patient, Safety Investigation)
        2. The relay (suspend TTL)
        3. The burn scheduler (suspend scheduled burns)
        """
        import uuid
        hold_id = str(uuid.uuid4())

        record = HoldRecord(
            hold_id=hold_id,
            patient_id=patient_id,
            device_serial=device_serial,
            trigger_type=trigger_type,
            triggered_by=triggered_by,
            reason=reason,
            triggered_at_s=timestamp_s,
        )

        # Apply hold to all worlds
        total_held = 0
        for world_name, world in self.worlds.items():
            held = world.apply_hold(patient_id, hold_id, timestamp_s)
            total_held += held
            if held > 0:
                record.worlds_held.append(world_name)

        # Apply hold to relay
        if self.relay is not None:
            relay_held = self.relay.apply_hold(patient_id, hold_id)
            record.relay_items_held = relay_held
            total_held += relay_held

        # Suspend scheduled burns
        if self.burn_scheduler is not None:
            self.burn_scheduler.suspend_burns(patient_id, hold_id)

        record.total_records_held = total_held

        # Store
        self._holds[hold_id] = record
        if patient_id not in self._holds_by_patient:
            self._holds_by_patient[patient_id] = []
        self._holds_by_patient[patient_id].append(hold_id)

        self._log(timestamp_s, "create_hold", hold_id, patient_id,
                  f"Hold created by {triggered_by}: {reason}. "
                  f"{total_held} records held across {len(record.worlds_held)} worlds + relay.")

        return record

    def close_investigation(self, hold_id: str, timestamp_s: float) -> HoldRecord | None:
        """Close the investigation. Start buffer countdown.
        Data remains held until buffer expires.
        """
        record = self._holds.get(hold_id)
        if record is None or record.status != "active":
            return None

        record.status = "closed"
        record.closed_at_s = timestamp_s
        record.buffer_expires_at_s = timestamp_s + self.buffer_duration_s

        self._log(timestamp_s, "close_investigation", hold_id, record.patient_id,
                  f"Investigation closed. Buffer expires at {record.buffer_expires_at_s:.0f}s "
                  f"({self.buffer_duration_s / 86400:.0f} days from now).")

        return record

    def release_hold(self, hold_id: str, timestamp_s: float) -> int:
        """Release a hold. Resume burns for the affected patient.
        Returns total records released.
        """
        record = self._holds.get(hold_id)
        if record is None:
            return 0

        record.status = "released"
        record.released_at_s = timestamp_s
        total_released = 0

        # Release in all worlds
        for world_name, world in self.worlds.items():
            released = world.release_hold(record.patient_id, timestamp_s)
            total_released += released

        # Release in relay
        if self.relay is not None:
            relay_released = self.relay.release_hold(record.patient_id)
            total_released += relay_released

        # Resume scheduled burns
        if self.burn_scheduler is not None:
            self.burn_scheduler.resume_burns(record.patient_id, current_time_s=timestamp_s)

        self._log(timestamp_s, "release_hold", hold_id, record.patient_id,
                  f"Hold released. {total_released} records released for burning.")

        return total_released

    def check_buffer_expirations(self, current_time_s: float) -> list[HoldRecord]:
        """Check for holds whose buffer period has expired.
        Returns list of holds ready for release.
        """
        ready: list[HoldRecord] = []
        for record in self._holds.values():
            if (record.status == "closed"
                    and record.buffer_expires_at_s is not None
                    and current_time_s >= record.buffer_expires_at_s):
                ready.append(record)
        return ready

    def tick(self, current_time_s: float) -> list[str]:
        """Process hold lifecycle. Auto-release expired buffers.
        Returns list of hold IDs that were auto-released.
        """
        released_ids: list[str] = []
        for record in self.check_buffer_expirations(current_time_s):
            self.release_hold(record.hold_id, current_time_s)
            released_ids.append(record.hold_id)
        return released_ids

    def get_hold(self, hold_id: str) -> HoldRecord | None:
        return self._holds.get(hold_id)

    def get_active_holds(self) -> list[HoldRecord]:
        return [h for h in self._holds.values() if h.status == "active"]

    def get_patient_holds(self, patient_id: str) -> list[HoldRecord]:
        hold_ids = self._holds_by_patient.get(patient_id, [])
        return [self._holds[hid] for hid in hold_ids if hid in self._holds]

    def has_active_hold(self, patient_id: str) -> bool:
        for hid in self._holds_by_patient.get(patient_id, []):
            hold = self._holds.get(hid)
            if hold and hold.status == "active":
                return True
        return False

    def _log(self, timestamp_s: float, action: str, hold_id: str,
             patient_id: str, message: str) -> None:
        self._audit.append({
            "timestamp_s": timestamp_s,
            "action": action,
            "hold_id": hold_id,
            "patient_id": patient_id,
            "message": message,
        })

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_holds": len(self._holds),
            "active": len([h for h in self._holds.values() if h.status == "active"]),
            "closed": len([h for h in self._holds.values() if h.status == "closed"]),
            "released": len([h for h in self._holds.values() if h.status == "released"]),
            "patients_affected": len(self._holds_by_patient),
            "audit_entries": len(self._audit),
        }
