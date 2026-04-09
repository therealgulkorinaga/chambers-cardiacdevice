"""Abstract base class for all typed worlds in the Chambers architecture."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.generator.stream import World, EventType


class AccessLevel(Enum):
    NONE = "none"
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


@dataclass
class WorldRecord:
    """A data record within a typed world."""
    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    patient_id: str = ""
    device_serial: str = ""
    event_type: str = ""
    timestamp_s: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)
    size_bytes: int = 0
    ingested_at_s: float = 0.0
    burn_eligible: bool = False
    burn_scheduled_at_s: float | None = None
    held: bool = False
    hold_id: str | None = None


@dataclass
class AuditEntry:
    """An audit log entry for world operations."""
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp_s: float = 0.0
    operation: str = ""  # 'accept', 'query', 'burn', 'hold', 'release'
    actor: str = ""
    world: str = ""
    record_id: str | None = None
    patient_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    reason: str = ""


class BaseWorld(ABC):
    """Abstract base class for typed worlds.

    Enforces:
    - Data scope validation (only accepts permitted event types)
    - Access control (only authorized actors)
    - Burn schedule interface
    - Audit logging
    - Hold awareness (respects safety investigation holds)
    """

    def __init__(self, world_type: World, allowed_event_types: set[EventType],
                 authorized_actors: set[str]) -> None:
        self.world_type = world_type
        self.allowed_event_types = allowed_event_types
        self.authorized_actors = authorized_actors

        # Storage
        self._records: dict[str, list[WorldRecord]] = {}  # patient_id -> records
        self._all_records: dict[str, WorldRecord] = {}  # record_id -> record

        # Audit log
        self._audit_log: list[AuditEntry] = []

        # Holds
        self._active_holds: dict[str, str] = {}  # patient_id -> hold_id

        # Metrics
        self._total_accepted = 0
        self._total_rejected = 0
        self._total_burned = 0
        self._total_bytes = 0
        self._total_queries = 0

    def accept_data(self, event_type: EventType, patient_id: str,
                    device_serial: str, data: dict[str, Any],
                    timestamp_s: float, size_bytes: int = 100) -> WorldRecord | None:
        """Accept data into this world. Validates scope before accepting.

        Returns the record if accepted, None if rejected.
        """
        # Scope validation
        if event_type not in self.allowed_event_types:
            self._total_rejected += 1
            self._audit(
                timestamp_s, "accept", "system", patient_id=patient_id,
                success=False, reason=f"Event type {event_type.value} not in scope for {self.world_type.value}"
            )
            return None

        record = WorldRecord(
            patient_id=patient_id,
            device_serial=device_serial,
            event_type=event_type.value,
            timestamp_s=timestamp_s,
            data=data,
            size_bytes=size_bytes,
            ingested_at_s=timestamp_s,
            held=patient_id in self._active_holds,
            hold_id=self._active_holds.get(patient_id),
        )

        if patient_id not in self._records:
            self._records[patient_id] = []
        self._records[patient_id].append(record)
        self._all_records[record.record_id] = record

        self._total_accepted += 1
        self._total_bytes += size_bytes

        self._audit(timestamp_s, "accept", "system", record_id=record.record_id,
                    patient_id=patient_id)

        # Apply world-specific acceptance logic
        self._on_accept(record)

        return record

    def query(self, actor: str, patient_id: str,
              event_type: str | None = None,
              start_s: float | None = None,
              end_s: float | None = None,
              timestamp_s: float = 0.0) -> list[WorldRecord]:
        """Query records from this world. Enforces access control."""
        if actor not in self.authorized_actors and actor != patient_id:
            self._audit(
                timestamp_s, "query", actor, patient_id=patient_id,
                success=False, reason=f"Actor {actor} not authorized for {self.world_type.value}"
            )
            raise PermissionError(
                f"Actor '{actor}' is not authorized to access {self.world_type.value} world"
            )

        records = self._records.get(patient_id, [])

        if event_type is not None:
            records = [r for r in records if r.event_type == event_type]
        if start_s is not None:
            records = [r for r in records if r.timestamp_s >= start_s]
        if end_s is not None:
            records = [r for r in records if r.timestamp_s <= end_s]

        self._total_queries += 1
        self._audit(timestamp_s, "query", actor, patient_id=patient_id,
                    details={"result_count": len(records)})

        return records

    def burn(self, record_id: str, timestamp_s: float) -> bool:
        """Burn (permanently delete) a specific record.

        Returns True if burned, False if held or not found.
        """
        record = self._all_records.get(record_id)
        if record is None:
            return False

        if record.held:
            self._audit(
                timestamp_s, "burn", "system", record_id=record_id,
                patient_id=record.patient_id,
                success=False, reason=f"Record held by investigation {record.hold_id}"
            )
            return False

        # Remove from all indexes
        if record.patient_id in self._records:
            self._records[record.patient_id] = [
                r for r in self._records[record.patient_id] if r.record_id != record_id
            ]
        del self._all_records[record_id]

        self._total_burned += 1
        self._total_bytes -= record.size_bytes

        self._audit(timestamp_s, "burn", "system", record_id=record_id,
                    patient_id=record.patient_id)

        self._on_burn(record)
        return True

    def burn_patient(self, patient_id: str, timestamp_s: float) -> int:
        """Burn all records for a patient. Respects holds. Returns count burned."""
        records = list(self._records.get(patient_id, []))
        burned = 0
        for record in records:
            if self.burn(record.record_id, timestamp_s):
                burned += 1
        return burned

    def apply_hold(self, patient_id: str, hold_id: str, timestamp_s: float) -> int:
        """Apply a safety investigation hold to all records for a patient.
        Returns count of records held.
        """
        self._active_holds[patient_id] = hold_id
        held_count = 0

        for record in self._records.get(patient_id, []):
            record.held = True
            record.hold_id = hold_id
            held_count += 1

        self._audit(timestamp_s, "hold", "investigation", patient_id=patient_id,
                    details={"hold_id": hold_id, "records_held": held_count})
        return held_count

    def release_hold(self, patient_id: str, timestamp_s: float) -> int:
        """Release a safety investigation hold. Returns count of records released."""
        if patient_id not in self._active_holds:
            return 0

        hold_id = self._active_holds.pop(patient_id)
        released = 0

        for record in self._records.get(patient_id, []):
            if record.hold_id == hold_id:
                record.held = False
                record.hold_id = None
                released += 1

        self._audit(timestamp_s, "release", "investigation", patient_id=patient_id,
                    details={"hold_id": hold_id, "records_released": released})
        return released

    def get_status(self) -> dict[str, Any]:
        """Get current world status."""
        return {
            "world": self.world_type.value,
            "total_accepted": self._total_accepted,
            "total_rejected": self._total_rejected,
            "total_burned": self._total_burned,
            "total_bytes": self._total_bytes,
            "total_mb": self._total_bytes / (1024 * 1024),
            "total_queries": self._total_queries,
            "active_records": len(self._all_records),
            "patient_count": len(self._records),
            "active_holds": len(self._active_holds),
            "allowed_event_types": [et.value for et in self.allowed_event_types],
        }

    def _audit(self, timestamp_s: float, operation: str, actor: str,
               record_id: str | None = None, patient_id: str | None = None,
               details: dict[str, Any] | None = None,
               success: bool = True, reason: str = "") -> None:
        """Write an audit log entry."""
        entry = AuditEntry(
            timestamp_s=timestamp_s,
            operation=operation,
            actor=actor,
            world=self.world_type.value,
            record_id=record_id,
            patient_id=patient_id,
            details=details or {},
            success=success,
            reason=reason,
        )
        self._audit_log.append(entry)

    @abstractmethod
    def _on_accept(self, record: WorldRecord) -> None:
        """World-specific post-accept logic."""
        ...

    @abstractmethod
    def _on_burn(self, record: WorldRecord) -> None:
        """World-specific post-burn logic."""
        ...

    @abstractmethod
    def get_burn_candidates(self, timestamp_s: float) -> list[str]:
        """Return record IDs that are eligible for burning at this time."""
        ...
