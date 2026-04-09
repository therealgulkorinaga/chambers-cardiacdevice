"""Data consumer actors for the current architecture simulation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AccessEvent:
    """Records a data access by a consumer."""
    consumer: str
    patient_id: str | None
    timestamp_s: float
    data_scope: str
    purpose: str
    records_accessed: int = 0
    bytes_accessed: int = 0


class BaseConsumer:
    """Base data consumer with access logging."""

    def __init__(self, consumer_type: str, access_level: str = "read") -> None:
        self.consumer_type = consumer_type
        self.access_level = access_level
        self._access_log: list[AccessEvent] = []

    def _log_access(self, patient_id: str | None, timestamp_s: float,
                    scope: str, purpose: str, records: int = 0, bytes_: int = 0) -> None:
        self._access_log.append(AccessEvent(
            consumer=self.consumer_type, patient_id=patient_id,
            timestamp_s=timestamp_s, data_scope=scope, purpose=purpose,
            records_accessed=records, bytes_accessed=bytes_,
        ))

    @property
    def access_count(self) -> int:
        return len(self._access_log)

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "consumer": self.consumer_type,
            "access_level": self.access_level,
            "total_accesses": len(self._access_log),
            "total_bytes_accessed": sum(a.bytes_accessed for a in self._access_log),
        }


class OEMConsumer(BaseConsumer):
    """Manufacturer (OEM) consumer — full access to all data."""

    def __init__(self) -> None:
        super().__init__("oem", "full")

    def batch_query(self, cloud: Any, device_model: str, timestamp_s: float) -> list:
        """Batch query for device performance by model."""
        records = cloud.query_device(device_model) if hasattr(cloud, 'query_device') else []
        self._log_access(None, timestamp_s, "all_devices", "product_analytics", len(records))
        return records

    def safety_signal_detection(self, cloud: Any, timestamp_s: float) -> dict[str, Any]:
        """Run safety signal detection algorithms."""
        self._log_access(None, timestamp_s, "population", "safety_monitoring")
        return {"status": "no_signals_detected", "timestamp_s": timestamp_s}

    def product_lifecycle(self, cloud: Any, timestamp_s: float) -> dict[str, Any]:
        """Product lifecycle management queries."""
        self._log_access(None, timestamp_s, "all_devices", "lifecycle_management")
        return {"active_devices": cloud.patient_count if hasattr(cloud, 'patient_count') else 0}


class ClinicianConsumer(BaseConsumer):
    """Treating physician consumer — own patients only."""

    def __init__(self, clinician_id: str = "DR-001") -> None:
        super().__init__("clinician", "patient_restricted")
        self.clinician_id = clinician_id
        self.assigned_patients: set[str] = set()

    def review_alert(self, cloud: Any, patient_id: str, timestamp_s: float) -> list:
        """Review alerts for a patient."""
        if patient_id not in self.assigned_patients:
            return []
        alerts = cloud.get_pending_alerts(patient_id) if hasattr(cloud, 'get_pending_alerts') else []
        self._log_access(patient_id, timestamp_s, "alerts", "clinical_review", len(alerts))
        return alerts

    def view_egm(self, cloud: Any, patient_id: str, timestamp_s: float) -> list:
        """View EGM strips for a patient."""
        records = cloud.query_patient(patient_id, record_type="episode_start") if hasattr(cloud, 'query_patient') else []
        self._log_access(patient_id, timestamp_s, "egm", "clinical_review", len(records))
        return records


class HospitalConsumer(BaseConsumer):
    """Hospital EMR consumer — imported via HL7/FHIR."""

    def __init__(self, hospital_id: str = "HOSP-001") -> None:
        super().__init__("hospital", "emr_import")
        self.hospital_id = hospital_id

    def import_report(self, report_data: dict, patient_id: str, timestamp_s: float) -> dict:
        """Import transmission report to EMR."""
        self._log_access(patient_id, timestamp_s, "transmission_report", "emr_import")
        return {"imported": True, "hospital_id": self.hospital_id}


class InsurerConsumer(BaseConsumer):
    """Insurance consumer — claims-based, no direct device data."""

    def __init__(self) -> None:
        super().__init__("insurer", "claims_only")

    def process_claim(self, diagnosis_codes: list[str], procedure_codes: list[str],
                      patient_id: str, timestamp_s: float) -> dict:
        """Process a claims-based data request."""
        self._log_access(patient_id, timestamp_s, "claims", "coverage_decision")
        return {"codes_received": len(diagnosis_codes) + len(procedure_codes)}


class RegulatorConsumer(BaseConsumer):
    """Regulatory consumer (FDA/Notified Body) — on-request access."""

    def __init__(self) -> None:
        super().__init__("regulator", "on_request")

    def request_patient_data(self, cloud: Any, patient_id: str,
                              reason: str, timestamp_s: float) -> list:
        """Request specific patient data for investigation."""
        records = cloud.get_all_patient_data(patient_id) if hasattr(cloud, 'get_all_patient_data') else []
        self._log_access(patient_id, timestamp_s, "full_patient", reason, len(records))
        return records

    def request_aggregate_safety(self, cloud: Any, timestamp_s: float) -> dict:
        """Request aggregate safety data."""
        self._log_access(None, timestamp_s, "aggregate", "safety_review")
        return {"status": "data_provided"}
