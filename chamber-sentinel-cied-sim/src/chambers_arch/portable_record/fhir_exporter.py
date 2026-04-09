"""FHIR R4 exporter — maps CIED telemetry data to FHIR resources."""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class FHIRResource:
    """A FHIR R4 resource."""
    resource_type: str
    resource_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"resourceType": self.resource_type, "id": self.resource_id, **self.data}


class FHIRExporter:
    """Exports CIED telemetry data as FHIR R4 resources.

    Mappings:
    - Device: device info (serial, model, manufacturer, firmware, implant date)
    - Observation: measurements (HR, pacing %, impedance, battery, thresholds)
    - Observation (EGM): base64-encoded waveform with SampledData
    - DiagnosticReport: transmission summaries
    - Condition: arrhythmia diagnoses
    - Procedure: therapy deliveries (ATP, shock)
    """

    def __init__(self, patient_id: str, device_serial: str) -> None:
        self.patient_ref = f"Patient/{patient_id}"
        self.device_ref = f"Device/{device_serial}"
        self._resources: list[FHIRResource] = []

    def export_device(self, device_serial: str, device_model: str,
                      manufacturer: str, firmware_version: str,
                      implant_date: str = "") -> FHIRResource:
        """Export device information as FHIR Device resource."""
        resource = FHIRResource(
            resource_type="Device",
            data={
                "identifier": [{"system": "urn:cied:serial", "value": device_serial}],
                "manufacturer": manufacturer,
                "modelNumber": device_model,
                "version": [{"type": {"text": "firmware"}, "value": firmware_version}],
                "patient": {"reference": self.patient_ref},
                "status": "active",
            },
        )
        if implant_date:
            resource.data["manufactureDate"] = implant_date
        self._resources.append(resource)
        return resource

    def export_heart_rate(self, heart_rate: float, rhythm: str,
                          timestamp: str) -> FHIRResource:
        """Export heart rate as FHIR Observation."""
        resource = FHIRResource(
            resource_type="Observation",
            data={
                "status": "final",
                "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "vital-signs"}]}],
                "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4", "display": "Heart rate"}]},
                "subject": {"reference": self.patient_ref},
                "device": {"reference": self.device_ref},
                "effectiveDateTime": timestamp,
                "valueQuantity": {"value": heart_rate, "unit": "/min", "system": "http://unitsofmeasure.org", "code": "/min"},
                "component": [
                    {"code": {"text": "rhythm"}, "valueString": rhythm},
                ],
            },
        )
        self._resources.append(resource)
        return resource

    def export_lead_impedance(self, lead_id: str, impedance_ohms: float,
                               status: str, timestamp: str) -> FHIRResource:
        """Export lead impedance measurement."""
        resource = FHIRResource(
            resource_type="Observation",
            data={
                "status": "final",
                "code": {"coding": [{"system": "urn:cied:observation", "code": "lead-impedance", "display": f"Lead impedance ({lead_id})"}]},
                "subject": {"reference": self.patient_ref},
                "device": {"reference": self.device_ref},
                "effectiveDateTime": timestamp,
                "valueQuantity": {"value": impedance_ohms, "unit": "Ohm", "system": "http://unitsofmeasure.org", "code": "Ohm"},
                "interpretation": [{"text": status}],
            },
        )
        self._resources.append(resource)
        return resource

    def export_battery(self, voltage: float, stage: str,
                       timestamp: str) -> FHIRResource:
        """Export battery status."""
        resource = FHIRResource(
            resource_type="Observation",
            data={
                "status": "final",
                "code": {"coding": [{"system": "urn:cied:observation", "code": "battery-status", "display": "Battery status"}]},
                "subject": {"reference": self.patient_ref},
                "device": {"reference": self.device_ref},
                "effectiveDateTime": timestamp,
                "valueQuantity": {"value": voltage, "unit": "V", "system": "http://unitsofmeasure.org", "code": "V"},
                "component": [
                    {"code": {"text": "stage"}, "valueString": stage},
                ],
            },
        )
        self._resources.append(resource)
        return resource

    def export_egm_strip(self, channels: dict[str, Any], sample_rate_hz: int,
                          duration_ms: int, trigger_type: str,
                          timestamp: str) -> FHIRResource:
        """Export EGM strip as FHIR Observation with SampledData."""
        components = []
        for channel_name, samples in channels.items():
            if isinstance(samples, np.ndarray):
                encoded = base64.b64encode(samples.tobytes()).decode("ascii")
            else:
                encoded = base64.b64encode(bytes(str(samples), "utf-8")).decode("ascii")

            components.append({
                "code": {"text": channel_name},
                "valueSampledData": {
                    "origin": {"value": 0, "unit": "mV"},
                    "period": 1000.0 / sample_rate_hz,
                    "dimensions": 1,
                    "data": encoded,
                },
            })

        resource = FHIRResource(
            resource_type="Observation",
            data={
                "status": "final",
                "code": {"coding": [{"system": "urn:cied:observation", "code": "iegm", "display": "Intracardiac Electrogram"}]},
                "subject": {"reference": self.patient_ref},
                "device": {"reference": self.device_ref},
                "effectiveDateTime": timestamp,
                "component": components,
                "note": [{"text": f"Trigger: {trigger_type}, Duration: {duration_ms}ms, Rate: {sample_rate_hz}Hz"}],
            },
        )
        self._resources.append(resource)
        return resource

    def export_arrhythmia_episode(self, episode_type: str, duration_s: float,
                                   max_rate: float, terminated_by: str,
                                   timestamp: str) -> FHIRResource:
        """Export arrhythmia episode as FHIR Condition."""
        snomed_codes = {
            "AF": ("49436004", "Atrial fibrillation"),
            "AFL": ("5370000", "Atrial flutter"),
            "SVT": ("6456007", "Supraventricular tachycardia"),
            "VT": ("25569003", "Ventricular tachycardia"),
            "VF": ("71908006", "Ventricular fibrillation"),
        }
        code, display = snomed_codes.get(episode_type, ("unknown", episode_type))

        resource = FHIRResource(
            resource_type="Condition",
            data={
                "clinicalStatus": {"coding": [{"code": "resolved" if terminated_by != "ongoing" else "active"}]},
                "code": {"coding": [{"system": "http://snomed.info/sct", "code": code, "display": display}]},
                "subject": {"reference": self.patient_ref},
                "onsetDateTime": timestamp,
                "note": [{"text": f"Duration: {duration_s:.1f}s, Max rate: {max_rate:.0f}bpm, Terminated by: {terminated_by}"}],
            },
        )
        self._resources.append(resource)
        return resource

    def export_therapy_delivery(self, therapy_type: str, energy: float,
                                 success: bool, timestamp: str) -> FHIRResource:
        """Export therapy delivery (ATP/shock) as FHIR Procedure."""
        resource = FHIRResource(
            resource_type="Procedure",
            data={
                "status": "completed",
                "code": {"coding": [{"system": "urn:cied:therapy", "code": therapy_type, "display": f"Device therapy: {therapy_type}"}]},
                "subject": {"reference": self.patient_ref},
                "performedDateTime": timestamp,
                "outcome": {"text": "successful" if success else "unsuccessful"},
                "note": [{"text": f"Energy: {energy}J" if therapy_type == "shock" else "ATP burst pacing"}],
            },
        )
        self._resources.append(resource)
        return resource

    def export_transmission_report(self, transmission_type: str,
                                    alert_flags: list[str],
                                    timestamp: str) -> FHIRResource:
        """Export transmission as FHIR DiagnosticReport."""
        resource = FHIRResource(
            resource_type="DiagnosticReport",
            data={
                "status": "final",
                "code": {"coding": [{"system": "urn:cied:report", "code": "device-transmission", "display": "Device Transmission Report"}]},
                "subject": {"reference": self.patient_ref},
                "effectiveDateTime": timestamp,
                "conclusion": f"Transmission type: {transmission_type}. Alerts: {', '.join(alert_flags) if alert_flags else 'None'}",
            },
        )
        self._resources.append(resource)
        return resource

    def get_bundle(self) -> dict[str, Any]:
        """Export all resources as a FHIR Bundle."""
        return {
            "resourceType": "Bundle",
            "type": "collection",
            "total": len(self._resources),
            "entry": [
                {"fullUrl": f"urn:uuid:{r.resource_id}", "resource": r.to_dict()}
                for r in self._resources
            ],
        }

    def export_json(self) -> str:
        """Export bundle as JSON string."""
        return json.dumps(self.get_bundle(), indent=2, default=str)

    @property
    def resource_count(self) -> int:
        return len(self._resources)
