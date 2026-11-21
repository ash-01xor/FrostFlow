# P5 — Corrective fulfilment

P5 is implemented on local branch `P5` for user review and publishing. Human process
ownership remains unassigned. It consumes applied P4 replacement/return decisions and
runs `CorrectiveFulfilment` in Camunda, alongside P1–P3. P6 claims and automatic
cooling/recovery remain future work.

The final-code live run **`9347c2c8`**, tested at **`d118870`**, passed four scenarios
and completed five P5 instances. All **136 offline tests** pass. Five real traces pass
local DCR replay; ProcessDoctor reports **4/4 conforming variants, zero structural
violations, coverage 100**. Exact code/model/log hashes, modes, engine exports and
limitations are in the [verification report](../compliance/p5/verification.json) and
[environment record](../compliance/p5/environment.json). Earlier P1–P4 reports retain
their original scope.

## Workflow and boundaries

1. P4 executes its DCR decision and applies `REJECTED` with the assessed evidence
   version. Its disposition transaction records one corrective case and queues
   `P5DispositionRecorded`. Release and the quality-status test stand-in create no
   corrective work. Historical finalized decisions are not backfilled.
2. P5 validates that exact authorization and waits for a manual arrangement. Both paths
   book full original-goods collection. Replacement also reserves one new shipment,
   journey and free vehicle atomically with acceptance; product, batch, quantity and
   recipient are copied from the original. Failed acceptance rolls back both records
   and reservation. Arrangement time cannot precede authorization.
3. The BPMN tracks collection and replacement in parallel. A deployment-bound call
   activity starts the existing P1 with the replacement shipment ID. Child variables
   cannot overwrite parent variables. P1 still requires processed loading and dispatch,
   or waits for stock/replenishment/cancellation. Existing handoffs start P2 and P3.
4. Physical original collection must match its processed booking, collector and full
   quantity, and follow P3 physical receipt/rejection. The P5 worker records collection;
   booking alone is insufficient. Collection credits neither inventory.
5. P3 atomically queues a replacement's actual admission/rejection event to P5. Dispatch,
   arrival and physical receipt alone cannot fulfil the replacement. The independent
   `replacementForCorrectiveId` survives any P4 incident/corrective case of its own.
6. The parallel join requires original collection plus the replacement result. Return
   needs no replacement. Admission yields `COMPLETED`; rejection/cancellation yields
   `FAILED`. The rejected goods remain blocked. New follow-up shipments require another
   explicit authorized arrangement; no supplier claim is created automatically.

