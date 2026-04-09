#!/usr/bin/env python3
"""Chamber Sentinel CIED Simulator — Demonstration Script.

Runs a single patient through 30 simulated days, narrates key events,
and produces a comparison between current and Chambers architectures.
"""

from __future__ import annotations

import sys
import time

from src.orchestrator import SimulationOrchestrator, SimulationConfig
from src.generator.cohort import CohortDistribution


def print_header() -> None:
    print("=" * 70)
    print("  CHAMBER SENTINEL — CIED Telemetry Simulator")
    print("  Burn-by-Default vs Persist-by-Default Architecture Comparison")
    print("=" * 70)
    print()


def print_section(title: str) -> None:
    print(f"\n{'─' * 50}")
    print(f"  {title}")
    print(f"{'─' * 50}")


def format_bytes(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    else:
        return f"{b / (1024 * 1024):.2f} MB"


def run_demo(days: int = 30, cohort_size: int = 1, verbose: bool = True,
             egm_mode: str = "parametric") -> dict:
    """Run the demo simulation."""
    print_header()

    # Configure simulation
    config = SimulationConfig(
        duration_days=days,
        clock_speed=86400.0,  # 1 day per second
        time_step_s=3600.0,   # 1-hour steps
        random_seed=42,
        cohort_size=cohort_size,
        relay_ttl_s=259200,   # 72 hours
        egm_mode=egm_mode,
    )

    mode_label = "openCARP (biophysical)" if egm_mode == "opencarp" else "Parametric (Gaussian)"
    print(f"Configuration:")
    print(f"  Duration:         {days} days")
    print(f"  Patients:         {cohort_size}")
    print(f"  Relay TTL:        72 hours")
    print(f"  EGM Mode:         {mode_label}")
    print(f"  Time step:        1 hour")
    print(f"  Random seed:      42")

    # Initialize
    print_section("Initializing Simulation")
    orchestrator = SimulationOrchestrator(config)
    orchestrator.initialize()

    patients = orchestrator.cohort_manager.patients
    print(f"  Created {len(patients)} virtual patient(s):")
    for p in patients[:5]:
        print(f"    - {p.profile_id}: {p.diagnosis}, {p.device_type}, age {p.age}")
    if len(patients) > 5:
        print(f"    ... and {len(patients) - 5} more")

    # Run with progress callback
    print_section("Running Simulation")
    start_time = time.time()
    last_report_day = -1

    def progress_callback(clock, stats):
        nonlocal last_report_day
        day = clock.day_number
        if day > last_report_day and day % max(1, days // 10) == 0:
            last_report_day = day
            current_bytes = stats["current_arch"]["total_bytes"]
            burns = stats["total_burns"]
            events = stats["total_events"]
            if verbose:
                print(f"  Day {day:4d} | Events: {events:,} | "
                      f"Current arch: {format_bytes(current_bytes)} | "
                      f"Burns: {burns:,}")

    final_stats = orchestrator.run(callback=progress_callback)
    elapsed = time.time() - start_time

    # Results
    print_section("Simulation Complete")
    print(f"  Wall time:        {elapsed:.1f} seconds")
    print(f"  Simulated days:   {orchestrator.clock.day_number}")
    print(f"  Total events:     {final_stats['total_events']:,}")
    print(f"  Total burns:      {final_stats['total_burns']:,}")

    # Comparison
    comparison = orchestrator.get_comparison_snapshot()
    print_section("Architecture Comparison")

    current_mb = comparison["persistence"]["current_mb"]
    chambers_mb = comparison["persistence"]["chambers_mb"]
    ratio = comparison["persistence"]["ratio"]

    print(f"  {'Metric':<35} {'Current':>12} {'Chambers':>12} {'Ratio':>8}")
    print(f"  {'─' * 67}")
    print(f"  {'Data persisted':<35} {current_mb:>10.2f} MB {chambers_mb:>10.2f} MB {ratio:>7.1f}x")
    print(f"  {'Total burns':<35} {'N/A':>12} {final_stats['total_burns']:>12,}")
    print(f"  {'Relay items':<35} {'N/A':>12} {comparison.get('chambers_arch', {}).get('relay', {}).get('items_in_relay', 'N/A'):>12}")

    burn_stats = final_stats.get("chambers_arch", {}).get("burn_scheduler", {})
    print(f"  {'Bytes destroyed':<35} {'0':>12} {format_bytes(burn_stats.get('total_bytes_burned', 0)):>12}")

    # World status
    print_section("Chambers World Status")
    worlds = final_stats.get("chambers_arch", {}).get("worlds", {})
    for name, status in worlds.items():
        accepted = status.get("total_accepted", 0)
        burned = status.get("total_burned", 0)
        active = status.get("active_records", 0)
        total_mb = status.get("total_mb", 0)
        print(f"  {name:<25} accepted: {accepted:>6,}  burned: {burned:>6,}  "
              f"active: {active:>6,}  size: {total_mb:.2f} MB")

    # Key insights
    print_section("Key Insights")
    if ratio > 1:
        print(f"  * Current architecture retains {ratio:.1f}x more data than Chambers")
    print(f"  * Chambers relay holds at most 72 hours of data")
    print(f"  * {final_stats['total_burns']:,} data elements were permanently destroyed")
    if ratio > 10:
        print(f"  * After {days} days, the persistence gap is already {ratio:.0f}x")
        print(f"    and will continue to grow indefinitely under current architecture")

    print(f"\n{'=' * 70}")
    print(f"  Demo complete. See PRD for full methodology.")
    print(f"{'=' * 70}\n")

    return final_stats


def main() -> None:
    """Entry point for the demo script.

    Usage: python -m scripts.demo [days] [cohort_size] [--egm-mode opencarp]
    """
    days = 30
    cohort_size = 1
    egm_mode = "parametric"

    args = sys.argv[1:]
    positional = []
    i = 0
    while i < len(args):
        if args[i] == "--egm-mode" and i + 1 < len(args):
            egm_mode = args[i + 1]
            i += 2
        else:
            positional.append(args[i])
            i += 1

    if len(positional) > 0:
        try:
            days = int(positional[0])
        except ValueError:
            pass
    if len(positional) > 1:
        try:
            cohort_size = int(positional[1])
        except ValueError:
            pass

    run_demo(days=days, cohort_size=cohort_size, egm_mode=egm_mode)


if __name__ == "__main__":
    main()
