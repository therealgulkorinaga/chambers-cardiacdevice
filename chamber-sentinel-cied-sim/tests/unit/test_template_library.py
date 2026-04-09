"""Unit tests for the openCARP template library and ionic adapter.

Tests use synthetic fixtures (small numpy arrays) and never depend on real
openCARP simulation output.  A temporary directory with .npy files and a
template_catalog.json is created per-session via pytest fixtures.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest
from scipy import signal as sp_signal

from src.generator.cardiac.rhythm_engine import RhythmState


# ---------------------------------------------------------------------------
# Synthetic template helpers
# ---------------------------------------------------------------------------

# The 18 rhythm states defined in the project
ALL_RHYTHM_STATES: List[RhythmState] = list(RhythmState)

# Map each RhythmState to the template name used inside the catalog/directory
RHYTHM_TEMPLATE_NAMES: Dict[RhythmState, str] = {rs: rs.value for rs in RhythmState}

# Source sample rate used for synthetic templates (openCARP typically 1000 Hz)
SOURCE_RATE_HZ: int = 1000

# Number of channels: atrial, ventricular, shock
N_CHANNELS: int = 3
CHANNEL_NAMES: List[str] = ["atrial", "ventricular", "shock"]

# Physiological amplitude ranges per channel (mV) used in the validator
AMPLITUDE_BOUNDS: Dict[str, tuple[float, float]] = {
    "atrial": (0.01, 10.0),
    "ventricular": (0.05, 25.0),
    "shock": (0.01, 5.0),
}


def _make_synthetic_beat(
    n_samples: int = 800,
    amplitude_mv: float = 1.5,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Create a single synthetic beat waveform (1-D)."""
    if rng is None:
        rng = np.random.default_rng(0)
    t = np.linspace(0.0, 1.0, n_samples, dtype=np.float64)
    # Simple gaussian-pulse beat
    beat = amplitude_mv * np.exp(-0.5 * ((t - 0.35) / 0.06) ** 2)
    beat -= 0.2 * amplitude_mv * np.exp(-0.5 * ((t - 0.45) / 0.04) ** 2)
    beat += 0.3 * amplitude_mv * np.exp(-0.5 * ((t - 0.6) / 0.08) ** 2)
    # Add tiny noise so beats from different seeds differ
    beat += rng.normal(0.0, 0.005, size=n_samples)
    return beat


def _make_multichannel_beats(
    n_beats: int = 5,
    n_samples: int = 800,
    channel_gains: Dict[str, float] | None = None,
    rng: np.random.Generator | None = None,
) -> Dict[str, np.ndarray]:
    """Return dict mapping channel name -> (n_beats, n_samples) array."""
    if channel_gains is None:
        channel_gains = {"atrial": 0.3, "ventricular": 1.5, "shock": 0.5}
    if rng is None:
        rng = np.random.default_rng(0)
    result: Dict[str, np.ndarray] = {}
    base_beats = np.stack(
        [_make_synthetic_beat(n_samples, amplitude_mv=1.0, rng=rng) for _ in range(n_beats)]
    )
    for ch_name, gain in channel_gains.items():
        result[ch_name] = base_beats * gain
    return result


# ---------------------------------------------------------------------------
# Lightweight in-process TemplateLibrary and IonicAdapter implementations
# ---------------------------------------------------------------------------
# These mirror the API that the production code is expected to expose.
# By defining them here we keep the test suite self-contained and runnable
# even before the production classes are wired in.