Source: [BPMN](../bpmn/corrective_fulfilment.bpmn), [PDF](../bpmn/corrective_fulfilment.pdf),
[API](../deployer/corrective.py), [registry effects](../deployer/corrective_registry.py)
and [wire contract](COLD-CHAIN-INTEGRATION-AGREEMENT.md#p5-corrective-fulfilment-contract).
Camunda's [8.8 call activity contract](https://docs.camunda.io/docs/8.8/components/modeler/bpmn/call-activities/)
describes the deployment binding and variable mappings used here.

## Demo and API

Start the normal stack as described in [RUNNING.md](RUNNING.md). Use CLI **19** to
complete P4's investigation and choose `DecideReplace` or `DecideReturn`. Use CLI **20**
on the original shipment to view the case, arrange collection/replacement and later
acknowledge collection. The case waits for P3 physical receipt/rejection before accepting
collection. On a replacement, use its existing shipment ID with CLI **3**, **4**, **14**
and **15** for loading, dispatch, receipt and inspection through P1–P3.

`GET /corrective-fulfilments?originalShipmentId=<id>` finds the case;
`GET /corrective-fulfilments/<correctiveId>` shows its state, accepted inputs, engine key,
booking, original collection and replacement outcome. The full original goods identity
is available at `GET /shipments/<originalShipmentId>`.

For `POST /corrective-fulfilments/<id>/arrange`, replace these example IDs and UTC times:

```json
{
  "eventId": "p5-arrangement-001",
  "occurredAt": "2026-12-01T10:00:00Z",
  "actor": "fulfilment-planner",
  "collector": "demo-collector",
  "bookingReference": "BOOK-001",
  "scheduledAt": "2026-12-01T10:30:00Z",
  "replacement": {
    "shipmentId": "replacement-001",
    "journeyId": "journey-replacement-001",
    "vehicleId": "V-02",
    "routeId": "route-01",
    "deliveryDeadline": "2026-12-01T12:00:00Z"
  }
}
```

Omit `replacement` for a return. A replacement cannot change product, batch, recipient
or quantity. For `POST /corrective-fulfilments/<id>/collect`, send `eventId`, UTC
`occurredAt`, `actor`, matching `collector`/`bookingReference`, and the full integer
`quantityCollected`. This is a manual/simulated physical acknowledgement.

Both inputs return **202 after durable acceptance**. Poll the case with a bounded
timeout for processed effects; the CLI uses 30 seconds. States are `START_REQUESTED`,
`AWAITING_ARRANGEMENT`, `ARRANGEMENT_REQUESTED`, `IN_PROGRESS`, then `COMPLETED`/`FAILED`.
Identical ID/time/payload retries reuse the original result even after completion;
conflicting action or event-ID reuse returns 409. Invalid fields return 400, unknown
cases 404, and not-ready 503. Keep registry and engine data together. The existing
24-hour message-retention and uncertain-start recovery limits also apply to P5.

## Live verification commands

Inspect existing containers/volumes first and run at most one stack. The retained
`p5-01` and `p5-02` datasets are consumed; preserve them. Choose a new matching registry
and volume suffix for a new run (the example uses `p5-03`):

```bash
export COMPOSE_PROJECT_NAME=frostflow-p5 VERIFY_DATASET=p5-03 REGISTRY_DIR=./runtime/p5/p5-03
export P2_SIMULATOR_ENABLED=true GPS_TICK_SECONDS=2 TOMTOM_API_KEY= DCR_MODE=local
docker compose -f docker-compose.yml -f stage_tests/verification.compose.yml up --build -d --wait --wait-timeout 180
.venv/bin/python stage_tests/check_p5_fulfilment.py --base http://localhost:5001 --output runtime/p5-compliance/p5-03/evidence.json
.venv/bin/python compliance/p5/build_p5_evidence.py live runtime/p5-compliance/p5-03/evidence.json --output-dir runtime/p5-compliance/p5-03/replay
.venv/bin/python compliance/check.py runtime/p5-compliance/p5-03/replay/execution-log.xes --model compliance/p5/rules.xml
```

Stop the current project before switching datasets. To resume the retained final run,
use `VERIFY_DATASET=p5-02 REGISTRY_DIR=./runtime/p5/p5-02` with the same other variables;
do not rerun the fresh-data runner on it. The live runner refuses reused datasets/output
paths. The builder requires a complete passing live-run capture and matching engine
completion records, performs local replay, and refuses to overwrite existing outputs.
It does not synthesize successful execution logs from the forbidden traces.

The runner uses actual Siddhi excursions and local DCR decisions, simulated GPS and
container readings, local demo ETAs, Open-Meteo context and manual physical actions.
It verifies return, admitted replacement, P4-rejected replacement (including a separately
completed return of those goods), and cancellation after a 20-crate chicken original
naturally exhausts its batch. It checks exact retries/refusals, original rejection,
P1 child/parent engine identity and P2/P3/P5 end records. Warehouse stock falls by 25 to
75 crates; receiving inventory gains exactly one yoghurt crate. No rejected goods are
credited and both vehicles are released.

The [DCR rules](../compliance/p5/rules.xml) project real P4 dispositions and P5
authorization into branch-specific labels while preserving journal IDs/times. Failure
is a conforming outcome when collection and the negative replacement result are
recorded. The [live XES](../compliance/p5/execution-log.xes) is separate from ten
[synthetic forbidden traces](../compliance/p5/invalid-traces.json) and the separate
[dispatch-only synthetic XES](../compliance/p5/synthetic-dispatch-only.xes).
ProcessDoctor separately classifies that synthetic trace as Partially Violating with
one structural violation (expected). External results and exact input hashes are in [ProcessDoctor evidence](../compliance/p5/processdoctor.json).

## Progress and review handover

| Step | Commit / result |
| --- | --- |
| Read repository/contracts and record plan | `1e556b1`, clean baseline `347ae74`; 118 baseline tests |
| Durable authorization, effects and P3/P4 outboxes | `d475498`; 126 tests |
| BPMN, workers, API/menu and inspected PDF | `ce3646d`; 129 tests; pinned lint clean |
| Live runner and DCR rules/forbidden checks | `27f324b`; 134 tests |
| First live development run | `649b1605`, all four scenarios passed on `27f324b`; older inspection-rejection path, preserved `p5-01` data |
| Parent-link, chronology and arrival-reference review fixes | `d118870`; 136 tests |
| Final-code verification | `9347c2c8`, four scenarios/five P5 completions; exact source/image/data identities in the verification report |

Documentation impact: README, run guide, implementation guide, integration agreement,
this P5 guide, P4 investigation/handover guides, task board, AGENTS.md and AI-use record are updated. The full P1–P6 target
and unconfirmed ownership remain explicit. Earlier reviewed evidence is unchanged.
The branch is committed locally; review, publishing and any subsequent PR/merge remain
with the user.
