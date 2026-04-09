"""
Regulatory compliance scoring engine.

Scores both architectures across three regulatory frameworks:

    **GDPR** (EU General Data Protection Regulation)
      - Storage limitation (Art. 5(1)(e))
      - Data minimisation (Art. 5(1)(c))
      - Right to erasure (Art. 17)
      - Purpose limitation (Art. 5(1)(b))

    **HIPAA** (US Health Insurance Portability and Accountability Act)
      - Minimum necessary standard (45 CFR 164.502(b))

    **MDR** (EU Medical Device Regulation 2017/745)
      - Post-market surveillance capability (Art. 83-86)

Each dimension is scored 0-100 and the results are structured for radar
chart visualisation (Plotly polar charts in the dashboard layer).
"""

from __future__ import annotations

from typing import Any

import numpy as np


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SECONDS_PER_DAY = 86_400.0

# Data types considered "strictly necessary" for each purpose
_CLINICAL_NECESSARY: set[str] = {"iegm", "episode", "therapy", "trends", "device_status"}
_RESEARCH_NECESSARY: set[str] = {"episode", "trends", "activity"}
_MAINTENANCE_NECESSARY: set[str] = {"device_status", "trends"}

# Maximum retention periods (days) considered "proportionate" under GDPR
# storage limitation for each data type.
_PROPORTIONATE_RETENTION_DAYS: dict[str, float] = {
    "iegm": 90.0,
    "episode": 365.0,
    "therapy": 365.0,
    "trends": 730.0,
    "device_status": 180.0,
    "demographics": 365.0,
    "activity": 90.0,
}


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------


