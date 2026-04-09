"""Layer 2: Device-to-Transmitter — BLE/RF telemetry to bedside monitor or smartphone app."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np


class TransmitterState(Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    TRANSMITTING = "transmitting"
    RETRYING = "retrying"
    FAILED = "failed"
    POWERED_OFF = "powered_off"


@dataclass
class TransmitterConfig:
    protocol: str = "BLE_4.2"  # BLE_4.2, RF_PROPRIETARY
    range_meters: float = 3.0
    session_timeout_s: float = 900.0  # 15 minutes max
    max_retries: int = 5
    base_retry_delay_s: float = 60.0  # Exponential backoff base
    failure_probability: float = 0.02  # 2% chance of transmission failure


class Transmitter:
    """Layer 2: Simulates transmission from implanted device to bedside/phone.

    Models:
    - BLE or RF connection establishment
    - Data transfer with configurable failure rate
    - Cache-until-uploaded behavior
    - Exponential backoff on failure
    """

    def __init__(self, config: TransmitterConfig | None = None, rng: np.random.Generator | None = None) -> None:
        self.config = config or TransmitterConfig()
        self.rng = rng or np.random.default_rng()
        self._state = TransmitterState.IDLE
        self._cache: list[dict[str, Any]] = []
        self._retry_count = 0
        self._next_retry_time_s = 0.0
        self._total_transmissions = 0
        self._total_failures = 0
        self._total_bytes_transmitted = 0
        self._last_successful_upload_s = 0.0

    def receive_from_device(self, data: dict[str, Any], timestamp_s: float) -> None:
        """Receive data from the implanted device (Layer 1)."""
        self._cache.append({
            "data": data,
            "received_at": timestamp_s,
            "size_bytes": data.get("size_bytes", 500),
        })

    def attempt_upload(self, timestamp_s: float) -> TransmitResult:
        """Attempt to upload cached data to the cloud (Layer 3).

        Returns a TransmitResult with the outcome.
        """
        if self._state == TransmitterState.POWERED_OFF:
            return TransmitResult(
                success=False,
                state=self._state,
                message="Transmitter powered off",
            )

        if not self._cache:
            return TransmitResult(
                success=True,
                state=TransmitterState.IDLE,
                message="No data to transmit",
            )

        # Check if we're in retry backoff
        if self._state == TransmitterState.RETRYING:
            if timestamp_s < self._next_retry_time_s:
                return TransmitResult(
                    success=False,
                    state=self._state,
                    message=f"In retry backoff until {self._next_retry_time_s:.0f}s",
                )

        self._state = TransmitterState.CONNECTING

        # Simulate connection + transmission
        if self.rng.random() < self.config.failure_probability:
            # Transmission failed
            self._total_failures += 1
            self._retry_count += 1

            if self._retry_count > self.config.max_retries:
                self._state = TransmitterState.FAILED
                return TransmitResult(
                    success=False,
                    state=self._state,
                    message=f"Max retries ({self.config.max_retries}) exceeded",
                )

            # Exponential backoff
            backoff = self.config.base_retry_delay_s * (2 ** (self._retry_count - 1))
            self._next_retry_time_s = timestamp_s + backoff
            self._state = TransmitterState.RETRYING
            return TransmitResult(
                success=False,
                state=self._state,
                message=f"Transmission failed, retry #{self._retry_count} in {backoff:.0f}s",
            )

        # Success
        self._state = TransmitterState.TRANSMITTING
        total_bytes = sum(item["size_bytes"] for item in self._cache)
        transmitted_data = list(self._cache)

        self._cache.clear()
        self._retry_count = 0
        self._total_transmissions += 1
        self._total_bytes_transmitted += total_bytes
        self._last_successful_upload_s = timestamp_s
        self._state = TransmitterState.IDLE

        return TransmitResult(
            success=True,
            state=self._state,
            data=transmitted_data,
            bytes_transmitted=total_bytes,
            message="Upload successful",
        )

    def power_off(self) -> None:
        """Simulate transmitter power off. Data accumulates on device."""
        self._state = TransmitterState.POWERED_OFF

    def power_on(self) -> None:
        """Restore transmitter power."""
        self._state = TransmitterState.IDLE
        self._retry_count = 0

    @property
    def state(self) -> TransmitterState:
        return self._state

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    @property
    def cache_bytes(self) -> int:
        return sum(item["size_bytes"] for item in self._cache)

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "cache_items": len(self._cache),
            "cache_bytes": self.cache_bytes,
            "total_transmissions": self._total_transmissions,
            "total_failures": self._total_failures,
            "total_bytes_transmitted": self._total_bytes_transmitted,
            "failure_rate": (
                self._total_failures / (self._total_transmissions + self._total_failures)
                if (self._total_transmissions + self._total_failures) > 0 else 0.0
            ),
            "last_successful_upload_s": self._last_successful_upload_s,
        }


@dataclass
class TransmitResult:
    """Result of a transmission attempt."""
    success: bool
    state: TransmitterState
    data: list[dict[str, Any]] | None = None
    bytes_transmitted: int = 0
    message: str = ""
