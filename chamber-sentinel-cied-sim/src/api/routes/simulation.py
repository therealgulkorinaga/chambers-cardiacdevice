"""
Simulation control endpoints.

Manages the lifecycle of telemetry simulations: start, stop, pause, resume,
status queries, adverse-event injection, and clock-speed adjustment.  Each
simulation is tracked in an in-memory registry keyed by ``sim_id``.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.generator.cohort import CohortDistribution, CohortManager, SimulationClock
from src.generator.stream import EventStream

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/simulation", tags=["simulation"])

# ---------------------------------------------------------------------------
# In-memory simulation registry
# ---------------------------------------------------------------------------


class SimStatus(str, Enum):
    """Possible states of a simulation run."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"
    ERROR = "error"


class SimulationInstance:
    """Runtime bookkeeping for one simulation run."""

    def __init__(
        self,
        sim_id: str,
        scenario_id: str | None,
        config_overrides: dict[str, Any],
        cohort_size: int,
        clock_speed: float,
    ) -> None:
        self.sim_id = sim_id
        self.scenario_id = scenario_id
        self.config_overrides = config_overrides
        self.status = SimStatus.RUNNING
        self.created_at = time.time()
        self.started_at = time.time()
        self.stopped_at: float | None = None

        self.clock = SimulationClock(speed_multiplier=clock_speed)
        self.event_stream = EventStream()
        self.cohort_manager = CohortManager(
            distribution=CohortDistribution(size=cohort_size),
            base_seed=config_overrides.get("random_seed", 42),
        )
        self.cohort_manager.generate_cohort()

        self.events_generated: int = 0
        self.adverse_events_injected: int = 0
        self.error_message: str | None = None


# Global registry
_simulations: dict[str, SimulationInstance] = {}


def _get_sim(sim_id: str) -> SimulationInstance:
    """Retrieve a simulation or raise 404."""
    sim = _simulations.get(sim_id)
    if sim is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation '{sim_id}' not found",
        )
    return sim


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class StartSimulationRequest(BaseModel):
    """Body for ``POST /start``."""

    scenario_id: str | None = Field(
        default=None,
        description="Optional scenario identifier to load preset configurations.",
    )
    cohort_size: int = Field(
        default=10,
        ge=1,
        le=10_000,
        description="Number of virtual patients in the cohort.",
    )
    clock_speed: float = Field(
        default=1.0,
        gt=0.0,
        description="Simulation clock multiplier (1.0 = real-time).",
    )
    config_overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary config overrides merged into the default Settings.",
    )


class StartSimulationResponse(BaseModel):
    """Returned by ``POST /start``."""

    sim_id: str
    status: str
    scenario_id: str | None
    cohort_size: int
    clock_speed: float
    message: str


class SimulationStatusResponse(BaseModel):
    """Returned by ``GET /{sim_id}/status``."""

    sim_id: str
    status: str
    scenario_id: str | None
    clock: dict[str, Any]
    events_generated: int
    adverse_events_injected: int
    cohort_size: int
    uptime_seconds: float
    error_message: str | None = None


class InjectEventRequest(BaseModel):
    """Body for ``POST /{sim_id}/inject-event``."""

    event_type: str = Field(
        description="Adverse event type, e.g. 'lead_fracture', 'inappropriate_shock'.",
    )
    severity: str = Field(
        default="major",
        description="Severity: minor | major | life_threatening | fatal.",
    )
    patient_id: str | None = Field(
        default=None,
        description="Target patient; if omitted a random patient is chosen.",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra data attached to the injected event.",
    )


class InjectEventResponse(BaseModel):
    """Returned by ``POST /{sim_id}/inject-event``."""

    event_id: str
    event_type: str
    severity: str
    patient_id: str
    timestamp_s: float
    message: str


class SetClockSpeedRequest(BaseModel):
    """Body for ``POST /{sim_id}/set-clock-speed``."""

    clock_speed: float = Field(gt=0.0, description="New simulation clock multiplier.")


class GenericSimResponse(BaseModel):
    """Lightweight acknowledgement response."""

    sim_id: str
    status: str
    message: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/start", response_model=StartSimulationResponse, status_code=201)
async def start_simulation(body: StartSimulationRequest) -> StartSimulationResponse:
    """Start a new simulation run.

    Creates a patient cohort, initialises the simulation clock, and begins
    event generation.  Returns the ``sim_id`` used to control the run.
    """
    sim_id = str(uuid.uuid4())
    sim = SimulationInstance(
        sim_id=sim_id,
        scenario_id=body.scenario_id,
        config_overrides=body.config_overrides,
        cohort_size=body.cohort_size,
        clock_speed=body.clock_speed,
    )
    _simulations[sim_id] = sim

    return StartSimulationResponse(
        sim_id=sim_id,
        status=sim.status.value,
        scenario_id=body.scenario_id,
        cohort_size=body.cohort_size,
        clock_speed=body.clock_speed,
        message="Simulation started successfully",
    )


