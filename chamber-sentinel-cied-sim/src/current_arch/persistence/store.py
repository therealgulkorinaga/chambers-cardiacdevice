"""Persistence store abstraction for the current architecture simulation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PersistenceMetrics:
    """Tracks persistence volume over time."""
    total_bytes: int = 0
    total_records: int = 0
    bytes_by_layer: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    bytes_by_patient: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    bytes_by_data_type: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    snapshots: list[dict[str, Any]] = field(default_factory=list)

    def record_storage(self, layer: str, patient_id: str, data_type: str, size_bytes: int) -> None:
        self.total_bytes += size_bytes
        self.total_records += 1
        self.bytes_by_layer[layer] += size_bytes
        self.bytes_by_patient[patient_id] += size_bytes
        self.bytes_by_data_type[data_type] += size_bytes

    def take_snapshot(self, timestamp_s: float) -> dict[str, Any]:
        snapshot = {
            "timestamp_s": timestamp_s,
            "total_bytes": self.total_bytes,
            "total_records": self.total_records,
            "total_mb": self.total_bytes / (1024 * 1024),
            "by_layer": dict(self.bytes_by_layer),
            "patient_count": len(self.bytes_by_patient),
        }
        self.snapshots.append(snapshot)
        return snapshot

    def get_time_series(self) -> list[dict[str, Any]]:
        return list(self.snapshots)


class CurrentArchPersistence:
    """Wraps all current architecture layers and tracks total persistence.

    This is the coordinator that connects Layer 1-5 and provides
    a unified view of data persistence for analytics.
    """

    def __init__(self) -> None:
        self.metrics = PersistenceMetrics()
        self._retention_policy = "indefinite"

    def record_on_device(self, patient_id: str, data_type: str, size_bytes: int) -> None:
        self.metrics.record_storage("on_device", patient_id, data_type, size_bytes)

    def record_transmitter(self, patient_id: str, data_type: str, size_bytes: int) -> None:
        self.metrics.record_storage("transmitter", patient_id, data_type, size_bytes)

    def record_cloud(self, patient_id: str, data_type: str, size_bytes: int) -> None:
        self.metrics.record_storage("cloud", patient_id, data_type, size_bytes)

    def record_clinician(self, patient_id: str, data_type: str, size_bytes: int) -> None:
        self.metrics.record_storage("clinician_portal", patient_id, data_type, size_bytes)

    def record_aggregate(self, data_type: str, size_bytes: int) -> None:
        self.metrics.record_storage("aggregate_pool", "aggregate", data_type, size_bytes)

    @property
    def total_persisted_bytes(self) -> int:
        return self.metrics.total_bytes

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_bytes": self.metrics.total_bytes,
            "total_mb": self.metrics.total_bytes / (1024 * 1024),
            "total_records": self.metrics.total_records,
            "by_layer": dict(self.metrics.bytes_by_layer),
            "by_data_type": dict(self.metrics.bytes_by_data_type),
            "patient_count": len(self.metrics.bytes_by_patient),
            "retention_policy": self._retention_policy,
        }
