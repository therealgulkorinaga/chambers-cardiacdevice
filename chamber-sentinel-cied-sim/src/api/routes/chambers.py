"""
Chambers architecture-specific endpoints.

Exposes the internal state of the Chambers (burn-by-default) architecture:
typed-world status, relay health, burn history, and safety investigation
hold management.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.generator.stream import World

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/chambers", tags=["chambers"])

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------

# Simulated world status (in a full integration these would come from the
# actual world instances; here we maintain lightweight dicts)
_world_statuses: dict[str, dict[str, Any]] = {
    World.CLINICAL.value: {
        "world": World.CLINICAL.value,
        "total_accepted": 0,
        "total_rejected": 0,
        "total_burned": 0,
        "total_bytes": 0,
        "total_mb": 0.0,
        "total_queries": 0,
        "active_records": 0,
        "patient_count": 0,
        "active_holds": 0,
        "burn_policy": "Burn after clinician ACK + patient delivery confirmed",
        "allowed_event_types": [
            "heartbeat", "pacing", "episode_start", "episode_end",
            "alert", "transmission", "lead_measurement", "threshold_test",
        ],
    },
    World.DEVICE_MAINTENANCE.value: {
        "world": World.DEVICE_MAINTENANCE.value,
        "total_accepted": 0,
        "total_rejected": 0,
        "total_burned": 0,
        "total_bytes": 0,
        "total_mb": 0.0,
        "total_queries": 0,
        "active_records": 0,
        "patient_count": 0,
        "active_holds": 0,
        "burn_policy": "Rolling 90-day window; burn data older than window",
        "allowed_event_types": [
            "device_status", "transmission", "firmware_update", "lead_measurement",
        ],
    },
    World.RESEARCH.value: {
        "world": World.RESEARCH.value,
        "total_accepted": 0,
        "total_rejected": 0,
        "total_burned": 0,
        "total_bytes": 0,
        "total_mb": 0.0,
        "total_queries": 0,
        "active_records": 0,
        "patient_count": 0,
        "active_holds": 0,
        "burn_policy": "Burn after k-anonymization + differential privacy release",
        "allowed_event_types": ["episode_start", "episode_end"],
    },
    World.PATIENT.value: {
        "world": World.PATIENT.value,
        "total_accepted": 0,
        "total_rejected": 0,
        "total_burned": 0,
        "total_bytes": 0,
        "total_mb": 0.0,
        "total_queries": 0,
        "active_records": 0,
        "patient_count": 0,
        "active_holds": 0,
        "burn_policy": "Patient-controlled; data persists in portable record under patient consent",
        "allowed_event_types": [
            "heartbeat", "pacing", "episode_start", "episode_end",
            "alert", "transmission", "device_status", "activity",
            "adverse_event", "lead_measurement", "threshold_test",
        ],
    },
    World.SAFETY_INVESTIGATION.value: {
        "world": World.SAFETY_INVESTIGATION.value,
        "total_accepted": 0,
        "total_rejected": 0,
        "total_burned": 0,
        "total_bytes": 0,
        "total_mb": 0.0,
        "total_queries": 0,
        "active_records": 0,
        "patient_count": 0,
        "active_holds": 0,
        "burn_policy": "Hold until investigation closed + 12-month buffer, then burn",
        "allowed_event_types": [
            "adverse_event", "alert", "episode_start", "episode_end",
            "device_status", "lead_measurement", "heartbeat", "pacing",
            "transmission", "threshold_test",
        ],
    },
}

_relay_status: dict[str, Any] = {
    "status": "healthy",
    "ttl_seconds": 259_200,
    "current_load_bytes": 0,
    "current_load_mb": 0.0,
    "max_capacity_bytes": 104_857_600,  # 100 MB
    "messages_in_flight": 0,
    "messages_delivered": 0,
    "messages_expired": 0,
    "uptime_seconds": 0.0,
    "started_at": time.time(),
}

_burn_history: list[dict[str, Any]] = []
_holds: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class WorldStatusResponse(BaseModel):
    """Status of a single typed world."""

    world: str
    total_accepted: int
    total_rejected: int
    total_burned: int
    total_bytes: int
    total_mb: float
    total_queries: int
    active_records: int
    patient_count: int
    active_holds: int
    burn_policy: str
    allowed_event_types: list[str]


class WorldListResponse(BaseModel):
    """List of all worlds with their status."""

    worlds: list[WorldStatusResponse]
    total: int


class RelayStatusResponse(BaseModel):
    """Health and load status of the Chambers relay."""

    status: str
    ttl_seconds: int
    current_load_bytes: int
    current_load_mb: float
    max_capacity_bytes: int
    utilization_pct: float
    messages_in_flight: int
    messages_delivered: int
    messages_expired: int
    uptime_seconds: float


class BurnRecord(BaseModel):
    """A single burn event in the history log."""

    burn_id: str
    record_id: str
    world: str
    patient_id: str
    event_type: str
    timestamp_s: float
    burned_at: float
    size_bytes: int
    reason: str


class BurnHistoryResponse(BaseModel):
    """Paginated burn history."""

    burns: list[BurnRecord]
    total: int
    total_bytes_burned: int


class HoldCreateRequest(BaseModel):
    """Body for ``POST /holds``."""

    patient_id: str = Field(description="Patient whose data should be held.")
    device_serial: str = Field(default="", description="Device serial number.")
    trigger_type: str = Field(
        default="clinician_report",
        description=(
            "Trigger type: manufacturer_report | fda_request | "
            "clinician_report | auto_detect"
        ),
    )
    triggered_by: str = Field(
        default="clinician",
        description="Actor who initiated the hold.",
    )
    reason: str = Field(description="Free-text reason for the safety investigation hold.")


class HoldResponse(BaseModel):
    """Safety investigation hold detail."""

    hold_id: str
    patient_id: str
    device_serial: str
    trigger_type: str
    triggered_by: str
    reason: str
    investigation_status: str
    triggered_at: float
    records_held: int
    data_loss_assessment: dict[str, Any]


class HoldReleaseResponse(BaseModel):
    """Returned when a hold is released."""

    hold_id: str
    status: str
    records_released: int
    message: str


# ---------------------------------------------------------------------------
# Endpoints -- Worlds
# ---------------------------------------------------------------------------


@router.get("/worlds", response_model=WorldListResponse)
async def list_worlds() -> WorldListResponse:
    """List all typed worlds in the Chambers architecture with their current status.

    Each world enforces data-scope validation, access control, and its own
    burn policy.
    """
    worlds = [
        WorldStatusResponse(**ws) for ws in _world_statuses.values()
    ]
    return WorldListResponse(worlds=worlds, total=len(worlds))


@router.get("/worlds/{world_name}/status", response_model=WorldStatusResponse)
async def get_world_status(world_name: str) -> WorldStatusResponse:
    """Get detailed status of a specific typed world.

    Valid world names: clinical, device_maintenance, research, patient,
    safety_investigation.
    """
    ws = _world_statuses.get(world_name)
    if ws is None:
        valid = list(_world_statuses.keys())
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"World '{world_name}' not found. Valid: {valid}",
        )
    return WorldStatusResponse(**ws)


# ---------------------------------------------------------------------------
# Endpoints -- Relay
# ---------------------------------------------------------------------------


@router.get("/relay/status", response_model=RelayStatusResponse)
async def get_relay_status() -> RelayStatusResponse:
    """Get health and load status of the Chambers relay.

    The relay is the transient store with a configurable TTL (default 72h).
    Data that is not consumed or held within the TTL is expired (burned).
    """
    uptime = time.time() - _relay_status.get("started_at", time.time())
    cap = _relay_status["max_capacity_bytes"] or 1
    utilization = (_relay_status["current_load_bytes"] / cap) * 100.0
    return RelayStatusResponse(
        status=_relay_status["status"],
        ttl_seconds=_relay_status["ttl_seconds"],
        current_load_bytes=_relay_status["current_load_bytes"],
        current_load_mb=_relay_status["current_load_mb"],
        max_capacity_bytes=_relay_status["max_capacity_bytes"],
        utilization_pct=round(utilization, 2),
        messages_in_flight=_relay_status["messages_in_flight"],
        messages_delivered=_relay_status["messages_delivered"],
        messages_expired=_relay_status["messages_expired"],
        uptime_seconds=round(uptime, 2),
    )


# ---------------------------------------------------------------------------
# Endpoints -- Burns
# ---------------------------------------------------------------------------


@router.get("/burns", response_model=BurnHistoryResponse)
async def get_burn_history(
    world: str | None = Query(default=None, description="Filter by world name."),
    patient_id: str | None = Query(default=None, description="Filter by patient."),
    limit: int = Query(default=100, ge=1, le=5000, description="Max records returned."),
    offset: int = Query(default=0, ge=0, description="Skip first N records."),
) -> BurnHistoryResponse:
    """Get the burn history log for the Chambers architecture.

    Each entry represents a record that was permanently deleted per the
    burn-by-default policy.  Supports filtering by world and patient.
    """
    burns = list(_burn_history)

    if world is not None:
        burns = [b for b in burns if b["world"] == world]
    if patient_id is not None:
        burns = [b for b in burns if b["patient_id"] == patient_id]

    total = len(burns)
    total_bytes = sum(b.get("size_bytes", 0) for b in burns)
    burns = burns[offset: offset + limit]

    return BurnHistoryResponse(
        burns=[BurnRecord(**b) for b in burns],
        total=total,
        total_bytes_burned=total_bytes,
    )


# ---------------------------------------------------------------------------
# Endpoints -- Safety Holds
# ---------------------------------------------------------------------------


@router.post("/holds", response_model=HoldResponse, status_code=201)
async def create_hold(body: HoldCreateRequest) -> HoldResponse:
    """Create a safety investigation hold.

    Freezes burn schedules for the specified patient across ALL worlds.
    Data arriving after the hold is preserved; data already burned is
    irrecoverable (an accepted cost of burn-by-default semantics).
    """
    hold_id = str(uuid.uuid4())
    now = time.time()

    hold_record: dict[str, Any] = {
        "hold_id": hold_id,
        "patient_id": body.patient_id,
        "device_serial": body.device_serial,
        "trigger_type": body.trigger_type,
        "triggered_by": body.triggered_by,
        "reason": body.reason,
        "investigation_status": "active",
        "triggered_at": now,
        "records_held": 0,
        "data_loss_assessment": {
            "data_already_burned": True,
            "note": (
                "Data that was burned before this hold was triggered is "
                "irrecoverable. This is an accepted trade-off of the "
                "burn-by-default architecture."
            ),
            "relay_snapshot_captured": True,
        },
    }

    # Apply hold to all worlds (update counters)
    for ws in _world_statuses.values():
        ws["active_holds"] = ws.get("active_holds", 0) + 1

    _holds[hold_id] = hold_record

    return HoldResponse(**hold_record)


@router.get("/holds/{hold_id}", response_model=HoldResponse)
async def get_hold(hold_id: str) -> HoldResponse:
    """Retrieve details of a specific safety investigation hold."""
    hold = _holds.get(hold_id)
    if hold is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hold '{hold_id}' not found",
        )
    return HoldResponse(**hold)


@router.delete("/holds/{hold_id}", response_model=HoldReleaseResponse)
async def release_hold(hold_id: str) -> HoldReleaseResponse:
    """Release a safety investigation hold, allowing burn schedules to resume.

    Once released, held records become eligible for burning per their
    respective world's burn policy.
    """
    hold = _holds.get(hold_id)
    if hold is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hold '{hold_id}' not found",
        )

    if hold["investigation_status"] == "released":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Hold has already been released",
        )

    hold["investigation_status"] = "released"
    records_released = hold.get("records_held", 0)

    # Decrement hold counters in all worlds
    for ws in _world_statuses.values():
        ws["active_holds"] = max(0, ws.get("active_holds", 0) - 1)

    return HoldReleaseResponse(
        hold_id=hold_id,
        status="released",
        records_released=records_released,
        message=(
            f"Hold released. {records_released} records are now eligible for "
            f"burning per their respective world burn policies."
        ),
    )
