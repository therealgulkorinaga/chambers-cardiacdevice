#!/usr/bin/env python3
"""Standalone validation script for generated openCARP beat templates.

Runnable as::

    python scripts/validate_templates.py [--templates-dir PATH] [--report-path PATH]

Performs the following automated checks for each rhythm type found in the
template catalog:

1. **Catalog completeness** -- all 18 rhythm types are present.
2. **Per-rhythm structural validation**:
   - Array shape is 2-D ``[n_beats, samples_per_beat]``.
   - No ``NaN`` or ``Inf`` values.
   - Amplitude within physiological bounds per channel.
   - Beat duration within expected range (200-2000 ms at source sample rate).
3. **Spectral validation** for selected rhythms:
   - NSR: dominant frequency 1.0 -- 1.7 Hz
   - AF atrial channel: dominant 4 -- 9 Hz
   - VT: dominant 2 -- 4.2 Hz
   - VF: dominant 3 -- 7 Hz
4. **Cross-channel consistency** -- all channels for the same rhythm have the
   same beat count.

Outputs a JSON validation report to *report-path* and prints a console summary
with PASS/FAIL per rhythm.  Exits with code 0 if all checks pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import signal as sp_signal


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# All 18 rhythm types (must match RhythmState.value strings)
ALL_RHYTHM_NAMES: List[str] = [
    "normal_sinus_rhythm",
    "sinus_bradycardia",
    "sinus_tachycardia",
    "atrial_fibrillation",
    "atrial_flutter",
    "supraventricular_tachycardia",
    "ventricular_tachycardia",
    "ventricular_fibrillation",
    "complete_heart_block",
    "mobitz_type_i",
    "mobitz_type_ii",
    "premature_ventricular_complex",
    "premature_atrial_complex",
    "junctional_rhythm",
    "paced_aai",
    "paced_vvi",
    "paced_ddd",
    "paced_crt",
]

CHANNEL_NAMES: List[str] = ["atrial", "ventricular", "shock"]

# Physiological amplitude bounds per channel (mV)
AMPLITUDE_BOUNDS: Dict[str, Tuple[float, float]] = {
    "atrial": (0.01, 10.0),
    "ventricular": (0.05, 25.0),
    "shock": (0.01, 5.0),
}

# Beat duration bounds in milliseconds (at source rate)
BEAT_DURATION_MS_BOUNDS: Tuple[float, float] = (200.0, 2000.0)

# Spectral validation: rhythm_name -> (channel, min_hz, max_hz)
SPECTRAL_CHECKS: Dict[str, Tuple[str, float, float]] = {
    "normal_sinus_rhythm": ("ventricular", 1.0, 1.7),
    "atrial_fibrillation": ("atrial", 4.0, 9.0),
    "ventricular_tachycardia": ("ventricular", 2.0, 4.2),
    "ventricular_fibrillation": ("ventricular", 3.0, 7.0),
}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _find_dominant_frequency(
    signal_1d: np.ndarray,
    sample_rate_hz: float,
    freq_range: Tuple[float, float] = (0.5, 50.0),
) -> float:
    """Return the dominant frequency (Hz) of a 1-D signal within *freq_range*.

    Uses Welch's method with a Hann window for robust spectral estimation.
    """
    if signal_1d.size < 8:
        return 0.0

    # Use a segment length that is at most the signal length
    nperseg = min(len(signal_1d), 256)
    freqs, psd = sp_signal.welch(
        signal_1d,
        fs=sample_rate_hz,
        window="hann",
        nperseg=nperseg,
        noverlap=nperseg // 2,
        detrend="linear",
    )

    # Mask to the frequency range of interest
    mask = (freqs >= freq_range[0]) & (freqs <= freq_range[1])
    if not np.any(mask):
        return 0.0

    masked_psd = psd[mask]
    masked_freqs = freqs[mask]

    dominant_idx = int(np.argmax(masked_psd))
    return float(masked_freqs[dominant_idx])


# ---------------------------------------------------------------------------
# Per-rhythm validation
# ---------------------------------------------------------------------------


def validate_rhythm(
    templates_dir: Path,
    rhythm_name: str,
    catalog_entry: Dict[str, Any],
    source_rate_hz: float,
) -> Dict[str, Any]:
    """Run all validation checks for a single rhythm type.

    Returns a dict with keys: ``rhythm``, ``passed``, ``checks`` (list of
    individual check results).
    """
    rhythm_dir = templates_dir / rhythm_name
    checks: List[Dict[str, Any]] = []
    overall_pass = True

    expected_channels = catalog_entry.get("channels", CHANNEL_NAMES)
    expected_n_beats = catalog_entry.get("n_beats", None)
    expected_samples = catalog_entry.get("samples_per_beat", None)

    beat_counts_per_channel: Dict[str, int] = {}

    for ch_name in expected_channels:
        npy_path = rhythm_dir / f"{ch_name}.npy"

        # --- File existence ---
        if not npy_path.exists():
            checks.append({
                "check": f"{ch_name}_file_exists",
                "passed": False,
                "detail": f"File not found: {npy_path}",
            })
            overall_pass = False
            continue

        arr = np.load(str(npy_path))

        # --- Shape: must be 2-D [n_beats, samples_per_beat] ---
        shape_ok = arr.ndim == 2
        checks.append({
            "check": f"{ch_name}_shape_2d",
            "passed": shape_ok,
            "detail": f"shape={arr.shape}, ndim={arr.ndim}",
        })
        if not shape_ok:
            overall_pass = False
            continue

        n_beats, samples_per_beat = arr.shape
        beat_counts_per_channel[ch_name] = n_beats

        # --- No NaN / Inf ---
        has_nan = bool(np.any(np.isnan(arr)))
        has_inf = bool(np.any(np.isinf(arr)))
        finite_ok = not has_nan and not has_inf
        checks.append({
            "check": f"{ch_name}_finite_values",
            "passed": finite_ok,
            "detail": f"nan={has_nan}, inf={has_inf}",
        })
        if not finite_ok:
            overall_pass = False

        # --- Amplitude within physiological bounds ---
        lo, hi = AMPLITUDE_BOUNDS.get(ch_name, (0.0, 100.0))
        max_abs = float(np.max(np.abs(arr)))
        amp_ok = lo <= max_abs <= hi
        checks.append({
            "check": f"{ch_name}_amplitude_bounds",
            "passed": amp_ok,
            "detail": f"max_abs={max_abs:.4f} mV, bounds=[{lo}, {hi}]",
        })
        if not amp_ok:
            overall_pass = False

        # --- Beat duration within expected range ---
        beat_duration_ms = samples_per_beat / source_rate_hz * 1000.0
        dur_lo, dur_hi = BEAT_DURATION_MS_BOUNDS
        dur_ok = dur_lo <= beat_duration_ms <= dur_hi
        checks.append({
            "check": f"{ch_name}_beat_duration_ms",
            "passed": dur_ok,
            "detail": f"duration={beat_duration_ms:.1f} ms, bounds=[{dur_lo}, {dur_hi}]",
        })
        if not dur_ok:
            overall_pass = False

    # --- Cross-channel consistency: same beat count ---
    if len(beat_counts_per_channel) > 1:
        unique_counts = set(beat_counts_per_channel.values())
        cc_ok = len(unique_counts) == 1
        checks.append({
            "check": "cross_channel_beat_count",
            "passed": cc_ok,
            "detail": f"counts_per_channel={dict(beat_counts_per_channel)}",
        })
        if not cc_ok:
            overall_pass = False

    # --- Spectral validation (only for selected rhythms) ---
    if rhythm_name in SPECTRAL_CHECKS:
        spec_ch, spec_lo, spec_hi = SPECTRAL_CHECKS[rhythm_name]
        npy_path = rhythm_dir / f"{spec_ch}.npy"
        if npy_path.exists():
            arr = np.load(str(npy_path))
            if arr.ndim == 2 and arr.shape[0] > 0:
                # Concatenate all beats into a continuous signal for spectral analysis
                continuous = arr.ravel()
                dom_freq = _find_dominant_frequency(
                    continuous, source_rate_hz, freq_range=(0.5, 50.0),
                )
                spec_ok = spec_lo <= dom_freq <= spec_hi
                checks.append({
                    "check": f"spectral_{rhythm_name}",
                    "passed": spec_ok,
                    "detail": (
                        f"dominant_freq={dom_freq:.2f} Hz on {spec_ch}, "
                        f"expected=[{spec_lo}, {spec_hi}]"
                    ),
                })
                if not spec_ok:
                    overall_pass = False
        else:
            checks.append({
                "check": f"spectral_{rhythm_name}",
                "passed": False,
                "detail": f"Channel file not found for spectral check: {npy_path}",
            })
            overall_pass = False

    return {
        "rhythm": rhythm_name,
        "passed": overall_pass,
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# Top-level validation
# ---------------------------------------------------------------------------


def validate_templates(
    templates_dir: Path,
    report_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run all validations and return the full report dict.

    Parameters:
        templates_dir: Path to the openCARP templates directory.
        report_path:   If provided, the JSON report is written here.

    Returns:
        Report dictionary with keys ``all_passed``, ``catalog_complete``,
        ``rhythms`` (list of per-rhythm results), and ``summary``.
    """
    catalog_path = templates_dir / "template_catalog.json"

    report: Dict[str, Any] = {
        "templates_dir": str(templates_dir),
        "all_passed": True,
        "catalog_complete": False,
        "missing_rhythms": [],
        "rhythms": [],
        "summary": {},
    }

    # ------------------------------------------------------------------
    # 1. Load catalog
    # ------------------------------------------------------------------
    if not catalog_path.exists():
        report["all_passed"] = False
        report["summary"]["error"] = f"Catalog not found: {catalog_path}"
        _write_report(report, report_path)
        return report

    with open(catalog_path) as f:
        catalog = json.load(f)

    source_rate_hz = float(catalog.get("source_rate_hz", 1000))
    rhythms_in_catalog = set(catalog.get("rhythms", {}).keys())

    # ------------------------------------------------------------------
    # 2. Check catalog completeness
    # ------------------------------------------------------------------
    missing = sorted(set(ALL_RHYTHM_NAMES) - rhythms_in_catalog)
    report["missing_rhythms"] = missing
    report["catalog_complete"] = len(missing) == 0
    if missing:
        report["all_passed"] = False

    # ------------------------------------------------------------------
    # 3. Per-rhythm validation
    # ------------------------------------------------------------------
    pass_count = 0
    fail_count = 0

    for rhythm_name in ALL_RHYTHM_NAMES:
        if rhythm_name not in rhythms_in_catalog:
            result = {
                "rhythm": rhythm_name,
                "passed": False,
                "checks": [{"check": "in_catalog", "passed": False, "detail": "missing"}],
            }
        else:
            result = validate_rhythm(
                templates_dir,
                rhythm_name,
                catalog["rhythms"][rhythm_name],
                source_rate_hz,
            )

        report["rhythms"].append(result)
        if result["passed"]:
            pass_count += 1
        else:
            fail_count += 1
            report["all_passed"] = False

    report["summary"] = {
        "total": len(ALL_RHYTHM_NAMES),
        "passed": pass_count,
        "failed": fail_count,
    }

    # ------------------------------------------------------------------
    # 4. Write report
    # ------------------------------------------------------------------
    _write_report(report, report_path)

    return report


