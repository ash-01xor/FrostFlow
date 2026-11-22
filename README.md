# FrostFlow

A cold-chain delivery and quality investigation demonstration for DTU **02268 — Process-oriented and Event-driven Software Systems**, Group 12.

FrostFlow follows chilled goods from warehouse preparation through transport, receiving and quality investigation. Vehicle movement, container readings and physical actions are simulated. Temperature thresholds are demonstration assessment triggers, not validated food-safety limits.

## Implemented processes

| Process | Engine | Owner |
| --- | --- | --- |
| P1 — Shipment preparation | Camunda / Zeebe | Ashvanth |
| P2 — Transport and delivery | Camunda / Zeebe | Rahul |
| P3 — Receiving and stock admission | Camunda / Zeebe | Arunima Garg |
| P4 — Quality investigation | DCR | Abid |
| P5 — Corrective fulfilment | Camunda / Zeebe | Daniel |

P1 reserves stock and dispatches shipments. P2 tracks transport and releases the vehicle after physical receipt. P3 inspects goods and admits them into separate receiving inventory. Transport or monitoring risks place goods on hold; P4 investigates and decides release, replacement or return before admission can proceed.

Siddhi evaluates transport and container telemetry. Optional TomTom traffic ETAs and Open-Meteo current conditions provide external context. P4 uses a local DCR engine by default; dcrgraphs.net repository mode is also supported.

P5 consumes applied P4 replacement/return decisions, books original-goods collection and reuses P1–P3 for linked replacements. Replacement success requires collection and stock admission of the new goods; rejection/cancellation produces a failed outcome. P6 supplier claims are planned. Automatic cooling/recovery integration and an external physical simulator remain future work. P5 is published in [PR #14](https://github.com/ash-01xor/FrostFlow/pull/14), open and non-draft, awaiting review follow-up and merge as of 22 November 2026.

## Run locally

Prerequisites: Docker Desktop with Compose; Python 3.12 for tests. No API keys are needed for the default demonstration. Inspect existing stacks first: run one stack at a time and preserve each registry with its matching engine data. See the [run guide](docs/RUNNING.md) before changing datasets or resuming a verification stack.

From the repository root:

```bash
docker compose up --build -d --wait --wait-timeout 180
curl http://localhost:5001/health
python3 tools/demo.py
```

Wait for health status `ready`. The API is at `http://localhost:5001`; Operate is at `http://localhost:8081` (local development login: `demo` / `demo`). Stop with `docker compose stop` to preserve data.

A fresh dataset has five products, 20 crates each, two vehicles and three recipients. Each shipment contains one product and batch. The [run guide](docs/RUNNING.md) covers the catalogue, demonstration sequence, configuration and recovery.

## Verification

Reuse the existing `.venv`, or create it once with `python3.12 -m venv .venv`:

```bash
.venv/bin/python -m pip install -r deployer/requirements.txt
.venv/bin/python -m unittest discover -s stage_tests -p 'test_*.py' -v
```

Live checks require the datasets and settings in the [verification instructions](docs/RUNNING.md#automated-verification). Results identify their tested revisions and distinguish offline tests, live execution, local replay and external conformance; see the [evidence index](docs/RUNNING.md#evidence-what-was-verified-and-where).

The [merged-main smoke record](compliance/merged-main-verification.json) identifies the tested revision and results. The [P5 verification record](compliance/p5/verification.json) covers this branch’s corrective workflow, including live engine execution and separate local/external conformance checks.

## Repository and documentation

- `bpmn/`, `dcr/` — executable process models and PDF exports.
- `deployer/`, `siddhi-apps/` — application, workers and telemetry rules.
- `stage_tests/`, `compliance/` — checks, compliance rules and recorded evidence.
- [Run guide](docs/RUNNING.md) — setup, API, demonstrations and verification.
- [Implementation guide](docs/COLD-CHAIN-IMPLEMENTATION-GUIDE.md) — full P1–P6 scope.
- [Integration agreement](docs/COLD-CHAIN-INTEGRATION-AGREEMENT.md) — shared contracts and safeguards.
- [P4 guide](docs/P4-QUALITY-INVESTIGATION.md) — investigation behavior and interfaces.
- [P5 guide](docs/P5-CORRECTIVE-FULFILMENT.md) — corrective workflow, API/menu controls and verification.
- [Task board](project_notes/TASKS.md), [AI-use record](project_notes/AI-USAGE.md) and [agent instructions](AGENTS.md) — team workflow and project records.

Changes go through feature branches and review. Credentials and generated runtime data stay untracked.
