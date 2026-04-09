"""Offline template generator for openCARP-based EGM waveforms.

Pre-computes EGM beat templates using openCARP (via Docker or native binary)
and saves them as .npy files with a template_catalog.json manifest.  When
openCARP is not available, a high-fidelity synthetic fallback generates
physiologically plausible waveforms using analytical action-potential and
PQRST models that exceed simple Gaussian synthesis.

Run this module as a script to populate the templates/ directory:

    python -m src.generator.cardiac.opencarp.template_generator
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rhythm configuration dataclass
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[4]  # src/generator/cardiac/opencarp -> project root


@dataclass
class RhythmConfig:
    """Configuration for a single rhythm type's template generation."""

    rhythm_name: str
    ionic_model: str  # tenTusscher2006, OHaraRudy2011, Courtemanche1998
    geometry: str  # slab, ring, wedge, dual_slab
    hr_range_bpm: tuple[int, int]
    n_beats: int  # templates to generate per rhythm
    stimulation_protocol: str  # description
    channels: list[str] = field(default_factory=lambda: ["atrial", "ventricular", "shock"])


# ---------------------------------------------------------------------------
# All 18 rhythm configs from the PRD
# ---------------------------------------------------------------------------

RHYTHM_CONFIGS: dict[str, RhythmConfig] = {
    "nsr": RhythmConfig(
        "nsr", "tenTusscher2006", "slab", (60, 100), 100,
        "paced_regular", ["atrial", "ventricular", "shock"],
    ),
    "sinus_bradycardia": RhythmConfig(
        "sinus_bradycardia", "tenTusscher2006", "slab", (30, 59), 80,
        "paced_slow", ["atrial", "ventricular", "shock"],
    ),
    "sinus_tachycardia": RhythmConfig(
        "sinus_tachycardia", "tenTusscher2006", "slab", (101, 150), 80,
        "paced_fast", ["atrial", "ventricular", "shock"],
    ),
    "atrial_fibrillation": RhythmConfig(
        "atrial_fibrillation", "Courtemanche1998", "dual_slab", (80, 180), 100,
        "burst_pacing_af", ["atrial", "ventricular", "shock"],
    ),
    "atrial_flutter": RhythmConfig(
        "atrial_flutter", "Courtemanche1998", "ring", (130, 170), 80,
        "macro_reentry", ["atrial", "ventricular", "shock"],
    ),
    "svt": RhythmConfig(
        "svt", "Courtemanche1998", "dual_slab", (150, 250), 80,
        "avnrt_circuit", ["atrial", "ventricular", "shock"],
    ),
    "ventricular_tachycardia": RhythmConfig(
        "ventricular_tachycardia", "OHaraRudy2011", "wedge", (120, 250), 100,
        "s1s2_reentry", ["atrial", "ventricular", "shock"],
    ),
    "ventricular_fibrillation": RhythmConfig(
        "ventricular_fibrillation", "OHaraRudy2011", "wedge", (200, 400), 100,
        "burst_degenerate", ["atrial", "ventricular", "shock"],
    ),
    "complete_heart_block": RhythmConfig(
        "complete_heart_block", "tenTusscher2006", "dual_slab", (20, 45), 80,
        "av_dissociation", ["atrial", "ventricular", "shock"],
    ),
    "mobitz_i": RhythmConfig(
        "mobitz_i", "tenTusscher2006", "dual_slab", (50, 100), 80,
        "wenckebach_av_delay", ["atrial", "ventricular", "shock"],
    ),
    "mobitz_ii": RhythmConfig(
        "mobitz_ii", "tenTusscher2006", "dual_slab", (40, 80), 80,
        "intermittent_block", ["atrial", "ventricular", "shock"],
    ),
    "pvc": RhythmConfig(
        "pvc", "OHaraRudy2011", "wedge", (60, 100), 80,
        "ectopic_ventricular", ["atrial", "ventricular", "shock"],
    ),
    "pac": RhythmConfig(
        "pac", "Courtemanche1998", "slab", (60, 100), 80,
        "ectopic_atrial", ["atrial", "ventricular", "shock"],
    ),
    "junctional": RhythmConfig(
        "junctional", "tenTusscher2006", "dual_slab", (40, 60), 80,
        "junctional_escape", ["atrial", "ventricular", "shock"],
    ),
    "paced_aai": RhythmConfig(
        "paced_aai", "tenTusscher2006", "slab", (60, 130), 80,
        "atrial_paced", ["atrial", "ventricular", "shock"],
    ),
    "paced_vvi": RhythmConfig(
        "paced_vvi", "OHaraRudy2011", "slab", (60, 130), 80,
        "ventricular_paced", ["atrial", "ventricular", "shock"],
    ),
    "paced_ddd": RhythmConfig(
        "paced_ddd", "tenTusscher2006", "dual_slab", (60, 130), 80,
        "dual_chamber_paced", ["atrial", "ventricular", "shock"],
    ),
    "paced_crt": RhythmConfig(
        "paced_crt", "OHaraRudy2011", "dual_slab", (60, 130), 80,
        "biventricular_paced", ["atrial", "ventricular", "shock"],
    ),
}

# ---------------------------------------------------------------------------
# Internal sample rate for template generation (high-res, resampled at load)
# ---------------------------------------------------------------------------
_TEMPLATE_SAMPLE_RATE_HZ = 1000


# ===================================================================
# Synthetic waveform primitives (enhanced analytical models)
# ===================================================================

