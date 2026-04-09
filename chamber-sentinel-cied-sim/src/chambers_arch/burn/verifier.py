"""Burn verification — cryptographic and audit-based verification of data destruction."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BurnCertificate:
    """Certificate attesting to the destruction of a data element."""
    certificate_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    burn_id: str = ""
    record_id: str = ""
    world: str = ""
    patient_id: str = ""
    burned_at_s: float = 0.0
    size_bytes_destroyed: int = 0
    verification_method: str = ""
    proof: str = ""  # Cryptographic proof or audit reference
    merkle_root_before: str = ""
    merkle_root_after: str = ""
    auditor: str = "system"


class CryptographicDeletion:
    """Approach 1: Encrypt data with per-record key, burn = destroy key.

    Each record is encrypted with a unique AES-256 key.
    Burning means destroying the key — data becomes unrecoverable.
    Verification: key destruction is an auditable event.
    """

    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}  # record_id -> key hash (simulated)
        self._destroyed_keys: dict[str, float] = {}  # record_id -> destruction timestamp

    def register_record(self, record_id: str) -> str:
        """Generate and store an encryption key for a record."""
        # Simulated: in production this would be a real AES-256 key
        key_hash = hashlib.sha256(f"key-{record_id}-{uuid.uuid4()}".encode()).hexdigest()
        self._keys[record_id] = key_hash.encode()
        return key_hash

    def destroy_key(self, record_id: str, timestamp_s: float) -> bool:
        """Destroy the encryption key, making data unrecoverable."""
        if record_id in self._keys:
            del self._keys[record_id]
            self._destroyed_keys[record_id] = timestamp_s
            return True
        return False

    def is_recoverable(self, record_id: str) -> bool:
        """Check if a record's data is still recoverable (key exists)."""
        return record_id in self._keys

    def get_destruction_proof(self, record_id: str) -> dict[str, Any]:
        """Get proof of key destruction."""
        if record_id in self._destroyed_keys:
            return {
                "record_id": record_id,
                "key_destroyed": True,
                "destroyed_at_s": self._destroyed_keys[record_id],
                "method": "cryptographic_deletion",
                "proof_hash": hashlib.sha256(f"destroyed-{record_id}".encode()).hexdigest(),
            }
        return {"record_id": record_id, "key_destroyed": False}


class MerkleTreeVerifier:
    """Approach 2: Merkle tree verification of data removal.

    Data elements tracked in a Merkle tree.
    Burn = remove element + update tree root.
    Verification: prove non-inclusion in updated tree.
    """

    def __init__(self) -> None:
        self._leaves: dict[str, str] = {}  # record_id -> leaf hash
        self._root: str = self._compute_root()
        self._history: list[tuple[str, str, float]] = []  # (action, record_id, timestamp)

    def add_leaf(self, record_id: str) -> str:
        """Add a record to the Merkle tree."""
        leaf_hash = hashlib.sha256(f"leaf-{record_id}".encode()).hexdigest()
        self._leaves[record_id] = leaf_hash
        old_root = self._root
        self._root = self._compute_root()
        self._history.append(("add", record_id, 0.0))
        return self._root

    def remove_leaf(self, record_id: str, timestamp_s: float) -> tuple[str, str]:
        """Remove a record from the tree. Returns (old_root, new_root)."""
        old_root = self._root
        if record_id in self._leaves:
            del self._leaves[record_id]
        self._root = self._compute_root()
        self._history.append(("remove", record_id, timestamp_s))
        return old_root, self._root

    def prove_non_inclusion(self, record_id: str) -> dict[str, Any]:
        """Prove that a record is NOT in the current tree."""
        return {
            "record_id": record_id,
            "included": record_id in self._leaves,
            "current_root": self._root,
            "tree_size": len(self._leaves),
        }

    def _compute_root(self) -> str:
        """Compute the Merkle root from current leaves."""
        if not self._leaves:
            return hashlib.sha256(b"empty").hexdigest()

        hashes = sorted(self._leaves.values())
        while len(hashes) > 1:
            next_level = []
            for i in range(0, len(hashes), 2):
                if i + 1 < len(hashes):
                    combined = hashlib.sha256(
                        (hashes[i] + hashes[i + 1]).encode()
                    ).hexdigest()
                else:
                    combined = hashes[i]
                next_level.append(combined)
            hashes = next_level
        return hashes[0]


