"""
Battery depletion model for lithium-iodine CIED pulse generators.

Models the characteristic voltage-vs-capacity curve of a Li/I2 cell,
including internal-impedance rise near end-of-service.  The model is
driven by pacing current draw and optional telemetry/sensor loads.

Voltage model::

    V(t) = V_BOL - k1 * ln(1 + k2 * Q_cumulative)

where *Q_cumulative* is the cumulative charge drawn in ampere-hours.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Public data-model types
# ---------------------------------------------------------------------------


@dataclass
class BatteryState:
    """Snapshot of the battery state at a point in time."""

    voltage_v: float
    impedance_ohms: float
    stage: str  # 'BOL', 'MOL', 'ERI', 'EOS'
    cumulative_charge_ah: float
    remaining_capacity_ah: float
    projected_longevity_days: float
    elapsed_hours: float


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class BatteryModel:
    """
    Lithium-iodine battery model for CIED pulse generators.

    Parameters
    ----------
    chemistry:
        Battery chemistry identifier (currently only ``'lithium_iodine'``).
    voltage_bol:
        Beginning-of-life open-circuit voltage (V).
    voltage_eri:
        Elective replacement indicator voltage threshold (V).
    voltage_eos:
        End-of-service voltage threshold (V).
    capacity_ah:
        Nominal cell capacity in ampere-hours.
    """

    # Stage thresholds (computed from voltage)
    _STAGE_ORDER = ("BOL", "MOL", "ERI", "EOS")

    def __init__(
        self,
        chemistry: str = "lithium_iodine",
        voltage_bol: float = 2.8,
        voltage_eri: float = 2.6,
        voltage_eos: float = 2.4,
        capacity_ah: float = 1.0,
    ) -> None:
        self._chemistry = chemistry
        self._voltage_bol = voltage_bol
        self._voltage_eri = voltage_eri
        self._voltage_eos = voltage_eos
        self._capacity_ah = capacity_ah

        # Internal state
        self._cumulative_charge_ah: float = 0.0
        self._elapsed_hours: float = 0.0

        # Model constants derived so that V reaches EOS when the full
        # capacity is consumed.
        # V_BOL - k1 * ln(1 + k2 * capacity) = V_EOS
        # => k1 * ln(1 + k2 * capacity) = V_BOL - V_EOS
        # We pick k2 such that 1 + k2 * capacity ≈ e^3 ≈ 20.09 (gives
        # reasonable curvature), then solve for k1.
        self._k2: float = (math.e ** 3 - 1) / capacity_ah  # ~19.09 / capacity
        self._k1: float = (voltage_bol - voltage_eos) / math.log(1.0 + self._k2 * capacity_ah)

        # Impedance model: baseline impedance grows exponentially as
        # voltage drops.
        # Z(V) = Z_bol * exp(alpha * (V_bol - V))
        self._impedance_bol_ohms: float = 100.0  # fresh cell
        # At EOS the impedance should be ~10x higher -> alpha chosen accordingly
        self._impedance_alpha: float = math.log(10.0) / (voltage_bol - voltage_eos)

        # Average current draw tracker for longevity projection
        self._current_draw_history_ua: list[float] = []
        self._max_history: int = 1000

    # -- Voltage model -----------------------------------------------------

    def _compute_voltage(self) -> float:
        """Compute the current terminal voltage from cumulative charge."""
        v = self._voltage_bol - self._k1 * math.log(
            1.0 + self._k2 * self._cumulative_charge_ah
        )
        return max(v, self._voltage_eos * 0.95)  # clamp slightly below EOS

    # -- Impedance model ---------------------------------------------------

    def _compute_impedance(self, voltage: float) -> float:
        """Compute internal cell impedance from current voltage."""
        delta_v = self._voltage_bol - voltage
        return self._impedance_bol_ohms * math.exp(self._impedance_alpha * delta_v)

    # -- Stage classification ----------------------------------------------

    def _classify_stage(self, voltage: float) -> str:
        """Return the battery lifecycle stage based on voltage."""
        if voltage >= self._voltage_eri + 0.5 * (self._voltage_bol - self._voltage_eri):
            return "BOL"
        if voltage >= self._voltage_eri:
            return "MOL"
        if voltage >= self._voltage_eos:
            return "ERI"
        return "EOS"

    # -- Longevity projection ----------------------------------------------

    def _project_longevity_days(self, remaining_ah: float) -> float:
        """
        Estimate remaining device longevity in days based on the
        rolling-average current draw.
        """
        if not self._current_draw_history_ua:
            return float("inf")
        avg_current_ua = sum(self._current_draw_history_ua) / len(self._current_draw_history_ua)
        if avg_current_ua <= 0:
            return float("inf")
        avg_current_a = avg_current_ua * 1e-6
        remaining_hours = remaining_ah / avg_current_a
        return remaining_hours / 24.0

    # -- Public API --------------------------------------------------------

    def step(
        self,
        dt_hours: float,
        pacing_current_ua: float,
        telemetry_active: bool = False,
        rate_response_active: bool = False,
    ) -> BatteryState:
        """
        Advance the battery model by *dt_hours* with the given load.

        Parameters
        ----------
        dt_hours:
            Time step in hours.
        pacing_current_ua:
            Total pacing output current in micro-amperes (typically
            10-25 uA per active output).
        telemetry_active:
            If ``True``, adds 50 uA telemetry load.
        rate_response_active:
            If ``True``, adds 5 uA accelerometer/sensor load.

        Returns
        -------
        BatteryState
        """
        total_current_ua = pacing_current_ua
        if telemetry_active:
            total_current_ua += 50.0
        if rate_response_active:
            total_current_ua += 5.0

        # Quiescent current (digital logic, clock, etc.)
        total_current_ua += 8.0

        # Track for longevity projection
        self._current_draw_history_ua.append(total_current_ua)
        if len(self._current_draw_history_ua) > self._max_history:
            self._current_draw_history_ua.pop(0)

        # Charge consumed this step
        charge_ah = (total_current_ua * 1e-6) * dt_hours
        self._cumulative_charge_ah += charge_ah
        self._elapsed_hours += dt_hours

        return self.get_state()

    def get_state(self) -> BatteryState:
        """Return the current battery state snapshot."""
        voltage = self._compute_voltage()
        impedance = self._compute_impedance(voltage)
        stage = self._classify_stage(voltage)
        remaining = max(0.0, self._capacity_ah - self._cumulative_charge_ah)
        longevity = self._project_longevity_days(remaining)

        return BatteryState(
            voltage_v=round(voltage, 4),
            impedance_ohms=round(impedance, 1),
            stage=stage,
            cumulative_charge_ah=round(self._cumulative_charge_ah, 6),
            remaining_capacity_ah=round(remaining, 6),
            projected_longevity_days=round(longevity, 1),
            elapsed_hours=round(self._elapsed_hours, 2),
        )

    def get_stage(self) -> str:
        """Return the current lifecycle stage: BOL, MOL, ERI, or EOS."""
        return self._classify_stage(self._compute_voltage())
