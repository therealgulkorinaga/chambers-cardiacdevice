# Issue & Ticket List
# Chamber Sentinel CIED Telemetry Simulator

**Companion to:** PRD_CIED_TELEMETRY_SIMULATOR.md
**Date:** 2026-04-08
**Total Issues:** 143
**Organized by:** Epic > Story > Task/Sub-task

---

## Notation

- **[E-XX]** = Epic
- **[S-XX]** = Story
- **[T-XXX]** = Task
- **Priority:** P0 (critical path), P1 (high), P2 (medium), P3 (nice-to-have)
- **Size:** XS (< 2h), S (2-4h), M (4-8h), L (1-2d), XL (2-5d), XXL (5+d)
- **Depends:** lists blocking issue IDs

---

# EPIC E-01: Project Scaffolding & Infrastructure

> Set up the repository, CI/CD, containerization, and shared configuration before any feature work begins.

---

### S-01: Repository & Build System Setup

**T-001: Initialize Python project with pyproject.toml**
- Priority: P0 | Size: S | Depends: none
- Description: Create the repository with `pyproject.toml` using modern Python packaging (PEP 621). Target Python 3.12+. Configure project metadata, entry points, and dependency groups (`[project.optional-dependencies]` for `dev`, `test`, `viz`).
- Acceptance Criteria:
  - [ ] `pyproject.toml` with project name `chamber-sentinel-cied-sim`
  - [ ] Dependency groups: core, dev, test, viz
  - [ ] `pip install -e .` works cleanly
  - [ ] `pip install -e ".[dev,test,viz]"` installs all optional deps
  - [ ] Python version constraint: `requires-python = ">=3.12"`

**T-002: Create directory structure per PRD Section 4.3**
- Priority: P0 | Size: S | Depends: T-001
- Description: Create the full directory tree with `__init__.py` files and placeholder modules. Every module listed in PRD Section 4.3 gets a file with a docstring and a stub class/function.
- Acceptance Criteria:
  - [ ] All directories from PRD 4.3 exist
  - [ ] All `.py` files exist with docstrings
  - [ ] `import src.generator.cardiac.rhythm_engine` resolves without error
  - [ ] `import src.chambers_arch.worlds.clinical_world` resolves without error

**T-003: Configure linting and formatting (ruff, mypy)**
- Priority: P1 | Size: XS | Depends: T-001
- Description: Add `ruff` configuration in `pyproject.toml` (rules: `E`, `F`, `W`, `I`, `N`, `UP`, `B`, `SIM`). Add `mypy` with strict mode for `src/`. Add `pre-commit` hooks for both.
- Acceptance Criteria:
  - [ ] `ruff check src/` passes on clean repo
  - [ ] `ruff format src/` produces no changes on clean repo
  - [ ] `mypy src/` passes with strict mode
  - [ ] `.pre-commit-config.yaml` includes ruff + mypy

**T-004: Create Makefile with standard targets**
- Priority: P1 | Size: XS | Depends: T-001
- Description: Targets: `install`, `dev`, `test`, `lint`, `format`, `typecheck`, `docker-up`, `docker-down`, `run-sim`, `run-dashboard`, `run-api`, `generate-report`, `clean`.
- Acceptance Criteria:
  - [ ] `make install` installs core deps
  - [ ] `make dev` installs all deps including dev/test/viz
  - [ ] `make test` runs pytest
  - [ ] `make lint` runs ruff + mypy

---

### S-02: Docker & Infrastructure

**T-005: Create Dockerfile for simulator application**
- Priority: P0 | Size: M | Depends: T-002
- Description: Multi-stage Dockerfile. Build stage installs dependencies. Runtime stage copies only installed packages + source. Non-root user. Health check endpoint.
- Acceptance Criteria:
  - [ ] `docker build -t cied-sim .` succeeds
  - [ ] Container runs as non-root user
  - [ ] Image size < 500 MB
  - [ ] Health check at `/health` returns 200

**T-006: Create docker-compose.yml with all services**
- Priority: P0 | Size: M | Depends: T-005
- Description: Services per PRD Section 14.1: `simulator`, `postgres` (with TimescaleDB extension), `redis`, `dashboard`, `api`, `worker` (Celery). Shared network. Named volumes for postgres data. Environment variable configuration.
- Acceptance Criteria:
  - [ ] `docker compose up` starts all 6 services
  - [ ] PostgreSQL is accessible on port 5432 with TimescaleDB extension loaded
  - [ ] Redis is accessible on port 6379
  - [ ] API is accessible on port 8000
  - [ ] Dashboard is accessible on port 8050
  - [ ] Services can communicate on internal network
  - [ ] `docker compose down -v` cleanly removes all containers and volumes

**T-007: Create database migration system (Alembic)**
- Priority: P1 | Size: M | Depends: T-006
- Description: Set up Alembic for PostgreSQL schema migrations. Create initial migration with tables for current architecture simulation (Layer 3 cloud store) and Chambers architecture (Device Maintenance world, hold registry, burn audit log).
- Acceptance Criteria:
  - [ ] `alembic upgrade head` creates all tables
  - [ ] `alembic downgrade base` drops all tables
  - [ ] TimescaleDB hypertables created for time-series data
  - [ ] Retention policies configured on hypertables

**T-008: Configure Celery with Redis broker**
- Priority: P1 | Size: S | Depends: T-006
- Description: Set up Celery app with Redis as broker and result backend. Configure task serialization (JSON), result expiry (1 hour), task rate limits. Create a health-check task that verifies broker connectivity.
- Acceptance Criteria:
  - [ ] Celery worker starts and connects to Redis
  - [ ] Health check task executes and returns
  - [ ] Task results are stored in Redis with TTL
  - [ ] `celery -A src.worker inspect ping` returns pong

---

### S-03: Configuration System

**T-009: Implement Pydantic settings management**
- Priority: P0 | Size: M | Depends: T-002
- Description: Create `src/config/settings.py` with Pydantic `BaseSettings` for all configurable parameters. Load from environment variables, `.env` file, and YAML config files (in that priority order). Sections: simulation, generator, current_arch, chambers_arch, analytics, visualization.
- Acceptance Criteria:
  - [ ] Settings load from env vars (`CIED_SIM_*` prefix)
  - [ ] Settings load from `.env` file
  - [ ] Settings load from `config.yaml` file
  - [ ] Priority: env > .env > yaml > defaults
  - [ ] All PRD-specified parameters have defaults
  - [ ] Settings are type-validated (Pydantic v2)
  - [ ] `settings.generator.sample_rate_hz` returns int
  - [ ] `settings.chambers.relay_ttl_seconds` returns int

**T-010: Create device profiles YAML**
- Priority: P0 | Size: M | Depends: T-009
- Description: Create `src/config/device_profiles.yaml` defining device types (VVI, DDD, CRT-D, CRT-P, ICD) with their specific parameters: memory capacity, supported channels, pacing modes, battery chemistry, lead configurations, supported transmission protocols, firmware version history.
- Acceptance Criteria:
  - [ ] VVI profile with single ventricular lead configuration
  - [ ] DDD profile with atrial + ventricular lead configuration
  - [ ] CRT-D profile with atrial + RV + LV leads + shock capability
  - [ ] Each profile includes memory allocation breakdown (per PRD 6.1)
  - [ ] Each profile includes battery chemistry and longevity parameters
  - [ ] Each profile includes supported EGM channels and sample rates
  - [ ] Profiles validated by Pydantic model on load

**T-011: Create patient profiles YAML**
- Priority: P0 | Size: M | Depends: T-009
- Description: Create `src/config/patient_profiles.yaml` with all 10 patient archetypes from PRD Section 5.6.1. Each profile defines: diagnosis, device type, age, comorbidities, AF burden, VT risk, activity level, circadian parameters, medication effects on thresholds.
- Acceptance Criteria:
  - [ ] All 10 profiles from PRD Table 5.6.1 defined
  - [ ] Each profile includes rhythm transition probability overrides
  - [ ] Each profile includes activity level distribution parameters
  - [ ] Each profile includes comorbidity flags with clinical effects
  - [ ] Profiles validated by Pydantic model on load
  - [ ] Profile P-007 (young athlete) has significantly different activity parameters than P-010 (elderly, multiple comorbidities)

**T-012: Create scenario definitions YAML**
- Priority: P1 | Size: M | Depends: T-010, T-011
- Description: Create `src/config/scenario_definitions.yaml` with the 9 pre-built scenarios from PRD Section 4.3 (`scenarios/` directory). Each scenario defines: patient cohort (size, profile mix), simulation duration, clock speed, event injections (timed adverse events), and specific parameters to measure.
- Acceptance Criteria:
  - [ ] All 9 scenarios from PRD defined
  - [ ] `baseline_single_patient.yaml`: 1 patient, P-001, 365 days
  - [ ] `af_detection_and_alert.yaml`: 1 patient, P-003, AF episodes triggered
  - [ ] `lead_fracture_adverse_event.yaml`: 1 patient, P-004, lead fracture at day 180
  - [ ] `battery_eol_transition.yaml`: 1 patient, P-006, accelerated battery drain
  - [ ] `clinician_latency_stress.yaml`: 100 patients, varied clinician response times
  - [ ] `provider_transition.yaml`: 1 patient, clinician change at day 180
  - [ ] `population_1000_mixed.yaml`: 1000 patients, mixed profiles, 365 days
  - [ ] `law_enforcement_access.yaml`: 1 patient, simulated warrant at day 200
  - [ ] `cybersecurity_breach.yaml`: 1000 patients, simulated breach at day 100

---

### S-04: CI/CD Pipeline

**T-013: Set up GitHub Actions CI pipeline**
- Priority: P1 | Size: M | Depends: T-003, T-005
- Description: Create `.github/workflows/ci.yml` with: lint (ruff), typecheck (mypy), unit tests (pytest), integration tests (docker compose), build docker image. Run on push to main and all PRs.
- Acceptance Criteria:
  - [ ] CI runs on push to main
  - [ ] CI runs on all PRs
  - [ ] Lint step fails on ruff violations
  - [ ] Type check step fails on mypy errors
  - [ ] Unit tests run without docker dependencies
  - [ ] Integration tests use docker compose to stand up services
  - [ ] Docker image builds successfully
  - [ ] CI completes in < 10 minutes

**T-014: Set up test coverage reporting**
- Priority: P2 | Size: S | Depends: T-013
- Description: Add `pytest-cov` configuration. Generate coverage reports in CI. Set minimum coverage threshold at 80% for `src/` (excluding `visualization/`). Upload coverage to Codecov or similar.
- Acceptance Criteria:
  - [ ] `make test` generates coverage report
  - [ ] Coverage threshold enforced in CI (80%)
  - [ ] Coverage report uploaded as CI artifact
  - [ ] Visualization code excluded from coverage requirement

---

# EPIC E-02: Synthetic Telemetry Generator (Module 1)

> The heart of the simulator: generate physiologically plausible cardiac event streams for virtual patients.

---

### S-05: Cardiac Rhythm Engine

**T-015: Implement base rhythm state machine**
- Priority: P0 | Size: L | Depends: T-009
- Description: Implement the rhythm state machine in `src/generator/cardiac/rhythm_engine.py`. Each rhythm state produces a characteristic heart rate (with variance) and transitions to other states based on Markov chain probabilities. The engine operates on a per-heartbeat time step.
- Acceptance Criteria:
  - [ ] All 18 rhythm types from PRD Section 5.1.1 implemented as states
  - [ ] Each state produces HR within specified range
  - [ ] Transitions follow Markov chain with configurable probabilities
  - [ ] Transition probabilities modulated by time of day (circadian)
  - [ ] Transition probabilities modulated by activity level
  - [ ] Transition probabilities modulated by patient profile
  - [ ] State machine is deterministic given a random seed
  - [ ] Can produce 24 hours of rhythm data in < 1 second

**T-016: Implement circadian heart rate modulation**
- Priority: P0 | Size: M | Depends: T-015
- Description: Create `src/generator/patient/circadian_model.py`. Modulate base heart rate with circadian variation: lower HR during sleep (0000-0600), gradual increase to daytime baseline, peaks during activity. Use sinusoidal base + noise model.
- Acceptance Criteria:
  - [ ] HR at 0300 is 10-20% lower than HR at 1400 (for NSR)
  - [ ] Transition between sleep/wake HR is gradual (not step function)
  - [ ] Circadian pattern is configurable per patient profile
  - [ ] Night-time arrhythmia transition probabilities adjusted (e.g., AF onset more common during vagal periods)

**T-017: Implement rhythm-specific beat generation**
- Priority: P0 | Size: XL | Depends: T-015
- Description: For each rhythm state, implement the beat-level timing model:
  - NSR: regular RR intervals with HRV (SDNN ~100ms)
  - AF: irregularly irregular RR intervals (coefficient of variation >15%)
  - VT: regular wide-complex at programmed rate
  - VF: completely chaotic, no discernible RR pattern
  - Heart blocks: PR prolongation patterns, dropped beats
  - PVCs: premature coupling intervals with compensatory pauses
- Acceptance Criteria:
  - [ ] NSR RR intervals pass HRV analysis (SDNN 80-120ms, RMSSD 20-50ms)
  - [ ] AF RR intervals have coefficient of variation > 15%
  - [ ] VT has regular rate 120-250 bpm
  - [ ] VF shows no organized rhythm
  - [ ] Mobitz I shows progressive PR prolongation then dropped beat
  - [ ] Mobitz II shows intermittent dropped beats without PR change
  - [ ] PVCs have coupling interval 350-500ms with compensatory pause

**T-018: Implement AV conduction model**
- Priority: P1 | Size: M | Depends: T-017
- Description: Create `src/generator/cardiac/conduction.py`. Model the AV node as a delay element with refractory period. Normal conduction: PR 120-200ms. First-degree block: PR >200ms. Wenckebach: progressive PR prolongation. Complete block: independent P and QRS.
- Acceptance Criteria:
  - [ ] Normal conduction: every P wave followed by QRS at PR interval
  - [ ] First-degree: PR >200ms but every P conducted
  - [ ] Wenckebach: PR increases by decreasing increments, then dropped QRS
  - [ ] Complete block: P rate and QRS rate independent
  - [ ] Rate-dependent conduction: faster rates → longer PR (decremental conduction)

---

### S-06: EGM Signal Synthesis

