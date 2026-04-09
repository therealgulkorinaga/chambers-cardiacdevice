"""Markov-chain-based cardiac rhythm state machine.

Implements a clinically plausible rhythm state machine that transitions between
cardiac rhythm states according to configurable Markov transition probabilities.
Heart rate and RR interval variability are modeled per rhythm type.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np


class RhythmState(enum.Enum):
    """Enumeration of all supported cardiac rhythm states."""

    NSR = "normal_sinus_rhythm"
    SINUS_BRADYCARDIA = "sinus_bradycardia"
    SINUS_TACHYCARDIA = "sinus_tachycardia"
    ATRIAL_FIBRILLATION = "atrial_fibrillation"
    ATRIAL_FLUTTER = "atrial_flutter"
    SVT = "supraventricular_tachycardia"
    VENTRICULAR_TACHYCARDIA = "ventricular_tachycardia"
    VENTRICULAR_FIBRILLATION = "ventricular_fibrillation"
    COMPLETE_HEART_BLOCK = "complete_heart_block"
    MOBITZ_I = "mobitz_type_i"
    MOBITZ_II = "mobitz_type_ii"
    PVC = "premature_ventricular_complex"
    PAC = "premature_atrial_complex"
    JUNCTIONAL = "junctional_rhythm"
    PACED_AAI = "paced_aai"
    PACED_VVI = "paced_vvi"
    PACED_DDD = "paced_ddd"
    PACED_CRT = "paced_crt"


@dataclass
class RhythmContext:
    """Contextual information influencing rhythm transitions and heart rate.

    Attributes:
        time_of_day_hours: Current time expressed as fractional hours (0.0 - 24.0).
        activity_level: Patient activity intensity, normalized 0.0 (rest) to 1.0 (peak).
        medications: Mapping of medication names to dosage or boolean presence.
        patient_age: Patient age in years.
    """

    time_of_day_hours: float = 12.0
    activity_level: float = 0.0
    medications: Dict[str, float] = field(default_factory=dict)
    patient_age: int = 65


# ---------------------------------------------------------------------------
# Heart rate ranges per rhythm state: (min_bpm, max_bpm)
# ---------------------------------------------------------------------------
HR_RANGES: Dict[RhythmState, tuple[float, float]] = {
    RhythmState.NSR: (60.0, 100.0),
    RhythmState.SINUS_BRADYCARDIA: (30.0, 59.0),
    RhythmState.SINUS_TACHYCARDIA: (101.0, 150.0),
    RhythmState.ATRIAL_FIBRILLATION: (80.0, 180.0),
    RhythmState.ATRIAL_FLUTTER: (130.0, 170.0),
    RhythmState.SVT: (150.0, 250.0),
    RhythmState.VENTRICULAR_TACHYCARDIA: (120.0, 250.0),
    RhythmState.VENTRICULAR_FIBRILLATION: (200.0, 400.0),
    RhythmState.COMPLETE_HEART_BLOCK: (20.0, 45.0),
    RhythmState.MOBITZ_I: (50.0, 100.0),
    RhythmState.MOBITZ_II: (40.0, 80.0),
    RhythmState.PVC: (60.0, 100.0),  # Underlying rate; PVC is a single-beat event
    RhythmState.PAC: (60.0, 100.0),  # Underlying rate; PAC is a single-beat event
    RhythmState.JUNCTIONAL: (40.0, 60.0),
    RhythmState.PACED_AAI: (60.0, 130.0),
    RhythmState.PACED_VVI: (60.0, 130.0),
    RhythmState.PACED_DDD: (60.0, 130.0),
    RhythmState.PACED_CRT: (60.0, 130.0),
}


# ---------------------------------------------------------------------------
# Default clinically plausible transition matrix (rates per hour)
# ---------------------------------------------------------------------------

def _build_default_transition_matrix() -> Dict[RhythmState, Dict[RhythmState, float]]:
    """Construct the default Markov transition-rate matrix.

    Rates are expressed as probability per hour.  During each simulation step the
    engine converts these to per-step probabilities using ``p = 1 - exp(-rate * dt)``.
    Only non-zero transitions are listed; all omitted pairs are implicitly 0.
    """

    matrix: Dict[RhythmState, Dict[RhythmState, float]] = {state: {} for state in RhythmState}

    # --- From NSR ---
    matrix[RhythmState.NSR] = {
        RhythmState.ATRIAL_FIBRILLATION: 0.001,
        RhythmState.SVT: 0.0005,
        RhythmState.VENTRICULAR_TACHYCARDIA: 0.0001,
        RhythmState.SINUS_BRADYCARDIA: 0.002,
        RhythmState.SINUS_TACHYCARDIA: 0.002,
        RhythmState.PVC: 0.01,
        RhythmState.PAC: 0.008,
        RhythmState.ATRIAL_FLUTTER: 0.0003,
        RhythmState.MOBITZ_I: 0.0002,
        RhythmState.MOBITZ_II: 0.0001,
        RhythmState.JUNCTIONAL: 0.0002,
    }

    # --- From AF ---
    matrix[RhythmState.ATRIAL_FIBRILLATION] = {
        RhythmState.NSR: 0.01,
        RhythmState.VENTRICULAR_TACHYCARDIA: 0.0002,
        RhythmState.ATRIAL_FLUTTER: 0.002,
        RhythmState.SVT: 0.001,
    }

    # --- From Atrial Flutter ---
    matrix[RhythmState.ATRIAL_FLUTTER] = {
        RhythmState.NSR: 0.02,
        RhythmState.ATRIAL_FIBRILLATION: 0.01,
    }

    # --- From SVT ---
    matrix[RhythmState.SVT] = {
        RhythmState.NSR: 0.5,
        RhythmState.ATRIAL_FIBRILLATION: 0.01,
    }

    # --- From VT ---
    matrix[RhythmState.VENTRICULAR_TACHYCARDIA] = {
        RhythmState.VENTRICULAR_FIBRILLATION: 0.1,
        RhythmState.NSR: 0.3,
    }

    # --- From VF ---
    matrix[RhythmState.VENTRICULAR_FIBRILLATION] = {
        RhythmState.NSR: 0.05,  # only with intervention (defibrillation)
        RhythmState.VENTRICULAR_TACHYCARDIA: 0.02,
    }

    # --- From Sinus Bradycardia ---
    matrix[RhythmState.SINUS_BRADYCARDIA] = {
        RhythmState.NSR: 0.1,
        RhythmState.JUNCTIONAL: 0.005,
        RhythmState.COMPLETE_HEART_BLOCK: 0.001,
    }

    # --- From Sinus Tachycardia ---
    matrix[RhythmState.SINUS_TACHYCARDIA] = {
        RhythmState.NSR: 0.1,
        RhythmState.SVT: 0.005,
        RhythmState.ATRIAL_FIBRILLATION: 0.002,
    }

    # --- From Complete Heart Block ---
    matrix[RhythmState.COMPLETE_HEART_BLOCK] = {
        RhythmState.NSR: 0.005,
        RhythmState.PACED_VVI: 0.05,
        RhythmState.VENTRICULAR_TACHYCARDIA: 0.002,
    }

    # --- From Mobitz I ---
    matrix[RhythmState.MOBITZ_I] = {
        RhythmState.NSR: 0.05,
        RhythmState.MOBITZ_II: 0.005,
    }

    # --- From Mobitz II ---
    matrix[RhythmState.MOBITZ_II] = {
        RhythmState.COMPLETE_HEART_BLOCK: 0.01,
        RhythmState.NSR: 0.02,
        RhythmState.PACED_DDD: 0.03,
    }

    # --- From PVC (single-beat; returns to underlying) ---
    matrix[RhythmState.PVC] = {
        RhythmState.NSR: 5.0,  # High rate: PVC is typically a single beat
        RhythmState.VENTRICULAR_TACHYCARDIA: 0.01,
    }

    # --- From PAC (single-beat; returns to underlying) ---
    matrix[RhythmState.PAC] = {
        RhythmState.NSR: 5.0,
        RhythmState.SVT: 0.005,
        RhythmState.ATRIAL_FIBRILLATION: 0.002,
    }

    # --- From Junctional ---
    matrix[RhythmState.JUNCTIONAL] = {
        RhythmState.NSR: 0.05,
        RhythmState.COMPLETE_HEART_BLOCK: 0.005,
    }

    # --- From Paced rhythms (relatively stable) ---
    matrix[RhythmState.PACED_AAI] = {
        RhythmState.ATRIAL_FIBRILLATION: 0.001,
        RhythmState.NSR: 0.005,
    }

    matrix[RhythmState.PACED_VVI] = {
        RhythmState.NSR: 0.005,
        RhythmState.VENTRICULAR_TACHYCARDIA: 0.0002,
    }

    matrix[RhythmState.PACED_DDD] = {
        RhythmState.NSR: 0.005,
        RhythmState.ATRIAL_FIBRILLATION: 0.001,
    }

    matrix[RhythmState.PACED_CRT] = {
        RhythmState.NSR: 0.003,
        RhythmState.VENTRICULAR_TACHYCARDIA: 0.0003,
    }

    return matrix


DEFAULT_TRANSITION_MATRIX: Dict[RhythmState, Dict[RhythmState, float]] = (
    _build_default_transition_matrix()
)


class RhythmEngine:
    """Markov-chain cardiac rhythm state machine.

    The engine holds a current :class:`RhythmState` and, on each call to
    :meth:`step`, stochastically transitions according to the provided
    transition-rate matrix.  Heart rate and RR-interval variability are
    synthesized to match each rhythm's clinical characteristics.

    Parameters:
        initial_state: The starting rhythm.
        transition_matrix: Per-hour transition rates between states.
        rng: A :class:`numpy.random.Generator` instance for reproducibility.
    """

    def __init__(
        self,
        initial_state: RhythmState,
        transition_matrix: Dict[RhythmState, Dict[RhythmState, float]],
        rng: np.random.Generator,
    ) -> None:
        self._state: RhythmState = initial_state
        self._transition_matrix = transition_matrix
        self._rng: np.random.Generator = rng

        # Internal accumulators
        self._time_in_state_s: float = 0.0
        self._current_hr: float = self._sample_initial_hr(initial_state)
        self._last_rr_ms: float = 60_000.0 / self._current_hr

        # For AF irregular-irregular model
        self._af_rr_history: list[float] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def state(self) -> RhythmState:
        """Return the current rhythm state."""
        return self._state

    def step(self, dt_seconds: float, context: RhythmContext) -> RhythmState:
        """Advance the rhythm engine by *dt_seconds*.

        The method evaluates possible transitions from the current state and,
        if a transition fires, updates internal heart-rate parameters.

        Returns:
            The (possibly new) :class:`RhythmState` after this time step.
        """
        self._time_in_state_s += dt_seconds

        # Apply context-based modifiers to transition rates
        modified_matrix = self._apply_context_modifiers(context)

        # Evaluate transitions
        transitions = modified_matrix.get(self._state, {})
        if transitions:
            new_state = self._evaluate_transitions(transitions, dt_seconds)
            if new_state is not None and new_state != self._state:
                self._transition_to(new_state, context)

        # Update heart rate based on context (circadian, activity)
        self._update_hr(context, dt_seconds)

        return self._state

    def get_heart_rate(self) -> float:
        """Return the instantaneous heart rate in beats per minute."""
        return self._current_hr

    def get_rr_interval_ms(self) -> float:
        """Return the current RR interval in milliseconds with rhythm-appropriate variability."""
        base_rr_ms = 60_000.0 / max(self._current_hr, 1.0)
        noisy_rr = self._apply_rr_variability(base_rr_ms)
        # Clamp to physiological limits
        noisy_rr = max(150.0, min(noisy_rr, 3000.0))
        self._last_rr_ms = noisy_rr
        return noisy_rr

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _sample_initial_hr(self, state: RhythmState) -> float:
        """Pick an initial heart rate uniformly within the state's range."""
        lo, hi = HR_RANGES[state]
        return float(self._rng.uniform(lo, hi))

    def _evaluate_transitions(
        self,
        transitions: Dict[RhythmState, float],
        dt_seconds: float,
    ) -> Optional[RhythmState]:
        """Evaluate competing transition rates and return the winning state, if any.

        Each rate (per hour) is converted to a probability over *dt_seconds*
        using the exponential CDF: ``p = 1 - exp(-rate * dt / 3600)``.
        """
        dt_hours = dt_seconds / 3600.0
        cumulative = 0.0
        roll = float(self._rng.random())

        for target_state, rate_per_hour in transitions.items():
            if rate_per_hour <= 0.0:
                continue
            prob = 1.0 - math.exp(-rate_per_hour * dt_hours)
            cumulative += prob
            if roll < cumulative:
                return target_state

        return None

    def _transition_to(self, new_state: RhythmState, context: RhythmContext) -> None:
        """Handle state transition bookkeeping."""
        self._state = new_state
        self._time_in_state_s = 0.0
        self._current_hr = self._sample_initial_hr(new_state)
        self._af_rr_history.clear()

    def _apply_context_modifiers(
        self, context: RhythmContext
    ) -> Dict[RhythmState, Dict[RhythmState, float]]:
        """Return a modified copy of the transition matrix reflecting patient context.

        Modifiers:
        - High activity increases tachyarrhythmia transition rates.
        - Beta-blocker medication suppresses SVT/AF/VT transitions.
        - Night time (sleep) slightly increases bradycardia transitions.
        - Advanced age increases block/AF rates.
        """
        modified: Dict[RhythmState, Dict[RhythmState, float]] = {}

        # Medication modifiers
        beta_blocker = context.medications.get("beta_blocker", 0.0)
        antiarrhythmic = context.medications.get("antiarrhythmic", 0.0)
        beta_blocker_factor = max(0.1, 1.0 - 0.6 * float(beta_blocker))
        antiarrhythmic_factor = max(0.1, 1.0 - 0.5 * float(antiarrhythmic))

        # Activity modifier: higher activity -> more tachyarrhythmia
        activity_factor = 1.0 + 2.0 * context.activity_level

        # Night modifier: 0-6 hours -> increased bradycardia
        is_night = 0.0 <= context.time_of_day_hours < 6.0
        night_brady_factor = 1.5 if is_night else 1.0
        night_tachy_factor = 0.7 if is_night else 1.0

        # Age modifier: >75 increases conduction disease rates
        age_factor = 1.0 + max(0.0, (context.patient_age - 60)) / 40.0

        tachyarrhythmia_states = {
            RhythmState.SVT,
            RhythmState.VENTRICULAR_TACHYCARDIA,
            RhythmState.VENTRICULAR_FIBRILLATION,
            RhythmState.ATRIAL_FIBRILLATION,
            RhythmState.ATRIAL_FLUTTER,
            RhythmState.SINUS_TACHYCARDIA,
        }

        bradyarrhythmia_states = {
            RhythmState.SINUS_BRADYCARDIA,
            RhythmState.COMPLETE_HEART_BLOCK,
            RhythmState.MOBITZ_I,
            RhythmState.MOBITZ_II,
            RhythmState.JUNCTIONAL,
        }

        for src_state in RhythmState:
            src_transitions = self._transition_matrix.get(src_state, {})
            if not src_transitions:
                modified[src_state] = {}
                continue

            new_transitions: Dict[RhythmState, float] = {}
            for dst_state, base_rate in src_transitions.items():
                rate = base_rate

                if dst_state in tachyarrhythmia_states:
                    rate *= activity_factor * night_tachy_factor
                    rate *= beta_blocker_factor * antiarrhythmic_factor

                if dst_state in bradyarrhythmia_states:
                    rate *= night_brady_factor * age_factor

                # Age factor for AF and conduction blocks
                if dst_state in {
                    RhythmState.ATRIAL_FIBRILLATION,
                    RhythmState.COMPLETE_HEART_BLOCK,
                    RhythmState.MOBITZ_I,
                    RhythmState.MOBITZ_II,
                }:
                    rate *= age_factor

                new_transitions[dst_state] = rate

            modified[src_state] = new_transitions

        return modified

    def _update_hr(self, context: RhythmContext, dt_seconds: float) -> None:
        """Drift heart rate toward a context-appropriate target within the state's range."""
        lo, hi = HR_RANGES[self._state]

        # Target HR influenced by activity level (normalized within the state range)
        activity_offset = context.activity_level * (hi - lo)
        target_hr = lo + activity_offset

        # Smooth exponential approach toward target (time constant ~30s)
        tau = 30.0
        alpha = 1.0 - math.exp(-dt_seconds / tau)
        self._current_hr += alpha * (target_hr - self._current_hr)

        # Add small stochastic drift
        drift_noise = float(self._rng.normal(0.0, 0.5 * math.sqrt(dt_seconds)))
        self._current_hr += drift_noise

        # Clamp to range
        self._current_hr = max(lo, min(hi, self._current_hr))

    def _apply_rr_variability(self, base_rr_ms: float) -> float:
        """Apply rhythm-specific RR interval variability.

        NSR:   Heart-rate variability modeled via Gaussian noise (SDNN ~100 ms).
        AF:    Irregularly irregular with coefficient of variation >15%.
        VT:    Regular with low variance.
        VF:    Completely random intervals within physiological range.
        Others: Moderate Gaussian variability.
        """
        state = self._state

        if state == RhythmState.NSR:
            # SDNN ~ 100 ms (standard deviation of NN intervals)
            sdnn = 100.0
            return float(self._rng.normal(base_rr_ms, sdnn))

        if state == RhythmState.ATRIAL_FIBRILLATION:
            # Irregularly irregular: CV > 15%
            cv = 0.18  # 18% coefficient of variation
            sigma = cv * base_rr_ms
            rr = float(self._rng.normal(base_rr_ms, sigma))
            # AF also has long-short-long sequences
            if len(self._af_rr_history) >= 2:
                last = self._af_rr_history[-1]
                # Tendency for alternation (Lorenz plot clustering)
                correction = 0.1 * (base_rr_ms - last)
                rr += correction
            self._af_rr_history.append(rr)
            if len(self._af_rr_history) > 20:
                self._af_rr_history.pop(0)
            return rr

        if state == RhythmState.VENTRICULAR_TACHYCARDIA:
            # Regular with low variance (SDNN ~ 10 ms)
            return float(self._rng.normal(base_rr_ms, 10.0))

        if state == RhythmState.VENTRICULAR_FIBRILLATION:
            # Completely random intervals
            min_rr = 150.0  # ~400 bpm ceiling
            max_rr = 300.0  # ~200 bpm floor
            return float(self._rng.uniform(min_rr, max_rr))

        if state == RhythmState.ATRIAL_FLUTTER:
            # Relatively regular with discrete ventricular response (2:1, 4:1 conduction)
            conduction_ratios = [2.0, 3.0, 4.0]
            ratio = float(self._rng.choice(conduction_ratios, p=[0.7, 0.2, 0.1]))
            flutter_cycle_ms = 200.0  # ~300 bpm atrial rate
            return flutter_cycle_ms * ratio + float(self._rng.normal(0.0, 5.0))

        if state in {RhythmState.SINUS_BRADYCARDIA, RhythmState.SINUS_TACHYCARDIA}:
            # Moderate HRV (SDNN ~ 80 ms)
            return float(self._rng.normal(base_rr_ms, 80.0))

        if state in {RhythmState.PACED_AAI, RhythmState.PACED_VVI,
                     RhythmState.PACED_DDD, RhythmState.PACED_CRT}:
            # Paced: very regular, minimal jitter (~2 ms)
            return float(self._rng.normal(base_rr_ms, 2.0))

        if state == RhythmState.COMPLETE_HEART_BLOCK:
            # Escape rhythm: fairly regular (SDNN ~ 30 ms)
            return float(self._rng.normal(base_rr_ms, 30.0))

        if state == RhythmState.MOBITZ_I:
            # Wenckebach: progressive prolongation creates grouped beating
            # Small variability on the base interval
            return float(self._rng.normal(base_rr_ms, 40.0))

        if state == RhythmState.MOBITZ_II:
            # Regular with occasional dropped beats handled at conduction level
            return float(self._rng.normal(base_rr_ms, 20.0))

        if state == RhythmState.SVT:
            # Regular, low variance (SDNN ~ 15 ms)
            return float(self._rng.normal(base_rr_ms, 15.0))

        if state == RhythmState.JUNCTIONAL:
            # Regular escape (SDNN ~ 25 ms)
            return float(self._rng.normal(base_rr_ms, 25.0))

        if state in {RhythmState.PVC, RhythmState.PAC}:
            # Single premature beat: coupling interval is shorter than base
            coupling_fraction = float(self._rng.uniform(0.6, 0.8))
            return base_rr_ms * coupling_fraction

        # Fallback: moderate Gaussian noise
        return float(self._rng.normal(base_rr_ms, 50.0))
