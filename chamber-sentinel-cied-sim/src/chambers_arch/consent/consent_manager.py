"""Research consent lifecycle management."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Consent:
    """A patient's consent for a research programme."""
    consent_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    patient_id: str = ""
    programme_id: str = ""
    channel: str = "A"  # 'A' (aggregate) or 'B' (individual)
    data_scope: list[str] = field(default_factory=list)
    retention_days: int = 365
    status: str = "pending"  # pending, granted, active, withdrawn
    requested_at_s: float = 0.0
    granted_at_s: float | None = None
    activated_at_s: float | None = None
    withdrawn_at_s: float | None = None
    expires_at_s: float | None = None
    ethics_approved: bool = False
    ethics_approval_id: str | None = None


class ConsentManager:
    """Manages research consent lifecycle.

    States: PENDING -> GRANTED -> ACTIVE -> WITHDRAWN
    Channel A: opt-out (included by default)
    Channel B: explicit opt-in + ethics review
    """

    def __init__(self) -> None:
        self._consents: dict[str, Consent] = {}  # consent_id -> Consent
        self._by_patient: dict[str, list[str]] = {}  # patient_id -> [consent_ids]
        self._by_programme: dict[str, list[str]] = {}  # programme_id -> [consent_ids]
        self._audit: list[dict[str, Any]] = []

    def request_consent(self, patient_id: str, programme_id: str,
                        channel: str, data_scope: list[str],
                        retention_days: int, timestamp_s: float) -> Consent:
        """Create a consent request."""
        consent = Consent(
            patient_id=patient_id,
            programme_id=programme_id,
            channel=channel,
            data_scope=data_scope,
            retention_days=retention_days,
            requested_at_s=timestamp_s,
        )
        self._store(consent)
        self._log(timestamp_s, "request", consent)
        return consent

    def grant_consent(self, consent_id: str, timestamp_s: float) -> Consent | None:
        """Patient grants consent."""
        consent = self._consents.get(consent_id)
        if consent is None or consent.status not in ("pending",):
            return None
        consent.status = "granted"
        consent.granted_at_s = timestamp_s
        self._log(timestamp_s, "grant", consent)
        return consent

    def activate_consent(self, consent_id: str, timestamp_s: float,
                         ethics_approval_id: str | None = None) -> Consent | None:
        """Activate consent (after ethics review for Channel B)."""
        consent = self._consents.get(consent_id)
        if consent is None or consent.status != "granted":
            return None

        if consent.channel == "B" and not ethics_approval_id:
            return None  # Channel B requires ethics approval

        consent.status = "active"
        consent.activated_at_s = timestamp_s
        consent.expires_at_s = timestamp_s + (consent.retention_days * 86400)
        consent.ethics_approved = ethics_approval_id is not None
        consent.ethics_approval_id = ethics_approval_id
        self._log(timestamp_s, "activate", consent)
        return consent

    def withdraw_consent(self, consent_id: str, timestamp_s: float) -> Consent | None:
        """Patient withdraws consent. Triggers mandatory burn for Channel B."""
        consent = self._consents.get(consent_id)
        if consent is None or consent.status in ("withdrawn",):
            return None
        consent.status = "withdrawn"
        consent.withdrawn_at_s = timestamp_s
        self._log(timestamp_s, "withdraw", consent)
        return consent

    def get_active_consents(self, patient_id: str) -> list[Consent]:
        """Get all active consents for a patient."""
        ids = self._by_patient.get(patient_id, [])
        return [
            self._consents[cid] for cid in ids
            if cid in self._consents and self._consents[cid].status == "active"
        ]

    def get_consent(self, consent_id: str) -> Consent | None:
        return self._consents.get(consent_id)

    def check_expirations(self, current_time_s: float) -> list[Consent]:
        """Check for expired consents. Returns list of newly expired."""
        expired = []
        for consent in self._consents.values():
            if (consent.status == "active"
                    and consent.expires_at_s is not None
                    and current_time_s >= consent.expires_at_s):
                consent.status = "withdrawn"
                consent.withdrawn_at_s = current_time_s
                expired.append(consent)
                self._log(current_time_s, "expired", consent)
        return expired

    def _store(self, consent: Consent) -> None:
        self._consents[consent.consent_id] = consent
        if consent.patient_id not in self._by_patient:
            self._by_patient[consent.patient_id] = []
        self._by_patient[consent.patient_id].append(consent.consent_id)
        if consent.programme_id not in self._by_programme:
            self._by_programme[consent.programme_id] = []
        self._by_programme[consent.programme_id].append(consent.consent_id)

    def _log(self, timestamp_s: float, action: str, consent: Consent) -> None:
        self._audit.append({
            "timestamp_s": timestamp_s,
            "action": action,
            "consent_id": consent.consent_id,
            "patient_id": consent.patient_id,
            "programme_id": consent.programme_id,
            "channel": consent.channel,
        })

    @property
    def stats(self) -> dict[str, Any]:
        statuses = {}
        for c in self._consents.values():
            statuses[c.status] = statuses.get(c.status, 0) + 1
        return {
            "total_consents": len(self._consents),
            "by_status": statuses,
            "patients": len(self._by_patient),
            "programmes": len(self._by_programme),
        }