**T-019: Implement waveform component models (P, QRS, T)**
- Priority: P0 | Size: L | Depends: T-017
- Description: Create `src/generator/cardiac/waveform_models.py`. Generate individual waveform components using parameterized templates:
  - P wave: Gaussian envelope, duration 80-120ms, amplitude 0.5-2.0mV (atrial channel)
  - QRS complex: multi-Gaussian (Q, R, S deflections), duration 80-120ms (narrow) or 120-200ms (wide), amplitude 5-20mV (ventricular channel)
  - T wave: asymmetric Gaussian, duration 150-250ms, amplitude 1-5mV
  - Pacing artifact: sharp biphasic spike, 0.5ms duration, amplitude dependent on output setting
- Acceptance Criteria:
  - [ ] Each component is parameterizable (duration, amplitude, morphology)
  - [ ] Components combine to form complete cardiac cycle
  - [ ] Narrow QRS (< 120ms) for supraventricular rhythms
  - [ ] Wide QRS (> 120ms) for ventricular rhythms and paced beats
  - [ ] Pacing artifacts visually distinct from native deflections
  - [ ] Beat-to-beat morphology variation (subtle, realistic)

**T-020: Implement atrial EGM channel synthesis**
- Priority: P0 | Size: M | Depends: T-019
- Description: Generate near-field atrial electrogram as seen from an atrial bipolar lead. Large atrial deflection, small far-field ventricular deflection. Apply lead impedance characteristics.
- Acceptance Criteria:
  - [ ] Atrial deflection amplitude 1.0-5.0 mV
  - [ ] Far-field ventricular signal < 30% of atrial amplitude
  - [ ] Noise floor 0.05-0.2 mV (Gaussian)
  - [ ] 50/60 Hz interference model (optional, configurable)
  - [ ] Signal characteristics change with lead impedance

**T-021: Implement ventricular EGM channel synthesis**
- Priority: P0 | Size: M | Depends: T-019
- Description: Generate near-field ventricular electrogram. Large ventricular deflection, minimal atrial signal. Apply RV lead characteristics.
- Acceptance Criteria:
  - [ ] Ventricular deflection amplitude 5.0-20.0 mV
  - [ ] Minimal atrial signal (< 10% of ventricular amplitude)
  - [ ] Noise floor 0.1-0.5 mV
  - [ ] Morphology changes during VT (wider, different axis)
  - [ ] Paced ventricular morphology distinct from intrinsic

**T-022: Implement shock channel (far-field) synthesis**
- Priority: P1 | Size: M | Depends: T-019
- Description: Generate far-field EGM from RV coil to can/SVC coil. Captures both atrial and ventricular activity. Used for morphology discrimination algorithms.
- Acceptance Criteria:
  - [ ] Both atrial and ventricular deflections visible
  - [ ] Lower sample rate (128 Hz vs 256 Hz for near-field)
  - [ ] Amplitude range 0.5-3.0 mV
  - [ ] Morphology useful for SVT vs VT discrimination

**T-023: Implement EGM assembly pipeline**
- Priority: P0 | Size: L | Depends: T-020, T-021, T-022
- Description: Create `src/generator/cardiac/egm_synthesizer.py`. Assemble complete EGM strips from component waveforms according to the pipeline in PRD Section 5.1.3. Handle multi-channel synchronization, quantization to 12-bit ADC resolution, buffering into episode-triggered segments.
- Acceptance Criteria:
  - [ ] Pipeline steps 1-7 from PRD 5.1.3 all implemented
  - [ ] Multi-channel EGMs are time-synchronized
  - [ ] Output is quantized to 12-bit resolution (4096 levels)
  - [ ] Configurable sample rate (128, 256, 512 Hz)
  - [ ] EGM strips are generated only on trigger (not continuous)
  - [ ] Strip duration matches trigger type (per PRD Table 5.1.4)
  - [ ] Pre-trigger buffer captured correctly (10s before trigger)

---

### S-07: Device Simulation Engine

**T-024: Implement pacing decision logic (VVI mode)**
- Priority: P0 | Size: L | Depends: T-017
- Description: Create `src/generator/device/pacing_engine.py` starting with VVI mode. Implement: lower rate interval, ventricular sensing, ventricular pacing on timeout, hysteresis (optional), rate response (optional). Track pacing percentage.
- Acceptance Criteria:
  - [ ] Pace ventricle when no sensed event within lower rate interval
  - [ ] Inhibit pacing on sensed ventricular event
  - [ ] Track pacing percentage (0-100%)
  - [ ] Hysteresis: longer escape interval after sensed beat (configurable)
  - [ ] Rate response: adjust lower rate based on activity sensor
  - [ ] Pacing artifacts appear in EGM at correct timing

**T-025: Implement pacing decision logic (DDD mode)**
- Priority: P0 | Size: XL | Depends: T-024
- Description: Extend pacing engine with DDD mode. Implement all four states: AS-VS (inhibited), AS-VP (P-wave triggered ventricular pace), AP-VS (atrial pace, ventricular sense), AP-VP (full dual-chamber pacing). AV delay, PVARP, upper rate behavior, mode switching.
- Acceptance Criteria:
  - [ ] All four DDD states (AS-VS, AS-VP, AP-VS, AP-VP) implemented
  - [ ] AV delay: sensed AV (100-200ms), paced AV (150-250ms)
  - [ ] PVARP: 250-350ms post ventricular event
  - [ ] Upper rate behavior: Wenckebach, then 2:1 block at upper rate limit
  - [ ] Mode switch: DDD → DDI/VVI on detection of AT/AF
  - [ ] Mode switch recovery when AT/AF terminates
  - [ ] Tracks atrial pacing %, ventricular pacing % independently

**T-026: Implement pacing decision logic (CRT-D mode)**
- Priority: P1 | Size: L | Depends: T-025
- Description: Extend with CRT-D (biventricular pacing). Add LV pacing channel with configurable VV delay. Implement VT/VF detection zones, ATP therapy delivery, shock therapy delivery. Track biventricular pacing percentage.
- Acceptance Criteria:
  - [ ] Biventricular pacing with configurable VV delay (-80 to +80 ms)
  - [ ] VT detection zone: rate + duration criteria
  - [ ] VF detection zone: rate criteria (faster)
  - [ ] ATP: burst pacing (8 pulses at 88% of VT cycle length)
  - [ ] Shock: simulated high-voltage therapy delivery
  - [ ] Biventricular pacing percentage tracked (target >98%)
  - [ ] Therapy delivery logged as episodes with EGM

**T-027: Implement sensing threshold simulation**
- Priority: P1 | Size: M | Depends: T-020, T-021
- Description: Create `src/generator/device/sensing_engine.py`. Model the device's sensing amplifier: adjustable sensitivity, automatic gain control, blanking periods, refractory periods. Detect oversensing (T-wave oversensing, myopotential) and undersensing (low amplitude signals).
- Acceptance Criteria:
  - [ ] Configurable sensing threshold (atrial: 0.2-2.0 mV, ventricular: 1.0-8.0 mV)
  - [ ] Automatic sensitivity adjustment after sensed/paced events
  - [ ] Blanking period: no sensing for configured window after pace/sense
  - [ ] T-wave oversensing: detectable when T-wave exceeds sensing threshold
  - [ ] Undersensing: missed events when signal amplitude drops below threshold

**T-028: Implement battery depletion model**
- Priority: P0 | Size: M | Depends: T-024
- Description: Create `src/generator/device/battery_model.py` per PRD Section 5.2.1. Model voltage and impedance over time as function of pacing load, telemetry sessions, and capacitor reformations. Produce BOL→MOL→ERI→EOS progression.
- Acceptance Criteria:
  - [ ] Battery model follows exponential depletion curve from PRD
  - [ ] Depletion rate scales with pacing percentage
  - [ ] Depletion rate scales with output voltage setting
  - [ ] ERI trigger at correct voltage threshold (2.6V)
  - [ ] EOS trigger at correct voltage threshold (2.4V)
  - [ ] Battery voltage reported in daily transmissions
  - [ ] Projected longevity: 7-12 years for typical programming

**T-029: Implement lead impedance evolution model**
- Priority: P0 | Size: M | Depends: T-002
- Description: Create `src/generator/device/lead_model.py` per PRD Section 5.2.2. Model normal impedance maturation (acute→chronic), slow chronic drift, and failure modes (fracture: sudden rise; insulation breach: sudden or gradual drop; connection issue: intermittent spikes).
- Acceptance Criteria:
  - [ ] Acute phase: 400-1200 Ω
  - [ ] Maturation (1-3 months): drops to 300-800 Ω
  - [ ] Chronic: stable at 300-700 Ω with slow upward drift
  - [ ] Lead fracture: sigmoid ramp to >2000 Ω, configurable sharpness
  - [ ] Insulation breach: drop to <200 Ω
  - [ ] Connection issue: high-variance intermittent spikes
  - [ ] Daily measurement with ±5% measurement noise

**T-030: Implement pacing threshold evolution model**
- Priority: P1 | Size: M | Depends: T-024
- Description: Per PRD Section 5.2.3. Model threshold maturation: low implant threshold → inflammatory peak at 2-4 weeks → chronic stabilization. Include medication effects, steroid-eluting lead modifier, auto-capture algorithm.
- Acceptance Criteria:
  - [ ] Implant threshold: 0.5-1.5V at 0.4ms
  - [ ] Inflammatory peak: 1.0-3.0V at 2-4 weeks
  - [ ] Chronic: stabilizes at 0.5-1.5V
  - [ ] Steroid-eluting: reduced peak, faster stabilization
  - [ ] Medication effect: configurable threshold increase
  - [ ] Auto-capture: periodic threshold test, output adjustment

**T-031: Implement firmware state tracking**
- Priority: P2 | Size: S | Depends: T-002
- Description: Create `src/generator/device/firmware_state.py`. Track firmware version per device. Support simulated firmware updates (version change at configurable time). Log firmware version in every transmission.
- Acceptance Criteria:
  - [ ] Device has initial firmware version
  - [ ] Firmware update event changes version
  - [ ] Version reported in every transmission
  - [ ] Version history maintained for Device Maintenance World

---

### S-08: Patient Activity & Motion Engine

**T-032: Implement accelerometer activity simulation**
- Priority: P0 | Size: M | Depends: T-016
- Description: Create `src/generator/patient/activity_engine.py` per PRD Section 5.3.1. Generate activity counts per minute following the circadian pattern and patient profile. States: sleep, resting, light, moderate, vigorous.
- Acceptance Criteria:
  - [ ] Activity counts match ranges from PRD Table 5.3.1
  - [ ] Circadian pattern follows PRD time blocks
  - [ ] Patient profile modifiers applied (HF → reduced peaks)
  - [ ] Stochastic transitions between activity states
  - [ ] Activity data stored as daily summaries (histogram bins)
  - [ ] Activity data stored as minute-by-minute for detailed analysis

**T-033: Implement rate response algorithm**
- Priority: P1 | Size: M | Depends: T-032, T-024
- Description: Per PRD Section 5.3.2. Translate activity level to target pacing rate using configurable response curve (sigmoid), reaction time, and recovery time. Feed into pacing engine.
- Acceptance Criteria:
  - [ ] Target rate = lower_rate + (max_sensor_rate - lower_rate) × response_curve
  - [ ] Response factor configurable (1-10 scale)
  - [ ] Reaction time: 15-45 seconds from activity onset to rate increase
  - [ ] Recovery time: 2-8 minutes from activity cessation to rate decrease
  - [ ] Rate response disabled during detected arrhythmias

**T-034: Implement comorbidity interaction model**
- Priority: P2 | Size: M | Depends: T-015, T-032
- Description: Create `src/generator/patient/comorbidity_model.py`. Model how comorbidities modify cardiac behavior: HF → reduced activity capacity, increased arrhythmia risk, fluid status effects; Diabetes → threshold changes; CKD → electrolyte effects on EGM morphology.
- Acceptance Criteria:
  - [ ] Heart failure: reduced max activity, increased AF/VT transition probability
  - [ ] Diabetes: periodic threshold elevation (metabolic derangement)
  - [ ] CKD: EGM morphology changes (peaked T waves in hyperkalemia)
  - [ ] Multiple comorbidities: effects are additive/multiplicative as clinically appropriate
  - [ ] Comorbidity effects are configurable per patient profile

---

### S-09: Episode & Alert System

**T-035: Implement stochastic arrhythmia episode generator**
- Priority: P0 | Size: L | Depends: T-015, T-017
- Description: Create `src/generator/episodes/arrhythmia_generator.py` per PRD Section 5.4.1. Generate AF, VT, SVT, PVC episodes using the specified distributions (Poisson frequency, LogNormal/Exponential duration).
- Acceptance Criteria:
  - [ ] AF episodes: Poisson frequency, LogNormal duration
  - [ ] VT episodes: Poisson frequency (rare), Exponential duration
  - [ ] PVC burden: Poisson count, beta-binomial clustering
  - [ ] Episode parameters respect patient profile (AF burden, VT risk)
  - [ ] Episodes trigger rhythm state transitions in rhythm engine
  - [ ] Episodes trigger EGM recording (per PRD Table 5.1.4)
  - [ ] Episode log maintained with start time, duration, type, max rate

**T-036: Implement device alert condition detection**
- Priority: P0 | Size: L | Depends: T-035, T-028, T-029
- Description: Create `src/generator/episodes/alert_generator.py` implementing all 11 alert types from PRD Table 5.4.2. Each alert has trigger condition, priority level, and associated data.
- Acceptance Criteria:
  - [ ] All 11 alert types from PRD Table 5.4.2 implemented
  - [ ] AT/AF alert: duration > programmed detection threshold
  - [ ] VT/VF + therapy: detected episode with therapy delivery
  - [ ] Lead impedance: out of range (<200 or >2000 Ω)
  - [ ] Battery ERI/EOS: voltage thresholds
  - [ ] Pacing % change: >20% change over 30 days
  - [ ] Alerts include priority level (Low/Medium/High/Critical)
  - [ ] Alerts trigger immediate transmission (for High/Critical)
  - [ ] Alert queue maintained for clinician review

**T-037: Implement adverse event generator**
- Priority: P0 | Size: L | Depends: T-029, T-028
- Description: Create `src/generator/episodes/adverse_event_gen.py` per PRD Section 5.4.3. Generate rare but critical events: lead fracture, lead dislodgement, insulation breach, generator malfunction, unexpected battery EOL, inappropriate shock, patient death. Use annual rates from published literature.
- Acceptance Criteria:
  - [ ] All adverse event types from PRD 5.4.3 implemented
  - [ ] Rates match published literature ranges
  - [ ] Lead fracture: triggers impedance model failure mode
  - [ ] Lead dislodgement: triggers threshold increase + impedance change
  - [ ] Patient death: stops telemetry generation, triggers investigation hold scenario
  - [ ] Adverse events can be deterministically injected at specified simulation time
  - [ ] Adverse events log includes timestamp, type, severity, device state at time

