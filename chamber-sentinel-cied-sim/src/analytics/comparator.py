"""
Side-by-side architecture comparison aggregator.

Pulls metrics from all analytics sub-modules and produces unified
comparison structures suitable for:

- Summary tables (terminal / report)
- Time-series charts (Plotly / Dash)
- CSV / JSON export for external analysis
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from src.analytics.adverse_event_impact import AdverseEventImpactAnalyzer
from src.analytics.attack_surface import AttackSurfaceCalculator
from src.analytics.clinical_availability import ClinicalAvailabilityMonitor
from src.analytics.persistence_tracker import PersistenceTracker
from src.analytics.regulatory_compliance import RegulatoryComplianceScorer


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SECONDS_PER_DAY = 86_400.0


# ---------------------------------------------------------------------------
# Comparator
# ---------------------------------------------------------------------------


class ArchitectureComparator:
    """Aggregates ALL analytics into one architecture comparison.

    Parameters
    ----------
    persistence_tracker:
        Tracks data-volume time series for both architectures.
    attack_surface_calc:
        Attack-surface scorer.
    clinical_monitor:
        Clinical availability metric tracker.
    adverse_analyzer:
        Adverse event impact analyzer.
    compliance_scorer:
        Regulatory compliance scorer.
    """

    def __init__(
        self,
        persistence_tracker: PersistenceTracker,
        attack_surface_calc: AttackSurfaceCalculator,
        clinical_monitor: ClinicalAvailabilityMonitor,
        adverse_analyzer: AdverseEventImpactAnalyzer,
        compliance_scorer: RegulatoryComplianceScorer,
    ) -> None:
        self._persistence = persistence_tracker
        self._attack_surface = attack_surface_calc
        self._clinical = clinical_monitor
        self._adverse = adverse_analyzer
        self._compliance = compliance_scorer

        # Cache for the latest generated comparison
        self._latest_comparison: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Full comparison snapshot
    # ------------------------------------------------------------------

    def generate_comparison(
        self,
        timestamp_s: float,
        current_arch_state: dict[str, Any] | None = None,
        chambers_arch_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Aggregate ALL metrics into a unified comparison structure.

        Parameters
        ----------
        timestamp_s:
            Current simulation time for point-in-time metrics.
        current_arch_state:
            Full data-state dict for the current architecture (used by
            compliance scorer).  If ``None``, compliance scores are omitted.
        chambers_arch_state:
            Full data-state dict for Chambers architecture.

        Returns
        -------
        dict
            ``{metric_name: {current: value, chambers: value,
            delta: value, ratio: value}}``.
        """
        comparison: dict[str, Any] = {
            "timestamp_s": timestamp_s,
            "timestamp_days": timestamp_s / _SECONDS_PER_DAY,
            "metrics": {},
        }

        metrics = comparison["metrics"]

        # -- Persistence volume --
        ratio = self._persistence.get_ratio(timestamp_s)
        ts_data = self._persistence.get_time_series()

        cur_bytes = self._get_latest_bytes(ts_data.get("current", []), timestamp_s)
        ch_bytes = self._get_latest_bytes(ts_data.get("chambers", []), timestamp_s)

        metrics["data_volume_bytes"] = self._metric_entry(
            cur_bytes, ch_bytes, "lower_is_better",
        )
        metrics["data_volume_mb"] = self._metric_entry(
            cur_bytes / (1024 * 1024),
            ch_bytes / (1024 * 1024),
            "lower_is_better",
        )
        metrics["persistence_ratio"] = {
            "value": round(ratio, 4),
            "description": "V_current / V_chambers",
            "interpretation": (
                "Higher ratio means Chambers stores less data"
                if ratio > 1 else "Ratio <= 1 means comparable storage"
            ),
        }

        # -- Attack surface --
        as_comparison = self._attack_surface.get_comparison()
        if as_comparison["time_series"]:
            latest_as = as_comparison["time_series"][-1]
            metrics["attack_surface_score"] = self._metric_entry(
                latest_as.get("current_as", 0.0),
                latest_as.get("chambers_as", 0.0),
                "lower_is_better",
            )
            metrics["attack_surface_ratio"] = {
                "value": as_comparison["latest_ratio"],
                "average_ratio": as_comparison["average_ratio"],
                "description": "AS_current / AS_chambers",
            }

        # -- Clinical availability --
        ca_metrics = self._clinical.get_all_metrics()
        for ca_name in ("ca1", "ca2", "ca3", "ca4", "ca5"):
            ca_value = ca_metrics.get(ca_name, 1.0)
            # For current arch, CA metrics are generally 1.0 (all data kept)
            # except CA-2 which doesn't apply (no burn)
            if ca_name == "ca2":
                current_val = 1.0  # no burn in current arch
                chambers_val = ca_value
            elif ca_name in ("ca3", "ca4"):
                current_val = 1.0  # all historical data available
                chambers_val = ca_value
            elif ca_name == "ca5":
                current_val = 1.0  # all emergency data available
                chambers_val = ca_value
            else:
                # CA-1 is about alert delivery, same for both architectures
                current_val = ca_value
                chambers_val = ca_value

            metrics[ca_name] = self._metric_entry(
                current_val, chambers_val, "higher_is_better",
            )

        # -- Adverse event analysis --
        analyses = self._adverse.get_analyses()
        if analyses:
            latest_analysis = analyses[-1]
            metrics["adverse_event_loss_rate"] = {
                "chambers": latest_analysis.get("loss_rate", 0.0),
                "current": 0.0,  # current arch never loses data
                "delta": -latest_analysis.get("loss_rate", 0.0),
                "description": "Fraction of data lost due to burn",
            }
            metrics["investigation_adequacy"] = {
                "chambers": latest_analysis.get("investigation_adequacy", 0.0),
                "current": 1.0,
                "delta": latest_analysis.get("investigation_adequacy", 0.0) - 1.0,
                "description": "Weighted adequacy score for investigation",
            }

        # -- Regulatory compliance --
        if current_arch_state is not None and chambers_arch_state is not None:
            radar = self._compliance.get_radar_chart_data(
                current_arch_state, chambers_arch_state,
            )
            metrics["compliance_overall_current"] = {
                "value": radar["current_mean"],
                "description": "Mean compliance score across all frameworks (current)",
            }
            metrics["compliance_overall_chambers"] = {
                "value": radar["chambers_mean"],
                "description": "Mean compliance score across all frameworks (Chambers)",
            }
            metrics["compliance_radar"] = radar

        # -- 10-year projection --
        projection = self._persistence.get_projected_10_year()
        metrics["projected_10yr"] = projection

        self._latest_comparison = comparison
        return comparison

    # ------------------------------------------------------------------
    # Summary table
    # ------------------------------------------------------------------

    def generate_summary_table(self) -> list[dict[str, Any]]:
        """Generate a flat summary table suitable for rendering or export.

        Returns
        -------
        list[dict]
            Each dict has ``metric``, ``current``, ``chambers``, ``delta``,
            ``ratio``, ``unit``, ``preference``.
        """
        rows: list[dict[str, Any]] = []

        # Persistence
        ts = self._persistence.get_time_series()
        cur_bytes = self._latest_total(ts.get("current", []))
        ch_bytes = self._latest_total(ts.get("chambers", []))
        rows.append(self._table_row(
            "Data Volume", cur_bytes / (1024 * 1024), ch_bytes / (1024 * 1024),
            "MB", "lower_is_better",
        ))

        ratio = self._persistence.get_ratio()
        rows.append(self._table_row(
            "Persistence Ratio (V_cur/V_ch)", ratio, 1.0,
            "ratio", "higher_is_better",
        ))

        # Attack surface
        as_data = self._attack_surface.get_comparison()
        if as_data["time_series"]:
            latest = as_data["time_series"][-1]
            rows.append(self._table_row(
                "Attack Surface Score",
                latest.get("current_as", 0.0),
                latest.get("chambers_as", 0.0),
                "score", "lower_is_better",
            ))

        # Clinical availability
        ca = self._clinical.get_all_metrics()
        for i, name in enumerate(["CA-1 Alert Delivery", "CA-2 Ack Before Burn",
                                   "CA-3 Historical Availability",
                                   "CA-4 Care Continuity",
                                   "CA-5 Emergency Availability"], 1):
            ca_key = f"ca{i}"
            val = ca.get(ca_key, 1.0)
            # For current arch, most CAs are 1.0
            cur_val = 1.0 if i > 1 else val
            rows.append(self._table_row(name, cur_val, val, "rate", "higher_is_better"))

        # Projection
        proj = self._persistence.get_projected_10_year()
        rows.append(self._table_row(
            "10-Year Projected Volume",
            proj.get("current_10yr_gb", 0.0),
            proj.get("chambers_10yr_gb", 0.0),
            "GB", "lower_is_better",
        ))

        return rows

    # ------------------------------------------------------------------
    # Time-series comparison
    # ------------------------------------------------------------------

    def generate_time_series_comparison(self) -> dict[str, Any]:
        """Return time-series data for charting.

        Returns
        -------
        dict
            ``persistence``: volume time series,
            ``attack_surface``: AS time series,
            ``clinical_availability``: CA metric snapshots.
        """
        return {
            "persistence": self._persistence.get_time_series(),
            "attack_surface": self._attack_surface.get_comparison(),
            "clinical_availability": self._clinical.get_all_metrics(),
        }

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_csv(self, filepath: str) -> None:
        """Export the summary table to a CSV file.

        Parameters
        ----------
        filepath:
            Destination file path.
        """
        rows = self.generate_summary_table()
        if not rows:
            return

        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        fieldnames = ["metric", "current", "chambers", "delta",
                      "ratio", "unit", "preference"]

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def export_json(self, filepath: str) -> None:
        """Export the full comparison to a JSON file.

        Parameters
        ----------
        filepath:
            Destination file path.
        """
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        data: dict[str, Any] = {}

        if self._latest_comparison is not None:
            data["comparison"] = self._serialize(self._latest_comparison)

        data["summary_table"] = self.generate_summary_table()
        data["time_series"] = self._serialize(self.generate_time_series_comparison())

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _metric_entry(
        current: float,
        chambers: float,
        preference: str,
    ) -> dict[str, Any]:
        """Build a standard metric comparison entry."""
        delta = chambers - current
        ratio = (current / chambers) if chambers != 0 else (
            float("inf") if current > 0 else 0.0
        )
        return {
            "current": round(current, 6),
            "chambers": round(chambers, 6),
            "delta": round(delta, 6),
            "ratio": round(ratio, 4) if ratio != float("inf") else "inf",
            "preference": preference,
        }

    @staticmethod
    def _table_row(
        metric: str,
        current: float,
        chambers: float,
        unit: str,
        preference: str,
    ) -> dict[str, Any]:
        """Build a summary table row."""
        delta = chambers - current
        ratio = (current / chambers) if chambers != 0 else (
            float("inf") if current > 0 else 0.0
        )
        return {
            "metric": metric,
            "current": round(current, 4),
            "chambers": round(chambers, 4),
            "delta": round(delta, 4),
            "ratio": round(ratio, 4) if ratio != float("inf") else "inf",
            "unit": unit,
            "preference": preference,
        }

    @staticmethod
    def _get_latest_bytes(series: list[dict[str, Any]], timestamp_s: float) -> int:
        """Extract the total_bytes closest to timestamp_s from a series."""
        if not series:
            return 0
        # Find the entry at or before timestamp_s
        best = series[0]
        for entry in series:
            if entry["timestamp_s"] <= timestamp_s:
                best = entry
            else:
                break
        return best.get("total_bytes", 0)

    @staticmethod
    def _latest_total(series: list[dict[str, Any]]) -> int:
        """Get the latest total_bytes from a series."""
        if not series:
            return 0
        return series[-1].get("total_bytes", 0)

    @staticmethod
    def _serialize(obj: Any) -> Any:
        """Recursively convert numpy types to Python natives for JSON."""
        if isinstance(obj, dict):
            return {k: ArchitectureComparator._serialize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [ArchitectureComparator._serialize(v) for v in obj]
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
            return str(obj)
        return obj
