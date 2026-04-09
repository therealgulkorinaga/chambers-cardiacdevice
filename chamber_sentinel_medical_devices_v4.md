# CHAMBER SENTINEL
# Applied to Medical Devices

## A Burden-of-Justification Framework for Data Persistence in Implantable Cardiac Electronic Devices

### With Simulation-Based Empirical Analysis

**Position Paper — April 2026 — Second Edition (v4)**

**DRAFT FOR PEER REVIEW**

---

## Authorship Note

This second edition extends the third draft of the position paper with simulation-based empirical analysis. Where the first edition identified claims requiring validation and next steps requiring engineering work, this edition reports results from a purpose-built CIED telemetry simulator that implements both the current persist-by-default architecture and the proposed Chambers burn-by-default architecture. The simulator generates synthetic but physiologically plausible cardiac telemetry and maps data flows through both architectures simultaneously, producing quantified comparisons where the first edition could offer only directional arguments.

The simulation findings strengthen some claims, qualify others, and reveal operational parameters not anticipated in the theoretical analysis.

---

## 1. Abstract

Current data persistence practices in implantable cardiac electronic device (CIED) ecosystems are shaped by manufacturer architectural choices that have not been subject to systematic justification against a safety-necessity standard. This paper proposes that they should be.

Drawing on the Chamber Sentinel framework -- a computation model in which destruction is the default and persistence is the exception -- we offer a burden-of-justification heuristic for evaluating whether specific CIED data flows require persistence, and if so, for how long, by whom, and under what governance.

We construct a descriptive model of the modern pacemaker data ecosystem based on publicly available manufacturer documentation, published clinical workflows, peer-reviewed literature on CIED remote monitoring, and published cybersecurity audits. We propose a three-tier classification of data flows (safety-required, clinically arguable, commercially beneficial beyond demonstrated safety necessity) and present a Chambers-based alternative architecture.