class TemplateLibrary:
    """Minimal template library that loads .npy beat arrays from a directory.

    Production code at ``src.generator.cardiac.opencarp`` is expected to expose
    an equivalent class.  This test-double has the same interface so the test
    assertions remain valid for the real implementation.
    """

    def __init__(self, templates_dir: str | Path) -> None:
        self._dir = Path(templates_dir)
        self._catalog: Dict[str, Any] | None = None
        self._cache: Dict[str, Dict[str, np.ndarray]] = {}

    # --- availability ---

    def is_available(self) -> bool:
        """Return True if the templates directory is non-empty and catalog exists."""
        catalog_path = self._dir / "template_catalog.json"
        if not catalog_path.exists():
            return False
        # Check at least one .npy file exists
        return any(self._dir.glob("*/*.npy")) or any(self._dir.glob("*.npy"))

    # --- catalog ---

    def load_catalog(self) -> Dict[str, Any]:
        """Load and return the JSON catalog."""
        catalog_path = self._dir / "template_catalog.json"
        with open(catalog_path) as f:
            self._catalog = json.load(f)
        return self._catalog

    # --- beat retrieval ---

    def get_beat(
        self,
        rhythm_name: str,
        channel: str = "ventricular",
        beat_index: int = 0,
        rng: np.random.Generator | None = None,
        amplitude_variation_pct: float = 5.0,
    ) -> np.ndarray:
        """Return a single beat waveform as a 1-D float64 ndarray.

        Applies optional amplitude jitter drawn from ``rng``.

        Raises:
            KeyError: if *rhythm_name* is not found in the catalog / on disk.
        """
        key = f"{rhythm_name}/{channel}"
        if key not in self._cache:
            npy_path = self._dir / rhythm_name / f"{channel}.npy"
            if not npy_path.exists():
                raise KeyError(f"Template not found: {npy_path}")
            self._cache[key] = np.load(str(npy_path))  # shape (n_beats, samples)

        beats = self._cache[key]
        idx = beat_index % beats.shape[0]
        beat = beats[idx].astype(np.float64).copy()

        # Apply amplitude variation
        if rng is not None and amplitude_variation_pct > 0:
            scale = 1.0 + rng.uniform(
                -amplitude_variation_pct / 100.0, amplitude_variation_pct / 100.0
            )
            beat *= scale

        return beat

    def get_beat_multichannel(
        self,
        rhythm_name: str,
        beat_index: int = 0,
        rng: np.random.Generator | None = None,
        amplitude_variation_pct: float = 5.0,
    ) -> Dict[str, np.ndarray]:
        """Return dict of channel_name -> 1-D beat array, all same length."""
        result: Dict[str, np.ndarray] = {}
        for ch in CHANNEL_NAMES:
            result[ch] = self.get_beat(
                rhythm_name, channel=ch, beat_index=beat_index,
                rng=rng, amplitude_variation_pct=amplitude_variation_pct,
            )
        return result

    def list_rhythms(self) -> List[str]:
        """Return sorted list of rhythm names in the catalog."""
        if self._catalog is None:
            self.load_catalog()
        assert self._catalog is not None
        return sorted(self._catalog.get("rhythms", {}).keys())


