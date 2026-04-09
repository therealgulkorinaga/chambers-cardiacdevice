"""Tests for the simulation orchestrator."""

import pytest
from src.orchestrator import SimulationOrchestrator, SimulationConfig


class TestOrchestrator:
    """Test the main simulation orchestrator."""

    def test_initialization(self):
        config = SimulationConfig(duration_days=1, cohort_size=1, random_seed=42)
        orch = SimulationOrchestrator(config)
        orch.initialize()

        assert len(orch.cohort_manager.patients) == 1
        assert len(orch.patient_simulators) == 1
        assert orch._status == "initialized"

    def test_run_single_patient_one_day(self):
        config = SimulationConfig(
            duration_days=1,
            cohort_size=1,
            time_step_s=3600.0,
            random_seed=42,
        )
        orch = SimulationOrchestrator(config)
        orch.initialize()
        stats = orch.run()

        assert stats["status"] == "completed"
        assert stats["total_events"] > 0
        assert stats["clock"]["sim_days"] >= 1.0

    def test_run_produces_events_in_both_architectures(self):
        config = SimulationConfig(
            duration_days=7,
            cohort_size=1,
            time_step_s=3600.0,
            random_seed=42,
        )
        orch = SimulationOrchestrator(config)
        orch.initialize()
        stats = orch.run()

        # Current arch should have accumulated data
        current_bytes = stats["current_arch"]["total_bytes"]
        assert current_bytes > 0, "Current arch should have persisted data"

        # Chambers arch should have processed data through relay
        relay_stats = stats["chambers_arch"]["relay"]
        assert relay_stats["total_received"] > 0, "Relay should have received events"

    def test_comparison_snapshot(self):
        config = SimulationConfig(
            duration_days=7,
            cohort_size=1,
            time_step_s=3600.0,
            random_seed=42,
        )
        orch = SimulationOrchestrator(config)
        orch.initialize()
        orch.run()

        comparison = orch.get_comparison_snapshot()
        assert "persistence" in comparison
        assert comparison["persistence"]["current_bytes"] >= 0
        assert comparison["persistence"]["chambers_bytes"] >= 0

    def test_multiple_patients(self):
        config = SimulationConfig(
            duration_days=3,
            cohort_size=5,
            time_step_s=3600.0,
            random_seed=42,
        )
        orch = SimulationOrchestrator(config)
        orch.initialize()

        assert len(orch.patient_simulators) == 5
        stats = orch.run()
        assert stats["patients"] == 5

    def test_pause_resume(self):
        config = SimulationConfig(
            duration_days=2,
            cohort_size=1,
            time_step_s=3600.0,
            random_seed=42,
        )
        orch = SimulationOrchestrator(config)
        orch.initialize()

        # Run for a bit, then pause
        tick_count = 0
        def callback(clock, stats):
            nonlocal tick_count
            tick_count += 1
            if tick_count >= 5:
                orch.pause()

        orch.run(callback=callback)
        assert orch._status == "paused"
        assert orch.clock.time_s > 0
        assert orch.clock.time_s < 2 * 86400  # Should not have completed

    def test_stop(self):
        config = SimulationConfig(
            duration_days=10,
            cohort_size=1,
            time_step_s=3600.0,
            random_seed=42,
        )
        orch = SimulationOrchestrator(config)
        orch.initialize()

        def callback(clock, stats):
            if clock.day_number >= 2:
                orch.stop()

        orch.run(callback=callback)
        assert orch._status == "stopped"

    def test_inject_adverse_event(self):
        config = SimulationConfig(
            duration_days=1,
            cohort_size=1,
            time_step_s=3600.0,
            random_seed=42,
        )
        orch = SimulationOrchestrator(config)
        orch.initialize()

        patient_id = orch.cohort_manager.patients[0].patient_id
        result = orch.inject_adverse_event(patient_id, "lead_fracture", "major")
        assert result["injected"] is True

    def test_create_safety_hold(self):
        config = SimulationConfig(
            duration_days=7,
            cohort_size=1,
            time_step_s=3600.0,
            random_seed=42,
        )
        orch = SimulationOrchestrator(config)
        orch.initialize()
        orch.run()

        patient_id = orch.cohort_manager.patients[0].patient_id
        result = orch.create_safety_hold(patient_id, "Test investigation")
        assert "hold_id" in result

    def test_time_series_snapshots(self):
        config = SimulationConfig(
            duration_days=7,
            cohort_size=1,
            time_step_s=3600.0,
            random_seed=42,
            snapshot_interval_s=86400.0,  # Daily
        )
        orch = SimulationOrchestrator(config)
        orch.initialize()
        orch.run()

        snapshots = orch.time_series
        assert len(snapshots) >= 7, f"Expected at least 7 daily snapshots, got {len(snapshots)}"
        assert all("current_arch" in s for s in snapshots)
        assert all("chambers_arch" in s for s in snapshots)


