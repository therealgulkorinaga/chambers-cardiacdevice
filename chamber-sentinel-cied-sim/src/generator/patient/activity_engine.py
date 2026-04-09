"""Accelerometer-based activity simulation engine.

Simulates patient physical activity states and their associated accelerometer
counts and heart-rate modifiers.  State transitions follow circadian
probabilities with stochastic noise to produce realistic daily activity
patterns.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np

from .circadian_model import CircadianModel


class ActivityLevel(enum.Enum):
    """Discrete patient activity levels with associated accelerometer count ranges."""

    SLEEP = "sleep"
    RESTING = "resting"
    LIGHT = "light"
    MODERATE = "moderate"
    VIGOROUS = "vigorous"


# Accelerometer counts-per-minute ranges and HR target modifiers per activity level
_ACTIVITY_PARAMS: Dict[ActivityLevel, Dict[str, float]] = {
    ActivityLevel.SLEEP: {
        "counts_min": 0.0,
        "counts_max": 10.0,
        "hr_target_modifier": 0.80,  # HR drops to ~80% of baseline during sleep
    },
    ActivityLevel.RESTING: {
        "counts_min": 10.0,
        "counts_max": 30.0,
        "hr_target_modifier": 1.00,
    },
    ActivityLevel.LIGHT: {
        "counts_min": 30.0,
        "counts_max": 80.0,
        "hr_target_modifier": 1.15,
    },
    ActivityLevel.MODERATE: {
        "counts_min": 80.0,
        "counts_max": 150.0,
        "hr_target_modifier": 1.40,
    },
    ActivityLevel.VIGOROUS: {
        "counts_min": 150.0,
        "counts_max": 300.0,
        "hr_target_modifier": 1.80,
    },
}

# Mapping from string names (circadian model output) to enum
_NAME_TO_LEVEL: Dict[str, ActivityLevel] = {level.value: level for level in ActivityLevel}


@dataclass
class ActivityState:
    """Snapshot of the current patient activity.

    Attributes:
        state_name:         Human-readable activity level name.
        counts_per_min:     Simulated accelerometer output (counts/min).
        hr_target_modifier: Multiplicative factor applied to the baseline HR
                            target for this activity level.
    """

    state_name: str
    counts_per_min: float
    hr_target_modifier: float


@dataclass
class _ProfileParams:
    """Internal parameters governing activity transition behavior.

    Attributes:
        min_bout_seconds:  Minimum duration before a state can transition.
        transition_noise:  Stochastic scaling applied to circadian probabilities
                           (higher = more random transitions).
        sedentary_bias:    Extra weight toward RESTING/SLEEP (models sedentary patients).
    """

    min_bout_seconds: float = 300.0
    transition_noise: float = 0.1
    sedentary_bias: float = 0.0


class ActivityEngine:
    """Simulates patient activity states over time.

    The engine maintains a current activity state and, at each time step,
    evaluates whether to transition to a new state based on circadian
    probabilities, minimum bout durations, and stochastic noise.

    Parameters:
        profile_params: Dictionary of behavioral profile parameters.  Accepted
            keys: ``min_bout_seconds`` (float), ``transition_noise`` (float),
            ``sedentary_bias`` (float, 0-1).
        circadian_model: A :class:`CircadianModel` providing time-of-day
            activity probabilities.
        rng: NumPy random generator for reproducibility.
    """

    def __init__(
        self,
        profile_params: Dict[str, float],
        circadian_model: CircadianModel,
        rng: np.random.Generator,
    ) -> None:
        self._profile = _ProfileParams(
            min_bout_seconds=profile_params.get("min_bout_seconds", 300.0),
            transition_noise=profile_params.get("transition_noise", 0.1),
            sedentary_bias=profile_params.get("sedentary_bias", 0.0),
        )
        self._circadian: CircadianModel = circadian_model
        self._rng: np.random.Generator = rng

        # State tracking
        self._current_level: ActivityLevel = ActivityLevel.RESTING
        self._time_in_state_s: float = 0.0
        self._current_counts: float = 15.0  # mid-range resting

        # Daily histogram: seconds spent in each state
        self._daily_histogram: Dict[ActivityLevel, float] = {
            level: 0.0 for level in ActivityLevel
        }

    @property
    def current_level(self) -> ActivityLevel:
        """Return the current activity level."""
        return self._current_level

    def step(
        self,
        time_of_day_hours: float,
        dt_seconds: float,
    ) -> ActivityState:
        """Advance the activity engine by one time step.

        Parameters:
            time_of_day_hours: Current time as fractional hours (0.0-24.0).
            dt_seconds: Size of the time step in seconds.

        Returns:
            An :class:`ActivityState` reflecting the (possibly updated) activity.
        """
        self._time_in_state_s += dt_seconds
        self._daily_histogram[self._current_level] += dt_seconds

        # Evaluate possible state transition
        if self._time_in_state_s >= self._profile.min_bout_seconds:
            new_level = self._evaluate_transition(time_of_day_hours, dt_seconds)
            if new_level != self._current_level:
                self._current_level = new_level
                self._time_in_state_s = 0.0

        # Update accelerometer counts with noise
        self._current_counts = self._sample_counts(self._current_level)

        params = _ACTIVITY_PARAMS[self._current_level]
        return ActivityState(
            state_name=self._current_level.value,
            counts_per_min=self._current_counts,
            hr_target_modifier=params["hr_target_modifier"],
        )

    def get_daily_summary(self) -> Dict[str, float]:
        """Return a summary of time (in seconds) spent in each activity state today.

        Returns:
            Dictionary mapping activity state names to cumulative seconds.
        """
        return {level.value: seconds for level, seconds in self._daily_histogram.items()}

    def reset_daily_summary(self) -> None:
        """Reset the daily activity histogram to zero."""
        for level in ActivityLevel:
            self._daily_histogram[level] = 0.0

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _evaluate_transition(
        self,
        time_of_day_hours: float,
        dt_seconds: float,
    ) -> ActivityLevel:
        """Determine whether to transition to a new activity state.

        Uses circadian probabilities as a base, applies sedentary bias and
        stochastic noise, then samples a new state.
        """
        circadian_probs = self._circadian.get_activity_probability(time_of_day_hours)

        # Convert to ordered arrays
        levels = list(ActivityLevel)
        probs = np.array(
            [circadian_probs.get(level.value, 0.0) for level in levels],
            dtype=np.float64,
        )

        # Apply sedentary bias (increase sleep + resting weight)
        bias = self._profile.sedentary_bias
        if bias > 0:
            for i, level in enumerate(levels):
                if level in {ActivityLevel.SLEEP, ActivityLevel.RESTING}:
                    probs[i] *= 1.0 + bias
                elif level in {ActivityLevel.MODERATE, ActivityLevel.VIGOROUS}:
                    probs[i] *= max(0.1, 1.0 - bias)

        # Add stochastic noise
        noise = self._rng.dirichlet(
            np.ones(len(levels)) * (1.0 / max(self._profile.transition_noise, 0.01))
        )
        alpha = self._profile.transition_noise
        probs = (1.0 - alpha) * probs + alpha * noise

        # Persistence bias: current state gets a bonus to avoid rapid oscillation
        current_idx = levels.index(self._current_level)
        persistence_bonus = 0.3
        probs[current_idx] += persistence_bonus

        # Adjacency preference: transitions to distant states are penalized
        # (e.g., SLEEP -> VIGOROUS is less likely than SLEEP -> RESTING)
        for i, level in enumerate(levels):
            distance = abs(i - current_idx)
            if distance > 1:
                probs[i] *= max(0.05, 1.0 / (distance ** 1.5))

        # Transition probability scales with dt (longer steps = more likely to transition)
        transition_prob = 1.0 - math.exp(-dt_seconds / self._profile.min_bout_seconds)
        stay_prob = 1.0 - transition_prob
        probs[current_idx] += stay_prob * probs.sum()

        # Normalize
        total = probs.sum()
        if total > 0:
            probs /= total
        else:
            probs = np.ones(len(levels)) / len(levels)

        # Sample
        chosen_idx = int(self._rng.choice(len(levels), p=probs))
        return levels[chosen_idx]

    def _sample_counts(self, level: ActivityLevel) -> float:
        """Sample an accelerometer counts-per-minute value for the given activity level.

        The count is drawn from a truncated Gaussian centered in the level's range.
        """
        params = _ACTIVITY_PARAMS[level]
        lo = params["counts_min"]
        hi = params["counts_max"]
        mid = (lo + hi) / 2.0
        sigma = (hi - lo) / 4.0  # ~95% within range

        count = float(self._rng.normal(mid, sigma))
        return max(lo, min(hi, count))
