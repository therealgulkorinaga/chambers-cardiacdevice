"""Simulation Orchestrator — ties all modules together and runs the simulation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from src.generator.stream import (
    EventStream, TelemetryEvent, TransmissionAssembler, DualArchitectureRouter,
)
from src.generator.cohort import (
    CohortManager, CohortDistribution, PatientInstance, SimulationClock,
    PatientSimulator, PROFILE_DEVICE_MAP,
)
from src.current_arch.layers.on_device import OnDeviceStorage
from src.current_arch.layers.transmitter import Transmitter
from src.current_arch.layers.cloud import ManufacturerCloud
from src.current_arch.layers.clinician_portal import ClinicianPortal
from src.current_arch.layers.aggregate_pool import AggregatePool
from src.current_arch.persistence.store import CurrentArchPersistence
from src.chambers_arch.relay.processor import RelayProcessor
from src.chambers_arch.worlds.clinical_world import ClinicalWorld
from src.chambers_arch.worlds.device_maintenance_world import DeviceMaintenanceWorld
from src.chambers_arch.worlds.research_world import ResearchWorld
from src.chambers_arch.worlds.patient_world import PatientWorld
from src.chambers_arch.worlds.safety_investigation_world import SafetyInvestigationWorld
from src.chambers_arch.burn.scheduler import BurnScheduler
from src.chambers_arch.burn.verifier import BurnVerifier
from src.chambers_arch.burn.hold_manager import HoldManager


@dataclass
class SimulationConfig:
    """Configuration for a simulation run."""
    simulation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    duration_days: int = 365
    clock_speed: float = 3600.0  # 1 hour of sim per 1 second real
    time_step_s: float = 3600.0  # Advance 1 hour per tick
    random_seed: int = 42

    # Cohort
    cohort_size: int = 1
    cohort_distribution: CohortDistribution | None = None
    patient_profiles: list[str] | None = None  # Specific profiles to use

    # Chambers settings
    relay_ttl_s: int = 259200  # 72 hours
    device_maint_window_days: int = 90
    clinical_max_hold_days: int = 30
    research_k_anonymity: int = 10
    safety_buffer_months: int = 12

    # Current arch settings
    cloud_retention_days: int | None = None  # None = indefinite

    # EGM mode
    egm_mode: str = "parametric"  # "parametric" (Mode A) or "opencarp" (Mode B)
    opencarp_template_dir: str = "src/generator/cardiac/opencarp/templates"

    # Analytics
    snapshot_interval_s: float = 86400.0  # Daily snapshots


class CurrentArchHandler:
    """Handles ingestion into the current architecture (5 layers)."""

    def __init__(self, cloud: ManufacturerCloud, persistence: CurrentArchPersistence) -> None:
        self.cloud = cloud
        self.persistence = persistence

    def ingest(self, event: TelemetryEvent) -> None:
        record = self.cloud.ingest_event(event, event.timestamp_s)
        self.persistence.record_cloud(
            event.patient_id, event.event_type.value, event.size_bytes
        )

    def ingest_transmission(self, packet: Any) -> None:
        self.cloud.ingest_transmission(
            {
                "patient_id": packet.patient_id,
                "device_serial": packet.device_serial,
                "transmission_type": packet.transmission_type,
                "events": [],
                "alert_flags": packet.alert_flags,
                "size_bytes": packet.payload_size_bytes,
            },
            packet.timestamp_s,
        )
        self.persistence.record_cloud(
            packet.patient_id, "transmission", packet.payload_size_bytes
        )


class ChambersArchHandler:
    """Handles ingestion into the Chambers architecture (relay + worlds)."""

    def __init__(self, relay: RelayProcessor) -> None:
        self.relay = relay

    def ingest(self, event: TelemetryEvent) -> None:
        self.relay.ingest(event)

    def ingest_transmission(self, packet: Any) -> None:
        self.relay.ingest_transmission(packet)


class SimulationOrchestrator:
    """Main orchestrator that runs the complete simulation.

    Creates all components, wires them together, and runs
    the simulation loop advancing the clock and processing events.
    """

    def __init__(self, config: SimulationConfig | None = None) -> None:
        self.config = config or SimulationConfig()
        self.rng = np.random.default_rng(self.config.random_seed)

        # Clock
        self.clock = SimulationClock(speed_multiplier=self.config.clock_speed)

        # Event stream
        self.stream = EventStream()

        # --- Current Architecture ---
        self.cloud = ManufacturerCloud(retention_days=self.config.cloud_retention_days)
        self.current_persistence = CurrentArchPersistence()
        self.clinician_portal = ClinicianPortal(
            cloud=self.cloud, rng=self.rng,
        )
        self.aggregate_pool = AggregatePool(rng=self.rng)
        self.current_handler = CurrentArchHandler(self.cloud, self.current_persistence)

        # --- Chambers Architecture ---
        self.clinical_world = ClinicalWorld(
            max_hold_window_s=self.config.clinical_max_hold_days * 86400,
        )
        self.device_maint_world = DeviceMaintenanceWorld(
            retention_window_days=self.config.device_maint_window_days,
        )
        self.research_world = ResearchWorld(
            k_anonymity=self.config.research_k_anonymity, rng=self.rng,
        )
        self.patient_worlds: dict[str, PatientWorld] = {}  # patient_id -> PatientWorld
        self.safety_world = SafetyInvestigationWorld(
            buffer_months=self.config.safety_buffer_months,
        )

        self.worlds = {
            "clinical": self.clinical_world,
            "device_maintenance": self.device_maint_world,
            "research": self.research_world,
            "safety_investigation": self.safety_world,
        }

        self.relay = RelayProcessor(
            ttl_seconds=self.config.relay_ttl_s,
            worlds=self.worlds,
        )

        self.burn_scheduler = BurnScheduler(worlds=self.worlds, relay=self.relay)
        self.burn_verifier = BurnVerifier()
        self.hold_manager = HoldManager(
            worlds=self.worlds, relay=self.relay,
            burn_scheduler=self.burn_scheduler,
            buffer_months=self.config.safety_buffer_months,
        )

        self.chambers_handler = ChambersArchHandler(self.relay)

        # Dual architecture router
        self.router = DualArchitectureRouter(
            current_arch_handler=self.current_handler,
            chambers_arch_handler=self.chambers_handler,
        )

        # --- Patient Management ---
        self.cohort_manager = CohortManager(
            distribution=self.config.cohort_distribution or CohortDistribution(
                size=self.config.cohort_size,
            ),
            base_seed=self.config.random_seed,
        )
        self.patient_simulators: dict[str, PatientSimulator] = {}

        # --- State ---
        self._status = "initialized"  # initialized, running, paused, stopped, completed
        self._total_events = 0
        self._total_burns = 0
        self._last_snapshot_s = 0.0
        self._snapshots: list[dict[str, Any]] = []

    def initialize(self) -> None:
        """Initialize the simulation: create patients, wire up components."""
        patients = self.cohort_manager.generate_cohort()

        for patient in patients:
            self._initialize_patient(patient)

        self._status = "initialized"

    def _initialize_patient(self, patient: PatientInstance) -> None:
        """Set up all engines for a single patient."""
        patient_rng = np.random.default_rng(patient.seed)
        profile_data = PROFILE_DEVICE_MAP.get(patient.profile_id, {})

        # Create patient-specific world
        patient_world = PatientWorld(patient_id=patient.patient_id)
        self.patient_worlds[patient.patient_id] = patient_world
        # Add to relay's world map
        self.relay.worlds[f"patient_{patient.patient_id}"] = patient_world

        # Set up generators (lazy — check if modules are importable)
        try:
            from src.generator.cardiac.rhythm_engine import RhythmEngine, RhythmState
            patient.rhythm_engine = RhythmEngine(
                initial_state=RhythmState.NSR,
                rng=patient_rng,
            )
        except (ImportError, Exception):
            pass

        try:
            from src.generator.device.battery_model import BatteryModel
            patient.battery_model = BatteryModel(rng=patient_rng)
        except (ImportError, Exception):
            pass

        try:
            from src.generator.device.lead_model import LeadModel, LeadConfig
            positions = ["RV"]
            if patient.device_type in ("DDD", "CRT_D", "CRT_P"):
                positions.append("RA")
            if patient.device_type in ("CRT_D", "CRT_P"):
                positions.append("LV")

            for pos in positions:
                lead_config = LeadConfig(lead_id=f"{pos}-{patient.device_serial}", position=pos)
                patient.lead_models[pos] = LeadModel(config=lead_config, rng=patient_rng)
        except (ImportError, Exception):
            pass

        try:
            from src.generator.episodes.arrhythmia_generator import ArrhythmiaGenerator
            patient.arrhythmia_generator = ArrhythmiaGenerator(
                af_burden=profile_data.get("af_burden", 0.0),
                vt_risk=profile_data.get("vt_risk", 0.0),
                rng=patient_rng,
            )
        except (ImportError, Exception):
            pass

        try:
            from src.generator.episodes.alert_generator import AlertGenerator
            patient.alert_generator = AlertGenerator()
        except (ImportError, Exception):
            pass

        try:
            from src.generator.cardiac.egm_synthesizer import EGMSynthesizer
            template_lib = None
            if self.config.egm_mode == "opencarp":
                try:
                    from src.generator.cardiac.opencarp.template_library import TemplateLibrary
                    template_lib = TemplateLibrary(
                        template_dir=self.config.opencarp_template_dir,
                    )
                except ImportError:
                    pass
            patient.egm_synthesizer = EGMSynthesizer(
                sample_rate_hz=256,
                mode=self.config.egm_mode,
                template_library=template_lib,
                rng=patient_rng,
            )
        except (ImportError, Exception):
            pass

        try:
            from src.generator.patient.circadian_model import CircadianModel
            from src.generator.patient.activity_engine import ActivityEngine
            circadian = CircadianModel(base_hr=72, rng=patient_rng)
            patient.activity_engine = ActivityEngine(
                profile_params={}, circadian_model=circadian, rng=patient_rng,
            )
        except (ImportError, Exception):
            pass

        patient.transmission_assembler = TransmissionAssembler(
            patient_id=patient.patient_id,
            device_serial=patient.device_serial,
        )

        # Assign to clinician
        self.clinician_portal.assign_patient(patient.patient_id)

        # Register in aggregate pool
        self.aggregate_pool.register_patient(
            patient.patient_id, patient.age, patient.sex,
            patient.device_type, patient.region,
            2026 - (patient.implant_age_days // 365),
        )

        # Create simulator
        simulator = PatientSimulator(patient, self.stream)
        self.patient_simulators[patient.patient_id] = simulator

    def run(self, callback: Any = None) -> dict[str, Any]:
        """Run the simulation to completion.

        Args:
            callback: Optional function called each tick with (clock, stats)

        Returns:
            Final simulation statistics.
        """
        self._status = "running"
        duration_s = self.config.duration_days * 86400.0
        dt = self.config.time_step_s

        while self.clock.time_s < duration_s and self._status == "running":
            self._tick(dt)

            if callback:
                callback(self.clock, self.get_stats())

        if self._status == "running":
            self._status = "completed"

        return self.get_stats()

    def _tick(self, dt_seconds: float) -> None:
        """Advance the simulation by one time step."""
        self.clock.advance_sim(dt_seconds)

        # 1. Generate events for each patient
        for simulator in self.patient_simulators.values():
            events = simulator.step(dt_seconds, self.clock)
            self._total_events += len(events)

        # 2. Route events to both architectures
        while not self.stream.is_empty:
            event = self.stream.pop()
            if event:
                self.router.route(event)

        # 3. Process clinician reviews (current arch)
        self.clinician_portal.simulate_review_cycle(self.clock.time_s)

        # 4. Process burns (Chambers arch)
        burn_events = self.burn_scheduler.tick(self.clock.time_s)
        self._total_burns += len(burn_events)

        # 5. Process hold lifecycle
        self.hold_manager.tick(self.clock.time_s)

        # 6. Take periodic snapshots
        if self.clock.time_s - self._last_snapshot_s >= self.config.snapshot_interval_s:
            self._take_snapshot()
            self._last_snapshot_s = self.clock.time_s

    def _take_snapshot(self) -> None:
        """Capture metrics snapshot for analytics."""
        snapshot = {
            "timestamp_s": self.clock.time_s,
            "day": self.clock.day_number,
            "current_arch": {
                "total_bytes": self.cloud.total_data_volume_bytes,
                "total_records": self.cloud.total_records,
            },
            "chambers_arch": {
                "relay_bytes": self.relay.total_bytes_in_relay,
                "relay_items": self.relay.items_in_relay,
                "world_bytes": sum(
                    w.get_status().get("total_bytes", 0)
                    for w in self.worlds.values()
                ),
                "total_burns": self._total_burns,
            },
            "events_generated": self._total_events,
        }

        # Add patient world bytes
        patient_world_bytes = sum(
            pw.get_status().get("total_bytes", 0)
            for pw in self.patient_worlds.values()
        )
        snapshot["chambers_arch"]["patient_world_bytes"] = patient_world_bytes
        snapshot["chambers_arch"]["total_bytes"] = (
            snapshot["chambers_arch"]["relay_bytes"]
            + snapshot["chambers_arch"]["world_bytes"]
            + patient_world_bytes
        )

        self._snapshots.append(snapshot)

    def pause(self) -> None:
        self._status = "paused"
        self.clock.pause()

    def resume(self) -> None:
        self._status = "running"
        self.clock.resume()

    def stop(self) -> None:
        self._status = "stopped"

    def inject_adverse_event(self, patient_id: str, event_type: str,
                              severity: str = "major") -> dict[str, Any]:
        """Manually inject an adverse event for a patient."""
        from src.generator.stream import EventType as ET
        event = TelemetryEvent(
            timestamp_s=self.clock.time_s,
            patient_id=patient_id,
            event_type=ET.ADVERSE_EVENT,
            payload={
                "event_type": event_type,
                "severity": severity,
                "injected": True,
            },
            size_bytes=512,
        )
        self.stream.push(event)
        return {"injected": True, "event_type": event_type, "patient_id": patient_id}

    def create_safety_hold(self, patient_id: str, reason: str,
                            triggered_by: str = "manual") -> dict[str, Any]:
        """Create a safety investigation hold."""
        device_serial = ""
        for p in self.cohort_manager.patients:
            if p.patient_id == patient_id:
                device_serial = p.device_serial
                break

        hold = self.hold_manager.create_hold(
            patient_id=patient_id,
            device_serial=device_serial,
            trigger_type="manual",
            triggered_by=triggered_by,
            reason=reason,
            timestamp_s=self.clock.time_s,
        )
        return {
            "hold_id": hold.hold_id,
            "patient_id": patient_id,
            "records_held": hold.total_records_held,
        }

    def get_stats(self) -> dict[str, Any]:
        """Get current simulation statistics."""
        return {
            "simulation_id": self.config.simulation_id,
            "status": self._status,
            "clock": self.clock.stats,
            "patients": len(self.patient_simulators),
            "total_events": self._total_events,
            "total_burns": self._total_burns,
            "current_arch": self.cloud.stats,
            "chambers_arch": {
                "relay": self.relay.stats,
                "burn_scheduler": self.burn_scheduler.stats,
                "hold_manager": self.hold_manager.stats,
                "worlds": {
                    name: world.get_status()
                    for name, world in self.worlds.items()
                },
            },
            "snapshots": len(self._snapshots),
        }

    def get_comparison_snapshot(self) -> dict[str, Any]:
        """Get a comparison snapshot of both architectures at current time."""
        current_bytes = self.cloud.total_data_volume_bytes
        chambers_bytes = (
            self.relay.total_bytes_in_relay
            + sum(w.get_status().get("total_bytes", 0) for w in self.worlds.values())
            + sum(pw.get_status().get("total_bytes", 0) for pw in self.patient_worlds.values())
        )

        return {
            "timestamp_s": self.clock.time_s,
            "day": self.clock.day_number,
            "persistence": {
                "current_bytes": current_bytes,
                "chambers_bytes": chambers_bytes,
                "ratio": current_bytes / chambers_bytes if chambers_bytes > 0 else float("inf"),
                "current_mb": current_bytes / (1024 * 1024),
                "chambers_mb": chambers_bytes / (1024 * 1024),
            },
            "burns": {
                "total": self._total_burns,
                "by_world": self.burn_scheduler.stats.get("burns_by_world", {}),
            },
            "clinical_availability": {
                "pending_alerts_current": self.clinician_portal.pending_alert_count,
                "pending_alerts_chambers": len(self.clinical_world.get_unacknowledged_alerts()),
            },
        }

    @property
    def time_series(self) -> list[dict[str, Any]]:
        """Get the full time series of snapshots."""
        return list(self._snapshots)
