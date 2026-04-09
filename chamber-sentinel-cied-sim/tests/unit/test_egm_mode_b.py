"""Integration tests comparing Mode A (parametric) and Mode B (openCARP) EGM synthesis.

Mode A is the existing parametric waveform generation in ``EGMSynthesizer``.
Mode B adds an ``"opencarp"`` mode that pulls beat templates from a
``TemplateLibrary`` and adapts them via an ``IonicAdapter`` before overlaying
pacing artifacts and noise.

These tests verify that both modes coexist, produce compatible output formats,
and that Mode B gracefully degrades when templates are unavailable.
"""

from __future__ import annotations

import json
import logging
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pytest
from scipy import signal as sp_signal

from src.generator.cardiac.egm_synthesizer import EGMStrip, EGMSynthesizer
from src.generator.cardiac.rhythm_engine import RhythmState


# ---------------------------------------------------------------------------
# Lightweight template library + ionic adapter stubs for Mode B testing
# ---------------------------------------------------------------------------

CHANNEL_NAMES = ["atrial", "ventricular", "shock"]
SOURCE_RATE_HZ = 1000


def _make_synthetic_beat(
    n_samples: int = 800,
    amplitude_mv: float = 1.5,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Create a single synthetic beat waveform (1-D)."""
    if rng is None:
        rng = np.random.default_rng(0)
    t = np.linspace(0.0, 1.0, n_samples, dtype=np.float64)
    beat = amplitude_mv * np.exp(-0.5 * ((t - 0.35) / 0.06) ** 2)
    beat -= 0.2 * amplitude_mv * np.exp(-0.5 * ((t - 0.45) / 0.04) ** 2)
    beat += 0.3 * amplitude_mv * np.exp(-0.5 * ((t - 0.6) / 0.08) ** 2)
    beat += rng.normal(0.0, 0.005, size=n_samples)
    return beat


class _TestTemplateLibrary:
    """Minimal template library used as a test fixture for Mode B."""

    def __init__(self, templates_dir: Path) -> None:
        self._dir = templates_dir
        self._catalog: Dict[str, Any] | None = None
        self._cache: Dict[str, np.ndarray] = {}

    def is_available(self) -> bool:
        catalog_path = self._dir / "template_catalog.json"
        if not catalog_path.exists():
            return False
        return any(self._dir.glob("*/*.npy")) or any(self._dir.glob("*.npy"))

    def load_catalog(self) -> Dict[str, Any]:
        with open(self._dir / "template_catalog.json") as f:
            self._catalog = json.load(f)
        return self._catalog

    def get_beat(
        self,
        rhythm_name: str,
        channel: str = "ventricular",
        beat_index: int = 0,
        rng: np.random.Generator | None = None,
        amplitude_variation_pct: float = 5.0,
    ) -> np.ndarray:
        key = f"{rhythm_name}/{channel}"
        if key not in self._cache:
            npy_path = self._dir / rhythm_name / f"{channel}.npy"
            if not npy_path.exists():
                raise KeyError(f"Template not found: {npy_path}")
            self._cache[key] = np.load(str(npy_path))
        beats = self._cache[key]
        idx = beat_index % beats.shape[0]
        beat = beats[idx].astype(np.float64).copy()
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
        result: Dict[str, np.ndarray] = {}
        for ch in CHANNEL_NAMES:
            result[ch] = self.get_beat(
                rhythm_name, channel=ch, beat_index=beat_index,
                rng=rng, amplitude_variation_pct=amplitude_variation_pct,
            )
        return result

    def list_rhythms(self) -> List[str]:
        if self._catalog is None:
            self.load_catalog()
        assert self._catalog is not None
        return sorted(self._catalog.get("rhythms", {}).keys())


class _IonicAdapter:
    """Minimal adapter for resampling + time-stretching template beats."""

    def __init__(self, source_rate_hz: int = 1000, target_rate_hz: int = 256) -> None:
        self._source_rate = source_rate_hz
        self._target_rate = target_rate_hz

    def resample(self, beat: np.ndarray) -> np.ndarray:
        n_target = max(1, int(round(len(beat) * self._target_rate / self._source_rate)))
        return sp_signal.resample(beat, n_target).astype(np.float64)

    def time_stretch(self, beat: np.ndarray, target_samples: int) -> np.ndarray:
        return sp_signal.resample(beat, max(1, target_samples)).astype(np.float64)

    def adapt_beat(
        self, beat: np.ndarray, target_rr_samples: int, channel_gain: float = 1.0,
    ) -> np.ndarray:
        resampled = self.resample(beat)
        stretched = self.time_stretch(resampled, target_rr_samples)
        return stretched * channel_gain

    def adapt_multichannel(
        self,
        beats: Dict[str, np.ndarray],
        target_rr_samples: int,
        channel_gains: Dict[str, float] | None = None,
    ) -> Dict[str, np.ndarray]:
        if channel_gains is None:
            channel_gains = {"atrial": 3.0, "ventricular": 1.0, "shock": 0.6}
        return {
            ch: self.adapt_beat(w, target_rr_samples, channel_gains.get(ch, 1.0))
            for ch, w in beats.items()
        }


# ---------------------------------------------------------------------------
# Extended EGMSynthesizer with Mode B support (test double)
# ---------------------------------------------------------------------------


class _EGMSynthesizerModeB(EGMSynthesizer):
    """Subclass that adds opencarp mode on top of the existing parametric synthesizer.

    This mirrors the planned production changes: the real EGMSynthesizer will
    accept ``mode="opencarp"`` and ``template_library`` parameters.  By
    subclassing here, we can test the integration contract without modifying
    production code inside the test suite.
    """

    def __init__(
        self,
        sample_rate_hz: int = 256,
        noise_floor_mv: float = 0.1,
        rng: np.random.Generator | None = None,
        mode: str = "parametric",
        template_library: _TestTemplateLibrary | None = None,
        ionic_adapter: _IonicAdapter | None = None,
    ) -> None:
        super().__init__(
            sample_rate_hz=sample_rate_hz,
            noise_floor_mv=noise_floor_mv,
            rng=rng,
        )
        self._mode = mode
        self._template_library = template_library
        self._ionic_adapter = ionic_adapter or _IonicAdapter(
            source_rate_hz=SOURCE_RATE_HZ, target_rate_hz=sample_rate_hz,
        )

        # Validate mode B availability; fall back if needed
        if self._mode == "opencarp":
            if self._template_library is None or not self._template_library.is_available():
                warnings.warn(
                    "openCARP templates unavailable; falling back to parametric mode.",
                    UserWarning,
                    stacklevel=2,
                )
                self._mode = "parametric"

    @property
    def mode(self) -> str:
        return self._mode

    # --- override synthesize_beat to dispatch on mode ---

    def synthesize_beat(  # type: ignore[override]
        self,
        rhythm_state: RhythmState,
        is_paced: bool = False,
        pacing_channels: Optional[List[str]] = None,
        conduction_delay_ms: float = 160.0,
        rr_interval_ms: float = 800.0,
        pacing_artifact_amplitude_mv: float = 4.0,
    ) -> Dict[str, np.ndarray]:
        if self._mode == "opencarp":
            return self._synthesize_beat_opencarp(
                rhythm_state=rhythm_state,
                is_paced=is_paced,
                pacing_channels=pacing_channels or [],
                conduction_delay_ms=conduction_delay_ms,
                rr_interval_ms=rr_interval_ms,
                pacing_artifact_amplitude_mv=pacing_artifact_amplitude_mv,
            )
        # Default: parametric (Mode A)
        return super().synthesize_beat(
            rhythm_state=rhythm_state,
            is_paced=is_paced,
            pacing_channels=pacing_channels,
            conduction_delay_ms=conduction_delay_ms,
            rr_interval_ms=rr_interval_ms,
            pacing_artifact_amplitude_mv=pacing_artifact_amplitude_mv,
        )

    def _synthesize_beat_opencarp(
        self,
        rhythm_state: RhythmState,
        is_paced: bool,
        pacing_channels: List[str],
        conduction_delay_ms: float,
        rr_interval_ms: float,
        pacing_artifact_amplitude_mv: float,
    ) -> Dict[str, np.ndarray]:
        """Mode B: build beat from openCARP template + adapt + artifacts."""
        assert self._template_library is not None
        total_samples = max(1, int(round(rr_interval_ms * self._sample_rate / 1000.0)))
        rhythm_name = rhythm_state.value

        # Retrieve multichannel template beat
        mc = self._template_library.get_beat_multichannel(
            rhythm_name, beat_index=0, rng=self._rng, amplitude_variation_pct=5.0,
        )

        # Channel mapping: template channel -> output key
        channel_map = {
            "atrial": "atrial_egm",
            "ventricular": "ventricular_egm",
            "shock": "shock_channel",
        }
        gains = {"atrial": 3.0, "ventricular": 1.0, "shock": 0.6}

        result: Dict[str, np.ndarray] = {}
        for src_ch, out_key in channel_map.items():
            adapted = self._ionic_adapter.adapt_beat(
                mc[src_ch], target_rr_samples=total_samples,
                channel_gain=gains.get(src_ch, 1.0),
            )
            result[out_key] = adapted

        # Overlay pacing artifacts if paced
        if is_paced:
            from src.generator.cardiac.waveform_models import generate_pacing_artifact

            if "atrial" in pacing_channels:
                artifact = generate_pacing_artifact(
                    pacing_artifact_amplitude_mv, self._sample_rate,
                )
                self._overlay(result["atrial_egm"], artifact, start_sample=0)

            if "ventricular" in pacing_channels:
                artifact = generate_pacing_artifact(
                    pacing_artifact_amplitude_mv, self._sample_rate,
                )
                qrs_onset_sample = int(round(conduction_delay_ms * self._sample_rate / 1000.0))
                qrs_onset_sample = min(qrs_onset_sample, total_samples - 1)
                art_start = max(0, qrs_onset_sample - len(artifact))
                self._overlay(result["ventricular_egm"], artifact, start_sample=art_start)
                self._overlay(
                    result["shock_channel"], artifact * 0.7, start_sample=art_start,
                )

        return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _create_test_templates(base_dir: Path, rhythms: List[RhythmState]) -> Path:
    """Populate *base_dir* with synthetic templates and return the path."""
    rng = np.random.default_rng(42)
    catalog: Dict[str, Any] = {"version": "1.0", "source_rate_hz": SOURCE_RATE_HZ, "rhythms": {}}

    for rs in rhythms:
        rhythm_name = rs.value
        rhythm_dir = base_dir / rhythm_name
        rhythm_dir.mkdir(parents=True, exist_ok=True)

        n_beats, n_samples = 5, 800
        ch_gains = {"atrial": 0.3, "ventricular": 1.5, "shock": 0.5}

        base_beats = np.stack(
            [_make_synthetic_beat(n_samples, 1.0, rng) for _ in range(n_beats)]
        )
        for ch_name, gain in ch_gains.items():
            np.save(str(rhythm_dir / f"{ch_name}.npy"), base_beats * gain)

        catalog["rhythms"][rhythm_name] = {
            "n_beats": n_beats,
            "samples_per_beat": n_samples,
            "channels": list(ch_gains.keys()),
        }

    with open(base_dir / "template_catalog.json", "w") as f:
        json.dump(catalog, f, indent=2)

    return base_dir


@pytest.fixture(scope="session")
def mode_b_templates_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session-scoped directory with templates for all 18 rhythms."""
    base = tmp_path_factory.mktemp("mode_b_templates")
    return _create_test_templates(base, list(RhythmState))


@pytest.fixture
def mode_b_template_library(mode_b_templates_dir: Path) -> _TestTemplateLibrary:
    lib = _TestTemplateLibrary(mode_b_templates_dir)
    lib.load_catalog()
    return lib


@pytest.fixture
def synth_mode_a() -> EGMSynthesizer:
    """Mode A (parametric) synthesizer with fixed seed."""
    return EGMSynthesizer(sample_rate_hz=256, noise_floor_mv=0.1, rng=np.random.default_rng(1))


@pytest.fixture
def synth_mode_b(mode_b_template_library: _TestTemplateLibrary) -> _EGMSynthesizerModeB:
    """Mode B (openCARP) synthesizer with fixed seed."""
    return _EGMSynthesizerModeB(
        sample_rate_hz=256,
        noise_floor_mv=0.1,
        rng=np.random.default_rng(1),
        mode="opencarp",
        template_library=mode_b_template_library,
    )


# ---------------------------------------------------------------------------
# Standard beat parameters used across tests
# ---------------------------------------------------------------------------

_BEAT_PARAMS: Dict[str, Any] = {
    "rhythm_state": RhythmState.NSR,
    "is_paced": False,
    "rr_interval_ms": 800.0,
}

_STRIP_BEATS = [
    {"rhythm_state": RhythmState.NSR, "rr_interval_ms": 800.0},
    {"rhythm_state": RhythmState.NSR, "rr_interval_ms": 820.0},
    {"rhythm_state": RhythmState.NSR, "rr_interval_ms": 790.0},
]

EXPECTED_CHANNEL_KEYS = {"atrial_egm", "ventricular_egm", "shock_channel"}


# ===================================================================
# Tests
# ===================================================================


class TestEGMModeADefault:
    """Verify the default synthesizer behaves as Mode A (parametric)."""

    def test_mode_a_default(self) -> None:
        """EGMSynthesizer() defaults to parametric mode (no mode attr needed)."""
        synth = EGMSynthesizer()
        # The base class does not expose a ``mode`` attribute; its behaviour
        # *is* Mode A.  We verify by confirming it can synthesize without any
        # template library.
        beat = synth.synthesize_beat(RhythmState.NSR, rr_interval_ms=800.0)
        assert isinstance(beat, dict)
        assert set(beat.keys()) == EXPECTED_CHANNEL_KEYS


class TestModeBWithTemplates:
    """Mode B synthesizer backed by a template library."""

    def test_mode_b_with_templates(
        self, synth_mode_b: _EGMSynthesizerModeB
    ) -> None:
        """Constructing with mode='opencarp' + template_library succeeds."""
        assert synth_mode_b.mode == "opencarp"
        beat = synth_mode_b.synthesize_beat(RhythmState.NSR, rr_interval_ms=800.0)
        assert isinstance(beat, dict)
        assert set(beat.keys()) == EXPECTED_CHANNEL_KEYS


class TestModeBFallback:
    """Mode B falls back to parametric when templates are absent."""

    def test_mode_b_fallback_without_templates(self, tmp_path: Path) -> None:
        """If template_library is None or unavailable, Mode B falls back to
        parametric with a warning."""
        with pytest.warns(UserWarning, match="falling back to parametric"):
            synth = _EGMSynthesizerModeB(
                mode="opencarp",
                template_library=None,
                rng=np.random.default_rng(1),
            )
        assert synth.mode == "parametric"

        # It should still produce valid output via Mode A
        beat = synth.synthesize_beat(RhythmState.NSR, rr_interval_ms=800.0)
        assert set(beat.keys()) == EXPECTED_CHANNEL_KEYS

    def test_mode_b_fallback_empty_dir(self, tmp_path: Path) -> None:
        """An empty template directory triggers the same fallback."""
        lib = _TestTemplateLibrary(tmp_path)
        assert not lib.is_available()
        with pytest.warns(UserWarning, match="falling back to parametric"):
            synth = _EGMSynthesizerModeB(
                mode="opencarp",
                template_library=lib,
                rng=np.random.default_rng(1),
            )
        assert synth.mode == "parametric"


class TestBothModesSameOutputFormat:
    """Both modes produce dict with identical keys."""

    def test_both_modes_same_output_format(
        self,
        synth_mode_a: EGMSynthesizer,
        synth_mode_b: _EGMSynthesizerModeB,
    ) -> None:
        beat_a = synth_mode_a.synthesize_beat(**_BEAT_PARAMS)
        beat_b = synth_mode_b.synthesize_beat(**_BEAT_PARAMS)

        assert set(beat_a.keys()) == EXPECTED_CHANNEL_KEYS
        assert set(beat_b.keys()) == EXPECTED_CHANNEL_KEYS

        # All channels should be 1-D float arrays
        for key in EXPECTED_CHANNEL_KEYS:
            assert beat_a[key].ndim == 1
            assert beat_b[key].ndim == 1
            assert beat_a[key].dtype == np.float64
            assert beat_b[key].dtype == np.float64


class TestBothModesSameStripFormat:
    """synthesize_strip produces EGMStrip in both modes."""

    def test_both_modes_same_strip_format(
        self,
        synth_mode_a: EGMSynthesizer,
        synth_mode_b: _EGMSynthesizerModeB,
    ) -> None:
        duration_ms = 3000
        strip_a = synth_mode_a.synthesize_strip(
            beats=_STRIP_BEATS, duration_ms=duration_ms,
        )
        strip_b = synth_mode_b.synthesize_strip(
            beats=_STRIP_BEATS, duration_ms=duration_ms,
        )

        for strip in (strip_a, strip_b):
            assert isinstance(strip, EGMStrip)
            assert set(strip.channels.keys()) == EXPECTED_CHANNEL_KEYS
            assert strip.sample_rate_hz == 256
            assert strip.duration_ms == duration_ms
            assert isinstance(strip.annotations, list)
            assert isinstance(strip.strip_id, str)


class TestModeBWaveformsDiffer:
    """The actual waveform data should differ between modes."""

    def test_mode_b_waveforms_differ_from_mode_a(
        self,
        synth_mode_a: EGMSynthesizer,
        synth_mode_b: _EGMSynthesizerModeB,
    ) -> None:
        # Use identical RNG seeds so the only difference is the synthesis method
        synth_a = EGMSynthesizer(
            sample_rate_hz=256, noise_floor_mv=0.0, rng=np.random.default_rng(99),
        )
        # Re-create mode B with same seed
        synth_b = _EGMSynthesizerModeB(
            sample_rate_hz=256,
            noise_floor_mv=0.0,
            rng=np.random.default_rng(99),
            mode="opencarp",
            template_library=synth_mode_b._template_library,
        )

        beat_a = synth_a.synthesize_beat(RhythmState.NSR, rr_interval_ms=800.0)
        beat_b = synth_b.synthesize_beat(RhythmState.NSR, rr_interval_ms=800.0)

        # At least one channel should differ in waveform shape
        any_differ = False
        for key in EXPECTED_CHANNEL_KEYS:
            # Same length (same RR), but different samples
            assert beat_a[key].shape == beat_b[key].shape
            if not np.allclose(beat_a[key], beat_b[key], atol=1e-6):
                any_differ = True

        assert any_differ, "Mode A and Mode B should produce different waveforms"


class TestModeBRespectsRhythmState:
    """Different rhythm states produce different templates in Mode B."""

    def test_mode_b_respects_rhythm_state(
        self, synth_mode_b: _EGMSynthesizerModeB
    ) -> None:
        beat_nsr = synth_mode_b.synthesize_beat(
            RhythmState.NSR, rr_interval_ms=800.0,
        )
        beat_vt = synth_mode_b.synthesize_beat(
            RhythmState.VENTRICULAR_TACHYCARDIA, rr_interval_ms=400.0,
        )

        # Different RR -> different length
        assert beat_nsr["ventricular_egm"].shape[0] != beat_vt["ventricular_egm"].shape[0]

        # Even if we force same length, the waveform shape should differ
        beat_nsr_same = synth_mode_b.synthesize_beat(
            RhythmState.NSR, rr_interval_ms=600.0,
        )
        beat_af_same = synth_mode_b.synthesize_beat(
            RhythmState.ATRIAL_FIBRILLATION, rr_interval_ms=600.0,
        )
        # Same number of samples but different content (different underlying template)
        assert beat_nsr_same["ventricular_egm"].shape == beat_af_same["ventricular_egm"].shape
        assert not np.allclose(
            beat_nsr_same["ventricular_egm"], beat_af_same["ventricular_egm"], atol=1e-6,
        )


class TestModeBPacingArtifacts:
    """Pacing artifacts should be overlaid on openCARP templates."""

    def test_mode_b_pacing_artifacts_added(
        self, synth_mode_b: _EGMSynthesizerModeB
    ) -> None:
        beat_unpaced = synth_mode_b.synthesize_beat(
            RhythmState.PACED_DDD,
            is_paced=False,
            rr_interval_ms=800.0,
        )
        beat_paced = synth_mode_b.synthesize_beat(
            RhythmState.PACED_DDD,
            is_paced=True,
            pacing_channels=["atrial", "ventricular"],
            rr_interval_ms=800.0,
            pacing_artifact_amplitude_mv=4.0,
        )

        # The paced version should have a larger peak on the atrial channel
        # (because pacing spikes are high-amplitude)
        peak_unpaced_atrial = np.max(np.abs(beat_unpaced["atrial_egm"]))
        peak_paced_atrial = np.max(np.abs(beat_paced["atrial_egm"]))
        assert peak_paced_atrial > peak_unpaced_atrial, (
            "Pacing artifact should increase the peak amplitude of atrial EGM"
        )

        # The ventricular channel should differ due to the pacing spike overlay
        diff_vent = np.max(np.abs(beat_paced["ventricular_egm"] - beat_unpaced["ventricular_egm"]))
        assert diff_vent > 0.1, (
            "Pacing artifact should visibly modify the ventricular EGM waveform"
        )


class TestModeBNoiseAdded:
    """Noise model should be applied to openCARP templates in strip mode."""

    def test_mode_b_noise_added(
        self, mode_b_template_library: _TestTemplateLibrary
    ) -> None:
        # Create synthesizer with non-zero noise
        synth_noisy = _EGMSynthesizerModeB(
            sample_rate_hz=256,
            noise_floor_mv=0.5,
            rng=np.random.default_rng(77),
            mode="opencarp",
            template_library=mode_b_template_library,
        )
        # Create synthesizer with zero noise for comparison
        synth_clean = _EGMSynthesizerModeB(
            sample_rate_hz=256,
            noise_floor_mv=0.0,
            rng=np.random.default_rng(77),
            mode="opencarp",
            template_library=mode_b_template_library,
        )

        beats_spec = [
            {"rhythm_state": RhythmState.NSR, "rr_interval_ms": 800.0},
            {"rhythm_state": RhythmState.NSR, "rr_interval_ms": 810.0},
        ]
        duration_ms = 2000

        strip_noisy = synth_noisy.synthesize_strip(beats=beats_spec, duration_ms=duration_ms)
        strip_clean = synth_clean.synthesize_strip(beats=beats_spec, duration_ms=duration_ms)

        for ch_key in EXPECTED_CHANNEL_KEYS:
            noisy_sig = strip_noisy.channels[ch_key]
            clean_sig = strip_clean.channels[ch_key]
            # The noisy signal should differ from the clean one
            diff = np.abs(noisy_sig - clean_sig)
            rms_diff = np.sqrt(np.mean(diff ** 2))
            assert rms_diff > 0.01, (
                f"Noise should be apparent on {ch_key}; RMS diff = {rms_diff:.6f}"
            )
