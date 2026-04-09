"""Stateless relay processor — processes data in transit without persistent storage."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from src.generator.stream import TelemetryEvent, TransmissionPacket, EventType, World


@dataclass
class RelayItem:
    """An item in the relay with TTL-based expiry."""
    item_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event: TelemetryEvent | None = None
    transmission: TransmissionPacket | None = None
    received_at_s: float = 0.0
    expires_at_s: float = 0.0
    state: str = "received"  # received, processed, delivered, burned, held
    delivery_acks: dict[str, float] = field(default_factory=dict)  # world -> ack_timestamp
    target_worlds: list[str] = field(default_factory=list)
    size_bytes: int = 0
    patient_id: str = ""
    device_serial: str = ""

    @property
    def all_delivered(self) -> bool:
        return all(w in self.delivery_acks for w in self.target_worlds)

    @property
    def age_s(self) -> float:
        return 0.0  # Must be calculated with current time externally


class RelayProcessor:
    """Stateless relay — processes data in transit, does NOT persist beyond TTL.

    The relay:
    1. Receives telemetry events and transmission packets
    2. Processes them (alert detection, report generation)
    3. Routes results to appropriate typed worlds
    4. Expires data after TTL (default 72 hours)

    This models the manufacturer infrastructure as a relay,
    not a repository.
    """

    def __init__(self, ttl_seconds: int = 259200, worlds: dict[str, Any] | None = None) -> None:
        self.ttl_seconds = ttl_seconds  # Default 72 hours
        self.worlds = worlds or {}  # world_name -> world instance

        # In-flight items (simulates Redis with TTL)
        self._items: dict[str, RelayItem] = {}

        # Metrics
        self._total_received = 0
        self._total_processed = 0
        self._total_delivered = 0
        self._total_burned = 0
        self._total_expired = 0
        self._total_bytes_processed = 0
        self._held_count = 0

    def ingest(self, event: TelemetryEvent) -> RelayItem:
        """Ingest a single telemetry event into the relay."""
        item = RelayItem(
            event=event,
            received_at_s=event.timestamp_s,
            expires_at_s=event.timestamp_s + self.ttl_seconds,
            target_worlds=[w.value for w in event.world_targets],
            size_bytes=event.size_bytes,
            patient_id=event.patient_id,
            device_serial=event.device_serial,
        )

        self._items[item.item_id] = item
        self._total_received += 1
        self._total_bytes_processed += event.size_bytes

        # Process and route
        self._process_item(item)
        return item

    def ingest_transmission(self, packet: TransmissionPacket) -> RelayItem:
        """Ingest a transmission packet."""
        # Determine target worlds from constituent events
        target_worlds = set()
        for event in packet.events:
            if hasattr(event, 'world_targets'):
                for w in event.world_targets:
                    target_worlds.add(w.value if hasattr(w, 'value') else str(w))

        if not target_worlds:
            target_worlds = {
                World.CLINICAL.value,
                World.DEVICE_MAINTENANCE.value,
                World.PATIENT.value,
            }

        item = RelayItem(
            transmission=packet,
            received_at_s=packet.timestamp_s,
            expires_at_s=packet.timestamp_s + self.ttl_seconds,
            target_worlds=list(target_worlds),
            size_bytes=packet.payload_size_bytes,
            patient_id=packet.patient_id,
            device_serial=packet.device_serial,
        )

        self._items[item.item_id] = item
        self._total_received += 1
        self._total_bytes_processed += packet.payload_size_bytes

        self._process_item(item)
        return item

    def _process_item(self, item: RelayItem) -> None:
        """Process an item through the relay pipeline and route to worlds."""
        item.state = "processed"
        self._total_processed += 1

        # Route to target worlds
        for world_name in item.target_worlds:
            world = self.worlds.get(world_name)
            if world is None:
                continue

            # Determine event type and data to send
            if item.event is not None:
                record = world.accept_data(
                    event_type=item.event.event_type,
                    patient_id=item.patient_id,
                    device_serial=item.device_serial,
                    data=item.event.payload,
                    timestamp_s=item.event.timestamp_s,
                    size_bytes=item.event.size_bytes,
                )
                if record is not None:
                    item.delivery_acks[world_name] = item.event.timestamp_s

            elif item.transmission is not None:
                # Route transmission data to world
                tx = item.transmission
                # Send individual events to appropriate worlds
                for event in tx.events:
                    if hasattr(event, 'event_type') and hasattr(event, 'payload'):
                        record = world.accept_data(
                            event_type=event.event_type,
                            patient_id=tx.patient_id,
                            device_serial=tx.device_serial,
                            data=event.payload if isinstance(event.payload, dict) else {"value": event.payload},
                            timestamp_s=event.timestamp_s if hasattr(event, 'timestamp_s') else tx.timestamp_s,
                            size_bytes=event.size_bytes if hasattr(event, 'size_bytes') else 100,
                        )

                # Also send device status summary
                if tx.device_status and world_name in (World.DEVICE_MAINTENANCE.value, World.PATIENT.value):
                    world.accept_data(
                        event_type=EventType.DEVICE_STATUS,
                        patient_id=tx.patient_id,
                        device_serial=tx.device_serial,
                        data=tx.device_status,
                        timestamp_s=tx.timestamp_s,
                        size_bytes=128,
                    )

                item.delivery_acks[world_name] = tx.timestamp_s

        if item.all_delivered:
            item.state = "delivered"
            self._total_delivered += 1

    def process_burns(self, current_time_s: float) -> list[str]:
        """Process TTL expirations. Burns items past their TTL.

        Returns list of burned item IDs.
        """
        burned: list[str] = []

        for item_id, item in list(self._items.items()):
            if item.state == "held":
                continue  # Safety investigation hold

            if current_time_s >= item.expires_at_s:
                item.state = "burned"
                del self._items[item_id]
                self._total_burned += 1
                burned.append(item_id)
            elif not item.all_delivered and (current_time_s - item.received_at_s) > self.ttl_seconds / 2:
                # Escalation: item is past half its TTL without full delivery
                self._escalate_delivery(item, current_time_s)

        self._total_expired += len(burned)
        return burned

    def _escalate_delivery(self, item: RelayItem, current_time_s: float) -> None:
        """Escalate undelivered items past half their TTL."""
        undelivered_worlds = [w for w in item.target_worlds if w not in item.delivery_acks]
        # Retry delivery to undelivered worlds
        for world_name in undelivered_worlds:
            world = self.worlds.get(world_name)
            if world is not None and item.event is not None:
                record = world.accept_data(
                    event_type=item.event.event_type,
                    patient_id=item.patient_id,
                    device_serial=item.device_serial,
                    data=item.event.payload,
                    timestamp_s=item.event.timestamp_s,
                    size_bytes=item.event.size_bytes,
                )
                if record is not None:
                    item.delivery_acks[world_name] = current_time_s

    def apply_hold(self, patient_id: str, hold_id: str) -> int:
        """Apply safety investigation hold — suspend TTL for patient's items."""
        held = 0
        for item in self._items.values():
            if item.patient_id == patient_id and item.state != "held":
                item.state = "held"
                item.expires_at_s = float("inf")  # Never expire while held
                held += 1
        self._held_count += held
        return held

    def release_hold(self, patient_id: str) -> int:
        """Release hold — restore TTL-based expiry."""
        released = 0
        for item in self._items.values():
            if item.patient_id == patient_id and item.state == "held":
                item.state = "processed"
                # Reset TTL from now
                item.expires_at_s = item.received_at_s + self.ttl_seconds
                released += 1
        return released

    def get_snapshot(self, patient_id: str) -> dict[str, Any]:
        """Capture a snapshot of all relay data for a patient.
        Used by Safety Investigation World at hold trigger.
        """
        items = [
            {
                "item_id": item.item_id,
                "state": item.state,
                "received_at_s": item.received_at_s,
                "size_bytes": item.size_bytes,
                "event_type": item.event.event_type.value if item.event else "transmission",
                "data_summary": (
                    {k: str(v)[:100] for k, v in item.event.payload.items()}
                    if item.event else {"type": "transmission"}
                ),
            }
            for item in self._items.values()
            if item.patient_id == patient_id
        ]
        return {"patient_id": patient_id, "items": items, "count": len(items)}

    @property
    def items_in_relay(self) -> int:
        return len(self._items)

    @property
    def oldest_item_age_s(self) -> float:
        if not self._items:
            return 0.0
        return min(item.received_at_s for item in self._items.values())

    @property
    def total_bytes_in_relay(self) -> int:
        return sum(item.size_bytes for item in self._items.values())

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "ttl_seconds": self.ttl_seconds,
            "ttl_hours": self.ttl_seconds / 3600,
            "items_in_relay": len(self._items),
            "total_bytes_in_relay": self.total_bytes_in_relay,
            "total_mb_in_relay": self.total_bytes_in_relay / (1024 * 1024),
            "total_received": self._total_received,
            "total_processed": self._total_processed,
            "total_delivered": self._total_delivered,
            "total_burned": self._total_burned,
            "total_expired": self._total_expired,
            "total_bytes_processed": self._total_bytes_processed,
            "held_items": sum(1 for i in self._items.values() if i.state == "held"),
            "worlds_connected": list(self.worlds.keys()),
        }
