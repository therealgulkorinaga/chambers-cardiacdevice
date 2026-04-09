"""Delivery confirmation tracker — ensures data reaches target worlds before burn."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeliveryRecord:
    """Tracks delivery status for a single data element."""
    item_id: str
    patient_id: str
    target_worlds: list[str]
    acks: dict[str, float] = field(default_factory=dict)  # world -> timestamp
    created_at_s: float = 0.0
    escalated: bool = False
    escalated_at_s: float | None = None
    fully_delivered: bool = False
    fully_delivered_at_s: float | None = None

    @property
    def pending_worlds(self) -> list[str]:
        return [w for w in self.target_worlds if w not in self.acks]

    @property
    def delivery_complete(self) -> bool:
        return all(w in self.acks for w in self.target_worlds)


class DeliveryTracker:
    """Tracks delivery confirmations from typed worlds.

    Ensures every data element is ACKed by all target worlds before
    the relay allows it to burn. Implements escalation if ACK is
    not received within half the TTL.
    """

    def __init__(self, ttl_seconds: int = 259200) -> None:
        self.ttl_seconds = ttl_seconds
        self.escalation_threshold_s = ttl_seconds / 2

        self._records: dict[str, DeliveryRecord] = {}
        self._total_tracked = 0
        self._total_completed = 0
        self._total_escalated = 0

    def track(self, item_id: str, patient_id: str,
              target_worlds: list[str], timestamp_s: float) -> DeliveryRecord:
        """Start tracking delivery for an item."""
        record = DeliveryRecord(
            item_id=item_id,
            patient_id=patient_id,
            target_worlds=target_worlds,
            created_at_s=timestamp_s,
        )
        self._records[item_id] = record
        self._total_tracked += 1
        return record

    def ack(self, item_id: str, world: str, timestamp_s: float) -> bool:
        """Record an ACK from a world. Returns True if all worlds have ACKed."""
        record = self._records.get(item_id)
        if record is None:
            return False

        record.acks[world] = timestamp_s

        if record.delivery_complete and not record.fully_delivered:
            record.fully_delivered = True
            record.fully_delivered_at_s = timestamp_s
            self._total_completed += 1
            return True

        return record.delivery_complete

    def check_escalations(self, current_time_s: float) -> list[DeliveryRecord]:
        """Check for items that need escalation (past half TTL without full ACK)."""
        escalated: list[DeliveryRecord] = []

        for record in self._records.values():
            if record.fully_delivered or record.escalated:
                continue

            age = current_time_s - record.created_at_s
            if age > self.escalation_threshold_s:
                record.escalated = True
                record.escalated_at_s = current_time_s
                self._total_escalated += 1
                escalated.append(record)

        return escalated

    def get_burn_ready(self) -> list[str]:
        """Get item IDs that are fully delivered and ready for burn."""
        return [
            item_id for item_id, record in self._records.items()
            if record.fully_delivered
        ]

    def remove(self, item_id: str) -> None:
        """Remove tracking for a burned/expired item."""
        self._records.pop(item_id, None)

    def get_record(self, item_id: str) -> DeliveryRecord | None:
        return self._records.get(item_id)

    @property
    def pending_count(self) -> int:
        return sum(1 for r in self._records.values() if not r.fully_delivered)

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_tracked": self._total_tracked,
            "total_completed": self._total_completed,
            "total_escalated": self._total_escalated,
            "currently_pending": self.pending_count,
            "currently_tracking": len(self._records),
            "completion_rate": (
                self._total_completed / self._total_tracked
                if self._total_tracked > 0 else 0.0
            ),
        }
