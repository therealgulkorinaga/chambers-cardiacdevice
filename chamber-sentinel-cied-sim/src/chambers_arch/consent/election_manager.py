"""Patient-elected persistence manager — opt-in manufacturer retention."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PersistenceElection:
    """A patient's election to allow manufacturer data retention."""
    patient_id: str
    category: str  # 'clinical', 'activity', 'device_status'
    elected: bool = False
    elected_at_s: float | None = None
    revoked_at_s: float | None = None


class ElectionManager:
    """Manages patient-elected persistence.

    Default: burn-by-default (no manufacturer retention).
    Patient can opt-in to manufacturer retention per data category.
    Elections are granular and revocable.
    """

    CATEGORIES = ("clinical", "activity", "device_status")

    def __init__(self) -> None:
        # patient_id -> {category: PersistenceElection}
        self._elections: dict[str, dict[str, PersistenceElection]] = {}
        self._audit: list[dict[str, Any]] = []

    def initialize_patient(self, patient_id: str) -> None:
        """Initialize a patient with default elections (all False = burn-by-default)."""
        self._elections[patient_id] = {
            cat: PersistenceElection(patient_id=patient_id, category=cat)
            for cat in self.CATEGORIES
        }

    def elect(self, patient_id: str, category: str, timestamp_s: float) -> bool:
        """Patient elects manufacturer persistence for a category."""
        if category not in self.CATEGORIES:
            return False
        if patient_id not in self._elections:
            self.initialize_patient(patient_id)

        election = self._elections[patient_id][category]
        election.elected = True
        election.elected_at_s = timestamp_s
        election.revoked_at_s = None

        self._audit.append({
            "timestamp_s": timestamp_s, "action": "elect",
            "patient_id": patient_id, "category": category,
        })
        return True

    def revoke(self, patient_id: str, category: str, timestamp_s: float) -> bool:
        """Patient revokes manufacturer persistence. Triggers burn of manufacturer copy."""
        if patient_id not in self._elections:
            return False
        if category not in self._elections[patient_id]:
            return False

        election = self._elections[patient_id][category]
        election.elected = False
        election.revoked_at_s = timestamp_s

        self._audit.append({
            "timestamp_s": timestamp_s, "action": "revoke",
            "patient_id": patient_id, "category": category,
        })
        return True

    def is_elected(self, patient_id: str, category: str) -> bool:
        """Check if a patient has elected persistence for a category."""
        if patient_id not in self._elections:
            return False
        election = self._elections[patient_id].get(category)
        return election.elected if election else False

    def get_patient_elections(self, patient_id: str) -> dict[str, bool]:
        """Get all election statuses for a patient."""
        if patient_id not in self._elections:
            return {cat: False for cat in self.CATEGORIES}
        return {
            cat: el.elected
            for cat, el in self._elections[patient_id].items()
        }

    @property
    def stats(self) -> dict[str, Any]:
        total_elected = sum(
            1 for elections in self._elections.values()
            for el in elections.values() if el.elected
        )
        return {
            "patients_tracked": len(self._elections),
            "total_elections_active": total_elected,
            "by_category": {
                cat: sum(
                    1 for elections in self._elections.values()
                    if elections.get(cat, PersistenceElection("", "")).elected
                )
                for cat in self.CATEGORIES
            },
        }
