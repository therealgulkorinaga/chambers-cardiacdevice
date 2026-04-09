"""Layer 5: Aggregate Pool — population-level analytics with de-identification."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class AggregateRecord:
    """A single aggregate data point."""
    metric_name: str
    period: str  # e.g., "2026-Q1", "2026-03"
    device_model: str = "all"
    value: float = 0.0
    sample_size: int = 0
    k_anonymity_satisfied: bool = True


@dataclass
class QuasiIdentifier:
    """A combination of quasi-identifiers for re-identification risk analysis."""
    age_bucket: str  # e.g., "65-74"
    sex: str
    device_model: str
    region: str
    implant_year: int


class AggregatePool:
    """Layer 5: Population-level data across all patients on the platform.

    Models:
    - Monthly batch aggregation from manufacturer cloud data
    - k-anonymity with configurable k
    - Re-identification risk tracking
    - Feeds R&D, regulatory, and commercial analytics
    """

    def __init__(self, k_anonymity_k: int = 5, rng: np.random.Generator | None = None) -> None:
        self.k = k_anonymity_k
        self.rng = rng or np.random.default_rng()

        # Aggregate metrics by period
        self._aggregates: dict[str, list[AggregateRecord]] = defaultdict(list)

        # Population tracking for k-anonymity
        self._population: dict[str, QuasiIdentifier] = {}  # patient_id -> QI

        # Re-identification risk tracking
        self._equivalence_classes: dict[tuple, set[str]] = defaultdict(set)

        # Metrics
        self._total_aggregations = 0
        self._total_records = 0
        self._total_bytes = 0
        self._suppressed_records = 0

    def register_patient(self, patient_id: str, age: int, sex: str,
                         device_model: str, region: str, implant_year: int) -> None:
        """Register a patient's quasi-identifiers for k-anonymity analysis."""
        age_bucket = self._age_to_bucket(age)
        qi = QuasiIdentifier(
            age_bucket=age_bucket,
            sex=sex,
            device_model=device_model,
            region=region,
            implant_year=implant_year,
        )
        self._population[patient_id] = qi

        # Update equivalence classes
        key = (qi.age_bucket, qi.sex, qi.device_model, qi.region, qi.implant_year)
        self._equivalence_classes[key].add(patient_id)

    def run_aggregation(self, cloud: Any, period: str) -> list[AggregateRecord]:
        """Run monthly aggregation from the manufacturer cloud.

        Computes population-level metrics and checks k-anonymity.
        """
        self._total_aggregations += 1
        records: list[AggregateRecord] = []

        # Collect all patient data from cloud
        all_patients = set(self._population.keys())
        if not all_patients:
            return records

        # Metric: Average heart rate by device model
        # Metric: Episode counts by type
        # Metric: Pacing percentages
        # Metric: Battery longevity
        # Metric: Lead performance
        # Metric: Therapy delivery rates

        # Simulated aggregate metrics
        metric_templates = [
            ("avg_heart_rate_bpm", "heart_rate"),
            ("af_episode_rate_per_patient_year", "arrhythmia"),
            ("vt_episode_rate_per_patient_year", "arrhythmia"),
            ("avg_ventricular_pacing_pct", "pacing"),
            ("avg_atrial_pacing_pct", "pacing"),
            ("avg_battery_voltage", "device"),
            ("avg_lead_impedance_rv", "device"),
            ("shock_rate_per_patient_year", "therapy"),
            ("atp_success_rate_pct", "therapy"),
        ]

        device_models = set(qi.device_model for qi in self._population.values())

        for metric_name, _category in metric_templates:
            # Overall aggregate
            overall = AggregateRecord(
                metric_name=metric_name,
                period=period,
                device_model="all",
                value=self._generate_aggregate_value(metric_name),
                sample_size=len(all_patients),
                k_anonymity_satisfied=len(all_patients) >= self.k,
            )
            records.append(overall)

            # Per-device-model aggregates
            for model in device_models:
                patients_with_model = [
                    pid for pid, qi in self._population.items()
                    if qi.device_model == model
                ]
                if len(patients_with_model) >= self.k:
                    rec = AggregateRecord(
                        metric_name=metric_name,
                        period=period,
                        device_model=model,
                        value=self._generate_aggregate_value(metric_name),
                        sample_size=len(patients_with_model),
                        k_anonymity_satisfied=True,
                    )
                    records.append(rec)
                else:
                    self._suppressed_records += 1

        self._aggregates[period] = records
        self._total_records += len(records)
        self._total_bytes += len(records) * 128  # ~128 bytes per aggregate record
        return records

    def _generate_aggregate_value(self, metric_name: str) -> float:
        """Generate a plausible aggregate value for a metric."""
        defaults: dict[str, tuple[float, float]] = {
            "avg_heart_rate_bpm": (72.0, 8.0),
            "af_episode_rate_per_patient_year": (2.5, 3.0),
            "vt_episode_rate_per_patient_year": (0.3, 0.5),
            "avg_ventricular_pacing_pct": (45.0, 25.0),
            "avg_atrial_pacing_pct": (35.0, 20.0),
            "avg_battery_voltage": (2.75, 0.05),
            "avg_lead_impedance_rv": (480.0, 80.0),
            "shock_rate_per_patient_year": (0.15, 0.2),
            "atp_success_rate_pct": (85.0, 10.0),
        }
        mean, std = defaults.get(metric_name, (50.0, 10.0))
        return max(0.0, float(self.rng.normal(mean, std)))

    def get_reidentification_risk(self) -> dict[str, Any]:
        """Analyze re-identification risk based on equivalence class sizes."""
        if not self._equivalence_classes:
            return {"risk": "unknown", "population_size": 0}

        class_sizes = [len(members) for members in self._equivalence_classes.values()]
        arr = np.array(class_sizes)

        # Patients in classes smaller than k
        at_risk = sum(1 for size in class_sizes if size < self.k)
        total_classes = len(class_sizes)

        return {
            "k": self.k,
            "population_size": len(self._population),
            "equivalence_classes": total_classes,
            "min_class_size": int(np.min(arr)) if len(arr) > 0 else 0,
            "max_class_size": int(np.max(arr)) if len(arr) > 0 else 0,
            "mean_class_size": float(np.mean(arr)) if len(arr) > 0 else 0,
            "classes_below_k": at_risk,
            "risk_fraction": at_risk / total_classes if total_classes > 0 else 0,
            "k_anonymity_satisfied": at_risk == 0,
        }

    def query_aggregates(self, period: str | None = None,
                         metric_name: str | None = None) -> list[AggregateRecord]:
        """Query aggregate records with optional filters."""
        results: list[AggregateRecord] = []

        periods = [period] if period else list(self._aggregates.keys())
        for p in periods:
            records = self._aggregates.get(p, [])
            if metric_name:
                records = [r for r in records if r.metric_name == metric_name]
            results.extend(records)

        return results

    @staticmethod
    def _age_to_bucket(age: int) -> str:
        """Convert age to 10-year bucket."""
        bucket_start = (age // 10) * 10
        return f"{bucket_start}-{bucket_start + 9}"

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "k_anonymity_k": self.k,
            "population_size": len(self._population),
            "total_aggregations": self._total_aggregations,
            "total_records": self._total_records,
            "total_bytes": self._total_bytes,
            "suppressed_records": self._suppressed_records,
            "periods_covered": list(self._aggregates.keys()),
            "reidentification_risk": self.get_reidentification_risk(),
        }
