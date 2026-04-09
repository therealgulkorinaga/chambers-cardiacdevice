"""Full EGM (electrogram) synthesis pipeline.

Assembles per-beat waveform components into multi-channel intracardiac
electrograms (atrial EGM, ventricular EGM, shock/far-field channel) with
configurable noise, pacing artifacts, and time-aligned annotations.

Supports two generation modes:
- Mode A (parametric): Built-in Gaussian waveform synthesis. Fast, lightweight.
- Mode B (opencarp): Pre-computed openCARP ionic-model templates. Biophysically
  accurate. Requires template library to be generated first. Falls back to
  Mode A if templates are unavailable.
"""

from __future__ import annotations

import logging
import uuid
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

import numpy as np

from .rhythm_engine import RhythmState
from .waveform_models import (
    generate_p_wave,
    generate_pacing_artifact,
    generate_qrs_complex,
    generate_t_wave,
)

logger = logging.getLogger(__name__)


@dataclass
class EGMStrip:
    """Container for a multi-channel EGM strip recording.

    Attributes:
        strip_id:       Unique identifier (UUID4 string).
        channels:       Mapping of channel name to sample array (mV).
        sample_rate_hz: Sampling frequency in Hz.
        duration_ms:    Total strip duration in milliseconds.
        annotations:    Time-stamped event labels ``(time_ms, label)``.
        trigger_type:   What triggered the strip storage (e.g. ``"arrhythmia"``,
                        ``"periodic"``, ``"manual"``).
    """

    strip_id: str
    channels: Dict[str, np.ndarray]
    sample_rate_hz: int
    duration_ms: int
    annotations: List[Tuple[float, str]]
    trigger_type: str


# ---------------------------------------------------------------------------
# Rhythm-to-waveform parameter mappings
# ---------------------------------------------------------------------------

