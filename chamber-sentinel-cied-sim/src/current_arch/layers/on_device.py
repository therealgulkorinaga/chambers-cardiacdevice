"""Layer 1: On-Device Storage — simulates pacemaker internal memory."""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StoredEpisode:
    """An episode stored in device memory."""
    episode_id: str
    episode_type: str  # AF, VT, VF, SVT, etc.
    timestamp_s: float
    duration_s: float
    max_rate_bpm: float
    priority: int  # Higher = more protected from overwrite (VT/VF=10, AF=3, PAC=1)
    egm_data: bytes | None = None  # Raw EGM strip (may be overwritten separately)
    egm_size_bytes: int = 0
    header_size_bytes: int = 64


@dataclass
class DeviceMemory:
    """Simulated device memory allocation."""
    total_bytes: int = 524288  # 512 KB default
    programming_bytes: int = 4096
    diagnostic_bytes: int = 8192
    episode_header_max: int = 200
    egm_max_bytes: int = 262144  # 256 KB for EGM storage
    histogram_bytes: int = 16384
    activity_bytes: int = 8192
    threshold_bytes: int = 4096
    system_log_bytes: int = 4096


class OnDeviceStorage:
    """Layer 1: Simulates the implanted pacemaker's internal memory.

    Implements FIFO overwrite with priority protection:
    - VT/VF episodes are protected from overwrite
    - AT/AF episodes are overwritten first when memory is full
    - EGM strips are stored separately and overwritten independently
    """

    def __init__(self, memory: DeviceMemory | None = None) -> None:
        self.memory = memory or DeviceMemory()

        # Episode log (FIFO with priority protection)
        self._episodes: deque[StoredEpisode] = deque()
        self._episode_headers_used = 0

        # EGM storage (FIFO, separate from headers)
        self._egm_used_bytes = 0

        # Current programming parameters
        self._programming: dict[str, Any] = {}

        # Diagnostic counters (rolling)
        self._diagnostics: dict[str, Any] = {
            "total_paced_beats_atrial": 0,
            "total_paced_beats_ventricular": 0,
            "total_sensed_beats_atrial": 0,
            "total_sensed_beats_ventricular": 0,
            "mode_switches": 0,
            "atp_deliveries": 0,
            "shock_deliveries": 0,
        }

        # Histogram data (rolling bins)
        self._histograms: dict[str, list[int]] = {
            "heart_rate_bins": [0] * 20,  # 10-beat bins from 30-230 bpm
            "atrial_pacing_pct_daily": [],
            "ventricular_pacing_pct_daily": [],
        }

        # Activity log (daily summaries)
        self._activity_log: deque[dict[str, Any]] = deque(maxlen=365)

        # Threshold test results
        self._threshold_tests: deque[dict[str, Any]] = deque(maxlen=10)

        # System log
        self._system_log: deque[dict[str, Any]] = deque(maxlen=100)

        # Tracking
        self._total_events_stored = 0
        self._total_overwrites = 0

    def store_episode(self, episode: StoredEpisode) -> bool:
        """Store an episode. Returns True if successful, False if overwrite needed."""
        overwrote = False

        # Check if episode header log is full
        if self._episode_headers_used >= self.memory.episode_header_max:
            # Find lowest priority episode to overwrite
            overwrote = self._overwrite_lowest_priority_episode()
            if not overwrote:
                # All episodes are high priority — overwrite oldest regardless
                self._episodes.popleft()
                self._episode_headers_used -= 1
                self._total_overwrites += 1

        # Check if EGM storage is full
        if episode.egm_data is not None:
            while (self._egm_used_bytes + episode.egm_size_bytes > self.memory.egm_max_bytes
                   and self._episodes):
                # Remove oldest EGM data (but keep header)
                for ep in self._episodes:
                    if ep.egm_data is not None:
                        self._egm_used_bytes -= ep.egm_size_bytes
                        ep.egm_data = None
                        break
                else:
                    break  # No more EGM data to free

            self._egm_used_bytes += episode.egm_size_bytes

        self._episodes.append(episode)
        self._episode_headers_used += 1
        self._total_events_stored += 1
        return not overwrote

    def _overwrite_lowest_priority_episode(self) -> bool:
        """Remove the lowest priority (oldest first) episode. Returns True if removed."""
        # Find lowest priority
        min_priority = float("inf")
        min_idx = -1
        for i, ep in enumerate(self._episodes):
            if ep.priority < min_priority:
                min_priority = ep.priority
                min_idx = i

        if min_idx >= 0 and min_priority < 10:  # Don't overwrite VT/VF (priority 10)
            removed = self._episodes[min_idx]
            del self._episodes[min_idx]
            self._episode_headers_used -= 1
            if removed.egm_data is not None:
                self._egm_used_bytes -= removed.egm_size_bytes
            self._total_overwrites += 1
            return True
        return False

    def store_programming(self, params: dict[str, Any]) -> None:
        """Store current programming parameters."""
        self._programming = dict(params)

    def update_diagnostics(self, updates: dict[str, Any]) -> None:
        """Update diagnostic counters."""
        for key, value in updates.items():
            if key in self._diagnostics:
                if isinstance(value, (int, float)):
                    self._diagnostics[key] += value
                else:
                    self._diagnostics[key] = value

    def store_activity_summary(self, day: int, summary: dict[str, Any]) -> None:
        """Store a daily activity summary."""
        self._activity_log.append({"day": day, **summary})

    def store_threshold_test(self, result: dict[str, Any]) -> None:
        """Store a threshold test result."""
        self._threshold_tests.append(result)

    def log_system_event(self, event: dict[str, Any]) -> None:
        """Store a system log entry."""
        self._system_log.append(event)

    def get_transmission_data(self, full: bool = False) -> dict[str, Any]:
        """Get data for transmission. If full=True, returns complete interrogation."""
        data: dict[str, Any] = {
            "programming": dict(self._programming),
            "diagnostics": dict(self._diagnostics),
            "episode_count": len(self._episodes),
        }

        if full:
            data["episodes"] = [
                {
                    "episode_id": ep.episode_id,
                    "episode_type": ep.episode_type,
                    "timestamp_s": ep.timestamp_s,
                    "duration_s": ep.duration_s,
                    "max_rate_bpm": ep.max_rate_bpm,
                    "has_egm": ep.egm_data is not None,
                    "egm_size_bytes": ep.egm_size_bytes,
                }
                for ep in self._episodes
            ]
            data["histograms"] = dict(self._histograms)
            data["activity_log"] = list(self._activity_log)
            data["threshold_tests"] = list(self._threshold_tests)

        return data

    def clear_transmitted_episodes(self) -> None:
        """After successful transmission, clear episode data that was sent.
        (In practice, devices keep data until overwritten, so this is optional.)
        """
        pass  # Realistic behavior: data stays until overwritten

    @property
    def memory_utilization(self) -> dict[str, Any]:
        episode_header_bytes = self._episode_headers_used * 64
        return {
            "total_bytes": self.memory.total_bytes,
            "episode_headers": {
                "used": self._episode_headers_used,
                "max": self.memory.episode_header_max,
                "bytes": episode_header_bytes,
            },
            "egm_storage": {
                "used_bytes": self._egm_used_bytes,
                "max_bytes": self.memory.egm_max_bytes,
                "utilization_pct": (self._egm_used_bytes / self.memory.egm_max_bytes * 100)
                if self.memory.egm_max_bytes > 0 else 0,
            },
            "total_events_stored": self._total_events_stored,
            "total_overwrites": self._total_overwrites,
        }

    @property
    def episodes(self) -> list[StoredEpisode]:
        return list(self._episodes)
