"""Circadian heart-rate modulation model.

Models the 24-hour physiological variation in heart rate and activity
probabilities using a cosine-based circadian oscillator.  The heart-rate
nadir occurs around 03:00 and the peak around 14:00, consistent with
published ambulatory monitoring data.
"""

from __future__ import annotations

import math
from typing import Dict, Optional

import numpy as np


class CircadianModel:
    """Circadian heart-rate and activity modulator.

    Parameters:
        base_hr:      Baseline resting heart rate (bpm) around which modulation
                      is applied.
        amplitude:    Peak-to-trough heart-rate swing in bpm (default 15).
        phase_shift:  Hours by which the cosine peak is shifted from midnight
                      (default 3.0, placing the nadir at ~03:00).
        rng:          NumPy random generator for stochastic variation.
    """

    def __init__(
        self,
        base_hr: float,
        amplitude: float = 15.0,
        phase_shift: float = 3.0,
        rng: Optional[np.random.Generator] = None,
    ) -> None:
        self._base_hr: float = base_hr
        self._amplitude: float = amplitude
        self._phase_shift: float = phase_shift
        self._rng: np.random.Generator = rng if rng is not None else np.random.default_rng()

    @property
    def base_hr(self) -> float:
        return self._base_hr

    @property
    def amplitude(self) -> float:
        return self._amplitude

    def get_hr_modifier(self, time_of_day_hours: float) -> float:
        """Return a multiplicative heart-rate modifier for the given time of day.

        The modifier oscillates between approximately 0.8 and 1.2 over 24 hours:
        - Nadir (~0.8x) at approximately 03:00.
        - Peak (~1.2x) at approximately 14:00-15:00.

        A small stochastic term is added to avoid perfect periodicity.

        Parameters:
            time_of_day_hours: Current time as fractional hours (0.0-24.0).

        Returns:
            Multiplicative HR modifier in the range [0.8, 1.2].
        """
        # Cosine model: nadir at phase_shift hours, peak 12 hours later
        # cos(x) = 1 at x=0, so we shift to put the nadir (cos=-1) at phase_shift
        theta = 2.0 * math.pi * (time_of_day_hours - self._phase_shift - 12.0) / 24.0

        # Raw cosine in [-1, 1] -> map to modifier range
        cosine_value = math.cos(theta)

        # Map [-1, 1] to [0.8, 1.2]: midpoint=1.0, half-range=0.2
        half_range = self._amplitude / self._base_hr
        # Clamp half_range to avoid extreme modifiers
        half_range = min(half_range, 0.20)

        modifier = 1.0 + half_range * cosine_value

        # Add small stochastic jitter (SD ~ 0.01)
        jitter = float(self._rng.normal(0.0, 0.01))
        modifier += jitter

        # Clamp to [0.8, 1.2]
        return max(0.8, min(1.2, modifier))

    def get_activity_probability(
        self,
        time_of_day_hours: float,
    ) -> Dict[str, float]:
        """Return the probability of each activity state at the given time of day.

        Activity states: sleep, resting, light, moderate, vigorous.

        The probabilities reflect typical circadian patterns:
        - Sleep is dominant 22:00-06:00.
        - Vigorous activity is most likely 06:00-20:00 with a peak mid-morning.
        - Resting is the baseline awake state.

        Parameters:
            time_of_day_hours: Current time as fractional hours (0.0-24.0).

        Returns:
            Dict mapping activity state names to probabilities that sum to 1.0.
        """
        h = time_of_day_hours % 24.0

        # --- Sleep probability: high 23:00-05:00, transition zones ---
        if h < 5.0:
            p_sleep = 0.85 - 0.05 * h  # 0.85 at midnight, ~0.60 at 5am
        elif h < 7.0:
            # Wake-up transition
            p_sleep = 0.60 - 0.30 * (h - 5.0)  # drops from 0.60 to ~0.0
        elif h < 22.0:
            p_sleep = 0.02  # Nap probability
        elif h < 23.0:
            p_sleep = 0.02 + 0.30 * (h - 22.0)  # rising toward bedtime
        else:
            p_sleep = 0.32 + 0.53 * (h - 23.0)  # 0.32 at 23h -> 0.85 at 24h

        p_sleep = max(0.0, min(1.0, p_sleep))

        awake = 1.0 - p_sleep

        # --- Awake-state distribution (among resting, light, moderate, vigorous) ---
        # Morning peak activity (07:00-10:00), afternoon dip, evening decline
        if 6.0 <= h < 10.0:
            p_vigorous_given_awake = 0.08
            p_moderate_given_awake = 0.15
            p_light_given_awake = 0.35
            p_resting_given_awake = 0.42
        elif 10.0 <= h < 14.0:
            p_vigorous_given_awake = 0.05
            p_moderate_given_awake = 0.12
            p_light_given_awake = 0.30
            p_resting_given_awake = 0.53
        elif 14.0 <= h < 17.0:
            # Afternoon: moderate activity possible (work, errands)
            p_vigorous_given_awake = 0.06
            p_moderate_given_awake = 0.14
            p_light_given_awake = 0.35
            p_resting_given_awake = 0.45
        elif 17.0 <= h < 20.0:
            # Evening exercise window
            p_vigorous_given_awake = 0.07
            p_moderate_given_awake = 0.13
            p_light_given_awake = 0.30
            p_resting_given_awake = 0.50
        elif 20.0 <= h < 22.0:
            # Wind down
            p_vigorous_given_awake = 0.01
            p_moderate_given_awake = 0.05
            p_light_given_awake = 0.25
            p_resting_given_awake = 0.69
        else:
            # Night / early morning: mostly resting if awake
            p_vigorous_given_awake = 0.00
            p_moderate_given_awake = 0.02
            p_light_given_awake = 0.10
            p_resting_given_awake = 0.88

        # Scale by awake probability
        p_resting = awake * p_resting_given_awake
        p_light = awake * p_light_given_awake
        p_moderate = awake * p_moderate_given_awake
        p_vigorous = awake * p_vigorous_given_awake

        # Normalize to exactly 1.0
        total = p_sleep + p_resting + p_light + p_moderate + p_vigorous
        if total > 0:
            p_sleep /= total
            p_resting /= total
            p_light /= total
            p_moderate /= total
            p_vigorous /= total
        else:
            p_resting = 1.0

        return {
            "sleep": p_sleep,
            "resting": p_resting,
            "light": p_light,
            "moderate": p_moderate,
            "vigorous": p_vigorous,
        }