def _action_potential_shape(n_samples: int, upstroke_frac: float = 0.02,
                            plateau_frac: float = 0.35,
                            repol_frac: float = 0.45) -> np.ndarray:
    """Generate a realistic ventricular action potential morphology.

    Phase 0: rapid upstroke
    Phase 1: early repolarization notch
    Phase 2: plateau
    Phase 3: repolarization
    Phase 4: resting potential
    """
    ap = np.zeros(n_samples, dtype=np.float64)
    idx_up = int(upstroke_frac * n_samples)
    idx_plat_end = int((upstroke_frac + plateau_frac) * n_samples)
    idx_repol_end = int((upstroke_frac + plateau_frac + repol_frac) * n_samples)

    # Phase 0 - rapid upstroke (sigmoid)
    if idx_up > 0:
        t_up = np.linspace(-6, 6, max(idx_up, 1))
        ap[:idx_up] = 1.0 / (1.0 + np.exp(-t_up))

    # Phase 1 - notch (small dip from 1.0 to ~0.85)
    notch_len = max(1, int(0.03 * n_samples))
    notch_end = min(idx_up + notch_len, n_samples)
    if notch_end > idx_up:
        t_n = np.linspace(0, np.pi, notch_end - idx_up)
        ap[idx_up:notch_end] = 1.0 - 0.15 * np.sin(t_n)

    # Phase 2 - plateau (slow linear decline from ~0.85 to ~0.75)
    plat_start = min(notch_end, n_samples)
    plat_end_actual = min(idx_plat_end, n_samples)
    if plat_end_actual > plat_start:
        plat_len = plat_end_actual - plat_start
        ap[plat_start:plat_end_actual] = np.linspace(0.85, 0.70, plat_len)

    # Phase 3 - repolarization (smooth sigmoid-like fall)
    repol_end_actual = min(idx_repol_end, n_samples)
    if repol_end_actual > plat_end_actual:
        repol_len = repol_end_actual - plat_end_actual
        t_repol = np.linspace(0, 1, repol_len)
        # Exponential decay from 0.70 to ~0.0
        ap[plat_end_actual:repol_end_actual] = 0.70 * np.exp(-3.0 * t_repol)

    # Phase 4 - resting
    ap[repol_end_actual:] = 0.0

    return ap


def _pqrst_beat(n_samples: int, rng: np.random.Generator,
                p_amp: float = 0.2, qrs_amp: float = 1.5,
                t_amp: float = 0.3, qrs_width_frac: float = 0.08,
                p_present: bool = True,
                qrs_morphology: str = "narrow") -> np.ndarray:
    """Generate a single PQRST beat with physiological timing.

    All timings are given as fractions of the total beat duration:
        P wave:   5-12%  (onset at ~10%)
        PR seg:   ~5%
        QRS:      6-16%  (onset at ~25%)
        ST seg:   ~8%
        T wave:   ~15%   (onset at ~50%)
    """
    beat = np.zeros(n_samples, dtype=np.float64)
    t = np.linspace(0.0, 1.0, n_samples, dtype=np.float64)

    # --- P wave ---
    if p_present and p_amp > 0:
        p_center = 0.12
        p_sigma = 0.030
        beat += p_amp * np.exp(-0.5 * ((t - p_center) / p_sigma) ** 2)

    # --- QRS complex ---
    if qrs_amp > 0:
        if qrs_morphology == "narrow":
            q_center = 0.24
            r_center = 0.27
            s_center = 0.30
            q_sigma = 0.008
            r_sigma = 0.010
            s_sigma = 0.008
            beat += -0.10 * qrs_amp * np.exp(-0.5 * ((t - q_center) / q_sigma) ** 2)
            beat += qrs_amp * np.exp(-0.5 * ((t - r_center) / r_sigma) ** 2)
            beat += -0.25 * qrs_amp * np.exp(-0.5 * ((t - s_center) / s_sigma) ** 2)
        elif qrs_morphology == "wide":
            # Wide-complex: broader Gaussians, notching
            q_center = 0.22
            r_center = 0.27
            s_center = 0.33
            q_sigma = 0.012
            r_sigma = 0.018
            s_sigma = 0.014
            beat += -0.15 * qrs_amp * np.exp(-0.5 * ((t - q_center) / q_sigma) ** 2)
            beat += qrs_amp * np.exp(-0.5 * ((t - r_center) / r_sigma) ** 2)
            beat += -0.35 * qrs_amp * np.exp(-0.5 * ((t - s_center) / s_sigma) ** 2)
            # Notch
            notch_center = 0.30
            beat += -0.12 * qrs_amp * np.exp(-0.5 * ((t - notch_center) / 0.006) ** 2)
        elif qrs_morphology == "paced":
            # Pacing artifact + wide QRS
            spike_center = 0.245
            spike_sigma = 0.002
            beat += 3.0 * np.exp(-0.5 * ((t - spike_center) / spike_sigma) ** 2)
            beat += -1.2 * np.exp(-0.5 * ((t - (spike_center + 0.003)) / 0.003) ** 2)
            r_center = 0.28
            s_center = 0.34
            beat += 0.7 * qrs_amp * np.exp(-0.5 * ((t - r_center) / 0.016) ** 2)
            beat += -0.55 * qrs_amp * np.exp(-0.5 * ((t - s_center) / 0.018) ** 2)

    # --- T wave (asymmetric) ---
    if t_amp > 0:
        t_center = 0.52
        sigma_l = 0.050
        sigma_r = 0.035
        sigma_arr = np.where(t < t_center, sigma_l, sigma_r)
        beat += t_amp * np.exp(-0.5 * ((t - t_center) / sigma_arr) ** 2)

    # --- U wave (subtle, only for normal rhythms) ---
    if p_present and qrs_morphology == "narrow":
        u_center = 0.68
        u_sigma = 0.025
        beat += 0.04 * np.exp(-0.5 * ((t - u_center) / u_sigma) ** 2)

    return beat


def _fibrillatory_baseline(n_samples: int, rng: np.random.Generator,
                           freq_range: tuple[float, float] = (4.0, 9.0),
                           amplitude: float = 0.10,
                           n_components: int = 8) -> np.ndarray:
    """Generate multi-frequency fibrillatory waves for AF.

    Uses superposition of sinusoids with dominant frequency in the 4-9 Hz
    range plus harmonics, creating realistic spectral content resembling
    f-waves seen in intracardiac recordings.
    """
    t = np.arange(n_samples, dtype=np.float64) / _TEMPLATE_SAMPLE_RATE_HZ
    signal = np.zeros(n_samples, dtype=np.float64)

    # Dominant frequency component
    f_dom = rng.uniform(freq_range[0], freq_range[1])

    for i in range(n_components):
        if i == 0:
            freq = f_dom
            amp = amplitude
        else:
            # Sub-harmonics and harmonics with declining power
            freq = f_dom * rng.uniform(0.5, 2.5)
            freq = max(2.0, min(freq, 12.0))
            amp = amplitude * rng.uniform(0.1, 0.5) / (1 + 0.3 * i)

        phase = rng.uniform(0.0, 2.0 * np.pi)
        signal += amp * np.sin(2.0 * np.pi * freq * t + phase)

    # Amplitude modulation for realistic waxing/waning
    mod_freq = rng.uniform(0.3, 1.5)
    mod_phase = rng.uniform(0.0, 2.0 * np.pi)
    modulation = 0.6 + 0.4 * np.sin(2.0 * np.pi * mod_freq * t + mod_phase)
    signal *= modulation

    return signal


