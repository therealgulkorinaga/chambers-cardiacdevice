"""
Pydantic models that validate YAML device and patient profiles on load.

Usage::

    from src.config.models import load_device_profiles, load_patient_profiles

    devices = load_device_profiles()            # list[DeviceProfile]
    patients = load_patient_profiles()          # list[PatientProfile]
    device_map = load_device_profiles_map()     # dict[str, DeviceProfile]
    patient_map = load_patient_profiles_map()   # dict[str, PatientProfile]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_CONFIG_DIR = Path(__file__).resolve().parent
_DEVICE_PROFILES_PATH = _CONFIG_DIR / "device_profiles.yaml"
_PATIENT_PROFILES_PATH = _CONFIG_DIR / "patient_profiles.yaml"


# ---------------------------------------------------------------------------
# Device profile models
# ---------------------------------------------------------------------------


class BatterySpec(BaseModel):
    """Battery characteristics for a CIED."""

    chemistry: str
    voltage_bol: float = Field(gt=0, description="Beginning-of-life voltage (V)")
    voltage_eri: float = Field(gt=0, description="Elective replacement indicator voltage (V)")
    voltage_eos: float = Field(gt=0, description="End-of-service voltage (V)")
    longevity_years_min: int = Field(gt=0)
    longevity_years_max: int = Field(gt=0)

    @model_validator(mode="after")
    def _voltage_ordering(self) -> "BatterySpec":
        if not (self.voltage_bol >= self.voltage_eri >= self.voltage_eos):
            msg = (
                f"Battery voltages must satisfy BOL >= ERI >= EOS; got "
                f"{self.voltage_bol}, {self.voltage_eri}, {self.voltage_eos}"
            )
            raise ValueError(msg)
        if self.longevity_years_min > self.longevity_years_max:
            msg = (
                f"longevity_years_min ({self.longevity_years_min}) "
                f"> longevity_years_max ({self.longevity_years_max})"
            )
            raise ValueError(msg)
        return self


class LeadSpec(BaseModel):
    """Single lead position and expected impedance range."""

    position: str
    impedance_ohms_min: int = Field(gt=0)
    impedance_ohms_max: int = Field(gt=0)

    @model_validator(mode="after")
    def _impedance_range(self) -> "LeadSpec":
        if self.impedance_ohms_min > self.impedance_ohms_max:
            msg = (
                f"impedance_ohms_min ({self.impedance_ohms_min}) "
                f"> impedance_ohms_max ({self.impedance_ohms_max})"
            )
            raise ValueError(msg)
        return self


class TransmissionSpec(BaseModel):
    """RF transmission characteristics."""

    daily_check_bytes: int = Field(gt=0)
    full_interrogation_kb: int = Field(gt=0)
    protocol: str


class DeviceProfile(BaseModel):
    """Validated specification for a single CIED device type."""

    device_type: str
    description: str
    pacing_modes: list[str] = Field(min_length=1)
    channels: list[str] = Field(min_length=1)
    memory_kb: int = Field(gt=0)
    battery: BatterySpec
    leads: list[LeadSpec] = Field(min_length=1)
    sample_rate_hz: int = Field(gt=0)
    transmission: TransmissionSpec


class DeviceProfilesFile(BaseModel):
    """Top-level wrapper matching the YAML root key ``devices``."""

    devices: list[DeviceProfile]


# ---------------------------------------------------------------------------
# Patient profile models
# ---------------------------------------------------------------------------


class Demographics(BaseModel):
    """Basic demographic facts."""

    age: int = Field(ge=0, le=120)
    sex: str
    bmi: float = Field(gt=0)

    @field_validator("sex")
    @classmethod
    def _normalize_sex(cls, v: str) -> str:
        allowed = {"male", "female", "other"}
        v_lower = v.strip().lower()
        if v_lower not in allowed:
            msg = f"sex must be one of {allowed}, got '{v}'"
            raise ValueError(msg)
        return v_lower


class RhythmTransitionOverrides(BaseModel):
    """Per-hour transition probabilities between cardiac rhythm states."""

    nsr_to_af_per_hour: float = Field(ge=0, le=1, default=0.0)
    af_to_nsr_per_hour: float = Field(ge=0, le=1, default=0.0)
    nsr_to_vt_per_hour: float = Field(ge=0, le=1, default=0.0)
    vt_to_nsr_per_hour: float = Field(ge=0, le=1, default=0.0)
    vt_to_vf_per_hour: float = Field(ge=0, le=1, default=0.0)

    model_config = {"extra": "allow"}  # forward-compatible with new rhythms


class ActivityParams(BaseModel):
    """Physical-activity and circadian parameters."""

    resting_hr_bpm: int = Field(gt=0)
    max_hr_bpm: int = Field(gt=0)
    daily_active_minutes_min: int = Field(ge=0)
    daily_active_minutes_max: int = Field(ge=0)
    circadian_amplitude: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _hr_range(self) -> "ActivityParams":
        if self.resting_hr_bpm >= self.max_hr_bpm:
            msg = (
                f"resting_hr_bpm ({self.resting_hr_bpm}) "
                f"must be < max_hr_bpm ({self.max_hr_bpm})"
            )
            raise ValueError(msg)
        if self.daily_active_minutes_min > self.daily_active_minutes_max:
            msg = (
                f"daily_active_minutes_min ({self.daily_active_minutes_min}) "
                f"> daily_active_minutes_max ({self.daily_active_minutes_max})"
            )
            raise ValueError(msg)
        return self


class MedicationEffect(BaseModel):
    """Effect a single medication class has on cardiac parameters."""

    hr_reduction_fraction: float = Field(
        ge=-1.0,
        le=1.0,
        description="Fractional reduction in HR (negative = increase).",
    )
    av_conduction_delay_ms: float = Field(
        ge=0,
        description="Additional AV conduction delay in milliseconds.",
    )


class PatientProfile(BaseModel):
    """Validated archetype for a virtual patient."""

    patient_id: str
    label: str
    primary_diagnosis: str
    device_type: str
    demographics: Demographics
    comorbidities: list[str] = Field(default_factory=list)
    heart_failure_nyha: int | None = Field(default=None, ge=1, le=4)
    af_burden_fraction: float = Field(ge=0, le=1)
    vt_risk_annual: float = Field(ge=0, le=1)
    pacing_dependency: float = Field(ge=0, le=1)
    rhythm_transition_overrides: RhythmTransitionOverrides = Field(
        default_factory=RhythmTransitionOverrides
    )
    activity_params: ActivityParams
    medication_effects: dict[str, MedicationEffect] = Field(default_factory=dict)

    @field_validator("medication_effects", mode="before")
    @classmethod
    def _coerce_medication_effects(
        cls, v: Any
    ) -> dict[str, MedicationEffect]:
        """Allow an empty dict ``{}`` from YAML without error."""
        if v is None:
            return {}
        if isinstance(v, dict):
            return {
                k: (MedicationEffect(**val) if isinstance(val, dict) else val)
                for k, val in v.items()
            }
        return v


class PatientProfilesFile(BaseModel):
    """Top-level wrapper matching the YAML root key ``patients``."""

    patients: list[PatientProfile]


# ---------------------------------------------------------------------------
# Loader functions
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file and return its contents as a dict."""
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        msg = f"Expected a YAML mapping at the root of {path}, got {type(data).__name__}"
        raise TypeError(msg)
    return data


