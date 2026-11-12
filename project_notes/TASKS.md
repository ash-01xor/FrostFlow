# Team task board

**Ashvanth owns P1; Rahul owns P2.** P3–P6 and external simulator/CEP owners remain unassigned. Use `TODO`, `IN PROGRESS`, `IMPLEMENTED / VERIFY`, `BLOCKED` and `DONE`. A checked box records only the specific item described.

| Work | Owner | Status | Main dependency |
| --- | --- | --- | --- |
| P1 Shipment preparation | Ashvanth | IMPLEMENTED; engine and external conformance verified | Teammate review |
| P2 Transport and delivery | Rahul | MANUAL FLOW + SCRIPTED SIMULATOR/DECISION PATH IMPLEMENTED; external verification IN PROGRESS | [Detailed handover](../docs/COLD-CHAIN-INTEGRATION-AGREEMENT.md#p2-and-p3-development-handover); confirm Siddhi/P3 contracts and perform live acceptance |
| P3 Receiving and admission | Unassigned | TODO | P2 arrival + P4 shared hold |
| P4 Quality investigation (DCR) | Unassigned | TODO | DCR access + incident integration |
| P5 Corrective fulfilment | Unassigned | TODO | P4 authorised disposition + P1–P3 |
| P6 Supplier claim resolution | Unassigned | TODO | Explicit claimable-loss event |
| Shared CEP, APIs, simulator, logging | Unassigned | TODO | Agree interfaces and access |
| Compliance and submission | All; coordinator unassigned | TODO | Models + real execution logs |

## Foundation and access

- [x] Read course specification, proposal, signed contract and both existing guides.
- [x] Track Markdown guides and shared project notes; ignore secrets, PDFs and runtime data.
- [x] Add project README, shared task board and consolidated run/verification guide.
- [ ] Team reviews the scope and integration agreement; assigns P2–P6 owners.
- [x] Run the P1 engine check and restart assertions; record results in [the run guide](../docs/RUNNING.md#automated-verification).
- [ ] Create and execute one minimal real DCR case; record access instructions without credentials.
- [ ] Check live Open-Meteo and TomTom requests; record response fixtures and data provenance.
- [x] Import the P1 DCR graph and real three-trace log into processdoctor.compute.dtu.dk and run conformance checking.

P1 external conformance checking has passed. DCR-PAIS and live API access checks remain pending; P4 still needs its own executable DCR investigation and account/graph access.

## Dataset decision — 1 November 2026

- [x] Adopt five chilled items: milk, yoghurt, fresh cheese, chicken breast and prepared salad; 20 crates each, 12 retail packs per crate.
- [x] Implement product/batch-specific stock, single-product/batch shipments and traceable handoffs/activity exports.
- [x] Update menu controls, manual scenario, API contract and offline regression coverage.
- [x] Preserve legacy pooled datasets; require fresh matching registry/engine data for version 3.
- [x] Re-run P1/P2 engine scenarios on fresh five-product data, verify persistence after stack recreation and refresh reviewed evidence/source hashes.
- [ ] Agree numeric demonstration temperature settings before CEP work; CHILLED-DEMO is currently a profile identifier only.

The catalogue and scope assumptions are documented in [the run guide](../docs/RUNNING.md#demonstration-dataset). The reviewed execution evidence now covers the five-product catalogue.

## P1 — Shipment preparation

- [x] Create executable BPMN with diagram coordinates and explicit loading/dispatch waits.
- [x] Implement create, verify-loading, dispatch, state and event endpoints.
- [x] Reserve demo stock and one of two vehicles; preserve shipment/journey and replacement references.
- [x] Block dispatch until verification is processed; reject duplicate commands and IDs.
- [x] Persist registry and idempotent worker activity records across ordinary service restarts.
- [x] Record `ShipmentDispatched` with the agreed identifiers, timestamps and payload.
- [x] Pass BPMN lint, Compose validation and offline guard/restart/concurrency tests.
- [x] Prove a full P1 run and engine completion using `stage_tests/check_stage1.py`; confirm second engine instance waits independently and registry survives communication restart.
- [x] Add stock-wait/replenishment/cancellation paths; block allocation/loading/dispatch during shortage.
- [x] Export updated P1 and P2 models to PDF; inspect rendered P1 diagram.
- [x] Deliver dispatch through a durable outbox; P2 registers and acknowledges once.
- [x] Add P1 DCR rules and replay actual normal/replenished/cancelled logs; reject a separate invalid trace.
- [x] Import the P1 graph/log into processdoctor.compute.dtu.dk and record actual results: three conforming traces, zero violations; separate synthetic negative trace flagged.
- [ ] A teammate runs P1, inspects the models in Modeler/Operate and reviews the handover.
- [ ] Implement operational reconciliation for uncertain engine starts/expired messages.

## P2 — Transport and delivery

- [x] Consume `ShipmentDispatched`, register one transport instance and acknowledge the handoff; reject duplicate effects.
- [x] Extend P2 through `StartJourney`, arrival catch/recording, receipt catch, vehicle release and `TransportCompleted`.
- [x] Add manual/test arrival and receipt endpoints, transactional states/activities, exact retries and vehicle-release gating.
- [x] Extend the CLI for manual receipt, optional manual arrival, state-aware Operate hints, scripted-risk input and dispatcher decisions.
- [x] Add a regression for external event IDs colliding with internal activity IDs.
- [x] Run the 33-test offline suite at tested commit `720ed5109fc26ec5b128f87010d01b62bc9628e4`; command: `python -m unittest discover -s stage_tests -p 'test_*.py' -v`.
- [x] Run `stage_tests/check_stage1.py` on a fresh version-3 dataset and verify two P2 completions, receipt gating, retries, Zeebe end events and vehicle reuse. P1 compliance artifacts: `runtime/verification-results/execution-log.xes` and `verification.json`; P2 execution evidence: `runtime/verification-results/p2-completion.json`. This live report is from the manual-flow validation run; the later legacy-collision repair is covered by the offline regression at tested commit `720ed5109fc26ec5b128f87010d01b62bc9628e4`. Runtime evidence is local/ignored; P2 JSON is not DCR conformance evidence.
- [x] Manually observe the CLI/Operate flow through `WaitForReceiptAcknowledged`, `TransportCompleted` and vehicle release.
- [x] Add the P3 arrival notification to the durable outbox with a distinct delivery ID and shipment/journey/product/batch/replacement/incident references.
- [x] Script GPS positions and record ETA updates; make the last route point publish automatic arrival.
- [x] Add a TomTom traffic-ETA adapter with a labeled local fallback.
- [x] Add the correlated `DeliveryRiskDetected` BPMN branch and deduplicate unresolved alerts.
- [x] Add delay acceptance and alternative-route decisions; require matching simulator acknowledgement before recording route change.
- [x] Add CLI controls for test risk, delay acceptance and route approval.
- [ ] Agree the P3 receiver envelope/idempotency response with its owner; verify retries start P3 once against the real receiver.
- [ ] Verify automatic arrival and route updates for two independent shipments in Operate.
- [ ] Verify a TomTom ETA request with the team's API key and record its source/response.
- [ ] Verify Siddhi receives telemetry and emits a shipment-correlated risk alert.
- [ ] Verify an approved route command reaches the simulator through Siddhi, receives acknowledgement, then changes GPS/ETA.
- [ ] Rehearse the risk/decision/route controls from the CLI against the live stack.
- [ ] Rerun the expanded 35-test offline suite under Python 3.12; the current local Python 3.10 runtime lacks `grpc`.
- [ ] Deliver P2 DCR rules and a real execution log; verify conformance separately from P1.
- [ ] Run the final live P2 acceptance with both independent journeys, automatic arrival, dispatcher outcomes, external notifications and receipt-gated release.

External P3/Siddhi/TomTom service owners and follow-up PR/issue links have not been agreed yet. Add names and links after team review. The code is implemented behind configurable endpoints; live acceptance and P2 DCR remain outstanding. See the [file-by-file handover](../docs/COLD-CHAIN-INTEGRATION-AGREEMENT.md#p2-and-p3-development-handover).

## P3 — Receiving and stock admission

- [ ] Start on arrival; record one physical receipt and notify P2.
- [ ] Inspect goods; serialize admission attempts with current shared quality-status updates.
- [ ] Block admission during PENDING and after REJECTED; allow authorised current-incident release.
- [ ] Emit admission/rejection outcome, retaining replacement links.
- [ ] Coordinate a simple human-action menu for all processes.
- [ ] Prove receipt is distinct from admission; stale releases cannot clear a newer hold.

## P4 — Quality investigation (knowledge-intensive DCR process)

- [ ] Author a real DCR graph with flexible evidence order and required assessment/disposition.
- [ ] Persist hold before creating the DCR case; keep it if DCR creation fails.
- [ ] Coalesce repeated alerts into one active incident; create a new episode after closure.
- [ ] Enforce requested-check obligations and prevent premature closure.
- [ ] Send cooling command through Siddhi; collect acknowledgement and recovery evidence.
- [ ] Record authorised RELEASE/REPLACE/RETURN and notify P3/P5 for the current incident.
- [ ] Prove blocked DCR actions, alternative evidence order and the stock-admission gate.

## P5 — Corrective fulfilment

- [ ] Reject unauthorised corrective work; consume valid replacement/return decisions once.
- [ ] Create linked replacement through P1; wait for P3 receiving outcome and original-goods disposition.
- [ ] Track return booking and actual collection acknowledgement.
- [ ] Prove booking/dispatch alone cannot complete fulfilment.
- [ ] Coordinate temperature, delivery-risk and recovery CEP patterns with B/D's eventual owners.
- [ ] Coordinate Open-Meteo evidence and cooling-command forwarding.

## P6 — Supplier claim resolution

- [ ] Require explicit claimable loss; link original shipment and incident; deduplicate claims.
- [ ] Assemble evidence; simulate submission and settlement/rejection response.
- [ ] Prevent claim closure before final outcome.
- [ ] Coordinate shared activity logger, JSON/XES export and compliance-tool import.

## Shared integration and demonstration

- [ ] Agree reading cadence, windows, gap limits, API freshness, demo durations and units.
- [ ] Implement agreed event/command envelope validation and durable ID deduplication.
- [ ] Implement temperature excursion, persistent ETA risk and post-command recovery in Siddhi.
- [ ] Demonstrate PAIS → Siddhi → simulator → telemetry → Siddhi → PAIS in both directions.
- [ ] Label live, recorded, simulated, manual and system inputs; retain original evidence times.
- [ ] Check missing/stale data, repeated alerts/commands, two shipments and a new incident after release.
- [ ] Rehearse normal delivery, temperature release, replacement/return + eligible claim, and delay handling.
- [ ] Compare assumed manual baseline with improved process on the same recorded inputs.

## Every process owner's completion checklist

- [ ] Source model and PDF identify responsible student; AS-IS and improvement are explained.
- [ ] Real engine executes the model and its neighbouring handoffs.
- [ ] Successful run and forbidden-action check have reproducible commands and actual evidence.
- [ ] DCR compliance graph matches logged activities and includes relevant neighbouring events.
- [ ] Real execution log imports into processdoctor.compute.dtu.dk; results are recorded and explained.
- [ ] A teammate can run and explain it; AI assistance is declared accurately.

## Submission

- [ ] Prepare intermediate presentation for 3 November 2026; distinguish implemented from planned work.
- [ ] `report.pdf`: at most 10 pages, includes critical analysis and link to screencast ≤3 minutes.
- [ ] `source.zip`: source/PDF models, CEP, services, configuration, scenarios and reproducible instructions.
- [ ] `presentation.pdf`: final 10-minute presentation, with a contribution from every member.
- [ ] Include faithful AI-use declaration and verify one final ZIP containing the three named deliverables.
- [ ] Submit by 15 December 2026 at 23:59; check course announcements for any revisions.

## Collaboration

After the initial baseline has been pushed, start work from the shared main branch:

```bash
git switch main
git pull --ff-only
git switch -c feat/p2-transport
```

Choose a branch name for your task and agree on ownership before starting shared changes. Update this board with the implementation, push your branch and open a pull request with the relevant test results. A teammate should review and run the change before it is merged into `main`.

Keep business IDs and message contracts consistent with the [integration agreement](../docs/COLD-CHAIN-INTEGRATION-AGREEMENT.md). Use the implemented outbox/inbox for P2 handoff; extend its manual-recovery limits when implementing the rest of transport. Agree on geographic routes, CEP thresholds, reading cadence and freshness limits before simulator/CEP work. Keep each developer's engine volumes and registry local and consistent.
