# Cold-chain Delivery and Quality Incident Management

A process-oriented application for coordinating refrigerated-food deliveries, detecting transport risks, and managing quality incidents.

Developed for **02268 — Process-oriented and Event-driven Software Systems**, Technical University of Denmark, **Group 12**.

## Project overview

The system follows refrigerated shipments from warehouse preparation to cafeteria receiving and any subsequent quality investigation, corrective fulfilment or supplier claim. The scenario covers two vehicles, three campus cafeterias and five chilled products: milk, yoghurt, fresh cheese, chicken breast and prepared salad.

Temperature and delivery-risk events will support workflow decisions through complex event processing. Physical receipt is separate from stock admission: goods under investigation must remain on hold until an authorised quality decision permits their release. Vehicle telemetry and physical actions are simulated; weather and traffic information are planned external inputs.

## Demonstration dataset

A fresh dataset contains **20 crates of each of five products: 100 crates total**. Each crate represents 12 retail packs, with the pack size defined in [the catalogue](deployer/catalog.json). Every shipment contains one product from one batch. Reservations and replenishment use that exact product/batch; total stock cannot substitute for a missing item.

The [dataset reference](docs/RUNNING.md#demonstration-dataset) lists all products, batches, units and stored records. These are simulated quantities and handling assumptions. `CHILLED-DEMO` identifies a shared demonstration profile; product-specific food-safety limits have not been validated or implemented.

**Existing pooled-stock databases require a new dataset.** Preserve their matching engine data and follow [the upgrade/start instructions](docs/RUNNING.md#persistence-isolated-checks-and-troubleshooting); they are not converted automatically.

## Processes

| Process | Execution platform | Status |
| --- | --- | --- |
| P1 — Shipment preparation | Camunda / Zeebe | Stock wait/cancellation, dispatch handoff and local compliance verification implemented |
| P2 — Transport and delivery | Camunda / Zeebe | Manual completion flow implemented; GPS/ETA, risk decisions and live P3 notification remain |
| P3 — Receiving and stock admission | Camunda / Zeebe | Planned |
| P4 — Quality incident investigation | DCR | Planned |
| P5 — Corrective fulfilment | Camunda / Zeebe | Planned |
| P6 — Supplier claim resolution | Camunda / Zeebe | Planned |

P1 supports shipment creation, stock checking, replenishment or cancellation during shortages, loading verification and dispatch. Dispatch starts a protected P2 transport instance carrying shipment details. P2 reaches completion through manual arrival and receipt inputs; it holds the vehicle until receipt acknowledgement. These inputs do not notify P3. P1 has a DCR compliance graph and XES execution log; P2 DCR verification remains outstanding. GPS simulation, TomTom ETA, delivery-risk decisions, Siddhi processing and P3–P6 are planned follow-up work.

## Architecture

The planned architecture connects simulated telemetry and external weather/traffic data to Siddhi. A Python integration service coordinates Camunda workflows, DCR investigations and process-approved simulator commands. Business activity logs will support compliance analysis using DCR rules.

The current implementation contains:

- **Camunda Zeebe 8.8** — execution of shipment preparation and the initial transport handoff.
- **Python 3.12** — HTTP endpoints and Zeebe job workers, using Flask and pyzeebe.
- **SQLite** — persistent shipment registry, reservations and activity journal.
- **Elasticsearch and Camunda Operate** — exported engine records and process inspection.

## Getting started

Prerequisites: Docker Desktop with Compose, Python 3.12 for the checks, and Camunda Modeler for editing diagrams. BPMN lint commands also use Node.js/npm. The current checkpoint needs no external API keys or `.env` file.

Start Docker Desktop, then run these commands from the repository root. Ports 5001, 8081, 9200, 9601 and 26501 must be available.

```bash
docker compose up --build -d --wait --wait-timeout 180
curl http://localhost:5001/health
```

Wait for the health endpoint to report `ready`. Open **Operate** at <http://localhost:8081> using the local development credentials `demo` / `demo`.

Follow the [run guide](docs/RUNNING.md) to create a shipment, verify loading and dispatch it. The guide also covers API behaviour, testing, persistence and local data resets.

For interactive demonstration controls after startup:

```bash
python3 tools/demo.py
```

The menu performs application actions and prints the corresponding instance keys to inspect in Operate. See [presentation controls](docs/RUNNING.md#presentation-controls) for the rehearsal sequence.

To stop the stack while preserving its data:

```bash
docker compose stop
```

Resume the existing containers with `docker compose start`, then check `/health` before opening the demo. Stopping preserves orders, stock and engine history. The menu uses preset order quantities (A: 15 crates; B/C: 10) to make the documented shortage scenario repeatable; the API accepts other quantities.

## Testing

Run the offline API and registry tests with Python 3.12:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r deployer/requirements.txt
.venv/bin/python -m unittest discover -s stage_tests -p 'test_*.py' -v
```

With the stack running and both vehicles available, run the engine integration check:

```bash
.venv/bin/python stage_tests/check_stage1.py
```

The live integration check exercises P1 stock wait/cancellation/replenishment and completes two independent P2 journeys through manual arrival and receipt. It confirms early receipt is rejected, exact retries do not duplicate activities, both Zeebe instances reach the `TransportCompleted` end event, and a released vehicle can be reused. It leaves no dispatched shipment waiting for arrival; see the [run guide](docs/RUNNING.md) before repeating it.

The offline suite contains **32 tests**; run the committed live P1/P2 check separately against a fresh stack. P1 compliance evidence is stored under `compliance/p1/`; that XES contains P1 activities only. A live run writes P2 execution evidence to `runtime/verification-results/p2-completion.json`, not a P2 DCR conformance result. The committed P1 report and ProcessDoctor screenshots document P1 conformance only.

## Repository structure

```text
bpmn/                Executable BPMN models and PDF exports
deployer/            HTTP integration, Zeebe workers and shipment registry
event_streams/       Scenario inputs
stage_tests/         Offline tests and engine integration checks
tools/               Interactive demonstration controls
compliance/          P1 DCR rules, log checker and reviewed execution evidence
docs/                Implementation guide, integration agreement and run instructions
project_notes/       Team task board and AI-use declaration record
docker-compose.yml   Local application and workflow infrastructure
```

Directories for additional components will be introduced with their implementations.

### DCR models and compliance rules

`compliance/p1/rules.xml` uses DCR to check P1's recorded execution; Camunda runs P1. Each process will have its own compliance rules under `compliance/`.

P4 will use DCR to **execute** the quality investigation. Its model will belong in `dcr/` when that process is implemented, with its API integration in the shared Python service.

## Continuing with P2 and P3

P2's manual-flow PR covers the `ShipmentDispatched` handoff through arrival, receipt acknowledgement, transport completion and vehicle release. The [P2/P3 handover](docs/COLD-CHAIN-INTEGRATION-AGREEMENT.md#p2-and-p3-development-handover) lists exact current contracts, worker entry points, manual-only inputs and remaining implementation checks.

**P1 owner: Ashvanth. P2 manual-flow owner: Rahul.** P3–P6 and simulator/CEP owners remain to be assigned. P3 has no executable receiving model or notification consumer; the P2 receipt input is manual/test only. Coordinate future shared Python and message-contract changes with the receiving owner and keep the handover/task board current. P2's full simulator and risk-decision scope is not complete.

## Project documentation

- [Implementation guide](docs/COLD-CHAIN-IMPLEMENTATION-GUIDE.md) — scenario, process scope and responsibilities.
- [Integration agreement](docs/COLD-CHAIN-INTEGRATION-AGREEMENT.md) — identifiers, messages, quality states and shared rules.
- [Run guide](docs/RUNNING.md) — beginner walkthrough, BPMN/DCR explanation and verification.
- [P1 model PDF](bpmn/shipment_preparation.pdf) and [DCR compliance rules](compliance/p1/rules.xml).
- [Team task board](project_notes/TASKS.md) — ownership, progress and remaining deliverables.
- [AI-use record](project_notes/AI-USAGE.md) — assistance recorded for the team contract declaration.

Team members work on feature branches and review changes before merging into `main`. Changes to shared interfaces must update the integration agreement and their affected producers and consumers. Credentials, generated runtime data and supplied course/team PDFs are excluded from version control.
