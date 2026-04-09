"""
Scenario management endpoints.

Lists predefined simulation scenarios and allows running them.  Each scenario
encodes a specific clinical situation or architecture-stress test together
with its expected configuration and adverse-event schedule.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/scenarios", tags=["scenarios"])

# ---------------------------------------------------------------------------
# Built-in scenario definitions
# ---------------------------------------------------------------------------


class ScenarioDefinition(BaseModel):
    """Full specification of a simulation scenario."""

    scenario_id: str
    name: str
    description: str
    category: str = Field(
        description="Category: baseline | adverse_event | compliance | stress | privacy"
    )
    duration_days: int
    cohort_size: int
    clock_speed: float
    config_overrides: dict[str, Any] = Field(default_factory=dict)
    scheduled_events: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Adverse events injected at deterministic times.",
    )
    expected_outcomes: dict[str, Any] = Field(
        default_factory=dict,
        description="Expected metric ranges for validation.",
    )
    tags: list[str] = Field(default_factory=list)


_BUILT_IN_SCENARIOS: dict[str, ScenarioDefinition] = {
    "baseline-steady-state": ScenarioDefinition(
        scenario_id="baseline-steady-state",
        name="Baseline Steady State",
        description=(
            "One year of normal operation for a 10-patient cohort.  No adverse "
            "events.  Establishes baseline persistence volume and attack-surface "
            "metrics for both architectures."
        ),
        category="baseline",
        duration_days=365,
        cohort_size=10,
        clock_speed=100.0,
        config_overrides={},
        scheduled_events=[],
        expected_outcomes={
            "persistence_reduction_pct_min": 85.0,
            "attack_surface_reduction_pct_min": 70.0,
            "clinical_availability_pct_min": 95.0,
        },
        tags=["baseline", "privacy", "comparison"],
    ),
    "lead-fracture-investigation": ScenarioDefinition(
        scenario_id="lead-fracture-investigation",
        name="Lead Fracture Safety Investigation",
        description=(
            "A lead fracture occurs at day 180.  Under the current architecture "
            "all historical data is available for the investigation.  Under "
            "Chambers, a safety hold is triggered -- data after the hold is "
            "preserved but data burned before the hold is irrecoverable."
        ),
        category="adverse_event",
        duration_days=365,
        cohort_size=5,
        clock_speed=200.0,
        config_overrides={"chambers__relay_ttl_seconds": 259200},
        scheduled_events=[
            {
                "day": 180,
                "event_type": "lead_fracture",
                "severity": "major",
                "patient_index": 0,
                "description": "Lead fracture triggers safety investigation",
            },
        ],
        expected_outcomes={
            "safety_hold_triggered": True,
            "data_preserved_pct_chambers_min": 50.0,
            "investigation_duration_days": 90,
        },
        tags=["adverse_event", "safety", "investigation", "lead"],
    ),
    "inappropriate-shock-cluster": ScenarioDefinition(
        scenario_id="inappropriate-shock-cluster",
        name="Inappropriate Shock Cluster",
        description=(
            "Three patients experience inappropriate shocks over a 30-day "
            "window, triggering a manufacturer-level investigation.  Tests "
            "the safety hold mechanism across multiple patients simultaneously."
        ),
        category="adverse_event",
        duration_days=180,
        cohort_size=10,
        clock_speed=150.0,
        config_overrides={},
        scheduled_events=[
            {
                "day": 60,
                "event_type": "inappropriate_shock",
                "severity": "major",
                "patient_index": 0,
                "description": "First inappropriate shock",
            },
            {
                "day": 75,
                "event_type": "inappropriate_shock",
                "severity": "major",
                "patient_index": 3,
                "description": "Second inappropriate shock -- different patient",
            },
            {
                "day": 85,
                "event_type": "inappropriate_shock",
                "severity": "major",
                "patient_index": 7,
                "description": "Third shock triggers manufacturer-level hold",
            },
        ],
        expected_outcomes={
            "safety_holds_triggered": 3,
            "manufacturer_investigation": True,
        },
        tags=["adverse_event", "safety", "shock", "cluster"],
    ),
    "gdpr-right-to-erasure": ScenarioDefinition(
        scenario_id="gdpr-right-to-erasure",
        name="GDPR Right-to-Erasure Request",
        description=(
            "A patient exercises their GDPR Art. 17 right to erasure after "
            "6 months.  Under the current architecture, erasure must propagate "
            "across 5 storage layers.  Under Chambers, most data has already "
            "been burned -- only the portable record requires action."
        ),
        category="compliance",
        duration_days=180,
        cohort_size=3,
        clock_speed=200.0,
        config_overrides={},
        scheduled_events=[
            {
                "day": 180,
                "event_type": "gdpr_erasure_request",
                "severity": "minor",
                "patient_index": 0,
                "description": "Patient requests full data erasure",
            },
        ],
        expected_outcomes={
            "current_arch_layers_to_erase": 5,
            "chambers_layers_to_erase": 1,
            "compliance_time_current_hours": 72,
            "compliance_time_chambers_hours": 1,
        },
        tags=["compliance", "gdpr", "erasure", "privacy"],
    ),
    "high-volume-stress": ScenarioDefinition(
        scenario_id="high-volume-stress",
        name="High-Volume Stress Test",
        description=(
            "500-patient cohort running for 90 days at 500x speed.  Tests "
            "system throughput, relay backpressure, and burn-schedule "
            "performance under load."
        ),
        category="stress",
        duration_days=90,
        cohort_size=500,
        clock_speed=500.0,
        config_overrides={"chambers__relay_ttl_seconds": 86400},
        scheduled_events=[],
        expected_outcomes={
            "events_per_second_min": 1000,
            "relay_backpressure_events": 0,
        },
        tags=["stress", "performance", "scale"],
    ),
    "privacy-breach-simulation": ScenarioDefinition(
        scenario_id="privacy-breach-simulation",
        name="Privacy Breach Simulation",
        description=(
            "Simulates a cloud-layer breach at day 120.  Under the current "
            "architecture the attacker gains access to the full patient history. "
            "Under Chambers the relay holds at most 72 hours of data, and the "
            "cloud layer does not exist."
        ),
        category="privacy",
        duration_days=180,
        cohort_size=10,
        clock_speed=200.0,
        config_overrides={},
        scheduled_events=[
            {
                "day": 120,
                "event_type": "simulated_breach",
                "severity": "life_threatening",
                "patient_index": -1,
                "description": "Attacker compromises cloud storage layer",
            },
        ],
        expected_outcomes={
            "current_records_exposed": 100_000,
            "chambers_records_exposed": 500,
            "current_patient_days_exposed": 1200,
            "chambers_patient_days_exposed": 3,
        },
        tags=["privacy", "breach", "security"],
    ),
}


# In-memory run registry
_scenario_runs: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ScenarioSummary(BaseModel):
    """Lightweight scenario listing item."""

    scenario_id: str
    name: str
    category: str
    description: str
    duration_days: int
    cohort_size: int
    tags: list[str]


class ScenarioListResponse(BaseModel):
    """List of all available scenarios."""

    scenarios: list[ScenarioSummary]
    total: int


class ScenarioRunResponse(BaseModel):
    """Returned when a scenario is launched."""

    run_id: str
    scenario_id: str
    sim_id: str
    status: str
    message: str
    started_at: float


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/", response_model=ScenarioListResponse)
async def list_scenarios(
    category: str | None = None,
    tag: str | None = None,
) -> ScenarioListResponse:
    """List all available simulation scenarios.

    Optionally filter by category or tag.
    """
    scenarios = list(_BUILT_IN_SCENARIOS.values())

    if category is not None:
        scenarios = [s for s in scenarios if s.category == category]
    if tag is not None:
        scenarios = [s for s in scenarios if tag in s.tags]

    summaries = [
        ScenarioSummary(
            scenario_id=s.scenario_id,
            name=s.name,
            category=s.category,
            description=s.description,
            duration_days=s.duration_days,
            cohort_size=s.cohort_size,
            tags=s.tags,
        )
        for s in scenarios
    ]

    return ScenarioListResponse(scenarios=summaries, total=len(summaries))


@router.get("/{scenario_id}", response_model=ScenarioDefinition)
async def get_scenario(scenario_id: str) -> ScenarioDefinition:
    """Retrieve the full definition of a scenario by ID.

    Includes configuration overrides, scheduled adverse events, and expected
    outcome ranges.
    """
    scenario = _BUILT_IN_SCENARIOS.get(scenario_id)
    if scenario is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scenario '{scenario_id}' not found",
        )
    return scenario


@router.post("/{scenario_id}/run", response_model=ScenarioRunResponse, status_code=201)
async def run_scenario(scenario_id: str) -> ScenarioRunResponse:
    """Run a predefined scenario.

    Creates a new simulation using the scenario's configuration and starts
    event generation.  Returns a ``run_id`` that can be used to track the
    run, plus the underlying ``sim_id`` for simulation-control endpoints.
    """
    scenario = _BUILT_IN_SCENARIOS.get(scenario_id)
    if scenario is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scenario '{scenario_id}' not found",
        )

    # Create simulation via the simulation module (avoid circular: use registry directly)
    from src.api.routes.simulation import (
        SimulationInstance,
        _simulations,
    )

    sim_id = str(uuid.uuid4())
    sim = SimulationInstance(
        sim_id=sim_id,
        scenario_id=scenario_id,
        config_overrides=scenario.config_overrides,
        cohort_size=scenario.cohort_size,
        clock_speed=scenario.clock_speed,
    )
    _simulations[sim_id] = sim

    run_id = str(uuid.uuid4())
    run_record: dict[str, Any] = {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "sim_id": sim_id,
        "status": "running",
        "started_at": time.time(),
        "scheduled_events": scenario.scheduled_events,
        "expected_outcomes": scenario.expected_outcomes,
    }
    _scenario_runs[run_id] = run_record

    return ScenarioRunResponse(
        run_id=run_id,
        scenario_id=scenario_id,
        sim_id=sim_id,
        status="running",
        message=f"Scenario '{scenario.name}' started with {scenario.cohort_size} patients",
        started_at=run_record["started_at"],
    )