class RegulatoryComplianceScorer:
    """Scores regulatory compliance for both architectures.

    Each ``score_*`` method accepts an architecture name (``'current'``
    or ``'chambers'``) and a data-state dict describing the system's
    current state.  The data-state dict should have the following keys:

    - ``volumes``: ``{location: {data_type: bytes}}``
    - ``retention_days``: ``{data_type: float}`` -- actual retention
    - ``copies_count``: ``{data_type: int}`` -- number of independent copies
    - ``purposes``: ``{data_type: list[str]}`` -- declared purposes
    - ``access_controls``: ``{location: list[str]}`` -- who has access
    - ``erasure_capable``: bool -- can data be erased on request?
    - ``burn_window_s``: float (Chambers only)
    - ``patients_count``: int
    - ``total_bytes``: int
    - ``investigation_data_available``: float (0-1) -- fraction available
      for post-market surveillance
    """

    def __init__(self) -> None:
        self._scores: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # GDPR
    # ------------------------------------------------------------------

    def score_gdpr(
        self,
        architecture: str,
        data_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Score GDPR compliance across four dimensions.

        Parameters
        ----------
        architecture:
            ``'current'`` or ``'chambers'``.
        data_state:
            System state dict (see class docstring).

        Returns
        -------
        dict
            ``storage_limitation``, ``data_minimisation``,
            ``right_to_erasure``, ``purpose_limitation`` -- each 0-100.
            Plus ``overall``: unweighted mean.
        """
        storage = self._score_storage_limitation(architecture, data_state)
        minimisation = self._score_data_minimisation(architecture, data_state)
        erasure = self._score_right_to_erasure(architecture, data_state)
        purpose = self._score_purpose_limitation(architecture, data_state)

        overall = float(np.mean([storage, minimisation, erasure, purpose]))

        result = {
            "framework": "GDPR",
            "architecture": architecture,
            "storage_limitation": round(storage, 1),
            "data_minimisation": round(minimisation, 1),
            "right_to_erasure": round(erasure, 1),
            "purpose_limitation": round(purpose, 1),
            "overall": round(overall, 1),
        }
        self._scores.append(result)
        return result

    # ------------------------------------------------------------------
    # HIPAA
    # ------------------------------------------------------------------

    def score_hipaa(
        self,
        architecture: str,
        data_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Score HIPAA minimum-necessary compliance.

        The minimum necessary standard requires that covered entities
        limit PHI disclosures to the minimum needed for the intended
        purpose.

        Parameters
        ----------
        architecture:
            ``'current'`` or ``'chambers'``.
        data_state:
            System state dict.

        Returns
        -------
        dict
            ``minimum_necessary``: 0-100 score.
        """
        volumes: dict[str, dict[str, int]] = data_state.get("volumes", {})
        purposes: dict[str, list[str]] = data_state.get("purposes", {})
        copies_count: dict[str, int] = data_state.get("copies_count", {})
        access_controls: dict[str, list[str]] = data_state.get("access_controls", {})

        # Factors:
        # 1. Are there redundant copies? Fewer = better.
        total_copies = sum(copies_count.values()) if copies_count else 1
        total_types = max(len(copies_count), 1)
        avg_copies = total_copies / total_types

        if architecture == "chambers":
            # Chambers: data is in purpose-bound worlds, each accessible
            # only to authorized actors.  Fewer copies by design.
            copies_score = 100.0 * max(0.0, 1.0 - (avg_copies - 1.0) / 5.0)
        else:
            # Current: data propagates through 5 layers.
            copies_score = 100.0 * max(0.0, 1.0 - (avg_copies - 1.0) / 5.0)

        # 2. Is access properly scoped per location?
        access_score = self._score_access_scoping(architecture, access_controls, volumes)

        # 3. Is only necessary data retained for each purpose?
        necessity_score = self._score_data_necessity(architecture, volumes, purposes)

        minimum_necessary = float(np.mean([copies_score, access_score, necessity_score]))

        result = {
            "framework": "HIPAA",
            "architecture": architecture,
            "minimum_necessary": round(minimum_necessary, 1),
            "copies_score": round(copies_score, 1),
            "access_score": round(access_score, 1),
            "necessity_score": round(necessity_score, 1),
        }
        self._scores.append(result)
        return result

    # ------------------------------------------------------------------
    # MDR
    # ------------------------------------------------------------------

    def score_mdr(
        self,
        architecture: str,
        data_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Score EU MDR post-market surveillance capability.

        MDR Articles 83-86 require manufacturers to maintain a
        post-market surveillance system with access to clinical data
        for safety signal detection.  The tension with Chambers is that
        burning data may reduce PMS capability.

        Parameters
        ----------
        architecture:
            ``'current'`` or ``'chambers'``.
        data_state:
            System state dict.  Must include
            ``investigation_data_available`` (float 0-1).

        Returns
        -------
        dict
            ``post_market_surveillance_capability``: 0-100 score.
        """
        investigation_available = data_state.get("investigation_data_available", 0.0)

        if architecture == "current":
            # Current architecture: all data is retained indefinitely,
            # so PMS capability is essentially 100%.
            volumes = data_state.get("volumes", {})
            total_bytes = sum(
                sum(lv.values()) for lv in volumes.values()
            ) if volumes else data_state.get("total_bytes", 0)

            # Score based on data richness and retention
            if total_bytes > 0:
                pms_score = 95.0  # near-perfect -- everything is kept
            else:
                pms_score = 0.0

        else:
            # Chambers: PMS depends on:
            # 1. Safety investigation hold mechanism effectiveness
            # 2. Research world aggregate data availability
            # 3. Device-retained data
            # 4. Actual investigation data fraction
            hold_effectiveness = data_state.get("hold_effectiveness", 0.9)
            research_data_fraction = data_state.get("research_data_fraction", 0.3)

            # Weight: investigation holds matter most, then research aggregate,
            # then the reported available fraction.
            pms_score = (
                hold_effectiveness * 40.0
                + research_data_fraction * 25.0
                + investigation_available * 35.0
            )
            pms_score = min(100.0, pms_score)

        result = {
            "framework": "MDR",
            "architecture": architecture,
            "post_market_surveillance_capability": round(pms_score, 1),
            "investigation_data_available": round(investigation_available, 4),
        }
        self._scores.append(result)
        return result

    # ------------------------------------------------------------------
    # Radar chart aggregator
    # ------------------------------------------------------------------

    def get_radar_chart_data(
        self,
        current_arch_state: dict[str, Any],
        chambers_arch_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Score both architectures and return radar-chart-ready data.

        Computes all three frameworks for both architectures and
        structures the output for a Plotly polar/radar chart.

        Parameters
        ----------
        current_arch_state:
            Data state dict for the current architecture.
        chambers_arch_state:
            Data state dict for the Chambers architecture.

        Returns
        -------
        dict
            ``dimensions``: list of dimension names,
            ``current``: list of scores (same order),
            ``chambers``: list of scores (same order),
            ``details``: full scoring dicts.
        """
        # Score current arch
        gdpr_cur = self.score_gdpr("current", current_arch_state)
        hipaa_cur = self.score_hipaa("current", current_arch_state)
        mdr_cur = self.score_mdr("current", current_arch_state)

        # Score Chambers arch
        gdpr_ch = self.score_gdpr("chambers", chambers_arch_state)
        hipaa_ch = self.score_hipaa("chambers", chambers_arch_state)
        mdr_ch = self.score_mdr("chambers", chambers_arch_state)

        dimensions = [
            "GDPR Storage Limitation",
            "GDPR Data Minimisation",
            "GDPR Right to Erasure",
            "GDPR Purpose Limitation",
            "HIPAA Minimum Necessary",
            "MDR PMS Capability",
        ]

        current_scores = [
            gdpr_cur["storage_limitation"],
            gdpr_cur["data_minimisation"],
            gdpr_cur["right_to_erasure"],
            gdpr_cur["purpose_limitation"],
            hipaa_cur["minimum_necessary"],
            mdr_cur["post_market_surveillance_capability"],
        ]

        chambers_scores = [
            gdpr_ch["storage_limitation"],
            gdpr_ch["data_minimisation"],
            gdpr_ch["right_to_erasure"],
            gdpr_ch["purpose_limitation"],
            hipaa_ch["minimum_necessary"],
            mdr_ch["post_market_surveillance_capability"],
        ]

        return {
            "dimensions": dimensions,
            "current": current_scores,
            "chambers": chambers_scores,
            "current_mean": round(float(np.mean(current_scores)), 1),
            "chambers_mean": round(float(np.mean(chambers_scores)), 1),
            "details": {
                "current": {
                    "gdpr": gdpr_cur,
                    "hipaa": hipaa_cur,
                    "mdr": mdr_cur,
                },
                "chambers": {
                    "gdpr": gdpr_ch,
                    "hipaa": hipaa_ch,
                    "mdr": mdr_ch,
                },
            },
        }

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_all_scores(self) -> list[dict[str, Any]]:
        """Return all scores computed so far."""
        return list(self._scores)

    # ------------------------------------------------------------------
    # Internal scoring helpers
    # ------------------------------------------------------------------

    def _score_storage_limitation(
        self,
        architecture: str,
        data_state: dict[str, Any],
    ) -> float:
        """GDPR Art. 5(1)(e): storage limitation.

        Data should not be kept longer than necessary.
        """
        retention: dict[str, float] = data_state.get("retention_days", {})

        if architecture == "chambers":
            burn_window_s = data_state.get("burn_window_s", 259_200.0)
            burn_window_days = burn_window_s / _SECONDS_PER_DAY

            # All relay data is burned within the burn window.
            # Score: how well does the burn window align with proportionate retention?
            type_scores: list[float] = []
            for dtype, proportionate_days in _PROPORTIONATE_RETENTION_DAYS.items():
                actual_days = retention.get(dtype, burn_window_days)
                if actual_days <= proportionate_days:
                    type_scores.append(100.0)
                else:
                    # Penalize proportionally to the overshoot
                    overshoot = (actual_days - proportionate_days) / proportionate_days
                    type_scores.append(max(0.0, 100.0 * (1.0 - overshoot)))

            if not type_scores:
                # No explicit retention -- burn window is the bound
                return min(100.0, 100.0 * (90.0 / max(burn_window_days, 1.0)))

            return float(np.mean(type_scores))

        else:
            # Current architecture: indefinite retention by default
            if not retention:
                return 15.0  # Very poor -- no defined limits

            type_scores = []
            for dtype, proportionate_days in _PROPORTIONATE_RETENTION_DAYS.items():
                actual_days = retention.get(dtype, float("inf"))
                if actual_days == float("inf") or actual_days <= 0:
                    type_scores.append(10.0)  # Indefinite = poor
                elif actual_days <= proportionate_days:
                    type_scores.append(100.0)
                else:
                    overshoot = (actual_days - proportionate_days) / proportionate_days
                    type_scores.append(max(0.0, 100.0 * (1.0 - overshoot)))

            return float(np.mean(type_scores)) if type_scores else 15.0

    def _score_data_minimisation(
        self,
        architecture: str,
        data_state: dict[str, Any],
    ) -> float:
        """GDPR Art. 5(1)(c): data minimisation.

        Only data that is adequate, relevant, and limited to what is
        necessary should be processed.
        """
        volumes: dict[str, dict[str, int]] = data_state.get("volumes", {})
        copies_count: dict[str, int] = data_state.get("copies_count", {})

        if architecture == "chambers":
            # Chambers naturally minimises:
            # - Data is purpose-bound (worlds only accept relevant types)
            # - Burn eliminates unneeded data
            # - Relay is transient

            # Penalty for any unnecessary copies
            total_copies = sum(copies_count.values()) if copies_count else 0
            total_types = max(len(copies_count), 1)
            avg_copies = total_copies / total_types if total_types > 0 else 1

            # Each additional copy beyond 1 reduces score
            copy_penalty = max(0.0, (avg_copies - 1.0) * 15.0)
            return max(0.0, 90.0 - copy_penalty)

        else:
            # Current architecture: data is replicated across 5 layers
            total_bytes = 0
            for loc_data in volumes.values():
                total_bytes += sum(loc_data.values())

            # Count unique bytes (across all locations, same data type)
            unique_by_type: dict[str, int] = {}
            for loc_data in volumes.values():
                for dtype, nbytes in loc_data.items():
                    unique_by_type[dtype] = max(unique_by_type.get(dtype, 0), nbytes)
            unique_bytes = sum(unique_by_type.values())

            if total_bytes == 0:
                return 50.0

            # Ratio of unique to total: closer to 1 = more minimal
            minimisation_ratio = unique_bytes / total_bytes
            # Also consider copies count
            total_copies = sum(copies_count.values()) if copies_count else 0
            total_types = max(len(copies_count), 1)
            avg_copies = total_copies / total_types if total_types > 0 else 1
            copy_penalty = max(0.0, (avg_copies - 1.0) * 10.0)

            return max(0.0, min(100.0, minimisation_ratio * 80.0 - copy_penalty))

    def _score_right_to_erasure(
        self,
        architecture: str,
        data_state: dict[str, Any],
    ) -> float:
        """GDPR Art. 17: right to erasure ('right to be forgotten').

        Scores the ability to comply with an erasure request.
        """
        erasure_capable = data_state.get("erasure_capable", False)

        if architecture == "chambers":
            # Chambers: burn-by-default provides inherent erasure.
            # Data can also be actively erased per-patient via the
            # world burn mechanisms.
            base = 85.0
            if erasure_capable:
                base = 95.0
            # Additional credit if the burn window is short
            burn_window_s = data_state.get("burn_window_s", 259_200.0)
            burn_days = burn_window_s / _SECONDS_PER_DAY
            if burn_days <= 3.0:
                base += 5.0
            return min(100.0, base)

        else:
            # Current architecture: erasure requires finding and deleting
            # data across all 5 layers, including aggregate pools that
            # may have de-identified the data beyond traceability.
            if erasure_capable:
                # Can erase but it's complex and error-prone
                return 40.0
            else:
                return 15.0

    def _score_purpose_limitation(
        self,
        architecture: str,
        data_state: dict[str, Any],
    ) -> float:
        """GDPR Art. 5(1)(b): purpose limitation.

        Data should only be used for the specified purpose.
        """
        purposes: dict[str, list[str]] = data_state.get("purposes", {})
        volumes: dict[str, dict[str, int]] = data_state.get("volumes", {})

        if architecture == "chambers":
            # Chambers enforces purpose limitation architecturally:
            # each world only receives data relevant to its purpose.
            # Score based on how well-defined the purposes are.
            if not purposes:
                return 80.0  # Architectural enforcement even without explicit config

            well_defined = 0
            for dtype, purpose_list in purposes.items():
                if len(purpose_list) <= 2:
                    well_defined += 1  # narrow purpose = good

            fraction_well_defined = well_defined / max(len(purposes), 1)
            return min(100.0, 80.0 + fraction_well_defined * 20.0)

        else:
            # Current architecture: data flows through shared layers
            # without purpose-specific segregation.
            if not purposes:
                return 20.0  # No purpose definitions = poor

            # Check if any data type has too many purposes (scope creep)
            overscoped = 0
            for dtype, purpose_list in purposes.items():
                if len(purpose_list) > 3:
                    overscoped += 1

            if overscoped == 0:
                return 55.0
            overshoot_fraction = overscoped / max(len(purposes), 1)
            return max(10.0, 55.0 - overshoot_fraction * 30.0)

    @staticmethod
    def _score_access_scoping(
        architecture: str,
        access_controls: dict[str, list[str]],
        volumes: dict[str, dict[str, int]],
    ) -> float:
        """Score how well access is scoped to minimum necessary."""
        if not access_controls:
            if architecture == "chambers":
                return 75.0  # Worlds provide inherent scoping
            return 30.0  # No controls defined

        # Count total actor-location pairs
        total_access_pairs = sum(len(actors) for actors in access_controls.values())
        total_locations = max(len(access_controls), 1)
        avg_actors_per_location = total_access_pairs / total_locations

        if architecture == "chambers":
            # Fewer actors per location is better; worlds limit access
            score = 100.0 - max(0.0, (avg_actors_per_location - 2.0) * 10.0)
        else:
            # Current: cloud and aggregate typically have broad access
            score = 100.0 - max(0.0, (avg_actors_per_location - 2.0) * 15.0)

        return max(0.0, min(100.0, score))

    @staticmethod
    def _score_data_necessity(
        architecture: str,
        volumes: dict[str, dict[str, int]],
        purposes: dict[str, list[str]],
    ) -> float:
        """Score whether only necessary data is retained for each purpose."""
        if not volumes:
            return 50.0

        if architecture == "chambers":
            # Check each world/location against its necessary data types
            location_checks: dict[str, set[str]] = {
                "relay": _CLINICAL_NECESSARY,
                "patient_record": _CLINICAL_NECESSARY | {"demographics", "activity"},
                "research_channel": _RESEARCH_NECESSARY,
                "device_maint": _MAINTENANCE_NECESSARY,
            }

            scores: list[float] = []
            for location, type_volumes in volumes.items():
                necessary = location_checks.get(location, _CLINICAL_NECESSARY)
                stored_types = set(type_volumes.keys())
                unnecessary = stored_types - necessary
                if not stored_types:
                    continue
                unnecessary_fraction = len(unnecessary) / len(stored_types)
                scores.append(100.0 * (1.0 - unnecessary_fraction))

            return float(np.mean(scores)) if scores else 80.0

        else:
            # Current: all data goes to all layers -> poor necessity
            all_types_in_cloud: set[str] = set()
            for loc_data in volumes.values():
                all_types_in_cloud.update(loc_data.keys())

            if not all_types_in_cloud:
                return 30.0

            # Everything is in the cloud; necessity depends on purpose definitions
            if not purposes:
                return 25.0

            # Check if purposes justify the stored types
            justified_types: set[str] = set()
            for dtype, purpose_list in purposes.items():
                if purpose_list:
                    justified_types.add(dtype)

            justified_fraction = len(justified_types) / max(len(all_types_in_cloud), 1)
            return min(100.0, 30.0 + justified_fraction * 40.0)
