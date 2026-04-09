"""Runtime template library for openCARP-generated EGM waveforms.

Loads pre-computed .npy beat templates and template_catalog.json at runtime.
No openCARP dependency is required -- this module works exclusively with
the artefacts produced by :mod:`template_generator`.

Templates are memory-mapped for efficiency and cached after first access.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mapping from RhythmState enum values to template directory names
# ---------------------------------------------------------------------------

_RHYTHM_STATE_TO_TEMPLATE: dict[str, str] = {
    # Enum .value strings -> template directory keys
    "normal_sinus_rhythm": "nsr",
    "sinus_bradycardia": "sinus_bradycardia",
    "sinus_tachycardia": "sinus_tachycardia",
    "atrial_fibrillation": "atrial_fibrillation",
    "atrial_flutter": "atrial_flutter",
    "supraventricular_tachycardia": "svt",
    "ventricular_tachycardia": "ventricular_tachycardia",
    "ventricular_fibrillation": "ventricular_fibrillation",
    "complete_heart_block": "complete_heart_block",
    "mobitz_type_i": "mobitz_i",
    "mobitz_type_ii": "mobitz_ii",
    "premature_ventricular_complex": "pvc",
    "premature_atrial_complex": "pac",
    "junctional_rhythm": "junctional",
    "paced_aai": "paced_aai",
    "paced_vvi": "paced_vvi",
    "paced_ddd": "paced_ddd",
    "paced_crt": "paced_crt",
    # Also accept the short-form keys directly
    "nsr": "nsr",
    "svt": "svt",
    "mobitz_i": "mobitz_i",
    "mobitz_ii": "mobitz_ii",
    "pvc": "pvc",
    "pac": "pac",
    "junctional": "junctional",
}

# Enum .name strings (uppercase) -> template directory keys
_RHYTHM_NAME_TO_TEMPLATE: dict[str, str] = {
    "NSR": "nsr",
    "SINUS_BRADYCARDIA": "sinus_bradycardia",
    "SINUS_TACHYCARDIA": "sinus_tachycardia",
    "ATRIAL_FIBRILLATION": "atrial_fibrillation",
    "ATRIAL_FLUTTER": "atrial_flutter",
    "SVT": "svt",
    "VENTRICULAR_TACHYCARDIA": "ventricular_tachycardia",
    "VENTRICULAR_FIBRILLATION": "ventricular_fibrillation",
    "COMPLETE_HEART_BLOCK": "complete_heart_block",
    "MOBITZ_I": "mobitz_i",
    "MOBITZ_II": "mobitz_ii",
    "PVC": "pvc",
    "PAC": "pac",
    "JUNCTIONAL": "junctional",
    "PACED_AAI": "paced_aai",
    "PACED_VVI": "paced_vvi",
    "PACED_DDD": "paced_ddd",
    "PACED_CRT": "paced_crt",
}


class TemplateLibrary:
    """Runtime loader for pre-computed EGM waveform templates.

    Reads ``.npy`` files produced by :class:`~template_generator.TemplateGenerator`
    and provides random beat selection with optional amplitude/duration variation.

    Templates are memory-mapped (``np.load(..., mmap_mode='r')``) to keep
    resident memory low when the template pool is large, and cached after
    first load.

    Parameters:
        template_dir: Path to the directory containing per-rhythm subdirectories
            and ``template_catalog.json``.
    """

    def __init__(
        self,
        template_dir: str = "src/generator/cardiac/opencarp/templates",
    ) -> None:
        self._template_dir = Path(template_dir)
        self._catalog: dict | None = None
        # Cache: { (rhythm, channel) : np.ndarray of shape (n_beats, n_samples) }
        self._cache: dict[tuple[str, str], np.ndarray] = {}

    # ------------------------------------------------------------------
    # Catalog access
    # ------------------------------------------------------------------

    def load_catalog(self) -> dict:
        """Parse and return ``template_catalog.json``.

        The catalog is cached after first load.

        Returns:
            Full catalog dictionary.

        Raises:
            FileNotFoundError: If the catalog file does not exist.
        """
        if self._catalog is not None:
            return self._catalog

        catalog_path = self._template_dir / "template_catalog.json"
        if not catalog_path.exists():
            raise FileNotFoundError(
                f"Template catalog not found at {catalog_path}. "
                "Run TemplateGenerator.generate_all() first."
            )

        with open(catalog_path) as f:
            self._catalog = json.load(f)

        logger.info(
            "Loaded template catalog: %d rhythms, method=%s",
            len(self._catalog.get("rhythms", {})),
            self._catalog.get("generation_method", "unknown"),
        )
        return self._catalog

    # ------------------------------------------------------------------
    # Rhythm name resolution
    # ------------------------------------------------------------------

    def _resolve_rhythm(self, rhythm: str) -> str:
        """Map any rhythm identifier to the canonical template directory name.

        Accepts:
        - RhythmState enum ``.value`` strings (e.g. ``"normal_sinus_rhythm"``)
        - RhythmState enum ``.name`` strings (e.g. ``"NSR"``)
        - Direct template keys (e.g. ``"nsr"``)
        - RhythmState enum instances (via ``str()``)

        Returns:
            Canonical template directory name (e.g. ``"nsr"``).

        Raises:
            KeyError: If the rhythm cannot be resolved.
        """
        # Handle enum objects
        rhythm_str = str(rhythm)

        # Try .value mapping first
        if rhythm_str in _RHYTHM_STATE_TO_TEMPLATE:
            return _RHYTHM_STATE_TO_TEMPLATE[rhythm_str]

        # Try .name mapping (uppercase)
        upper = rhythm_str.upper()
        if upper in _RHYTHM_NAME_TO_TEMPLATE:
            return _RHYTHM_NAME_TO_TEMPLATE[upper]

        # Try direct (already a template key)
        catalog = self.load_catalog()
        if rhythm_str in catalog.get("rhythms", {}):
            return rhythm_str

        # Try lowercase
        lower = rhythm_str.lower()
        if lower in catalog.get("rhythms", {}):
            return lower

        raise KeyError(
            f"Cannot resolve rhythm '{rhythm}' to a template directory. "
            f"Available: {list(catalog.get('rhythms', {}).keys())}"
        )

    # ------------------------------------------------------------------
    # Beat loading (memory-mapped, cached)
    # ------------------------------------------------------------------

    def _load_channel_beats(self, rhythm: str, channel: str) -> np.ndarray:
        """Load all beat templates for a (rhythm, channel) pair.

        Results are cached so subsequent calls return the same array.

        Returns:
            ndarray of shape ``(n_beats, n_samples)``.
        """
        key = (rhythm, channel)
        if key in self._cache:
            return self._cache[key]

        rhythm_dir = self._template_dir / rhythm
        if not rhythm_dir.is_dir():
            raise FileNotFoundError(
                f"Template directory not found: {rhythm_dir}"
            )

        # Discover .npy files for this channel
        pattern = f"{channel}_beat_*.npy"
        npy_files = sorted(rhythm_dir.glob(pattern))

        if not npy_files:
            raise FileNotFoundError(
                f"No .npy files matching '{pattern}' in {rhythm_dir}"
            )

        # Memory-map each file and stack into a single array
        beats: list[np.ndarray] = []
        for npy_path in npy_files:
            try:
                beat = np.load(npy_path, mmap_mode="r")
                beats.append(np.asarray(beat))  # materialize from mmap into contiguous array
            except Exception as exc:
                logger.warning("Failed to load %s: %s", npy_path, exc)

        if not beats:
            raise RuntimeError(
                f"All .npy files failed to load for ({rhythm}, {channel})."
            )

        stacked = np.stack(beats, axis=0)
        self._cache[key] = stacked

        logger.debug(
            "Loaded %d beats for (%s, %s), shape=%s",
            stacked.shape[0], rhythm, channel, stacked.shape,
        )
        return stacked

    # ------------------------------------------------------------------
    # Public beat retrieval API
    # ------------------------------------------------------------------

    def get_beat(
        self,
        rhythm: str,
        channel: str,
        rng: np.random.Generator | None = None,
    ) -> np.ndarray:
        """Select a random beat template and apply small natural variation.

        The returned beat has +/-5% amplitude variation and +/-3% duration
        variation applied on top of the stored template.

        Parameters:
            rhythm: Rhythm identifier (enum value, enum name, or template key).
            channel: Channel name (``"atrial"``, ``"ventricular"``, ``"shock"``).
            rng: Optional random generator for reproducibility.

        Returns:
            1-D ndarray of voltage samples.
        """
        if rng is None:
            rng = np.random.default_rng()

        resolved = self._resolve_rhythm(rhythm)
        beats = self._load_channel_beats(resolved, channel)

        # Pick a random beat
        idx = int(rng.integers(0, beats.shape[0]))
        beat = beats[idx].copy()

        # Apply amplitude variation (+/- 5%)
        amp_scale = 1.0 + rng.normal(0.0, 0.05)
        beat *= amp_scale

        # Apply duration variation (+/- 3%) via resampling
        duration_scale = 1.0 + rng.normal(0.0, 0.03)
        new_len = max(1, int(round(len(beat) * duration_scale)))
        if new_len != len(beat) and new_len > 1:
            x_old = np.linspace(0, 1, len(beat))
            x_new = np.linspace(0, 1, new_len)
            beat = np.interp(x_new, x_old, beat)

        return beat

    def get_beat_multichannel(
        self,
        rhythm: str,
        rng: np.random.Generator | None = None,
    ) -> dict[str, np.ndarray]:
        """Retrieve time-aligned beats for all available channels.

        The same beat index and the same amplitude/duration variation are
        applied to all channels so they remain temporally consistent.

        Parameters:
            rhythm: Rhythm identifier.
            rng: Optional random generator for reproducibility.

        Returns:
            Dict mapping channel name to 1-D voltage array.
        """
        if rng is None:
            rng = np.random.default_rng()

        resolved = self._resolve_rhythm(rhythm)
        catalog = self.load_catalog()
        rhythm_meta = catalog.get("rhythms", {}).get(resolved, {})
        channels = rhythm_meta.get("channels", ["atrial", "ventricular", "shock"])

        # Determine common beat index and variation parameters
        # Load first channel to find n_beats
        first_beats = self._load_channel_beats(resolved, channels[0])
        idx = int(rng.integers(0, first_beats.shape[0]))
        amp_scale = 1.0 + rng.normal(0.0, 0.05)
        duration_scale = 1.0 + rng.normal(0.0, 0.03)

        result: dict[str, np.ndarray] = {}
        for ch in channels:
            beats = self._load_channel_beats(resolved, ch)
            # Use same index, clamped to available beats for this channel
            ch_idx = min(idx, beats.shape[0] - 1)
            beat = beats[ch_idx].copy()

            # Apply consistent variation
            beat *= amp_scale
            new_len = max(1, int(round(len(beat) * duration_scale)))
            if new_len != len(beat) and new_len > 1:
                x_old = np.linspace(0, 1, len(beat))
                x_new = np.linspace(0, 1, new_len)
                beat = np.interp(x_new, x_old, beat)

            result[ch] = beat

        return result

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if the template directory has loadable templates.

        Checks for the catalog file and at least one rhythm subdirectory
        with .npy files.
        """
        catalog_path = self._template_dir / "template_catalog.json"
        if not catalog_path.exists():
            return False

        # Check at least one rhythm directory has .npy files
        try:
            catalog = self.load_catalog()
        except Exception:
            return False

        for rhythm_name in catalog.get("rhythms", {}):
            rhythm_dir = self._template_dir / rhythm_name
            if rhythm_dir.is_dir() and any(rhythm_dir.glob("*.npy")):
                return True

        return False

    def get_rhythm_names(self) -> list[str]:
        """Return the list of available rhythm template names.

        Returns:
            Sorted list of rhythm keys that have templates on disk.
        """
        try:
            catalog = self.load_catalog()
        except FileNotFoundError:
            return []

        available: list[str] = []
        for rhythm_name in catalog.get("rhythms", {}):
            rhythm_dir = self._template_dir / rhythm_name
            if rhythm_dir.is_dir() and any(rhythm_dir.glob("*.npy")):
                available.append(rhythm_name)

        return sorted(available)

    def get_stats(self) -> dict:
        """Return summary statistics about the template library.

        Returns:
            Dictionary with keys:
            - ``total_templates``: Total number of individual .npy beat files.
            - ``rhythms``: Number of rhythm types with templates.
            - ``memory_cached_bytes``: Approximate memory used by cached arrays.
            - ``disk_bytes``: Total size of .npy files on disk.
            - ``catalog_generation_method``: How templates were generated.
        """
        total_templates = 0
        disk_bytes = 0
        rhythm_count = 0

        try:
            catalog = self.load_catalog()
        except FileNotFoundError:
            return {
                "total_templates": 0,
                "rhythms": 0,
                "memory_cached_bytes": 0,
                "disk_bytes": 0,
                "catalog_generation_method": "none",
            }

        for rhythm_name in catalog.get("rhythms", {}):
            rhythm_dir = self._template_dir / rhythm_name
            if not rhythm_dir.is_dir():
                continue
            npy_files = list(rhythm_dir.glob("*.npy"))
            if npy_files:
                rhythm_count += 1
                total_templates += len(npy_files)
                for npy_path in npy_files:
                    try:
                        disk_bytes += npy_path.stat().st_size
                    except OSError:
                        pass

        # Compute cached memory
        memory_cached = 0
        for arr in self._cache.values():
            memory_cached += arr.nbytes

        return {
            "total_templates": total_templates,
            "rhythms": rhythm_count,
            "memory_cached_bytes": memory_cached,
            "disk_bytes": disk_bytes,
            "catalog_generation_method": catalog.get("generation_method", "unknown"),
        }

    def clear_cache(self) -> None:
        """Clear all cached beat arrays to free memory."""
        self._cache.clear()
        self._catalog = None
        logger.info("Template library cache cleared.")