---

### S-10: Transmission & Stream Assembly

**T-038: Implement transmission cycle simulation**
- Priority: P0 | Size: L | Depends: T-036, T-035
- Description: Create transmission cycle logic per PRD Section 5.5.1. Implement daily check (small payload ~500 bytes) and full interrogation (large payload ~50-200 KB). Handle alert-triggered transmissions. Bundle events into transmission packets.
- Acceptance Criteria:
  - [ ] Daily check: device status, alert flags, battery, impedance, pacing %
  - [ ] Full interrogation: all daily check + episodes + EGMs + histograms + activity
  - [ ] Alert-triggered: immediate transmission on High/Critical alerts
  - [ ] Transmission packet size matches PRD estimates
  - [ ] Transmission frequency configurable per manufacturer profile
  - [ ] Failed transmissions queued for retry with exponential backoff

**T-039: Implement unified event stream assembly**
- Priority: P0 | Size: M | Depends: T-038
- Description: Create `src/generator/stream.py`. Unify all generator outputs (rhythm, EGM, device status, activity, episodes, alerts, transmissions) into a single ordered event stream. Each event is tagged with typed-world targets for Chambers routing.
- Acceptance Criteria:
  - [ ] Events are strictly ordered by simulation timestamp
  - [ ] Each event has a unique ID, type, patient ID, timestamp
  - [ ] Each event tagged with sensitivity score and world targets
  - [ ] Stream is consumable by both current arch and Chambers arch simultaneously
  - [ ] Stream supports backpressure (producer waits if consumers lag)
  - [ ] Stream supports multi-patient interleaving for cohort simulation

**T-040: Implement multi-patient cohort manager**
- Priority: P1 | Size: L | Depends: T-039, T-011
- Description: Create `src/generator/cohort.py`. Manage concurrent simulation of multiple patients. Instantiate patient generators from profiles/archetypes. Support configurable cohort distributions (per PRD Section 5.6.2). Handle inter-patient independence (no shared state).
- Acceptance Criteria:
  - [ ] Create cohort of N patients from distribution parameters
  - [ ] Each patient has independent random seed (reproducible)
  - [ ] Cohort-level distribution matches configured parameters (age, sex, device type)
  - [ ] Memory usage scales linearly with patient count
  - [ ] Simulation time for 1000 patients × 1 year < 1 hour
  - [ ] Events from all patients merge into unified stream (ordered by time)

---

# EPIC E-03: Current Architecture Simulation (Module 2)

> Model the 5-layer persist-by-default architecture of existing CIED manufacturers.

---

### S-11: Data Flow Layers

**T-041: Implement Layer 1 — On-Device Storage**
- Priority: P0 | Size: L | Depends: T-039
- Description: Create `src/current_arch/layers/on_device.py` per PRD Section 6.1. Simulate device memory with FIFO overwrite for EGM storage, episode log, histograms. Track memory utilization. Implement priority protection (VT/VF episodes protected from overwrite).
- Acceptance Criteria:
  - [ ] Memory allocated per PRD Section 6.1 breakdown
  - [ ] FIFO overwrite: oldest episodes overwritten when full
  - [ ] VT/VF episode protection: AT/AF overwritten first
  - [ ] Memory utilization tracked over time
  - [ ] Data retained until transmission or overwrite
  - [ ] Queryable: "what data is currently on device?"

**T-042: Implement Layer 2 — Transmitter**
- Priority: P0 | Size: M | Depends: T-041
- Description: Create `src/current_arch/layers/transmitter.py`. Simulate BLE/RF transmission from device to bedside monitor/smartphone app. Model transmission sessions, cache-until-uploaded behavior, and failure modes (out of range, power off, interference).
- Acceptance Criteria:
  - [ ] Receives data from Layer 1 on transmission trigger
  - [ ] Caches data until confirmed upload to Layer 3
  - [ ] Failure modes: out-of-range → exponential backoff retry
  - [ ] Failure modes: power off → data accumulates on device
  - [ ] Transmission latency: 2-15 minutes depending on payload
  - [ ] Cache cleared after confirmed upload

**T-043: Implement Layer 3 — Manufacturer Cloud**
- Priority: P0 | Size: XL | Depends: T-042, T-007
- Description: Create `src/current_arch/layers/cloud.py` per PRD Section 6.3. Full persistent storage in TimescaleDB. Implement ingestion pipeline (decrypt, parse, alert algorithms, report generation, store). Retention policy: indefinite (no deletion).
- Acceptance Criteria:
  - [ ] Receives uploads from Layer 2
  - [ ] Processes through 6-step pipeline (PRD 6.3)
  - [ ] Stores raw transmission data in TimescaleDB
  - [ ] Stores processed/structured data
  - [ ] Stores generated reports
  - [ ] No data ever deleted (indefinite retention)
  - [ ] Queryable by device serial, patient ID, time range, alert type
  - [ ] Data volume grows monotonically (tracked by analytics)

**T-044: Implement Layer 4 — Clinician Portal**
- Priority: P0 | Size: L | Depends: T-043
- Description: Create `src/current_arch/layers/clinician_portal.py` per PRD Section 6.4. Simulate clinician access patterns: login frequency, alert queue processing, EGM review, acknowledgment. Model acknowledgment latency per PRD distributions (LogNormal by priority).
- Acceptance Criteria:
  - [ ] Clinician access simulated with configurable frequency
  - [ ] Alert queue sorted by priority
  - [ ] Acknowledgment latency follows LogNormal distributions from PRD
  - [ ] Critical alerts: µ=2h, σ=1h
  - [ ] High alerts: µ=8h, σ=4h
  - [ ] Medium alerts: µ=48h, σ=24h
  - [ ] Low alerts: µ=168h, σ=72h
  - [ ] Acknowledgment events logged with timestamp

**T-045: Implement Layer 5 — Aggregate Pool**
- Priority: P1 | Size: L | Depends: T-043
- Description: Create `src/current_arch/layers/aggregate_pool.py` per PRD Section 6.5. Simulate monthly batch aggregation with configurable k-anonymity. Track re-identification risk. Feed R&D, regulatory, commercial consumers.
- Acceptance Criteria:
  - [ ] Monthly batch aggregation from Layer 3 data
  - [ ] k-anonymity with configurable k (default k=5)
  - [ ] Quasi-identifiers: age bucket, sex, device model, region, implant year
  - [ ] Re-identification probability tracked as population varies
  - [ ] Aggregate metrics: episode rates, device performance, therapy stats
  - [ ] Data retained indefinitely

---

### S-12: Data Consumer Simulation

**T-046: Implement data consumer actors**
- Priority: P1 | Size: L | Depends: T-044, T-045
- Description: Create consumer actors in `src/current_arch/data_consumers/` per PRD Section 6.6. Each consumer (OEM, clinician, hospital, insurer, regulator) has access policies, query patterns, and data extraction behavior. Log all access events.
- Acceptance Criteria:
  - [ ] OEM consumer: queries all data, batch analytics, safety signal detection
  - [ ] Clinician consumer: queries own patients, alert review, EGM viewing
  - [ ] Hospital consumer: exports to EMR, billing codes
  - [ ] Insurer consumer: claims-based access only (no direct device data)
  - [ ] Regulator consumer: on-request access, adverse event investigation
  - [ ] All access events logged with consumer, timestamp, data scope

---

# EPIC E-04: Chambers Architecture Simulation (Module 3)

> Implement the burn-by-default, typed-world architecture proposed in the position paper.

---

### S-13: Relay Implementation

**T-047: Implement stateless relay processor**
- Priority: P0 | Size: XL | Depends: T-039
- Description: Create `src/chambers_arch/relay/processor.py`. Receives telemetry events, processes them (alert detection, report generation — same algorithms as current arch), routes results to typed worlds, does NOT persist beyond TTL window. Uses Redis with TTL-based key expiration.
- Acceptance Criteria:
  - [ ] Receives unified event stream
  - [ ] Processes through same pipeline as current arch (alert detection, etc.)
  - [ ] Routes processed data to correct typed worlds based on event tags
  - [ ] All data stored in Redis with configurable TTL (default 72h)
  - [ ] Redis AOF/RDB persistence disabled for relay keys
  - [ ] No data queryable after TTL expiration
  - [ ] Processing latency < 100ms per event

**T-048: Implement delivery confirmation tracker**
- Priority: P0 | Size: M | Depends: T-047
- Description: Create `src/chambers_arch/relay/delivery_tracker.py`. Track ACK from each target world. Implement escalation if ACK not received within TTL/2. Only allow burn after all target worlds have ACKed.
- Acceptance Criteria:
  - [ ] Each data element tracked: RECEIVED → PROCESSED → DELIVERED → BURNED
  - [ ] ACK required from every target world
  - [ ] Escalation triggered if no ACK within TTL/2
  - [ ] Escalation: retry delivery, alert, optionally extend TTL
  - [ ] Burn only after all ACKs received OR TTL expires with audit trail
  - [ ] Delivery confirmation log (who ACKed, when)

**T-049: Implement ephemeral store (TTL-based)**
- Priority: P0 | Size: M | Depends: T-047
- Description: Create `src/chambers_arch/relay/ephemeral_store.py`. Wrapper around Redis that enforces TTL on all keys. Provides get/set/delete with mandatory TTL. Reports storage metrics (items count, oldest item age, storage bytes).
- Acceptance Criteria:
  - [ ] All set operations require TTL parameter
  - [ ] No TTL-less keys allowed
  - [ ] Get returns None after TTL expiry (verified)
  - [ ] Storage metrics: item count, oldest item age, total bytes
  - [ ] Integration with burn verifier (T-055)

---

### S-14: Typed Worlds

**T-050: Implement base typed world abstraction**
- Priority: P0 | Size: M | Depends: T-047
- Description: Create `src/chambers_arch/worlds/base_world.py`. Abstract base class for all typed worlds. Enforces: data scope validation (only accepts permitted data types), access control (only authorized actors), burn schedule interface, audit logging.
- Acceptance Criteria:
  - [ ] Abstract methods: `accept_data()`, `query()`, `burn()`, `get_status()`
  - [ ] Data scope validation: rejects data types not in world's scope
  - [ ] Access control: rejects unauthorized actors
  - [ ] Audit log: all accept, query, burn operations logged
  - [ ] World isolation: no cross-world data references
  - [ ] Hold awareness: respects active safety investigation holds

**T-051: Implement Clinical World**
- Priority: P0 | Size: XL | Depends: T-050
- Description: Create `src/chambers_arch/worlds/clinical_world.py` per PRD Section 7.3.1. Full-fidelity clinical data (IEGMs, therapy logs, trends, alerts). Burns from relay after confirmed delivery to patient record AND clinician acknowledgment. Implements notification to clinician and acknowledgment tracking.
- Acceptance Criteria:
  - [ ] Accepts: IEGMs, therapy logs, diagnostic trends, alerts, episode details
  - [ ] Rejects: activity data, device maintenance data (scope enforcement)
  - [ ] Access: treating clinician (authenticated) + patient
  - [ ] Burn trigger: confirmed delivery to patient record + clinician ack
  - [ ] Non-alert transmissions: burn after delivery confirmation
  - [ ] Alert transmissions: burn after clinician acknowledgment
  - [ ] Fallback: escalation if no ack within 30 days, burn with audit note
  - [ ] Clinician notification on data arrival (webhook/event)

**T-052: Implement Device Maintenance World**
- Priority: P0 | Size: L | Depends: T-050
- Description: Create `src/chambers_arch/worlds/device_maintenance_world.py` per PRD Section 7.3.2. Minimal device-focused data: serial, model, firmware, lead impedance, battery status. Rolling retention window (default 90 days). NO IEGMs, NO episodes, NO activity data.
- Acceptance Criteria:
  - [ ] Accepts: device serial, model, firmware version, lead impedance, battery voltage, last transmission timestamp, alert summary counts
  - [ ] Rejects: IEGMs, arrhythmia episodes, activity data, patient demographics
  - [ ] Access: manufacturer (warranty/recall purposes)
  - [ ] Rolling retention: oldest data point burns when window advances
  - [ ] Window length configurable per data type (default 90 days)
  - [ ] Can answer: "Which devices of model X are active?"
  - [ ] Cannot answer: "What was patient Z's arrhythmia history?"

**T-053: Implement Research World**
- Priority: P1 | Size: XL | Depends: T-050
- Description: Create `src/chambers_arch/worlds/research_world.py` per PRD Section 7.3.3. Two channels: Channel A (aggregated, de-identified, opt-out) and Channel B (individual-level, consent-gated, opt-in). Implement k-anonymity + differential privacy for Channel A. Implement consent lifecycle for Channel B.
- Acceptance Criteria:
  - [ ] Channel A: aggregated metrics, k-anonymity (k≥10), differential privacy (configurable ε)
  - [ ] Channel A: patient can opt out (data removed from aggregation)
  - [ ] Channel A: no raw data access, only pre-computed aggregations
  - [ ] Channel B: explicit opt-in consent required
  - [ ] Channel B: ethics committee approval gate (simulated)
  - [ ] Channel B: retention period defined at consent time
  - [ ] Channel B: mandatory burn on consent withdrawal
  - [ ] Channel B: access limited to named researchers on approved protocol
  - [ ] Channel B: encrypted per-patient store, key destroyed on burn

**T-054: Implement Patient World (Portable Record)**
- Priority: P0 | Size: XL | Depends: T-050
- Description: Create `src/chambers_arch/worlds/patient_world.py` per PRD Section 7.3.4. Patient-controlled store for ALL data the patient chooses to retain. SQLite-based encrypted portable record. FHIR R4 resource format. Patient controls retention (no auto-burn).
- Acceptance Criteria:
  - [ ] Accepts: all data types (patient chooses what to keep)
  - [ ] Storage: encrypted SQLite (AES-256)
  - [ ] Format: FHIR R4 resources (Observation, DiagnosticReport, Device)
  - [ ] Export: FHIR JSON Bundle, PDF summary, CSV
  - [ ] Patient controls retention (keep, delete, auto-expire options)
  - [ ] Independent of manufacturer infrastructure
  - [ ] Survives manufacturer system downtime/termination

