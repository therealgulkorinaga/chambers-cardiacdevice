"""
Pydantic-based configuration for the Chamber Sentinel CIED Telemetry Simulator.

All settings can be overridden via environment variables prefixed with ``CIED_SIM_``.
Nested settings use double-underscore separators, e.g.
``CIED_SIM_SIMULATION__CLOCK_SPEED=2.0``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Sub-setting groups
# ---------------------------------------------------------------------------


class SimulationSettings(BaseModel):
    """Top-level simulation control knobs."""

    clock_speed: float = Field(
        default=1.0,
        gt=0.0,
        description=(
            "Ratio of simulated time to wall-clock time. "
            "1.0 = real-time, 10.0 = 10x faster."
        ),
    )
    duration_days: int = Field(
        default=365,
        gt=0,
        description="Total simulated time span in days.",
    )
    random_seed: int | None = Field(
        default=None,
        description="If set, seeds all PRNGs for reproducible runs.",
    )


class GeneratorSettings(BaseModel):
    """Signal-generation parameters."""

    sample_rate_hz: int = Field(
        default=256,
        gt=0,
        description="EGM sampling rate in Hz.",
    )
    egm_channels: list[str] = Field(
        default=["atrial", "ventricular"],
        min_length=1,
        description="Named electrogram channels to simulate.",
    )
    noise_floor_mv: float = Field(
        default=0.1,
        ge=0.0,
        description="Baseline noise floor in millivolts.",
    )
    egm_mode: str = Field(
        default="parametric",
        pattern=r"^(parametric|opencarp)$",
        description=(
            "EGM waveform generation mode. "
            "'parametric' = built-in Gaussian synthesis (Mode A). "
            "'opencarp' = pre-computed openCARP ionic-model templates (Mode B). "
            "Mode B falls back to Mode A if templates are not available."
        ),
    )
    opencarp_template_dir: str = Field(
        default="src/generator/cardiac/opencarp/templates",
        description="Directory containing pre-computed openCARP .npy template files.",
    )

    @field_validator("egm_channels", mode="before")
    @classmethod
    def _parse_channels(cls, v: Any) -> list[str]:
        """Accept a comma-separated string from environment variables."""
        if isinstance(v, str):
            return [ch.strip() for ch in v.split(",") if ch.strip()]
        return v


class CurrentArchSettings(BaseModel):
    """Parameters governing the *current* (persist-by-default) architecture."""

    cloud_retention_days: int | None = Field(
        default=None,
        description=(
            "Days to retain data in the cloud layer. "
            "None means indefinite retention (current-arch default)."
        ),
    )
    enable_aggregate_pool: bool = Field(
        default=True,
        description=(
            "Whether de-identified data is pooled into the provider aggregate store."
        ),
    )


class ChambersSettings(BaseModel):
    """Parameters governing the *Chambers* (burn-by-default) architecture."""

    relay_ttl_seconds: int = Field(
        default=259_200,  # 72 hours
        gt=0,
        description="Time-to-live for data sitting in the relay (seconds). Default 72 h.",
    )
    clinical_burn_after_ack: bool = Field(
        default=True,
        description=(
            "Burn clinical telemetry from the relay once the clinician acknowledges receipt."
        ),
    )
    device_maint_window_days: int = Field(
        default=90,
        gt=0,
        description="Rolling window (days) of device-maintenance data kept on the device.",
    )
    research_k_anonymity: int = Field(
        default=10,
        ge=2,
        description="Minimum k-anonymity threshold before research data leaves the relay.",
    )
    research_epsilon: float = Field(
        default=1.0,
        gt=0.0,
        description="Differential-privacy epsilon budget for research data release.",
    )


class AnalyticsSettings(BaseModel):
    """Metrics and analytics configuration."""

    track_persistence_volume: bool = Field(
        default=True,
        description="Track cumulative data-at-rest volume over time.",
    )
    track_attack_surface: bool = Field(
        default=True,
        description="Track composite attack-surface score over time.",
    )
    attack_surface_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "data_at_rest_gb": 0.30,
            "copies_count": 0.25,
            "access_points": 0.20,
            "retention_days": 0.15,
            "identifiability_score": 0.10,
        },
        description=(
            "Weights for each factor in the composite attack-surface score. "
            "Values should sum to 1.0."
        ),
    )

    @field_validator("attack_surface_weights")
    @classmethod
    def _weights_sum_to_one(cls, v: dict[str, float]) -> dict[str, float]:
        total = sum(v.values())
        if abs(total - 1.0) > 1e-6:
            msg = f"attack_surface_weights must sum to 1.0, got {total:.6f}"
            raise ValueError(msg)
        return v


# ---------------------------------------------------------------------------
# Root settings object
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """
    Root configuration for the Chamber Sentinel CIED Simulator.

    Environment variables are read with the ``CIED_SIM_`` prefix.  Nested
    groups use double-underscore separators::

        CIED_SIM_SIMULATION__CLOCK_SPEED=5.0
        CIED_SIM_CHAMBERS__RELAY_TTL_SECONDS=86400
    """

    model_config = SettingsConfigDict(
        env_prefix="CIED_SIM_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    simulation: SimulationSettings = Field(default_factory=SimulationSettings)
    generator: GeneratorSettings = Field(default_factory=GeneratorSettings)
    current_arch: CurrentArchSettings = Field(default_factory=CurrentArchSettings)
    chambers: ChambersSettings = Field(default_factory=ChambersSettings)
    analytics: AnalyticsSettings = Field(default_factory=AnalyticsSettings)


def get_settings(**overrides: Any) -> Settings:
    """
    Construct a :class:`Settings` instance, optionally applying programmatic
    overrides (useful in tests).

    Parameters
    ----------
    **overrides:
        Keyword arguments forwarded to the ``Settings`` constructor.  Nested
        groups may be passed as dicts, e.g.
        ``get_settings(simulation={"clock_speed": 10.0})``.

    Returns
    -------
    Settings
    """
    return Settings(**overrides)
