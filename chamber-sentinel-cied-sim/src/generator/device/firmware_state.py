"""
Firmware version tracking for CIED pulse generators.

Maintains an ordered history of firmware versions applied to the device,
with semantic-version validation and timestamp recording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z\-]+(?:\.[0-9A-Za-z\-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z\-]+(?:\.[0-9A-Za-z\-]+)*))?$"
)


def _parse_version(version: str) -> tuple[int, int, int]:
    """
    Parse a version string into a ``(major, minor, patch)`` tuple.

    Raises
    ------
    ValueError
        If *version* does not conform to semantic versioning.
    """
    m = _SEMVER_RE.match(version)
    if m is None:
        raise ValueError(
            f"Invalid firmware version string: {version!r}. "
            "Expected semantic versioning (e.g. '1.2.3')."
        )
    return int(m.group("major")), int(m.group("minor")), int(m.group("patch"))


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class FirmwareState:
    """
    Tracks the firmware version of a CIED device over time.

    Parameters
    ----------
    initial_version:
        The firmware version installed at device manufacture / initial
        programming.  Must be a valid semantic version string.
    """

    def __init__(self, initial_version: str = "1.0.0") -> None:
        _parse_version(initial_version)  # validate
        self._current_version: str = initial_version
        # History stores (version, timestamp) pairs.  The initial version
        # is recorded at timestamp 0.0.
        self._history: list[tuple[str, float]] = [(initial_version, 0.0)]

    # -- Public API --------------------------------------------------------

    def update(self, new_version: str, timestamp: float) -> None:
        """
        Apply a firmware update.

        Parameters
        ----------
        new_version:
            New version string (semantic versioning).
        timestamp:
            Simulation timestamp (seconds) at which the update is applied.

        Raises
        ------
        ValueError
            If *new_version* is not a valid semantic version, if the
            timestamp is not monotonically increasing, or if the new
            version is not strictly greater than the current version.
        """
        new_parsed = _parse_version(new_version)
        current_parsed = _parse_version(self._current_version)

        if new_parsed <= current_parsed:
            raise ValueError(
                f"Firmware version must increase: current={self._current_version}, "
                f"requested={new_version}"
            )

        if self._history and timestamp <= self._history[-1][1]:
            raise ValueError(
                f"Firmware update timestamp must be strictly increasing: "
                f"last={self._history[-1][1]}, requested={timestamp}"
            )

        self._current_version = new_version
        self._history.append((new_version, timestamp))

    def get_version(self) -> str:
        """Return the current firmware version string."""
        return self._current_version

    def get_history(self) -> list[tuple[str, float]]:
        """
        Return the full firmware update history.

        Returns
        -------
        list[tuple[str, float]]
            List of ``(version, timestamp)`` pairs in chronological order.
        """
        return list(self._history)

    def get_version_tuple(self) -> tuple[int, int, int]:
        """Return the current firmware version as a ``(major, minor, patch)`` tuple."""
        return _parse_version(self._current_version)

    def __repr__(self) -> str:
        return f"FirmwareState(version={self._current_version!r}, updates={len(self._history) - 1})"
