"""Patient World — patient-controlled portable record."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from src.generator.stream import World, EventType
from src.chambers_arch.worlds.base_world import BaseWorld, WorldRecord


@dataclass
class Delegate:
    """A person delegated access to the patient's record."""
    delegate_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    relationship: str = ""  # 'family', 'caregiver', 'legal_guardian'
    access_level: str = "read"  # 'read' only — no delete
    delegated_at_s: float = 0.0
    revoked: bool = False
    revoked_at_s: float | None = None
    is_primary: bool = False
    is_secondary: bool = False


@dataclass
class EmergencyDataset:
    """Minimal dataset available without authentication for emergency access."""
    device_type: str = ""
    device_serial: str = ""
    device_model: str = ""
    current_programming_summary: str = ""
    treating_physician: str = ""
    treating_physician_phone: str = ""
    emergency_contact: str = ""
    known_allergies: list[str] = field(default_factory=list)
    last_3_transmission_summaries: list[dict[str, Any]] = field(default_factory=list)


class PatientWorld(BaseWorld):
    """Patient World: Patient-controlled portable record.

    Accepts ALL data types — the patient chooses what to retain.
    No automatic burn — patient controls retention.
    Independent of manufacturer infrastructure.

    Storage: Simulated encrypted SQLite (AES-256).
    Format: FHIR R4 resources.
    Export: FHIR JSON Bundle, PDF summary, CSV.
    """

    # Accept everything
    ALLOWED_EVENTS = set(EventType)

    AUTHORIZED_ACTORS = {"patient", "delegate", "clinician", "system", "emergency"}

    def __init__(self, patient_id: str = "") -> None:
        super().__init__(
            world_type=World.PATIENT,
            allowed_event_types=self.ALLOWED_EVENTS,
            authorized_actors=self.AUTHORIZED_ACTORS,
        )
        self.owner_patient_id = patient_id

        # Delegation
        self._delegates: list[Delegate] = []

        # Emergency dataset
        self._emergency_dataset = EmergencyDataset()

        # Retention preferences per data type
        self._retention_preferences: dict[str, str] = {}  # event_type -> 'keep'|'auto_expire'|'delete'

        # Election status: has the patient elected manufacturer persistence?
        self._manufacturer_persistence_elected: dict[str, bool] = {
            "clinical": False,
            "activity": False,
            "device_status": False,
        }

        # FHIR export tracking
        self._fhir_resources: list[dict[str, Any]] = []

        # Patient death state
        self._patient_deceased = False
        self._death_timestamp_s: float | None = None
        self._delegate_access_expiry_s: float | None = None  # 2 years after death

    def _on_accept(self, record: WorldRecord) -> None:
        """Convert to FHIR resource and update emergency dataset."""
        fhir_resource = self._to_fhir_resource(record)
        self._fhir_resources.append(fhir_resource)

        # Update emergency dataset with latest device info
        if record.event_type in (EventType.DEVICE_STATUS.value, "device_status"):
            self._emergency_dataset.device_type = record.data.get(
                "device_type", self._emergency_dataset.device_type
            )
            self._emergency_dataset.device_serial = record.device_serial
            self._emergency_dataset.device_model = record.data.get(
                "device_model", self._emergency_dataset.device_model
            )

        elif record.event_type in (EventType.TRANSMISSION.value, "transmission"):
            summaries = self._emergency_dataset.last_3_transmission_summaries
            summaries.append({
                "timestamp_s": record.timestamp_s,
                "type": record.data.get("transmission_type", "unknown"),
                "alert_flags": record.data.get("alert_flags", []),
            })
            self._emergency_dataset.last_3_transmission_summaries = summaries[-3:]

    def _on_burn(self, record: WorldRecord) -> None:
        """Remove FHIR resource on patient-initiated burn."""
        self._fhir_resources = [
            r for r in self._fhir_resources
            if r.get("_source_record_id") != record.record_id
        ]

    def _to_fhir_resource(self, record: WorldRecord) -> dict[str, Any]:
        """Convert a world record to a FHIR R4 resource."""
        resource_type = self._map_event_to_fhir_type(record.event_type)

        resource: dict[str, Any] = {
            "resourceType": resource_type,
            "id": str(uuid.uuid4()),
            "status": "final",
            "subject": {"reference": f"Patient/{record.patient_id}"},
            "effectiveDateTime": record.timestamp_s,
            "device": {"reference": f"Device/{record.device_serial}"},
            "_source_record_id": record.record_id,
        }

        if resource_type == "Observation":
            resource["code"] = {"coding": [{"system": "http://loinc.org", "code": record.event_type}]}
            if "heart_rate" in record.data:
                resource["valueQuantity"] = {
                    "value": record.data["heart_rate"],
                    "unit": "bpm",
                    "system": "http://unitsofmeasure.org",
                }
            elif "impedance_ohms" in record.data:
                resource["valueQuantity"] = {
                    "value": record.data["impedance_ohms"],
                    "unit": "Ohm",
                }
            else:
                resource["valueString"] = json.dumps(record.data)

        elif resource_type == "DiagnosticReport":
            resource["conclusion"] = record.data.get("transmission_type", "")
            resource["result"] = []

        elif resource_type == "DetectedIssue":
            resource["severity"] = record.data.get("priority", "moderate")
            resource["detail"] = record.data.get("alert_type", "")

        return resource

    @staticmethod
    def _map_event_to_fhir_type(event_type: str) -> str:
        """Map event type to FHIR resource type."""
        mapping = {
            "heartbeat": "Observation",
            "pacing": "Observation",
            "episode_start": "Observation",
            "episode_end": "Observation",
            "alert": "DetectedIssue",
            "transmission": "DiagnosticReport",
            "device_status": "Observation",
            "activity": "Observation",
            "lead_measurement": "Observation",
            "threshold_test": "Observation",
            "adverse_event": "AdverseEvent",
        }
        return mapping.get(event_type, "Observation")

    def add_delegate(self, name: str, relationship: str, is_primary: bool = False,
                     timestamp_s: float = 0.0) -> Delegate:
        """Add a delegate with read access."""
        delegate = Delegate(
            name=name,
            relationship=relationship,
            delegated_at_s=timestamp_s,
            is_primary=is_primary,
            is_secondary=not is_primary and not any(d.is_secondary for d in self._delegates if not d.revoked),
        )
        self._delegates.append(delegate)
        self.authorized_actors.add(f"delegate_{delegate.delegate_id}")
        self._audit(timestamp_s, "delegate_added", "patient",
                    details={"delegate_id": delegate.delegate_id, "relationship": relationship})
        return delegate

    def revoke_delegate(self, delegate_id: str, timestamp_s: float) -> bool:
        """Revoke a delegate's access."""
        for d in self._delegates:
            if d.delegate_id == delegate_id and not d.revoked:
                d.revoked = True
                d.revoked_at_s = timestamp_s
                self.authorized_actors.discard(f"delegate_{delegate_id}")
                self._audit(timestamp_s, "delegate_revoked", "patient",
                            details={"delegate_id": delegate_id})
                return True
        return False

    def get_emergency_dataset(self) -> EmergencyDataset:
        """Get the emergency dataset. No authentication required."""
        return self._emergency_dataset

    def generate_emergency_qr_data(self) -> dict[str, str]:
        """Generate data for emergency QR code."""
        ed = self._emergency_dataset
        return {
            "device_type": ed.device_type,
            "device_serial": ed.device_serial,
            "manufacturer": ed.device_model.split()[0] if ed.device_model else "",
            "treating_md": ed.treating_physician,
            "emergency_contact": ed.emergency_contact,
        }

    def export_fhir_bundle(self) -> dict[str, Any]:
        """Export all data as a FHIR R4 Bundle."""
        return {
            "resourceType": "Bundle",
            "type": "collection",
            "total": len(self._fhir_resources),
            "entry": [{"resource": r} for r in self._fhir_resources],
        }

    def elect_manufacturer_persistence(self, category: str, elected: bool,
                                        timestamp_s: float) -> None:
        """Patient elects (or revokes) manufacturer persistence for a data category."""
        if category in self._manufacturer_persistence_elected:
            self._manufacturer_persistence_elected[category] = elected
            self._audit(timestamp_s, "persistence_election", "patient",
                        details={"category": category, "elected": elected})

    def set_retention_preference(self, event_type: str, preference: str) -> None:
        """Set retention preference for a data type: 'keep', 'auto_expire', 'delete'."""
        self._retention_preferences[event_type] = preference

    def record_patient_death(self, timestamp_s: float, delegate_access_years: float = 2.0) -> None:
        """Record patient death. Delegate retains access for configured period."""
        self._patient_deceased = True
        self._death_timestamp_s = timestamp_s
        self._delegate_access_expiry_s = timestamp_s + (delegate_access_years * 365.25 * 86400)

    def get_burn_candidates(self, timestamp_s: float) -> list[str]:
        """Patient-controlled — no automatic burn candidates.

        Only returns records the patient has marked for deletion or
        auto-expire records past expiry.
        """
        candidates: list[str] = []

        for record_id, record in list(self._all_records.items()):
            if record.held:
                continue

            pref = self._retention_preferences.get(record.event_type, "keep")
            if pref == "delete":
                candidates.append(record_id)

        # If patient is deceased and delegate access has expired, burn all
        if (self._patient_deceased and self._delegate_access_expiry_s
                and timestamp_s > self._delegate_access_expiry_s):
            for record_id, record in self._all_records.items():
                if not record.held and record_id not in candidates:
                    candidates.append(record_id)

        return candidates

    def get_status(self) -> dict[str, Any]:
        status = super().get_status()
        active_delegates = [d for d in self._delegates if not d.revoked]
        status.update({
            "owner_patient_id": self.owner_patient_id,
            "fhir_resources": len(self._fhir_resources),
            "active_delegates": len(active_delegates),
            "manufacturer_persistence_elections": dict(self._manufacturer_persistence_elected),
            "patient_deceased": self._patient_deceased,
            "emergency_dataset_available": True,
        })
        return status