**T-055: Implement Safety Investigation World**
- Priority: P0 | Size: XL | Depends: T-050
- Description: Create `src/chambers_arch/worlds/safety_investigation_world.py` per PRD Section 7.3.5. Hold mechanism triggered by adverse events. Freezes burn schedules. Captures relay snapshots. Duration: investigation + 12-month buffer. Strict access control (investigating parties only).
- Acceptance Criteria:
  - [ ] Hold triggers: manufacturer report, FDA request, clinician report, auto-detect
  - [ ] On trigger: freeze all burn schedules for affected patient
  - [ ] Capture snapshot of all data currently in relay
  - [ ] Request hold from all other worlds (Clinical, Patient, Device Maint)
  - [ ] Hold duration: investigation period + 12-month buffer (configurable)
  - [ ] Access: investigating parties only (FDA, manufacturer safety, clinician)
  - [ ] Hold termination: authority release → 12-month buffer → burn
  - [ ] Burn verification certificate on termination
  - [ ] Already-burned data NOT recoverable (accepted trade-off, logged)
  - [ ] Hold registry with audit trail

---

### S-15: Burn System

**T-056: Implement burn scheduler**
- Priority: P0 | Size: L | Depends: T-051, T-052, T-053
- Description: Create `src/chambers_arch/burn/scheduler.py`. Celery-based scheduled task executor. Manages burn schedules for all worlds. Respects hold freezes. Logs all burn events. Supports configurable burn policies per world.
- Acceptance Criteria:
  - [ ] Burn tasks scheduled via Celery beat
  - [ ] Each world's burn policy independently configurable
  - [ ] Burn execution: data deleted from storage, verification triggered
  - [ ] Hold-aware: skips burn for held data, reschedules after hold release
  - [ ] Burn event log: timestamp, world, data reference, policy, verification
  - [ ] Burn execution within 1 second of scheduled time

**T-057: Implement burn policies**
- Priority: P0 | Size: M | Depends: T-056
- Description: Create `src/chambers_arch/burn/policies.py`. Define burn policies per world per PRD Section 7.3: Clinical (after ACK), Device Maintenance (rolling window), Research (consent withdrawal or programme end), Patient (patient-controlled), Safety Investigation (post-investigation + buffer).
- Acceptance Criteria:
  - [ ] Clinical policy: burn after delivery + ack, with fallback timeout
  - [ ] Device Maintenance policy: rolling window eviction
  - [ ] Research Channel A policy: programme completion
  - [ ] Research Channel B policy: consent withdrawal OR retention expiry
  - [ ] Patient policy: patient-initiated only (no auto-burn)
  - [ ] Safety Investigation policy: authority release + buffer expiry
  - [ ] Policies are composable and configurable via YAML

**T-058: Implement burn verification**
- Priority: P1 | Size: L | Depends: T-056
- Description: Create `src/chambers_arch/burn/verifier.py` per PRD Section 7.5. Implement three verification approaches: cryptographic deletion (key destruction), Merkle tree verification (non-inclusion proof), audit-based verification (third-party attestation). Generate burn verification certificates.
- Acceptance Criteria:
  - [ ] Approach 1: encrypted data with per-record key, burn = destroy key
  - [ ] Approach 2: Merkle tree with non-inclusion proofs
  - [ ] Approach 3: audit log with tamper-evident timestamps
  - [ ] Burn verification certificate generated for each burn event
  - [ ] Verification dashboard data feed
  - [ ] Intentional burn failure injection for testing

**T-059: Implement hold manager**
- Priority: P0 | Size: L | Depends: T-055, T-056
- Description: Create `src/chambers_arch/burn/hold_manager.py`. Manages safety investigation holds: creation, TTL suspension, cross-world coordination, hold release, post-hold burn scheduling. Implements hold registry in PostgreSQL.
- Acceptance Criteria:
  - [ ] Create hold: suspend TTL on all affected data
  - [ ] Cross-world coordination: notify all worlds of hold
  - [ ] Hold registry: PostgreSQL table with hold metadata
  - [ ] Hold release: resume burn schedules, apply buffer period
  - [ ] Audit trail: immutable log of all hold lifecycle events
  - [ ] Concurrent holds: support multiple active holds (different patients)

---

### S-16: Portable Record & Delegation

**T-060: Implement FHIR R4 exporter**
- Priority: P1 | Size: XL | Depends: T-054
- Description: Create `src/chambers_arch/portable_record/fhir_exporter.py`. Map CIED telemetry data to FHIR R4 resources: Device (device info), Observation (measurements, EGM data), DiagnosticReport (transmission reports), Condition (arrhythmia diagnoses), Procedure (therapy deliveries).
- Acceptance Criteria:
  - [ ] Device resource: serial, model, manufacturer, firmware, implant date
  - [ ] Observation resources: HR, pacing %, impedance, battery, thresholds
  - [ ] Observation (EGM): base64-encoded waveform data with SampledData type
  - [ ] DiagnosticReport: transmission summary with linked Observations
  - [ ] Condition: arrhythmia diagnoses from episode data
  - [ ] Procedure: ATP/shock therapy deliveries
  - [ ] FHIR Bundle export (transaction bundle, all resources for a transmission)
  - [ ] FHIR validation passes (using HAPI FHIR validator or equivalent)

**T-061: Implement delegation model**
- Priority: P1 | Size: L | Depends: T-054
- Description: Create `src/chambers_arch/portable_record/delegation.py` per PRD Section 7.3.4. Model primary and secondary delegates. Delegation permissions: read-only (no delete). Revocable by patient. Fallback if primary unavailable.
- Acceptance Criteria:
  - [ ] Primary delegate: full read access, can share with clinicians
  - [ ] Secondary delegate: backup if primary unavailable
  - [ ] Patient can revoke delegation at any time
  - [ ] Delegate cannot delete data from patient record
  - [ ] On patient death: delegate retains access for configurable period (default 2 years)
  - [ ] Delegation events logged in audit trail

**T-062: Implement emergency access protocol**
- Priority: P1 | Size: L | Depends: T-054
- Description: Create `src/chambers_arch/portable_record/emergency_access.py` per PRD Section 7.4. Implement the 5-method priority chain: smartphone app, emergency QR, delegate contact, device interrogation, manufacturer fallback. Simulate emergency scenarios and measure data availability.
- Acceptance Criteria:
  - [ ] Method 1: emergency dataset from smartphone (no auth required)
  - [ ] Emergency dataset: device type, serial, programming, last 3 summaries, allergies, treating MD
  - [ ] Method 2: QR code with encoded device/contact info
  - [ ] Method 3: delegate authorization for full record
  - [ ] Method 4: direct device interrogation (independent of cloud)
  - [ ] Method 5: manufacturer fallback (Device Maint data only, unless patient elected persistence)
  - [ ] Scenario simulation: measure data availability under each method

---

### S-17: Consent Management

**T-063: Implement research consent manager**
- Priority: P1 | Size: M | Depends: T-053
- Description: Create `src/chambers_arch/consent/consent_manager.py`. Track consent lifecycle: grant, active, withdrawn. Link consent to Research World data. Trigger burn on withdrawal. Support multiple concurrent consents (different research programmes).
- Acceptance Criteria:
  - [ ] Consent states: PENDING → GRANTED → ACTIVE → WITHDRAWN
  - [ ] Consent links patient to specific research programme
  - [ ] Consent includes: programme ID, retention period, data scope
  - [ ] Withdrawal triggers mandatory burn of individual-level data
  - [ ] Multiple consents per patient (different programmes)
  - [ ] Consent audit trail with timestamps

**T-064: Implement patient-elected persistence manager**
- Priority: P1 | Size: M | Depends: T-054
- Description: Create `src/chambers_arch/consent/election_manager.py` per PRD Section 6.5. Patient opt-in to manufacturer retention. Granular (clinical data vs. activity data). Revocable. Default: NOT elected (burn-by-default).
- Acceptance Criteria:
  - [ ] Default: no manufacturer persistence (burn-by-default)
  - [ ] Patient can elect persistence for specific data categories
  - [ ] Granularity: clinical, activity, device status (independently selectable)
  - [ ] Revocation: triggers burn of manufacturer-held copy
  - [ ] Election status tracked and reported in analytics

---

# EPIC E-05: Comparative Analytics Engine (Module 4)

> Quantify the differences between architectures with rigorous metrics.

---

### S-18: Persistence & Exposure Analytics

**T-065: Implement persistence volume tracker**
- Priority: P0 | Size: L | Depends: T-043, T-047
- Description: Create `src/analytics/persistence_tracker.py` per PRD Section 8.1. Continuously measure total data volume persisted under each architecture. Track by patient, data type, location. Produce time-series comparison.
- Acceptance Criteria:
  - [ ] V_current(T): sum of all persisted data across all layers
  - [ ] V_chambers(T): sum of active data across all worlds
  - [ ] V_current is monotonically increasing
  - [ ] V_chambers reaches steady state (bounded by burn)
  - [ ] Per-patient breakdown available
  - [ ] Per-data-type breakdown available
  - [ ] Ratio V_current/V_chambers at configurable time points
  - [ ] Projected 10-year differential

**T-066: Implement attack surface calculator**
- Priority: P0 | Size: L | Depends: T-065
- Description: Create `src/analytics/attack_surface.py` per PRD Section 8.2. Calculate attack surface as function of data volume × accessibility × sensitivity × exposure time. Compare architectures.
- Acceptance Criteria:
  - [ ] Attack surface formula from PRD 8.2 implemented
  - [ ] Accessibility weights per location (device, cloud, relay, etc.)
  - [ ] Sensitivity weights per data type (IEGM=1.0, device status=0.3, etc.)
  - [ ] Temporal factor: current = ∞ exposure; Chambers = burn window exposure
  - [ ] Breach impact model: max impact under each architecture
  - [ ] Output: AS_current / AS_chambers ratio over time

**T-067: Implement regulatory compliance scorer**
- Priority: P1 | Size: L | Depends: T-065
- Description: Create `src/analytics/regulatory_compliance.py` per PRD Section 8.5. Score each architecture against GDPR (storage limitation, minimisation, erasure, purpose limitation), HIPAA (minimum necessary), MDR (post-market surveillance capability). Output radar chart data.
- Acceptance Criteria:
  - [ ] GDPR dimensions: storage limitation, data minimisation, right to erasure, purpose limitation
  - [ ] HIPAA dimensions: minimum necessary standard
  - [ ] MDR dimensions: post-market surveillance capability
  - [ ] Scoring: 0-100 per dimension per architecture
  - [ ] Scoring methodology documented and configurable
  - [ ] Output: per-dimension scores + radar chart data structure

---

### S-19: Clinical Impact Analytics

**T-068: Implement clinical availability monitor**
- Priority: P0 | Size: XL | Depends: T-044, T-051
- Description: Create `src/analytics/clinical_availability.py` per PRD Section 8.3. Implement all 5 CA metrics: alert delivery rate, ack-before-burn rate, historical data availability, care continuity, emergency data availability. Run threshold analysis to find minimum safe burn windows.
- Acceptance Criteria:
  - [ ] CA-1: alert delivery rate (target: 1.0)
  - [ ] CA-2: ack-before-burn rate (target: ≥0.95 at 72h window)
  - [ ] CA-3: historical data availability via patient record queries
  - [ ] CA-4: care continuity across provider transitions
  - [ ] CA-5: emergency data availability under various scenarios
  - [ ] Threshold analysis: find min burn window for CA ≥ 0.95, 0.99, 0.999
  - [ ] Per-patient CA scores available
  - [ ] Population-level CA distributions

**T-069: Implement adverse event impact analyzer**
- Priority: P0 | Size: XL | Depends: T-055, T-037
- Description: Create `src/analytics/adverse_event_impact.py` per PRD Section 8.4. For each adverse event type, simulate detection delay, hold trigger, and measure data availability. Compare data loss rates across burn window configurations.
- Acceptance Criteria:
  - [ ] For each adverse event type: generate, detect, hold, measure
  - [ ] Detection delay: configurable distribution per event type
  - [ ] Data availability at hold trigger: categorize as AVAILABLE, LOST, ON_DEVICE
  - [ ] Data loss rate = lost elements / generated pre-event elements
  - [ ] Sweep burn windows: 24h, 48h, 72h, 7d, 14d, 30d
  - [ ] Critical scenario: patient death with delayed discovery
  - [ ] Output: data loss rate × burn window heatmap
  - [ ] Qualitative investigation adequacy assessment

**T-070: Implement architecture comparator**
- Priority: P0 | Size: L | Depends: T-065, T-066, T-067, T-068, T-069
- Description: Create `src/analytics/comparator.py`. Side-by-side comparison engine that aggregates all metrics into a unified comparison. Produces summary tables, comparison charts, and structured data for report generation.
- Acceptance Criteria:
  - [ ] Aggregates all metric types into unified comparison
  - [ ] Summary table: metric name, current arch value, Chambers value, delta
  - [ ] Time-series comparisons for dynamic metrics
  - [ ] Configurable output: JSON, CSV, structured dict for visualization
  - [ ] Supports comparison across multiple scenario runs

---

# EPIC E-06: Visualization & Reporting (Module 5)

> Make the simulation results visible, interactive, and publishable.

---

### S-20: Real-Time Dashboard

**T-071: Create Dash application scaffold**
- Priority: P1 | Size: M | Depends: T-070
- Description: Create `src/visualization/dashboard/app.py`. Plotly Dash application with multi-page layout, navigation, shared state management, and real-time update callbacks. Tabs/pages for: Overview, Flow View, Comparison, Patient Deep-Dive, Scenario Runner.
- Acceptance Criteria:
  - [ ] Dash app starts on port 8050
  - [ ] Multi-page navigation (tabs or URL routing)
  - [ ] 5 pages: Overview, Flow, Comparison, Patient, Scenario
  - [ ] Shared simulation state across pages
  - [ ] Real-time updates via Dash intervals or WebSocket

**T-072: Implement overview dashboard layout**
- Priority: P1 | Size: L | Depends: T-071
- Description: Create `src/visualization/dashboard/layouts/overview.py` matching PRD Section 9.1 layout. Side-by-side architecture panels (data persisted, growth rate, attack surface). Simulation clock, patient count, event rate. Clinical availability scores. Live EGM trace.
- Acceptance Criteria:
  - [ ] Layout matches PRD Section 9.1 wireframe
  - [ ] Side-by-side panels: current arch vs Chambers
  - [ ] KPI cards: data persisted, growth rate, attack surface, oldest data
  - [ ] Simulation status bar: clock, patients, status, events/sec
  - [ ] Clinical availability scores (CA-1 through CA-5)
  - [ ] Burn timeline with event count
  - [ ] All metrics update in real-time during simulation

