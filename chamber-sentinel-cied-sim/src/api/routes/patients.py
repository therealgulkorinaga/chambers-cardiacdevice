"""
Patient and cohort management endpoints.

Provides CRUD for virtual patients, telemetry queries with time-range
filtering, portable FHIR record download, and cohort creation/inspection.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1", tags=["patients"])

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------

_patients: dict[str, dict[str, Any]] = {}
_patient_telemetry: dict[str, list[dict[str, Any]]] = {}
_cohorts: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class PatientCreateRequest(BaseModel):
    """Body for ``POST /patients``."""

    profile_id: str = Field(
        description="Patient profile archetype ID (e.g. 'P-001').",
    )
    device_type: str | None = Field(
        default=None,
        description="Override device type; defaults to the profile's device_type.",
    )
    age: int | None = Field(default=None, ge=0, le=120, description="Override age.")
    sex: str | None = Field(default=None, description="Override sex (male/female/other).")
    region: str | None = Field(default=None, description="Geographic region code.")
    overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key-value overrides applied on top of the profile.",
    )


class PatientResponse(BaseModel):
    """Single patient detail."""

    patient_id: str
    profile_id: str
    device_type: str
    device_serial: str
    age: int
    sex: str
    diagnosis: str
    comorbidities: list[str]
    region: str
    implant_age_days: int
    created_at: float


class PatientListResponse(BaseModel):
    """Paginated patient list."""

    patients: list[PatientResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class TelemetryPoint(BaseModel):
    """A single telemetry data point."""

    event_id: str
    event_type: str
    timestamp_s: float
    payload: dict[str, Any]
    size_bytes: int


class TelemetryQueryResponse(BaseModel):
    """Paginated telemetry query result."""

    patient_id: str
    events: list[TelemetryPoint]
    total: int
    start_s: float | None
    end_s: float | None


class PortableRecordResponse(BaseModel):
    """Simulated FHIR Bundle portable record."""

    resource_type: str = "Bundle"
    type: str = "document"
    patient_id: str
    generated_at: str
    entry_count: int
    entries: list[dict[str, Any]]


class CohortCreateRequest(BaseModel):
    """Body for ``POST /cohorts``."""

    name: str = Field(description="Human-friendly cohort name.")
    size: int = Field(default=100, ge=1, le=10_000, description="Number of patients.")
    profile_weights: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Map of profile_id to relative weight. "
            "If empty, default distribution is used."
        ),
    )
    age_mean: float = Field(default=72.0, description="Mean age of the cohort.")
    age_std: float = Field(default=12.0, ge=0.0, description="Standard deviation of age.")
    male_fraction: float = Field(
        default=0.55, ge=0.0, le=1.0, description="Fraction of male patients."
    )
    random_seed: int = Field(default=42, description="PRNG seed for reproducibility.")


class CohortResponse(BaseModel):
    """Cohort detail."""

    cohort_id: str
    name: str
    size: int
    created_at: float
    summary: dict[str, Any]
    patient_ids: list[str]


# ---------------------------------------------------------------------------
# Default patient data helpers
# ---------------------------------------------------------------------------

# Profile-to-defaults mapping (mirrors the YAML patient profiles)
_PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "P-001": {"device_type": "DDD", "age": 72, "sex": "male", "diagnosis": "Sick Sinus Syndrome", "comorbidities": ["hypertension"]},
    "P-002": {"device_type": "DDD", "age": 65, "sex": "male", "diagnosis": "Complete Heart Block", "comorbidities": []},
    "P-003": {"device_type": "DDD", "age": 78, "sex": "female", "diagnosis": "Paroxysmal AF + Bradycardia", "comorbidities": ["heart_failure", "diabetes"]},
    "P-004": {"device_type": "CRT_D", "age": 58, "sex": "male", "diagnosis": "Ischemic CMP + VT", "comorbidities": ["heart_failure", "ckd"]},
    "P-005": {"device_type": "CRT_D", "age": 45, "sex": "female", "diagnosis": "Idiopathic DCM", "comorbidities": ["heart_failure"]},
    "P-006": {"device_type": "VVI", "age": 82, "sex": "male", "diagnosis": "Post-AVR + CHB", "comorbidities": ["hypertension"]},
    "P-007": {"device_type": "DDD", "age": 28, "sex": "male", "diagnosis": "Young athlete CHB", "comorbidities": []},
    "P-008": {"device_type": "DDD", "age": 70, "sex": "male", "diagnosis": "AF + tachy-brady", "comorbidities": ["hypertension", "copd"]},
    "P-009": {"device_type": "ICD", "age": 35, "sex": "male", "diagnosis": "HCM + VT risk", "comorbidities": ["hcm"]},
    "P-010": {"device_type": "VVI", "age": 88, "sex": "female", "diagnosis": "Elderly multi-comorbidity", "comorbidities": ["heart_failure", "ckd", "diabetes", "atrial_fibrillation"]},
}


def _build_patient_record(
    profile_id: str,
    device_type: str | None = None,
    age: int | None = None,
    sex: str | None = None,
    region: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a patient dict from profile defaults + overrides."""
    defaults = _PROFILE_DEFAULTS.get(profile_id)
    if defaults is None:
        raise ValueError(f"Unknown profile_id '{profile_id}'")
    record: dict[str, Any] = {
        "patient_id": str(uuid.uuid4()),
        "profile_id": profile_id,
        "device_type": device_type or defaults["device_type"],
        "device_serial": f"SIM-{uuid.uuid4().hex[:8].upper()}",
        "age": age if age is not None else defaults["age"],
        "sex": sex or defaults["sex"],
        "diagnosis": defaults["diagnosis"],
        "comorbidities": list(defaults["comorbidities"]),
        "region": region or "US-NE",
        "implant_age_days": 0,
        "created_at": time.time(),
    }
    if overrides:
        for k, v in overrides.items():
            if k in record:
                record[k] = v
    return record


