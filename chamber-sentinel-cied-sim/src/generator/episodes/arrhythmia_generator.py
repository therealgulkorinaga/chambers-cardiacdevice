"""
Stochastic arrhythmia episode generator.

Generates realistic arrhythmia episodes (AF, AFL, SVT, VT, VF, PVC, PAC)
using clinically-motivated Poisson arrival processes and duration
distributions.  Episode parameters are tunable through patient-level risk
factors.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.random import Generator


# ---------------------------------------------------------------------------
# Public data-model types
# ---------------------------------------------------------------------------


@dataclass
class ArrhythmiaEpisode:
    """A single arrhythmia episode with full metadata."""

    episode_id: str
    episode_type: str  # 'AF', 'AFL', 'SVT', 'VT', 'VF', 'PVC', 'PAC'
    onset_time_s: float
    duration_s: float
    max_rate_bpm: float
    terminated_by: str  # 'spontaneous', 'atp', 'shock', 'ongoing'
    is_sustained: bool
    morphology: str  # 'monomorphic', 'polymorphic'


# ---------------------------------------------------------------------------
# Rate/duration profiles per arrhythmia type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ArrhythmiaProfile:
    """Internal profile defining rate and duration distributions."""

    rate_range_bpm: tuple[float, float]
    sustained_threshold_s: float
    default_morphology: str
    default_termination: str


_PROFILES: dict[str, _ArrhythmiaProfile] = {
    "AF": _ArrhythmiaProfile(
        rate_range_bpm=(100.0, 180.0),
        sustained_threshold_s=30.0,
        default_morphology="polymorphic",
        default_termination="spontaneous",
    ),
    "AFL": _ArrhythmiaProfile(
        rate_range_bpm=(140.0, 160.0),
        sustained_threshold_s=30.0,
        default_morphology="monomorphic",
        default_termination="spontaneous",
    ),
    "SVT": _ArrhythmiaProfile(
        rate_range_bpm=(150.0, 230.0),
        sustained_threshold_s=30.0,
        default_morphology="monomorphic",
        default_termination="spontaneous",
    ),
    "VT": _ArrhythmiaProfile(
        rate_range_bpm=(140.0, 250.0),
        sustained_threshold_s=30.0,
        default_morphology="monomorphic",
        default_termination="atp",
    ),
    "VF": _ArrhythmiaProfile(
        rate_range_bpm=(250.0, 400.0),
        sustained_threshold_s=10.0,
        default_morphology="polymorphic",
        default_termination="shock",
    ),
    "PVC": _ArrhythmiaProfile(
        rate_range_bpm=(0.0, 0.0),  # single beats; rate not applicable
        sustained_threshold_s=0.0,
        default_morphology="monomorphic",
        default_termination="spontaneous",
    ),
    "PAC": _ArrhythmiaProfile(
        rate_range_bpm=(0.0, 0.0),
        sustained_threshold_s=0.0,
        default_morphology="monomorphic",
        default_termination="spontaneous",
    ),
}


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class ArrhythmiaGenerator:
    """
    Stochastic generator that produces arrhythmia episodes over a given
    simulation window based on patient-specific risk factors.

    Parameters
    ----------
    af_burden:
        Fraction of time in AF (0.0-1.0). Drives Poisson rate for AF
        episodes.  0.0 = no AF, 1.0 = permanent AF.
    vt_risk:
        VT risk factor (0.0-1.0). Drives Poisson rate for VT/VF episodes.
    pvc_burden:
        Fraction of beats that are PVCs (0.0-0.30 typical).
    rng:
        NumPy random generator for reproducibility.
    """

    def __init__(
        self,
        af_burden: float = 0.0,
        vt_risk: float = 0.0,
        pvc_burden: float = 0.0,
        rng: Generator | None = None,
    ) -> None:
        self._af_burden = float(np.clip(af_burden, 0.0, 1.0))
        self._vt_risk = float(np.clip(vt_risk, 0.0, 1.0))
        self._pvc_burden = float(np.clip(pvc_burden, 0.0, 0.30))
        self._rng: Generator = rng if rng is not None else np.random.default_rng()

    # -- Internal Poisson helpers ------------------------------------------

    def _poisson_arrivals(
        self,
        rate_per_hour: float,
        duration_hours: float,
        time_offset_s: float,
    ) -> list[float]:
        """
        Generate Poisson-process arrival times (in seconds) over the window
        ``[time_offset_s, time_offset_s + duration_hours * 3600)``.
        """
        if rate_per_hour <= 0:
            return []

        expected_count = rate_per_hour * duration_hours
        n_events = self._rng.poisson(expected_count)
        if n_events == 0:
            return []

        # Uniform arrival times within the window, then sorted
        arrivals = self._rng.uniform(
            time_offset_s,
            time_offset_s + duration_hours * 3600.0,
            size=int(n_events),
        )
        arrivals.sort()
        return arrivals.tolist()

    # -- Episode factories -------------------------------------------------

    def _generate_af_episodes(
        self,
        duration_hours: float,
        time_offset_s: float,
    ) -> list[ArrhythmiaEpisode]:
        """
        AF episodes: Poisson(lambda=af_burden*2) per hour,
        LogNormal(mu=4.5, sigma=1.2) *minutes* duration.
        """
        rate = self._af_burden * 2.0  # episodes per hour
        arrivals = self._poisson_arrivals(rate, duration_hours, time_offset_s)

        episodes: list[ArrhythmiaEpisode] = []
        profile = _PROFILES["AF"]

        for onset in arrivals:
            duration_min = float(self._rng.lognormal(mean=4.5, sigma=1.2))
            duration_s = duration_min * 60.0
            # Cap duration so episodes don't exceed the window
            max_duration = (time_offset_s + duration_hours * 3600.0) - onset
            duration_s = min(duration_s, max(0.0, max_duration))

            max_rate = float(self._rng.uniform(*profile.rate_range_bpm))
            is_sustained = duration_s >= profile.sustained_threshold_s

            episodes.append(
                ArrhythmiaEpisode(
                    episode_id=uuid.uuid4().hex[:12],
                    episode_type="AF",
                    onset_time_s=onset,
                    duration_s=round(duration_s, 2),
                    max_rate_bpm=round(max_rate, 1),
                    terminated_by=profile.default_termination,
                    is_sustained=is_sustained,
                    morphology=profile.default_morphology,
                )
            )

        # Occasionally generate AFL as well (30 % of AF episodes become AFL)
        afl_episodes: list[ArrhythmiaEpisode] = []
        for ep in episodes:
            if self._rng.random() < 0.30:
                afl_profile = _PROFILES["AFL"]
                afl_episodes.append(
                    ArrhythmiaEpisode(
                        episode_id=uuid.uuid4().hex[:12],
                        episode_type="AFL",
                        onset_time_s=ep.onset_time_s + ep.duration_s + self._rng.uniform(1, 60),
                        duration_s=round(ep.duration_s * self._rng.uniform(0.3, 0.8), 2),
                        max_rate_bpm=round(float(self._rng.uniform(*afl_profile.rate_range_bpm)), 1),
                        terminated_by=afl_profile.default_termination,
                        is_sustained=True,
                        morphology=afl_profile.default_morphology,
                    )
                )

        episodes.extend(afl_episodes)
        return episodes

    def _generate_svt_episodes(
        self,
        duration_hours: float,
        time_offset_s: float,
    ) -> list[ArrhythmiaEpisode]:
        """SVT episodes at a low background rate proportional to AF burden."""
        rate = self._af_burden * 0.3  # less common than AF
        arrivals = self._poisson_arrivals(rate, duration_hours, time_offset_s)
        profile = _PROFILES["SVT"]
        episodes: list[ArrhythmiaEpisode] = []

        for onset in arrivals:
            duration_s = float(self._rng.exponential(120.0))  # mean 2 minutes
            max_duration = (time_offset_s + duration_hours * 3600.0) - onset
            duration_s = min(duration_s, max(0.0, max_duration))
            max_rate = float(self._rng.uniform(*profile.rate_range_bpm))

            episodes.append(
                ArrhythmiaEpisode(
                    episode_id=uuid.uuid4().hex[:12],
                    episode_type="SVT",
                    onset_time_s=onset,
                    duration_s=round(duration_s, 2),
                    max_rate_bpm=round(max_rate, 1),
                    terminated_by=profile.default_termination,
                    is_sustained=duration_s >= profile.sustained_threshold_s,
                    morphology=profile.default_morphology,
                )
            )

        return episodes

    def _generate_vt_episodes(
        self,
        duration_hours: float,
        time_offset_s: float,
    ) -> list[ArrhythmiaEpisode]:
        """
        VT episodes: Poisson(lambda=vt_risk*0.01) per hour,
        Exponential(lambda=0.05) seconds duration.
        """
        rate = self._vt_risk * 0.01  # episodes per hour
        arrivals = self._poisson_arrivals(rate, duration_hours, time_offset_s)
        profile = _PROFILES["VT"]
        episodes: list[ArrhythmiaEpisode] = []

        for onset in arrivals:
            duration_s = float(self._rng.exponential(1.0 / 0.05))  # mean 20s
            max_duration = (time_offset_s + duration_hours * 3600.0) - onset
            duration_s = min(duration_s, max(0.0, max_duration))
            max_rate = float(self._rng.uniform(*profile.rate_range_bpm))
            is_sustained = duration_s >= profile.sustained_threshold_s

            # 80 % monomorphic, 20 % polymorphic
            morphology = "monomorphic" if self._rng.random() < 0.8 else "polymorphic"

            # Sustained VT may need ATP or shock; non-sustained terminates spontaneously
            if is_sustained:
                termination = "atp" if self._rng.random() < 0.7 else "shock"
            else:
                termination = "spontaneous"

            episodes.append(
                ArrhythmiaEpisode(
                    episode_id=uuid.uuid4().hex[:12],
                    episode_type="VT",
                    onset_time_s=onset,
                    duration_s=round(duration_s, 2),
                    max_rate_bpm=round(max_rate, 1),
                    terminated_by=termination,
                    is_sustained=is_sustained,
                    morphology=morphology,
                )
            )

        return episodes

    def _generate_vf_episodes(
        self,
        duration_hours: float,
        time_offset_s: float,
    ) -> list[ArrhythmiaEpisode]:
        """VF episodes: rare, derived from VT risk at 1/10 the rate."""
        rate = self._vt_risk * 0.001  # very rare
        arrivals = self._poisson_arrivals(rate, duration_hours, time_offset_s)
        profile = _PROFILES["VF"]
        episodes: list[ArrhythmiaEpisode] = []

        for onset in arrivals:
            duration_s = float(self._rng.exponential(8.0))  # mean 8s
            max_duration = (time_offset_s + duration_hours * 3600.0) - onset
            duration_s = min(duration_s, max(0.0, max_duration))

            episodes.append(
                ArrhythmiaEpisode(
                    episode_id=uuid.uuid4().hex[:12],
                    episode_type="VF",
                    onset_time_s=onset,
                    duration_s=round(duration_s, 2),
                    max_rate_bpm=round(float(self._rng.uniform(*profile.rate_range_bpm)), 1),
                    terminated_by="shock",
                    is_sustained=True,
                    morphology=profile.default_morphology,
                )
            )

        return episodes

    def _generate_pvc_episodes(
        self,
        duration_hours: float,
        time_offset_s: float,
    ) -> list[ArrhythmiaEpisode]:
        """
        PVC episodes: Poisson(lambda=pvc_burden*1000/24) per hour.
        Each PVC is a single premature beat with coupling interval 350-500 ms.
        """
        rate = self._pvc_burden * 1000.0 / 24.0  # PVCs per hour
        arrivals = self._poisson_arrivals(rate, duration_hours, time_offset_s)
        episodes: list[ArrhythmiaEpisode] = []

        for onset in arrivals:
            coupling_ms = float(self._rng.uniform(350.0, 500.0))

            episodes.append(
                ArrhythmiaEpisode(
                    episode_id=uuid.uuid4().hex[:12],
                    episode_type="PVC",
                    onset_time_s=onset,
                    duration_s=round(coupling_ms / 1000.0, 3),
                    max_rate_bpm=0.0,  # single beat
                    terminated_by="spontaneous",
                    is_sustained=False,
                    morphology="monomorphic" if self._rng.random() < 0.85 else "polymorphic",
                )
            )

        return episodes

    def _generate_pac_episodes(
        self,
        duration_hours: float,
        time_offset_s: float,
    ) -> list[ArrhythmiaEpisode]:
        """
        PAC episodes: proportional to AF burden (atrial ectopy is a
        precursor).  Rate ~ af_burden * 500 / 24 per hour.
        """
        rate = self._af_burden * 500.0 / 24.0
        arrivals = self._poisson_arrivals(rate, duration_hours, time_offset_s)
        episodes: list[ArrhythmiaEpisode] = []

        for onset in arrivals:
            coupling_ms = float(self._rng.uniform(300.0, 450.0))
            episodes.append(
                ArrhythmiaEpisode(
                    episode_id=uuid.uuid4().hex[:12],
                    episode_type="PAC",
                    onset_time_s=onset,
                    duration_s=round(coupling_ms / 1000.0, 3),
                    max_rate_bpm=0.0,
                    terminated_by="spontaneous",
                    is_sustained=False,
                    morphology="monomorphic",
                )
            )

        return episodes

    # -- Public API --------------------------------------------------------

    def generate_episodes(
        self,
        duration_hours: float,
        time_offset_s: float = 0.0,
    ) -> list[ArrhythmiaEpisode]:
        """
        Generate all arrhythmia episodes over the given time window.

        Parameters
        ----------
        duration_hours:
            Length of the simulation window in hours.
        time_offset_s:
            Absolute start time of the window in seconds.

        Returns
        -------
        list[ArrhythmiaEpisode]
            All generated episodes sorted by onset time.
        """
        episodes: list[ArrhythmiaEpisode] = []

        episodes.extend(self._generate_af_episodes(duration_hours, time_offset_s))
        episodes.extend(self._generate_svt_episodes(duration_hours, time_offset_s))
        episodes.extend(self._generate_vt_episodes(duration_hours, time_offset_s))
        episodes.extend(self._generate_vf_episodes(duration_hours, time_offset_s))
        episodes.extend(self._generate_pvc_episodes(duration_hours, time_offset_s))
        episodes.extend(self._generate_pac_episodes(duration_hours, time_offset_s))

        # Sort by onset time
        episodes.sort(key=lambda e: e.onset_time_s)
        return episodes
