# Working on FrostFlow

**P4 reintegration (replacement PR, branch `feat/p4-foundation-integration`):** Abid’s P4 from [PR #10](https://github.com/ash-01xor/FrostFlow/pull/10) was reverted by PR #11 so foundation [PR #9](https://github.com/ash-01xor/FrostFlow/pull/9) could merge first (merged as `ae941cc`). [PR #12](https://github.com/ash-01xor/FrostFlow/pull/12) restores P4 by reverting `31ad23b` and adapts it to the [foundation contract v1](docs/P4-FOUNDATION-HANDOVER.md); see [sequencing](docs/P4-INTEGRATION-SEQUENCING.md#checkpoint-4--replacement-p4-pr) and the [P4 guide](docs/P4-QUALITY-INVESTIGATION.md). Review, merge and post-merge verification outcomes are recorded on PR #12.

These instructions apply throughout this repository. Read this file before changing code, process models, tests or documentation. Keep it current when architecture, commands or shared contracts change.

## Project and sources of truth

FrostFlow is DTU **02268 — Process-oriented and Event-driven Software Systems**, Group 12's cold-chain delivery coursework. The intended system has five Camunda BPMN processes and one executable DCR investigation. It is a local demonstration with simulated goods and vehicle movement, not a production food-safety system.

Start with the documents relevant to your task:

| Source | Use it for |
| --- | --- |
| [README.md](README.md) | Overview, implemented processes and repository entry points |
| [Implementation guide](docs/COLD-CHAIN-IMPLEMENTATION-GUIDE.md) | Full P1–P6 scope, required demonstrations and submission requirements |
| [Integration agreement](docs/COLD-CHAIN-INTEGRATION-AGREEMENT.md) | Shared IDs, message envelopes, handoffs, quality rules and API contracts |
| [P4 foundation handover](docs/P4-FOUNDATION-HANDOVER.md) | Abid’s reintegration boundary, durable acknowledgements, assessed evidence versions and fixtures |
| [P5 guide](docs/P5-CORRECTIVE-FULFILMENT.md) | Corrective fulfilment, authorization, replacement/collection contracts and verification |
| [P4 guide](docs/P4-QUALITY-INVESTIGATION.md) | P4 DCR investigation: rules, contract v1 behaviour, engine modes, API, CLI 19 and evidence |
| [Run guide](docs/RUNNING.md) | Environment, startup, verification commands, fresh datasets and recovery limits |
| [Task board](project_notes/TASKS.md) | Current checkpoint, ownership, open dependencies and collaboration workflow |
| [AI-use record](project_notes/AI-USAGE.md) | Actual assistance and verification history; append your contribution |
| `compliance/p1/` … `compliance/p5/` | Rules, captured execution logs and verification reports for each process |
| [Foundation review refresh](compliance/foundation-review-18.json) | Foundation review-18 verification, handover fixtures and original-P4 reproduction; original scope retained |
| [P5 verification](compliance/p5/verification.json) | Corrective live run, source/image hashes, offline tests and separate local/external conformance |
| [Merged-main verification](compliance/merged-main-verification.json) | Recorded integrated smoke result and its exact tested revision |

Some guide sections retain older checkpoints or describe the target architecture. Check current code, the task board's latest checkpoint and each report's tested commit/hash before claiming something works. Flag contradictions instead of silently inventing ownership or treating planned work as implemented. The separate course specification, proposal and signed contract PDFs are intentionally absent from Git; do not claim to have inspected them from these summaries.

## Implemented scope and boundaries

- **P1 — preparation:** stock checks, reservation, shortage/replenishment/cancellation, loading verification and dispatch. Owner: Ashvanth.
- **P2 — transport:** scripted GPS/ETA, optional TomTom traffic ETAs, local Siddhi risk detection, dispatcher decisions, automatic arrival and receipt-gated vehicle release. Owner: Rahul Shanbhag.
- **P3 — receiving:** durable arrival handoff, physical receipt, inspection, separate receiving inventory and quality hold/release/rejection. Owner: Arunima Garg.
- **P4 — investigation (DCR):** Abid’s investigation (`deployer/quality.py`, `dcr/`) consumes the foundation's durable `DeliveryRiskIncident`/`AssessmentRequested` handoffs, acknowledges `{deliveryId, status}`, runs a DCR case per incident and checks each disposition read-only (`Registry.check_quality_status`), executes it in DCR and only then applies it through `Registry.accept_quality_status` with the assessed `expectedEvidenceVersion`, so quality permission never precedes the DCR decision; refused dispositions require reassessment. Default engine is the local DCR engine on the same graph; dcrgraphs.net repository mode needs `DCR_*` credentials. Restored and adapted in replacement PR #12; offline tests (including the first-review fault/race regressions) pass. Combined live run `ed88d16d` (on `35d154a`, the final PR #12 code) passed with P4 executing in the dcrgraphs.net repository, and ProcessDoctor reports 4/4 conforming variants for its log. The dcrgraphs.net API check (`stage_tests/check_dcr_repository.py`), the earlier live runs `d2558070` and `fd021988` and its ProcessDoctor 4/4 result apply to their recorded revisions. `POST /integrations/p4/quality-status` and CLI option 17 remain test stand-ins that bypass P4.
- **P5 — corrective fulfilment:** `bpmn/corrective_fulfilment.bpmn` and `deployer/corrective*.py` consume only applied P4 dispositions. The workflow books and acknowledges original collection, calls the co-deployed P1 for a linked full replacement, and waits for P3 admission. Rejected/cancelled replacements fail fulfilment. `replacementForCorrectiveId` preserves the parent even if the replacement gets its own P4/P5 case. APIs and CLI option 20 expose manual arrangements/collection. Owner: Daniel. P5 is published in [PR #14](https://github.com/ash-01xor/FrostFlow/pull/14), open, non-draft and not merged as of 22 November 2026; see the P5 guide for exact verification scope.
- **P6:** supplier claim resolution remains planned. Container excursion/combined-risk assessment, Open-Meteo current conditions and P3 monitoring finalization are implemented in this foundation. Recovery CEP, a shared logger and an external physical simulator remain future work. Abid owns P4 and Daniel owns P5; P6 and shared integration owners remain unassigned.

Preserve the full P1–P6 plan without expanding an unrelated repair into those features. Mixed-product crates, partial fulfilment, new-batch procurement, route optimisation and detailed refrigeration physics are outside the current increment.

## Repository and runtime architecture

| Path | Responsibility |
| --- | --- |
| `deployer/app.py` | Flask routes, validation, Zeebe workers, deployment, message delivery and background integration loops |
| `deployer/registry.py` | SQLite transactions, business state/effects, inventories, activity journal and durable outbox/retry identities |
| `deployer/transport_integrations.py` | Scripted routes, TomTom adapter and HTTP integration helpers |
| `deployer/monitoring.py`, `monitoring_registry.py`, `weather.py` | Snapshotted demo profile, durable container sessions/assessment, bounded Open-Meteo current data |
| `deployer/catalog.json` | Fresh-dataset product/batch seed; existing registries are not reseeded |
| `bpmn/` | Executable P1/P2/P3/P5 BPMN sources and PDF exports |
| `deployer/corrective.py`, `corrective_registry.py` | P5 API, durable corrective effects and transactional P4/P3 handoffs |
| `siddhi-apps/ColdChainTelemetry.siddhi` | Telemetry classification, ETA persistence rule and acknowledged route-command bridge |
| `tools/demo.py` | Terminal demonstration menu using the application API |
| `stage_tests/test_*.py` | Offline `unittest` regressions with substituted engine/external calls |
| `stage_tests/check_*.py` | Live engine/integration runners; inspect prerequisites before running |
| `compliance/check.py` | Flat DCR replay and XES tooling; not a DCR process execution service |
| `deployer/quality.py`, `deployer/dcr_client.py`, `dcr/` | P4 investigation service, dcrgraphs.net client/local DCR engine and the executable graph |
| `compliance/p*/build_*_evidence.py` | Rule/log generation and verification helpers for P2/P3 |
| `event_streams/` | Example request inputs |
| `runtime/` | Ignored local registries, provider responses and generated execution evidence |

Python **3.12**, Flask and `pyzeebe` are used; dependencies are pinned in `deployer/requirements.txt`. Docker Compose runs one communication service, Zeebe/Operate **8.8.0**, Elasticsearch **8.16.0** and Siddhi. The current Siddhi image uses `latest`; record the actual image/environment for verification rather than assuming it is immutable.

**Zeebe owns control flow; SQLite owns durable business effects.** Preserve this separation. Use the existing `Registry` transaction/outbox/event patterns rather than updating state directly in HTTP handlers or bypassing engine waits. Only one communication instance is supported.

## Business and integration invariants

1. A shipment contains **one product and one batch**, with one shipment per journey and one active shipment per vehicle. A fresh version-3 dataset has five products, 20 crates each, 12 retail packs per crate, two vehicles and three recipients. Warehouse reservation/replenishment addresses the exact `(productId, batchId)`; another product cannot satisfy a shortage. Recipient inventory is separate, keyed by `(recipientId, productId, batchId)`. Arrival/admission never credits warehouse stock.
2. `CHILLED-DEMO` identifies the demonstration catalogue. New shipments snapshot the approved configurable `CHILLED-DEMO-v1` monitoring profile: normal 3°C, fault 10°C, assessment above 4°C for chicken and above 5°C otherwise, three readings spanning 10 s, 10 s maximum gap, 15 s freshness, 5 s sampling and 20 s missing/evaluation timeouts. These are assessment triggers, not validated safety limits. See [foundation requirements](docs/PRE-P4-FOUNDATION.md).
3. Keep business IDs distinct from engine instance keys. Preserve shipment, journey, product, batch, incident, command and replacement references through the relevant handoffs. Retain UTC `occurredAt` and `recordedAt`, provenance and input-mode labels.
4. All business effects must tolerate retries. Identical event/delivery/command IDs and payloads reuse the accepted result; conflicting reuse must fail. Keep external producer IDs separate from internal journal identities. Persist effects and their outgoing messages in the same transaction; use the receiver's durable deduplication/acknowledgement contract.
5. HTTP **202 means durably accepted**, not completed. Poll the actual state with a bounded timeout. Loading verification requires `AWAITING_LOADING` **and no pending command**; retain the application guard and wait for readiness in runners.
6. Dispatch requires processed loading verification. Shortage cancellation/replenishment targets the current stock-wait episode (`stockWaitKey`). Preserve safeguards for competing commands, stock quantities and vehicle reservations.
7. GPS arrival is not receipt. P2 stays `ARRIVED` with its vehicle reserved until P3 records physical receipt and sends `ReceiptAcknowledged`. The manual P2 receipt route is refused while P3 receiving is enabled or has started. Receipt releases transport; it does not grant stock admission.
8. P3 checks inspection and **current shared quality status transactionally** at admission. `PENDING` blocks admission; release must name the current incident. `REJECTED` is terminal: later delay alerts retain the incident/disposition, add evidence, and hand off `qualityStatus: REJECTED` with `incidentOpened: false`. They cannot reopen quality permission. Transport decisions remain independent.
9. Accepting delivery risk establishes a durable hold/P4 handoff before investigation delivery. If P4 is unavailable, retain both. A delay is evidence for assessment, not automatic proof of damage; recovery evidence must never automatically release goods.
10. Siddhi counts **three distinct consecutive fresh late ETA observations within 180 seconds** per shipment/route revision. Deduplicate by `(shipmentId, journeyId, eventId)` **before** the sequence. Freshness is 30 seconds; on-time, unavailable or stale observations break the sequence. Forward at most one alert per shipment/journey/revision. Preserve tests for duplicate delivery, isolated/stale/gap readings and revision changes.
11. Route changes start at the current position and preserve the recipient destination. Apply them only after the matching simulator `commandId` is acknowledged; return updated `routeId`/`routeRevision` to Camunda. Arrival before a dispatcher decision closes the risk and rejects late decisions.
12. With no TomTom key, label local demo estimates clearly. With a configured key, provider failure/unusable data means unavailable ETA (`tomtom-unavailable`), not a fallback estimate or an on-time reading. Preserve unavailable-data sequence behavior.

13. Container sampling is independent of GPS/provider calls and continues until physical receipt. Persist observations before Siddhi delivery; acknowledgements apply contiguously with quality effects in one transaction. P3 must finalize through the closing sequence or establish a durable uncertainty hold before admission. Monitored dispositions require the current incident and expected evidence version. Weather alone never holds/rejects goods; urgent combined risk needs rising container readings and fresh current-revision persistent ETA lateness.

14. P5 requires a journalled applied P4 `REPLACE`/`RETURN` decision, never the quality-status test stand-in. Reserve each linked replacement once through P5, retaining its parent reference independently of its own quality case. Keep original rejection/collection separate from replacement admission. Dispatch, booking or rejected replacement goods cannot produce successful fulfilment. Full collection must match its booking and P3 physical receipt/rejection; it credits neither inventory.

When changing a shared message, update its producer, consumer, workers/model mappings, regression coverage and integration agreement together.

## Development and Git workflow

- Inspect `git status`, the current branch and applicable instructions first. Preserve unrelated work.
- Use a descriptive feature/fix/test/docs branch from current main. **Tests and documentation also go through a separate branch and PR**, not direct commits to main. Do not merge without user authorization; retain review requirements and resolve findings with concrete verification results.
- Use `colgrep` as the primary code search tool, for example `colgrep "quality admission hold" -k 20 deployer stage_tests`. Use scoped queries and `-e` for text filters. If unavailable or its indexer fails (some hosts lack a Metal GPU), report that and fall back to `rg`. File-name discovery can use `rg --files`. Exclude generated/runtime data and secrets from searches.
- Follow existing Python conventions: four-space indentation, snake_case internal functions, existing camelCase wire fields. Keep validation at API boundaries and atomic business changes in `Registry`. Avoid unrelated refactors or dependency churn.
- BPMN task types, worker registration, fetched variables, correlation keys and returned variables must agree. Preserve explicit gateway merges/defaults and diagram coordinates. Models deploy at service startup; existing instances remain on their original deployed version.
- Use the existing local `.venv` for offline work. Check the environment before reinstalling dependencies or creating another environment. Do not start Docker just to edit documentation.
- Update the task board and relevant guides when scope/status/contracts change. Append a concise, accurate AI-use entry describing actual assistance, checks and limits. Do not fabricate human review or test outcomes.

## Documentation is part of completion

**Every major change, feature implementation, integration, or completed milestone must include a documentation review and the necessary updates in the same PR. Do not declare the work complete while affected documentation describes the previous behavior.** For small fixes, update any affected commands, contracts or status claims as well.

Before requesting review or reporting completion:

1. Check `README.md` for implemented/planned status, ownership, entry points and verification claims.
2. Check `docs/RUNNING.md` for runnable commands, prerequisites, ports, endpoints, expected outcomes, retry/readiness waits and dataset requirements. Examples must use the current default integration path.
3. Check the integration agreement for changed messages, producers/consumers, states, guards and handoffs; check the implementation guide for scope, ownership and progress. Preserve the full P1–P6 target while marking which parts actually exist.
4. Update `project_notes/TASKS.md`: completed items, remaining dependencies, confirmed owners and PR/evidence links. Split mixed tasks when only part is implemented. Resolve contradictory current statements across files, rather than appending another summary that leaves them in place. Leave unconfirmed ownership explicitly unconfirmed.
5. Update affected verification reports after relevant checks, recording the exact tested revision, input modes and outcomes. Regenerate execution logs/PDFs only when their source or evidence changes. Keep live execution, local replay and external conformance separate; older results must identify the original log/revision. Preserve dated AI-use history and append the assistance actually provided.
6. Check `AGENTS.md` itself for changes to architecture, invariants, workflow or commands. Search other current documentation for superseded claims such as "not implemented", "needs verification", "unassigned" and obsolete endpoints/run IDs. Verify links and paths and run `git diff --check`.

Include a short documentation-impact note in the PR: list updated guides, or explain why no documentation changes are needed. A passing test suite alone does not satisfy this completion requirement. Do not manufacture evidence or mark pending external checks complete to make the documents agree.

## Verification: choose the appropriate level

Run from the repository root. Documentation-only edits need link/path/command consistency checks and `git diff --check`, not a fresh live dataset. Code repairs should have meaningful regressions for the defect and appropriate integration checks; stubs do not prove engine execution.

Offline setup/checks (reuse `.venv` if present):

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r deployer/requirements.txt
.venv/bin/python -m unittest discover -s stage_tests -p 'test_*.py' -v
docker compose config --quiet
git diff --check
```

For BPMN changes, use the pinned linter on every affected model and finish with zero errors/warnings:

```bash
npx --yes @camunda8/cli@4.6.1 bpmn lint bpmn/shipment_preparation.bpmn
npx --yes @camunda8/cli@4.6.1 bpmn lint bpmn/transport.bpmn
npx --yes @camunda8/cli@4.6.1 bpmn lint bpmn/receiving.bpmn
npx --yes @camunda8/cli@4.6.1 bpmn lint bpmn/corrective_fulfilment.bpmn
```

Regenerate affected model PDFs using the pinned export commands in the run guide and visually inspect them. Lint is structural validation; verify changed behavior in the real engine with fresh cases as appropriate.

| Check | Command using `.venv/bin/python` | Prerequisites and purpose |
| --- | --- | --- |
| Isolated ETA rule | `stage_tests/check_siddhi_risk_rule.py` | Docker; temporary Siddhi runner; duplicate/freshness/consecutive-reading regressions |
| P1 regression | `stage_tests/check_stage1.py` | Fresh paired version-3 data, 100 warehouse crates, free vehicles, simulator **off**; includes independent transports, retry/receipt gates and vehicle reuse |
| P2 live acceptance | `stage_tests/check_p2_siddhi.py` | Fresh paired data, simulator **on**, local Siddhi, GPS tick 10 s; risk, acknowledged route change, arrival closure and current-incident hold/release |
| P3 live scenarios | `stage_tests/check_p3_compliance.py --base http://localhost:5001` | Fresh paired data, simulator on; normal admission, hold/release, inspection rejection, quality rejection; runner otherwise defaults to alternate port 5011 |
| P4 live investigations | `stage_tests/check_p4_investigation.py --base http://localhost:5001` | Fresh paired data, simulator on, local Siddhi, `GPS_TICK_SECONDS=15`; four investigations on monitored shipments (release, replace, return, newer-evidence reassessment) with refused actions and P3 outcomes; then `compliance/p4/build_p4_evidence.py --live ...` |
| P5 live fulfilment | `stage_tests/check_p5_fulfilment.py --base http://localhost:5001` | Fresh paired version-3 data; simulator on, GPS tick 2 s, local Siddhi/P4. Four scenarios plus the rejected replacement’s separate return; then `compliance/p5/build_p5_evidence.py live ...`. Preserve prior outputs. |
| Integrated smoke | `stage_tests/check_integration_smoke.py` | Existing healthy default-port stack permitted; simulator on, free vehicles, one salad and one chicken crate; new shipments, admission/hold-rejection, exported end events and stock deltas |

Follow [the run guide's sequential stack commands](docs/RUNNING.md#automated-verification) rather than launching separate concurrent stacks. Set `TOMTOM_API_KEY=` for deterministic local-estimate runs and label that mode; do not report them as live TomTom verification.

Local replay of saved evidence can run without Docker:

```bash
.venv/bin/python compliance/check.py compliance/p1/execution-log.xes
.venv/bin/python compliance/check.py compliance/p2/execution-log.xes --model compliance/p2/rules.xml
.venv/bin/python compliance/check.py compliance/p3/execution-log.xes --model compliance/p3/rules.xml
.venv/bin/python compliance/p4/build_p4_evidence.py   # P4: simulation log, forbidden traces, report
.venv/bin/python compliance/check.py compliance/p5/execution-log.xes --model compliance/p5/rules.xml
```

The flat checker implements conditions, responses, includes, excludes and milestones; it does not implement timed/data/hierarchical DCR. P4's local engine (`deployer/dcr_client.LocalDCR`) uses the same flat semantics; `compliance/p4/rules.xml` must equal `dcr/p4_quality_investigation.xml` without its `<roles>` elements (ProcessDoctor cannot check the role-assigned graph against our logs; see `compliance/p4/verification.json`). Keep synthetic forbidden traces separate from actual execution logs. A synthetic invalid trace's failure is expected, not a suite failure.

Keep **offline tests, live execution, local DCR replay and external ProcessDoctor conformance** distinct. Record actual commands, environment/input modes, tested commit, source/model/log hashes and outcomes. Manual ProcessDoctor checks for the review-18 P1/P2/P3/container logs passed; screenshots, user-confirmed input identity and exact model/log hashes are in `compliance/foundation/review-18/processdoctor.json`. Earlier reports retain their original scope; their results do not validate changed logs. If model/log evidence changes, refresh external results or mark them pending with the actual reason. Log/verification builders overwrite artifacts: do not run them casually or replace reviewed evidence with synthetic, partial or failed runs.

## Docker, data and secrets

- Inspect existing containers, their project labels/mounts, registries and named volumes before choosing a stack. Run **at most one project stack at a time**. Reuse a compatible stack and rebuild only affected services.
- **SQLite registry and Zeebe/Elasticsearch data form a matched dataset.** Keep them together. Never attach a fresh registry to consumed engine volumes or reseed stock under live instances. Preserve legacy pooled-stock databases; the app refuses automatic conversion to version 3.
- Fresh-data suites require both fresh registry and engine data. Use `stage_tests/verification.compose.yml` with `COMPOSE_PROJECT_NAME`, `VERIFY_DATASET` and matching `REGISTRY_DIR`; change registry and volume identities together between suites. Keep the same variables when resuming/recreating a dataset. Do not rerun a fresh-data check against consumed stock.
- Ordinary `stop`/`start` preserves data. For recreation, provide the original project, registry and overlay variables again. Python changes require rebuilding communication; BPMN sources are mounted and redeploy after communication restart; Siddhi changes require reloading/restarting Siddhi.
- Do not broadly prune Docker or remove volumes with unverified contents. Identify disposable stopped containers, preserve needed evidence and remove only those. `down -v` is a deliberate destructive reset, not a routine test or cleanup step.
- API: `127.0.0.1:5001`; Operate: `8081`; Elasticsearch: `9200`; Zeebe gRPC: `26501`; broker management: `9601`; Siddhi telemetry/commands/container evaluation: `8006`/`8007`/`8008`. Check health for `status: ready`, `stage: 3` and integration modes. The alternative-port P3 overlay is documented in the run guide.
- Retain existing environment names, including the spelling `SIDDIHI_TELEMETRY_URL` and `SIDDIHI_COMMAND_URL`. `P3_ARRIVAL_URL` defaults to the local P3 receiver; Compose has one `P4_INCIDENT_URL`, defaulting to the local P4 consumer (`/integrations/p4/incident`); set it empty to leave handoffs pending. `DCR_GRAPH_ID`, `DCR_USERNAME`, `DCR_PASSWORD` belong only in an untracked `.env`. Compose does not currently forward every app-supported variable; check both configurations when adding one.
- `.env`, keys, SQLite files and raw `runtime/` output stay untracked. Do not print secrets, dump environment files, commit credentials in URLs/logs or expose unauthenticated local demo endpoints publicly. Commit only reviewed, sanitized evidence. Model PDFs under `bpmn/` are tracked; separately supplied reference PDFs under `docs/` are ignored.
- Engine-start timeouts can have uncertain outcomes. Inspect retained state before retrying. Published messages have finite retention (24 hours in the current implementation); expired-message/uncertain-start reconciliation remains manual. Do not erase records to hide incidents or manufacture success.

## Coursework completion requirements

As recorded in the implementation guide, each process owner supplies the source model/PDF, executable implementation connected to neighboring processes, a successful run and forbidden-action check, DCR compliance rules with matching real logs/results, and reproducible instructions another teammate can explain. Respect existing attribution choices; P1's BPMN/PDF intentionally omit the student name, while ownership is documented elsewhere.

The recorded submission target is **15 December 2026, 23:59**: one ZIP containing `report.pdf` (maximum 10 pages and a screencast link of at most 3 minutes), `source.zip` (models/PDFs, services, CEP, scenarios/checks and instructions), and `presentation.pdf` (10-minute group presentation with every member contributing). Verify against the actual course materials/announcements before final submission. Declare AI assistance faithfully and distinguish assumptions, simulation and observed results.