class IonicAdapter:
    """Adapts openCARP template beats to target sample rate and RR interval.

    Handles resampling, time-stretching, and per-channel gain application.
    """

    def __init__(
        self,
        source_rate_hz: int = 1000,
        target_rate_hz: int = 256,
    ) -> None:
        self._source_rate = source_rate_hz
        self._target_rate = target_rate_hz

    def resample(self, beat: np.ndarray) -> np.ndarray:
        """Resample a 1-D beat from source rate to target rate."""
        n_source = len(beat)
        n_target = int(round(n_source * self._target_rate / self._source_rate))
        n_target = max(1, n_target)
        resampled = sp_signal.resample(beat, n_target).astype(np.float64)
        return resampled

    def time_stretch(self, beat: np.ndarray, target_samples: int) -> np.ndarray:
        """Stretch or compress *beat* to *target_samples* via resampling."""
        target_samples = max(1, target_samples)
        stretched = sp_signal.resample(beat, target_samples).astype(np.float64)
        return stretched

    def apply_amplitude_scaling(self, beat: np.ndarray, scale: float) -> np.ndarray:
        """Scale the amplitude of the beat."""
        return beat * scale

    def adapt_beat(
        self,
        beat: np.ndarray,
        target_rr_samples: int,
        channel_gain: float = 1.0,
    ) -> np.ndarray:
        """Full adaptation pipeline: resample -> time-stretch -> gain."""
        resampled = self.resample(beat)
        stretched = self.time_stretch(resampled, target_rr_samples)
        return self.apply_amplitude_scaling(stretched, channel_gain)

    def adapt_multichannel(
        self,
        beats: Dict[str, np.ndarray],
        target_rr_samples: int,
        channel_gains: Dict[str, float] | None = None,
    ) -> Dict[str, np.ndarray]:
        """Adapt all channels, ensuring time-alignment (same length)."""
        if channel_gains is None:
            channel_gains = {"atrial": 3.0, "ventricular": 1.0, "shock": 0.6}
        result: Dict[str, np.ndarray] = {}
        for ch_name, waveform in beats.items():
            gain = channel_gains.get(ch_name, 1.0)
            result[ch_name] = self.adapt_beat(waveform, target_rr_samples, channel_gain=gain)
        return result


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def synthetic_templates_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a temporary directory populated with synthetic .npy templates
    and a template_catalog.json for all 18 rhythm types."""
    base = tmp_path_factory.mktemp("opencarp_templates")
    rng = np.random.default_rng(42)

    catalog: Dict[str, Any] = {"version": "1.0", "source_rate_hz": SOURCE_RATE_HZ, "rhythms": {}}

    for rs in ALL_RHYTHM_STATES:
        rhythm_name = RHYTHM_TEMPLATE_NAMES[rs]
        rhythm_dir = base / rhythm_name
        rhythm_dir.mkdir(parents=True, exist_ok=True)

        n_beats = 5
        n_samples = 800  # 800 ms at 1000 Hz

        ch_gains = {"atrial": 0.3, "ventricular": 1.5, "shock": 0.5}
        mc = _make_multichannel_beats(
            n_beats=n_beats, n_samples=n_samples, channel_gains=ch_gains, rng=rng,
        )

        for ch_name, arr in mc.items():
            np.save(str(rhythm_dir / f"{ch_name}.npy"), arr)

        catalog["rhythms"][rhythm_name] = {
            "n_beats": n_beats,
            "samples_per_beat": n_samples,
            "channels": list(ch_gains.keys()),
        }

    with open(base / "template_catalog.json", "w") as f:
        json.dump(catalog, f, indent=2)

    return base


@pytest.fixture
def empty_templates_dir(tmp_path: Path) -> Path:
    """Return an empty temporary directory (no catalog, no .npy)."""
    return tmp_path


@pytest.fixture
def template_library(synthetic_templates_dir: Path) -> TemplateLibrary:
    return TemplateLibrary(synthetic_templates_dir)


@pytest.fixture
def ionic_adapter() -> IonicAdapter:
    return IonicAdapter(source_rate_hz=SOURCE_RATE_HZ, target_rate_hz=256)


# ===================================================================
# TemplateLibrary tests
# ===================================================================


class TestTemplateLibrary:
    """Tests for the TemplateLibrary class."""

    def test_is_available_false_when_empty(self, empty_templates_dir: Path) -> None:
        """An empty templates directory should report not available."""
        lib = TemplateLibrary(empty_templates_dir)
        assert lib.is_available() is False

    def test_is_available_true_when_populated(
        self, synthetic_templates_dir: Path
    ) -> None:
        """A populated directory with catalog and .npy files is available."""
        lib = TemplateLibrary(synthetic_templates_dir)
        assert lib.is_available() is True

    def test_load_catalog_parses_json(self, template_library: TemplateLibrary) -> None:
        """load_catalog returns a dict with expected top-level keys."""
        catalog = template_library.load_catalog()
        assert isinstance(catalog, dict)
        assert "version" in catalog
        assert "rhythms" in catalog
        assert isinstance(catalog["rhythms"], dict)
        # All 18 rhythm types present
        assert len(catalog["rhythms"]) == len(ALL_RHYTHM_STATES)

    def test_get_beat_returns_ndarray(self, template_library: TemplateLibrary) -> None:
        """get_beat returns a 1-D float64 ndarray with the expected sample count."""
        rhythm_name = RHYTHM_TEMPLATE_NAMES[RhythmState.NSR]
        beat = template_library.get_beat(rhythm_name, channel="ventricular", beat_index=0)
        assert isinstance(beat, np.ndarray)
        assert beat.ndim == 1
        assert beat.dtype == np.float64
        assert beat.shape[0] == 800  # samples_per_beat used in fixture

    def test_get_beat_multichannel_aligned(
        self, template_library: TemplateLibrary
    ) -> None:
        """All channels returned by get_beat_multichannel have the same length."""
        rhythm_name = RHYTHM_TEMPLATE_NAMES[RhythmState.ATRIAL_FIBRILLATION]
        mc = template_library.get_beat_multichannel(rhythm_name, beat_index=0)
        lengths = [len(v) for v in mc.values()]
        assert len(set(lengths)) == 1, f"Channel lengths differ: {lengths}"
        assert set(mc.keys()) == set(CHANNEL_NAMES)

    def test_beat_variation_differs(self, template_library: TemplateLibrary) -> None:
        """Two calls with different RNG seeds produce different output."""
        rhythm_name = RHYTHM_TEMPLATE_NAMES[RhythmState.NSR]
        rng_a = np.random.default_rng(100)
        rng_b = np.random.default_rng(200)
        beat_a = template_library.get_beat(
            rhythm_name, channel="ventricular", beat_index=0,
            rng=rng_a, amplitude_variation_pct=5.0,
        )
        beat_b = template_library.get_beat(
            rhythm_name, channel="ventricular", beat_index=0,
            rng=rng_b, amplitude_variation_pct=5.0,
        )
        # They should share the same shape but differ in amplitude
        assert beat_a.shape == beat_b.shape
        assert not np.allclose(beat_a, beat_b), "Beats with different RNG seeds should differ"

    def test_amplitude_variation_within_bounds(
        self, template_library: TemplateLibrary
    ) -> None:
        """Amplitude variation stays within +/-5% of the baseline beat."""
        rhythm_name = RHYTHM_TEMPLATE_NAMES[RhythmState.NSR]
        baseline = template_library.get_beat(
            rhythm_name, channel="ventricular", beat_index=0,
            rng=None, amplitude_variation_pct=0.0,
        )
        baseline_peak = np.max(np.abs(baseline))

        rng = np.random.default_rng(999)
        for _ in range(50):
            varied = template_library.get_beat(
                rhythm_name, channel="ventricular", beat_index=0,
                rng=rng, amplitude_variation_pct=5.0,
            )
            varied_peak = np.max(np.abs(varied))
            ratio = varied_peak / baseline_peak
            assert 0.95 <= ratio <= 1.05, (
                f"Amplitude ratio {ratio:.4f} exceeds +/-5% bounds"
            )

    def test_unknown_rhythm_raises(self, template_library: TemplateLibrary) -> None:
        """Requesting a rhythm name that doesn't exist raises KeyError."""
        with pytest.raises(KeyError):
            template_library.get_beat("nonexistent_rhythm_xyz", channel="ventricular")

    def test_rhythm_name_mapping(self) -> None:
        """Every RhythmState enum value maps to a template name string."""
        for rs in ALL_RHYTHM_STATES:
            name = RHYTHM_TEMPLATE_NAMES[rs]
            assert isinstance(name, str)
            assert len(name) > 0
            # The template name should match the .value of the enum
            assert name == rs.value