_RHYTHM_WAVEFORM_PARAMS: Dict[RhythmState, Dict[str, Any]] = {
    RhythmState.NSR: {
        "p_duration_ms": 100.0,
        "p_amplitude_mv": 0.2,
        "p_morphology": "normal",
        "qrs_duration_ms": 80.0,
        "qrs_amplitude_mv": 1.5,
        "qrs_morphology": "narrow",
        "t_duration_ms": 160.0,
        "t_amplitude_mv": 0.3,
        "t_morphology": "normal",
    },
    RhythmState.SINUS_BRADYCARDIA: {
        "p_duration_ms": 110.0,
        "p_amplitude_mv": 0.2,
        "p_morphology": "normal",
        "qrs_duration_ms": 80.0,
        "qrs_amplitude_mv": 1.5,
        "qrs_morphology": "narrow",
        "t_duration_ms": 180.0,
        "t_amplitude_mv": 0.35,
        "t_morphology": "normal",
    },
    RhythmState.SINUS_TACHYCARDIA: {
        "p_duration_ms": 80.0,
        "p_amplitude_mv": 0.25,
        "p_morphology": "peaked",
        "qrs_duration_ms": 75.0,
        "qrs_amplitude_mv": 1.4,
        "qrs_morphology": "narrow",
        "t_duration_ms": 130.0,
        "t_amplitude_mv": 0.25,
        "t_morphology": "normal",
    },
    RhythmState.ATRIAL_FIBRILLATION: {
        "p_duration_ms": 0.0,  # No discrete P waves; replaced by fibrillatory baseline
        "p_amplitude_mv": 0.0,
        "p_morphology": "absent",
        "qrs_duration_ms": 80.0,
        "qrs_amplitude_mv": 1.4,
        "qrs_morphology": "narrow",
        "t_duration_ms": 150.0,
        "t_amplitude_mv": 0.3,
        "t_morphology": "normal",
    },
    RhythmState.ATRIAL_FLUTTER: {
        "p_duration_ms": 0.0,  # Replaced by sawtooth flutter waves
        "p_amplitude_mv": 0.0,
        "p_morphology": "absent",
        "qrs_duration_ms": 80.0,
        "qrs_amplitude_mv": 1.3,
        "qrs_morphology": "narrow",
        "t_duration_ms": 140.0,
        "t_amplitude_mv": 0.25,
        "t_morphology": "normal",
    },
    RhythmState.SVT: {
        "p_duration_ms": 60.0,
        "p_amplitude_mv": 0.1,
        "p_morphology": "inverted",
        "qrs_duration_ms": 75.0,
        "qrs_amplitude_mv": 1.3,
        "qrs_morphology": "narrow",
        "t_duration_ms": 120.0,
        "t_amplitude_mv": 0.2,
        "t_morphology": "normal",
    },
    RhythmState.VENTRICULAR_TACHYCARDIA: {
        "p_duration_ms": 0.0,  # AV dissociation: P waves not reliably visible
        "p_amplitude_mv": 0.0,
        "p_morphology": "absent",
        "qrs_duration_ms": 160.0,
        "qrs_amplitude_mv": 2.0,
        "qrs_morphology": "wide",
        "t_duration_ms": 180.0,
        "t_amplitude_mv": 0.5,
        "t_morphology": "inverted",
    },
    RhythmState.VENTRICULAR_FIBRILLATION: {
        "p_duration_ms": 0.0,
        "p_amplitude_mv": 0.0,
        "p_morphology": "absent",
        "qrs_duration_ms": 0.0,  # No discrete QRS; chaotic activity
        "qrs_amplitude_mv": 0.0,
        "qrs_morphology": "wide",
        "t_duration_ms": 0.0,
        "t_amplitude_mv": 0.0,
        "t_morphology": "normal",
    },
    RhythmState.COMPLETE_HEART_BLOCK: {
        "p_duration_ms": 100.0,
        "p_amplitude_mv": 0.2,
        "p_morphology": "normal",
        "qrs_duration_ms": 140.0,
        "qrs_amplitude_mv": 1.8,
        "qrs_morphology": "wide",
        "t_duration_ms": 180.0,
        "t_amplitude_mv": 0.4,
        "t_morphology": "normal",
    },
    RhythmState.MOBITZ_I: {
        "p_duration_ms": 100.0,
        "p_amplitude_mv": 0.2,
        "p_morphology": "normal",
        "qrs_duration_ms": 80.0,
        "qrs_amplitude_mv": 1.5,
        "qrs_morphology": "narrow",
        "t_duration_ms": 160.0,
        "t_amplitude_mv": 0.3,
        "t_morphology": "normal",
    },
    RhythmState.MOBITZ_II: {
        "p_duration_ms": 100.0,
        "p_amplitude_mv": 0.2,
        "p_morphology": "normal",
        "qrs_duration_ms": 100.0,
        "qrs_amplitude_mv": 1.5,
        "qrs_morphology": "narrow",
        "t_duration_ms": 160.0,
        "t_amplitude_mv": 0.3,
        "t_morphology": "normal",
    },
    RhythmState.PVC: {
        "p_duration_ms": 0.0,
        "p_amplitude_mv": 0.0,
        "p_morphology": "absent",
        "qrs_duration_ms": 160.0,
        "qrs_amplitude_mv": 2.5,
        "qrs_morphology": "wide",
        "t_duration_ms": 200.0,
        "t_amplitude_mv": 0.6,
        "t_morphology": "inverted",
    },
    RhythmState.PAC: {
        "p_duration_ms": 80.0,
        "p_amplitude_mv": 0.15,
        "p_morphology": "bifid",
        "qrs_duration_ms": 80.0,
        "qrs_amplitude_mv": 1.4,
        "qrs_morphology": "narrow",
        "t_duration_ms": 150.0,
        "t_amplitude_mv": 0.3,
        "t_morphology": "normal",
    },
    RhythmState.JUNCTIONAL: {
        "p_duration_ms": 80.0,
        "p_amplitude_mv": 0.15,
        "p_morphology": "inverted",
        "qrs_duration_ms": 80.0,
        "qrs_amplitude_mv": 1.4,
        "qrs_morphology": "narrow",
        "t_duration_ms": 160.0,
        "t_amplitude_mv": 0.3,
        "t_morphology": "normal",
    },
    RhythmState.PACED_AAI: {
        "p_duration_ms": 100.0,
        "p_amplitude_mv": 0.2,
        "p_morphology": "normal",
        "qrs_duration_ms": 80.0,
        "qrs_amplitude_mv": 1.5,
        "qrs_morphology": "narrow",
        "t_duration_ms": 160.0,
        "t_amplitude_mv": 0.3,
        "t_morphology": "normal",
    },
    RhythmState.PACED_VVI: {
        "p_duration_ms": 100.0,
        "p_amplitude_mv": 0.15,
        "p_morphology": "normal",
        "qrs_duration_ms": 160.0,
        "qrs_amplitude_mv": 2.0,
        "qrs_morphology": "paced",
        "t_duration_ms": 180.0,
        "t_amplitude_mv": 0.5,
        "t_morphology": "inverted",
    },
    RhythmState.PACED_DDD: {
        "p_duration_ms": 100.0,
        "p_amplitude_mv": 0.2,
        "p_morphology": "normal",
        "qrs_duration_ms": 160.0,
        "qrs_amplitude_mv": 2.0,
        "qrs_morphology": "paced",
        "t_duration_ms": 180.0,
        "t_amplitude_mv": 0.5,
        "t_morphology": "inverted",
    },
    RhythmState.PACED_CRT: {
        "p_duration_ms": 100.0,
        "p_amplitude_mv": 0.2,
        "p_morphology": "normal",
        "qrs_duration_ms": 140.0,
        "qrs_amplitude_mv": 1.8,
        "qrs_morphology": "paced",
        "t_duration_ms": 170.0,
        "t_amplitude_mv": 0.4,
        "t_morphology": "inverted",
    },
}


