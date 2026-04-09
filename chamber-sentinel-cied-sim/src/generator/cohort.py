"""Multi-patient cohort management for population-scale simulation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from src.generator.stream import EventStream, TelemetryEvent, EventType


@dataclass
class PatientInstance:
    """A single virtual patient with their device and clinical state."""

    patient_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    profile_id: str = ""  # Reference to patient profile (P-001 through P-010)
    device_serial: str = field(default_factory=lambda: f"SIM-{uuid.uuid4().hex[:8].upper()}")
    device_type: str = "DDD"
    age: int = 72
    sex: str = "M"
    diagnosis: str = ""
    comorbidities: list[str] = field(default_factory=list)
    implant_age_days: int = 0
    region: str = "US-NE"
    seed: int = 0

    # Runtime state references (set during simulation init)
    rhythm_engine: Any = field(default=None, repr=False)
    pacing_engine: Any = field(default=None, repr=False)
    battery_model: Any = field(default=None, repr=False)
    lead_models: dict[str, Any] = field(default_factory=dict, repr=False)
    activity_engine: Any = field(default=None, repr=False)
    arrhythmia_generator: Any = field(default=None, repr=False)
    alert_generator: Any = field(default=None, repr=False)
    transmission_assembler: Any = field(default=None, repr=False)
    egm_synthesizer: Any = field(default=None, repr=False)


@dataclass
class CohortDistribution:
    """Parameters for generating a patient cohort with realistic demographics."""

    size: int = 100
    age_mean: float = 72.0
    age_std: float = 12.0
    age_min: int = 18
    age_max: int = 100
    male_fraction: float = 0.55
    device_type_weights: dict[str, float] = field(default_factory=lambda: {
        "DDD": 0.55, "VVI": 0.15, "CRT_D": 0.20, "CRT_P": 0.05, "ICD": 0.05
    })
    comorbidity_prevalence: dict[str, float] = field(default_factory=lambda: {
        "heart_failure": 0.40,
        "atrial_fibrillation": 0.35,
        "diabetes": 0.25,
        "ckd": 0.15,
        "hypertension": 0.60,
    })
    implant_age_max_years: int = 10
    profile_weights: dict[str, float] = field(default_factory=lambda: {
        "P-001": 0.15, "P-002": 0.10, "P-003": 0.15, "P-004": 0.10,
        "P-005": 0.08, "P-006": 0.10, "P-007": 0.02, "P-008": 0.10,
        "P-009": 0.05, "P-010": 0.15,
    })
    regions: list[str] = field(default_factory=lambda: [
        "US-NE", "US-SE", "US-MW", "US-W", "EU-W", "EU-E", "APAC"
    ])


# Map profiles to their device types and diagnoses
PROFILE_DEVICE_MAP: dict[str, dict[str, Any]] = {
    "P-001": {"device": "DDD", "diagnosis": "Sick Sinus Syndrome", "comorbidities": ["hypertension"], "af_burden": 0.02, "vt_risk": 0.0},
    "P-002": {"device": "DDD", "diagnosis": "Complete Heart Block", "comorbidities": [], "af_burden": 0.0, "vt_risk": 0.0},
    "P-003": {"device": "DDD", "diagnosis": "Paroxysmal AF + Bradycardia", "comorbidities": ["heart_failure", "diabetes"], "af_burden": 0.30, "vt_risk": 0.01},
    "P-004": {"device": "CRT_D", "diagnosis": "Ischemic CMP + VT", "comorbidities": ["heart_failure", "ckd"], "af_burden": 0.10, "vt_risk": 0.15},
    "P-005": {"device": "CRT_D", "diagnosis": "Idiopathic DCM", "comorbidities": ["heart_failure"], "af_burden": 0.02, "vt_risk": 0.05},
    "P-006": {"device": "VVI", "diagnosis": "Post-AVR + CHB", "comorbidities": ["hypertension"], "af_burden": 0.02, "vt_risk": 0.01},
    "P-007": {"device": "DDD", "diagnosis": "Young athlete CHB", "comorbidities": [], "af_burden": 0.0, "vt_risk": 0.0},
    "P-008": {"device": "DDD", "diagnosis": "AF + tachy-brady", "comorbidities": ["hypertension", "copd"], "af_burden": 0.60, "vt_risk": 0.01},
    "P-009": {"device": "ICD", "diagnosis": "HCM + VT risk", "comorbidities": ["hcm"], "af_burden": 0.02, "vt_risk": 0.10},
    "P-010": {"device": "VVI", "diagnosis": "Elderly multi-comorbidity", "comorbidities": ["heart_failure", "ckd", "diabetes", "atrial_fibrillation"], "af_burden": 0.40, "vt_risk": 0.05},
}

PROFILE_AGE_MAP: dict[str, int] = {
    "P-001": 72, "P-002": 65, "P-003": 78, "P-004": 58, "P-005": 45,
    "P-006": 82, "P-007": 28, "P-008": 70, "P-009": 35, "P-010": 88,
}


class CohortManager:
    """Manages creation and simulation of multi-patient cohorts."""

    def __init__(self, distribution: CohortDistribution | None = None, base_seed: int = 42) -> None:
        self.distribution = distribution or CohortDistribution()
        self.base_seed = base_seed
        self.rng = np.random.default_rng(base_seed)
        self.patients: list[PatientInstance] = []
        self._generated = False

    def generate_cohort(self) -> list[PatientInstance]:
        """Generate a cohort of patients based on the distribution parameters."""
        if self._generated:
            return self.patients

        dist = self.distribution
        profiles = list(dist.profile_weights.keys())
        profile_probs = np.array([dist.profile_weights[p] for p in profiles])
        profile_probs = profile_probs / profile_probs.sum()  # Normalize

        for i in range(dist.size):
            patient_seed = self.base_seed + i
            patient_rng = np.random.default_rng(patient_seed)

            # Select profile
            profile_id = profiles[patient_rng.choice(len(profiles), p=profile_probs)]
            profile_data = PROFILE_DEVICE_MAP[profile_id]

            # Age: use profile's age with some jitter, clamped
            base_age = PROFILE_AGE_MAP[profile_id]
            age = int(np.clip(
                patient_rng.normal(base_age, 5),
                dist.age_min,
                dist.age_max
            ))

            # Sex
            sex = "M" if patient_rng.random() < dist.male_fraction else "F"

            # Implant age
            implant_age_days = int(patient_rng.uniform(0, dist.implant_age_max_years * 365))

            # Region
            region = dist.regions[patient_rng.integers(0, len(dist.regions))]

            patient = PatientInstance(
                profile_id=profile_id,
                device_type=profile_data["device"],
                age=age,
                sex=sex,
                diagnosis=profile_data["diagnosis"],
                comorbidities=list(profile_data["comorbidities"]),
                implant_age_days=implant_age_days,
                region=region,
                seed=patient_seed,
            )
            self.patients.append(patient)

        self._generated = True
        return self.patients

    def get_patient(self, patient_id: str) -> PatientInstance | None:
        """Look up a patient by ID."""
        for p in self.patients:
            if p.patient_id == patient_id:
                return p
        return None

    def get_patients_by_profile(self, profile_id: str) -> list[PatientInstance]:
        """Get all patients matching a profile."""
        return [p for p in self.patients if p.profile_id == profile_id]

    def get_patients_by_device(self, device_type: str) -> list[PatientInstance]:
        """Get all patients with a specific device type."""
        return [p for p in self.patients if p.device_type == device_type]

    @property
    def cohort_summary(self) -> dict[str, Any]:
        """Summary statistics for the cohort."""
        if not self.patients:
            return {"size": 0}

        ages = [p.age for p in self.patients]
        device_counts: dict[str, int] = {}
        profile_counts: dict[str, int] = {}
        sex_counts = {"M": 0, "F": 0}

        for p in self.patients:
            device_counts[p.device_type] = device_counts.get(p.device_type, 0) + 1
            profile_counts[p.profile_id] = profile_counts.get(p.profile_id, 0) + 1
            sex_counts[p.sex] = sex_counts.get(p.sex, 0) + 1

        return {
            "size": len(self.patients),
            "age_mean": float(np.mean(ages)),
            "age_std": float(np.std(ages)),
            "age_min": min(ages),
            "age_max": max(ages),
            "sex_distribution": sex_counts,
            "device_distribution": device_counts,
            "profile_distribution": profile_counts,
        }


class SimulationClock:
    """Manages simulation time with configurable acceleration."""

    def __init__(self, speed_multiplier: float = 1.0, start_time_s: float = 0.0) -> None:
        self._sim_time_s = start_time_s
        self._speed = speed_multiplier
        self._paused = False
        self._total_steps = 0

    def advance(self, real_dt_seconds: float) -> float:
        """Advance simulation clock by real_dt_seconds * speed. Returns new sim time."""
        if self._paused:
            return self._sim_time_s
        sim_dt = real_dt_seconds * self._speed
        self._sim_time_s += sim_dt
        self._total_steps += 1
        return self._sim_time_s

    def advance_sim(self, sim_dt_seconds: float) -> float:
        """Advance simulation clock by exact sim seconds (ignoring speed)."""
        if self._paused:
            return self._sim_time_s
        self._sim_time_s += sim_dt_seconds
        self._total_steps += 1
        return self._sim_time_s

    @property
    def time_s(self) -> float:
        return self._sim_time_s

    @property
    def time_hours(self) -> float:
        return self._sim_time_s / 3600.0

    @property
    def time_days(self) -> float:
        return self._sim_time_s / 86400.0

    @property
    def time_of_day_hours(self) -> float:
        """Current time of day (0-24 hours)."""
        return (self._sim_time_s % 86400) / 3600.0

    @property
    def day_number(self) -> int:
        return int(self._sim_time_s // 86400)

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        self._speed = max(0.001, value)

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "sim_time_s": self._sim_time_s,
            "sim_days": self.time_days,
            "time_of_day": self.time_of_day_hours,
            "speed_multiplier": self._speed,
            "paused": self._paused,
            "total_steps": self._total_steps,
        }


class PatientSimulator:
    """Runs a single patient's simulation, producing events on the stream."""

    def __init__(self, patient: PatientInstance, stream: EventStream) -> None:
        self.patient = patient
        self.stream = stream
        self._sim_time_s = 0.0
        self._device_status_interval_s = 3600.0  # Report device status hourly
        self._last_device_status_s = 0.0
        self._last_lead_check_s = 0.0
        self._lead_check_interval_s = 86400.0  # Daily lead measurements

    def step(self, dt_seconds: float, clock: SimulationClock) -> list[TelemetryEvent]:
        """Advance this patient's simulation by dt_seconds. Returns generated events."""
        events: list[TelemetryEvent] = []
        self._sim_time_s = clock.time_s
        time_of_day = clock.time_of_day_hours

        p = self.patient

        # --- Activity step ---
        if p.activity_engine is not None:
            activity_state = p.activity_engine.step(time_of_day, dt_seconds)
            if activity_state is not None:
                events.append(TelemetryEvent(
                    timestamp_s=self._sim_time_s,
                    patient_id=p.patient_id,
                    device_serial=p.device_serial,
                    event_type=EventType.ACTIVITY,
                    payload={
                        "state": getattr(activity_state, "state", "unknown"),
                        "counts_per_min": getattr(activity_state, "counts_per_min", 0),
                    },
                    size_bytes=32,
                ))

        # --- Rhythm step ---
        activity_level = 0.3  # Default
        if p.activity_engine is not None:
            activity_level = getattr(
                p.activity_engine, "current_activity_level", 0.3
            )

        if p.rhythm_engine is not None:
            from src.generator.cardiac.rhythm_engine import RhythmContext
            context = RhythmContext(
                time_of_day_hours=time_of_day,
                activity_level=activity_level,
                medications={},
                patient_age=p.age,
            )
            p.rhythm_engine.step(dt_seconds, context)
            hr = p.rhythm_engine.get_heart_rate()
            rr_ms = p.rhythm_engine.get_rr_interval_ms()

            events.append(TelemetryEvent(
                timestamp_s=self._sim_time_s,
                patient_id=p.patient_id,
                device_serial=p.device_serial,
                event_type=EventType.HEARTBEAT,
                payload={
                    "heart_rate": hr,
                    "rr_interval_ms": rr_ms,
                    "rhythm": p.rhythm_engine.current_state.value,
                },
                size_bytes=64,
            ))

        # --- Battery step ---
        if p.battery_model is not None:
            dt_hours = dt_seconds / 3600.0
            pacing_pct = 50.0  # Default
            if p.pacing_engine is not None:
                stats = p.pacing_engine.get_pacing_statistics()
                pacing_pct = stats.get("ventricular_pacing_pct", 50.0)
            pacing_current_ua = 15.0 * (pacing_pct / 100.0)
            p.battery_model.step(dt_hours, pacing_current_ua)

        # --- Lead impedance step ---
        if self._sim_time_s - self._last_lead_check_s >= self._lead_check_interval_s:
            dt_days = (self._sim_time_s - self._last_lead_check_s) / 86400.0
            for lead_id, lead_model in p.lead_models.items():
                impedance = lead_model.step(dt_days)
                events.append(TelemetryEvent(
                    timestamp_s=self._sim_time_s,
                    patient_id=p.patient_id,
                    device_serial=p.device_serial,
                    event_type=EventType.LEAD_MEASUREMENT,
                    payload={
                        "lead_id": lead_id,
                        "impedance_ohms": impedance,
                        "status": lead_model.get_status(),
                    },
                    size_bytes=48,
                ))

                # Check for lead alerts
                if p.alert_generator is not None:
                    alert = p.alert_generator.check_lead(lead_id, impedance)
                    if alert is not None:
                        events.append(TelemetryEvent(
                            timestamp_s=self._sim_time_s,
                            patient_id=p.patient_id,
                            device_serial=p.device_serial,
                            event_type=EventType.ALERT,
                            payload={
                                "alert_type": alert.alert_type,
                                "priority": alert.priority,
                                "data": alert.data,
                            },
                            size_bytes=256,
                        ))
            self._last_lead_check_s = self._sim_time_s

        # --- Device status report ---
        if self._sim_time_s - self._last_device_status_s >= self._device_status_interval_s:
            status_payload: dict[str, Any] = {
                "device_serial": p.device_serial,
                "device_type": p.device_type,
            }
            if p.battery_model is not None:
                bstate = p.battery_model.get_state()
                status_payload["battery_voltage"] = bstate.voltage
                status_payload["battery_stage"] = bstate.stage
                # Check battery alert
                if p.alert_generator is not None:
                    alert = p.alert_generator.check_battery(bstate)
                    if alert is not None:
                        events.append(TelemetryEvent(
                            timestamp_s=self._sim_time_s,
                            patient_id=p.patient_id,
                            device_serial=p.device_serial,
                            event_type=EventType.ALERT,
                            payload={
                                "alert_type": alert.alert_type,
                                "priority": alert.priority,
                                "data": alert.data,
                            },
                            size_bytes=256,
                        ))

            if p.pacing_engine is not None:
                status_payload["pacing_stats"] = p.pacing_engine.get_pacing_statistics()

            events.append(TelemetryEvent(
                timestamp_s=self._sim_time_s,
                patient_id=p.patient_id,
                device_serial=p.device_serial,
                event_type=EventType.DEVICE_STATUS,
                payload=status_payload,
                size_bytes=128,
            ))
            self._last_device_status_s = self._sim_time_s

        # --- Arrhythmia episodes ---
        if p.arrhythmia_generator is not None:
            dt_hours = dt_seconds / 3600.0
            episodes = p.arrhythmia_generator.generate_episodes(
                dt_hours, time_offset_s=self._sim_time_s
            )
            for ep in episodes:
                events.append(TelemetryEvent(
                    timestamp_s=ep.onset_time_s,
                    patient_id=p.patient_id,
                    device_serial=p.device_serial,
                    event_type=EventType.EPISODE_START,
                    payload={
                        "episode_id": ep.episode_id,
                        "episode_type": ep.episode_type,
                        "max_rate_bpm": ep.max_rate_bpm,
                        "duration_s": ep.duration_s,
                        "terminated_by": ep.terminated_by,
                        "is_sustained": ep.is_sustained,
                    },
                    size_bytes=1024,  # Includes EGM reference
                ))

                # Check for alert
                if p.alert_generator is not None:
                    alert = p.alert_generator.check_episode(ep)
                    if alert is not None:
                        events.append(TelemetryEvent(
                            timestamp_s=ep.onset_time_s,
                            patient_id=p.patient_id,
                            device_serial=p.device_serial,
                            event_type=EventType.ALERT,
                            payload={
                                "alert_type": alert.alert_type,
                                "priority": alert.priority,
                                "data": alert.data,
                                "episode_id": ep.episode_id,
                            },
                            size_bytes=256,
                        ))

        # Push to stream and transmission assembler
        for event in events:
            self.stream.push(event)
            if p.transmission_assembler is not None:
                transmissions = p.transmission_assembler.add_event(event)
                for tx in transmissions:
                    self.stream.push(TelemetryEvent(
                        timestamp_s=tx.timestamp_s,
                        patient_id=p.patient_id,
                        device_serial=p.device_serial,
                        event_type=EventType.TRANSMISSION,
                        payload={
                            "transmission_id": tx.transmission_id,
                            "transmission_type": tx.transmission_type,
                            "payload_size_bytes": tx.payload_size_bytes,
                            "alert_flags": tx.alert_flags,
                            "event_count": len(tx.events),
                        },
                        size_bytes=tx.payload_size_bytes,
                    ))

        return events