**T-073: Implement EGM trace visualization component**
- Priority: P1 | Size: L | Depends: T-023
- Description: Create `src/visualization/dashboard/components/egm_trace.py`. Real-time scrolling EGM waveform display. Shows atrial + ventricular channels. Annotations for pacing spikes, detected events. Patient selector dropdown.
- Acceptance Criteria:
  - [ ] Scrolling waveform display (like a bedside monitor)
  - [ ] Atrial and ventricular channels displayed
  - [ ] Pacing spike annotations (vertical markers)
  - [ ] Detected event annotations (AF onset, VT, etc.)
  - [ ] Patient selector to switch between patients
  - [ ] Configurable time window (5s, 10s, 30s visible)
  - [ ] Configurable sweep speed

**T-074: Implement data flow Sankey diagram**
- Priority: P1 | Size: L | Depends: T-070
- Description: Create `src/visualization/dashboard/components/flow_sankey.py`. Interactive Sankey diagram showing data volume flowing from generator through each layer (current) / world (Chambers) to consumers. Width proportional to data volume. Animated flow during simulation.
- Acceptance Criteria:
  - [ ] Sankey for current architecture: Generator → Layer 1-5 → Consumers
  - [ ] Sankey for Chambers architecture: Generator → Relay → Worlds → Patient Record
  - [ ] Link width proportional to data volume (KB/MB)
  - [ ] Color coding by data type (clinical, device, activity, etc.)
  - [ ] Hover details: exact volume, data types, flow rate
  - [ ] Side-by-side or toggle view

**T-075: Implement burn timeline component**
- Priority: P1 | Size: M | Depends: T-056
- Description: Create `src/visualization/dashboard/components/burn_timeline.py`. Timeline showing burn events with data type, volume, and world. Highlight held items. Show upcoming scheduled burns.
- Acceptance Criteria:
  - [ ] Chronological timeline of burn events
  - [ ] Each event shows: timestamp, world, data type, volume burned
  - [ ] Color coding by world
  - [ ] Held items highlighted (orange/yellow)
  - [ ] Upcoming burns shown as scheduled (lighter color)
  - [ ] Zoom: minute, hour, day, week views

**T-076: Implement comparison view layout**
- Priority: P1 | Size: L | Depends: T-071, T-070
- Description: Create `src/visualization/dashboard/layouts/comparison.py`. Dedicated page for side-by-side architecture comparison. Time-series plots for all metrics. Radar chart for regulatory compliance. Data table with raw values.
- Acceptance Criteria:
  - [ ] Persistence volume time-series (dual line chart)
  - [ ] Attack surface time-series (dual line chart)
  - [ ] Clinical availability bar chart (5 CA metrics, both architectures)
  - [ ] Regulatory compliance radar chart (GDPR, HIPAA, MDR dimensions)
  - [ ] Adverse event impact comparison (data loss rate × burn window)
  - [ ] Summary statistics table with export capability

**T-077: Implement patient deep-dive layout**
- Priority: P2 | Size: L | Depends: T-071
- Description: Create `src/visualization/dashboard/layouts/patient_view.py`. Select individual patient and see: profile, device info, recent telemetry, episode history, EGM viewer, data in each world/layer, burn history for this patient.
- Acceptance Criteria:
  - [ ] Patient selector (dropdown or search)
  - [ ] Patient profile card (demographics, diagnosis, device)
  - [ ] Recent telemetry summary (last 5 transmissions)
  - [ ] Episode history timeline
  - [ ] EGM strip viewer (clickable episodes)
  - [ ] Per-architecture data inventory (what data is where)
  - [ ] Burn history for this patient (Chambers only)

**T-078: Implement scenario runner interface**
- Priority: P2 | Size: M | Depends: T-071, T-012
- Description: Create `src/visualization/dashboard/layouts/scenario.py`. UI for selecting, configuring, and running pre-built scenarios. Live progress tracking. Results comparison across scenario runs.
- Acceptance Criteria:
  - [ ] Scenario selector (from YAML definitions)
  - [ ] Parameter override form (burn window, cohort size, etc.)
  - [ ] Start/pause/stop controls
  - [ ] Progress bar with estimated completion
  - [ ] Results appear on Comparison page when complete
  - [ ] Multiple scenario results can be compared

---

### S-21: Flow Diagrams

**T-079: Create current architecture flow diagram (Mermaid)**
- Priority: P1 | Size: S | Depends: none
- Description: Create `docs/architecture/current_arch_flow.mermaid` per PRD Section 9.2. Detailed flow diagram showing all 5 layers, data types at each stage, persistence indicators, and consumer access points.
- Acceptance Criteria:
  - [ ] All 5 layers represented
  - [ ] Data types listed at each layer
  - [ ] Persistence markers (PERSISTENT, INDEFINITE)
  - [ ] Consumer access points shown
  - [ ] Renders correctly in GitHub Mermaid viewer

**T-080: Create Chambers architecture flow diagram (Mermaid)**
- Priority: P1 | Size: S | Depends: none
- Description: Create `docs/architecture/chambers_arch_flow.mermaid` per PRD Section 9.2. Flow diagram showing relay, typed worlds, burn indicators, patient record, and safety investigation hold.
- Acceptance Criteria:
  - [ ] Relay with TTL indicator
  - [ ] All 5 typed worlds with scope labels
  - [ ] Burn indicators (⏳, 🔄, ✓ symbols)
  - [ ] Patient portable record as primary persistence
  - [ ] Safety investigation hold path shown
  - [ ] Renders correctly in GitHub Mermaid viewer

**T-081: Create data model ERD (Mermaid)**
- Priority: P2 | Size: M | Depends: T-050
- Description: Create `docs/architecture/data_model_erd.mermaid`. Entity-relationship diagram for all core entities: Patient, Device, TelemetryEvent, TransmissionPacket, EGMStrip, BurnEvent, SafetyInvestigationHold, typed world records.
- Acceptance Criteria:
  - [ ] All core entities from PRD Section 10.1 represented
  - [ ] Relationships between entities shown
  - [ ] Key fields listed per entity
  - [ ] Cardinality notation (1:N, N:M)
  - [ ] Renders correctly in GitHub Mermaid viewer

---

### S-22: Report Generation

**T-082: Create report generation engine**
- Priority: P1 | Size: L | Depends: T-070
- Description: Create `src/visualization/reports/generator.py`. Generate PDF and HTML reports from simulation results using Jinja2 templates and WeasyPrint. Support all 5 report types from PRD Section 9.3.
- Acceptance Criteria:
  - [ ] Jinja2 templates for all 5 report types
  - [ ] PDF output via WeasyPrint
  - [ ] HTML output (self-contained, inline CSS/JS)
  - [ ] Embedded charts (rendered as PNG/SVG in PDF, interactive in HTML)
  - [ ] Table of contents and page numbering (PDF)
  - [ ] Report metadata: scenario, date, parameters, duration

**T-083: Create Simulation Summary Report template**
- Priority: P1 | Size: M | Depends: T-082
- Description: Template for the primary report type. Includes: scenario description, cohort characteristics, duration, key metrics comparison table, persistence volume charts, attack surface comparison, clinical availability scores, regulatory compliance radar, recommendations.
- Acceptance Criteria:
  - [ ] All sections from PRD 9.3 Report Type 1 included
  - [ ] Comparison table with Current vs Chambers columns
  - [ ] Charts: persistence volume, attack surface, CA metrics
  - [ ] Regulatory compliance radar chart
  - [ ] Executive summary section
  - [ ] Methodology note referencing position paper

**T-084: Create Adverse Event Analysis Report template**
- Priority: P1 | Size: M | Depends: T-082
- Description: Report for adverse event scenario analysis. Event description, timeline, data availability comparison, investigation capability assessment, gap analysis.
- Acceptance Criteria:
  - [ ] Event timeline visualization
  - [ ] Data availability matrix (what data, which architecture, available?)
  - [ ] Investigation capability scoring
  - [ ] Gap analysis: what data was lost under Chambers?
  - [ ] Hold mechanism effectiveness assessment

---

# EPIC E-07: API Layer

> REST API and WebSocket endpoints for programmatic access and real-time streaming.

---

### S-23: REST API

**T-085: Create FastAPI application scaffold**
- Priority: P1 | Size: M | Depends: T-009
- Description: Create `src/api/main.py`. FastAPI application with CORS, error handling, OpenAPI documentation, health check endpoint, and structured logging.
- Acceptance Criteria:
  - [ ] FastAPI app on port 8000
  - [ ] CORS configured for dashboard origin
  - [ ] OpenAPI docs at `/docs`
  - [ ] Health check at `/health`
  - [ ] Structured JSON logging
  - [ ] Global exception handler with error response format

**T-086: Implement simulation control endpoints**
- Priority: P1 | Size: L | Depends: T-085, T-040
- Description: Create `src/api/routes/simulation.py` per PRD Section 11.1. Start, stop, pause, resume simulation. Inject events. Set clock speed. Get status.
- Acceptance Criteria:
  - [ ] POST `/api/v1/simulation/start`: start with scenario and config overrides
  - [ ] POST `/api/v1/simulation/{id}/stop`: stop running simulation
  - [ ] POST `/api/v1/simulation/{id}/pause`: pause at current sim clock
  - [ ] POST `/api/v1/simulation/{id}/resume`: resume from paused state
  - [ ] GET `/api/v1/simulation/{id}/status`: return sim clock, events, status
  - [ ] POST `/api/v1/simulation/{id}/inject-event`: inject adverse event
  - [ ] POST `/api/v1/simulation/{id}/set-clock-speed`: change time multiplier

**T-087: Implement patient and cohort endpoints**
- Priority: P1 | Size: M | Depends: T-085, T-040
- Description: Create `src/api/routes/patients.py` per PRD Section 11.2. CRUD for patients and cohorts. Query telemetry, EGM strips, portable record.
- Acceptance Criteria:
  - [ ] GET `/api/v1/patients`: list with pagination and profile filter
  - [ ] POST `/api/v1/patients`: create patient with profile and overrides
  - [ ] GET `/api/v1/patients/{id}`: patient detail
  - [ ] GET `/api/v1/patients/{id}/telemetry`: query with time range and type filter
  - [ ] GET `/api/v1/patients/{id}/egm/{strip_id}`: retrieve EGM strip data
  - [ ] GET `/api/v1/patients/{id}/portable-record`: download portable record
  - [ ] POST `/api/v1/cohorts`: create cohort with size and distribution
  - [ ] GET `/api/v1/cohorts/{id}`: cohort detail with patient list

**T-088: Implement analytics endpoints**
- Priority: P1 | Size: M | Depends: T-085, T-070
- Description: Create `src/api/routes/analytics.py` per PRD Section 11.3. Query persistence volume, attack surface, clinical availability, adverse event impact, compliance scores, and full comparison reports.
- Acceptance Criteria:
  - [ ] GET `/api/v1/analytics/persistence-volume`: time-series data
  - [ ] GET `/api/v1/analytics/attack-surface`: current scores
  - [ ] GET `/api/v1/analytics/clinical-availability`: CA-1 through CA-5
  - [ ] GET `/api/v1/analytics/adverse-event-impact`: scenario results
  - [ ] GET `/api/v1/analytics/compliance-score`: GDPR/HIPAA/MDR scores
  - [ ] GET `/api/v1/analytics/comparison-report`: full comparison (json/pdf/html)

**T-089: Implement Chambers-specific endpoints**
- Priority: P1 | Size: M | Depends: T-085, T-047, T-055
- Description: Create `src/api/routes/` endpoints per PRD Section 11.4. World status, relay status, burn history, hold management (create, get, release).
- Acceptance Criteria:
  - [ ] GET `/api/v1/chambers/worlds`: list all worlds with status
  - [ ] GET `/api/v1/chambers/worlds/{name}/status`: world detail
  - [ ] GET `/api/v1/chambers/relay/status`: items in relay, oldest, next burn
  - [ ] GET `/api/v1/chambers/burns`: burn history with time/world filter
  - [ ] POST `/api/v1/chambers/holds`: create safety investigation hold
  - [ ] GET `/api/v1/chambers/holds/{id}`: hold detail
  - [ ] DELETE `/api/v1/chambers/holds/{id}`: release hold

---

### S-24: WebSocket Streaming

**T-090: Implement WebSocket telemetry stream**
- Priority: P1 | Size: M | Depends: T-085, T-039
- Description: Create `src/api/websockets/stream.py` per PRD Section 11.5. Four WebSocket endpoints: telemetry events, burn events, clinical alerts, real-time metrics. Each streams JSON messages.
- Acceptance Criteria:
  - [ ] WS `/ws/telemetry/{sim_id}`: streams telemetry events
  - [ ] WS `/ws/burns/{sim_id}`: streams burn events
  - [ ] WS `/ws/alerts/{sim_id}`: streams clinical alerts
  - [ ] WS `/ws/metrics/{sim_id}`: streams metric updates
  - [ ] All streams: JSON message format per PRD 11.5
  - [ ] Connection management: handle connect/disconnect gracefully
  - [ ] Backpressure: skip events if client is slow (with count of skipped)

---

# EPIC E-08: Testing

> Comprehensive test suite covering signal properties, burn guarantees, and end-to-end scenarios.

---

### S-25: Unit Tests

**T-091: Unit tests for rhythm engine**
- Priority: P0 | Size: L | Depends: T-015, T-017
- Description: Test all 18 rhythm types produce HR within specified ranges. Test state transitions follow Markov chain. Test circadian modulation. Test reproducibility with fixed seeds.
- Acceptance Criteria:
  - [ ] Each rhythm type: 100 runs, HR within specified range (100% pass)
  - [ ] Transition probabilities: Monte Carlo test matches expected rates (±10%)
  - [ ] Circadian modulation: night HR < day HR
  - [ ] Fixed seed: two runs produce identical output

**T-092: Unit tests for EGM synthesizer**
- Priority: P0 | Size: L | Depends: T-023
- Description: Test waveform components have correct morphology, amplitude, duration. Test multi-channel synchronization. Test quantization. Test episode-triggered recording.
- Acceptance Criteria:
  - [ ] P wave duration 80-120ms, amplitude 0.5-2.0mV (atrial channel)
  - [ ] QRS duration 80-120ms (narrow) or 120-200ms (wide)
  - [ ] Multi-channel time alignment within 1 sample
  - [ ] 12-bit quantization: values within 0-4095
  - [ ] Episode trigger: EGM strip generated with correct duration and buffer

