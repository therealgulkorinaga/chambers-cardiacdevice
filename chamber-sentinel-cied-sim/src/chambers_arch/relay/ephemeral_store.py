"""TTL-based ephemeral store — wrapper around in-memory storage with mandatory TTL."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class EphemeralEntry:
    """A single entry in the ephemeral store."""
    key: str
    value: Any
    created_at_s: float
    expires_at_s: float
    size_bytes: int = 0

    def is_expired(self, current_time_s: float) -> bool:
        return current_time_s >= self.expires_at_s


class EphemeralStore:
    """In-memory store where every key MUST have a TTL.

    Models Redis with TTL-based key expiration for the relay.
    No persistence to disk — memory-only.
    """

    def __init__(self, default_ttl_s: int = 259200) -> None:
        self.default_ttl_s = default_ttl_s
        self._store: dict[str, EphemeralEntry] = {}
        self._total_sets = 0
        self._total_gets = 0
        self._total_expired = 0
        self._total_bytes = 0

    def set(self, key: str, value: Any, ttl_s: int | None = None,
            timestamp_s: float = 0.0, size_bytes: int = 100) -> None:
        """Set a key with mandatory TTL."""
        actual_ttl = ttl_s if ttl_s is not None else self.default_ttl_s
        if actual_ttl <= 0:
            raise ValueError("TTL must be positive. No TTL-less keys allowed.")

        entry = EphemeralEntry(
            key=key,
            value=value,
            created_at_s=timestamp_s,
            expires_at_s=timestamp_s + actual_ttl,
            size_bytes=size_bytes,
        )

        # If replacing, subtract old size
        if key in self._store:
            self._total_bytes -= self._store[key].size_bytes

        self._store[key] = entry
        self._total_sets += 1
        self._total_bytes += size_bytes

    def get(self, key: str, current_time_s: float = 0.0) -> Any | None:
        """Get a value. Returns None if expired or not found."""
        self._total_gets += 1
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry.is_expired(current_time_s):
            self._evict(key)
            return None
        return entry.value

    def delete(self, key: str) -> bool:
        """Explicitly delete a key."""
        if key in self._store:
            self._evict(key)
            return True
        return False

    def _evict(self, key: str) -> None:
        """Remove a key from the store."""
        if key in self._store:
            self._total_bytes -= self._store[key].size_bytes
            del self._store[key]
            self._total_expired += 1

    def cleanup_expired(self, current_time_s: float) -> int:
        """Remove all expired entries. Returns count removed."""
        expired_keys = [
            key for key, entry in self._store.items()
            if entry.is_expired(current_time_s)
        ]
        for key in expired_keys:
            self._evict(key)
        return len(expired_keys)

    def suspend_ttl(self, key: str) -> bool:
        """Suspend TTL for a key (safety investigation hold)."""
        entry = self._store.get(key)
        if entry:
            entry.expires_at_s = float("inf")
            return True
        return False

    def restore_ttl(self, key: str, new_ttl_s: int | None = None,
                    current_time_s: float = 0.0) -> bool:
        """Restore TTL after hold release."""
        entry = self._store.get(key)
        if entry:
            ttl = new_ttl_s if new_ttl_s is not None else self.default_ttl_s
            entry.expires_at_s = current_time_s + ttl
            return True
        return False

    @property
    def item_count(self) -> int:
        return len(self._store)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    def get_oldest_age_s(self, current_time_s: float) -> float:
        """Age of the oldest item in the store."""
        if not self._store:
            return 0.0
        oldest = min(e.created_at_s for e in self._store.values())
        return current_time_s - oldest

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "item_count": len(self._store),
            "total_bytes": self._total_bytes,
            "total_mb": self._total_bytes / (1024 * 1024),
            "total_sets": self._total_sets,
            "total_gets": self._total_gets,
            "total_expired": self._total_expired,
            "default_ttl_s": self.default_ttl_s,
            "default_ttl_hours": self.default_ttl_s / 3600,
        }
