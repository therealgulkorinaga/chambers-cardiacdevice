"""Burn policies — per-world burn schedule definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class BurnPolicy:
    """Defines when and how data should be burned in a typed world."""
    world: str
    policy_name: str
    description: str
    trigger: str  # 'delivery_ack', 'rolling_window', 'consent', 'patient_controlled', 'investigation_buffer'
    parameters: dict[str, Any]

    def should_burn(self, record_age_s: float, delivered: bool = False,
                    acknowledged: bool = False, consent_active: bool = True,
                    investigation_released: bool = False) -> bool:
        """Evaluate whether a record should be burned given its current state."""
        if self.trigger == "delivery_ack":
            return delivered and acknowledged

        elif self.trigger == "delivery_only":
            return delivered

        elif self.trigger == "rolling_window":
            window_s = self.parameters.get("window_days", 90) * 86400
            return record_age_s > window_s

        elif self.trigger == "consent":
            return not consent_active

        elif self.trigger == "patient_controlled":
            return False  # Patient must explicitly request

        elif self.trigger == "investigation_buffer":
            buffer_s = self.parameters.get("buffer_months", 12) * 30 * 86400
            return investigation_released and record_age_s > buffer_s

        elif self.trigger == "max_hold_timeout":
            timeout_s = self.parameters.get("timeout_days", 30) * 86400
            return record_age_s > timeout_s

        return False


# Pre-defined burn policies per world

CLINICAL_ALERT_POLICY = BurnPolicy(
    world="clinical",
    policy_name="clinical_alert_burn",
    description="Burn alert data after delivery to patient record AND clinician acknowledgment",
    trigger="delivery_ack",
    parameters={"require_ack": True, "fallback_timeout_days": 30},
)

CLINICAL_NON_ALERT_POLICY = BurnPolicy(
    world="clinical",
    policy_name="clinical_non_alert_burn",
    description="Burn non-alert data after delivery to patient record",
    trigger="delivery_only",
    parameters={"require_ack": False},
)

DEVICE_MAINTENANCE_POLICY = BurnPolicy(
    world="device_maintenance",
    policy_name="device_maint_rolling",
    description="Rolling window retention — oldest data evicted when window advances",
    trigger="rolling_window",
    parameters={"window_days": 90},
)

RESEARCH_CHANNEL_A_POLICY = BurnPolicy(
    world="research",
    policy_name="research_aggregate_burn",
    description="Aggregate data burns on programme completion",
    trigger="consent",
    parameters={"channel": "A"},
)

RESEARCH_CHANNEL_B_POLICY = BurnPolicy(
    world="research",
    policy_name="research_individual_burn",
    description="Individual data burns on consent withdrawal or retention expiry",
    trigger="consent",
    parameters={"channel": "B", "mandatory_on_withdrawal": True},
)

PATIENT_POLICY = BurnPolicy(
    world="patient",
    policy_name="patient_controlled",
    description="Patient-initiated only — no automatic burn",
    trigger="patient_controlled",
    parameters={"auto_burn": False},
)

SAFETY_INVESTIGATION_POLICY = BurnPolicy(
    world="safety_investigation",
    policy_name="investigation_buffer_burn",
    description="Burns after investigation closure + 12-month buffer",
    trigger="investigation_buffer",
    parameters={"buffer_months": 12},
)

# Collect all default policies
DEFAULT_POLICIES: dict[str, list[BurnPolicy]] = {
    "clinical": [CLINICAL_ALERT_POLICY, CLINICAL_NON_ALERT_POLICY],
    "device_maintenance": [DEVICE_MAINTENANCE_POLICY],
    "research": [RESEARCH_CHANNEL_A_POLICY, RESEARCH_CHANNEL_B_POLICY],
    "patient": [PATIENT_POLICY],
    "safety_investigation": [SAFETY_INVESTIGATION_POLICY],
}


def get_policies_for_world(world_name: str) -> list[BurnPolicy]:
    """Get the default burn policies for a world."""
    return DEFAULT_POLICIES.get(world_name, [])


def create_custom_policy(world: str, trigger: str, **kwargs: Any) -> BurnPolicy:
    """Create a custom burn policy."""
    return BurnPolicy(
        world=world,
        policy_name=f"custom_{world}_{trigger}",
        description=f"Custom {trigger} policy for {world}",
        trigger=trigger,
        parameters=kwargs,
    )