**T-093: Unit tests for pacing engine**
- Priority: P0 | Size: L | Depends: T-024, T-025
- Description: Test VVI and DDD pacing logic. Verify correct pacing/inhibition behavior. Verify mode switching. Test rate response.
- Acceptance Criteria:
  - [ ] VVI: pace when no sense within escape interval
  - [ ] VVI: inhibit on sensed event
  - [ ] DDD: all four states produce correct behavior
  - [ ] DDD: mode switch on AF detection, recovery on AF termination
  - [ ] Rate response: rate increases with activity, decreases on rest

**T-094: Unit tests for burn scheduler**
- Priority: P0 | Size: M | Depends: T-056
- Description: Test burn execution timing, hold interaction, verification. Test all burn policies.
- Acceptance Criteria:
  - [ ] Burn executes within 1s of scheduled time
  - [ ] Held data is not burned
  - [ ] Post-hold: burn resumes after buffer period
  - [ ] Each burn policy triggers correctly for its world
  - [ ] Burn verification certificate generated

**T-095: Unit tests for typed worlds**
- Priority: P0 | Size: L | Depends: T-051, T-052, T-053, T-054, T-055
- Description: Test data scope enforcement, access control, and burn behavior for each typed world.
- Acceptance Criteria:
  - [ ] Clinical World rejects activity data
  - [ ] Device Maintenance rejects IEGMs and episodes
  - [ ] Research World Channel B rejects without consent
  - [ ] Safety Investigation World freezes burns on hold
  - [ ] Cross-world queries are denied

**T-096: Unit tests for analytics**
- Priority: P1 | Size: M | Depends: T-065, T-066, T-068
- Description: Test persistence volume calculation, attack surface formula, clinical availability metrics with known inputs and expected outputs.
- Acceptance Criteria:
  - [ ] Persistence volume: known input produces expected volume
  - [ ] Attack surface: formula produces correct values for test cases
  - [ ] CA metrics: known scenario produces expected scores
  - [ ] Comparator: produces correct deltas

---

### S-26: Integration Tests

**T-097: Integration test — current architecture end-to-end**
- Priority: P0 | Size: L | Depends: T-041, T-042, T-043, T-044
- Description: Generate 1 patient, 30 days. Verify data flows through all 5 layers. Verify persistence at each layer. Verify clinician portal access and acknowledgment.
- Acceptance Criteria:
  - [ ] Data present on device (Layer 1)
  - [ ] Transmission to cloud (Layer 2→3) occurs on schedule
  - [ ] Cloud stores all transmitted data (Layer 3)
  - [ ] Clinician portal shows alerts and reports (Layer 4)
  - [ ] Aggregate pool updated (Layer 5)
  - [ ] Data volume monotonically increases

**T-098: Integration test — Chambers architecture end-to-end**
- Priority: P0 | Size: L | Depends: T-047, T-051, T-052, T-054
- Description: Generate 1 patient, 30 days. Verify data routes through relay to worlds. Verify burn execution. Verify patient record receives all data. Verify relay is empty after burns.
- Acceptance Criteria:
  - [ ] Data enters relay and routes to worlds
  - [ ] Clinical World receives clinical data, burns after ack
  - [ ] Device Maintenance receives device data with rolling window
  - [ ] Patient record receives full dataset
  - [ ] Relay is empty after all burns execute
  - [ ] Data volume reaches steady state

**T-099: Integration test — adverse event scenario**
- Priority: P0 | Size: XL | Depends: T-055, T-059, T-069
- Description: Generate patient, inject lead fracture at day 90, simulate detection delay, trigger hold. Verify hold freezes burns. Compare data availability with current architecture. Release hold, verify delayed burn.
- Acceptance Criteria:
  - [ ] Lead fracture generates expected impedance change
  - [ ] Alert generated and transmitted
  - [ ] Hold triggered (manually or by adverse event detection)
  - [ ] Burns frozen for affected patient
  - [ ] Data in relay preserved during hold
  - [ ] Data already burned is NOT recoverable (verified)
  - [ ] Hold release → buffer period → burn executes
  - [ ] Comparison report shows data availability difference

**T-100: Integration test — comparison pipeline**
- Priority: P0 | Size: L | Depends: T-070
- Description: Run 100 patients, 365 days through both architectures. Verify all analytics metrics are calculated. Verify comparison report generates correctly.
- Acceptance Criteria:
  - [ ] Both architectures process identical event stream
  - [ ] Persistence volume tracked for both
  - [ ] Attack surface calculated for both
  - [ ] Clinical availability metrics calculated
  - [ ] Comparison report (PDF) generates without error
  - [ ] All charts render correctly

---

### S-27: Property-Based Tests

**T-101: Property tests for signal characteristics**
- Priority: P1 | Size: L | Depends: T-015, T-023
- Description: Use Hypothesis to verify signal properties hold across wide parameter ranges.
- Acceptance Criteria:
  - [ ] ∀ rhythm: HR within specified range
  - [ ] ∀ EGM strip: sample count = duration × rate / 1000
  - [ ] ∀ paced beat: pacing artifact present AND timing correct
  - [ ] ∀ NSR: RR interval coefficient of variation < 15%
  - [ ] ∀ AF: RR interval coefficient of variation > 15%
  - [ ] Properties hold for 1000+ generated examples

**T-102: Property tests for burn guarantees**
- Priority: P0 | Size: L | Depends: T-056
- Description: Use Hypothesis to verify burn properties: no data survives burn, holds prevent burn, TTL expiry triggers burn.
- Acceptance Criteria:
  - [ ] ∀ burned data: query returns empty
  - [ ] ∀ held data: not burned during hold
  - [ ] ∀ relay data: TTL expired AND NOT held → burned
  - [ ] ∀ consent-withdrawn research data: burned
  - [ ] Properties hold for 1000+ generated scenarios

**T-103: Property tests for world isolation**
- Priority: P0 | Size: M | Depends: T-050
- Description: Use Hypothesis to verify world boundaries cannot be violated.
- Acceptance Criteria:
  - [ ] ∀ cross-world query: result = AccessDenied
  - [ ] ∀ data element: assigned to exactly the correct worlds
  - [ ] ∀ world: only accepts data types in its scope
  - [ ] Properties hold for 1000+ generated attempts

---

# EPIC E-09: Scenarios & Demo

> Pre-built scenarios for demonstration and publication.

---

### S-28: Scenario Implementation

**T-104: Implement baseline single patient scenario**
- Priority: P0 | Size: M | Depends: T-039, T-043, T-047
- Description: 1 patient (P-001, sick sinus syndrome, DDD), 365 days. Normal operation, no adverse events. Establishes baseline comparison between architectures.
- Acceptance Criteria:
  - [ ] Runs to completion without error
  - [ ] Produces comparison report
  - [ ] Persistence volume ratio ≥ 10:1 at 1 year
  - [ ] Clinical availability CA-1 = 1.0

**T-105: Implement AF detection and alert scenario**
- Priority: P0 | Size: M | Depends: T-035, T-036
- Description: 1 patient (P-003, paroxysmal AF), 180 days. Frequent AF episodes trigger alerts. Tests alert delivery and acknowledgment under both architectures. Measures clinical data availability for AF management.
- Acceptance Criteria:
  - [ ] Multiple AF episodes generated (AF burden ~30%)
  - [ ] Alerts generated for episodes exceeding detection threshold
  - [ ] Alerts delivered under both architectures
  - [ ] Chambers: alert data available until clinician acknowledgment
  - [ ] Comparison of alert acknowledgment timing vs burn window

**T-106: Implement lead fracture adverse event scenario**
- Priority: P0 | Size: L | Depends: T-037, T-055
- Description: 1 patient (P-004, CRT-D), lead fracture injected at day 180. Simulate detection delay (variable), hold trigger, investigation. Compare data availability for investigation under both architectures.
- Acceptance Criteria:
  - [ ] Lead fracture at day 180: impedance spike, alert, EGM recording
  - [ ] Detection delay: simulated at 1h, 24h, 72h, 7 days
  - [ ] Hold triggered: all data frozen in Chambers
  - [ ] Data availability comparison at each detection delay
  - [ ] Report: data loss rate vs detection delay under each burn window

**T-107: Implement battery EOL transition scenario**
- Priority: P1 | Size: M | Depends: T-028
- Description: 1 patient (P-006, VVI, old device), accelerated battery drain to ERI then EOS. Tests alert generation, clinical urgency communication, data availability during device replacement planning.
- Acceptance Criteria:
  - [ ] Battery progresses through BOL → MOL → ERI → EOS
  - [ ] ERI alert generated with correct priority (High)
  - [ ] EOS alert generated with correct priority (Critical)
  - [ ] Device programming changes at ERI (backup mode)
  - [ ] Clinical data available for replacement planning

**T-108: Implement clinician latency stress test**
- Priority: P0 | Size: L | Depends: T-044, T-068
- Description: 100 patients, mixed profiles, 180 days. Vary clinician response times from ideal (hours) to worst-case (weeks). Measure CA-2 (ack-before-burn) across burn window configurations. Find the minimum safe burn window.
- Acceptance Criteria:
  - [ ] 100 patients with varied alert frequencies
  - [ ] Clinician latency varied: ideal, typical, stressed, worst-case
  - [ ] CA-2 calculated for burn windows: 24h, 48h, 72h, 7d, 14d, 30d
  - [ ] Minimum burn window for CA-2 ≥ 0.95 identified
  - [ ] Minimum burn window for CA-2 ≥ 0.99 identified
  - [ ] Report: CA-2 × burn window × clinician latency heatmap

**T-109: Implement provider transition scenario**
- Priority: P1 | Size: M | Depends: T-054, T-068
- Description: 1 patient, clinician change at day 180. Test care continuity: does the new clinician have access to historical data? Compare current arch (manufacturer cloud continuity) vs Chambers (portable record).
- Acceptance Criteria:
  - [ ] Provider transition at day 180
  - [ ] Current arch: new provider accesses same manufacturer portal → full history
  - [ ] Chambers: new provider receives portable record → full history in patient record
  - [ ] CA-4 measured for both architectures
  - [ ] Edge case: what if portable record unavailable?

**T-110: Implement population-scale mixed scenario**
- Priority: P1 | Size: L | Depends: T-040, T-070
- Description: 1000 patients, mixed profiles (per PRD 5.6.2 distributions), 365 days. Full comparison across all metrics. Primary benchmark for the simulator's headline claims.
- Acceptance Criteria:
  - [ ] 1000 patients generated with correct distributions
  - [ ] 365 days simulated in < 1 hour (performance target)
  - [ ] All analytics metrics calculated
  - [ ] Full comparison report generated (PDF + HTML)
  - [ ] Headline metrics: persistence ratio, attack surface ratio, CA scores

**T-111: Implement cybersecurity breach scenario**
- Priority: P1 | Size: L | Depends: T-066
- Description: 1000 patients, simulated breach at day 100. Under current arch: attacker accesses cloud (all data exposed). Under Chambers: attacker accesses relay (only TTL-window data exposed). Quantify exposure differential.
- Acceptance Criteria:
  - [ ] Breach simulated at day 100
  - [ ] Current arch: all data from day 0-100 exposed (100 days × 1000 patients)
  - [ ] Chambers: only relay window data exposed (72h × 1000 patients)
  - [ ] Exposure volume calculated: GB current vs MB Chambers
  - [ ] Sensitivity-weighted exposure calculated
  - [ ] Breach impact report generated

**T-112: Implement law enforcement access scenario**
- Priority: P2 | Size: M | Depends: T-043, T-054
- Description: 1 patient, simulated warrant at day 200. Compare: current arch (single subpoena to manufacturer → full history) vs Chambers (warrant targets patient/clinician → only patient record available). Document structural differences.
- Acceptance Criteria:
  - [ ] Current arch: manufacturer produces complete data on subpoena
  - [ ] Chambers: patient record contains full history, but manufacturer has only Device Maint data
  - [ ] Structural difference documented: centralised vs distributed access
  - [ ] Report section on bulk surveillance implications

---

### S-29: Demo & Documentation

**T-113: Create demo script**
- Priority: P1 | Size: M | Depends: T-104, T-072
- Description: Create `scripts/demo.py` — a guided demonstration that runs a single patient through 30 simulated days, narrates what's happening at each stage, and produces a comparison report. Suitable for conference presentations and stakeholder demos.
- Acceptance Criteria:
  - [ ] Runs standalone: `python scripts/demo.py`
  - [ ] Console output narrates simulation progress
  - [ ] Key events highlighted (transmissions, alerts, burns)
  - [ ] Opens dashboard in browser (optional)
  - [ ] Generates summary report at completion
  - [ ] Runs in < 2 minutes

**T-114: Create OpenAPI specification**
- Priority: P2 | Size: M | Depends: T-085, T-086, T-087, T-088, T-089
- Description: Export and refine the auto-generated OpenAPI spec from FastAPI. Add detailed descriptions, examples, and error response schemas. Save to `docs/api/openapi.yaml`.
- Acceptance Criteria:
  - [ ] Complete OpenAPI 3.1 spec for all endpoints
  - [ ] Request/response examples for each endpoint
  - [ ] Error response schemas documented
  - [ ] Published at `/docs` in running API

**T-115: Write project README**
- Priority: P1 | Size: M | Depends: T-113
- Description: Comprehensive README with: project overview, relationship to position paper, quickstart (docker compose up), architecture diagram, running scenarios, generating reports, API documentation link, contributing guide.
- Acceptance Criteria:
  - [ ] Project overview and motivation
  - [ ] Quickstart: 3-command setup
  - [ ] Architecture diagram (from Mermaid sources)
  - [ ] Running pre-built scenarios
  - [ ] Generating comparison reports
  - [ ] Link to API docs
  - [ ] License and citation information

---

# EPIC E-10: Performance & Optimization

> Ensure the simulator meets throughput targets for population-scale simulation.

---

### S-30: Performance Engineering

**T-116: Profile and optimize telemetry generator**
- Priority: P1 | Size: L | Depends: T-040
- Description: Profile the generator with 1000 patients. Identify bottlenecks (EGM synthesis is likely the hottest path). Optimize with NumPy vectorization, pre-computed waveform templates, and batched event emission.
- Acceptance Criteria:
  - [ ] Profiling report identifies top 5 bottlenecks
  - [ ] EGM synthesis: use pre-computed templates with parameterized transforms
  - [ ] Rhythm engine: vectorize beat generation (batch 1000 beats)
  - [ ] 1000 patients × 1 year simulated in < 1 hour
  - [ ] Memory usage < 4 GB for 1000 patients