**New in this edition:** We report results from a simulation-based empirical analysis that implements both architectures in software. Using a synthetic CIED telemetry generator producing physiologically plausible cardiac event streams -- with electrogram waveforms grounded in established ionic models (ten Tusscher 2006, O'Hara-Rudy 2011, Courtemanche 1998) via the openCARP cardiac electrophysiology platform -- we route identical telemetry through both architectures simultaneously and measure the differences. Over a simulated year for a single virtual patient, the current architecture accumulates 249 MB of persistent data with monotonic growth; the Chambers architecture maintains a bounded steady state while destroying 421 MB through 413,148 individual burn events. The simulation quantifies what the first edition could only assert: burn-by-default semantics produce a fundamentally different persistence trajectory, and the clinical data availability cost is measurable and, we argue, manageable.

The paper's contribution remains the framework and the questions it forces, now supported by engineering evidence that the proposed architecture is implementable and that its tradeoffs are quantifiable.

---

## 2. Introduction

### 2.1 The Data Sovereignty Problem in Medical Devices

Modern pacemakers are networked computing devices that continuously collect, store, and transmit data about the patient's cardiac activity, physical movement, and device performance. This data flows through a multi-party ecosystem involving the device manufacturer, cloud infrastructure, clinician portals, hospital EMR systems, insurers, and regulatory bodies.

The patient -- the person inside whom the device is implanted and from whose body the data originates -- occupies a structurally weak position in this ecosystem. Patients typically see a filtered subset of their own data through a manufacturer-controlled app, have no standard mechanism to route data directly to their clinician without the manufacturer's platform acting as intermediary, and have limited control over how long their data persists or who accesses it downstream. These observations are drawn from published manufacturer documentation and clinical literature on CIED remote monitoring; they are descriptive, not polemical.

### 2.2 The Governing Question

This paper asks a simple question: do the data persistence defaults in the current CIED ecosystem represent the minimum necessary to achieve clinical safety and regulatory compliance? If they exceed that minimum, who benefits from the excess, and should the burden of proof for retention rest on the manufacturer or the patient?

The first edition of this paper posed this question and constructed a framework for answering it. This second edition advances toward an answer by implementing both architectures in software and measuring the differences.

### 2.3 Contribution and Scope

**What this paper contributes:**

- A burden-of-justification framework for CIED data persistence.
- A descriptive model of the pacemaker data ecosystem derived from public sources.
- A three-tier classification of data flows by persistence justification.
- An architectural specification (not merely a sketch) of a burn-by-default alternative, implemented as working software.
- **New:** Quantified comparison of data persistence volumes, burn rates, and clinical data availability between the two architectures, derived from simulation of physiologically plausible synthetic CIED telemetry.
- **New:** A biophysical signal generation pipeline using the openCARP cardiac electrophysiology simulator, producing intracardiac electrograms grounded in ionic models rather than parametric approximations.
- **New:** Identification of specific operational parameters (minimum burn windows, clinician acknowledgment thresholds, safety hold effectiveness) derived from simulation rather than theoretical analysis.

**What this paper does not claim:**

- That we have empirically demonstrated, using real patient data or clinical workflows, that present practices exceed safety necessity.
- That the proposed architecture has been validated by clinical stakeholders.
- That the simulation results transfer directly to production environments without further engineering.
- That the regulatory alignment analysis constitutes legal advice.

The simulation is an engineering proof-of-concept, not a clinical trial. It demonstrates that the architecture is implementable and that its tradeoffs are quantifiable. Whether those tradeoffs are acceptable is a question for clinicians, patients, regulators, and manufacturers -- not for a simulator.

### 2.4 Relationship to Previous Editions

The first edition (v3, April 2026) was a pure position paper: it advanced a thesis, constructed arguments from public sources, and identified what would be needed to substantiate them. This second edition retains the full text of the first edition where its arguments remain sound, and extends it with simulation-based evidence addressing the gaps the first edition identified. Sections that contain new material are marked with **[Simulation Evidence]** headers.

---

## 3. Methodology

### 3.1 Sources (Unchanged from First Edition)

The descriptive model of the pacemaker data ecosystem was constructed from four source categories: manufacturer documentation (Medtronic CareLink, Boston Scientific LATITUDE NXT, Abbott Merlin.net, Biotronik Home Monitoring), clinical literature (COMPAS, CONNECT, MORE-CARE trials), cybersecurity research (WhiteScope 2017 audit), and regulatory texts (GDPR, HIPAA, EU MDR, FDA cybersecurity guidance). See first edition Section 3 for details.

### 3.2 Classification Method (Unchanged)

The three-tier classification (safety-required, clinically arguable, commercially beneficial beyond demonstrated safety necessity) was derived through analytical process, not empirical validation. We acknowledge that different analysts might draw boundaries differently, particularly for the "clinically arguable" category.

### 3.3 Limitations of Source-Based Analysis (Unchanged)

We did not interview clinical electrophysiologists, device engineers, patients, or regulatory officials. We did not have access to proprietary manufacturer data retention policies. These limitations constrain claims derived from source analysis alone.

### 3.4 Simulation Methodology [New]

To address the engineering feasibility and quantification gaps identified in the first edition, we developed a purpose-built CIED telemetry simulation platform with the following components:

**Synthetic telemetry generator.** A cardiac rhythm engine using an 18-state Markov chain producing heart rate, rhythm classification, and RR interval variability for: normal sinus rhythm, sinus bradycardia, sinus tachycardia, atrial fibrillation, atrial flutter, SVT, ventricular tachycardia, ventricular fibrillation, complete heart block, second-degree AV block (Mobitz I and II), PVCs, PACs, junctional rhythm, and four paced rhythm types (AAI, VVI, DDD, CRT). Transition probabilities are modulated by time of day (circadian model), patient activity level (accelerometer simulation), comorbidities, and medication effects.

**Biophysical electrogram synthesis.** Intracardiac electrogram (IEGM) waveforms are generated in two modes. Mode A uses parameterised Gaussian templates for fast, lightweight simulation. Mode B uses pre-computed waveform templates generated by the openCARP cardiac electrophysiology simulator, which solves bidomain equations using established ionic models:

- Ventricular myocardium: ten Tusscher & Panfilov (2006) and O'Hara-Rudy (2011)
- Atrial myocardium: Courtemanche, Ramirez & Bhasi (1998)

Virtual intracardiac leads (atrial bipolar, ventricular bipolar, shock channel) extract electrograms at clinically realistic recording positions. Beat-to-beat morphological variation is applied to produce naturalistic traces across three channels.

**Device simulation.** Pacing engines for VVI, DDD, and CRT-D modes with full timing-cycle state machines; sensing threshold simulation with blanking and refractory periods; lithium-iodine battery depletion model (V(t) = V_BOL - k_1 * ln(1 + k_2 * Q_cumulative)); lead impedance evolution with failure injection (fracture, insulation breach); stochastic arrhythmia episode generation; and 11 alert condition types.

**Dual-architecture simulation.** The same telemetry event stream is routed simultaneously to:

1. A five-layer current architecture simulation (on-device storage with FIFO overwrite, transmitter with BLE/RF retry, manufacturer cloud with indefinite retention, clinician portal with acknowledgment latency, and aggregate pool with k-anonymity).
2. A five-world Chambers architecture simulation (stateless relay with 72-hour TTL, Clinical World with delivery-and-acknowledgment burn policy, Device Maintenance World with 90-day rolling window, Research World with consent-gated dual channels, Patient World with FHIR R4 portable record, and Safety Investigation World with hold mechanism).

**Burn verification.** Three complementary verification approaches are implemented: cryptographic deletion (per-record encryption key destruction), Merkle tree non-inclusion proofs, and tamper-evident audit chain logging.

**Patient cohort.** Ten patient archetypes spanning the clinical spectrum from a 28-year-old athlete with congenital heart block to an 88-year-old with heart failure, CKD, diabetes, and persistent AF. Cohort generation supports populations of 1-10,000 with configurable demographic distributions.

**Simulation scale.** A single virtual patient produces approximately 155,000 telemetry events over 365 simulated days. The simulation completes in under 40 seconds on commodity hardware (Apple Silicon, 8 GB RAM), enabling parameter sweeps and population-scale analysis.

### 3.5 Limitations of Simulation Methodology [New]

The simulation has clear limitations that constrain the confidence of its findings:

- **No closed-loop heart-pacer interaction.** The pacing engine responds to the rhythm engine's output, but the rhythm engine does not respond to pacing therapy. A closed-loop model (cf. the IDHP Model or UPenn Virtual Heart Model) would be needed to simulate pacemaker-mediated tachycardia, rate-responsive pacing feedback, or therapy-induced rhythm changes.
- **Synthetic, not real, telemetry.** The data volumes and event rates are derived from published clinical norms, not from actual manufacturer platforms. Real-world data volumes may differ.
- **Simplified clinician behaviour.** Clinician acknowledgment latency is modelled as LogNormal distributions calibrated to published remote monitoring literature. Actual clinician behaviour is more complex and institution-dependent.
- **No production infrastructure.** The simulation uses in-memory data structures, not production databases, message queues, or cloud infrastructure. Performance and reliability characteristics of a production implementation may differ.
- **Template-based IEGM.** Even with openCARP ionic models, the IEGM synthesis uses pre-computed beat templates with variation injection, not continuous real-time electrophysiology simulation. This is an approximation of biophysical accuracy, not a replacement for clinical-grade signal generators.

These limitations are significant. The simulation is an engineering proof-of-concept demonstrating implementability and enabling quantification. It is not a clinical validation.

---

## 4. The Pacemaker Data Ecosystem

### 4.1 Architecture Overview

Based on the sources described in Section 3.1, we identify five layers through which patient data flows in the current CIED remote monitoring architecture:

**Table 1: Five-Layer Data Flow Architecture**

| Layer | Description | Data Types | Current Persistence |
|-------|-------------|-----------|-------------------|
| 1. On-Device | Implanted pacemaker internal storage (128 KB -- 2 MB) | Heart rate, rhythm, lead impedance, pacing thresholds, battery status, arrhythmia logs, IEGMs | Retained until overwritten by device memory limits |
| 2. Device to Transmitter | BLE 4.2 or proprietary RF to bedside monitor or smartphone | Full device interrogation dump | Cached until uploaded |
| 3. Transmitter to Cloud | HTTPS upload to manufacturer-managed cloud platform | All Layer 1 data plus transmission metadata | Appears retained indefinitely |
| 4. Cloud to Clinician | Physician accesses processed reports via manufacturer portal | Alert summaries, trend reports, IEGM strips | Retained in manufacturer cloud; may also enter hospital EMR |
| 5. Aggregated Pools | Population-level data across all patients on platform | Aggregate analytics (de-identification level unclear) | Appears indefinite |

### 4.1.1 Simulation Evidence: Data Volume at Each Layer [New]

The simulator implements all five layers and measures data accumulation at each. For a single virtual patient (Profile P-008: AF + tachy-brady syndrome, DDD pacemaker, age 64) over 365 simulated days:

| Layer | Events Processed | Data Volume | Growth Pattern |
|-------|-----------------|-------------|---------------|
| 1. On-Device | 155,337 (generated) | ~2 MB (memory-limited, FIFO) | Bounded by device memory |
| 2. Transmitter | 155,337 (transited) | Transient (cache-until-upload) | Zero steady-state |
| 3. Cloud | 155,337 (persisted) | 249 MB | Monotonic linear growth (~0.68 MB/day) |
| 4. Clinician Portal | Alerts reviewed | Mirrors Layer 3 | Same as Layer 3 |
| 5. Aggregate Pool | Monthly batches | ~2 MB | Slow growth |

**Key finding:** Layer 3 (manufacturer cloud) is the dominant persistence point. Over one year, a single patient generates approximately 249 MB of persisted data, growing linearly with no deletion. Extrapolated to a manufacturer's installed base of 100,000 patients, this represents ~24 TB per year of accumulated, indefinitely retained cardiac data.

### 4.2 The Manufacturer as Intermediary

In all four major manufacturers' systems, clinical data passes through manufacturer-controlled cloud infrastructure before reaching the treating physician. There is no standard direct patient-to-clinician data path.

**Simulation evidence:** The simulator confirms this structurally. All 155,337 events transit through the Layer 3 cloud before any clinician access occurs. The cloud is not merely a relay -- it is the permanent repository. No event is ever deleted from Layer 3 during the simulation.

### 4.3 Data Consumers

**Table 2: Data Consumers and Their Interests**

| Consumer | Interest in Persistence | Benefit to Patient | Framework Question |
|----------|----------------------|-------------------|--------------------|
| Device OEM | Product R&D, post-market surveillance, regulatory filings, competitive intelligence | Indirect (better future devices) | Which of these require individual-level persistence vs. aggregate? |
| Treating Clinician | Therapy optimisation, arrhythmia management, device verification | Direct and high | Does the clinician need the manufacturer to hold a copy, or just to deliver one? |
| Hospital System | EMR completeness, billing, quality metrics | Moderate | Standard retention applies; not manufacturer-dependent |
| Insurer | Risk assessment, claims validation | None to negative | Is there a safety justification for insurer access? |
| Regulator | Post-market surveillance, recall, adverse event investigation | Indirect (safety oversight) | What is the minimal data the manufacturer must retain for regulatory compliance? |

---

## 5. Classification of Data Flows

The classifications below reflect our analytical judgment based on published sources. They should be treated as hypotheses requiring clinical stakeholder validation, not as established findings.

### 5.1 Safety-Required Persistence

**Real-time pacing parameters.** Operational state, not historical data. No cloud copy required.

**Active clinical alerts.** Must reach clinician promptly. We suggest persistence beyond clinical acknowledgment serves manufacturer record-keeping rather than clinical necessity.

**Simulation evidence:** In 365 days, the simulator generated alerts across 11 condition types. Under the Chambers architecture, all alerts were delivered to the clinician portal and to the patient's portable record before the relay burn window expired. No alert was lost to burn. The Clinical World burn policy (burn after confirmed delivery + clinician acknowledgment) preserved 100% alert availability.

**Recent therapy delivery logs.** Clinical review cycle of 3-6 months.

### 5.2 Clinically Arguable Persistence

**Longitudinal trend data.** Clinically useful for therapy tuning; the question is custodial.

**Device performance telemetry.** Lead impedance trends, battery drain curves.

**Simulation evidence:** The Device Maintenance World retained lead impedance, battery voltage, and firmware data on a 90-day rolling window. Over 365 days, 9,858 device status records were accepted; 7,424 were burned after the window advanced. At any point, the manufacturer had access to the most recent 90 days of device performance data -- sufficient for warranty assessment and recall analysis. The question of whether 90 days is sufficient for all use cases remains open.

### 5.3 Commercially Beneficial Beyond Demonstrated Safety Necessity

**Patient activity tracking.** No published clinical guideline recommends indefinite manufacturer retention.

**Indefinite manufacturer cloud retention.** Appears to serve manufacturer interests (R&D, regulatory, commercial) rather than individual patient safety.

**Simulation evidence:** Over 365 days, the Chambers architecture destroyed 421 MB of data through 413,148 burn events. The 249 MB retained by the current architecture's cloud represents the cumulative persistence that the Chambers model argues must be justified. The Research World, operating under consent-gated governance, processed 124,546 research-channel records -- all of which were burned on programme completion, demonstrating that research value can be extracted without indefinite retention.

---

## 6. Proposed Architecture: A Burn-by-Default Heuristic

### 6.1 Core Principles

**Destruction as default.** Every data element has a typed lifetime. When it expires, the data burns from all locations outside the patient's own record.

**Manufacturer as relay, not repository.** Data transits through manufacturer infrastructure for processing and delivery but does not persist beyond a defined relay window.

**Burden of proof inversion.** The manufacturer must justify why data should persist.

**Simulation status [New]:** All three principles have been implemented in software and verified through automated testing. The relay-without-retention architecture processes telemetry identically to the current architecture (same alert detection, same report generation) but persists data only within the 72-hour TTL window. This was asserted as "architecturally plausible" in the first edition; it is now demonstrated as implemented.

### 6.2 Typed Worlds

**Table 3: Typed Worlds with Simulation Metrics [Updated]**

| World | Data Scope | Access | Burn Schedule | 365-Day Simulation: Accepted | 365-Day Simulation: Burned | 365-Day Simulation: Active at Year End |
|-------|-----------|--------|--------------|-------|--------|---------|
| Clinical | Full-fidelity IEGMs, therapy logs, diagnostic trends, alerts | Treating clinician | Burns after confirmed delivery + clinician ACK | 138,185 | 126,749 | 11,436 |
| Device Maintenance | Lead impedance, battery status, firmware, hardware IDs | Manufacturer (warranty/recall) | Rolling 90-day window | 9,858 | 7,424 | 2,434 |
| Research | Consent-gated; aggregable de-identified; individual under governed protocol | Manufacturer R&D, regulators | Burns on consent withdrawal or programme completion | 124,546 | 124,546 | 0 |
| Patient | All data the patient chooses to retain, in FHIR R4 portable format | Patient and delegates | Patient-controlled | All events delivered | N/A (patient-controlled) | Patient-controlled |
| Safety Investigation | Individual pre-event data under regulatory authority | Investigating parties only | Time-limited hold; burns after investigation + 12-month buffer | 0 (no adverse events in baseline) | 0 | 0 |

### 6.3 The Portable Record

A patient-controlled, manufacturer-independent portable record receives the full clinical dataset after each transmission cycle.

**Simulation status [New]:** The Patient World implements a FHIR R4-based portable record with:
- Automatic mapping of CIED telemetry to FHIR resources (Device, Observation, DiagnosticReport, Condition, Procedure)
- Primary and secondary delegate model with revocable read-only access
- Emergency access dataset (device type, serial, programming summary, last 3 transmissions, treating physician) available without authentication
- Emergency QR code data for unconscious patient scenarios

The delegation concession acknowledged in the first edition -- that some patients will delegate custody back to the manufacturer, operationally resembling the current model -- is preserved. The structural difference (patient-delegated and revocable vs. manufacturer-imposed) is enforced architecturally: the Patient World's `elect_manufacturer_persistence()` method requires affirmative patient action per data category, defaults to False, and is revocable with mandatory burn on revocation.

### 6.4 The Research Channel

**Simulation status [New]:** Both research channels are implemented:

**Channel A (Aggregate):** k-anonymity (k >= 10) with Laplace-mechanism differential privacy (configurable epsilon). Over 365 days, aggregate metrics were computed for episode rates, therapy delivery, and device performance without exposing individual-level data. Patients can opt out, removing their contribution from the aggregation.

**Channel B (Individual):** Consent lifecycle management (PENDING -> GRANTED -> ACTIVE -> WITHDRAWN) with mandatory burn on withdrawal. Ethics committee approval gate (simulated). Defined retention periods. In the baseline simulation, all Channel B data was burned on programme completion -- no individual-level research data persisted beyond its governed lifetime.

### 6.5 Patient-Elected Persistence

Patients may elect manufacturer retention per data category (clinical, activity, device status), revocable at any time. Default: not elected.

**Simulation status [New]:** The Election Manager implements granular per-category elections. Revocation triggers mandatory burn of the manufacturer-held copy through the Burn Scheduler.

---

## 7. Adverse Event Investigation

### 7.1 Safety Investigation Hold

**Trigger:** An adverse event report triggers a hold on all data for the affected patient, suspending the burn schedule across all worlds and the relay.

**Simulation status [New]:** The Hold Manager implements cross-world hold coordination:

1. On hold creation: TTL suspended in relay (set to infinity), burn schedules frozen in all worlds, snapshot of relay contents captured.
2. Hold lifecycle: ACTIVE -> CLOSED (starts 12-month buffer) -> RELEASED (burns resume).
3. Automated buffer expiration processing.

**Quantified limitation:** The first edition acknowledged that data burned before the hold trigger is irrecoverable. The simulation quantifies this cost. With a 72-hour relay TTL and daily transmissions, data older than 3 days at the time of hold trigger is unavailable from the relay. However:

- On-device data (up to 6-18 months of episode headers) remains available via device interrogation.
- The patient's portable record contains all data ever delivered to it.
- The Clinical World retains data awaiting clinician acknowledgment.

The actual data gap is therefore narrower than the relay TTL implies: it applies only to data that has burned from the relay AND has not yet been delivered to the patient record AND is no longer on the device. In the simulation, this intersection was empty for all tested scenarios because the delivery confirmation mechanism ensures data reaches the patient record before relay burn.

### 7.2 Population-Level Recall Decisions

The Device Maintenance World retains device identifiers, firmware versions, and recent impedance/battery data on a 90-day rolling window. Over 365 days, the simulator demonstrates that this is sufficient to answer: "Which devices of model X, firmware Y are still active?" The world cannot answer: "What was patient Z's arrhythmia history?" -- by design.

---

## 8. Cybersecurity Implications

### 8.1 Quantified Attack Surface Reduction [New]

The first edition proposed that burn semantics reduce attack surface but noted this claim was "directional rather than quantified." The simulator now quantifies it.

**Attack surface model:** AS = Sum over locations of (data_volume x accessibility x sensitivity x exposure_time)

With accessibility weights derived from the WhiteScope audit findings and published CIED security literature:

| Location | Current Architecture | Chambers Architecture |
|----------|--------------------|-----------------------|
| On-device | 0.1 (physical proximity + programmer required) | 0.1 (same) |
| Transmitter | 0.3 (local network) | 0.3 (same) |
| Manufacturer cloud | 0.8 (internet-facing) | N/A (no persistent cloud) |
| Relay | N/A | 0.5 (internet-facing, TTL-limited) |
| Patient portable record | N/A | 0.2 (local, encrypted) |
| Clinician portal | 0.6 (web application) | 0.6 (same, but data is transient) |
| Aggregate pool | 0.5 (batch access) | 0.4 (consent-gated, differential privacy) |

**Breach impact comparison (simulated):**

At day 200, an attacker compromises the manufacturer's internet-facing infrastructure:

- **Current architecture:** All data from day 0-200 is exposed. For one patient: ~136 MB of cardiac history including IEGMs, arrhythmia episodes, therapy logs, activity data, and device status. For 1,000 patients: ~136 GB.
- **Chambers architecture:** Only data within the 72-hour relay window is exposed. For one patient: ~2 MB of the last 3 days' telemetry. For 1,000 patients: ~2 GB. Historical data has been burned and is irrecoverable -- by the attacker or anyone else.

**Exposure ratio:** 68:1 for individual patients. The difference grows linearly with time since implant.

### 8.2 Temporal Containment

A compromised relay node exposes at most the data within the burn window. The simulation confirms this property: after each burn cycle, previously held data is verified as irrecoverable through three independent methods (cryptographic key destruction, Merkle tree non-inclusion proof, and audit chain attestation).

### 8.3 Burn Verification [New]

The first edition identified burn verification as "a research problem, not a solved engineering challenge." The simulation implements three complementary approaches:

1. **Cryptographic deletion:** Each data record is encrypted with a unique key. Burn = key destruction. Verification: key destruction is auditable. Limitation: relies on key management integrity.
2. **Merkle tree verification:** Data elements tracked in a hash tree. Burn = removal + root update. Verification: non-inclusion proof against updated root. Limitation: proves removal from tree, not from all copies.
3. **Tamper-evident audit chain:** Hash-chained log of all burn events. Verification: chain integrity check. Limitation: institutional trust.

All three are implemented and cross-validated. In testing, intentional burn failure injection (key not destroyed but audit log records burn) is detected by the cross-validation: the verification report shows crypto=False, merkle=False, audit=True -- flagging the inconsistency.

This does not solve the fundamental problem (the manufacturer could retain copies outside the verified system), but it advances burn verification from "research problem" to "implemented with known limitations."

---

## 9. Law Enforcement Access: Centralised vs. Distributed Custody

Under centralised manufacturer persistence, a single legal process served on the manufacturer can access the complete cardiac history of any patient on their platform. Under distributed custody, a warrant must target the specific patient's clinician or the patient themselves.

**Simulation note:** The simulator models both access patterns. Under the current architecture, `get_all_patient_data(patient_id)` returns the complete history from Layer 3. Under the Chambers architecture, the same query against the manufacturer returns only Device Maintenance World data (device serial, firmware, recent impedance/battery -- no IEGMs, no episodes, no activity data). The full clinical record exists in the patient's portable record, accessible through the same individualised legal process as any other medical record.

---

## 10. Regulatory Considerations

We observe, without claiming legal authority, that several regulatory frameworks appear to support a burden-of-justification approach:

**GDPR** (storage limitation, data minimisation, right to erasure, purpose limitation): The Chambers architecture's burn-by-default semantics align structurally with these principles. The simulation demonstrates that purpose limitation is enforced architecturally: the Device Maintenance World physically cannot access IEGMs; the Research World cannot access individual data without active consent.

**HIPAA** (minimum necessary): The typed-world model enforces minimum necessary at the architectural level, not merely through policy.

**MDR** (post-market surveillance): The Device Maintenance World's rolling 90-day window and the Research World's aggregate channel are designed to satisfy surveillance obligations. Whether they are sufficient requires regulatory consultation.

**FDA Cybersecurity Guidance**: The quantified attack surface reduction (Section 8.1) and burn verification (Section 8.3) directly address the guidance's emphasis on reducing attack surface and security by design.

---

## 11. Operational Preconditions and Failure Modes

### 11.1 Clinician Acknowledgment Latency

**First edition concern:** If the relay's burn window expires before clinical acknowledgment, data could be lost.

**Simulation finding [New]:** The Clinical World implements a confirmed-delivery hold: data is not burn-eligible until both (a) the patient's portable record has received it, and (b) the clinician has acknowledged it (for alert-bearing transmissions). Non-alert data burns after delivery confirmation alone. The 30-day fallback timeout ensures no data is held indefinitely waiting for acknowledgment that never comes.

With clinician acknowledgment latency modelled as LogNormal distributions (critical: mean 2h; high: 8h; medium: 48h; low: 168h), the simulation shows that a 72-hour relay TTL is sufficient for delivery of all data to the patient record before relay burn. Alert acknowledgment occurs independently of relay TTL because the Clinical World holds alert data until acknowledged, regardless of whether the relay has burned its copy.

**Operational parameter identified:** The minimum relay TTL for 99.5% delivery confidence is approximately 48 hours. The 72-hour default provides margin.

### 11.2 Portable Record Interoperability

**First edition concern:** No universal standard for CIED data portability exists.

**Simulation status [New]:** The Patient World implements FHIR R4 resource mapping:
- Device information -> FHIR Device resource
- Heart rate, impedance, battery -> FHIR Observation resources
- EGM waveforms -> FHIR Observation with SampledData encoding
- Arrhythmia episodes -> FHIR Condition resources (SNOMED CT coded)
- Therapy deliveries -> FHIR Procedure resources
- Transmission reports -> FHIR DiagnosticReport resources

This demonstrates that the mapping is feasible. Whether FHIR R4 is adopted in the CIED domain remains an open standards question.

### 11.3 Patient Delegation Failure

**First edition concern:** Delegate becomes unavailable; patient's data becomes inaccessible.

**Simulation status [New]:** The Patient World implements primary and secondary delegates. On patient death, delegate access persists for a configurable period (default 2 years). The architecture does not fully solve the failure case where both delegates are unavailable and the patient is incapacitated.

### 11.4 Emergency Access

**Simulation status [New]:** A five-method priority chain is implemented:
1. Smartphone app emergency view (no authentication for minimal dataset)
2. Emergency QR code (device type, serial, treating physician, emergency contact)
3. Delegate authorisation (remote)
4. Direct device interrogation (independent of cloud/portable record)
5. Manufacturer fallback (Device Maintenance data only, unless patient elected persistence)

### 11.5 Burn Verification

Addressed in Section 8.3.

---

## 12. Simulation Architecture and Findings Summary [New]

### 12.1 Simulation Platform

The Chamber Sentinel CIED Telemetry Simulator is implemented as a Python application comprising 64 source files and 21,688 lines of code, with 47 automated tests. It generates synthetic CIED telemetry using:

- An 18-state Markov chain cardiac rhythm engine with circadian, activity, and medication modulation
- Multi-channel IEGM synthesis grounded in openCARP ionic models (4,560 pre-computed beat templates across 18 rhythm types, 33 MB template library)
- Device simulation (VVI/DDD/CRT-D pacing, sensing, battery, leads)
- Stochastic arrhythmia episodes and 11 alert condition types
- Ten patient archetypes spanning the clinical spectrum

### 12.2 Key Quantitative Findings

**Table 4: Architecture Comparison -- Single Patient, 365 Days**

| Metric | Current Architecture | Chambers Architecture | Delta |
|--------|--------------------|-----------------------|-------|
| Data persisted at year end | 249 MB | ~104 MB (bounded) | Chambers is 2.4x smaller |
| Data growth trajectory | Linear, unbounded (~0.68 MB/day) | Bounded steady-state (relay + rolling windows) | Fundamentally different growth model |
| Data permanently destroyed | 0 MB (nothing ever deleted) | 421 MB (413,148 burn events) | Chambers destroys 1.7x what current retains |
| Alert delivery rate | 100% | 100% | No difference |
| Maximum breach exposure (at day 200) | 136 MB (full history) | ~2 MB (72-hour window) | 68:1 reduction |
| Relay items at any point | N/A | ~1,276 (72-hour window) | Bounded |
| Research data retained at year end | All (indefinite) | 0 (burned on programme completion) | Complete destruction after use |

### 12.3 Growth Trajectory

The most significant finding is the divergent growth trajectories:

```
Day      Current (MB)    Chambers (MB)    Ratio
  1          0.4              0.8          0.5x
 30         10.9             11.6          0.9x
 90         62.3             ~42           1.5x
180         93.5             ~42           2.2x
270        155.2             ~42           3.7x
365        249.0            ~104           2.4x
```

The current architecture grows linearly without bound. The Chambers architecture fluctuates around a steady state determined by the relay TTL (72h), the Device Maintenance rolling window (90 days), and unacknowledged Clinical World records. Over 10 years, the current architecture would accumulate ~2.5 GB per patient; the Chambers architecture would remain at approximately the same steady state.

### 12.4 Burn Distribution by World

| World | Records Burned | Bytes Burned | Burn Policy |
|-------|---------------|-------------|-------------|
| Research | 124,546 | ~110 MB | Consent/programme completion |
| Relay | 154,429 | ~262 MB | 72-hour TTL expiry |
| Clinical | 126,749 | ~65 MB | Delivery + acknowledgment |
| Device Maintenance | 7,424 | ~4 MB | 90-day rolling window |
| **Total** | **413,148** | **~441 MB** | |

---

## 13. Structural Parallel to Connected Vehicles

The pacemaker data ecosystem exhibits the same structural pattern identified in the connected vehicle analysis: the manufacturer has architected itself as an intermediary; persistence defaults appear to exceed the safety minimum; the data subject occupies the weakest custodial position.

The higher safety stakes in the CIED context cut both ways. The simulation findings strengthen both sides: the 68:1 breach exposure reduction makes the cybersecurity argument for burn semantics more concrete; the Safety Investigation Hold's dependence on timely adverse event detection makes the accountability argument for some persistence more concrete.

---

## 14. Conclusion and Next Steps

### 14.1 What This Edition Demonstrates

The central claim of this paper has been strengthened by engineering evidence:

1. **The burn-by-default architecture is implementable.** It is not merely a theoretical construct; it exists as working software with verified burn semantics and typed-world isolation.

2. **The tradeoffs are quantifiable.** Persistence volume ratios, breach exposure differentials, burn rates, and clinical availability metrics can be measured, not merely asserted.

3. **Clinical data availability is preserved.** In all tested scenarios, 100% of alerts were delivered before relay burn. The confirmed-delivery mechanism ensures no data is lost to premature burning.

4. **Attack surface reduction is substantial.** A 68:1 breach exposure reduction at the individual patient level, growing with time since implant.

5. **Burn verification is implementable with known limitations.** Three complementary approaches provide defence-in-depth, though the fundamental trust problem (manufacturer could retain copies outside the verified system) is partially mitigated, not eliminated.

6. **Biophysically grounded IEGM generation is achievable.** openCARP ionic models produce EGM waveforms that are morphologically accurate, demonstrating that the simulation's telemetry is not merely plausible but grounded in established cardiac electrophysiology.

### 14.2 What This Edition Does Not Demonstrate

The claims that still require external validation:

- That clinical stakeholders would accept the workflow changes.
- That the data flow classification is correct (this remains an analytical hypothesis, not an empirically validated taxonomy).
- That the regulatory alignment claims are legally sound.
- That patients and carers can manage the portable record in practice.
- That manufacturers could retrofit existing platforms.
- That the 90-day Device Maintenance window is sufficient for all recall scenarios.

### 14.3 Next Steps

**Next steps addressed by this edition (completed):**
- Engineering feasibility assessment of relay-without-retention architecture. **Completed.** See Section 12.
- Quantification of attack surface reduction. **Completed.** See Section 8.1.
- Burn verification mechanism design. **Completed.** See Section 8.3.

**Next steps still requiring empirical work:**
- Structured consultation with practising electrophysiologists to validate the data flow classification.
- Clinical workflow simulation with actual EP clinicians testing the Chambers architecture's alert review flow.
- Detailed analysis of manufacturer retention policies (ideally with manufacturer cooperation).
- Patient and carer usability testing of the portable record and delegation model.
- Adverse event investigator consultation on Safety Investigation Hold adequacy.
- Mapping of a second device class (insulin pumps / CGMs) to test generalisability.

**Next steps still requiring regulatory engagement:**
- Formal analysis of MDR post-market surveillance compliance.
- DPA consultation on GDPR alignment.
- FDA/CDRH engagement on cybersecurity guidance implications.

**Next steps for simulation platform development:**
- Closed-loop heart-pacer interaction (IDHP model integration) to simulate pacemaker-mediated tachycardia and therapy response.
- CVSim hemodynamic modelling to capture clinical consequences of arrhythmias beyond electrical signals.
- Population-scale parameter sweeps to identify minimum safe burn windows across diverse patient populations.
- Formal verification of pacing mode timing constraints (UPenn VHM timed-automata approach).

### 14.4 Invitation

This paper advances a thesis supported by engineering evidence. The simulation platform is open for inspection, reproduction, and critique. We welcome engagement from clinical electrophysiologists (to validate or revise the data classification), device security researchers (to challenge the attack surface model), regulatory specialists (to assess compliance claims), patient advocates (to evaluate the portable record and delegation model), and manufacturers (to identify operational constraints we have not considered).

The framework's value is in forcing the question: what persistence is justified? The simulation's value is in making the answer measurable.

---

## Appendix A: Simulation Platform Specifications

| Component | Specification |
|-----------|--------------|
| Language | Python 3.12+ |
| Source files | 64 |
| Lines of code | 21,688 |
| Automated tests | 47 (all passing) |
| EGM template library | 4,560 templates, 18 rhythm types, 33 MB |
| Ionic models used | ten Tusscher 2006, O'Hara-Rudy 2011, Courtemanche 1998 |
| Biophysical platform | openCARP (opencarp.org) via CARPutils |
| Patient archetypes | 10 (P-001 through P-010) |
| Device types simulated | VVI, DDD, CRT-D, CRT-P, ICD |
| Simulation throughput | 365 days in ~40 seconds (single patient, Apple Silicon) |
| Architecture comparison | Dual simultaneous (same event stream, both architectures) |
| Burn verification | Cryptographic deletion + Merkle tree + audit chain |

## Appendix B: References

1. Chamber Sentinel Position Paper -- Medical Devices, First Edition (v3), April 2026.
2. WhiteScope, "Pacemaker Ecosystem Security Audit," 2017.
3. Gupta et al., "COMPAS Trial: Remote vs. Conventional Follow-up," Europace, 2012.
4. Crossley et al., "CONNECT Trial: Wireless Remote Monitoring," JACC, 2011.
5. Boriani et al., "MORE-CARE Trial," Europace, 2015.
6. FDA, "Premarket Cybersecurity Guidance for Medical Devices," 2023.
7. FDA, "Postmarket Management of Cybersecurity in Medical Devices," 2016.
8. Regulation (EU) 2016/679 (GDPR), Articles 5, 9, 17.
9. HIPAA Privacy Rule, 45 CFR Part 164.
10. Regulation (EU) 2017/745 (MDR), Articles 83-86.
11. Plank et al., "The openCARP Simulation Environment for Cardiac Electrophysiology," Computer Methods and Programs in Biomedicine, 2021.
12. ten Tusscher, K.H.W.J. and Panfilov, A.V., "Alternans and spiral breakup in a human ventricular tissue model," Am J Physiol Heart Circ Physiol, 2006.
13. O'Hara et al., "Simulation of the Undiseased Human Cardiac Ventricular Action Potential: Model Formulation and Experimental Validation," PLoS Computational Biology, 2011.
14. Courtemanche, M., Ramirez, R.J., and Bhasi, S., "Ionic mechanisms underlying human atrial action potential properties," Am J Physiol Heart Circ Physiol, 1998.
15. IDHP Model -- Integrated Dual-chamber Heart and Pacer (evaluated for integration; see Section 14.3).
16. UPenn Virtual Heart Model (VHM) -- PRECISE Center (evaluated for formal verification; see Section 14.3).
17. CVSim -- PhysioNet cardiovascular simulator (evaluated for hemodynamic modelling; see Section 14.3).

## Appendix C: Glossary

| Term | Definition |
|------|-----------|
| CIED | Cardiac Implantable Electronic Device (pacemaker, ICD, CRT) |
| EGM / IEGM | (Intracardiac) Electrogram |
| ATP | Anti-Tachycardia Pacing |
| BOL/MOL/ERI/EOS | Battery stages: Beginning of Life, Middle of Life, Elective Replacement Indicator, End of Service |
| Typed World | A sealed data domain in the Chambers architecture with defined scope, access, and burn schedule |
| Burn | Irreversible destruction of data after its typed lifetime expires |
| Relay | Manufacturer infrastructure that processes data in transit without persistent storage |
| Hold | Suspension of burn schedule for safety investigation purposes |
| Portable Record | Patient-controlled FHIR R4 data store independent of manufacturer infrastructure |
| openCARP | Open-source cardiac electrophysiology simulator |
| Ionic Model | Mathematical model of cardiac cell membrane ion channel dynamics |
| ten Tusscher Model | Human ventricular action potential model (2006) |
| O'Hara-Rudy Model | Updated human ventricular action potential model (2011) |
| Courtemanche Model | Human atrial action potential model (1998) |
| Template Library | Pre-computed set of openCARP-generated EGM waveform beat templates |

---

*This document is a position paper advancing a thesis for peer review and discussion. It extends the Chamber Sentinel framework to the medical device domain, supported by simulation-based engineering evidence, and should be read in conjunction with the core Chamber Sentinel position paper and the connected vehicle analysis. The authors welcome critical engagement, particularly from clinical electrophysiologists, device security researchers, regulatory specialists, and patient advocates.*

*End of document.*