def _write_report(report: Dict[str, Any], report_path: Optional[Path]) -> None:
    """Write the JSON report to disk if a path is given."""
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------


def print_summary(report: Dict[str, Any]) -> None:
    """Print a human-readable summary table to stdout."""
    print()
    print("=" * 72)
    print("  openCARP Template Validation Report")
    print("=" * 72)
    print(f"  Templates dir : {report['templates_dir']}")
    print(f"  Catalog complete: {report['catalog_complete']}")
    if report["missing_rhythms"]:
        print(f"  Missing rhythms : {', '.join(report['missing_rhythms'])}")
    print("-" * 72)
    print(f"  {'Rhythm':<45} {'Result':<10}")
    print("-" * 72)

    for entry in report["rhythms"]:
        status = "PASS" if entry["passed"] else "FAIL"
        marker = "  " if entry["passed"] else "X "
        print(f"  {marker}{entry['rhythm']:<43} {status:<10}")

        # Print failed checks as sub-items
        if not entry["passed"]:
            for chk in entry.get("checks", []):
                if not chk["passed"]:
                    print(f"      -> {chk['check']}: {chk['detail']}")

    print("-" * 72)
    summary = report.get("summary", {})
    total = summary.get("total", "?")
    passed = summary.get("passed", "?")
    failed = summary.get("failed", "?")
    overall = "ALL PASSED" if report["all_passed"] else "FAILURES DETECTED"
    print(f"  Total: {total}   Passed: {passed}   Failed: {failed}   [{overall}]")
    print("=" * 72)
    print()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate openCARP beat templates for the Chamber Sentinel CIED Simulator.",
    )
    parser.add_argument(
        "--templates-dir",
        type=str,
        default=None,
        help=(
            "Path to the templates directory.  Defaults to "
            "<project>/data/opencarp_templates."
        ),
    )
    parser.add_argument(
        "--report-path",
        type=str,
        default=None,
        help="Path for the JSON validation report.  Defaults to <templates-dir>/validation_report.json.",
    )

    args = parser.parse_args()

    # Resolve templates directory
    if args.templates_dir:
        templates_dir = Path(args.templates_dir)
    else:
        # Default location relative to project root
        project_root = Path(__file__).resolve().parent.parent
        templates_dir = project_root / "src" / "generator" / "cardiac" / "opencarp" / "templates"

    if not templates_dir.exists():
        print(f"ERROR: Templates directory does not exist: {templates_dir}", file=sys.stderr)
        sys.exit(1)

    # Resolve report path
    if args.report_path:
        report_path = Path(args.report_path)
    else:
        report_path = templates_dir / "validation_report.json"

    # Run validation
    report = validate_templates(templates_dir, report_path)

    # Print summary
    print_summary(report)

    if report_path.exists():
        print(f"  Full report written to: {report_path}")

    # Exit code
    sys.exit(0 if report["all_passed"] else 1)


if __name__ == "__main__":
    main()
