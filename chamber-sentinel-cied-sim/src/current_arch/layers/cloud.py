"""Layer 3: Manufacturer Cloud — persistent storage with indefinite retention."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CloudRecord:
    """A single record in the manufacturer cloud database."""
    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    patient_id: str = ""
    device_serial: str = ""
    record_type: str = ""  # transmission, alert, episode, device_status, report
    timestamp_s: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)
    size_bytes: int = 0
    ingested_at_s: float = 0.0
    processed: bool = False
    alerts_generated: list[str] = field(default_factory=list)


class ManufacturerCloud:
    """Layer 3: Manufacturer-controlled cloud platform.

    Models indefinite retention of all transmitted data.
    This is the central persistence point in the current architecture.

    All data flows through here and is retained indefinitely.
    No automatic deletion. No burn semantics.
    """

    def __init__(self, retention_days: int | None = None) -> None:
        # None = indefinite retention (default manufacturer behavior)
        self.retention_days = retention_days

        # Primary storage: patient_id -> list of records (time-ordered)
        self._records: dict[str, list[CloudRecord]] = defaultdict(list)

        # Indexes
        self._by_device: dict[str, list[str]] = defaultdict(list)  # device_serial -> record_ids
        self._by_type: dict[str, list[str]] = defaultdict(list)  # record_type -> record_ids
        self._alerts_pending: list[CloudRecord] = []
        self._all_record_ids: dict[str, CloudRecord] = {}

        # Metrics
        self._total_records = 0
        self._total_bytes = 0
        self._total_transmissions_ingested = 0
        self._bytes_by_type: dict[str, int] = defaultdict(int)
        self._bytes_by_patient: dict[str, int] = defaultdict(int)

    def ingest_transmission(self, transmission_data: dict[str, Any], timestamp_s: float) -> CloudRecord:
        """Ingest a transmission from Layer 2 (Transmitter).

        Processes the data through the ingestion pipeline:
        1. Parse and structure
        2. Run alert algorithms
        3. Generate reports
        4. Store everything
        """
        patient_id = transmission_data.get("patient_id", "unknown")
        device_serial = transmission_data.get("device_serial", "unknown")
        payload_size = transmission_data.get("size_bytes", 500)

        # Create the cloud record
        record = CloudRecord(
            patient_id=patient_id,
            device_serial=device_serial,
            record_type="transmission",
            timestamp_s=timestamp_s,
            data=transmission_data,
            size_bytes=payload_size,
            ingested_at_s=timestamp_s,
            processed=True,
        )

        self._store_record(record)
        self._total_transmissions_ingested += 1

        # Extract and store sub-records (episodes, alerts, etc.)
        events = transmission_data.get("events", [])
        for event in events:
            if isinstance(event, dict):
                sub_record = CloudRecord(
                    patient_id=patient_id,
                    device_serial=device_serial,
                    record_type=event.get("event_type", "unknown"),
                    timestamp_s=event.get("timestamp_s", timestamp_s),
                    data=event,
                    size_bytes=event.get("size_bytes", 100),
                    ingested_at_s=timestamp_s,
                    processed=True,
                )
                self._store_record(sub_record)

        # Store alerts
        for alert_flag in transmission_data.get("alert_flags", []):
            alert_record = CloudRecord(
                patient_id=patient_id,
                device_serial=device_serial,
                record_type="alert",
                timestamp_s=timestamp_s,
                data={"alert_type": alert_flag, "source_transmission": record.record_id},
                size_bytes=256,
                ingested_at_s=timestamp_s,
            )
            self._store_record(alert_record)
            self._alerts_pending.append(alert_record)

        return record

    def ingest_event(self, event: Any, timestamp_s: float) -> CloudRecord:
        """Ingest a single telemetry event directly."""
        patient_id = getattr(event, "patient_id", "unknown")
        device_serial = getattr(event, "device_serial", "unknown")
        event_type = getattr(event, "event_type", None)
        payload = getattr(event, "payload", {})
        size_bytes = getattr(event, "size_bytes", 100)

        record = CloudRecord(
            patient_id=patient_id,
            device_serial=device_serial,
            record_type=event_type.value if event_type else "unknown",
            timestamp_s=timestamp_s,
            data=payload if isinstance(payload, dict) else {"value": payload},
            size_bytes=size_bytes,
            ingested_at_s=timestamp_s,
            processed=True,
        )
        self._store_record(record)
        return record

    def _store_record(self, record: CloudRecord) -> None:
        """Store a record in all indexes."""
        self._records[record.patient_id].append(record)
        self._by_device[record.device_serial].append(record.record_id)
        self._by_type[record.record_type].append(record.record_id)
        self._all_record_ids[record.record_id] = record

        self._total_records += 1
        self._total_bytes += record.size_bytes
        self._bytes_by_type[record.record_type] += record.size_bytes
        self._bytes_by_patient[record.patient_id] += record.size_bytes

    def query_patient(
        self,
        patient_id: str,
        record_type: str | None = None,
        start_s: float | None = None,
        end_s: float | None = None,
        limit: int = 1000,
    ) -> list[CloudRecord]:
        """Query records for a patient with optional filters."""
        records = self._records.get(patient_id, [])

        if record_type is not None:
            records = [r for r in records if r.record_type == record_type]
        if start_s is not None:
            records = [r for r in records if r.timestamp_s >= start_s]
        if end_s is not None:
            records = [r for r in records if r.timestamp_s <= end_s]

        return records[:limit]

    def query_device(self, device_serial: str) -> list[CloudRecord]:
        """Query all records for a device."""
        record_ids = self._by_device.get(device_serial, [])
        return [self._all_record_ids[rid] for rid in record_ids if rid in self._all_record_ids]

    def get_pending_alerts(self, patient_id: str | None = None) -> list[CloudRecord]:
        """Get unacknowledged alerts, optionally filtered by patient."""
        alerts = self._alerts_pending
        if patient_id:
            alerts = [a for a in alerts if a.patient_id == patient_id]
        return alerts

    def acknowledge_alert(self, record_id: str, timestamp_s: float) -> bool:
        """Mark an alert as acknowledged by clinician."""
        self._alerts_pending = [a for a in self._alerts_pending if a.record_id != record_id]
        if record_id in self._all_record_ids:
            self._all_record_ids[record_id].data["acknowledged"] = True
            self._all_record_ids[record_id].data["acknowledged_at_s"] = timestamp_s
            return True
        return False

    def get_all_patient_data(self, patient_id: str) -> list[CloudRecord]:
        """Get ALL data for a patient. Used for law enforcement / subpoena scenarios."""
        return list(self._records.get(patient_id, []))

    def apply_retention_policy(self, current_time_s: float) -> int:
        """Apply retention policy. Returns number of records deleted.
        In the current architecture with no retention limit, this is a no-op.
        """
        if self.retention_days is None:
            return 0  # Indefinite retention — never delete

        cutoff_s = current_time_s - (self.retention_days * 86400)
        deleted = 0

        for patient_id in list(self._records.keys()):
            before = len(self._records[patient_id])
            self._records[patient_id] = [
                r for r in self._records[patient_id] if r.timestamp_s >= cutoff_s
            ]
            removed = before - len(self._records[patient_id])
            deleted += removed

        return deleted

    @property
    def total_data_volume_bytes(self) -> int:
        return self._total_bytes

    @property
    def total_records(self) -> int:
        return self._total_records

    @property
    def patient_count(self) -> int:
        return len(self._records)

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_records": self._total_records,
            "total_bytes": self._total_bytes,
            "total_mb": self._total_bytes / (1024 * 1024),
            "total_transmissions_ingested": self._total_transmissions_ingested,
            "patient_count": len(self._records),
            "pending_alerts": len(self._alerts_pending),
            "retention_policy": "indefinite" if self.retention_days is None else f"{self.retention_days} days",
            "bytes_by_type": dict(self._bytes_by_type),
            "oldest_record_s": min(
                (r.timestamp_s for records in self._records.values() for r in records),
                default=0.0,
            ),
        }