def load_device_profiles(path: Path | None = None) -> list[DeviceProfile]:
    """
    Load and validate all device profiles from the YAML file.

    Parameters
    ----------
    path:
        Override path to the YAML file.  Defaults to the bundled
        ``device_profiles.yaml`` shipped alongside this module.

    Returns
    -------
    list[DeviceProfile]
    """
    path = path or _DEVICE_PROFILES_PATH
    raw = _read_yaml(path)
    wrapper = DeviceProfilesFile.model_validate(raw)
    return wrapper.devices


def load_device_profiles_map(path: Path | None = None) -> dict[str, DeviceProfile]:
    """
    Return device profiles keyed by ``device_type``.

    Raises
    ------
    ValueError
        If duplicate ``device_type`` values are found.
    """
    profiles = load_device_profiles(path)
    result: dict[str, DeviceProfile] = {}
    for p in profiles:
        if p.device_type in result:
            msg = f"Duplicate device_type: {p.device_type}"
            raise ValueError(msg)
        result[p.device_type] = p
    return result


def load_patient_profiles(path: Path | None = None) -> list[PatientProfile]:
    """
    Load and validate all patient profiles from the YAML file.

    Parameters
    ----------
    path:
        Override path to the YAML file.  Defaults to the bundled
        ``patient_profiles.yaml`` shipped alongside this module.

    Returns
    -------
    list[PatientProfile]
    """
    path = path or _PATIENT_PROFILES_PATH
    raw = _read_yaml(path)
    wrapper = PatientProfilesFile.model_validate(raw)
    return wrapper.patients


def load_patient_profiles_map(path: Path | None = None) -> dict[str, PatientProfile]:
    """
    Return patient profiles keyed by ``patient_id``.

    Raises
    ------
    ValueError
        If duplicate ``patient_id`` values are found.
    """
    profiles = load_patient_profiles(path)
    result: dict[str, PatientProfile] = {}
    for p in profiles:
        if p.patient_id in result:
            msg = f"Duplicate patient_id: {p.patient_id}"
            raise ValueError(msg)
        result[p.patient_id] = p
    return result