class EGMSynthesizer:
    """Multi-channel intracardiac electrogram synthesizer.

    Generates atrial EGM, ventricular EGM, and far-field (shock) channel
    signals by assembling parameterized waveform components and adding
    realistic noise.

    Supports two modes:
    - ``"parametric"`` (Mode A): Built-in Gaussian waveform templates.
    - ``"opencarp"`` (Mode B): Pre-computed openCARP ionic-model templates.

    Parameters:
        sample_rate_hz: Sampling rate for all output channels (default 256 Hz).
        noise_floor_mv: Baseline Gaussian noise amplitude in mV (default 0.1).
        mode: ``"parametric"`` or ``"opencarp"``.
        template_library: Optional :class:`TemplateLibrary` for Mode B.
        rng: NumPy random generator for reproducibility.
    """

    def __init__(
        self,
        sample_rate_hz: int = 256,
        noise_floor_mv: float = 0.1,
        mode: str = "parametric",
        template_library: Any = None,
        rng: Optional[np.random.Generator] = None,
    ) -> None:
        self._sample_rate: int = sample_rate_hz
        self._noise_floor: float = noise_floor_mv
        self._rng: np.random.Generator = rng if rng is not None else np.random.default_rng()
        self._mode: str = mode
        self._templates: Any = None
        self._adapter: Any = None

        if mode == "opencarp":
            self._init_opencarp_mode(template_library)

    def _init_opencarp_mode(self, template_library: Any) -> None:
        """Initialize Mode B (openCARP templates)."""
        try:
            from .opencarp.template_library import TemplateLibrary
            from .opencarp.ionic_adapter import IonicAdapter

            if template_library is not None:
                self._templates = template_library
            else:
                self._templates = TemplateLibrary()

            if not self._templates.is_available():
                warnings.warn(
                    "openCARP templates not found. "
                    "Run `make generate-templates` first. "
                    "Falling back to parametric mode.",
                    stacklevel=2,
                )
                logger.warning("openCARP templates unavailable — falling back to parametric mode")
                self._mode = "parametric"
                return

            self._adapter = IonicAdapter(
                source_rate_hz=1000,
                target_rate_hz=self._sample_rate,
            )
            logger.info(
                "EGM Mode B (openCARP) active: %d rhythms, %s",
                len(self._templates.get_rhythm_names()),
                self._templates.get_stats().get("total_templates", "?"),
            )

        except ImportError:
            warnings.warn(
                "openCARP module not found. Falling back to parametric mode.",
                stacklevel=2,
            )
            self._mode = "parametric"

    @property
    def mode(self) -> str:
        """Current EGM generation mode: 'parametric' or 'opencarp'."""
        return self._mode

    @property
    def sample_rate_hz(self) -> int:
        return self._sample_rate

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def synthesize_beat(
        self,
        rhythm_state: RhythmState,
        is_paced: bool = False,
        pacing_channels: Optional[List[Literal["atrial", "ventricular"]]] = None,
        conduction_delay_ms: float = 160.0,
        rr_interval_ms: float = 800.0,
        pacing_artifact_amplitude_mv: float = 4.0,
    ) -> Dict[str, np.ndarray]:
        """Synthesize one cardiac cycle across all EGM channels.

        Parameters:
            rhythm_state:   Current cardiac rhythm determining waveform morphology.
            is_paced:       Whether the beat is paced.
            pacing_channels: Which chambers are paced (``"atrial"`` and/or ``"ventricular"``).
            conduction_delay_ms: AV conduction delay (PR interval equivalent).
            rr_interval_ms: Duration of this cardiac cycle in ms.
            pacing_artifact_amplitude_mv: Peak pacing spike amplitude.

        Returns:
            Dictionary with keys ``"atrial_egm"``, ``"ventricular_egm"``,
            ``"shock_channel"`` each mapping to a 1-D NumPy array (mV).
        """
        if pacing_channels is None:
            pacing_channels = []

        # Dispatch to Mode B if active
        if self._mode == "opencarp" and self._templates is not None:
            return self._synthesize_beat_opencarp(
                rhythm_state=rhythm_state,
                is_paced=is_paced,
                pacing_channels=pacing_channels,
                conduction_delay_ms=conduction_delay_ms,
                rr_interval_ms=rr_interval_ms,
                pacing_artifact_amplitude_mv=pacing_artifact_amplitude_mv,
            )

        params = _RHYTHM_WAVEFORM_PARAMS.get(rhythm_state, _RHYTHM_WAVEFORM_PARAMS[RhythmState.NSR])
        total_samples = max(1, int(round(rr_interval_ms * self._sample_rate / 1000.0)))

        atrial_egm = np.zeros(total_samples, dtype=np.float64)
        ventricular_egm = np.zeros(total_samples, dtype=np.float64)
        shock_channel = np.zeros(total_samples, dtype=np.float64)

        # Handle VF separately: chaotic waveform, no discrete complexes
        if rhythm_state == RhythmState.VENTRICULAR_FIBRILLATION:
            atrial_egm, ventricular_egm, shock_channel = self._synthesize_vf_beat(
                total_samples, rr_interval_ms
            )
            return {
                "atrial_egm": atrial_egm,
                "ventricular_egm": ventricular_egm,
                "shock_channel": shock_channel,
            }

        # Handle atrial flutter: sawtooth baseline
        if rhythm_state == RhythmState.ATRIAL_FLUTTER:
            atrial_egm = self._generate_flutter_waves(total_samples, rr_interval_ms)

        # Handle AF: fibrillatory baseline on atrial channel
        if rhythm_state == RhythmState.ATRIAL_FIBRILLATION:
            atrial_egm = self._generate_fibrillatory_baseline(total_samples, rr_interval_ms)

        # --- P wave ---
        p_dur = params["p_duration_ms"]
        if p_dur > 0:
            p_wave = generate_p_wave(
                duration_ms=p_dur,
                amplitude_mv=params["p_amplitude_mv"],
                sample_rate_hz=self._sample_rate,
                morphology=params["p_morphology"],
            )

            # Pacing artifact before P wave on atrial channel
            if is_paced and "atrial" in pacing_channels:
                artifact = generate_pacing_artifact(
                    pacing_artifact_amplitude_mv, self._sample_rate
                )
                self._overlay(atrial_egm, artifact, start_sample=0)
                p_start_sample = len(artifact)
            else:
                p_start_sample = 0

            # Atrial EGM: large near-field P wave
            self._overlay(atrial_egm, p_wave * 3.0, start_sample=p_start_sample)
            # Shock channel: attenuated far-field P wave
            self._overlay(shock_channel, p_wave * 0.5, start_sample=p_start_sample)

        # --- QRS complex ---
        qrs_dur = params["qrs_duration_ms"]
        if qrs_dur > 0:
            qrs = generate_qrs_complex(
                duration_ms=qrs_dur,
                amplitude_mv=params["qrs_amplitude_mv"],
                sample_rate_hz=self._sample_rate,
                morphology=params["qrs_morphology"],
            )

            # QRS onset position: after P wave + conduction delay
            qrs_onset_ms = p_dur + conduction_delay_ms if p_dur > 0 else conduction_delay_ms * 0.3
            qrs_onset_sample = int(round(qrs_onset_ms * self._sample_rate / 1000.0))
            qrs_onset_sample = min(qrs_onset_sample, total_samples - 1)

            # Ventricular pacing artifact
            if is_paced and "ventricular" in pacing_channels:
                artifact = generate_pacing_artifact(
                    pacing_artifact_amplitude_mv, self._sample_rate
                )
                art_start = max(0, qrs_onset_sample - len(artifact))
                self._overlay(ventricular_egm, artifact, start_sample=art_start)
                # Also visible on shock channel
                self._overlay(shock_channel, artifact * 0.7, start_sample=art_start)

            # Ventricular EGM: large near-field QRS
            self._overlay(ventricular_egm, qrs, start_sample=qrs_onset_sample)
            # Atrial EGM: small far-field R wave
            self._overlay(atrial_egm, qrs * 0.15, start_sample=qrs_onset_sample)
            # Shock channel: intermediate far-field QRS
            self._overlay(shock_channel, qrs * 0.6, start_sample=qrs_onset_sample)

            # --- T wave ---
            t_dur = params["t_duration_ms"]
            if t_dur > 0:
                t_wave = generate_t_wave(
                    duration_ms=t_dur,
                    amplitude_mv=params["t_amplitude_mv"],
                    sample_rate_hz=self._sample_rate,
                    morphology=params["t_morphology"],
                )

                # T wave starts after QRS + ST segment (~80 ms)
                st_segment_ms = 80.0
                t_onset_ms = qrs_onset_ms + qrs_dur + st_segment_ms
                t_onset_sample = int(round(t_onset_ms * self._sample_rate / 1000.0))
                t_onset_sample = min(t_onset_sample, total_samples - 1)

                self._overlay(ventricular_egm, t_wave * 0.8, start_sample=t_onset_sample)
                self._overlay(shock_channel, t_wave * 0.4, start_sample=t_onset_sample)
                # Small far-field T on atrial channel
                self._overlay(atrial_egm, t_wave * 0.05, start_sample=t_onset_sample)

        return {
            "atrial_egm": atrial_egm,
            "ventricular_egm": ventricular_egm,
            "shock_channel": shock_channel,
        }

    def synthesize_strip(
        self,
        beats: List[Dict[str, Any]],
        duration_ms: int,
        trigger_offset_ms: float = 10000.0,
        trigger_type: str = "arrhythmia",
    ) -> EGMStrip:
        """Assemble multiple beats into a continuous multi-channel EGM strip.

        Parameters:
            beats: List of per-beat parameter dicts.  Each dict must include:
                - ``"rhythm_state"`` (:class:`RhythmState`)
                - ``"rr_interval_ms"`` (float)
                - ``"is_paced"`` (bool, optional, default False)
                - ``"pacing_channels"`` (list, optional, default [])
                - ``"conduction_delay_ms"`` (float, optional, default 160)
            duration_ms: Target strip duration in ms.
            trigger_offset_ms: Time from strip start to trigger event.
            trigger_type: Label for what caused the strip to be stored.

        Returns:
            An :class:`EGMStrip` with concatenated, noise-added channels.
        """
        total_samples = int(round(duration_ms * self._sample_rate / 1000.0))

        atrial = np.zeros(total_samples, dtype=np.float64)
        ventricular = np.zeros(total_samples, dtype=np.float64)
        shock = np.zeros(total_samples, dtype=np.float64)

        annotations: List[Tuple[float, str]] = []
        current_sample = 0

        for beat_params in beats:
            rhythm_state: RhythmState = beat_params["rhythm_state"]
            rr_ms: float = beat_params.get("rr_interval_ms", 800.0)
            is_paced: bool = beat_params.get("is_paced", False)
            pacing_channels: List[str] = beat_params.get("pacing_channels", [])
            conduction_delay: float = beat_params.get("conduction_delay_ms", 160.0)

            beat_data = self.synthesize_beat(
                rhythm_state=rhythm_state,
                is_paced=is_paced,
                pacing_channels=pacing_channels,
                conduction_delay_ms=conduction_delay,
                rr_interval_ms=rr_ms,
            )

            beat_samples = len(beat_data["atrial_egm"])
            end_sample = min(current_sample + beat_samples, total_samples)
            usable = end_sample - current_sample

            if usable <= 0:
                break

            atrial[current_sample:end_sample] += beat_data["atrial_egm"][:usable]
            ventricular[current_sample:end_sample] += beat_data["ventricular_egm"][:usable]
            shock[current_sample:end_sample] += beat_data["shock_channel"][:usable]

            # Annotate beat onset
            beat_time_ms = current_sample * 1000.0 / self._sample_rate
            label = rhythm_state.value
            if is_paced:
                label = f"paced_{label}"
            annotations.append((beat_time_ms, label))

            current_sample = end_sample
            if current_sample >= total_samples:
                break

        # Add noise to all channels
        atrial = self.add_noise(atrial, self._noise_floor)
        ventricular = self.add_noise(ventricular, self._noise_floor)
        shock = self.add_noise(shock, self._noise_floor * 0.5)

        return EGMStrip(
            strip_id=str(uuid.uuid4()),
            channels={
                "atrial_egm": atrial,
                "ventricular_egm": ventricular,
                "shock_channel": shock,
            },
            sample_rate_hz=self._sample_rate,
            duration_ms=duration_ms,
            annotations=annotations,
            trigger_type=trigger_type,
        )

    def add_noise(
        self,
        signal: np.ndarray,
        noise_floor_mv: float,
        include_powerline: bool = True,
        powerline_freq_hz: float = 60.0,
        powerline_amplitude_mv: float = 0.02,
    ) -> np.ndarray:
        """Add realistic noise to a signal.

        Noise model includes:
        1. Gaussian white noise at the specified floor level.
        2. Optional 50/60 Hz power-line interference.
        3. Low-frequency baseline wander (~0.3 Hz).

        Parameters:
            signal:              Input signal array.
            noise_floor_mv:      RMS amplitude of white noise (mV).
            include_powerline:   Whether to add mains-frequency interference.
            powerline_freq_hz:   Power-line frequency (50 or 60 Hz).
            powerline_amplitude_mv: Peak amplitude of power-line noise (mV).

        Returns:
            A new array with noise added (original is not modified).
        """
        n = len(signal)
        noisy = signal.copy()

        # Gaussian white noise
        noisy += self._rng.normal(0.0, noise_floor_mv, size=n)

        if include_powerline and n > 0:
            t = np.arange(n, dtype=np.float64) / self._sample_rate
            # Mains interference
            phase = float(self._rng.uniform(0.0, 2.0 * np.pi))
            noisy += powerline_amplitude_mv * np.sin(
                2.0 * np.pi * powerline_freq_hz * t + phase
            )

        # Baseline wander (~0.3 Hz, low amplitude)
        if n > 0:
            t = np.arange(n, dtype=np.float64) / self._sample_rate
            wander_freq = 0.3
            wander_phase = float(self._rng.uniform(0.0, 2.0 * np.pi))
            wander_amplitude = noise_floor_mv * 0.5
            noisy += wander_amplitude * np.sin(
                2.0 * np.pi * wander_freq * t + wander_phase
            )

        return noisy

    # ------------------------------------------------------------------
    # Mode B: openCARP template-based synthesis
    # ------------------------------------------------------------------

    # Map RhythmState enum values to template library rhythm names
    _RHYTHM_STATE_TO_TEMPLATE: Dict[RhythmState, str] = {
        RhythmState.NSR: "nsr",
        RhythmState.SINUS_BRADYCARDIA: "sinus_bradycardia",
        RhythmState.SINUS_TACHYCARDIA: "sinus_tachycardia",
        RhythmState.ATRIAL_FIBRILLATION: "af",
        RhythmState.ATRIAL_FLUTTER: "atrial_flutter",
        RhythmState.SVT: "svt",
        RhythmState.VENTRICULAR_TACHYCARDIA: "vt_monomorphic",
        RhythmState.VENTRICULAR_FIBRILLATION: "vf",
        RhythmState.COMPLETE_HEART_BLOCK: "chb",
        RhythmState.MOBITZ_I: "mobitz_i",
        RhythmState.MOBITZ_II: "mobitz_ii",
        RhythmState.PVC: "pvc",
        RhythmState.PAC: "pac",
        RhythmState.JUNCTIONAL: "junctional",
        RhythmState.PACED_AAI: "paced_aai",
        RhythmState.PACED_VVI: "paced_vvi",
        RhythmState.PACED_DDD: "paced_ddd",
        RhythmState.PACED_CRT: "paced_crt",
    }

    def _synthesize_beat_opencarp(
        self,
        rhythm_state: RhythmState,
        is_paced: bool,
        pacing_channels: List[str],
        conduction_delay_ms: float,
        rr_interval_ms: float,
        pacing_artifact_amplitude_mv: float,
    ) -> Dict[str, np.ndarray]:
        """Synthesize one beat using openCARP template library (Mode B)."""
        total_samples = max(1, int(round(rr_interval_ms * self._sample_rate / 1000.0)))
        template_name = self._RHYTHM_STATE_TO_TEMPLATE.get(rhythm_state, "nsr")

        try:
            # Get multi-channel templates from library
            raw_templates = self._templates.get_beat_multichannel(
                template_name, rng=self._rng,
            )

            # Adapt to target rate and RR interval
            adapted = self._adapter.adapt_multichannel(
                raw_templates,
                target_rr_ms=rr_interval_ms,
                target_rate_hz=self._sample_rate,
            )

            atrial_egm = adapted.get("atrial", np.zeros(total_samples))
            ventricular_egm = adapted.get("ventricular", np.zeros(total_samples))
            shock_channel = adapted.get("shock", np.zeros(total_samples))

            # Ensure correct length (pad or truncate)
            atrial_egm = self._fit_to_length(atrial_egm, total_samples)
            ventricular_egm = self._fit_to_length(ventricular_egm, total_samples)
            shock_channel = self._fit_to_length(shock_channel, total_samples)

        except (KeyError, ValueError, IndexError) as exc:
            # Template not available for this rhythm — fall back to parametric
            logger.debug(
                "openCARP template unavailable for %s (%s), falling back to parametric",
                rhythm_state.value, exc,
            )
            params = _RHYTHM_WAVEFORM_PARAMS.get(
                rhythm_state, _RHYTHM_WAVEFORM_PARAMS[RhythmState.NSR],
            )
            # Delegate to the parametric path (call the base method logic directly)
            return self._synthesize_beat_parametric(
                rhythm_state, is_paced, pacing_channels,
                conduction_delay_ms, rr_interval_ms, pacing_artifact_amplitude_mv,
            )

        # Overlay pacing artifacts (same as Mode A)
        if is_paced:
            if "atrial" in pacing_channels:
                artifact = generate_pacing_artifact(
                    pacing_artifact_amplitude_mv, self._sample_rate,
                )
                self._overlay(atrial_egm, artifact, start_sample=0)

            if "ventricular" in pacing_channels:
                artifact = generate_pacing_artifact(
                    pacing_artifact_amplitude_mv, self._sample_rate,
                )
                qrs_onset_sample = int(round(conduction_delay_ms * self._sample_rate / 1000.0))
                qrs_onset_sample = min(qrs_onset_sample, total_samples - 1)
                art_start = max(0, qrs_onset_sample - len(artifact))
                self._overlay(ventricular_egm, artifact, start_sample=art_start)
                self._overlay(shock_channel, artifact * 0.7, start_sample=art_start)

        return {
            "atrial_egm": atrial_egm,
            "ventricular_egm": ventricular_egm,
            "shock_channel": shock_channel,
        }

    def _synthesize_beat_parametric(
        self,
        rhythm_state: RhythmState,
        is_paced: bool,
        pacing_channels: List[str],
        conduction_delay_ms: float,
        rr_interval_ms: float,
        pacing_artifact_amplitude_mv: float,
    ) -> Dict[str, np.ndarray]:
        """Mode A parametric synthesis — extracted for Mode B fallback."""
        params = _RHYTHM_WAVEFORM_PARAMS.get(
            rhythm_state, _RHYTHM_WAVEFORM_PARAMS[RhythmState.NSR],
        )
        total_samples = max(1, int(round(rr_interval_ms * self._sample_rate / 1000.0)))

        atrial_egm = np.zeros(total_samples, dtype=np.float64)
        ventricular_egm = np.zeros(total_samples, dtype=np.float64)
        shock_channel = np.zeros(total_samples, dtype=np.float64)

        if rhythm_state == RhythmState.VENTRICULAR_FIBRILLATION:
            a, v, s = self._synthesize_vf_beat(total_samples, rr_interval_ms)
            return {"atrial_egm": a, "ventricular_egm": v, "shock_channel": s}

        if rhythm_state == RhythmState.ATRIAL_FLUTTER:
            atrial_egm = self._generate_flutter_waves(total_samples, rr_interval_ms)
        if rhythm_state == RhythmState.ATRIAL_FIBRILLATION:
            atrial_egm = self._generate_fibrillatory_baseline(total_samples, rr_interval_ms)

        p_dur = params["p_duration_ms"]
        if p_dur > 0:
            p_wave = generate_p_wave(p_dur, params["p_amplitude_mv"],
                                      self._sample_rate, params["p_morphology"])
            if is_paced and "atrial" in pacing_channels:
                artifact = generate_pacing_artifact(pacing_artifact_amplitude_mv, self._sample_rate)
                self._overlay(atrial_egm, artifact, 0)
                p_start = len(artifact)
            else:
                p_start = 0
            self._overlay(atrial_egm, p_wave * 3.0, p_start)
            self._overlay(shock_channel, p_wave * 0.5, p_start)

        qrs_dur = params["qrs_duration_ms"]
        if qrs_dur > 0:
            qrs = generate_qrs_complex(qrs_dur, params["qrs_amplitude_mv"],
                                        self._sample_rate, params["qrs_morphology"])
            qrs_onset_ms = p_dur + conduction_delay_ms if p_dur > 0 else conduction_delay_ms * 0.3
            qrs_onset_sample = min(int(round(qrs_onset_ms * self._sample_rate / 1000.0)),
                                   total_samples - 1)
            if is_paced and "ventricular" in pacing_channels:
                artifact = generate_pacing_artifact(pacing_artifact_amplitude_mv, self._sample_rate)
                art_start = max(0, qrs_onset_sample - len(artifact))
                self._overlay(ventricular_egm, artifact, art_start)
                self._overlay(shock_channel, artifact * 0.7, art_start)
            self._overlay(ventricular_egm, qrs, qrs_onset_sample)
            self._overlay(atrial_egm, qrs * 0.15, qrs_onset_sample)
            self._overlay(shock_channel, qrs * 0.6, qrs_onset_sample)

            t_dur = params["t_duration_ms"]
            if t_dur > 0:
                t_wave = generate_t_wave(t_dur, params["t_amplitude_mv"],
                                          self._sample_rate, params["t_morphology"])
                t_onset_ms = qrs_onset_ms + qrs_dur + 80.0
                t_onset_sample = min(int(round(t_onset_ms * self._sample_rate / 1000.0)),
                                     total_samples - 1)
                self._overlay(ventricular_egm, t_wave * 0.8, t_onset_sample)
                self._overlay(shock_channel, t_wave * 0.4, t_onset_sample)
                self._overlay(atrial_egm, t_wave * 0.05, t_onset_sample)

        return {
            "atrial_egm": atrial_egm,
            "ventricular_egm": ventricular_egm,
            "shock_channel": shock_channel,
        }

    @staticmethod
    def _fit_to_length(signal: np.ndarray, target_length: int) -> np.ndarray:
        """Pad or truncate a signal to the target length."""
        if len(signal) == target_length:
            return signal
        if len(signal) > target_length:
            return signal[:target_length]
        padded = np.zeros(target_length, dtype=signal.dtype)
        padded[: len(signal)] = signal
        return padded

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _overlay(
        target: np.ndarray,
        source: np.ndarray,
        start_sample: int,
    ) -> None:
        """Add *source* into *target* starting at *start_sample* (in-place).

        Clips to the bounds of *target* without error.
        """
        if start_sample < 0:
            source = source[-start_sample:]
            start_sample = 0
        end_sample = min(start_sample + len(source), len(target))
        usable = end_sample - start_sample
        if usable > 0:
            target[start_sample:end_sample] += source[:usable]

    def _synthesize_vf_beat(
        self,
        total_samples: int,
        rr_interval_ms: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Generate chaotic VF waveform (no discrete P-QRS-T complexes).

        VF is modeled as a band-limited chaotic oscillation with amplitude
        modulation (coarse vs. fine VF).
        """
        t = np.arange(total_samples, dtype=np.float64) / self._sample_rate

        # Dominant frequency: 3-8 Hz for coarse VF
        dominant_freq = float(self._rng.uniform(3.0, 8.0))
        amplitude = float(self._rng.uniform(0.5, 2.0))

        # Base sinusoidal oscillation
        phase = float(self._rng.uniform(0.0, 2.0 * np.pi))
        vf_base = amplitude * np.sin(2.0 * np.pi * dominant_freq * t + phase)

        # Add harmonics for irregular shape
        for harmonic in range(2, 5):
            h_amp = amplitude * float(self._rng.uniform(0.1, 0.4)) / harmonic
            h_phase = float(self._rng.uniform(0.0, 2.0 * np.pi))
            vf_base += h_amp * np.sin(
                2.0 * np.pi * dominant_freq * harmonic * t + h_phase
            )

        # Amplitude modulation (waxing/waning pattern)
        mod_freq = float(self._rng.uniform(0.3, 1.0))
        mod_phase = float(self._rng.uniform(0.0, 2.0 * np.pi))
        modulation = 0.5 + 0.5 * np.sin(2.0 * np.pi * mod_freq * t + mod_phase)
        vf_signal = vf_base * modulation

        # Random jitter
        vf_signal += self._rng.normal(0.0, 0.1 * amplitude, size=total_samples)

        # All channels see similar VF signal with different gains
        atrial = vf_signal * 0.4
        ventricular = vf_signal * 1.0
        shock = vf_signal * 0.7

        return atrial, ventricular, shock

    def _generate_fibrillatory_baseline(
        self,
        total_samples: int,
        rr_interval_ms: float,
    ) -> np.ndarray:
        """Generate fibrillatory baseline for AF (irregular, low-amplitude oscillations)."""
        t = np.arange(total_samples, dtype=np.float64) / self._sample_rate

        # AF fibrillatory waves: 4-8 Hz, 0.05-0.15 mV
        baseline = np.zeros(total_samples, dtype=np.float64)
        n_components = int(self._rng.integers(4, 8))

        for _ in range(n_components):
            freq = float(self._rng.uniform(4.0, 9.0))
            amp = float(self._rng.uniform(0.03, 0.12))
            phase = float(self._rng.uniform(0.0, 2.0 * np.pi))
            baseline += amp * np.sin(2.0 * np.pi * freq * t + phase)

        return baseline

    def _generate_flutter_waves(
        self,
        total_samples: int,
        rr_interval_ms: float,
    ) -> np.ndarray:
        """Generate sawtooth flutter waves for atrial flutter (~300 bpm atrial rate)."""
        t = np.arange(total_samples, dtype=np.float64) / self._sample_rate

        flutter_rate_hz = 5.0  # ~300 bpm = 5 Hz
        flutter_amplitude = 0.3  # mV

        # Sawtooth wave using Fourier series approximation
        sawtooth = np.zeros(total_samples, dtype=np.float64)
        for k in range(1, 8):
            sign = (-1.0) ** (k + 1)
            sawtooth += sign * np.sin(2.0 * np.pi * flutter_rate_hz * k * t) / k

        sawtooth *= (2.0 / np.pi) * flutter_amplitude

        return sawtooth