@router.post("/{sim_id}/stop", response_model=GenericSimResponse)
async def stop_simulation(sim_id: str) -> GenericSimResponse:
    """Stop a running or paused simulation.

    Marks the run as stopped and closes the event stream.  The simulation
    cannot be resumed after stopping.
    """
    sim = _get_sim(sim_id)
    if sim.status == SimStatus.STOPPED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Simulation is already stopped",
        )
    sim.status = SimStatus.STOPPED
    sim.stopped_at = time.time()
    sim.event_stream.close()
    return GenericSimResponse(
        sim_id=sim_id,
        status=sim.status.value,
        message="Simulation stopped",
    )


@router.post("/{sim_id}/pause", response_model=GenericSimResponse)
async def pause_simulation(sim_id: str) -> GenericSimResponse:
    """Pause a running simulation.

    The simulation clock freezes; no new events are generated until resumed.
    """
    sim = _get_sim(sim_id)
    if sim.status != SimStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot pause simulation in state '{sim.status.value}'",
        )
    sim.status = SimStatus.PAUSED
    sim.clock.pause()
    return GenericSimResponse(
        sim_id=sim_id,
        status=sim.status.value,
        message="Simulation paused",
    )


@router.post("/{sim_id}/resume", response_model=GenericSimResponse)
async def resume_simulation(sim_id: str) -> GenericSimResponse:
    """Resume a paused simulation."""
    sim = _get_sim(sim_id)
    if sim.status != SimStatus.PAUSED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot resume simulation in state '{sim.status.value}'",
        )
    sim.status = SimStatus.RUNNING
    sim.clock.resume()
    return GenericSimResponse(
        sim_id=sim_id,
        status=sim.status.value,
        message="Simulation resumed",
    )


@router.get("/{sim_id}/status", response_model=SimulationStatusResponse)
async def get_simulation_status(sim_id: str) -> SimulationStatusResponse:
    """Return detailed status of a simulation including clock state and event counts."""
    sim = _get_sim(sim_id)
    uptime = (sim.stopped_at or time.time()) - sim.started_at
    return SimulationStatusResponse(
        sim_id=sim.sim_id,
        status=sim.status.value,
        scenario_id=sim.scenario_id,
        clock=sim.clock.stats,
        events_generated=sim.events_generated,
        adverse_events_injected=sim.adverse_events_injected,
        cohort_size=len(sim.cohort_manager.patients),
        uptime_seconds=round(uptime, 2),
        error_message=sim.error_message,
    )


@router.post("/{sim_id}/inject-event", response_model=InjectEventResponse, status_code=201)
async def inject_adverse_event(sim_id: str, body: InjectEventRequest) -> InjectEventResponse:
    """Manually inject an adverse event into a running simulation.

    Useful for deterministic scenario testing -- forces a specific event type
    and severity onto a chosen (or random) patient at the current sim clock.
    """
    sim = _get_sim(sim_id)
    if sim.status not in (SimStatus.RUNNING, SimStatus.PAUSED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot inject events into simulation in state '{sim.status.value}'",
        )

    # Resolve target patient
    patient_id = body.patient_id
    if patient_id is None:
        if not sim.cohort_manager.patients:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cohort is empty; cannot pick a random patient",
            )
        patient_id = sim.cohort_manager.patients[0].patient_id
    else:
        found = sim.cohort_manager.get_patient(patient_id)
        if found is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Patient '{patient_id}' not found in cohort",
            )

    event_id = uuid.uuid4().hex[:12]
    timestamp_s = sim.clock.time_s
    sim.adverse_events_injected += 1
    sim.events_generated += 1

    return InjectEventResponse(
        event_id=event_id,
        event_type=body.event_type,
        severity=body.severity,
        patient_id=patient_id,
        timestamp_s=timestamp_s,
        message=f"Adverse event '{body.event_type}' injected for patient {patient_id}",
    )


@router.post("/{sim_id}/set-clock-speed", response_model=GenericSimResponse)
async def set_clock_speed(sim_id: str, body: SetClockSpeedRequest) -> GenericSimResponse:
    """Change the simulation clock speed on the fly.

    A speed of 10.0 means simulated time advances 10x faster than wall-clock.
    """
    sim = _get_sim(sim_id)
    if sim.status == SimStatus.STOPPED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot change clock speed of a stopped simulation",
        )
    old_speed = sim.clock.speed
    sim.clock.speed = body.clock_speed
    return GenericSimResponse(
        sim_id=sim_id,
        status=sim.status.value,
        message=f"Clock speed changed from {old_speed:.2f}x to {body.clock_speed:.2f}x",
    )
