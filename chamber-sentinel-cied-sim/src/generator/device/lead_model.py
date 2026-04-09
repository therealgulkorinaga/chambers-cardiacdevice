"""
Lead impedance evolution model.

Simulates the characteristic impedance trajectory of a CIED pacing/sensing
lead from acute implant through chronic steady-state, with support for
injecting realistic failure modes (fracture, insulation breach, connection
fault).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np
from numpy.random import Generator


# ---------------------------------------------------------------------------
# Public data-model types
# ---------------------------------------------------------------------------


class LeadStatus(Enum):
    """Lead health status classification."""

    NORMAL = "normal"
    MATURING = "maturing"
    FRACTURED = "fractured"
    BREACHED = "breached"
    CONNECTION_ISSUE = "connection_issue"


class FailureType(Enum):
    """Supported lead failure modes."""

    FRACTURE = "fracture"
    INSULATION_BREACH = "insulation_breach"
    CONNECTION = "connection"


@dataclass
class LeadConfig:
    """Static configuration for a single lead."""

    lead_id: str
    position: str  # 'RA', 'RV', 'LV', 'RV_coil'
    implant_impedance_ohms: float = 600.0
    chronic_impedance_ohms: float = 500.0
    maturation_days: int = 90


@dataclass
class _FailureSpec:
    """Internal specification for an injected failure."""

    failure_type: FailureType
    onset_time_days: float
    sharpness: float  # sigmoid steepness parameter


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class LeadModel:
    """
    Models the impedance evolution of a single CIED lead.

    Normal evolution
    ~~~~~~~~~~~~~~~~
    * **Acute phase** (day 0): impedance at ``implant_impedance_ohms``
      (typically 400-1200 ohms).
    * **Maturation** (0 - ``maturation_days``): exponential decay toward
      ``chronic_impedance_ohms`` as fibrous capsule stabilises.
    * **Chronic phase**: slow linear drift (~ +0.5 ohm/year) with +-5 %
      measurement noise.

    Failure modes
    ~~~~~~~~~~~~~
    * **Fracture**: sigmoid ramp to >2000 ohms.
    * **Insulation breach**: sigmoid drop to <200 ohms.
    * **Connection fault**: intermittent high-variance spikes.
    """

    def __init__(self, config: LeadConfig, rng: Generator | None = None) -> None:
        self._config = config
        self._rng: Generator = rng if rng is not None else np.random.default_rng()

        # Time tracking
        self._elapsed_days: float = 0.0

        # Current impedance (initialised to implant value)
        self._impedance_ohms: float = config.implant_impedance_ohms

        # Chronic drift rate (ohms per day) -- slight upward trend
        self._chronic_drift_rate: float = 0.5 / 365.0  # ~0.5 ohm/year

        # Noise scale (5 % of chronic impedance)
        self._noise_scale: float = config.chronic_impedance_ohms * 0.05

        # Failure specs (injected via inject_failure)
        self._failures: list[_FailureSpec] = []

        # Cached failure state for status reporting
        self._active_failure: FailureType | None = None

    # -- Normal impedance trajectory ---------------------------------------

    def _baseline_impedance(self, days: float) -> float:
        """
        Compute the normal (no-failure) impedance at the given number of
        days post-implant.
        """
        cfg = self._config

        if days < 0:
            return cfg.implant_impedance_ohms

        # Maturation: exponential decay from implant to chronic
        # Z(t) = Z_chronic + (Z_implant - Z_chronic) * exp(-t / tau)
        tau = cfg.maturation_days / 3.0  # 3 time-constants = ~95 % settled
        maturation_component = (cfg.implant_impedance_ohms - cfg.chronic_impedance_ohms) * math.exp(
            -days / max(tau, 1e-6)
        )

        # Chronic drift (only meaningful after maturation largely complete)
        drift = self._chronic_drift_rate * max(0.0, days - cfg.maturation_days)

        return cfg.chronic_impedance_ohms + maturation_component + drift

    # -- Failure modifiers -------------------------------------------------

    def _apply_failures(self, base_impedance: float, days: float) -> float:
        """
        Apply any injected failure effects on top of the baseline impedance.
        Returns the modified impedance.
        """
        impedance = base_impedance
        self._active_failure = None

        for spec in self._failures:
            if days < spec.onset_time_days - 30:
                # Too early -- failure hasn't begun
                continue

            # Sigmoid transition: s(t) = 1 / (1 + exp(-sharpness * (t - onset)))
            x = spec.sharpness * (days - spec.onset_time_days)
            sigmoid = 1.0 / (1.0 + math.exp(-min(x, 50.0)))  # clamp to avoid overflow

            if spec.failure_type == FailureType.FRACTURE:
                # Ramp toward >2000 ohms
                target = self._rng.uniform(2000.0, 5000.0)
                impedance = base_impedance + (target - base_impedance) * sigmoid
                if sigmoid > 0.1:
                    self._active_failure = FailureType.FRACTURE

            elif spec.failure_type == FailureType.INSULATION_BREACH:
                # Drop toward <200 ohms
                target = self._rng.uniform(50.0, 200.0)
                impedance = base_impedance - (base_impedance - target) * sigmoid
                if sigmoid > 0.1:
                    self._active_failure = FailureType.INSULATION_BREACH

            elif spec.failure_type == FailureType.CONNECTION:
                # Intermittent spikes: add high-variance noise that
                # increases with the sigmoid
                if sigmoid > 0.05:
                    spike_probability = 0.3 * sigmoid
                    if self._rng.random() < spike_probability:
                        spike_magnitude = self._rng.uniform(500.0, 3000.0) * sigmoid
                        impedance = base_impedance + spike_magnitude
                    self._active_failure = FailureType.CONNECTION

        return impedance

    # -- Public API --------------------------------------------------------

    def step(self, dt_days: float) -> float:
        """
        Advance the lead model by *dt_days* and return the current impedance.

        Parameters
        ----------
        dt_days:
            Time step in days.

        Returns
        -------
        float
            Measured impedance in ohms (includes measurement noise).
        """
        self._elapsed_days += dt_days

        # Baseline impedance (normal trajectory)
        base = self._baseline_impedance(self._elapsed_days)

        # Apply failure effects
        impedance = self._apply_failures(base, self._elapsed_days)

        # Measurement noise (+-5 %)
        noise = self._rng.normal(0.0, self._noise_scale)
        impedance += noise

        # Physical clamp: impedance cannot be negative
        impedance = max(impedance, 10.0)

        self._impedance_ohms = impedance
        return impedance

    def inject_failure(
        self,
        failure_type: str,
        onset_time_days: float,
        sharpness: float = 5.0,
    ) -> None:
        """
        Schedule a lead failure.

        Parameters
        ----------
        failure_type:
            One of ``'fracture'``, ``'insulation_breach'``, ``'connection'``.
        onset_time_days:
            Days post-implant at which the failure midpoint occurs.
        sharpness:
            Sigmoid steepness (higher = more abrupt transition).
            Default 5.0 gives a transition over ~2 days.
        """
        ft = FailureType(failure_type)
        self._failures.append(
            _FailureSpec(
                failure_type=ft,
                onset_time_days=onset_time_days,
                sharpness=sharpness,
            )
        )

    def get_impedance(self) -> float:
        """Return the most recently computed impedance in ohms."""
        return self._impedance_ohms

    def get_status(self) -> str:
        """
        Return the lead health status string.

        Returns
        -------
        str
            One of ``'normal'``, ``'maturing'``, ``'fractured'``,
            ``'breached'``, ``'connection_issue'``.
        """
        if self._active_failure == FailureType.FRACTURE:
            return LeadStatus.FRACTURED.value
        if self._active_failure == FailureType.INSULATION_BREACH:
            return LeadStatus.BREACHED.value
        if self._active_failure == FailureType.CONNECTION:
            return LeadStatus.CONNECTION_ISSUE.value
        if self._elapsed_days < self._config.maturation_days:
            return LeadStatus.MATURING.value
        return LeadStatus.NORMAL.value

    def get_elapsed_days(self) -> float:
        """Return the total elapsed simulation time in days."""
        return self._elapsed_days
