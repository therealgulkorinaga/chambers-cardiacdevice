"""AV conduction model for cardiac rhythm simulation.

Simulates atrioventricular conduction with configurable block types including
normal conduction, first-degree block, Mobitz type I (Wenckebach), Mobitz type II,
and complete (third-degree) heart block.
"""

from __future__ import annotations

import math
from typing import Literal, Optional

import numpy as np


class ConductionModel:
    """Model of atrioventricular (AV) conduction.

    Determines the ventricular activation time for each atrial depolarization
    event, accounting for conduction delay, PR prolongation patterns, dropped
    beats, and independent escape rhythms.

    Parameters:
        pr_interval_ms: Baseline PR interval in milliseconds. Normal range is
            120-200 ms; first-degree block uses values > 200 ms.
        av_block_type: Type of AV conduction to simulate.
        rng: NumPy random generator for stochastic elements. If ``None``, a
            default generator is created.
    """

    VALID_BLOCK_TYPES = ("normal", "first_degree", "mobitz_i", "mobitz_ii", "complete")

    def __init__(
        self,
        pr_interval_ms: float = 160.0,
        av_block_type: Literal[
            "normal", "first_degree", "mobitz_i", "mobitz_ii", "complete"
        ] = "normal",
        rng: Optional[np.random.Generator] = None,
    ) -> None:
        if av_block_type not in self.VALID_BLOCK_TYPES:
            raise ValueError(
                f"av_block_type must be one of {self.VALID_BLOCK_TYPES!r}, "
                f"got {av_block_type!r}"
            )

        self._base_pr_ms: float = pr_interval_ms
        self._block_type: str = av_block_type
        self._rng: np.random.Generator = rng if rng is not None else np.random.default_rng()

        # --- Mobitz I (Wenckebach) state ---
        # Classic Wenckebach cycle length: typically 3:2, 4:3, or 5:4 conduction
        self._wenckebach_cycle_length: int = int(self._rng.choice([3, 4, 5], p=[0.3, 0.5, 0.2]))
        self._wenckebach_beat_index: int = 0
        # PR increment decreases with each successive beat in the cycle
        self._wenckebach_max_increment_ms: float = 60.0

        # --- Mobitz II state ---
        # Conduction ratio: e.g., 3:1 means every 3rd P wave is dropped
        self._mobitz_ii_conduction_ratio: int = int(self._rng.choice([2, 3, 4], p=[0.5, 0.35, 0.15]))
        self._mobitz_ii_beat_counter: int = 0

        # --- Complete block state ---
        # Independent ventricular escape rhythm (junctional 40-60 bpm or ventricular 20-40 bpm)
        self._escape_rate_bpm: float = float(self._rng.uniform(30.0, 50.0))
        self._escape_interval_ms: float = 60_000.0 / self._escape_rate_bpm
        self._last_escape_time_ms: float = 0.0
        self._escape_variability_ms: float = 50.0  # SDNN of escape rhythm

        # --- First degree state ---
        # PR interval is prolonged but fixed
        if av_block_type == "first_degree":
            # Ensure PR > 200 ms for first-degree block
            self._base_pr_ms = max(pr_interval_ms, 220.0)

    @property
    def block_type(self) -> str:
        """Return the current AV block type."""
        return self._block_type

    @property
    def base_pr_interval_ms(self) -> float:
        """Return the baseline PR interval in milliseconds."""
        return self._base_pr_ms

    def conduct(self, atrial_event_time_ms: float) -> Optional[float]:
        """Determine the ventricular activation time for an atrial event.

        Parameters:
            atrial_event_time_ms: Timestamp of the atrial depolarization in ms.

        Returns:
            The ventricular activation time in ms, or ``None`` if the beat is
            dropped (non-conducted P wave).
        """
        if self._block_type == "normal":
            return self._conduct_normal(atrial_event_time_ms)
        elif self._block_type == "first_degree":
            return self._conduct_first_degree(atrial_event_time_ms)
        elif self._block_type == "mobitz_i":
            return self._conduct_mobitz_i(atrial_event_time_ms)
        elif self._block_type == "mobitz_ii":
            return self._conduct_mobitz_ii(atrial_event_time_ms)
        elif self._block_type == "complete":
            return self._conduct_complete(atrial_event_time_ms)
        else:
            raise ValueError(f"Unknown block type: {self._block_type!r}")

    def reset(self) -> None:
        """Reset internal counters (e.g., Wenckebach cycle position)."""
        self._wenckebach_beat_index = 0
        self._mobitz_ii_beat_counter = 0
        self._last_escape_time_ms = 0.0

    # ------------------------------------------------------------------
    # Private conduction methods
    # ------------------------------------------------------------------

    def _conduct_normal(self, atrial_event_time_ms: float) -> float:
        """Normal AV conduction: fixed PR interval with small physiological jitter."""
        jitter = float(self._rng.normal(0.0, 5.0))
        pr = self._base_pr_ms + jitter
        return atrial_event_time_ms + max(pr, 80.0)

    def _conduct_first_degree(self, atrial_event_time_ms: float) -> float:
        """First-degree AV block: prolonged but consistent PR interval (>200 ms)."""
        jitter = float(self._rng.normal(0.0, 5.0))
        pr = self._base_pr_ms + jitter
        return atrial_event_time_ms + max(pr, 200.0)

    def _conduct_mobitz_i(self, atrial_event_time_ms: float) -> Optional[float]:
        """Mobitz type I (Wenckebach) conduction.

        The PR interval progressively prolongs with each beat in the cycle until
        one P wave is completely blocked (dropped beat).  The increment in PR
        shortening is greatest in the second beat and decreases thereafter,
        creating the classic "grouped beating" pattern.

        Typical pattern for a 4:3 Wenckebach:
            Beat 1: PR = base (e.g., 180 ms)
            Beat 2: PR = base + 40 ms (largest increment)
            Beat 3: PR = base + 60 ms (increment of 20 ms, decreasing)
            Beat 4: dropped (non-conducted)
        """
        cycle_len = self._wenckebach_cycle_length
        beat_in_cycle = self._wenckebach_beat_index % cycle_len

        self._wenckebach_beat_index += 1

        # Last beat in the cycle is dropped
        if beat_in_cycle == cycle_len - 1:
            return None

        # Calculate progressive PR prolongation
        # The increment decreases with each successive conducted beat
        if beat_in_cycle == 0:
            pr_prolongation = 0.0
        else:
            # Cumulative prolongation: sum of decreasing increments
            # Increment_i = max_increment / i (harmonic series produces decreasing increments)
            pr_prolongation = 0.0
            for i in range(1, beat_in_cycle + 1):
                increment = self._wenckebach_max_increment_ms / i
                pr_prolongation += increment

        pr = self._base_pr_ms + pr_prolongation
        jitter = float(self._rng.normal(0.0, 3.0))
        pr += jitter

        return atrial_event_time_ms + max(pr, 80.0)

    def _conduct_mobitz_ii(self, atrial_event_time_ms: float) -> Optional[float]:
        """Mobitz type II conduction.

        The PR interval remains constant for conducted beats, but intermittent
        complete failure of conduction occurs at a fixed ratio (e.g., 2:1, 3:1).
        """
        self._mobitz_ii_beat_counter += 1

        # Drop every Nth beat
        if self._mobitz_ii_beat_counter % self._mobitz_ii_conduction_ratio == 0:
            return None

        # Conducted beat: constant PR interval (this is the hallmark of Mobitz II)
        jitter = float(self._rng.normal(0.0, 2.0))  # Very small jitter
        pr = self._base_pr_ms + jitter
        return atrial_event_time_ms + max(pr, 80.0)

    def _conduct_complete(self, atrial_event_time_ms: float) -> Optional[float]:
        """Complete (third-degree) heart block.

        Atrial and ventricular rhythms are completely dissociated.  The ventricles
        beat at an independent escape rate.  The returned ventricular time is the
        next escape beat that falls after the atrial event.

        Returns:
            The time of the next ventricular escape beat, or ``None`` if the
            escape beat falls before the atrial event (indicating that the
            atrial event does not trigger any new ventricular activity).
        """
        # Calculate the next escape beat time
        jitter = float(self._rng.normal(0.0, self._escape_variability_ms))
        current_interval = max(self._escape_interval_ms + jitter, 300.0)

        # Advance escape timer to at least the atrial event time
        while self._last_escape_time_ms + current_interval < atrial_event_time_ms:
            self._last_escape_time_ms += current_interval
            jitter = float(self._rng.normal(0.0, self._escape_variability_ms))
            current_interval = max(self._escape_interval_ms + jitter, 300.0)

        # The next escape beat
        next_escape = self._last_escape_time_ms + current_interval
        self._last_escape_time_ms = next_escape

        return next_escape

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def get_wenckebach_state(self) -> dict[str, int]:
        """Return Wenckebach cycle information (for logging/display)."""
        return {
            "cycle_length": self._wenckebach_cycle_length,
            "current_beat_in_cycle": self._wenckebach_beat_index % self._wenckebach_cycle_length,
        }

    def get_mobitz_ii_state(self) -> dict[str, int]:
        """Return Mobitz II conduction ratio information."""
        return {
            "conduction_ratio": self._mobitz_ii_conduction_ratio,
            "beat_counter": self._mobitz_ii_beat_counter,
        }

    def get_escape_rate_bpm(self) -> float:
        """Return the ventricular escape rate for complete block."""
        return self._escape_rate_bpm