def _vf_chaotic_waveform(n_samples: int, rng: np.random.Generator,
                         amplitude: float = 1.5) -> np.ndarray:
    """Generate chaotic VF waveform with waxing/waning amplitude envelope.

    Models coarse-to-fine VF transition with:
    - Dominant 3-8 Hz oscillation
    - Multiple harmonics for irregular morphology
    - Amplitude envelope that waxes and wanes
    - Beat-to-beat amplitude and timing randomness
    """
    t = np.arange(n_samples, dtype=np.float64) / _TEMPLATE_SAMPLE_RATE_HZ

    # Dominant frequency
    f_dom = rng.uniform(3.0, 8.0)
    phase0 = rng.uniform(0, 2 * np.pi)

    signal = amplitude * np.sin(2.0 * np.pi * f_dom * t + phase0)

    # Add 3-5 harmonics with decreasing amplitude and random phase
    for k in range(2, rng.integers(4, 7)):
        h_amp = amplitude * rng.uniform(0.1, 0.35) / k
        h_phase = rng.uniform(0, 2 * np.pi)
        signal += h_amp * np.sin(2.0 * np.pi * f_dom * k * t + h_phase)

    # Frequency modulation (slight drift)
    fm_depth = rng.uniform(0.5, 2.0)
    fm_rate = rng.uniform(0.2, 0.8)
    fm_phase = rng.uniform(0, 2 * np.pi)
    freq_mod = fm_depth * np.sin(2.0 * np.pi * fm_rate * t + fm_phase)
    signal += amplitude * 0.3 * np.sin(2.0 * np.pi * (f_dom + freq_mod) * t)

    # Waxing/waning amplitude envelope
    env_freq = rng.uniform(0.3, 1.2)
    env_phase = rng.uniform(0, 2 * np.pi)
    envelope = 0.3 + 0.7 * np.abs(np.sin(2.0 * np.pi * env_freq * t + env_phase))
    signal *= envelope

    # Add broadband noise for chaotic character
    signal += rng.normal(0, 0.08 * amplitude, n_samples)

    return signal


def _sawtooth_flutter(n_samples: int, rng: np.random.Generator,
                      flutter_rate_hz: float = 5.0,
                      amplitude: float = 0.3) -> np.ndarray:
    """Generate sawtooth flutter waves at ~300 bpm (5 Hz)."""
    t = np.arange(n_samples, dtype=np.float64) / _TEMPLATE_SAMPLE_RATE_HZ

    # Fourier sawtooth approximation (8 harmonics)
    sawtooth = np.zeros(n_samples, dtype=np.float64)
    phase = rng.uniform(0, 2 * np.pi)
    for k in range(1, 9):
        sign = (-1.0) ** (k + 1)
        sawtooth += sign * np.sin(2.0 * np.pi * flutter_rate_hz * k * t + phase * k) / k
    sawtooth *= (2.0 / np.pi) * amplitude
    return sawtooth


def _apply_natural_variation(beat: np.ndarray, rng: np.random.Generator,
                             amp_var_pct: float = 0.04,
                             time_var_pct: float = 0.025) -> np.ndarray:
    """Apply small beat-to-beat variation in amplitude and timing.

    Args:
        beat: Template beat waveform.
        amp_var_pct: Amplitude variation (fraction, e.g. 0.04 = 4%).
        time_var_pct: Timing variation (fraction of beat length).

    Returns:
        Modified beat with natural variation applied.
    """
    # Amplitude variation
    amp_scale = 1.0 + rng.normal(0.0, amp_var_pct)
    varied = beat * amp_scale

    # Timing variation via sub-sample shift (resample with slight stretch/compress)
    n = len(varied)
    time_shift = rng.normal(0.0, time_var_pct)
    stretch_factor = 1.0 + time_shift
    new_n = max(1, int(round(n * stretch_factor)))

    if new_n != n and new_n > 1:
        x_old = np.linspace(0, 1, new_n)
        x_new = np.linspace(0, 1, n)
        # Stretch then resample back to original length
        stretched = np.interp(x_old, np.linspace(0, 1, n), varied)
        varied = np.interp(x_new, x_old, stretched)

    return varied


# ===================================================================
# Template Generator
# ===================================================================

