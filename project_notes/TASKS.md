# Team task board

**P6 merged integration — 22 November 2026:** all reported PR #15 findings are fixed. Independent verification at `163bd16` passed 140 offline tests, four P5 scenarios/five corrective completions and five live P6 claims, including a 116-character incident ID. Actual Zeebe ends, real Siddhi assessments, local DCR execution and exact -25/+1 stock effects (zero P6 stock effect) are recorded in the [supplement](../compliance/p6/review22/verification.json). PR #15 merged as `35d2d0b`; all 140 tests and its exact merged-main admission/rejection/claim smoke passed, with exported engine ends and -2/+1 stock effects (zero P6 stock effect). See the [merged-main record](../compliance/merged-main-verification.json). This does not complete automatic cooling/recovery or submission work.

**P5 integration — 22 November 2026:** Daniel owns P5 and merged [PR #14](https://github.com/ash-01xor/FrostFlow/pull/14) as `b3f6102`. The PDF margin and ownership corrections are included and have been rechecked. Application and BPMN source are unchanged from the reviewed code. The integrated P6 main revision `35d2d0b` now passes post-merge smoke; the full P5 workflow evidence below retains its original revision and scope.

**Recorded P5 verification — 21 November 2026:** all 136 offline tests and final-code live run `9347c2c8` passed at `d118870`: four scenarios, five P5 instances, exact -25/+1 inventory effects and real P1/P2/P3/P5 engine records. Local replay passed all five traces; ProcessDoctor reported 4/4 conforming variants with zero structural violations, and the separate synthetic dispatch-only trace violated as expected. See [P5 guide](../docs/P5-CORRECTIVE-FULFILMENT.md), [verification](../compliance/p5/verification.json) and [external evidence](../compliance/p5/processdoctor.json). These review corrections change only documentation and the PDF export; the recorded runtime evidence retains its original revision.

**Independent review — 21 November 2026:** all 118 offline tests and four live P4 scenarios passed (run `ccd5079d`, local DCR, Zeebe/Siddhi, fresh matched `review21` data). See [verification record](../compliance/p4/integration-review21.json). Repository-mode and ProcessDoctor evidence remains separately scoped to the owner’s recorded runs. Review, merge and exact merged-main smoke outcomes are recorded on [PR #12](https://github.com/ash-01xor/FrostFlow/pull/12). Earlier checkpoints below retain their original dates and scope.

**P4 reintegration (replacement PR, branch `feat/p4-foundation-integration`):** Abid’s P4 from [PR #10](https://github.com/ash-01xor/FrostFlow/pull/10) was reverted by PR #11 so foundation [PR #9](https://github.com/ash-01xor/FrostFlow/pull/9) could merge first (merged as `ae941cc`). [PR #12](https://github.com/ash-01xor/FrostFlow/pull/12) restores P4 by reverting `31ad23b` and adapts it to the [foundation contract v1](../docs/P4-FOUNDATION-HANDOVER.md); see [sequencing](../docs/P4-INTEGRATION-SEQUENCING.md#checkpoint-4--replacement-p4-pr) and the [P4 guide](../docs/P4-QUALITY-INVESTIGATION.md). Review, merge and post-merge verification outcomes are recorded on PR #12.

**Ashvanth owns P1; Rahul owns P2; Arunima Garg owns P3.** Abid owns P4 and Daniel owns P5; Sai Shashank Maktala owns P6 and shared external integration ownership remains open. Use `TODO`, `IN PROGRESS`, `IMPLEMENTED / VERIFY`, `BLOCKED` and `DONE`. A checked box records only the specific item described.

| Work | Owner | Status | Main dependency |
| --- | --- | --- | --- |
| P1 Shipment preparation | Ashvanth | IMPLEMENTED; engine and external conformance verified | Teammate review |
| P2 Transport and delivery | Rahul | IMPLEMENTED; live acceptance and local DCR pass; review-18 manual external conformance passed | [Detailed handover](../docs/COLD-CHAIN-INTEGRATION-AGREEMENT.md#p2-and-p3-development-handover); P4 consumer merged in PR #12 |
| P3 Receiving and admission | Arunima Garg | IMPLEMENTED; integrated with P2; four live scenarios/local DCR pass; review-18 manual external conformance passed | P2 arrival + P4 shared hold |
| P4 Quality investigation (DCR) | Abid | IMPLEMENTED; merged in PR #12; review fixes pass offline; combined live run `ed88d16d` passed on the final code with P4 in the dcrgraphs.net repository; owner ProcessDoctor check passed for that log | [P4 guide](../docs/P4-QUALITY-INVESTIGATION.md); PR #12 merged as `70d1a1b`; merged-main smoke passed |
| P5 Corrective fulfilment | Daniel | IMPLEMENTED; offline/live/local replay/external conformance pass | [P5 guide](../docs/P5-CORRECTIVE-FULFILMENT.md); PR #14 merged as `b3f6102`; latest integrated-main smoke passed at `35d2d0b` |
| P6 Supplier claim resolution | Sai Shashank Maktala | IMPLEMENTED / VERIFIED; 140-test suite and five independent live claims pass; original external conformance retains its recorded scope | PR #15 merged as `35d2d0b`; exact merged-main smoke passed |
| Shared CEP, APIs, simulator, logging | Unassigned (cooling loop: Abid/Rahul per issue #17) | PARTLY IMPLEMENTED | ETA/container CEP, weather, shared durable journals and JSON/XES exports implemented; cooling loop: P4 side implemented offline, simulator/Siddhi side pending; external simulator integration pending |
| Compliance and submission | All; coordinator unassigned | TODO | Models + real execution logs |

## Merged P4 checkpoint — 21 November 2026

PR #12 merged as `70d1a1b` after user authorization and independent review. All 118 offline tests and four real P4 live scenarios passed before merge. The exact merged-main admission/rejection smoke passed with exported engine completion and warehouse/receiving deltas of -2/+1; see [historical record](../compliance/historical/merged-main-70d1a1b.json). The smoke uses labelled test dispositions; the separate P4 investigation record identifies local DCR execution. README now presents current capabilities and entry points; detailed contracts and history remain in their guides. At this earlier checkpoint, P5/P6 and automatic cooling/recovery were planned; the later P5 result is recorded above.

## Foundation-first review checkpoint — 18 November 2026

- [x] Fast-forward shared `feat/pre-p4-foundation` to `8726a49`, preserving Arunima’s three evidence screenshots; no rebase or force-push.
- [x] Prepare `revert/p4-until-foundation` from `af3c68c`; removal commit `31ad23b` reverts merge #10 with parent 1 and restores the tree to `552945d` before intentional documentation updates.
- [x] Restored baseline: 57 offline regressions pass; Compose validation passes. See [verification record](../docs/P4-INTEGRATION-SEQUENCING.md).
- [x] [Revert PR #11](https://github.com/ash-01xor/FrostFlow/pull/11) merged as `f475904` after explicit user approval; preserve removal commit `31ad23b`.
- [x] Merge main into the shared foundation branch (`4b09482`), retaining history and Arunima's screenshots; add the [P4 handover](../docs/P4-FOUNDATION-HANDOVER.md) and eight producer-checked fixtures (`8d13c3e`). One empty-default P4 URL; temperature remains CLI 18.
- [x] Fresh verification at `8d13c3e`: 82 offline regressions; sequential P1/P2/P3; four container scenarios; six local Siddhi weather comparisons; closing outage/restore; 21-second restart; exact inventory/end states and local replay. Three legacy P4 contract failures reproduced offline with substituted DCR. See [18 November report](../compliance/foundation-review-18.json).
- [x] User completed manual ProcessDoctor checks for the review-18 logs: P1 3/3, P2 3/3, P3 4/4 and container 5/5 conforming variants; zero violations, coverage 100. [Screenshots and model/log hashes](../compliance/foundation/review-18/processdoctor.json) recorded.
- [x] PR #9 reviewed and merged as `ae941cc`.
- [ ] After approved foundation merge, rebuild merged main and record integrated smoke at its exact revision. Abid's replacement PR (below) restores and adapts P4, preserving option 18 for temperature simulation and using option 19 for investigation.

P4’s original models, implementation and evidence remain accessible in [PR #10](https://github.com/ash-01xor/FrostFlow/pull/10) at `45f654b` / merge `af3c68c`. They are historical evidence, not verification of the combined foundation/P4 system. P5/P6 were later work at this historical checkpoint. Pre-existing registries and named engine volumes are retained; fresh matched `p1-18`, `p2-18` and `p3-18` datasets hold this review execution. The final stack uses consumed `p3-18` data; see the run guide before resuming.

## Pre-P4 foundation checkpoint — 17 November 2026

- [x] Record authority/checklist, approved demo profile and v1 future-P4 assessment boundary.
- [x] Add Open-Meteo current conditions, bounded asynchronous calls, cache/eligibility and sanitized replay evidence.
- [x] Persist container sessions/scenarios through receipt, including after arrival; restart-safe silence watchdog.
- [x] Ordered Siddhi evaluation, coalesced/versioned holds, urgent combined-risk priority and terminal rejection guards.
- [x] Explicit P3 monitoring finalization before transactional admission; receipt acknowledgement remains independent.
- [x] Complete integrated foundation verification and local replay; record exact revisions, input modes, images and artifact hashes in the [verification report](../compliance/foundation-verification.json).
- [x] Review-18 foundation external conformance completed manually by the user; see the current checkpoint above. Earlier agent browser access failed.
- [ ] Review integration PR, then merge only with explicit authorization; smoke-test merged main afterward.
- [x] Abid restores and adapts the PR #10 consumer/investigation in the replacement PR after the foundation merge (`ae941cc`). P5/P6 were later work at this historical checkpoint.

The repair checkpoint below is historical; its reports identify the original revisions. Foundation results and external pending work are recorded separately.

## Current repair checkpoint — 17 November 2026

- [x] [P2 PR #4](https://github.com/ash-01xor/FrostFlow/pull/4), Rahul: preserve terminal rejection and its incident; later alerts remain evidence with `incidentOpened: false`. Regression proves transport decisions remain usable while release/admission stay forbidden.
- [x] [P2 duplicate-ETA finding](https://github.com/ash-01xor/FrostFlow/pull/4#discussion_r4599556202), Rahul: deduplicate by shipment, journey and event identity before the Siddhi sequence. Isolated live Siddhi regression passes alongside the existing freshness and sequence checks.
- [x] [P3 PR #5](https://github.com/ash-01xor/FrostFlow/pull/5), Arunima Garg: bounded loading-readiness wait; regression covers wrong state, pending command and useful timeout. Preserve the application guard, P3 BPMN corrections and all compliance deliverables.
- [x] Reconcile P2 into P3, resolving shared documentation. Offline suite: 57 passing tests; all three BPMN models lint cleanly.
- [x] P1 regression, three P2 live acceptance journeys and four P3 live scenarios pass. New P2/P3 logs pass local DCR replay; forbidden admission is rejected. Exact warehouse/receiving stock and all four receipt/inspection retries verified (see reports, tested checkout `89dfd26`).
- [x] Inspect the three stopped test stacks; preserve their logs and registry/engine data, remove their 15 obsolete containers without deleting volumes. One replacement verification stack uses fresh paired data for each suite.
- [ ] Refresh external ProcessDoctor checks for regenerated P2/P3 logs. Browser access host is unavailable in this session; previous log results are historical, not current evidence. Compliance owners: Rahul (P2), Arunima Garg (P3).
- [x] Merge #4 (`e36b0ac`), update #5 against main and inspect its remaining diff, then merge #5 (`ad47a3c`). Merged-main admission and hold/rejection smoke passed with engine end events and exact inventory deltas. Existing review records retained; three defect threads resolved. User explicitly approved merging with fresh ProcessDoctor checks pending. See `compliance/historical/merged-main-ad47a3c.json`.
- [x] Abid: PR #10 DCR investigation restored and adapted to the foundation in the replacement PR (see P4 section). Test-harness quality dispositions remain stand-ins.

Agent context and documentation maintenance are tracked in [PR #7](https://github.com/ash-01xor/FrostFlow/pull/7); implementation and verification results remain in their original reports.

The sections below retain dated historical checkpoints and original P1–P6 work. Historical results apply only to the named revision/log; current pending actions refer to the latest evidence above. Current commands and measured results are in [RUNNING.md](../docs/RUNNING.md) and the P2/P3 verification reports.

## Foundation and access

- [x] Read course specification, proposal, signed contract and both existing guides.
- [x] Track Markdown guides and shared project notes; ignore secrets, PDFs and runtime data.
- [x] Add project README, shared task board and consolidated run/verification guide.
- [x] Daniel confirmed as P5 owner; Abid owns P4 and P1/P2/P3 owners are recorded above.
- [ ] Team reviews scope/contracts and assigns P6 and shared integration owners.
- [x] Run the P1 engine check and restart assertions; record results in [the run guide](../docs/RUNNING.md#automated-verification).
- [x] P4 now executes real local/repository DCR cases; access instructions are in the P4 guide, without credentials.
- [x] Historical live TomTom requests and sanitized provider evidence recorded; see P2 report for the tested revision.
- [x] Independently call TomTom (HTTP 200) and Open-Meteo current conditions on 17 November 2026; sanitized foundation evidence records exact modes and timestamps.
- [x] Import the P1 DCR graph and real three-trace log into processdoctor.compute.dtu.dk and run conformance checking.

P1 external conformance has passed. The later P4 checkpoint above records its executable local/repository DCR investigation, API access check and scoped external conformance results.

## Dataset decision — 1 November 2026

- [x] Adopt five chilled items: milk, yoghurt, fresh cheese, chicken breast and prepared salad; 20 crates each, 12 retail packs per crate.
- [x] Implement product/batch-specific stock, single-product/batch shipments and traceable handoffs/activity exports.
- [x] Update menu controls, manual scenario, API contract and offline regression coverage.
- [x] Preserve legacy pooled datasets; require fresh matching registry/engine data for version 3.
- [x] Re-run P1/P2 engine scenarios on fresh five-product data, verify persistence after stack recreation and refresh reviewed evidence/source hashes.
- [x] Approved pre-P4 plan supplies numeric demonstration assessment settings; snapshot per shipment. See [foundation contract](../docs/PRE-P4-FOUNDATION.md).

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
- [x] Add a TomTom traffic-ETA adapter. Local estimates apply only with no configured key; configured-provider failure yields unavailable ETA, never an estimated fallback.
- [x] Add the correlated `DeliveryRiskDetected` BPMN branch and deduplicate unresolved alerts.
- [x] Add delay acceptance and alternative-route decisions; require matching simulator acknowledgement before recording route change.
- [x] Add CLI controls for test risk, delay acceptance and route approval.
- [x] Implement the local P3 receiver envelope/idempotency response and verify offline retries start P3 once; external receiver acceptance remains a follow-up only if the P3 endpoint is replaced.
- [x] P2 completes only from P3's receipt: the manual P2 receipt is refused while P3 receiving is enabled or once P3 has started (2026-11-15; 51 offline tests; `check_p2_siddhi.py` and `check_stage1.py` both pass, recording receipt through P3).
- [x] Verify automatic arrival and route updates for two independent shipments (`stage_tests/check_p2_siddhi.py`, 2026-11-14; registry/engine evidence, Inspected).
- [x] Verify TomTom ETA requests and record their source/response (2026-11-15, Rahul's personal developer key; 15/15 HTTP 200 in a passing live check; `compliance/p2/tomtom-responses.jsonl`). Re-check with the team key once one exists.
- [x] Live-verify local Siddhi receives telemetry and emits one shipment-correlated risk alert per route revision (since `d613960`: after three consecutive fresh late ETAs); follow [the short-deadline walkthrough](../docs/RUNNING.md#live-siddhi-risk-and-route-command-walkthrough).
- [x] Live-verify an approved route command passes through Siddhi to the local scripted simulator, receives acknowledgement, then changes GPS/ETA; a physical/external simulator remains a separate integration.
- [x] Rehearse the risk/decision/route controls from the CLI against the live stack.
- [x] Rerun the offline suite under Python 3.12 (40 tests OK on 2026-11-14, run inside the service image because the local Python lacks `grpc`).
- [x] Deliver P2 DCR rules and real execution logs separately from P1. Current run `131337` has three locally valid/accepting traces; six synthetic violations are rejected by the offline suite. Earlier runs/check counts remain in Git history.
- [x] Historical ProcessDoctor check: imported the P2 graph and P3-integrated execution log from run `100628` into processdoctor.compute.dtu.dk: 3 conforming.
- [ ] Run ProcessDoctor for the current P2 log (run `131337`, tested checkout `89dfd26`); record its exact log hash/result in `compliance/p2/verification.json`. Retain earlier screenshots as historical until a current screenshot is captured.
- [x] Historical live P2 acceptance (`check_p2_siddhi.py`, run `130506`, commit `d613960`): independent journeys, automatic arrival, dispatcher outcomes, P3 notification and receipt-gated release, quality hold and release.
- [x] PR review (a): routes are corridors to the recipient's location; a change keeps the destination and incompatible routes are rejected; regression and live check assert arrival at the original destination.
- [x] PR review (b): the decision worker returns `routeId`/`routeRevision`; the live check confirms Operate's exported variables match the shipment API.
- [x] PR review (d), extended by the duplicate-event repair: Siddhi alerts only after three distinct consecutive fresh late ETAs within 180 s; `check_siddhi_risk_rule.py` covers isolated, stale and gap-broken readings.
- [x] PR review (c), P2 side: accepting a delivery risk persists a `PENDING` quality hold and a durable P4 handoff in one transaction; admission stays blocked until the current incident is released (offline and live).
- [x] **P4 dependency (Abid):** the queued handoff (`p4-http` outbox, `P4_INCIDENT_URL`) is consumed by the P4 DCR investigation in the replacement PR, which issues dispositions with the assessed evidence version. The stand-in `POST /integrations/p4/quality-status` / CLI option 17 remains for P3-only checks.
- [x] Current foundation TomTom request succeeds (HTTP 200, 17 November 2026); earlier HTTP 401 observations remain historical.

Historical state (commit `d613960`; superseded by the repair checkpoint above):
- **Implemented and verified:** the Siddhi persistence rule (three consecutive fresh late ETAs), destination-preserving route changes, Camunda route variables, the P3 receipt gate, and the delivery-risk quality hold.
- **P2 DCR:** rules and log are delivered; local replay passes. ProcessDoctor needs a re-run for the latest log.
- **Outstanding:** a TomTom re-check with a valid key and the external simulator (team decision). The P4 consumer of the hold handoff is in replacement PR #12.

See the [evidence table](../docs/RUNNING.md#evidence-what-was-verified-and-where). P2/P3 fixes and integration were merged through [#4](https://github.com/ash-01xor/FrostFlow/pull/4) and [#5](https://github.com/ash-01xor/FrostFlow/pull/5); smoke tests/evidence through [#6](https://github.com/ash-01xor/FrostFlow/pull/6). External integration owners and P4–P6 follow-up links still require team coordination. See the [file-by-file handover](../docs/COLD-CHAIN-INTEGRATION-AGREEMENT.md#p2-and-p3-development-handover).

## P3 — Receiving and stock admission

- [x] Start once from P2 arrival; record physical receipt and acknowledge P2 through the durable outbox.
- [x] Inspect goods; transactionally gate admission on inspection and current quality status.
- [x] Block admission during PENDING and after REJECTED; require current-incident RELEASED disposition.
- [x] Record admission/rejection outcome and preserve replacement links in activity history.
- [x] Add CLI actions for physical receipt, inspection and receiving-stock view.
- [x] Prove receipt is distinct from admission and stale releases cannot clear a newer hold in offline tests.
- [x] Fix P3 BPMN lint labels and fake joins; pinned Camunda BPMN linter passes; regenerate receiving PDF.
- [x] Run four real Zeebe scenarios for normal admission, hold/release, inspection rejection and quality rejection; capture neighboring P1/P2/P3 events in `compliance/p3/execution-log.xes`.
- [x] Add P3 DCR graph, local replay and forbidden-admission check in `compliance/p3/`; all four traces conform locally.
- [x] Rehearse P3 message-start/catch flow in a live isolated Zeebe stack (run `85bd817a`, 2026-11-16).
- [x] Historical P3 ProcessDoctor check reported 4/4 conforming trace variants for the earlier log; preserved as `historicalProcessDoctor` in the report.
- [ ] Refresh ProcessDoctor for the current P3 log (run `9d1cd66e`, tested checkout `89dfd26`) and record the actual result and log hash.

## P4 — Quality investigation (knowledge-intensive DCR process)

Owner: Abid. Replacement PR on `feat/p4-foundation-integration` (restore = revert of `31ad23b` on `ae941cc`). Details: [P4 guide](../docs/P4-QUALITY-INVESTIGATION.md).

- [x] DCR graph (16 activities, 3 groups, 83 flat relations) with flexible evidence order and required assessment/disposition; 3 425 reachable markings, no deadlock, no dead activity.
- [x] Consume `DeliveryRiskDetected` (legacy and v1) and `AssessmentRequested` (four reasons); persist the full request; acknowledge `{deliveryId, status}`; exact retries are duplicates, conflicting reuse fails; requests not matching the foundation outbox are refused.
- [x] The foundation holds before the handoff; if DCR is unavailable the request is kept and case opening retried.
- [x] One incident collects all its evidence versions (`AddAlertEvidence` makes reassessment due); closed/`REJECTED` incidents only record later evidence.
- [x] `AssessEvidence` records the evidence version seen before DCR executed it; dispositions use it as `expectedEvidenceVersion`; refused (stale) dispositions are recorded unapplied and require reassessment; transient failures keep one durable decision identity.
- [x] PR #12 first review (changes requested, 19 November): assessment/version race, release before DCR confirmation, lost-response recovery, conflicting officer retries and the empty `P4_INCIDENT_URL` default fixed with regressions; stale docs reconciled. See [sequencing](../docs/P4-INTEGRATION-SEQUENCING.md#first-review-of-pr-12-19-november).
- [x] Combined live run repeated on the fixed code: `d2558070` (`3576812`, fresh `p4-03` data) and, after the second review pass, `ed88d16d` (`35d154a`, fresh `p4-04` data, owner's Mac) passed with P4 executing in the dcrgraphs.net repository (graph `2015919`, simulations `2110252`–`2110255` for the final run).
- [x] ProcessDoctor (owner, 20 Nov): `ed88d16d` log against `compliance/p4/rules.xml` 4/4 conforming, 0 violations, coverage 100.
- [x] Enforce requested-check and cooling-answer obligations and prevent premature closure (DCR refuses with 409).
- [x] Offline proof on new monitored shipments: release after receipt during a hold, replace, return, newer-evidence reassessment, duplicate delivery, DCR outage recovery, handover fixtures in order, legacy messages. CLI 18 temperature / 19 investigation.
- [x] Combined foundation/P4 live run `fd021988` (`check_p4_investigation.py`, macOS arm64, fresh `p4-02` data, commit `95e4a85`, local engine); now historical (`compliance/p4/historical/`), like PR #10 run `900821e9`.
- [x] dcrgraphs.net Active Repository API check (`check_dcr_repository.py`, graph `2015919`, simulation `2109421`): every step matches the local engine; early close refused. Nesting groups listed by the API are now ignored.
- [x] ProcessDoctor (owner, 19 Nov): live log `fd021988` (now historical) against `compliance/p4/rules.xml` 4/4 conforming, 0 violations, coverage 100. The compliance model is the executed graph without roles: ProcessDoctor reports cost Infinity against the role-assigned graph.
- [x] Full-stack run with P4 in repository mode: live runs `d2558070` and `ed88d16d`.
- [x] P5 consumes applied `REPLACE`/`RETURN` dispositions transactionally; `RELEASE` creates no corrective work.
- [ ] Cooling loop ([issue #17](https://github.com/ash-01xor/FrostFlow/issues/17), owners Abid/Rahul, verification Ashvanth): agree the [draft contract](../docs/P4-COOLING-CONTRACT.md) with Rahul (and Arunima for the P3 boundary).
- [x] P4 side implemented on `feat/p4-cooling-loop`: durable command per `RequestCoolingAdjustment` (outbox, stable `commandId`), receipt vs application kept apart, simulator result receiver, timeout → failure, recovery receiver that re-checks stored readings, labelled manual-test input; 10 offline regressions in `stage_tests/test_p4_cooling.py` (150 tests pass).
- [ ] Rahul: simulator applies commands to future readings and returns results; Siddhi recovery query and durable delivery (issue #17).
- [ ] Live run with real acknowledgement/recovery, then refresh P4 evidence (log, local replay, ProcessDoctor) — pending both sides.

## P5 — Corrective fulfilment

- [x] Reject unauthorised corrective work; consume applied P4 replacement/return decisions once, with a durable authorization/start outbox.
- [x] Create one linked full replacement, call co-deployed P1 and wait for P3 receiving outcome and original collection. Preserve parent identity through a replacement’s own P4/P5 case.
- [x] Track booking and full original collection acknowledgement; check collector/reference and P3 physical receipt/rejection. No rejected-goods inventory credit.
- [x] Guard successful completion on collection plus replacement admission; rejection/cancellation fails fulfilment. Offline regressions and live runner check these guards.
- [x] Add BPMN/PDF, API/menu option 20, DCR compliance rules, forbidden traces and reproducible live evidence tooling.
- [x] Final-code live run `9347c2c8`, matching source/image hashes, five local replay traces, 4/4 external conforming variants and a separate expected negative result recorded.
- [x] Branch `P5` published through [PR #14](https://github.com/ash-01xor/FrostFlow/pull/14); Daniel merged it as `b3f6102` on 22 November 2026.
- [x] Address and recheck PDF clipping and Daniel’s ownership; corrections are included in the merge.
- [x] Verify integrated main `35d2d0b` with fresh matched `smoke-r1` data: admission/rejection, exported engine ends, exact stock deltas and a P6 settlement. Full P5 paths retain their separate verified source/revision.
- [x] Temperature/combined-risk and persistent ETA patterns already implemented by the foundation/P2.
- [ ] Coordinate post-cooling recovery CEP with P2/P4 and the unconfirmed shared integration owner.
- [x] Open-Meteo current evidence already implemented by the foundation and supplied to P4.
- [ ] Coordinate cooling-command forwarding and external simulator integration; ownership unconfirmed.

## P6 — Supplier claim resolution

- [x] Require explicit claimable loss; link original shipment and incident; deduplicate claims.
- [x] Assemble evidence; simulate submission and settlement/rejection response.
- [x] Prevent claim closure before final outcome.
- [x] Journal P6 through shared `Registry.event`, export real JSON/XES and record scoped local/external conformance evidence.
- [x] Recheck the review fixes: long incident IDs, complete P4 graph validation and corrected run instructions; 140 tests and five live claims pass.
- [x] PR #15 merged as `35d2d0b`; rebuild and exact merged-main smoke pass, including a live P6 settlement and unchanged rejected goods/stock. Earlier evidence is preserved.

## Shared integration and demonstration

- [x] Implement/document the current ETA rule: three distinct consecutive fresh late observations, 30-second freshness and 180-second window.
- [x] Document approved temperature assessment cadence, persistence, gap limits, freshness and units.
- [ ] Agree cooling/recovery settings as part of real P4 work (proposed in the [draft contract](../docs/P4-COOLING-CONTRACT.md), not yet agreed).
- [x] Implement P1–P3 envelope validation, transactional handoffs and event/delivery/command deduplication.
- [x] Extend transactional contracts/retry checks to real P4 and P5.
- [x] Extend transactional start/response contracts and exact/conflicting retry checks to P6; independent live claims verify their expected outcomes.
- [x] Implement persistent distinct-ETA delivery risk in Siddhi; isolated-rule and live P2 checks pass.
- [x] Implement temperature excursion and weather-supported combined assessment in Siddhi with durable ordered acknowledgements.
- [ ] Implement post-cooling-command recovery with real P4.
- [x] Verify the P2 dispatcher-command/Siddhi/scripted-simulator/telemetry loop.
- [ ] Demonstrate the planned P4 cooling-command/recovery loop and any external simulator connection.
- [x] Preserve source/input-mode and evidence timestamps for implemented P1–P3 paths.
- [x] Extend provenance to container assessment and Open-Meteo current conditions.
- [x] Extend provenance to real P4 and P5 activities, including manual collection and system handoffs.
- [x] Record P6 actor, UTC occurrence/recording times, input modes and shipment/incident/claim references in the shared journal.
- [ ] Extend provenance to automated cooling/recovery when that loop is implemented.
- [x] Check unavailable/stale ETA observations, duplicate events/commands and independent journeys in the P1–P3 regressions/live runs.
- [ ] Verify missing temperature/recovery data and full new-incident-after-release behavior with the real P4 investigation.
- [x] Rehearse current P1–P3 delivery and delay paths, including admission and hold/release/rejection.
- [ ] Rehearse the complete three-minute P1–P6 demonstration, including quality investigation, corrective handling and an explicitly eligible claim. Automated regression runs do not constitute a human rehearsal.
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

See [Abid’s P4 foundation handover](../docs/P4-FOUNDATION-HANDOVER.md) for intake, durable acknowledgements, assessed evidence versions, fixtures and reintegration requirements.