**T-117: Optimize database writes for current arch simulation**
- Priority: P1 | Size: M | Depends: T-043
- Description: Batch TimescaleDB inserts. Use COPY instead of INSERT for bulk loads. Configure chunk intervals. Optimize indexes for query patterns.
- Acceptance Criteria:
  - [ ] Batch insert: 1000 events per INSERT or COPY batch
  - [ ] TimescaleDB chunk interval tuned for query patterns
  - [ ] Write throughput: > 10,000 events/second
  - [ ] Query latency: < 100ms for single-patient time-range queries

**T-118: Implement simulation clock acceleration**
- Priority: P0 | Size: M | Depends: T-039
- Description: The simulation must support variable time acceleration (1x = real-time, 3600x = 1 simulated hour per real second, etc.). Events are generated at accelerated rate. All time-dependent components (burn scheduler, acknowledgment latency) respect sim clock.
- Acceptance Criteria:
  - [ ] Clock speed configurable: 1x to 86400x (1 day/second)
  - [ ] All components use sim clock, not wall clock
  - [ ] Burn scheduler respects sim clock
  - [ ] Clinician acknowledgment latency scaled to sim clock
  - [ ] Dashboard displays sim clock prominently

**T-119: Implement parallel patient simulation**
- Priority: P2 | Size: L | Depends: T-040
- Description: Use multiprocessing or asyncio to simulate multiple patients in parallel. Patient simulations are independent (no shared state). Merge events from parallel workers into unified stream.
- Acceptance Criteria:
  - [ ] Patient simulation distributable across N workers
  - [ ] Linear speedup up to CPU core count
  - [ ] Event stream correctly merged (ordered by sim time)
  - [ ] No shared state between patient workers
  - [ ] 10,000 patients × 1 year in < 2 hours (16 cores)

---

# EPIC E-11: Extended Features (Future / P3)

> Features that extend the simulator's value but are not required for initial release.

---

### S-31: Second Device Class Mapping

**T-120: Design insulin pump / CGM data model**
- Priority: P3 | Size: L | Depends: T-050
- Description: Map the Chamber Sentinel framework to a second device class (insulin pumps + continuous glucose monitors) as identified in the position paper's next steps. Define data types, persistence requirements, typed worlds, and burn schedules for this domain.
- Acceptance Criteria:
  - [ ] Data model for CGM telemetry (glucose readings, calibrations, alerts)
  - [ ] Data model for insulin pump (basal rates, bolus deliveries, alarms)
  - [ ] Typed world mapping for diabetes device ecosystem
  - [ ] Burn schedule proposals for diabetes-specific data
  - [ ] Comparison of persistence requirements: cardiac vs diabetes devices

**T-121: Implement CGM telemetry generator**
- Priority: P3 | Size: XL | Depends: T-120
- Description: Synthetic CGM data: glucose time series (5-minute intervals), trend arrows, calibration events, alert thresholds, sensor sessions. Use established glucose variability models (e.g., Dalla Man model).
- Acceptance Criteria:
  - [ ] Glucose time series at 5-minute intervals
  - [ ] Physiologically plausible range (40-400 mg/dL)
  - [ ] Meal responses, exercise effects, insulin action
  - [ ] Sensor noise and drift modeling
  - [ ] Calibration events

---

### S-32: Advanced Analytics

**T-122: Implement Monte Carlo sensitivity analysis**
- Priority: P2 | Size: L | Depends: T-070
- Description: Run Monte Carlo simulations varying key parameters (burn window, clinician latency, adverse event rate, detection delay) to produce confidence intervals on all comparison metrics.
- Acceptance Criteria:
  - [ ] Configurable parameter sweep ranges
  - [ ] N=100+ simulation runs per parameter combination
  - [ ] 95% confidence intervals on all metrics
  - [ ] Sensitivity report: which parameters most affect outcomes
  - [ ] Tornado diagram for parameter sensitivity

**T-123: Implement re-identification risk analysis**
- Priority: P2 | Size: L | Depends: T-045, T-053
- Description: Model re-identification risk for aggregated CIED data using quasi-identifiers (age, sex, device model, region, implant year). Measure how risk changes with population size, k-anonymity level, and differential privacy epsilon.
- Acceptance Criteria:
  - [ ] Quasi-identifier combinations enumerated
  - [ ] Re-identification probability as function of population size
  - [ ] Risk vs k-anonymity level
  - [ ] Risk vs differential privacy epsilon
  - [ ] Recommendations for minimum k and epsilon values

---

### S-33: Interoperability

**T-124: Implement IEEE 11073 SDC data export**
- Priority: P3 | Size: XL | Depends: T-060
- Description: Export CIED telemetry in IEEE 11073 SDC (Service-oriented Device Connectivity) format for interoperability with hospital device integration platforms.
- Acceptance Criteria:
  - [ ] SDC data model mapping for CIED observations
  - [ ] Export capability for real-time and historical data
  - [ ] Validation against SDC conformance test suite

**T-125: Implement Apple Health / Google Health Connect export**
- Priority: P3 | Size: L | Depends: T-054
- Description: Export patient portable record to consumer health platforms. Map CIED data types to HealthKit/Health Connect data types.
- Acceptance Criteria:
  - [ ] Apple Health export: heart rate, AF episodes, device info
  - [ ] Google Health Connect export: equivalent mapping
  - [ ] Data fidelity assessment: what is lost in translation?

---

### S-34: Advanced Visualization

**T-126: Implement 3D heart model with lead placement**
- Priority: P3 | Size: XL | Depends: T-073
- Description: Three.js-based 3D heart model showing approximate lead placement positions. Animate pacing activations. Useful for presentations and patient education.
- Acceptance Criteria:
  - [ ] 3D heart mesh with chambers visible
  - [ ] Lead positions shown (RA, RV, LV for CRT)
  - [ ] Pacing activations animated (flash at pacing site)
  - [ ] Rotatable, zoomable viewer
  - [ ] Embedded in dashboard patient view

**T-127: Implement animated data flow visualization**
- Priority: P3 | Size: L | Depends: T-074
- Description: Animated particles flowing through the architecture diagram. Particles represent data elements. Speed represents data rate. Particles disappear at burn events. Visually striking for presentations.
- Acceptance Criteria:
  - [ ] Particles flow from device through architecture layers/worlds
  - [ ] Particle density proportional to data rate
  - [ ] Particles visually destroyed at burn events (fade/explosion)
  - [ ] Side-by-side: current (particles accumulate) vs Chambers (particles destroyed)
  - [ ] Embeddable as standalone HTML for presentations

---

# EPIC E-12: openCARP Integration — Biophysical EGM Fidelity (Module 6)

> Replace synthetic Gaussian waveforms with biophysically accurate EGM templates generated by openCARP's ionic models. Template library approach: run openCARP offline to pre-compute waveforms, load at runtime with zero openCARP dependency.

---

### S-35: openCARP Infrastructure & Template Generation

**T-128: Set up openCARP Docker environment for macOS**
- Priority: P1 | Size: M | Depends: T-005
- Description: Create Docker-based openCARP environment for template generation. Write `docker-compose.opencarp.yml` with the `opencarp/opencarp:latest` image. Create volume mounts for template output directory. Verify openCARP runs on macOS Docker Desktop with sufficient memory (8 GB). Add `make generate-templates-docker` target.
- Acceptance Criteria:
  - [ ] `docker pull opencarp/opencarp:latest` succeeds
  - [ ] openCARP binary runs inside container: `openCARP --version` returns
  - [ ] CARPutils Python package accessible inside container
  - [ ] Volume mount maps `src/generator/cardiac/opencarp/templates/` to container output
  - [ ] `make generate-templates-docker` target exists and invokes container
  - [ ] Works on macOS with Docker Desktop (Apple Silicon and Intel)
  - [ ] Documented fallback: native build instructions for Homebrew

