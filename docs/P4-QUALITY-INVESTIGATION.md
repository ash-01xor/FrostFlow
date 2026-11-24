# P4 — Quality incident investigation (DCR)

**Owner:** Abid Hasan · **Contract:** [foundation handover v1](P4-FOUNDATION-HANDOVER.md) · **Model:** [`dcr/p4_quality_investigation.xml`](../dcr/p4_quality_investigation.xml) ([PDF](../dcr/p4_quality_investigation.pdf), [AS-IS BPMN PDF](../dcr/p4_as_is.pdf)) · **Code:** [`deployer/quality.py`](../deployer/quality.py), [`deployer/dcr_client.py`](../deployer/dcr_client.py) · **Evidence:** [`compliance/p4/`](../compliance/p4/)

The original implementation (PR #10) was reverted by PR #11 so that the monitoring foundation (PR #9) could merge first. This replacement branch restores it by reverting removal commit `31ad23b` on merged main `ae941cc` and adapts it to the foundation contract; see [sequencing](P4-INTEGRATION-SEQUENCING.md#checkpoint-4--replacement-p4-pr).

## What P4 does

When the foundation places a quality hold (a delivery risk, a container temperature excursion, combined transport risk or missing/unavailable monitoring evidence), it sends one durable message to P4. P4 opens an investigation case in DCR. The quality officer collects evidence in whatever order the case needs while DCR enforces the rules that must always hold. The officer's decision is the only thing that lifts or confirms the hold: **release** lets P3 admit the goods (after monitoring finalization and a passed inspection); **replace** or **return** rejects them (P5 corrective fulfilment consumes the applied decision; see the [P5 guide](P5-CORRECTIVE-FULFILMENT.md)).

The AS-IS process (assumed coursework scenario, not interview data) is in [`dcr/p4_as_is.pdf`](../dcr/p4_as_is.pdf).

```mermaid
sequenceDiagram
    participant M as Monitoring + Siddhi
    participant P2 as P2 transport (Zeebe)
    participant R as Registry (SQLite)
    participant P4 as P4 investigation (DCR)
    participant P3 as P3 receiving (Zeebe)
    participant P5 as P5 corrective fulfilment (Zeebe)
    M->>R: container evaluation -> assessment_hold (PENDING, evidenceVersion n)
    P2->>R: delivery risk -> assessment_hold
    R-->>P4: outbox POST P4_INCIDENT_URL {"event": ...}, Idempotency-Key = deliveryId
    P4-->>R: {"deliveryId", "status": "accepted" or "duplicate"} (request persisted first)
    P4->>P4: OpenIncident (new) or AddAlertEvidence (assessment due again)
    P3->>R: receipt releases vehicle; finalization; admission: PENDING -> ON_HOLD
    Note over P4: officer reviews; AssessEvidence records assessed version n
    P4->>R: accept_quality_status(RELEASED/REJECTED, incident, expectedEvidenceVersion = n)
    R-->>P3: QualityStatusChanged -> admit or reject
    P4->>R: journal applied disposition + save decision
    R-->>P5: atomic P5 case/outbox for REPLACE or RETURN
```

## Rules (R1–R9) and the graph

16 activities in three nesting groups (Investigation, Requests, Decision), 34 drawn arrows = 83 flat relations, only the five lecture relations (condition, response, include, exclude, milestone). Initially `OpenIncident` is pending; acknowledgement, failure, recovery evidence and the additional check are excluded until a request includes them. The graph is unchanged from PR #10.

| Rule | Meaning | Activities (role) |
| --- | --- | --- |
| R1 | The integration service opens the incident once; nothing happens before | `OpenIncident` (Integration) |
| R2 | Every further foundation message for the incident is added as evidence and requires a new assessment | `AddAlertEvidence` (Integration) |
| R3 | Reviews in any order, repeatable; weather and route/ETA are optional | `ReviewTemperatureLog`, `ReviewWeatherData`, `ReviewRouteAndETA` (QualityOfficer) |
| R4 | Assessment only after the temperature log was reviewed | `AssessEvidence` (QualityOfficer) |
| R5 | A cooling request must be answered (acknowledged or failed); no closure while outstanding | `RequestCoolingAdjustment` (QualityOfficer), `RecordCoolingAcknowledgement`, `RecordCoolingFailure` (Integration) |
| R6 | Recovery evidence only after a successful acknowledgement; it must be assessed and never releases goods | `RecordRecoveryEvidence` (Integration) |
| R7 | A requested check must be completed by the receiver; while outstanding no assessment, decision or closure | `RequestAdditionalCheck` (QualityOfficer), `CompleteAdditionalCheck` (Receiver) |
| R8 | After assessment exactly one decision; afterwards no new checks or cooling requests | `DecideRelease`, `DecideReplace`, `DecideReturn` (QualityOfficer) |
| R9 | Every incident is assessed and closed; closing needs a decision; nothing after closing | `CloseInvestigation` (QualityOfficer) |

All **3 425** reachable markings can reach an accepting marking and every activity can occur (`stage_tests/test_p4.py::P4Graph`). Cooling acknowledgement/failure and recovery evidence come from the simulator and Siddhi under the [draft cooling contract](P4-COOLING-CONTRACT.md) when `P4_COOLING_COMMAND_URL` is set; otherwise (the current default, because the simulator/Siddhi side does not exist yet) they are entered manually and labelled `manual-test`. The graph is unchanged.

## Contract v1 behaviour (enforced by the service)

| Boundary | P4 behaviour |
| --- | --- |
| Intake | `POST /integrations/p4/incident` accepts `DeliveryRiskDetected` (legacy, or with the v1 `assessment` extension) and `AssessmentRequested` (`temperature-excursion`, `combined-transport-risk`, `container-telemetry-unavailable`, `monitoring-evaluation-unavailable`). The body must be the stored foundation outbox message (`{"event": ...}`, `Idempotency-Key` = `event.deliveryId`). |
| Durable acknowledgement | The full request and its deduplication identity are persisted before P4 answers `200 {"deliveryId", "status": "accepted"}`. An identical retry answers `"duplicate"` without a second case or effect; changed content with the same ID → 409; a body that does not match the foundation outbox → 409; invalid envelope → 400. |
| DCR unavailable | The request is still acknowledged; the incident stays `DCR_UNAVAILABLE` and case opening is retried by the service's delivery loop. The hold is never removed by P4. |
| Evidence | Every message is kept in full on the incident (`evidence[]`): reason, priority, evidenceVersion, profile/session/observation references, ETA/weather context, `missingEvidence`, provenance and timestamps. The incident tracks the highest evidence version received and escalates priority to `urgent` once seen. |
| Assessment | `AssessEvidence` records the evidence version the request saw **before** DCR executed it (`assessedEvidenceVersion`). Evidence that intake commits meanwhile is not labelled assessed: the incident gets `reassessmentRequired` and the journal entry `newerEvidenceArrived`. A later message executes `AddAlertEvidence`, which makes a new assessment due in DCR and blocks the decision. While a message is accepted but not yet in DCR (`pendingSystemEvents`), `AssessEvidence` and decisions are refused (409); the request first tries to bring it into DCR. |
| Disposition | Three steps: (1) `Registry.check_quality_status` checks the disposition read-only (current incident, `expectedEvidenceVersion` = the assessed version, allowed transition); (2) DCR executes the decision; (3) only then `Registry.accept_quality_status` (the operation behind `POST /integrations/p4/quality-status`) applies it. `RELEASE → RELEASED`, `REPLACE/RETURN → REJECTED`. Quality permission never exists before DCR has executed the decision: until step 3 succeeds the shipment stays `PENDING` and P3 keeps it on hold. |
| Stale or refused decision | If the registry refuses in step 1 (newer evidence, wrong incident, terminal rejection) or DCR no longer enables the decision, nothing is executed in DCR. If newer evidence reaches the registry between steps 1 and 3, the registry refuses in step 3; that DCR case already holds the decision and cannot take another, so the investigation continues in a new DCR case for the same incident (`previousDcrCases`), which needs a fresh assessment. Either way the attempt is journalled as `StaleDispositionRefused`, kept in `unappliedDecisions` (`executedInDcr` tells which case), and the officer must assess again (HTTP 409 with `reassessmentRequired`). The investigation cannot close as if a release happened. |
| Transient failure | A failed registry call or DCR call leaves one durable `pendingDecision` with a fixed `eventId`/`occurredAt`; the delivery loop retries it with the same identity (HTTP 503 to the officer meanwhile). The DCR attempt is recorded before the call. If DCR executed the decision but the response was lost (or the service stopped before journalling), the retry finds the decision no longer enabled and records it as executed (`reconciledAfterLostResponse`), then applies it in the registry. This relies on P4 being the only writer of its DCR cases: all DCR calls run under one lock, and an attempted decision is settled before any other change. A definite DCR refusal (HTTP 400/409/422, or the local engine refusing) is not taken as executed. The officer's other activities (`pendingActivity`, e.g. `CloseInvestigation`) and the system events (`OpenIncident`, `AddAlertEvidence`) are recorded before the DCR call too. A retry executes them again when DCR still enables them (executing a DCR event twice gives the same marking as once; `P4Graph` checks this for every reachable marking) and otherwise records them as already executed. Each is journalled once. |
| Officer request identity | With an `eventId`, the first request's activity, actor, reason and note are stored. An identical retry returns the original result (or finishes a decision still being applied); reuse with any different detail is 409. |
| Corrective handoff | After quality-status application, the disposition journal, saved P4 decision, one P5 case and `P5DispositionRecorded` outbox are committed together. A failed transaction leaves the pending decision recoverable; retry creates no duplicate corrective work. `RELEASE` and the quality-status test stand-in create none. P4 closure does not wait for P5. |
| Terminal rejection | Later evidence for a rejected incident (`qualityStatus: REJECTED`, `incidentOpened: false`) is recorded (`LateAlertEvidenceRecorded`) and never reopens admission. |
| Receiving | Physical receipt releases the vehicle while the hold stays; P3 waits for monitoring finalization; a P4 release never bypasses finalization or inspection. |
| Cooling loop ([draft contract](P4-COOLING-CONTRACT.md)) | `RequestCoolingAdjustment` creates one command (`P4:COOL:<incidentId>:<n>`) in the same transaction as its journal entry and, with `P4_COOLING_COMMAND_URL` set, a durable outbox message to the Siddhi bridge. Only while monitoring is active; one outstanding command per incident. The simulator's `APPLIED`/`FAILED` result executes `RecordCoolingAcknowledgement`/`RecordCoolingFailure`; no result by `expiresAt` fails the command (`acknowledgement-timeout`). A recovery evaluation is re-checked against the stored readings before `RecordRecoveryEvidence`; refused, late or not-recovered evaluations are journalled only. Recovery never changes quality status and DCR makes a new assessment due. Manual entry of these three activities is refused for integrated commands. |

The registry stays the authority for quality permission and admission. The test stand-in `POST /integrations/p4/quality-status` and CLI option **17** bypass P4; a later P4 decision on an incident they already decided is refused and recorded.

## DCR engine

| Mode (`/health` → `integrations.p4DcrEngine`) | When | Notes |
| --- | --- | --- |
| `dcr-repository` | `DCR_GRAPH_ID`, `DCR_USERNAME`, `DCR_PASSWORD` set (untracked `.env`) | Executes in the dcrgraphs.net Active Repository via its REST API. API check passed on graph `2015919` (simulation `2109421`): each step of the release path matches the local engine and an early close is refused. Nesting groups that the API lists as events are ignored. The full stack ran in this mode in live runs `d2558070` and `ed88d16d` (simulations `2110252`–`2110255`). |
| `local-dcr-engine` | Otherwise (default) | Executes the same XML with flat DCR semantics (identical to `compliance/check.py`); each case's marking is stored in the registry SQLite file. |

To use dcrgraphs.net: import `dcr/p4_quality_investigation.xml` as a new graph, copy `.env.example` to `.env`, fill in the three values and rebuild (`docker compose up --build -d --wait`). Never commit `.env`. Before switching the stack, check API access with `python3 stage_tests/check_dcr_repository.py`. It needs only the standard library and reads `.env`. It starts one repository simulation, walks the release path, compares enabled/pending activities with the local engine after every step, checks that an early `CloseInvestigation` is refused, and writes `runtime/p4-compliance/dcr-repository.json`. The owner's graph is dcrgraphs.net graph `2015919`.

Compose has exactly one `P4_INCIDENT_URL` entry. It now defaults to this compatible consumer (`http://communication:5001/integrations/p4/incident`); set `P4_INCIDENT_URL=` to leave handoffs pending as before.

## Compliance model and ProcessDoctor

`compliance/p4/rules.xml` is the executed graph without its role assignments. It has the same 16 events, 83 relations and initial marking (`stage_tests/test_p4.py` checks this). With roles in the model, ProcessDoctor reported every variant violating with cost Infinity, even when the log carried the DCR role in `org:resource` and `org:role` (`screenshots/processdoctor-model-with-roles.png`). The logs still record each step's role (`org:role`) and the performer (`actor`).

To repeat the check:

1. On processdoctor.compute.dtu.dk, choose **Conformance → Open Model → Open DCR Solution XML** → `compliance/p4/rules.xml`.
2. **Upload Log** → `compliance/p4/execution-log.xes` (4 traces, from live run `ed88d16d`).
3. Keep the threshold at 100 and click **Check!**. Expect 4 conforming, 0 violations.

## HTTP interface and CLI

| Request | Purpose |
| --- | --- |
| `POST /integrations/p4/incident` | Foundation outbox consumer (above). Internal, unauthenticated demo route. |
| `GET /quality/incidents/<incidentId>` | Incident, evidence, decision, unapplied decisions, the DCR case's enabled/pending activities and the registry's current evidence version (`registry.reassessmentNeeded`) |
| `POST /quality/incidents/<incidentId>/activities/<Activity>` | `{"actor", "reason" (decisions), "note", "eventId" (optional, idempotent external input; reuse with different details → 409)}`. 409 with `enabled` when DCR does not enable it; 409 with `reassessmentRequired` for a refused disposition; 409 while new evidence is still being added to the DCR case; 503 while a disposition is pending retry |
| `POST /quality/incidents/<incidentId>/sync` | Retry case opening, queued integration events or a pending decision |
| `GET /shipments/<shipmentId>/quality` | `qualityStatus`, `qualityIncidentId`, `evidenceVersion` and incident history |
| `POST /integrations/p4/cooling-result` | Simulator result for a cooling command (`Idempotency-Key` = `resultId`); see the [draft contract](P4-COOLING-CONTRACT.md#2-result-coolingcommandresult-simulator--p4) |
| `POST /integrations/p4/recovery` | Siddhi recovery evaluation (`Idempotency-Key` = `recoveryId`); see the [draft contract](P4-COOLING-CONTRACT.md#3-recovery-recoveryevaluated-siddhi--p4) |

Incident IDs contain `:` (`DELAY:...`, `ASSESS:<shipment>:<event>`); URL-encode them. CLI (`tools/demo.py`): **18** changes the simulated container scenario, **19** shows the investigation and executes an enabled activity, **17** is the stand-in that bypasses P4.

## Evidence and checks

Each result applies only to the revision it was produced on; later code changes need their own run.

| Evidence | Tested revision | Where | Status |
| --- | --- | --- | --- |
| Offline tests on new monitored shipments (real registry, Zeebe substituted, local DCR engine) | this branch's head | `stage_tests/test_p4.py` | PASS — release after receipt during a hold, replace, return, newer-evidence reassessment with a refused stale decision, duplicate delivery, DCR outage recovery, transient failures, handover fixtures consumed in order, legacy delay messages. Review fixes (PR #12): evidence arriving during `AssessEvidence`, no assessment/decision while evidence is being added, release not granted when the DCR decision fails, lost DCR response reconciled, definite DCR refusal, registry refusal after the DCR decision (new case), conflicting `eventId` reuse, empty `P4_INCIDENT_URL`, crash after the DCR decision and after the registry write, lost DCR answers for `CloseInvestigation`, `AssessEvidence` and `OpenIncident` |
| Cooling loop, P4 side (issue #17): integrated command, receipt vs application, duplicate/conflicting/wrong-session results, lost ack timeout, late ack without recovery, simulator failure and definite refusal, recovery re-checked against stored readings (before application, one reading, criteria, stale, above threshold, gap), receipt during an in-flight command, restart, labelled manual-test input | this branch's head | `stage_tests/test_p4_cooling.py` | PASS offline (simulator and Siddhi messages posted by the test). Not run live: the simulator/Siddhi side does not exist yet |
| dcrgraphs.net simulator, 4 manual runs + blocked actions | graph only (unchanged) | `compliance/p4/dcrgraphs-simulation-log.xes`, `compliance/p4/screenshots/` | PASS (run 4 intentionally partial) |
| Synthetic forbidden traces (9) | graph only | `compliance/p4/invalid-traces.json` | all rejected |
| Combined foundation/P4 live run (4 scenarios on monitored shipments; Zeebe 8.8.0, Siddhi, **P4 in the dcrgraphs.net repository**, graph `2015919`) | `35d154a` (final PR #12 code, both review passes) | `stage_tests/check_p4_investigation.py` → `compliance/p4/execution-log.xes` | PASS, run `ed88d16d` (454 s, fresh `p4-04` data, simulations `2110252`–`2110255`): release → admitted (2), replace/return → rejected (2); receipt released the vehicle while held in all four; monitoring finalized; forbidden actions refused; run 4 refused the decision until version 2 was reassessed; 4 traces valid and accepting |
| Earlier live run `d2558070` (P4 in the repository) | `3576812` (before the second review pass) | `verification.json` → `historical`; log in commit `52f5e6b` | PASS; historical only |
| Earlier combined live run `fd021988` (local DCR engine) | `95e4a85` (before the review fixes) | `compliance/p4/historical/fd021988-execution-log.xes`, `verification.json` → `historical` | PASS; historical only |
| PR #10 live run `900821e9` (pre-foundation, unmonitored, pre-v1 contract) | `f9a92ee` (PR #10 code) | `compliance/p4/historical/` | historical only; not evidence for this branch |
| dcrgraphs.net Active Repository API (`stage_tests/check_dcr_repository.py`) | `9eebc56` | `compliance/p4/verification.json` → `dcrRepositoryApi` | PASS on graph `2015919`, simulation `2109421`: 9 steps match the local engine; early `CloseInvestigation` refused (HTTP 400). Direct API check, not a full-stack run in repository mode |
| ProcessDoctor (owner, manual) for the current log `ed88d16d` | `35d154a` | `compliance/p4/screenshots/processdoctor-ed88d16d-conforming.png`, `verification.json` → `processDoctor` | PASS: against `rules.xml`, 4/4 conforming variants, 0 violations, coverage 100 |
| ProcessDoctor for the historical log `fd021988` | `95e4a85` | `compliance/p4/screenshots/processdoctor-conforming.png`, `verification.json` → `historical.fd021988ProcessDoctor` | PASS: against `rules.xml`, 4/4 conforming variants, 0 violations, coverage 100; does not cover the current log |

```bash
.venv/bin/python -m unittest discover -s stage_tests -p 'test_p4*.py' -v
.venv/bin/python compliance/p4/build_p4_evidence.py            # rebuilds verification.json
.venv/bin/python compliance/check.py compliance/p4/dcrgraphs-simulation-log.xes --model compliance/p4/rules.xml
```

The last command exits 1 only because simulator run 4 is intentionally unfinished.

**Live run** (fresh paired version-3 dataset, simulator on, Compose defaults, `GPS_TICK_SECONDS=15` so a delay risk can be raised in transit), following the [run guide](RUNNING.md#p4-investigation-live-run):

```bash
.venv/bin/python stage_tests/check_p4_investigation.py --base http://localhost:5001
.venv/bin/python compliance/p4/build_p4_evidence.py --live runtime/p4-compliance/evidence.json
```

1. **run1:** delay → cooling acknowledged, recovery evidence → release → admitted.
2. **run2:** container excursion (`AssessmentRequested`) → failed cooling → replace → rejected.
3. **run3:** delay → additional check → return → rejected.
4. **run4:** delay → assessed → new container evidence → decision refused → reassessment → release → admitted.

Every case checks that receipt released the vehicle while quality was still held, that monitoring finalized, that forbidden actions were refused (409) and the final P3 outcome.

## Still to do

- Cooling loop ([issue #17](https://github.com/ash-01xor/FrostFlow/issues/17)): agree the [draft contract](P4-COOLING-CONTRACT.md) with Rahul; his simulator/Siddhi side, a live run with real acknowledgement/recovery and refreshed P4 evidence remain. The external physical simulator remains future work. P6 supplier claims are implemented separately; see [the run guide](RUNNING.md#p6-supplier-claims).
- P5 now consumes applied `REPLACE`/`RETURN` decisions; its live/local/external results are in the [P5 guide](P5-CORRECTIVE-FULFILMENT.md). This branch’s P5 acceptance uses local DCR. Earlier full-stack repository-mode runs retain their recorded revisions above.