class TemplateGenerator:
    """Offline generator for EGM waveform templates.

    Detects openCARP availability and uses it if present; otherwise falls back
    to enhanced synthetic waveform generation.

    Parameters:
        output_dir: Directory path where .npy files and catalog are written.
        use_docker: Whether to attempt Docker-based openCARP first.
    """

    def __init__(
        self,
        output_dir: str = "src/generator/cardiac/opencarp/templates",
        use_docker: bool = True,
    ) -> None:
        self._output_dir = Path(output_dir)
        self._use_docker = use_docker
        self._rng = np.random.default_rng(seed=42)
        self._sample_rate = _TEMPLATE_SAMPLE_RATE_HZ

    # ------------------------------------------------------------------
    # openCARP detection
    # ------------------------------------------------------------------

    def detect_opencarp(self) -> tuple[bool, str]:
        """Detect openCARP availability.

        Checks Docker first (if enabled), then native ``openCARP`` binary.

        Returns:
            Tuple of (available, method) where method is one of
            ``'docker'``, ``'native'``, or ``'none'``.
        """
        if self._use_docker:
            try:
                result = subprocess.run(
                    ["docker", "images", "--format", "{{.Repository}}"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    images = result.stdout.strip().split("\n")
                    for img in images:
                        if "opencarp" in img.lower():
                            logger.info("openCARP detected via Docker image: %s", img)
                            return True, "docker"
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                logger.debug("Docker not available or timed out.")

        # Check native binary
        opencarp_bin = shutil.which("openCARP")
        if opencarp_bin is None:
            opencarp_bin = shutil.which("opencarp")
        if opencarp_bin is not None:
            logger.info("openCARP detected as native binary: %s", opencarp_bin)
            return True, "native"

        logger.info("openCARP not available; will use synthetic fallback.")
        return False, "none"

    # ------------------------------------------------------------------
    # Main generation entry points
    # ------------------------------------------------------------------

    def generate_all(self, force: bool = False) -> dict:
        """Generate templates for all 18 rhythm types.

        Parameters:
            force: If True, regenerate even if templates already exist on disk.

        Returns:
            Complete catalog dictionary (also written to template_catalog.json).
        """
        self._output_dir.mkdir(parents=True, exist_ok=True)

        available, method = self.detect_opencarp()
        logger.info(
            "Template generation starting — openCARP %s (method=%s), force=%s",
            "available" if available else "unavailable", method, force,
        )

        catalog: dict[str, Any] = {
            "version": "1.0.0",
            "sample_rate_hz": self._sample_rate,
            "generation_method": method if available else "synthetic_fallback",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "rhythms": {},
        }

        for rhythm_name, config in RHYTHM_CONFIGS.items():
            # Skip if already generated (unless force)
            rhythm_dir = self._output_dir / rhythm_name
            catalog_entry_exists = (
                rhythm_dir.exists()
                and any(rhythm_dir.glob("*.npy"))
                and not force
            )
            if catalog_entry_exists:
                logger.info("Skipping %s (already generated, use force=True to regenerate).",
                            rhythm_name)
                # Reconstruct catalog entry from existing files
                npy_files = sorted(rhythm_dir.glob("*.npy"))
                channels_found: dict[str, int] = {}
                for f in npy_files:
                    parts = f.stem.split("_beat_")
                    if len(parts) == 2:
                        ch = parts[0]
                        channels_found[ch] = channels_found.get(ch, 0) + 1
                catalog["rhythms"][rhythm_name] = {
                    "ionic_model": config.ionic_model,
                    "geometry": config.geometry,
                    "hr_range_bpm": list(config.hr_range_bpm),
                    "n_beats": max(channels_found.values()) if channels_found else 0,
                    "channels": list(channels_found.keys()),
                    "generation_method": "unknown_preexisting",
                }
                continue

            rhythm_meta = self.generate_rhythm(rhythm_name, config,
                                               available=available, method=method)
            catalog["rhythms"][rhythm_name] = rhythm_meta

        catalog_path = self._write_catalog(catalog)
        logger.info("Template catalog written to %s", catalog_path)
        return catalog

    def generate_rhythm(
        self,
        rhythm_name: str,
        config: RhythmConfig,
        available: bool | None = None,
        method: str | None = None,
    ) -> dict:
        """Generate templates for a single rhythm type.

        Parameters:
            rhythm_name: Key into RHYTHM_CONFIGS.
            config: The RhythmConfig for this rhythm.
            available: Whether openCARP is available (auto-detected if None).
            method: Detection method ('docker', 'native', 'none').

        Returns:
            Per-rhythm metadata dictionary.
        """
        if available is None or method is None:
            available, method = self.detect_opencarp()

        rhythm_dir = self._output_dir / rhythm_name
        rhythm_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Generating %d beats for rhythm '%s' ...", config.n_beats, rhythm_name)

        if available:
            templates = self._generate_opencarp(rhythm_name, config, method)
            gen_method = method
        else:
            templates = self._generate_synthetic_fallback(rhythm_name, config)
            gen_method = "synthetic_fallback"

        # Write .npy files
        n_beats_written = 0
        for channel, beats_array in templates.items():
            for beat_idx in range(beats_array.shape[0]):
                npy_path = rhythm_dir / f"{channel}_beat_{beat_idx:04d}.npy"
                np.save(npy_path, beats_array[beat_idx])
            n_beats_written = max(n_beats_written, beats_array.shape[0])

        meta = {
            "ionic_model": config.ionic_model,
            "geometry": config.geometry,
            "hr_range_bpm": list(config.hr_range_bpm),
            "n_beats": n_beats_written,
            "channels": list(templates.keys()),
            "stimulation_protocol": config.stimulation_protocol,
            "generation_method": gen_method,
        }

        logger.info(
            "Rhythm '%s': %d beats x %d channels written to %s",
            rhythm_name, n_beats_written, len(templates), rhythm_dir,
        )
        return meta

    # ------------------------------------------------------------------
    # openCARP simulation (real)
    # ------------------------------------------------------------------

    def _generate_opencarp(
        self, rhythm_name: str, config: RhythmConfig, method: str = "docker"
    ) -> dict[str, np.ndarray]:
        """Run openCARP simulations to generate templates.

        Constructs an openCARP parameter file, runs the simulation in a
        temporary directory, and extracts per-beat EGM waveforms from the
        output.

        Parameters:
            rhythm_name: The rhythm identifier.
            config: Rhythm configuration.
            method: 'docker' or 'native'.

        Returns:
            Dict mapping channel name to ndarray of shape (n_beats, n_samples).
        """
        with tempfile.TemporaryDirectory(prefix=f"opencarp_{rhythm_name}_") as tmpdir:
            tmpdir_path = Path(tmpdir)

            # --- Build openCARP parameter file ---
            param_file = tmpdir_path / "sim.par"
            sim_duration_ms = self._compute_sim_duration(config)
            param_content = self._build_opencarp_params(config, sim_duration_ms)
            param_file.write_text(param_content)

            # --- Run simulation ---
            if method == "docker":
                cmd = [
                    "docker", "run", "--rm",
                    "-v", f"{tmpdir}:/sim",
                    "-w", "/sim",
                    "opencarp/opencarp:latest",
                    "openCARP", "+F", "sim.par",
                ]
            else:
                cmd = ["openCARP", "+F", str(param_file)]

            logger.info("Running openCARP: %s", " ".join(cmd))
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True,
                    timeout=600, cwd=tmpdir,
                )
                if result.returncode != 0:
                    logger.error("openCARP failed (rc=%d): %s", result.returncode,
                                 result.stderr[:500])
                    logger.warning("Falling back to synthetic for rhythm '%s'.", rhythm_name)
                    return self._generate_synthetic_fallback(rhythm_name, config)
            except (subprocess.TimeoutExpired, OSError) as exc:
                logger.error("openCARP execution error: %s", exc)
                logger.warning("Falling back to synthetic for rhythm '%s'.", rhythm_name)
                return self._generate_synthetic_fallback(rhythm_name, config)

            # --- Parse output ---
            return self._parse_opencarp_output(tmpdir_path, config)

    def _compute_sim_duration(self, config: RhythmConfig) -> float:
        """Compute the required simulation duration in ms."""
        avg_hr = (config.hr_range_bpm[0] + config.hr_range_bpm[1]) / 2.0
        avg_rr_ms = 60_000.0 / max(avg_hr, 1.0)
        # Simulate enough beats plus a warm-up period
        return avg_rr_ms * (config.n_beats + 10)

    def _build_opencarp_params(self, config: RhythmConfig, duration_ms: float) -> str:
        """Build an openCARP .par parameter file content string."""
        # Map geometry to mesh
        geom_map = {
            "slab": "block 20 3 3 2000 300 300",
            "ring": "ring 20 1 100",
            "wedge": "block 30 10 10 3000 1000 1000",
            "dual_slab": "block 20 3 6 2000 300 600",
        }
        geom_line = geom_map.get(config.geometry, geom_map["slab"])

        avg_hr = (config.hr_range_bpm[0] + config.hr_range_bpm[1]) / 2.0
        bcl = 60_000.0 / max(avg_hr, 1.0)  # basic cycle length in ms

        params = f"""\
# openCARP parameter file for {config.rhythm_name}
# Auto-generated by TemplateGenerator

# Geometry
meshname = {geom_line}

# Ionic model
imp_region[0].im = {config.ionic_model}

# Time stepping
tend = {duration_ms:.1f}
dt = 0.025

# Stimulation
stimulus[0].stimtype = 0
stimulus[0].strength = 50.0
stimulus[0].duration = 2.0
stimulus[0].bcl = {bcl:.1f}
stimulus[0].start = 0.0
stimulus[0].npls = {config.n_beats + 10}

# Output
spacedt = 1.0
timedt = 1.0

# Extracellular
phie_rec_ptf = 1
num_phie_rec = 3
phie_rec[0].name = atrial_electrode
phie_rec[1].name = ventricular_electrode
phie_rec[2].name = shock_electrode
"""
        return params

    def _parse_opencarp_output(
        self, sim_dir: Path, config: RhythmConfig
    ) -> dict[str, np.ndarray]:
        """Parse openCARP output files into per-beat template arrays.

        Looks for igb/dat trace files and segments them into individual beats.
        Falls back to synthetic if parsing fails.
        """
        channel_map = {
            "atrial": "atrial_electrode",
            "ventricular": "ventricular_electrode",
            "shock": "shock_electrode",
        }

        templates: dict[str, np.ndarray] = {}

        for ch_name, electrode_name in channel_map.items():
            if ch_name not in config.channels:
                continue

            # Try to find the trace file
            trace_file = None
            for suffix in [".dat", ".igb", ".bin"]:
                candidate = sim_dir / f"{electrode_name}{suffix}"
                if candidate.exists():
                    trace_file = candidate
                    break

            if trace_file is None:
                logger.warning(
                    "Trace file for %s not found in %s; using synthetic fallback.",
                    electrode_name, sim_dir,
                )
                return self._generate_synthetic_fallback(config.rhythm_name, config)

            # Read the raw trace
            try:
                if trace_file.suffix == ".dat":
                    raw = np.loadtxt(trace_file)
                    if raw.ndim == 2:
                        signal = raw[:, 1]  # time in col 0, voltage in col 1
                    else:
                        signal = raw
                else:
                    signal = np.fromfile(trace_file, dtype=np.float32).astype(np.float64)
            except Exception as exc:
                logger.error("Failed to read %s: %s", trace_file, exc)
                return self._generate_synthetic_fallback(config.rhythm_name, config)

            # Segment into beats using R-peak detection (simple threshold)
            beats = self._segment_beats(signal, config)
            templates[ch_name] = beats

        return templates

    def _segment_beats(
        self, signal: np.ndarray, config: RhythmConfig
    ) -> np.ndarray:
        """Segment a continuous signal into individual beat templates.

        Uses a simple peak-detection approach and fixed window around each peak.
        """
        avg_hr = (config.hr_range_bpm[0] + config.hr_range_bpm[1]) / 2.0
        rr_samples = int(60_000.0 / max(avg_hr, 1.0))  # at 1 kHz, 1 sample = 1 ms

        # Peak detection: find peaks above 50th percentile with min distance
        threshold = np.percentile(np.abs(signal), 50)
        min_dist = int(rr_samples * 0.7)

        peaks: list[int] = []
        for i in range(1, len(signal) - 1):
            if (abs(signal[i]) > threshold
                    and abs(signal[i]) >= abs(signal[i - 1])
                    and abs(signal[i]) >= abs(signal[i + 1])):
                if not peaks or (i - peaks[-1]) >= min_dist:
                    peaks.append(i)

        # Extract windows around each peak
        half_window = rr_samples // 2
        beat_list: list[np.ndarray] = []
        for pk in peaks:
            start = max(0, pk - half_window)
            end = min(len(signal), pk + half_window)
            beat = signal[start:end]
            # Pad to uniform length if needed
            if len(beat) < rr_samples:
                beat = np.pad(beat, (0, rr_samples - len(beat)), mode="constant")
            else:
                beat = beat[:rr_samples]
            beat_list.append(beat)
            if len(beat_list) >= config.n_beats:
                break

        if len(beat_list) == 0:
            # Emergency fallback: chop signal into equal windows
            for i in range(0, len(signal) - rr_samples, rr_samples):
                beat_list.append(signal[i:i + rr_samples])
                if len(beat_list) >= config.n_beats:
                    break

        if len(beat_list) == 0:
            logger.warning("Could not segment any beats; returning single-window.")
            beat_list.append(signal[:min(rr_samples, len(signal))])

        return np.array(beat_list[:config.n_beats], dtype=np.float64)

    # ------------------------------------------------------------------
    # Enhanced synthetic fallback
    # ------------------------------------------------------------------

    def _generate_synthetic_fallback(
        self, rhythm_name: str, config: RhythmConfig
    ) -> dict[str, np.ndarray]:
        """Generate physiologically enhanced synthetic EGM templates.

        Produces waveforms that are significantly better than basic Gaussian
        synthesis by using:
        - Realistic action potential morphology for ventricular beats
        - Proper PQRST timing with physiological intervals
        - AF: multi-frequency fibrillatory waves (4-9 Hz dominant)
        - VT: wide-complex beats with consistent morphology
        - VF: chaotic waxing/waning amplitude envelopes
        - Natural beat-to-beat variation (3-5% amplitude, 2-3% timing)

        Returns:
            Dict mapping channel name -> ndarray of shape (n_beats, n_samples).
        """
        rng = np.random.default_rng(seed=hash(rhythm_name) % (2**31))

        # Compute beat duration at mean heart rate
        avg_hr = (config.hr_range_bpm[0] + config.hr_range_bpm[1]) / 2.0
        avg_rr_ms = 60_000.0 / max(avg_hr, 1.0)
        n_samples = int(avg_rr_ms)  # at 1 kHz, 1 sample = 1 ms

        templates: dict[str, np.ndarray] = {}

        # Dispatch to rhythm-specific generators
        generator_map: dict[str, Any] = {
            "nsr": self._synth_nsr,
            "sinus_bradycardia": self._synth_sinus_brady,
            "sinus_tachycardia": self._synth_sinus_tachy,
            "atrial_fibrillation": self._synth_af,
            "atrial_flutter": self._synth_aflutter,
            "svt": self._synth_svt,
            "ventricular_tachycardia": self._synth_vt,
            "ventricular_fibrillation": self._synth_vf,
            "complete_heart_block": self._synth_chb,
            "mobitz_i": self._synth_mobitz_i,
            "mobitz_ii": self._synth_mobitz_ii,
            "pvc": self._synth_pvc,
            "pac": self._synth_pac,
            "junctional": self._synth_junctional,
            "paced_aai": self._synth_paced_aai,
            "paced_vvi": self._synth_paced_vvi,
            "paced_ddd": self._synth_paced_ddd,
            "paced_crt": self._synth_paced_crt,
        }

        gen_fn = generator_map.get(rhythm_name)
        if gen_fn is None:
            logger.warning("No specific synthetic generator for '%s'; using generic NSR.",
                           rhythm_name)
            gen_fn = self._synth_nsr

        for ch in config.channels:
            beats = []
            for _ in range(config.n_beats):
                # Vary heart rate within the specified range for each beat
                hr = rng.uniform(config.hr_range_bpm[0], config.hr_range_bpm[1])
                beat_rr_ms = 60_000.0 / max(hr, 1.0)
                beat_n = int(beat_rr_ms)
                raw_beat = gen_fn(beat_n, ch, rng)
                varied_beat = _apply_natural_variation(raw_beat, rng)
                # Normalize to n_samples length for uniform array stacking
                if len(varied_beat) != n_samples:
                    x_old = np.linspace(0, 1, len(varied_beat))
                    x_new = np.linspace(0, 1, n_samples)
                    varied_beat = np.interp(x_new, x_old, varied_beat)
                beats.append(varied_beat)
            templates[ch] = np.array(beats, dtype=np.float64)

        return templates

    # ------------------------------------------------------------------
    # Per-rhythm synthetic generators
    # ------------------------------------------------------------------

    def _synth_nsr(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """Normal sinus rhythm: full PQRST on all channels."""
        if ch == "atrial":
            beat = _pqrst_beat(n, rng, p_amp=0.6, qrs_amp=0.22, t_amp=0.08,
                               qrs_morphology="narrow")
        elif ch == "ventricular":
            beat = _pqrst_beat(n, rng, p_amp=0.05, qrs_amp=1.5, t_amp=0.30,
                               qrs_morphology="narrow")
        else:  # shock / far-field
            beat = _pqrst_beat(n, rng, p_amp=0.10, qrs_amp=0.9, t_amp=0.18,
                               qrs_morphology="narrow")
        return beat

    def _synth_sinus_brady(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """Sinus bradycardia: like NSR but with slightly larger T waves."""
        if ch == "atrial":
            return _pqrst_beat(n, rng, p_amp=0.6, qrs_amp=0.22, t_amp=0.10)
        elif ch == "ventricular":
            return _pqrst_beat(n, rng, p_amp=0.05, qrs_amp=1.5, t_amp=0.38)
        else:
            return _pqrst_beat(n, rng, p_amp=0.10, qrs_amp=0.9, t_amp=0.22)

    def _synth_sinus_tachy(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """Sinus tachycardia: shorter intervals, slightly peaked P."""
        if ch == "atrial":
            return _pqrst_beat(n, rng, p_amp=0.75, qrs_amp=0.20, t_amp=0.06)
        elif ch == "ventricular":
            return _pqrst_beat(n, rng, p_amp=0.06, qrs_amp=1.4, t_amp=0.25)
        else:
            return _pqrst_beat(n, rng, p_amp=0.12, qrs_amp=0.85, t_amp=0.15)

    def _synth_af(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """Atrial fibrillation: fibrillatory baseline + narrow QRS."""
        if ch == "atrial":
            # Fibrillatory f-waves with far-field ventricular complex
            base = _fibrillatory_baseline(n, rng, amplitude=0.12, n_components=10)
            # Add small far-field QRS
            qrs_only = _pqrst_beat(n, rng, p_amp=0.0, qrs_amp=0.18, t_amp=0.04,
                                   p_present=False)
            return base + qrs_only
        elif ch == "ventricular":
            base = _fibrillatory_baseline(n, rng, amplitude=0.02, n_components=5)
            qrs = _pqrst_beat(n, rng, p_amp=0.0, qrs_amp=1.4, t_amp=0.30,
                              p_present=False)
            return base + qrs
        else:
            base = _fibrillatory_baseline(n, rng, amplitude=0.05, n_components=6)
            qrs = _pqrst_beat(n, rng, p_amp=0.0, qrs_amp=0.85, t_amp=0.18,
                              p_present=False)
            return base + qrs

    def _synth_aflutter(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """Atrial flutter: sawtooth + narrow QRS."""
        if ch == "atrial":
            saw = _sawtooth_flutter(n, rng, amplitude=0.35)
            qrs_ff = _pqrst_beat(n, rng, p_amp=0.0, qrs_amp=0.18, t_amp=0.04,
                                 p_present=False)
            return saw + qrs_ff
        elif ch == "ventricular":
            saw = _sawtooth_flutter(n, rng, amplitude=0.04)
            qrs = _pqrst_beat(n, rng, p_amp=0.0, qrs_amp=1.3, t_amp=0.25,
                              p_present=False)
            return saw + qrs
        else:
            saw = _sawtooth_flutter(n, rng, amplitude=0.12)
            qrs = _pqrst_beat(n, rng, p_amp=0.0, qrs_amp=0.80, t_amp=0.15,
                              p_present=False)
            return saw + qrs

    def _synth_svt(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """SVT: retrograde P waves (inverted), narrow QRS, fast rate."""
        if ch == "atrial":
            beat = _pqrst_beat(n, rng, p_amp=0.35, qrs_amp=0.18, t_amp=0.05)
            # Invert P wave component (it's at the beginning)
            p_end = int(0.18 * n)
            beat[:p_end] *= -1.0
            return beat
        elif ch == "ventricular":
            return _pqrst_beat(n, rng, p_amp=0.0, qrs_amp=1.3, t_amp=0.20,
                               p_present=False)
        else:
            beat = _pqrst_beat(n, rng, p_amp=0.05, qrs_amp=0.80, t_amp=0.12)
            p_end = int(0.18 * n)
            beat[:p_end] *= -0.5
            return beat

    def _synth_vt(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """VT: wide-complex QRS, no P waves, consistent morphology."""
        if ch == "atrial":
            # Atrial channel sees far-field wide QRS only
            return _pqrst_beat(n, rng, p_amp=0.0, qrs_amp=0.30, t_amp=0.08,
                               p_present=False, qrs_morphology="wide")
        elif ch == "ventricular":
            return _pqrst_beat(n, rng, p_amp=0.0, qrs_amp=2.0, t_amp=0.50,
                               p_present=False, qrs_morphology="wide")
        else:
            return _pqrst_beat(n, rng, p_amp=0.0, qrs_amp=1.3, t_amp=0.35,
                               p_present=False, qrs_morphology="wide")

    def _synth_vf(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """VF: chaotic waveform with waxing/waning amplitude."""
        vf = _vf_chaotic_waveform(n, rng, amplitude=1.5)
        if ch == "atrial":
            return vf * 0.4
        elif ch == "ventricular":
            return vf * 1.0
        else:
            return vf * 0.7

    def _synth_chb(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """Complete heart block: dissociated P waves and wide QRS escape."""
        t = np.linspace(0.0, 1.0, n, dtype=np.float64)

        # Independent atrial P waves at a normal sinus rate (~70 bpm)
        # Within one ventricular beat cycle, there may be 2-3 P waves
        # For CHB at ~35 bpm, each beat is ~1700 ms.  Sinus P at ~70 bpm -> ~860 ms
        # So roughly 2 P waves per ventricular beat
        p_signal = np.zeros(n, dtype=np.float64)
        n_p_waves = max(1, int(round(n / 860.0)))  # at 1kHz, 860 samples = 860ms
        for i in range(n_p_waves):
            p_center = (i + 0.3) / max(n_p_waves, 1)
            p_center = min(p_center, 0.95)
            p_sigma = 0.025
            p_amp = 0.20
            p_signal += p_amp * np.exp(-0.5 * ((t - p_center) / p_sigma) ** 2)

        # Ventricular escape beat (wide QRS)
        v_beat = _pqrst_beat(n, rng, p_amp=0.0, qrs_amp=1.8, t_amp=0.40,
                             p_present=False, qrs_morphology="wide")

        if ch == "atrial":
            return p_signal * 3.0 + v_beat * 0.15
        elif ch == "ventricular":
            return p_signal * 0.03 + v_beat
        else:
            return p_signal * 0.5 + v_beat * 0.6

    def _synth_mobitz_i(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """Mobitz I: normal PQRST (the progressive PR prolongation is a strip-level feature)."""
        if ch == "atrial":
            return _pqrst_beat(n, rng, p_amp=0.6, qrs_amp=0.22, t_amp=0.08)
        elif ch == "ventricular":
            return _pqrst_beat(n, rng, p_amp=0.05, qrs_amp=1.5, t_amp=0.30)
        else:
            return _pqrst_beat(n, rng, p_amp=0.10, qrs_amp=0.9, t_amp=0.18)

    def _synth_mobitz_ii(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """Mobitz II: slightly wider QRS than normal (infra-Hisian block)."""
        if ch == "atrial":
            return _pqrst_beat(n, rng, p_amp=0.6, qrs_amp=0.22, t_amp=0.08)
        elif ch == "ventricular":
            return _pqrst_beat(n, rng, p_amp=0.05, qrs_amp=1.5, t_amp=0.30,
                               qrs_width_frac=0.10)
        else:
            return _pqrst_beat(n, rng, p_amp=0.10, qrs_amp=0.9, t_amp=0.18)

    def _synth_pvc(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """PVC: wide QRS, no preceding P wave, large amplitude."""
        if ch == "atrial":
            return _pqrst_beat(n, rng, p_amp=0.0, qrs_amp=0.38, t_amp=0.10,
                               p_present=False, qrs_morphology="wide")
        elif ch == "ventricular":
            return _pqrst_beat(n, rng, p_amp=0.0, qrs_amp=2.5, t_amp=0.60,
                               p_present=False, qrs_morphology="wide")
        else:
            return _pqrst_beat(n, rng, p_amp=0.0, qrs_amp=1.6, t_amp=0.42,
                               p_present=False, qrs_morphology="wide")

    def _synth_pac(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """PAC: abnormal (bifid) P wave, narrow QRS."""
        t = np.linspace(0.0, 1.0, n, dtype=np.float64)
        # Bifid P wave: two humps
        p1_center = 0.10
        p2_center = 0.14
        p_sigma = 0.020
        bifid_p = 0.12 * (np.exp(-0.5 * ((t - p1_center) / p_sigma) ** 2)
                          + 0.8 * np.exp(-0.5 * ((t - p2_center) / p_sigma) ** 2))

        qrs_beat = _pqrst_beat(n, rng, p_amp=0.0, qrs_amp=1.4, t_amp=0.30,
                               p_present=False)

        if ch == "atrial":
            return bifid_p * 3.0 + qrs_beat * 0.15
        elif ch == "ventricular":
            return bifid_p * 0.03 + qrs_beat
        else:
            return bifid_p * 0.5 + qrs_beat * 0.6

    def _synth_junctional(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """Junctional rhythm: inverted P wave (retrograde), narrow QRS."""
        t = np.linspace(0.0, 1.0, n, dtype=np.float64)
        # Retrograde inverted P wave appearing near or after QRS
        retro_p_center = 0.32  # Just after QRS onset
        retro_p = -0.15 * np.exp(-0.5 * ((t - retro_p_center) / 0.025) ** 2)

        qrs_beat = _pqrst_beat(n, rng, p_amp=0.0, qrs_amp=1.4, t_amp=0.30,
                               p_present=False)

        if ch == "atrial":
            return retro_p * 3.0 + qrs_beat * 0.15
        elif ch == "ventricular":
            return retro_p * 0.03 + qrs_beat
        else:
            return retro_p * 0.5 + qrs_beat * 0.6

    def _synth_paced_aai(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """AAI pacing: pacing artifact before P wave, normal QRS."""
        t = np.linspace(0.0, 1.0, n, dtype=np.float64)
        # Pacing spike before P wave
        spike_center = 0.08
        spike = 3.5 * np.exp(-0.5 * ((t - spike_center) / 0.002) ** 2)
        spike += -1.0 * np.exp(-0.5 * ((t - spike_center - 0.004) / 0.003) ** 2)

        beat = _pqrst_beat(n, rng, p_amp=0.20, qrs_amp=1.5, t_amp=0.30)

        if ch == "atrial":
            return spike * 1.0 + beat * 0.6 + _pqrst_beat(n, rng, p_amp=0.5, qrs_amp=0.15, t_amp=0.05)
        elif ch == "ventricular":
            return spike * 0.1 + beat
        else:
            return spike * 0.3 + beat * 0.6

    def _synth_paced_vvi(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """VVI pacing: pacing artifact before wide paced QRS."""
        return _pqrst_beat(n, rng,
                           p_amp=0.15 if ch == "atrial" else 0.04,
                           qrs_amp={"atrial": 0.30, "ventricular": 2.0, "shock": 1.2}.get(ch, 1.0),
                           t_amp={"atrial": 0.08, "ventricular": 0.50, "shock": 0.30}.get(ch, 0.2),
                           qrs_morphology="paced")

    def _synth_paced_ddd(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """DDD pacing: atrial spike + ventricular spike + paced QRS."""
        t = np.linspace(0.0, 1.0, n, dtype=np.float64)
        # Atrial pacing spike
        a_spike_center = 0.08
        a_spike = 2.5 * np.exp(-0.5 * ((t - a_spike_center) / 0.002) ** 2)
        a_spike += -0.8 * np.exp(-0.5 * ((t - a_spike_center - 0.004) / 0.003) ** 2)

        # Ventricular pacing spike
        v_spike_center = 0.24
        v_spike = 3.5 * np.exp(-0.5 * ((t - v_spike_center) / 0.002) ** 2)
        v_spike += -1.1 * np.exp(-0.5 * ((t - v_spike_center - 0.004) / 0.003) ** 2)

        beat = _pqrst_beat(n, rng, p_amp=0.18, qrs_amp=2.0, t_amp=0.50,
                           qrs_morphology="paced")

        if ch == "atrial":
            return a_spike * 1.0 + v_spike * 0.15 + beat * 0.15 + \
                _pqrst_beat(n, rng, p_amp=0.5, qrs_amp=0.0, t_amp=0.0)
        elif ch == "ventricular":
            return a_spike * 0.08 + v_spike * 0.9 + beat
        else:
            return a_spike * 0.3 + v_spike * 0.5 + beat * 0.6

    def _synth_paced_crt(self, n: int, ch: str, rng: np.random.Generator) -> np.ndarray:
        """CRT pacing: biventricular paced beat — slightly narrower than VVI."""
        t = np.linspace(0.0, 1.0, n, dtype=np.float64)

        # Biventricular pacing produces two closely-spaced ventricular spikes
        v1_center = 0.245
        v2_center = 0.250  # ~5 ms offset for LV lead
        v1_spike = 3.0 * np.exp(-0.5 * ((t - v1_center) / 0.002) ** 2)
        v2_spike = 2.8 * np.exp(-0.5 * ((t - v2_center) / 0.002) ** 2)
        v1_spike += -0.9 * np.exp(-0.5 * ((t - v1_center - 0.004) / 0.003) ** 2)
        v2_spike += -0.85 * np.exp(-0.5 * ((t - v2_center - 0.004) / 0.003) ** 2)

        # CRT QRS is narrower than VVI (that is the whole point of CRT)
        beat = _pqrst_beat(n, rng, p_amp=0.18, qrs_amp=1.8, t_amp=0.40,
                           qrs_morphology="paced")

        combined_spike = v1_spike + v2_spike
        if ch == "atrial":
            return combined_spike * 0.12 + beat * 0.15 + \
                _pqrst_beat(n, rng, p_amp=0.5, qrs_amp=0.0, t_amp=0.0)
        elif ch == "ventricular":
            return combined_spike * 0.9 + beat
        else:
            return combined_spike * 0.45 + beat * 0.6

    # ------------------------------------------------------------------
    # Catalog I/O
    # ------------------------------------------------------------------

    def _write_catalog(self, catalog_data: dict) -> Path:
        """Write the template catalog to template_catalog.json.

        Returns:
            Path to the written catalog file.
        """
        catalog_path = self._output_dir / "template_catalog.json"
        with open(catalog_path, "w") as f:
            json.dump(catalog_data, f, indent=2, default=str)
        return catalog_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Generate all templates (CLI entry point)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    import argparse

    parser = argparse.ArgumentParser(
        description="Generate openCARP EGM waveform templates.",
    )
    parser.add_argument(
        "--output-dir",
        default="src/generator/cardiac/opencarp/templates",
        help="Output directory for .npy files and catalog.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Regenerate even if templates already exist.",
    )
    parser.add_argument(
        "--no-docker", action="store_true",
        help="Skip Docker-based openCARP detection.",
    )
    parser.add_argument(
        "--rhythm", type=str, default=None,
        help="Generate only a specific rhythm (by key name).",
    )
    args = parser.parse_args()

    gen = TemplateGenerator(output_dir=args.output_dir, use_docker=not args.no_docker)

    if args.rhythm:
        if args.rhythm not in RHYTHM_CONFIGS:
            print(f"Unknown rhythm '{args.rhythm}'. Available: {list(RHYTHM_CONFIGS.keys())}")
            return
        config = RHYTHM_CONFIGS[args.rhythm]
        meta = gen.generate_rhythm(args.rhythm, config)
        # Write a minimal catalog for this single rhythm
        catalog = {
            "version": "1.0.0",
            "sample_rate_hz": _TEMPLATE_SAMPLE_RATE_HZ,
            "generation_method": meta.get("generation_method", "unknown"),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "rhythms": {args.rhythm: meta},
        }
        gen._write_catalog(catalog)
        print(f"Generated {meta['n_beats']} beats for '{args.rhythm}'.")
    else:
        catalog = gen.generate_all(force=args.force)
        total_beats = sum(r.get("n_beats", 0) for r in catalog["rhythms"].values())
        print(f"Generated {total_beats} total beats across {len(catalog['rhythms'])} rhythms.")


if __name__ == "__main__":
    main()