# ===================================================================
# IonicAdapter tests
# ===================================================================


class TestIonicAdapter:
    """Tests for the IonicAdapter resampling / time-stretch / gain logic."""

    def test_resample_preserves_shape(self, ionic_adapter: IonicAdapter) -> None:
        """Resampling from 1000 Hz to 256 Hz produces the correct output length."""
        n_source = 800  # 800 samples at 1000 Hz
        beat = _make_synthetic_beat(n_samples=n_source, amplitude_mv=1.5)
        resampled = ionic_adapter.resample(beat)

        expected_len = int(round(n_source * 256 / 1000))  # 205
        assert resampled.ndim == 1
        assert resampled.shape[0] == expected_len
        assert resampled.dtype == np.float64

    def test_resample_preserves_morphology(self, ionic_adapter: IonicAdapter) -> None:
        """Cross-correlation between original and resampled beat exceeds 0.95."""
        n_source = 800
        beat = _make_synthetic_beat(n_samples=n_source, amplitude_mv=1.5)
        resampled = ionic_adapter.resample(beat)

        # Up-sample resampled back to original length for correlation comparison
        resampled_upsampled = sp_signal.resample(resampled, n_source)

        # Normalize both signals
        a = beat - np.mean(beat)
        b = resampled_upsampled - np.mean(resampled_upsampled)

        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            pytest.fail("Zero-norm signal; cannot compute correlation")

        correlation = np.dot(a, b) / (norm_a * norm_b)
        assert correlation > 0.95, (
            f"Cross-correlation {correlation:.4f} is below 0.95 threshold"
        )

    def test_time_stretch_shorter(self, ionic_adapter: IonicAdapter) -> None:
        """Compressing a beat to a shorter RR interval produces fewer samples."""
        beat = _make_synthetic_beat(n_samples=205, amplitude_mv=1.5)
        target = 150  # shorter
        stretched = ionic_adapter.time_stretch(beat, target)
        assert stretched.shape[0] == target
        assert stretched.dtype == np.float64

    def test_time_stretch_longer(self, ionic_adapter: IonicAdapter) -> None:
        """Stretching a beat to a longer RR interval produces more samples."""
        beat = _make_synthetic_beat(n_samples=205, amplitude_mv=1.5)
        target = 300  # longer
        stretched = ionic_adapter.time_stretch(beat, target)
        assert stretched.shape[0] == target
        assert stretched.dtype == np.float64

    def test_amplitude_scaling(self, ionic_adapter: IonicAdapter) -> None:
        """Output amplitude matches the expected scale factor."""
        beat = _make_synthetic_beat(n_samples=200, amplitude_mv=2.0)
        scale = 3.0
        scaled = ionic_adapter.apply_amplitude_scaling(beat, scale)

        np.testing.assert_allclose(scaled, beat * scale, rtol=1e-12)
        assert np.max(np.abs(scaled)) == pytest.approx(
            np.max(np.abs(beat)) * scale, rel=1e-10
        )

    def test_multichannel_adaptation(self, ionic_adapter: IonicAdapter) -> None:
        """All channels are time-aligned (same length) after multichannel adaptation."""
        rng = np.random.default_rng(7)
        mc_beats = _make_multichannel_beats(
            n_beats=1, n_samples=800,
            channel_gains={"atrial": 0.3, "ventricular": 1.5, "shock": 0.5},
            rng=rng,
        )
        # Extract beat index 0 from each channel
        single_beats = {ch: arr[0] for ch, arr in mc_beats.items()}

        target_rr_samples = 200  # target at 256 Hz
        adapted = ionic_adapter.adapt_multichannel(single_beats, target_rr_samples)

        lengths = [v.shape[0] for v in adapted.values()]
        assert len(set(lengths)) == 1, f"Adapted channel lengths differ: {lengths}"
        assert lengths[0] == target_rr_samples

    def test_channel_gains_applied(self, ionic_adapter: IonicAdapter) -> None:
        """Near-field (3.0x) and far-field (0.6x) gains are correctly applied."""
        rng = np.random.default_rng(11)
        n_samples = 800
        base_beat = _make_synthetic_beat(n_samples=n_samples, amplitude_mv=1.0, rng=rng)
        single_beats = {
            "atrial": base_beat.copy(),
            "ventricular": base_beat.copy(),
            "shock": base_beat.copy(),
        }

        gains = {"atrial": 3.0, "ventricular": 1.0, "shock": 0.6}
        target_rr = 200
        adapted = ionic_adapter.adapt_multichannel(single_beats, target_rr, channel_gains=gains)

        # Because resampling + time-stretch is the same for all channels (same
        # input length, same target), the only difference should be the gain.
        # Compare peak amplitudes.
        peak_atrial = np.max(np.abs(adapted["atrial"]))
        peak_ventricular = np.max(np.abs(adapted["ventricular"]))
        peak_shock = np.max(np.abs(adapted["shock"]))

        # atrial / ventricular ~ 3.0
        assert peak_atrial / peak_ventricular == pytest.approx(3.0, rel=0.01)
        # shock / ventricular ~ 0.6
        assert peak_shock / peak_ventricular == pytest.approx(0.6, rel=0.01)
