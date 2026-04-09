"""Research World — consent-gated data for R&D with two channels."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from src.generator.stream import World, EventType
from src.chambers_arch.worlds.base_world import BaseWorld, WorldRecord


@dataclass
class ResearchConsent:
    """Tracks a patient's consent for a research programme."""
    consent_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    patient_id: str = ""
    programme_id: str = ""
    channel: str = "A"  # 'A' (aggregate, opt-out) or 'B' (individual, opt-in)
    granted_at_s: float = 0.0
    retention_until_s: float = 0.0
    withdrawn: bool = False
    withdrawn_at_s: float | None = None


class ResearchWorld(BaseWorld):
    """Research World: Governed research data with two channels.

    Channel A (Aggregated, de-identified):
    - Episode counts, therapy rates, device utilization, battery/lead stats
    - k-anonymity (k >= 10) + differential privacy (configurable epsilon)
    - Opt-out model: included by default, patient can withdraw
    - Burns on programme completion

    Channel B (Individual-level, consent-gated):
    - Individual lead impedance trajectories, arrhythmia evolution, therapy response
    - Explicit opt-in required + simulated ethics review
    - Defined retention period at consent time
    - Mandatory burn on consent withdrawal
    - Access: named researchers on approved protocol only
    """

    ALLOWED_EVENTS = {
        EventType.EPISODE_START,
        EventType.EPISODE_END,
        EventType.DEVICE_STATUS,
        EventType.LEAD_MEASUREMENT,
        EventType.THRESHOLD_TEST,
    }

    AUTHORIZED_ACTORS = {"researcher", "system"}

    def __init__(self, k_anonymity: int = 10, epsilon: float = 1.0,
                 rng: np.random.Generator | None = None) -> None:
        super().__init__(
            world_type=World.RESEARCH,
            allowed_event_types=self.ALLOWED_EVENTS,
            authorized_actors=self.AUTHORIZED_ACTORS,
        )
        self.k_anonymity = k_anonymity
        self.epsilon = epsilon
        self.rng = rng or np.random.default_rng()

        # Consent tracking
        self._consents: dict[str, list[ResearchConsent]] = defaultdict(list)  # patient_id -> consents

        # Channel A: aggregate metrics
        self._channel_a_metrics: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._channel_a_counts: dict[str, int] = defaultdict(int)
        self._channel_a_opted_out: set[str] = set()

        # Channel B: individual records (separate from base world records)
        self._channel_b_records: dict[str, list[WorldRecord]] = defaultdict(list)

    def _on_accept(self, record: WorldRecord) -> None:
        """Route data to appropriate channel based on consent."""
        patient_id = record.patient_id

        # Channel A: aggregate (unless opted out)
        if patient_id not in self._channel_a_opted_out:
            self._update_aggregates(record)

        # Channel B: only if patient has active individual consent
        if self._has_active_channel_b_consent(patient_id):
            self._channel_b_records[patient_id].append(record)

    def _on_burn(self, record: WorldRecord) -> None:
        """Clean up channel B records on burn."""
        patient_id = record.patient_id
        if patient_id in self._channel_b_records:
            self._channel_b_records[patient_id] = [
                r for r in self._channel_b_records[patient_id]
                if r.record_id != record.record_id
            ]

    def _update_aggregates(self, record: WorldRecord) -> None:
        """Update Channel A aggregate metrics from a record."""
        event_type = record.event_type
        device_serial = record.device_serial

        if event_type in (EventType.EPISODE_START.value, "episode_start"):
            ep_type = record.data.get("episode_type", "unknown")
            self._channel_a_metrics["episode_counts"][ep_type] += 1
            self._channel_a_counts["total_episodes"] += 1

        elif event_type in (EventType.DEVICE_STATUS.value, "device_status"):
            if "battery_voltage" in record.data:
                self._channel_a_metrics["battery_voltages"][device_serial] = record.data["battery_voltage"]

        elif event_type in (EventType.LEAD_MEASUREMENT.value, "lead_measurement"):
            lead_id = record.data.get("lead_id", "unknown")
            if "impedance_ohms" in record.data:
                self._channel_a_metrics["lead_impedances"][f"{device_serial}_{lead_id}"] = record.data["impedance_ohms"]

    def grant_consent(self, patient_id: str, programme_id: str, channel: str,
                      retention_days: int, timestamp_s: float) -> ResearchConsent:
        """Grant research consent for a patient."""
        consent = ResearchConsent(
            patient_id=patient_id,
            programme_id=programme_id,
            channel=channel,
            granted_at_s=timestamp_s,
            retention_until_s=timestamp_s + (retention_days * 86400),
        )
        self._consents[patient_id].append(consent)

        if channel == "A" and patient_id in self._channel_a_opted_out:
            self._channel_a_opted_out.discard(patient_id)

        self._audit(timestamp_s, "consent_granted", "system", patient_id=patient_id,
                    details={"programme_id": programme_id, "channel": channel,
                             "retention_days": retention_days})
        return consent

    def withdraw_consent(self, patient_id: str, programme_id: str,
                         timestamp_s: float) -> int:
        """Withdraw consent. Triggers mandatory burn of individual data.
        Returns count of records burned.
        """
        burned = 0
        for consent in self._consents.get(patient_id, []):
            if consent.programme_id == programme_id and not consent.withdrawn:
                consent.withdrawn = True
                consent.withdrawn_at_s = timestamp_s

                if consent.channel == "B":
                    # Mandatory burn of individual-level data
                    for record in list(self._channel_b_records.get(patient_id, [])):
                        if self.burn(record.record_id, timestamp_s):
                            burned += 1

                elif consent.channel == "A":
                    self._channel_a_opted_out.add(patient_id)

        self._audit(timestamp_s, "consent_withdrawn", "system", patient_id=patient_id,
                    details={"programme_id": programme_id, "records_burned": burned})
        return burned

    def opt_out_channel_a(self, patient_id: str, timestamp_s: float) -> None:
        """Patient opts out of aggregate data contribution."""
        self._channel_a_opted_out.add(patient_id)
        self._audit(timestamp_s, "opt_out", "patient", patient_id=patient_id,
                    details={"channel": "A"})

    def get_aggregate_metrics(self, add_noise: bool = True) -> dict[str, Any]:
        """Get Channel A aggregate metrics with optional differential privacy noise."""
        metrics = {}
        for category, values in self._channel_a_metrics.items():
            if add_noise:
                # Laplace noise for differential privacy
                noisy_values = {}
                for key, val in values.items():
                    noise = self.rng.laplace(0, 1.0 / self.epsilon)
                    noisy_values[key] = max(0, val + noise)
                metrics[category] = noisy_values
            else:
                metrics[category] = dict(values)

        metrics["_meta"] = {
            "k_anonymity": self.k_anonymity,
            "epsilon": self.epsilon,
            "opted_out_count": len(self._channel_a_opted_out),
            "total_contributors": len(self._consents) - len(self._channel_a_opted_out),
        }
        return metrics

    def _has_active_channel_b_consent(self, patient_id: str) -> bool:
        """Check if patient has active Channel B consent."""
        for consent in self._consents.get(patient_id, []):
            if consent.channel == "B" and not consent.withdrawn:
                return True
        return False

    def get_burn_candidates(self, timestamp_s: float) -> list[str]:
        """Return records eligible for burning:
        - Expired retention periods
        - Withdrawn consent records
        """
        candidates: list[str] = []

        for record_id, record in list(self._all_records.items()):
            if record.held:
                continue

            patient_id = record.patient_id
            # Check if all consents for this patient have expired or been withdrawn
            active_consent = False
            for consent in self._consents.get(patient_id, []):
                if not consent.withdrawn and consent.retention_until_s > timestamp_s:
                    active_consent = True
                    break

            if not active_consent:
                candidates.append(record_id)

        return candidates

    def get_status(self) -> dict[str, Any]:
        status = super().get_status()
        total_consents = sum(len(c) for c in self._consents.values())
        active_consents = sum(
            1 for consents in self._consents.values()
            for c in consents if not c.withdrawn
        )
        status.update({
            "k_anonymity": self.k_anonymity,
            "epsilon": self.epsilon,
            "total_consents": total_consents,
            "active_consents": active_consents,
            "channel_a_opted_out": len(self._channel_a_opted_out),
            "channel_b_patients": sum(
                1 for pid in self._consents
                if self._has_active_channel_b_consent(pid)
            ),
            "channel_b_records": sum(
                len(recs) for recs in self._channel_b_records.values()
            ),
        })
        return status
