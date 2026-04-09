# Chamber Sentinel — CIED Telemetry Simulator

A simulation platform that empirically compares two data architecture models for cardiac implantable electronic devices (CIEDs — pacemakers, ICDs, CRT-Ds):

- **Current Architecture** — persist-by-default, as used by manufacturers (Medtronic, Boston Scientific, Abbott, Biotronik). Patient cardiac data flows through five layers and accumulates indefinitely in manufacturer clouds.
- **Chambers Architecture** — burn-by-default. Data is destroyed by default; persistence must be explicitly justified per-world policy.

The simulator routes identical synthetic telemetry through both architectures simultaneously and quantifies differences in data volume, persistence windows, attack surface, and clinical availability.

## Key Findings (365-day simulation, single patient)

| Metric | Current Arch | Chambers Arch |
|--------|-------------|---------------|
| Data persisted (cloud) | 249 MB (linear growth) | Bounded steady-state |
| Monthly growth rate | 0.68 MB/day | 0 (TTL expiration) |
| Alert delivery success | ~99% | 100% (with 72h TTL) |
| Burn events | 0 | 413,148 |

Extrapolated to a 100,000-patient installed base: the current architecture accumulates ~24 TB/year indefinitely; Chambers maintains bounded memory with deterministic destruction.

## Architecture Overview

The simulator is organized into six modules:

1. **Synthetic Telemetry Generator** — 18-state Markov chain cardiac rhythm engine, device pacing state machines (VVI/DDD/CRT-D), lead impedance modeling, battery depletion, and EGM waveform synthesis (parametric or openCARP-based)
2. **Current Architecture Simulation** — models five layers: on-device storage, transmitter, manufacturer cloud, clinician portal, and aggregate analytics pool
3. **Chambers Architecture Simulation** — five "worlds" (Clinical, Device Maintenance, Research, Patient, Safety Investigation) with a stateless relay processor enforcing 72-hour TTL and cryptographic deletion
4. **Comparative Analytics Engine** — persistence volume tracking, attack surface calculation, clinical availability monitoring, adverse event impact analysis, and regulatory compliance scoring (GDPR/HIPAA/MDR)
5. **Visualization & Reporting** — Plotly Dash real-time dashboard, Mermaid.js flow diagrams, PDF/HTML report generation, FHIR R4 export
6. **openCARP Integration** — optional biophysical EGM fidelity via pre-computed ionic model templates (ten Tusscher 2006, O'Hara-Rudy 2011, Courtemanche 1998)

### Patient Cohort

10 predefined patient archetypes (P-001 through P-010), ranging from a 28-year-old athlete with congenital heart block to an 88-year-old with HF/CKD/diabetes/AF. Supports scaling to 1–10,000 virtual patients with configurable demographic distributions.

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.12+ |
| Signal generation | NumPy, SciPy, neurokit2 |
| Biophysical EGMs | openCARP (optional, offline) |
| Event streaming | Apache Kafka (or in-process queue) |
| Time-series DB | PostgreSQL + TimescaleDB |
| Cache / burn scheduling | Redis + Celery |
| API | FastAPI (async) |
| Dashboard | Plotly Dash + D3.js |
| Containerization | Docker + Docker Compose |
| CI/CD | GitHub Actions |

## Quick Start

### Option 1: Local

```bash
# Install
make install        # core dependencies
make dev            # all dependencies (viz + dev + test)

# Run simulation
python scripts/demo.py 30 1          # 30 days, 1 patient
python scripts/demo.py 365 100       # 365 days, 100 patients

# Start API + dashboard
make run-api        # FastAPI on http://localhost:8000
                    # Dashboard on http://localhost:8050
```

### Option 2: Docker Compose

```bash
make docker-up      # starts postgres, redis, simulator, dashboard, api, celery worker
make docker-down    # shutdown
```

Services:
- API: http://localhost:8000 (OpenAPI docs at `/docs`)
- Dashboard: http://localhost:8050
- Postgres: localhost:5432
- Redis: localhost:6379

### Option 3: With openCARP (biophysical EGMs)

```bash
make generate-templates       # generate templates (synthetic fallback)
make validate-templates       # verify integrity
python scripts/demo.py 30 1 --egm-mode opencarp
```

## Project Structure

```
chamber-sentinel-cied-sim/
├── src/
│   ├── orchestrator.py              # Main simulation orchestrator
│   ├── api/                         # FastAPI app, routes, WebSocket streaming
│   ├── generator/
│   │   ├── cardiac/                 # Rhythm engine, EGM synthesis, conduction
│   │   ├── device/                  # Pacing, sensing, battery, lead models
│   │   ├── episodes/                # Arrhythmia episode generation
│   │   └── patient/                 # Patient profile instantiation
│   ├── current_arch/                # Five-layer persist-by-default simulation
│   ├── chambers_arch/
│   │   ├── worlds/                  # Clinical, maintenance, research, patient, safety
│   │   ├── relay/                   # Stateless TTL relay processor
│   │   ├── burn/                    # Scheduler, verifier, hold manager
│   │   └── portable_record/         # FHIR R4 export
│   ├── analytics/                   # Persistence, attack surface, compliance metrics
│   ├── visualization/               # Dash app, flow diagrams, report templates
│   └── config/                      # Settings, patient/device profiles, scenarios
├── tests/                           # Unit, integration, property-based (Hypothesis)
├── scripts/                         # Demo CLI, template validation
├── docs/                            # API docs, architecture diagrams, openCARP guide
├── scenarios/                       # YAML scenario definitions
├── Dockerfile
├── docker-compose.yml
├── Makefile
└── pyproject.toml
```

## Configuration

All settings are overridable via environment variables (prefixed `CIED_SIM_`) or YAML configs in `src/config/`.

```bash
CIED_SIM_SIMULATION__CLOCK_SPEED=3600          # simulated seconds per wall-clock second
CIED_SIM_SIMULATION__DURATION_DAYS=365
CIED_SIM_GENERATOR__EGM_MODE=parametric        # or "opencarp"
CIED_SIM_GENERATOR__SAMPLE_RATE_HZ=256
CIED_SIM_CHAMBERS__RELAY_TTL_S=259200           # 72 hours
CIED_SIM_CHAMBERS__DEVICE_MAINT_WINDOW_DAYS=90
DATABASE_URL=postgresql://user:pass@localhost:5432/cied_sim
REDIS_URL=redis://localhost:6379/0
```

## Development

```bash
make test           # run tests with coverage
make test-cov       # HTML coverage report
make lint           # ruff + mypy
make format         # auto-fix formatting
```

## Documentation

- **[PRD](PRD_CIED_TELEMETRY_SIMULATOR.md)** — full product specification (6 modules, data models, API specs, deployment architecture)
- **[Position Paper](chamber_sentinel_medical_devices_v4.md)** — methodology, simulation findings, regulatory analysis, and limitations
- **[Issue Tracker](ISSUES_CIED_TELEMETRY_SIMULATOR.md)** — 143 issues across 6 epics with priorities and sizing
- **[openCARP Integration](chamber-sentinel-cied-sim/docs/opencarp-integration.md)** — setup guide for biophysical EGM generation

## Limitations

- Research-grade proof-of-concept, not clinical or FDA-approvable
- Synthetic telemetry is physiologically plausible but not clinical-grade
- No real BLE/RF device protocols or manufacturer API integrations
- Open-loop: pacing engine reacts to rhythm engine but not vice versa
- Not validated against real patient data

## License

MIT
