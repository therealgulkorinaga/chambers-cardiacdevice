"""Unified event stream assembly — merges all generator outputs into a single ordered stream."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import heapq
import threading
from collections.abc import Iterator


class EventType(Enum):
    HEARTBEAT = "heartbeat"
    PACING = "pacing"
    EPISODE_START = "episode_start"
    EPISODE_END = "episode_end"
    ALERT = "alert"
    TRANSMISSION = "transmission"
    DEVICE_STATUS = "device_status"
    ACTIVITY = "activity"
    ADVERSE_EVENT = "adverse_event"
    BURN = "burn"
    HOLD = "hold"
    CONSENT = "consent"
    FIRMWARE_UPDATE = "firmware_update"
    LEAD_MEASUREMENT = "lead_measurement"
    THRESHOLD_TEST = "threshold_test"


class World(Enum):
    CLINICAL = "clinical"
    DEVICE_MAINTENANCE = "device_maintenance"
    RESEARCH = "research"
    PATIENT = "patient"
    SAFETY_INVESTIGATION = "safety_investigation"


# Sensitivity scores by event type (0-1, higher = more sensitive)
SENSITIVITY_SCORES: dict[EventType, float] = {
    EventType.HEARTBEAT: 0.8,
    EventType.PACING: 0.8,
    EventType.EPISODE_START: 0.9,
    EventType.EPISODE_END: 0.9,
    EventType.ALERT: 0.7,
    EventType.TRANSMISSION: 0.5,
    EventType.DEVICE_STATUS: 0.3,
    EventType.ACTIVITY: 0.7,
    EventType.ADVERSE_EVENT: 1.0,
    EventType.BURN: 0.1,
    EventType.HOLD: 0.2,
    EventType.CONSENT: 0.3,
    EventType.FIRMWARE_UPDATE: 0.2,
    EventType.LEAD_MEASUREMENT: 0.4,
    EventType.THRESHOLD_TEST: 0.5,
}

# Which worlds should receive each event type
WORLD_ROUTING: dict[EventType, list[World]] = {
    EventType.HEARTBEAT: [World.CLINICAL, World.PATIENT],
    EventType.PACING: [World.CLINICAL, World.PATIENT],
    EventType.EPISODE_START: [World.CLINICAL, World.PATIENT, World.RESEARCH],
    EventType.EPISODE_END: [World.CLINICAL, World.PATIENT, World.RESEARCH],
    EventType.ALERT: [World.CLINICAL, World.PATIENT],
    EventType.TRANSMISSION: [World.CLINICAL, World.DEVICE_MAINTENANCE, World.PATIENT],
    EventType.DEVICE_STATUS: [World.DEVICE_MAINTENANCE, World.PATIENT],
    EventType.ACTIVITY: [World.PATIENT],  # Activity only goes to patient world
    EventType.ADVERSE_EVENT: [World.CLINICAL, World.SAFETY_INVESTIGATION, World.PATIENT],
    EventType.LEAD_MEASUREMENT: [World.CLINICAL, World.DEVICE_MAINTENANCE, World.PATIENT],
    EventType.THRESHOLD_TEST: [World.CLINICAL, World.PATIENT],
    EventType.FIRMWARE_UPDATE: [World.DEVICE_MAINTENANCE],
}


@dataclass(order=True)
class TelemetryEvent:
    """A single event in the unified telemetry stream."""

    timestamp_s: float
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()), compare=False)
    patient_id: str = field(default="", compare=False)
    device_serial: str = field(default="", compare=False)
    event_type: EventType = field(default=EventType.HEARTBEAT, compare=False)
    payload: dict[str, Any] = field(default_factory=dict, compare=False)
    sensitivity: float = field(default=0.5, compare=False)
    world_targets: list[World] = field(default_factory=list, compare=False)
    burn_eligible: bool = field(default=True, compare=False)
    size_bytes: int = field(default=100, compare=False)

    def __post_init__(self) -> None:
        if not self.sensitivity:
            self.sensitivity = SENSITIVITY_SCORES.get(self.event_type, 0.5)
        if not self.world_targets:
            self.world_targets = WORLD_ROUTING.get(self.event_type, [World.PATIENT])


@dataclass
class TransmissionPacket:
    """A bundled transmission from device to cloud/relay."""

    transmission_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    patient_id: str = ""
    device_serial: str = ""
    transmission_type: str = "daily_check"  # daily_check, full_interrogation, alert_triggered
    timestamp_s: float = 0.0
    events: list[TelemetryEvent] = field(default_factory=list)
    alert_flags: list[str] = field(default_factory=list)
    egm_strip_ids: list[str] = field(default_factory=list)
    device_status: dict[str, Any] = field(default_factory=dict)
    activity_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def payload_size_bytes(self) -> int:
        base = 500 if self.transmission_type == "daily_check" else 50_000
        return base + sum(e.size_bytes for e in self.events)


class EventStream:
    """Thread-safe priority queue for ordered telemetry events.

    Supports multiple producers (patient generators) pushing events and
    a single consumer pulling events in timestamp order.
    """

    def __init__(self, max_size: int = 100_000) -> None:
        self._heap: list[TelemetryEvent] = []
        self._lock = threading.Lock()
        self._max_size = max_size
        self._closed = False
        self._event_count = 0
        self._total_bytes = 0

    def push(self, event: TelemetryEvent) -> None:
        """Push an event onto the stream. Thread-safe."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Stream is closed")
            if len(self._heap) >= self._max_size:
                # Backpressure: drop oldest if at capacity
                heapq.heappushpop(self._heap, event)
            else:
                heapq.heappush(self._heap, event)
            self._event_count += 1
            self._total_bytes += event.size_bytes

    def push_batch(self, events: list[TelemetryEvent]) -> None:
        """Push multiple events. Thread-safe."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Stream is closed")
            for event in events:
                if len(self._heap) >= self._max_size:
                    heapq.heappushpop(self._heap, event)
                else:
                    heapq.heappush(self._heap, event)
                self._event_count += 1
                self._total_bytes += event.size_bytes

    def pop(self) -> TelemetryEvent | None:
        """Pop the earliest event. Thread-safe. Returns None if empty."""
        with self._lock:
            if self._heap:
                return heapq.heappop(self._heap)
            return None

    def pop_batch(self, n: int) -> list[TelemetryEvent]:
        """Pop up to n events in timestamp order. Thread-safe."""
        with self._lock:
            result = []
            for _ in range(min(n, len(self._heap))):
                result.append(heapq.heappop(self._heap))
            return result

    def peek(self) -> TelemetryEvent | None:
        """Look at the earliest event without removing it."""
        with self._lock:
            return self._heap[0] if self._heap else None

    def drain(self) -> Iterator[TelemetryEvent]:
        """Drain all events in order. Not thread-safe — call after close()."""
        while self._heap:
            yield heapq.heappop(self._heap)

    def close(self) -> None:
        """Mark stream as closed. No more pushes allowed."""
        with self._lock:
            self._closed = True

    @property
    def size(self) -> int:
        return len(self._heap)

    @property
    def is_empty(self) -> bool:
        return len(self._heap) == 0

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "current_size": len(self._heap),
            "total_events_pushed": self._event_count,
            "total_bytes": self._total_bytes,
            "is_closed": self._closed,
        }


class TransmissionAssembler:
    """Assembles individual events into transmission packets based on schedule."""

    def __init__(
        self,
        patient_id: str,
        device_serial: str,
        daily_check_interval_s: float = 86400.0,
        full_interrogation_interval_s: float = 91 * 86400.0,
    ) -> None:
        self.patient_id = patient_id
        self.device_serial = device_serial
        self.daily_check_interval_s = daily_check_interval_s
        self.full_interrogation_interval_s = full_interrogation_interval_s
        self._pending_events: list[TelemetryEvent] = []
        self._pending_alerts: list[TelemetryEvent] = []
        self._last_daily_check_s = 0.0
        self._last_full_interrogation_s = 0.0

    def add_event(self, event: TelemetryEvent) -> list[TransmissionPacket]:
        """Add an event and return any transmissions that should be sent now."""
        transmissions: list[TransmissionPacket] = []

        if event.event_type == EventType.ALERT:
            self._pending_alerts.append(event)
            priority = event.payload.get("priority", "low")
            if priority in ("high", "critical"):
                transmissions.append(self._build_alert_transmission(event.timestamp_s))
        else:
            self._pending_events.append(event)

        # Check if scheduled transmission is due
        if event.timestamp_s - self._last_daily_check_s >= self.daily_check_interval_s:
            transmissions.append(self._build_daily_check(event.timestamp_s))
            self._last_daily_check_s = event.timestamp_s

        if event.timestamp_s - self._last_full_interrogation_s >= self.full_interrogation_interval_s:
            transmissions.append(self._build_full_interrogation(event.timestamp_s))
            self._last_full_interrogation_s = event.timestamp_s

        return transmissions

    def _build_daily_check(self, timestamp_s: float) -> TransmissionPacket:
        # Grab latest device status and alert flags from pending
        status_events = [e for e in self._pending_events if e.event_type == EventType.DEVICE_STATUS]
        latest_status = status_events[-1].payload if status_events else {}
        alert_flags = [a.payload.get("alert_type", "") for a in self._pending_alerts]

        packet = TransmissionPacket(
            patient_id=self.patient_id,
            device_serial=self.device_serial,
            transmission_type="daily_check",
            timestamp_s=timestamp_s,
            events=[],  # Daily checks are lightweight summaries
            alert_flags=alert_flags,
            device_status=latest_status,
        )
        self._pending_alerts.clear()
        return packet

    def _build_full_interrogation(self, timestamp_s: float) -> TransmissionPacket:
        all_events = list(self._pending_events)
        packet = TransmissionPacket(
            patient_id=self.patient_id,
            device_serial=self.device_serial,
            transmission_type="full_interrogation",
            timestamp_s=timestamp_s,
            events=all_events,
            alert_flags=[a.payload.get("alert_type", "") for a in self._pending_alerts],
            egm_strip_ids=[
                e.payload.get("strip_id", "")
                for e in all_events
                if e.event_type in (EventType.EPISODE_START, EventType.EPISODE_END)
                and "strip_id" in e.payload
            ],
            device_status=(
                all_events[-1].payload
                if all_events and all_events[-1].event_type == EventType.DEVICE_STATUS
                else {}
            ),
        )
        self._pending_events.clear()
        self._pending_alerts.clear()
        return packet

    def _build_alert_transmission(self, timestamp_s: float) -> TransmissionPacket:
        critical_alerts = [
            a for a in self._pending_alerts
            if a.payload.get("priority") in ("high", "critical")
        ]
        packet = TransmissionPacket(
            patient_id=self.patient_id,
            device_serial=self.device_serial,
            transmission_type="alert_triggered",
            timestamp_s=timestamp_s,
            events=critical_alerts,
            alert_flags=[a.payload.get("alert_type", "") for a in critical_alerts],
        )
        # Only clear the alerts that were sent
        for a in critical_alerts:
            if a in self._pending_alerts:
                self._pending_alerts.remove(a)
        return packet


class DualArchitectureRouter:
    """Routes the same event stream to both current and Chambers architectures simultaneously."""

    def __init__(
        self,
        current_arch_handler: Any = None,
        chambers_arch_handler: Any = None,
    ) -> None:
        self.current_arch = current_arch_handler
        self.chambers_arch = chambers_arch_handler
        self._events_routed = 0

    def route(self, event: TelemetryEvent) -> None:
        """Route a single event to both architectures."""
        if self.current_arch is not None:
            self.current_arch.ingest(event)
        if self.chambers_arch is not None:
            self.chambers_arch.ingest(event)
        self._events_routed += 1

    def route_transmission(self, packet: TransmissionPacket) -> None:
        """Route a transmission packet to both architectures."""
        if self.current_arch is not None:
            self.current_arch.ingest_transmission(packet)
        if self.chambers_arch is not None:
            self.chambers_arch.ingest_transmission(packet)

    @property
    def stats(self) -> dict[str, int]:
        return {"events_routed": self._events_routed}