def _patient_to_response(rec: dict[str, Any]) -> PatientResponse:
    return PatientResponse(
        patient_id=rec["patient_id"],
        profile_id=rec["profile_id"],
        device_type=rec["device_type"],
        device_serial=rec["device_serial"],
        age=rec["age"],
        sex=rec["sex"],
        diagnosis=rec["diagnosis"],
        comorbidities=rec["comorbidities"],
        region=rec["region"],
        implant_age_days=rec["implant_age_days"],
        created_at=rec["created_at"],
    )


# ---------------------------------------------------------------------------
# Patient endpoints
# ---------------------------------------------------------------------------


@router.get("/patients", response_model=PatientListResponse)
async def list_patients(
    page: int = Query(default=1, ge=1, description="Page number (1-based)."),
    page_size: int = Query(default=20, ge=1, le=500, description="Items per page."),
    profile_id: str | None = Query(default=None, description="Filter by profile ID."),
    device_type: str | None = Query(default=None, description="Filter by device type."),
) -> PatientListResponse:
    """List virtual patients with pagination and optional profile/device filter."""
    all_patients = list(_patients.values())

    if profile_id is not None:
        all_patients = [p for p in all_patients if p["profile_id"] == profile_id]
    if device_type is not None:
        all_patients = [p for p in all_patients if p["device_type"] == device_type]

    total = len(all_patients)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = all_patients[start:end]

    return PatientListResponse(
        patients=[_patient_to_response(p) for p in page_items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post("/patients", response_model=PatientResponse, status_code=201)
async def create_patient(body: PatientCreateRequest) -> PatientResponse:
    """Create a new virtual patient from a profile archetype with optional overrides."""
    try:
        record = _build_patient_record(
            profile_id=body.profile_id,
            device_type=body.device_type,
            age=body.age,
            sex=body.sex,
            region=body.region,
            overrides=body.overrides,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    _patients[record["patient_id"]] = record
    _patient_telemetry[record["patient_id"]] = []
    return _patient_to_response(record)


@router.get("/patients/{patient_id}", response_model=PatientResponse)
async def get_patient(patient_id: str) -> PatientResponse:
    """Retrieve a single patient by ID."""
    record = _patients.get(patient_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient '{patient_id}' not found",
        )
    return _patient_to_response(record)


@router.get("/patients/{patient_id}/telemetry", response_model=TelemetryQueryResponse)
async def get_patient_telemetry(
    patient_id: str,
    start_s: float | None = Query(default=None, description="Start timestamp (seconds)."),
    end_s: float | None = Query(default=None, description="End timestamp (seconds)."),
    event_type: str | None = Query(default=None, description="Filter by event type."),
    limit: int = Query(default=200, ge=1, le=5000, description="Max events returned."),
) -> TelemetryQueryResponse:
    """Query telemetry events for a patient within a time range.

    Supports filtering by event_type and time range.  Results are ordered
    by ascending timestamp.
    """
    if patient_id not in _patients:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient '{patient_id}' not found",
        )

    events = list(_patient_telemetry.get(patient_id, []))

    if start_s is not None:
        events = [e for e in events if e["timestamp_s"] >= start_s]
    if end_s is not None:
        events = [e for e in events if e["timestamp_s"] <= end_s]
    if event_type is not None:
        events = [e for e in events if e["event_type"] == event_type]

    events.sort(key=lambda e: e["timestamp_s"])
    total = len(events)
    events = events[:limit]

    return TelemetryQueryResponse(
        patient_id=patient_id,
        events=[
            TelemetryPoint(
                event_id=e.get("event_id", str(uuid.uuid4())),
                event_type=e.get("event_type", "unknown"),
                timestamp_s=e.get("timestamp_s", 0.0),
                payload=e.get("payload", {}),
                size_bytes=e.get("size_bytes", 100),
            )
            for e in events
        ],
        total=total,
        start_s=start_s,
        end_s=end_s,
    )


@router.get("/patients/{patient_id}/portable-record", response_model=PortableRecordResponse)
async def get_portable_record(patient_id: str) -> PortableRecordResponse:
    """Download a simulated FHIR Bundle representing the patient's portable record.

    The portable record is the patient-controlled data extract in the Chambers
    architecture.  It contains the subset of clinical data that has been
    confirmed-delivered to the patient world.
    """
    record = _patients.get(patient_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Patient '{patient_id}' not found",
        )

    telemetry = _patient_telemetry.get(patient_id, [])

    # Build FHIR-like entries from patient data and telemetry
    entries: list[dict[str, Any]] = []

    # Patient resource
    entries.append({
        "resource": {
            "resourceType": "Patient",
            "id": patient_id,
            "gender": record["sex"],
            "birthDate": f"{2026 - record['age']}-01-01",
        },
        "request": {"method": "PUT", "url": f"Patient/{patient_id}"},
    })

    # Device resource
    entries.append({
        "resource": {
            "resourceType": "Device",
            "id": record["device_serial"],
            "type": {
                "coding": [{
                    "system": "http://snomed.info/sct",
                    "code": "706004007",
                    "display": f"Cardiac {record['device_type']} implant",
                }],
            },
            "patient": {"reference": f"Patient/{patient_id}"},
            "serialNumber": record["device_serial"],
        },
        "request": {"method": "PUT", "url": f"Device/{record['device_serial']}"},
    })

    # Condition resource for diagnosis
    entries.append({
        "resource": {
            "resourceType": "Condition",
            "id": f"cond-{patient_id[:8]}",
            "subject": {"reference": f"Patient/{patient_id}"},
            "code": {
                "text": record["diagnosis"],
            },
            "clinicalStatus": {
                "coding": [{"code": "active"}],
            },
        },
        "request": {"method": "PUT", "url": f"Condition/cond-{patient_id[:8]}"},
    })

    # Observation entries from telemetry
    for evt in telemetry[-100:]:  # cap at last 100 events
        obs_id = evt.get("event_id", str(uuid.uuid4()))
        entries.append({
            "resource": {
                "resourceType": "Observation",
                "id": obs_id,
                "status": "final",
                "category": [{
                    "coding": [{
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "vital-signs",
                    }],
                }],
                "code": {"text": evt.get("event_type", "unknown")},
                "subject": {"reference": f"Patient/{patient_id}"},
                "effectiveDateTime": f"epoch+{evt.get('timestamp_s', 0):.0f}s",
                "valueQuantity": evt.get("payload", {}),
            },
            "request": {"method": "PUT", "url": f"Observation/{obs_id}"},
        })

    from datetime import datetime, timezone

    return PortableRecordResponse(
        patient_id=patient_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        entry_count=len(entries),
        entries=entries,
    )


# ---------------------------------------------------------------------------
# Cohort endpoints
# ---------------------------------------------------------------------------


@router.post("/cohorts", response_model=CohortResponse, status_code=201)
async def create_cohort(body: CohortCreateRequest) -> CohortResponse:
    """Create a new patient cohort using the specified distribution parameters.

    All generated patients are also registered in the patient store.
    """
    cohort_id = str(uuid.uuid4())
    patient_ids: list[str] = []

    # Determine available profiles
    available_profiles = list(_PROFILE_DEFAULTS.keys())
    weights = body.profile_weights if body.profile_weights else {
        pid: 1.0 / len(available_profiles) for pid in available_profiles
    }
    # Normalize
    total_w = sum(weights.values())
    normed: dict[str, float] = {k: v / total_w for k, v in weights.items()}

    import numpy as np

    rng = np.random.default_rng(body.random_seed)

    profile_ids = list(normed.keys())
    profile_probs = np.array([normed[p] for p in profile_ids])
    profile_probs = profile_probs / profile_probs.sum()

    for i in range(body.size):
        patient_rng = np.random.default_rng(body.random_seed + i)
        chosen_profile = profile_ids[int(patient_rng.choice(len(profile_ids), p=profile_probs))]
        defaults = _PROFILE_DEFAULTS[chosen_profile]

        age = int(np.clip(
            patient_rng.normal(body.age_mean, body.age_std),
            18,
            100,
        ))
        sex = "male" if patient_rng.random() < body.male_fraction else "female"

        record = _build_patient_record(
            profile_id=chosen_profile,
            age=age,
            sex=sex,
        )
        _patients[record["patient_id"]] = record
        _patient_telemetry[record["patient_id"]] = []
        patient_ids.append(record["patient_id"])

    # Compute summary
    ages = [_patients[pid]["age"] for pid in patient_ids]
    device_counts: dict[str, int] = {}
    profile_counts: dict[str, int] = {}
    for pid in patient_ids:
        dt = _patients[pid]["device_type"]
        pr = _patients[pid]["profile_id"]
        device_counts[dt] = device_counts.get(dt, 0) + 1
        profile_counts[pr] = profile_counts.get(pr, 0) + 1

    summary: dict[str, Any] = {
        "size": len(patient_ids),
        "age_mean": float(np.mean(ages)) if ages else 0.0,
        "age_std": float(np.std(ages)) if ages else 0.0,
        "age_min": int(min(ages)) if ages else 0,
        "age_max": int(max(ages)) if ages else 0,
        "device_distribution": device_counts,
        "profile_distribution": profile_counts,
    }

    cohort_record: dict[str, Any] = {
        "cohort_id": cohort_id,
        "name": body.name,
        "size": len(patient_ids),
        "created_at": time.time(),
        "summary": summary,
        "patient_ids": patient_ids,
    }
    _cohorts[cohort_id] = cohort_record

    return CohortResponse(**cohort_record)


@router.get("/cohorts/{cohort_id}", response_model=CohortResponse)
async def get_cohort(cohort_id: str) -> CohortResponse:
    """Retrieve a cohort by ID including its summary statistics and member list."""
    cohort = _cohorts.get(cohort_id)
    if cohort is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Cohort '{cohort_id}' not found",
        )
    return CohortResponse(**cohort)
