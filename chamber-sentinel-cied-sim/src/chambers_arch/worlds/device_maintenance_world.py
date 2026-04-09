"""Device Maintenance World — minimal device-focused data with rolling retention."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.generator.stream import World, EventType
from src.chambers_arch.worlds.base_world import BaseWorld, WorldRecord


class DeviceMaintenanceWorld(BaseWorld):
    """Device Maintenance World: Lead impedance, battery status, firmware version.

    Scope: Device-focused data ONLY. NO IEGMs, NO episodes, NO activity data.
    Access: Manufacturer (warranty/recall purposes only)
    Burn policy: Rolling retention window — oldest data burns when window advances.

    Key capability: "Which devices of model X, firmware Y are still active?"
    NOT capable of: "What was patient Z's arrhythmia history?"
    """

    ALLOWED_EVENTS = {
        EventType.DEVICE_STATUS,
        EventType.LEAD_MEASUREMENT,
        EventType.FIRMWARE_UPDATE,
        EventType.TRANSMISSION,  # Only device metadata from transmissions
    }

    AUTHORIZED_ACTORS = {"manufacturer", "system"}

    def __init__(self, retention_window_days: int = 90) -> None:
        super().__init__(
            world_type=World.DEVICE_MAINTENANCE,
            allowed_event_types=self.ALLOWED_EVENTS,
            authorized_actors=self.AUTHORIZED_ACTORS,
        )
        self.retention_window_days = retention_window_days
        self.retention_window_s = retention_window_days * 86400.0

        # Device registry (permanent — device identifiers are always retained)
        self._device_registry: dict[str, dict[str, Any]] = {}  # device_serial -> info

        # Alert summary counts (rolling, no episode detail)
        self._alert_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def _on_accept(self, record: WorldRecord) -> None:
        """Update device registry and strip non-device data from transmissions."""
        device_serial = record.device_serial

        # Update/create device registry entry
        if device_serial not in self._device_registry:
            self._device_registry[device_serial] = {
                "device_serial": device_serial,
                "first_seen_s": record.timestamp_s,
            }

        registry = self._device_registry[device_serial]
        registry["last_transmission_s"] = record.timestamp_s

        if record.event_type == EventType.DEVICE_STATUS.value:
            registry["device_model"] = record.data.get("device_model", registry.get("device_model", "unknown"))
            registry["device_type"] = record.data.get("device_type", registry.get("device_type", "unknown"))
            if "battery_voltage" in record.data:
                registry["latest_battery_voltage"] = record.data["battery_voltage"]
            if "battery_stage" in record.data:
                registry["latest_battery_stage"] = record.data["battery_stage"]

        elif record.event_type == EventType.LEAD_MEASUREMENT.value:
            lead_id = record.data.get("lead_id", "unknown")
            registry[f"latest_impedance_{lead_id}"] = record.data.get("impedance_ohms", 0)

        elif record.event_type == EventType.FIRMWARE_UPDATE.value:
            registry["firmware_version"] = record.data.get("new_version", registry.get("firmware_version", "unknown"))

        elif record.event_type == EventType.TRANSMISSION.value:
            # Only keep device metadata from transmissions — strip clinical data
            record.data = {
                k: v for k, v in record.data.items()
                if k in ("device_serial", "device_type", "device_model",
                         "battery_voltage", "battery_stage", "firmware_version",
                         "transmission_type", "payload_size_bytes")
            }

            # Track alert counts (just counts, no detail)
            for alert_flag in record.data.get("alert_flags", []):
                self._alert_counts[device_serial][alert_flag] += 1

        # Set burn schedule based on retention window
        record.burn_scheduled_at_s = record.timestamp_s + self.retention_window_s

    def _on_burn(self, record: WorldRecord) -> None:
        """No special cleanup needed — device registry is permanent."""
        pass

    def get_burn_candidates(self, timestamp_s: float) -> list[str]:
        """Return records that have exceeded the rolling retention window."""
        candidates: list[str] = []
        cutoff_s = timestamp_s - self.retention_window_s

        for record_id, record in list(self._all_records.items()):
            if record.held:
                continue
            if record.timestamp_s < cutoff_s:
                candidates.append(record_id)

        return candidates

    def query_active_devices(self, device_model: str | None = None,
                              firmware_version: str | None = None) -> list[dict[str, Any]]:
        """Query active devices. This is the key recall-support capability.

        Answers: "Which devices of model X, firmware Y are still active?"
        """
        results = list(self._device_registry.values())

        if device_model:
            results = [d for d in results if d.get("device_model") == device_model]
        if firmware_version:
            results = [d for d in results if d.get("firmware_version") == firmware_version]

        return results

    def get_device_info(self, device_serial: str) -> dict[str, Any] | None:
        """Get current info for a specific device."""
        return self._device_registry.get(device_serial)

    def get_alert_counts(self, device_serial: str) -> dict[str, int]:
        """Get alert summary counts for a device (no episode detail)."""
        return dict(self._alert_counts.get(device_serial, {}))

    def get_status(self) -> dict[str, Any]:
        status = super().get_status()
        status.update({
            "retention_window_days": self.retention_window_days,
            "registered_devices": len(self._device_registry),
            "note": "Device-focused only. No IEGMs, episodes, or patient activity.",
        })
        return status