**T-129: Create openCARP simulation parameter files for all rhythm types**
- Priority: P1 | Size: XL | Depends: T-128
- Description: Write openCARP `.par` parameter files, ionic model configurations, and mesh/geometry definitions for all 18 rhythm types per PRD Section 15.3.1. Each configuration specifies: ionic model (ten Tusscher 2006, O'Hara-Rudy 2011, or Courtemanche 1998), tissue geometry (slab, ring, wedge, or dual-slab), stimulation protocol (pacing rate, burst induction for AF/VT, ectopic timing for PVCs), and recording duration.
- Acceptance Criteria:
  - [ ] Parameter files for all 18 rhythm types from PRD Table 15.3.1
  - [ ] NSR: ten Tusscher + Courtemanche, slab geometry, 60-100 bpm pacing
  - [ ] AF: Courtemanche with reduced IKur/ICaL, 2D sheet with fibrosis, burst induction
  - [ ] VT monomorphic: O'Hara-Rudy, slab with scar region, re-entrant circuit
  - [ ] VT polymorphic: O'Hara-Rudy with reduced IKr, triggered activity
  - [ ] VF: O'Hara-Rudy, 3D wedge, spiral wave breakup
  - [ ] CHB: dual-slab (independent atrial/ventricular), no AV coupling
  - [ ] Mobitz I/II: AV delay zone with decremental/intermittent conduction
  - [ ] PVCs: focal ectopic source at varying coupling intervals
  - [ ] Paced (VVI/DDD/CRT): stimulus at lead positions per Section 15.3.2
  - [ ] Each parameter file tested: openCARP runs without error

**T-130: Define virtual lead positions and EGM extraction geometry**
- Priority: P1 | Size: M | Depends: T-129
- Description: Define virtual electrode positions per PRD Section 15.3.2 for all four recording channels: atrial bipolar (RA appendage tip-ring), ventricular bipolar (RV apex tip-ring), shock channel (RV coil to can), LV bipolar (CS lead tip-ring for CRT). Map these to openCARP mesh coordinates for each tissue geometry. Implement bipolar computation (tip minus ring voltage difference).
- Acceptance Criteria:
  - [ ] Atrial bipolar: RA appendage tip + ring, 1cm separation
  - [ ] Ventricular bipolar: RV apex tip + ring, 1cm separation
  - [ ] Shock channel: RV coil to pectoral can (far-field)
  - [ ] LV bipolar: lateral wall tip + ring (CRT only)
  - [ ] Coordinate mapping for slab, ring, wedge, and dual-slab geometries
  - [ ] Bipolar computation implemented: V_tip - V_ring
  - [ ] Channel gains match expected clinical amplitudes (A-EGM: 1-5mV, V-EGM: 5-20mV, Shock: 0.5-3mV)

**T-131: Implement template generator batch runner**
- Priority: P0 | Size: XL | Depends: T-129, T-130
- Description: Create `src/generator/cardiac/opencarp/template_generator.py`. Iterates over all rhythm configurations, runs openCARP for each, extracts virtual EGMs from output files (igb/vtk format), segments continuous recordings into individual beat templates, normalizes timing (align to fiducial point), and exports as `.npy` arrays. Generates `template_catalog.json` manifest.
- Acceptance Criteria:
  - [ ] `TemplateGenerator.generate_all()` runs all 18 rhythm configurations
  - [ ] openCARP invoked via subprocess (Docker or native, auto-detected)
  - [ ] Output files parsed: `.igb` or `.vtk` → numpy arrays
  - [ ] Virtual EGM computed at defined lead positions
  - [ ] Beat segmentation: R-peak (or equivalent) detection, individual beat extraction
  - [ ] Pre-trigger buffer: 10ms before fiducial point
  - [ ] Templates normalized: aligned to fiducial, consistent sample rate (1000 Hz source)
  - [ ] Export: one `.npy` file per rhythm per channel (shape: [n_beats, samples_per_beat])
  - [ ] `template_catalog.json` generated with metadata per PRD Section 15.3.3
  - [ ] 50-100 beat templates per rhythm type (100-150 for AF/VF due to variability)
  - [ ] Total library: ~1500-2000 templates, 50-200 MB on disk
  - [ ] Generation completes in < 4 hours on macOS (Docker, 8 GB RAM, 4 cores)
  - [ ] Progress reporting: prints current rhythm being generated + ETA

**T-132: Implement beat segmentation and fiducial alignment**
- Priority: P1 | Size: L | Depends: T-131
- Description: Robust beat segmentation from continuous openCARP output. Must handle: regular rhythms (QRS detection via derivative threshold), AF (atrial fibrillatory baseline segmentation by ventricular activation), VF (overlapping waveforms requiring entropy-based segmentation), and paced rhythms (pacing artifact as fiducial). Align all beats to consistent fiducial point for template interchangeability.
- Acceptance Criteria:
  - [ ] NSR/bradycardia/tachycardia: R-peak detection via first-derivative threshold
  - [ ] AF: ventricular activation as primary fiducial (irregular RR preserved in template metadata)
  - [ ] VT: QRS onset detection for monomorphic; sliding window for polymorphic
  - [ ] VF: fixed-duration windowing (no reliable fiducial; 500ms windows)
  - [ ] Paced beats: pacing artifact timestamp as fiducial
  - [ ] All beats aligned to fiducial at sample index 0
  - [ ] Beat duration stored in template metadata (not fixed — varies with HR)
  - [ ] Reject corrupted beats (clipping, simulation artifacts) — quality filter

---

### S-36: Template Library & Runtime Integration

**T-133: Implement template library loader**
- Priority: P0 | Size: L | Depends: T-131
- Description: Create `src/generator/cardiac/opencarp/template_library.py`. Loads `template_catalog.json` and memory-maps `.npy` files for efficient access. Provides `get_beat()` and `get_beat_multichannel()` methods that return numpy arrays for a given rhythm state. Implements beat-to-beat variation injection (±5% amplitude, ±3% duration, morphology interpolation between adjacent templates).
- Acceptance Criteria:
  - [ ] `TemplateLibrary.load_catalog()`: reads JSON manifest, memory-maps .npy files
  - [ ] `get_beat(rhythm, channel, rng)`: returns single beat as np.ndarray
  - [ ] `get_beat_multichannel(rhythm, rng)`: returns dict[channel, np.ndarray], time-aligned
  - [ ] Random selection from template pool with optional weighted distribution
  - [ ] Beat-to-beat variation: amplitude scaling (±5%), duration stretching (±3%)
  - [ ] Morphology variation: linear interpolation between adjacent template indices
  - [ ] `is_available()`: checks if templates directory is populated
  - [ ] Memory usage: < 300 MB for full library (memory-mapped, lazy loading)
  - [ ] Lookup time: < 1ms per beat

**T-134: Implement ionic model adapter**
- Priority: P1 | Size: M | Depends: T-133
- Description: Create `src/generator/cardiac/opencarp/ionic_adapter.py`. Adapts openCARP template output to the EGM synthesizer interface. Handles: sample rate conversion (openCARP 1000 Hz → device 128-512 Hz via scipy.signal.resample), amplitude scaling to device ADC range, channel mapping (openCARP recording sites → device channels), and template duration normalization (time-stretch to match target RR interval from rhythm engine).
- Acceptance Criteria:
  - [ ] `adapt_beat()`: resamples from 1000 Hz to target rate (128/256/512 Hz)
  - [ ] Time-stretch/compress: template fits target RR interval from rhythm engine
  - [ ] Amplitude scaling: output in device ADC range (0-4095 for 12-bit)
  - [ ] Channel mapping: openCARP atrial → A-EGM, ventricular → V-EGM, far-field → Shock
  - [ ] Per-channel gain factors: near-field (1.0x) vs far-field (0.3x)
  - [ ] `adapt_multichannel()`: consistent timing across all channels
  - [ ] Interpolation method: cubic spline for resampling (avoid aliasing)

**T-135: Integrate Mode B into EGM synthesizer**
- Priority: P0 | Size: L | Depends: T-133, T-134, T-023
- Description: Modify `src/generator/cardiac/egm_synthesizer.py` to support `mode="opencarp"` parameter. When Mode B is active, the synthesizer looks up the current rhythm state in the template library instead of generating Gaussian waveforms. Applies conduction timing from `conduction.py`, adds pacing artifacts from `pacing_engine.py`, then applies the same noise model as Mode A. Graceful fallback to Mode A if templates are unavailable.
- Acceptance Criteria:
  - [ ] `EGMSynthesizer(mode="opencarp")` activates Mode B
  - [ ] Mode B uses `TemplateLibrary.get_beat_multichannel()` for waveform generation
  - [ ] Conduction timing (PR interval, AV delay) applied from `conduction.py`
  - [ ] Pacing artifacts overlaid from `pacing_engine.py` (same as Mode A)
  - [ ] Noise model identical to Mode A (Gaussian + 50/60 Hz + baseline wander)
  - [ ] Graceful fallback: if `template_library.is_available() == False`, warn and use Mode A
  - [ ] Mode A behavior unchanged (no regression)
  - [ ] `synthesize_strip()` works identically for both modes (same output format)
  - [ ] Configurable via settings: `CIED_SIM_GENERATOR__EGM_MODE=opencarp`

**T-136: Add EGM mode to simulation config and CLI**
- Priority: P1 | Size: S | Depends: T-135, T-009
- Description: Add `egm_mode` field to `SimulationConfig` and `GeneratorSettings`. Expose in CLI demo script (`scripts/demo.py --egm-mode opencarp`), API simulation start endpoint, and scenario YAML definitions. Default: `parametric` (Mode A). If set to `opencarp` and templates missing, warn and fall back.
- Acceptance Criteria:
  - [ ] `SimulationConfig.egm_mode: str = "parametric"` added
  - [ ] `GeneratorSettings.egm_mode` loaded from env `CIED_SIM_GENERATOR__EGM_MODE`
  - [ ] `scripts/demo.py --egm-mode opencarp` flag works
  - [ ] API: `POST /api/v1/simulation/start` body accepts `egm_mode`
  - [ ] Scenario YAML: `egm_mode: opencarp` field supported
  - [ ] Fallback warning logged when templates unavailable

---

### S-37: openCARP Validation & Testing

**T-137: Unit tests for template library**
- Priority: P1 | Size: M | Depends: T-133
- Description: Test template loading, beat selection, variation injection, and memory-mapped access. Use small synthetic test templates (not full openCARP output) for unit test speed.
- Acceptance Criteria:
  - [ ] Test: `is_available()` returns False when templates dir is empty
  - [ ] Test: `load_catalog()` parses JSON manifest correctly
  - [ ] Test: `get_beat()` returns numpy array with correct shape
  - [ ] Test: `get_beat_multichannel()` returns time-aligned arrays for all channels
  - [ ] Test: beat-to-beat variation produces different output on repeated calls
  - [ ] Test: amplitude variation within ±5% of template baseline
  - [ ] Test: duration variation within ±3% of template baseline
  - [ ] Tests use synthetic fixture templates (not real openCARP output)

**T-138: Unit tests for ionic adapter**
- Priority: P1 | Size: M | Depends: T-134
- Description: Test sample rate conversion, amplitude scaling, time-stretching, and channel mapping with known inputs and expected outputs.
- Acceptance Criteria:
  - [ ] Test: 1000 Hz → 256 Hz resampling preserves waveform shape (cross-correlation > 0.95)
  - [ ] Test: time-stretch to target RR interval produces correct output length
  - [ ] Test: amplitude scaling to ADC range (0-4095) for 12-bit quantization
  - [ ] Test: channel gains applied correctly (near-field 1.0x, far-field 0.3x)
  - [ ] Test: multichannel adaptation produces time-aligned outputs

**T-139: Integration test — Mode A vs Mode B output comparison**
- Priority: P1 | Size: L | Depends: T-135
- Description: Run identical simulation (same patient, same random seed, same 30-day scenario) in Mode A and Mode B. Verify: same number of events generated, same alert triggers, same transmission packets, same burn events. The ONLY difference should be the EGM waveform morphology — all architecture comparison metrics should be functionally identical.
- Acceptance Criteria:
  - [ ] Same patient, same seed → same rhythm state sequence in both modes
  - [ ] Same number of episodes, alerts, and transmissions
  - [ ] Same burn event count and timing
  - [ ] Same persistence volume comparison (data sizes may differ slightly due to waveform encoding)
  - [ ] EGM waveforms are morphologically different between modes (not identical)
  - [ ] Architecture comparison report produces equivalent conclusions from both modes

**T-140: Spectral validation of openCARP templates**
- Priority: P2 | Size: M | Depends: T-131
- Description: Automated validation of generated templates. For each rhythm type, verify that: dominant frequency matches expected range (NSR: 1-1.7 Hz, AF atrial: 4-9 Hz, VT: 2-4 Hz, VF: 3-7 Hz), QRS duration is within physiological bounds (narrow: 80-120ms, wide: 120-200ms), P-wave amplitude is within expected range for atrial channel (0.5-5 mV), and signal-to-noise ratio of raw templates is > 20 dB.
- Acceptance Criteria:
  - [ ] Spectral analysis for all 18 rhythm types
  - [ ] Dominant frequency within expected range per rhythm
  - [ ] QRS duration within physiological bounds
  - [ ] P-wave amplitude within expected range (atrial channel)
  - [ ] Template SNR > 20 dB (before noise injection)
  - [ ] Automated validation script: `make validate-templates`
  - [ ] Validation report generated as JSON

**T-141: Create openCARP template generation CI job**
- Priority: P2 | Size: M | Depends: T-131, T-013
- Description: GitHub Actions workflow that generates templates on demand (manual trigger or scheduled weekly). Uses openCARP Docker image. Caches generated templates as CI artifacts. Runs validation tests on generated templates. Publishes template library as downloadable release artifact.
- Acceptance Criteria:
  - [ ] Workflow: `.github/workflows/generate-templates.yml`
  - [ ] Trigger: manual dispatch + weekly schedule
  - [ ] Uses `opencarp/opencarp:latest` Docker image
  - [ ] Generates full template library
  - [ ] Runs spectral validation (T-140)
  - [ ] Uploads templates as GitHub release artifact
  - [ ] Users can download pre-generated templates instead of running openCARP locally

---

### S-38: Documentation & Developer Experience

**T-142: Write openCARP integration documentation**
- Priority: P1 | Size: M | Depends: T-135
- Description: Add openCARP section to project README and create `docs/opencarp-integration.md`. Cover: what openCARP is and why it's used, macOS setup (Docker and native), generating templates, using Mode B, validation, and troubleshooting. Include architecture decision record documenting why openCARP was chosen over IDHP, VHM, and CVSim.
- Acceptance Criteria:
  - [ ] README section: "Biophysical EGM Mode (openCARP)"
  - [ ] `docs/opencarp-integration.md` with full setup guide
  - [ ] macOS Docker setup: 5-command quickstart
  - [ ] macOS native build: Homebrew instructions
  - [ ] Template generation instructions
  - [ ] Mode B usage examples (CLI, API, scenario YAML)
  - [ ] ADR: "Why openCARP over IDHP/VHM/CVSim"
  - [ ] Troubleshooting: Docker memory, native build failures, template validation errors

**T-143: Add pre-built template download mechanism**
- Priority: P2 | Size: M | Depends: T-141
- Description: Since openCARP template generation takes 2-4 hours, provide a mechanism to download pre-generated templates. Add `make download-templates` target that fetches the latest template library from GitHub Releases. Include checksum verification. Templates are version-tagged to match simulator version.
- Acceptance Criteria:
  - [ ] `make download-templates` fetches pre-built templates from GitHub Releases
  - [ ] SHA-256 checksum verification after download
  - [ ] Templates extracted to `src/generator/cardiac/opencarp/templates/`
  - [ ] Version check: warns if template version doesn't match simulator version
  - [ ] Download size: ~50-200 MB compressed
  - [ ] Works offline after initial download (no runtime network dependency)

---

# Dependency Graph (Critical Path)

```
T-001 → T-002 → T-009 → T-010, T-011
                       ↓
T-015 → T-016, T-017 → T-019 → T-020, T-021, T-022 → T-023
     ↓                                                    ↓
T-024 → T-025 → T-026                               T-039 (stream)
     ↓         ↓                                      ↓         ↓
T-028, T-029  T-035 → T-036, T-037                 T-043     T-047
                              ↓                     (cloud)   (relay)
                         T-038 → T-039                ↓         ↓
                                   ↓               T-044     T-051
                              T-040 (cohort)       T-045     T-052
                                   ↓                 ↓      T-054
                              T-043, T-047          T-046   T-055
                                   ↓                  ↓       ↓
                              T-065, T-066       T-068, T-069
                                   ↓                  ↓
                              T-070 (comparator)←─────┘
                                   ↓
                              T-071 → T-072, T-074, T-076
                                   ↓
                              T-082 → T-083, T-084
                                   ↓
                              T-104-T-112 (scenarios)
                                   ↓
                              T-113 (demo)
```

**Critical path (core):** T-001 → T-002 → T-009 → T-015 → T-017 → T-019 → T-023 → T-039 → T-047 → T-051/T-055 → T-070 → T-104 → T-113

**openCARP path (parallel):** T-005 → T-128 → T-129 → T-130 → T-131 → T-133 → T-135 → T-139
                                                                          ↓
                                                               T-132 (segmentation)
                                                                          ↓
                                                               T-134 (adapter) → T-135

**Estimated critical path duration:** ~15-20 working days with a single developer, ~7-10 days with 2-3 developers working in parallel on independent modules.

**openCARP addition:** +5-7 working days (can run in parallel with Milestones 3-7; only T-135 depends on the core EGM synthesizer T-023).

---

# Issue Summary

| Epic | Description | Issues | P0 | P1 | P2 | P3 |
|------|-------------|--------|----|----|----|----|
| E-01 | Project Scaffolding | 14 | 5 | 7 | 1 | 1 |
| E-02 | Telemetry Generator | 26 | 16 | 8 | 2 | 0 |
| E-03 | Current Architecture | 6 | 5 | 1 | 0 | 0 |
| E-04 | Chambers Architecture | 18 | 11 | 7 | 0 | 0 |
| E-05 | Analytics Engine | 6 | 4 | 2 | 0 | 0 |
| E-06 | Visualization | 14 | 0 | 10 | 4 | 0 |
| E-07 | API Layer | 6 | 0 | 5 | 1 | 0 |
| E-08 | Testing | 13 | 8 | 3 | 0 | 2 |
| E-09 | Scenarios & Demo | 12 | 4 | 5 | 2 | 1 |
| E-10 | Performance | 4 | 1 | 2 | 1 | 0 |
| E-11 | Extended Features | 8 | 0 | 0 | 2 | 6 |
| E-12 | openCARP Integration | 16 | 2 | 9 | 3 | 2 |
| **Total** | | **143** | **56** | **59** | **16** | **12** |

---

# Milestone Plan

### Milestone 1: Foundation (Issues: T-001 through T-014)
- Repository, build system, Docker, CI/CD
- All infrastructure operational
- **Exit criteria:** `docker compose up` starts all services; CI pipeline passes

### Milestone 2: Signal Generation (Issues: T-015 through T-040)
- Complete telemetry generator with all engines
- Unified event stream producing realistic CIED telemetry
- **Exit criteria:** 1 patient, 365 days of plausible telemetry generated in < 10 seconds

### Milestone 3: Dual Architecture (Issues: T-041 through T-064)
- Current architecture simulation (5 layers)
- Chambers architecture simulation (5 worlds + relay + burn)
- **Exit criteria:** same event stream flows through both architectures; burn executes in Chambers; data persists in current

### Milestone 4: Analytics & Comparison (Issues: T-065 through T-070)
- All comparison metrics implemented
- Architecture comparator producing structured results
- **Exit criteria:** comparison report data generated for baseline scenario

### Milestone 5: Visualization & Reporting (Issues: T-071 through T-084)
- Interactive dashboard operational
- Report generation working
- **Exit criteria:** dashboard shows real-time simulation; PDF report generates

### Milestone 6: API & Integration (Issues: T-085 through T-103)
- REST API and WebSocket endpoints
- Full test suite passing
- **Exit criteria:** all API endpoints functional; test coverage > 80%

### Milestone 7: Scenarios & Launch (Issues: T-104 through T-115)
- All pre-built scenarios runnable
- Demo script working
- Documentation complete
- **Exit criteria:** `python scripts/demo.py` runs end-to-end; README complete; all P0 scenarios produce reports

### Milestone 8: openCARP Integration (Issues: T-128 through T-143)
- openCARP Docker environment operational on macOS
- Template library generated for all 18 rhythm types
- Mode B integrated into EGM synthesizer with graceful fallback
- Validation tests passing
- **Exit criteria:** `make generate-templates-docker` produces full template library; simulation runs in both Mode A and Mode B with identical architecture comparison results; spectral validation passes for all rhythm types

---

*End of Issue List*