class TestClinicalWorld:
    """Test the Clinical World burn semantics."""

    def test_accepts_clinical_data(self):
        from src.chambers_arch.worlds.clinical_world import ClinicalWorld
        from src.generator.stream import EventType

        world = ClinicalWorld()
        record = world.accept_data(
            EventType.HEARTBEAT, "P1", "DEV1",
            {"heart_rate": 72}, 1000.0, 64,
        )
        assert record is not None

    def test_rejects_activity_data(self):
        from src.chambers_arch.worlds.clinical_world import ClinicalWorld
        from src.generator.stream import EventType

        world = ClinicalWorld()
        record = world.accept_data(
            EventType.ACTIVITY, "P1", "DEV1",
            {"counts_per_min": 50}, 1000.0, 32,
        )
        assert record is None

    def test_burn_after_delivery_and_ack(self):
        from src.chambers_arch.worlds.clinical_world import ClinicalWorld
        from src.generator.stream import EventType

        world = ClinicalWorld()

        # Add an alert
        record = world.accept_data(
            EventType.ALERT, "P1", "DEV1",
            {"alert_type": "AT_AF", "priority": "medium"}, 1000.0, 256,
        )
        assert record is not None

        # Not burn-eligible yet
        candidates = world.get_burn_candidates(1000.0)
        assert record.record_id not in candidates

        # Deliver to patient
        world.confirm_delivery_to_patient(record.record_id, 1001.0)

        # Still not eligible (alert needs ack)
        candidates = world.get_burn_candidates(1001.0)
        assert record.record_id not in candidates

        # Clinician acknowledges
        world.confirm_clinician_acknowledgment(record.record_id, "DR-001", 1002.0)

        # Now eligible
        candidates = world.get_burn_candidates(1002.0)
        assert record.record_id in candidates


class TestDeviceMaintenanceWorld:
    """Test Device Maintenance World scope enforcement."""

    def test_rejects_iegm_data(self):
        from src.chambers_arch.worlds.device_maintenance_world import DeviceMaintenanceWorld
        from src.generator.stream import EventType

        world = DeviceMaintenanceWorld()

        # Should reject heartbeat (IEGM data)
        record = world.accept_data(
            EventType.HEARTBEAT, "P1", "DEV1",
            {"heart_rate": 72}, 1000.0, 64,
        )
        assert record is None

    def test_accepts_device_status(self):
        from src.chambers_arch.worlds.device_maintenance_world import DeviceMaintenanceWorld
        from src.generator.stream import EventType

        world = DeviceMaintenanceWorld()
        record = world.accept_data(
            EventType.DEVICE_STATUS, "P1", "DEV1",
            {"battery_voltage": 2.75, "device_model": "TestDevice"}, 1000.0, 128,
        )
        assert record is not None

    def test_rolling_window_burn(self):
        from src.chambers_arch.worlds.device_maintenance_world import DeviceMaintenanceWorld
        from src.generator.stream import EventType

        world = DeviceMaintenanceWorld(retention_window_days=1)  # 1 day for testing

        record = world.accept_data(
            EventType.DEVICE_STATUS, "P1", "DEV1",
            {"battery_voltage": 2.75}, 1000.0, 128,
        )

        # Not eligible yet
        candidates = world.get_burn_candidates(1000.0)
        assert record.record_id not in candidates

        # After window expires
        candidates = world.get_burn_candidates(1000.0 + 86401)
        assert record.record_id in candidates


class TestBurnVerifier:
    """Test burn verification system."""

    def test_crypto_deletion(self):
        from src.chambers_arch.burn.verifier import BurnVerifier

        verifier = BurnVerifier()
        verifier.on_record_created("R1")
        assert verifier.crypto.is_recoverable("R1")

        cert = verifier.on_record_burned("R1", "clinical", 1000.0)
        assert not verifier.crypto.is_recoverable("R1")
        assert cert.record_id == "R1"

    def test_full_verification(self):
        from src.chambers_arch.burn.verifier import BurnVerifier

        verifier = BurnVerifier()
        verifier.on_record_created("R1")
        verifier.on_record_burned("R1", "clinical", 1000.0)

        result = verifier.verify_burn("R1")
        assert result["fully_verified"] is True
        assert result["crypto"] is True
        assert result["merkle"] is True
        assert result["audit"] is True

    def test_burn_failure_injection(self):
        from src.chambers_arch.burn.verifier import BurnVerifier

        verifier = BurnVerifier()
        verifier.on_record_created("R1")

        # Inject failure — audit says burned but crypto/merkle disagree
        verifier.inject_burn_failure("R1")

        result = verifier.verify_burn("R1")
        assert result["fully_verified"] is False
        assert result["crypto"] is False  # Key still exists
        assert result["audit"] is True  # Audit says burned


class TestSafetyHold:
    """Test safety investigation hold mechanism."""

    def test_hold_prevents_burn(self):
        from src.chambers_arch.worlds.clinical_world import ClinicalWorld
        from src.generator.stream import EventType

        world = ClinicalWorld()

        record = world.accept_data(
            EventType.HEARTBEAT, "P1", "DEV1",
            {"heart_rate": 72}, 1000.0, 64,
        )

        # Apply hold
        world.apply_hold("P1", "HOLD-1", 1001.0)

        # Deliver and ack
        world.confirm_delivery_to_patient(record.record_id, 1002.0)

        # Should NOT be in burn candidates (held)
        candidates = world.get_burn_candidates(1002.0)
        assert record.record_id not in candidates

        # Attempt burn — should fail
        result = world.burn(record.record_id, 1003.0)
        assert result is False

    def test_hold_release_enables_burn(self):
        from src.chambers_arch.worlds.clinical_world import ClinicalWorld
        from src.generator.stream import EventType

        world = ClinicalWorld()

        record = world.accept_data(
            EventType.HEARTBEAT, "P1", "DEV1",
            {"heart_rate": 72}, 1000.0, 64,
        )
        world.confirm_delivery_to_patient(record.record_id, 1001.0)

        # Hold then release
        world.apply_hold("P1", "HOLD-1", 1002.0)
        world.release_hold("P1", 1003.0)

        # Now should be burnable
        result = world.burn(record.record_id, 1004.0)
        assert result is True