class AuditVerifier:
    """Approach 3: Audit-based verification with tamper-evident timestamps.

    Independent auditor verifies burn execution.
    Periodic audits of storage backends.
    Verification: third-party attestation.
    """

    def __init__(self) -> None:
        self._audit_log: list[dict[str, Any]] = []
        self._chain_hash = hashlib.sha256(b"genesis").hexdigest()

    def record_burn(self, record_id: str, world: str, timestamp_s: float,
                    size_bytes: int = 0) -> dict[str, Any]:
        """Record a burn event with tamper-evident chaining."""
        entry = {
            "entry_id": str(uuid.uuid4()),
            "record_id": record_id,
            "world": world,
            "burned_at_s": timestamp_s,
            "size_bytes": size_bytes,
            "prev_hash": self._chain_hash,
        }
        # Chain hash includes previous hash for tamper evidence
        entry_str = f"{entry['entry_id']}-{record_id}-{timestamp_s}-{self._chain_hash}"
        entry["hash"] = hashlib.sha256(entry_str.encode()).hexdigest()
        self._chain_hash = entry["hash"]

        self._audit_log.append(entry)
        return entry

    def verify_chain_integrity(self) -> bool:
        """Verify the audit log chain hasn't been tampered with."""
        if not self._audit_log:
            return True

        expected_prev = hashlib.sha256(b"genesis").hexdigest()
        for entry in self._audit_log:
            if entry["prev_hash"] != expected_prev:
                return False
            entry_str = f"{entry['entry_id']}-{entry['record_id']}-{entry['burned_at_s']}-{entry['prev_hash']}"
            expected_hash = hashlib.sha256(entry_str.encode()).hexdigest()
            if entry["hash"] != expected_hash:
                return False
            expected_prev = entry["hash"]
        return True

    def get_attestation(self, record_id: str) -> dict[str, Any] | None:
        """Get audit attestation for a specific record's burn."""
        for entry in self._audit_log:
            if entry["record_id"] == record_id:
                return {
                    "record_id": record_id,
                    "burned": True,
                    "burned_at_s": entry["burned_at_s"],
                    "audit_entry_id": entry["entry_id"],
                    "chain_integrity": self.verify_chain_integrity(),
                    "method": "audit_attestation",
                }
        return None


class BurnVerifier:
    """Unified burn verifier combining all three approaches."""

    def __init__(self) -> None:
        self.crypto = CryptographicDeletion()
        self.merkle = MerkleTreeVerifier()
        self.audit = AuditVerifier()

    def on_record_created(self, record_id: str) -> None:
        """Register a new record across all verification systems."""
        self.crypto.register_record(record_id)
        self.merkle.add_leaf(record_id)

    def on_record_burned(self, record_id: str, world: str,
                         timestamp_s: float, size_bytes: int = 0) -> BurnCertificate:
        """Record a burn across all verification systems. Returns certificate."""
        # Cryptographic deletion
        self.crypto.destroy_key(record_id, timestamp_s)

        # Merkle tree update
        root_before, root_after = self.merkle.remove_leaf(record_id, timestamp_s)

        # Audit log
        self.audit.record_burn(record_id, world, timestamp_s, size_bytes)

        # Generate certificate
        return BurnCertificate(
            record_id=record_id,
            world=world,
            burned_at_s=timestamp_s,
            size_bytes_destroyed=size_bytes,
            verification_method="all_three",
            proof=self.crypto.get_destruction_proof(record_id).get("proof_hash", ""),
            merkle_root_before=root_before,
            merkle_root_after=root_after,
        )

    def verify_burn(self, record_id: str) -> dict[str, Any]:
        """Verify that a record has been burned using all methods."""
        return {
            "record_id": record_id,
            "crypto": not self.crypto.is_recoverable(record_id),
            "merkle": not self.merkle.prove_non_inclusion(record_id)["included"],
            "audit": self.audit.get_attestation(record_id) is not None,
            "chain_integrity": self.audit.verify_chain_integrity(),
            "fully_verified": (
                not self.crypto.is_recoverable(record_id)
                and not self.merkle.prove_non_inclusion(record_id)["included"]
                and self.audit.get_attestation(record_id) is not None
            ),
        }

    def inject_burn_failure(self, record_id: str) -> None:
        """Intentionally fail to burn a record (for testing).

        Leaves the key intact in crypto, leaf in merkle,
        but records in audit as if burned — creating a verifiable inconsistency.
        """
        # Only record in audit, don't actually destroy
        self.audit.record_burn(record_id, "test", 0.0)
        # crypto key and merkle leaf remain — verification will show inconsistency
