"""Burn scheduler — manages burn schedules across all worlds."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BurnEvent:
    """Record of a burn execution."""
    burn_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    record_id: str = ""
    world: str = ""
    patient_id: str = ""
    burn_policy: str = ""
    scheduled_at_s: float = 0.0
    executed_at_s: float = 0.0
    size_bytes: int = 0
    data_type: str = ""
    was_held: bool = False
    verification_method: str = "audit_log"


@dataclass
class ScheduledBurn:
    """A pending burn to be executed at a specific time."""
    schedule_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    record_id: str = ""
    world_name: str = ""
    patient_id: str = ""
    burn_at_s: float = 0.0
    policy: str = ""
    held: bool = False
    hold_id: str | None = None


class BurnScheduler:
    """Manages burn schedules for all typed worlds.

    Responsibilities:
    - Polls each world for burn candidates
    - Executes burns at scheduled times
    - Respects safety investigation holds
    - Logs all burn events
    - Produces burn verification certificates
    """

    def __init__(self, worlds: dict[str, Any] | None = None,
                 relay: Any | None = None) -> None:
        self.worlds = worlds or {}
        self.relay = relay

        # Scheduled burns
        self._scheduled: dict[str, ScheduledBurn] = {}

        # Burn history
        self._burn_history: list[BurnEvent] = []

        # Metrics
        self._total_burns = 0
        self._total_bytes_burned = 0
        self._total_held_skips = 0
        self._burns_by_world: dict[str, int] = {}

    def tick(self, current_time_s: float) -> list[BurnEvent]:
        """Execute all due burns. Called periodically by the simulation.

        Returns list of burn events executed.
        """
        executed: list[BurnEvent] = []

        # 1. Process relay TTL expirations
        if self.relay is not None:
            relay_burned = self.relay.process_burns(current_time_s)
            for item_id in relay_burned:
                event = BurnEvent(
                    record_id=item_id,
                    world="relay",
                    burn_policy="ttl_expiry",
                    scheduled_at_s=current_time_s,
                    executed_at_s=current_time_s,
                    verification_method="ttl_expiry",
                )
                self._record_burn(event)
                executed.append(event)

        # 2. Poll each world for burn candidates
        for world_name, world in self.worlds.items():
            candidates = world.get_burn_candidates(current_time_s)
            for record_id in candidates:
                # Get record info before burning
                record = world._all_records.get(record_id)
                if record is None:
                    continue

                if record.held:
                    self._total_held_skips += 1
                    continue

                # Execute burn
                success = world.burn(record_id, current_time_s)
                if success:
                    event = BurnEvent(
                        record_id=record_id,
                        world=world_name,
                        patient_id=record.patient_id,
                        burn_policy=self._get_policy_name(world_name),
                        scheduled_at_s=current_time_s,
                        executed_at_s=current_time_s,
                        size_bytes=record.size_bytes,
                        data_type=record.event_type,
                        verification_method="cryptographic_deletion",
                    )
                    self._record_burn(event)
                    executed.append(event)

        # 3. Execute scheduled burns
        for schedule_id, scheduled in list(self._scheduled.items()):
            if scheduled.held:
                continue
            if current_time_s >= scheduled.burn_at_s:
                world = self.worlds.get(scheduled.world_name)
                if world is not None:
                    record = world._all_records.get(scheduled.record_id)
                    if record and not record.held:
                        success = world.burn(scheduled.record_id, current_time_s)
                        if success:
                            event = BurnEvent(
                                record_id=scheduled.record_id,
                                world=scheduled.world_name,
                                patient_id=scheduled.patient_id,
                                burn_policy=scheduled.policy,
                                scheduled_at_s=scheduled.burn_at_s,
                                executed_at_s=current_time_s,
                                size_bytes=record.size_bytes if record else 0,
                                data_type=record.event_type if record else "",
                                verification_method="scheduled_burn",
                            )
                            self._record_burn(event)
                            executed.append(event)

                del self._scheduled[schedule_id]

        return executed

    def schedule_burn(self, record_id: str, world_name: str, patient_id: str,
                      burn_at_s: float, policy: str = "manual") -> ScheduledBurn:
        """Schedule a specific burn for a future time."""
        scheduled = ScheduledBurn(
            record_id=record_id,
            world_name=world_name,
            patient_id=patient_id,
            burn_at_s=burn_at_s,
            policy=policy,
        )
        self._scheduled[scheduled.schedule_id] = scheduled
        return scheduled

    def suspend_burns(self, patient_id: str, hold_id: str) -> int:
        """Suspend all scheduled burns for a patient (safety hold)."""
        suspended = 0
        for scheduled in self._scheduled.values():
            if scheduled.patient_id == patient_id and not scheduled.held:
                scheduled.held = True
                scheduled.hold_id = hold_id
                suspended += 1
        return suspended

    def resume_burns(self, patient_id: str, buffer_extension_s: float = 0.0,
                     current_time_s: float = 0.0) -> int:
        """Resume burns after hold release, with optional buffer extension."""
        resumed = 0
        for scheduled in self._scheduled.values():
            if scheduled.patient_id == patient_id and scheduled.held:
                scheduled.held = False
                scheduled.hold_id = None
                # Extend burn time by buffer
                if buffer_extension_s > 0:
                    scheduled.burn_at_s = current_time_s + buffer_extension_s
                resumed += 1
        return resumed

    def _record_burn(self, event: BurnEvent) -> None:
        """Record a burn event in history."""
        self._burn_history.append(event)
        self._total_burns += 1
        self._total_bytes_burned += event.size_bytes
        world = event.world
        self._burns_by_world[world] = self._burns_by_world.get(world, 0) + 1

    @staticmethod
    def _get_policy_name(world_name: str) -> str:
        """Get the burn policy name for a world."""
        policies = {
            "clinical": "delivery_and_ack",
            "device_maintenance": "rolling_window",
            "research": "consent_or_programme",
            "patient": "patient_controlled",
            "safety_investigation": "post_investigation_buffer",
        }
        return policies.get(world_name, "unknown")

    def get_burn_history(self, world: str | None = None,
                         patient_id: str | None = None,
                         start_s: float | None = None,
                         end_s: float | None = None,
                         limit: int = 1000) -> list[BurnEvent]:
        """Query burn history with optional filters."""
        results = list(self._burn_history)
        if world:
            results = [e for e in results if e.world == world]
        if patient_id:
            results = [e for e in results if e.patient_id == patient_id]
        if start_s is not None:
            results = [e for e in results if e.executed_at_s >= start_s]
        if end_s is not None:
            results = [e for e in results if e.executed_at_s <= end_s]
        return results[:limit]

    def generate_verification_certificate(self, burn_id: str) -> dict[str, Any]:
        """Generate a burn verification certificate."""
        event = next((e for e in self._burn_history if e.burn_id == burn_id), None)
        if event is None:
            return {"error": "Burn event not found"}

        return {
            "certificate_id": str(uuid.uuid4()),
            "burn_id": event.burn_id,
            "record_id": event.record_id,
            "world": event.world,
            "patient_id": event.patient_id,
            "executed_at_s": event.executed_at_s,
            "size_bytes_destroyed": event.size_bytes,
            "verification_method": event.verification_method,
            "attestation": "Data element has been permanently destroyed per burn policy.",
            "policy": event.burn_policy,
        }

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_burns": self._total_burns,
            "total_bytes_burned": self._total_bytes_burned,
            "total_mb_burned": self._total_bytes_burned / (1024 * 1024),
            "total_held_skips": self._total_held_skips,
            "pending_scheduled": len(self._scheduled),
            "burns_by_world": dict(self._burns_by_world),
            "burn_history_size": len(self._burn_history),
        }
