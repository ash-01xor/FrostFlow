# Cold-chain: shared integration agreement

**P4 reintegration (replacement PR, branch `feat/p4-foundation-integration`):** Abid’s P4 from [PR #10](https://github.com/ash-01xor/FrostFlow/pull/10) was reverted by PR #11 so foundation [PR #9](https://github.com/ash-01xor/FrostFlow/pull/9) could merge first (merged as `ae941cc`). [PR #12](https://github.com/ash-01xor/FrostFlow/pull/12) restores P4 by reverting `31ad23b` and adapts it to the [foundation contract v1](P4-FOUNDATION-HANDOVER.md); see [sequencing](P4-INTEGRATION-SEQUENCING.md#checkpoint-4--replacement-p4-pr) and the [P4 guide](P4-QUALITY-INVESTIGATION.md). Review, merge and post-merge verification outcomes are recorded on PR #12.

**Purpose:** define how the six processes exchange information before their owners implement the connections. Read the [team implementation guide](COLD-CHAIN-IMPLEMENTATION-GUIDE.md) for scope, assignments, and stages.

**Status:** shared contract for implemented P1–P5 and planned P6 connections. Target handoffs for unimplemented processes are identified by their scope; a message name is not proof of a deployed consumer. Changes to shared messages must be reflected here and coordinated with affected owners.

## 1. Common identifiers

| Field | Meaning and rule |
| --- | --- |
| `shipmentId` | Identifies one shipment of goods throughout preparation, transport, receiving, and follow-up. |
| `journeyId` | Identifies one vehicle journey. One shipment per journey in this project. |
| `vehicleId` | Assigned vehicle; no overlapping active journeys for that vehicle. |
| `recipientId` | Destination cafeteria. |
| `productId` | Catalogue product. One product per shipment; stock cannot be substituted between products. |
| `batchId` | Batch of that product. One batch per shipment; retain it through P2–P6 and quality evidence. |
| `incidentId` | Identifies one investigation episode for a shipment. Multiple alerts can contribute to one active episode. |
| `claimId` | Identifies one supplier claim; at most one claim per incident in the prototype. |
| `originalShipmentId` | Present on replacement shipments; points to the original goods. |
| `correctiveId` | One P5 case per applied P4 replacement/return disposition. |
| `replacementForCorrectiveId` | The P5 case a replacement fulfils; retained even if that replacement receives its own incident and `correctiveId`. |
| `eventId` | Identifies one event. Re-delivering it must not repeat the business effect. |
| `commandId` | Identifies one requested simulator action and links its acknowledgement. |

Assign shipment and journey IDs before dispatch, incident IDs before opening DCR, and claim IDs before submission. A replacement gets new shipment/journey IDs and retains the original shipment and incident references. Engine instance IDs are recorded separately; they do not replace business IDs.

## 2. Common message format

Use this envelope for events entering the application and for process handoffs:

```json
{
  "eventId": "evt-001",
  "eventType": "ShipmentDispatched",
  "shipmentId": "S-001",
  "journeyId": "J-001",
  "occurredAt": "2026-11-15T10:00:00Z",
  "recordedAt": "2026-11-15T10:00:01Z",
  "source": "preparation",
  "inputMode": "manual",
  "payload": {
    "productId": "PRD-001",
    "productName": "Fresh milk",
    "batchId": "DEMO-MILK-001",
    "quantity": 15,
    "quantityUnit": "crate",
    "packDescription": "1 litre carton",
    "packsPerCrate": 12,
    "temperatureProfileId": "CHILLED-DEMO",
    "vehicleId": "V-01",
    "recipientId": "C-01",
    "routeId": "route-01",
    "deliveryDeadline": "2026-11-15T10:30:00Z"
  }
}
```

The fields outside `payload` are required for shipment events. Add `incidentId`, `claimId`, and `originalShipmentId` when relevant. A reading also carries its observed location or measurement in `payload`.

- `occurredAt` is when the activity or observation happened; `recordedAt` is when our application recorded it. Use UTC timestamps.
- `source` identifies the producer, such as `simulator`, `tomtom`, or `quality-investigation`.
- `inputMode` is `live`, `recorded`, `simulated`, or `manual`. Process-generated handoffs use `system`.
- API readings retain provider data time, where available, and retrieval time. Replayed inputs retain their original evidence times and record the replay run separately.
- Commands use the same identifiers and timestamps, plus `commandId` and `commandType` instead of an event type. Acknowledgements are events referencing `commandId`.

Log business activities with actor/role, outcome, process name, and engine instance ID. Keep raw readings available as evidence without treating every reading as a workflow activity in the compliance log.

## 3. Process handoffs

The Python integration service delivers these messages to the relevant engine or application handler. A message name alone does not perform the handoff. Each producer sends only after its described business action succeeds.

| Message | Producer → consumer | Required payload or references | Consumer behaviour |
| --- | --- | --- | --- |
| `ShipmentDispatched` | P1 → P2 | Product, batch, quantity/unit, vehicle, recipient, route, delivery deadline | Start transport and simulator journey. |
| `ShipmentArrivalNotification` | P2 → P3 | Delivery ID, shipment/journey/product/batch, arrival time and destination | Deduplicate the HTTP handoff and start one receiving process. |
| `ReceiptAcknowledged` | P3 → P2 | Recipient, quantity received, receipt time | Allow transport completion; no quality release is implied. |
| `TemperatureExcursionDetected` | Siddhi → P4 integration | Pattern start/end, temperature evidence | Establish hold, then open/update investigation. |
| `DeliveryRiskDetected` | Siddhi → P2 and P4 integration | Latest ETA, deadline, supporting updates | Request dispatcher decision and establish quality assessment/hold. |
| `RecoveryVerified` | Siddhi → P4 | Incident, cooling command, stable-reading interval | Add evidence; do not automatically release goods. |
| `DispositionRecorded` | P4 → P3 and P5 | Incident, decision, officer, reason, evidence references | P3 receives `QualityStatusChanged` through `accept_quality_status`. On branch `P5`, journalling the applied `REPLACE`/`RETURN` decision also atomically creates one corrective case and queues `P5DispositionRecorded`; the deployed P5 message-start consumer checks that authorization. `RELEASE` and the quality-status test stand-in create no corrective work. |
| `StockAdmitted` | P3 → shared log; P5 when replacement | Admission time and receiver; original and parent-corrective links | Admission and its `P5ReplacementOutcome` outbox are atomic; P5 validates the actual P3 journal event. |
| `GoodsRejected` | P3 → shared log; P5 when replacement | Incident, reason and parent-corrective link | Inspection and quality rejection atomically queue `P5ReplacementOutcome`; neither can fulfil a replacement successfully. |
| `ReturnCollected` | Manual/simulated collection → P5 | Incident, collector, collection time | Complete acknowledged return after matching the authorised booking. |
| `CorrectiveFulfilmentCompleted` | P5 → shared log | Incident, action, replacement/return references, original-goods outcome | Requires original collection and either no replacement (return) or replacement admission. |
| `CorrectiveFulfilmentFailed` | P5 → shared log | Incident, replacement rejection/cancellation, original collection | Record failure separately from investigation and claim closure; do not count rejected/cancelled replacements as fulfilment. |
| `ClaimableLossRecorded` | Explicit user decision → P6 | Incident, loss description, evidence references | Create claim; an incident alone does not trigger a claim. |
| `SupplierResponseRecorded` | Manual/simulated supplier → P6 | Claim, settlement/rejection, reason | Record outcome and permit claim closure. |

`DispositionRecorded.decision` is `RELEASE`, `REPLACE`, or `RETURN`. Evidence and decisions may be passed as references to stored records rather than copying full histories into every message.

**Receipt ownership:** P3 records one acknowledgement and informs P2. P2 cannot complete just because GPS shows arrival.

### Implemented P2–P3 receiving contract

P2 records its `ShipmentArrived` event and queues a distinct `ShipmentArrivalNotification` HTTP outbox item in the same SQLite transaction. By default, `P3_ARRIVAL_URL` targets the local `POST /integrations/p3/arrival` receiver. That receiver requires an `Idempotency-Key` equal to `event.deliveryId`, validates the notification against the recorded arrival activity and shipment, journey, product, batch and destination, and queues one `P3ShipmentArrived` Zeebe message. This validation is against the durable arrival event, not the current P2 state: a delayed HTTP retry may arrive after P2 has completed via its receipt path. Repeated identical deliveries return `202`; reuse of a delivery ID with different content returns `409`. The HTTP outbox retries until a 2xx acknowledgement.

The `ShipmentReceiving` process starts on `P3ShipmentArrived`. Its `GoodsReceived` and `GoodsInspected` message subscriptions are correlated on `shipmentId`. The receiving API validates physical receipt quantity and destination against the shipment; receipt is durably recorded before one `ReceiptAcknowledged` is queued to P2. P2 accepts this P3-produced event as well as its existing manual/test receipt input and completes transport only after receipt. The P3 process then records inspection and either rejects failed goods, admits passing goods to the separate `(recipientId, productId, batchId)` receiving inventory, or waits on a quality hold.

The P2 manual/test routes remain `POST /shipments/<shipmentId>/arrive` and `POST /shipments/<shipmentId>/acknowledge-receipt`; the latter is a P2-only stand-in and is not the P3 physical receiving process. It is refused (409) while P3 receiving is enabled (`P3_ARRIVAL_URL` set, the Compose default) or once P3 has started for that shipment. P3 exposes:

| Endpoint | Required request fields | Effect |
| --- | --- | --- |
| `POST /integrations/p3/arrival` | `event` containing `deliveryId`, `eventType`, `shipmentId`, `journeyId`, `productId`, `batchId`, `arrival.recipientId`, `sourceEventId`, UTC `occurredAt` and `recordedAt`; matching `Idempotency-Key` header | Deduplicate P2 handoff and start P3 once |
| `POST /shipments/<id>/receiving/receipt` | `eventId`, UTC `occurredAt`, `recipientId`, integer `quantityReceived`, `receiver` | Record physical receipt and queue P2 acknowledgement |
| `POST /shipments/<id>/receiving/inspection` | `eventId`, UTC `occurredAt`, `inspector`, boolean `passed` | Record inspection and continue to admission/hold/rejection |
| `GET /receiving-stock` | Optional `recipientId` query parameter | View the separate admitted receiving inventory |
| `POST /integrations/p4/quality-status` | `shipmentId`, `eventId`, UTC `occurredAt`, `qualityStatus`, `incidentId`, `expectedEvidenceVersion` (monitored dispositions) | Apply `PENDING`, `RELEASED` or `REJECTED`; resumes P3 when held. P4 uses the same registry operation in-process; the endpoint itself is the test stand-in |
| `POST /integrations/p4/incident` | Foundation outbox envelope `{"event": ...}`, `Idempotency-Key` = `event.deliveryId` | P4 consumer: persists the request, answers `{deliveryId, status: accepted\|duplicate}`; opens or updates the DCR case (see the [P4 guide](P4-QUALITY-INVESTIGATION.md)) |

Receipt acknowledgement and stock admission are separate milestones. P2 may release the vehicle after recorded physical receipt, while P3 continues to wait for inspection. P1's warehouse stock is not credited when goods arrive or are admitted at the recipient. The P4 integration endpoint is an unauthenticated internal demo interface and must not be exposed outside the trusted local stack.

**Replacement ownership:** P5 requests a linked shipment through P1. It waits for that shipment's successful receiving outcome and for the recorded handling of the original goods. A replacement's dispatch alone is insufficient.

**Message timing:** configure buffering for Camunda messages that may arrive before a waiting subscription. Match messages to the intended process instance using the business ID; include incident and command IDs where episodes could otherwise be confused. Test this during the first process integration.

### Implemented preparation and handoff boundary

P1 accepts an order before checking stock. A shortage keeps a real P1 instance at an event-based gateway. `StockReplenished` returns it to the stock check; `ShipmentCancellationRequested` ends only a stock-waiting shipment and releases its planned vehicle. Cancellation and replenishment compete for one waiting episode. Each episode has a distinct `stockWaitKey`; loading/dispatch continue to correlate by shipment ID.

`POST /shipments/<id>/replenish-stock` accepts an external `eventId`, positive integer `quantity` and `actor`. It adds inventory for the addressed shipment's exact product and batch once, and queues the targeted notification in the same transaction. Optional `productId`/`batchId` labels must match that shipment. A supply of milk cannot clear a yoghurt shortage. Replenishment represents another receipt of the same demonstration batch; adding new batches is outside the current API. Exact retries do not add more stock; changed content under the same ID is rejected. API validation of a verification requires all loading checks to be true; no damaged-packaging branch is implemented.

Dispatch records the agreed `ShipmentDispatched` envelope and durable outbox entry together. Its payload is passed as `dispatchEvent` to P2's message start. P2's registration worker checks the event against the journal, persists its engine key and acknowledges the outbox. Repeated worker delivery reuses that registration; a different P2 instance is prevented from repeating the business effect. Message IDs and active-start correlation also suppress repeated engine starts within the supported retention period.

The P2 manual receipt endpoint is usable only in P2-only runs (`P3_ARRIVAL_URL` unset); the normal local flow uses P3's physical-receipt endpoint to acknowledge P2. `start_journey` initializes a scripted route, a background tick writes GPS/ETA activities and durable Siddhi telemetry, and the final route point queues `ShipmentArrived` for the P2 catch and a separate P3 HTTP notification. TomTom remains optional. The local Siddhi app receives telemetry and emits correlated risk alerts after three distinct consecutive fresh late ETAs; the local scripted simulator acknowledges route commands forwarded by Siddhi. Each risk alert requires an explicit delay or alternative-route decision, and P2 applies route changes only after a matching simulator acknowledgement. P2's DCR rules and log are in `compliance/p2/`, and TomTom was verified live on 2026-11-15. P4 consumes the durable delivery-risk handoff; the external physical simulator remains outstanding. APIs return accepted status after durable queueing while workers may still be processing. Preserve the registry and engine data together. Dataset version 3 uses five product/batch warehouse inventory rows and a separate receiving inventory; legacy pooled-stock datasets are preserved and rejected by the updated service until a fresh project/directory is selected. The [dataset reference](RUNNING.md#demonstration-dataset) defines the seed values. Product/batch identity is recorded in activity payloads.

### P2 and P3 development handover

P2 is owned by **Rahul Shanbhag** and P3 by **Arunima Garg**. Abid owns P4 and Daniel owns P5; P6 and shared external integration owners remain unassigned. P2's current implementation is in `bpmn/transport.bpmn`, `deployer/app.py`, `deployer/registry.py`, `deployer/transport_integrations.py`, `tools/demo.py` and `stage_tests/`.

#### P2 transport implementation

The `ShipmentTransport` process keeps the `ShipmentDispatched` message start. It registers transport, starts the journey, then waits at an event-based gateway for either `ShipmentArrived` or `DeliveryRiskDetected`. Arrival follows `p2-record-arrival` → `WaitForReceiptAcknowledged` → `p2-release-vehicle` → `TransportCompleted`. A risk follows `p2-record-delivery-risk` → the `DecisionOrArrival` event-based gateway. A `DispatcherDecisionRecorded` message leads to `p2-apply-dispatcher-decision` and back to the journey gateway. A `ShipmentArrived` message first leads (`WaitForArrivalDuringDecision`) to `p2-record-arrival`, which closes the alert as `DeliveryRiskClosedAtArrival`; later decisions are refused. Every message catch is correlated on `shipmentId`; duplicate risk IDs reuse their durable message and an unresolved risk allows only one decision.

| `transportState` | Trigger / job type | Registry effect |
| --- | --- | --- |
| `AWAITING_JOURNEY_START` | Dispatch handoff / `p2-start-transport` | Register the P2 key and acknowledge the P1 outbox |
| `IN_TRANSIT` | `p2-start-journey` | Record `JourneyStarted`, initialize route/position/ETA; keep the vehicle reserved. Each tick records `GPSPositionUpdated` and `ETAUpdated`. |
| `IN_TRANSIT` | `DeliveryRiskDetected` / `p2-record-delivery-risk` | Record one active risk episode and wait for a dispatcher decision |
| `IN_TRANSIT` | `DispatcherDecisionRecorded` / `p2-apply-dispatcher-decision` | Send an idempotent Siddhi command; apply `routeId` only after the matching simulator acknowledgement; resume route events |
| `ARRIVED` | `ShipmentArrived` catch / `p2-record-arrival` | Record one arrival activity; keep the vehicle reserved |
| `COMPLETED` | `ReceiptAcknowledged` catch / `p2-release-vehicle` | Record receipt and `VehicleReleased`; set `vehicleReleased: true` |

The worker mapping is defined in `deployer/app.py` and must remain aligned with the BPMN task types:

| BPMN job type | Worker | Required fetched variables |
| --- | --- | --- |
| `p2-start-transport` | `start_transport` | `shipmentId`, `dispatchEvent` |
| `p2-start-journey` | `start_journey` | `shipmentId` |
| `p2-record-arrival` | `record_arrival` | `shipmentId`, `arrivalEvent` |
| `p2-release-vehicle` | `release_vehicle` | `shipmentId`, `receiptEvent` |
| `p2-record-delivery-risk` | `record_delivery_risk` | `shipmentId`, `deliveryRiskEvent` |
| `p2-apply-dispatcher-decision` | `apply_dispatcher_decision` | `shipmentId`, `dispatcherDecision` |

The receipt endpoint and the optional manual arrival endpoint accept manual/test input. The simulator is the automatic arrival producer. The API routes for risk and dispatcher decisions are:

| Endpoint | Required JSON fields | Optional fields | Expected responses |
| --- | --- | --- | --- |
| `POST /shipments/<id>/arrive` (manual/test) | `eventId`, UTC `occurredAt`, `recipientId` | `latitude` and `longitude` together | `202` accepted; `400` invalid payload; `404` unknown shipment; `409` wrong state, mismatched recipient/identity, or conflicting event ID; `503` workers not ready |
| `POST /shipments/<id>/acknowledge-receipt` (P2-only runs; refused while P3 receiving is enabled) | `eventId`, UTC `occurredAt`, `recipientId`, `quantityReceived` equal to dispatched crates | `receiptReference` | Same response rules; receipt before arrival is `409` |
| `POST /integrations/siddhi/delivery-risk` | `shipmentId`, `eventId`, UTC `occurredAt`, UTC `eta`, UTC `deliveryDeadline`, `reason` | `estimatedDelaySeconds` | `202` accepted; exact retry reuses message; second unresolved risk is `409` |
| `POST /shipments/<id>/dispatcher-decision` | `eventId`, `decision` (`ACCEPT_DELAY` or `APPROVE_ALTERNATIVE_ROUTE`) | `routeId` required only for route approval | `202` accepted; wrong state/duplicate conflicting decision is `409`; invalid decision/route is `400` |

Example payloads:

```json
{"eventId":"arr-demo-A-001","occurredAt":"2026-11-15T10:22:00Z","recipientId":"C-01"}
```

```json
{"eventId":"receipt-demo-A-001","occurredAt":"2026-11-15T10:24:00Z","recipientId":"C-01","quantityReceived":15,"receiptReference":"RCPT-001"}
```

```json
{"shipmentId":"demo-A","eventId":"risk-demo-A-001","occurredAt":"2026-11-15T10:20:00Z","eta":"2026-11-15T11:10:00Z","deliveryDeadline":"2026-11-15T11:00:00Z","reason":"traffic delay","estimatedDelaySeconds":600}
```

```json
{"eventId":"decision-demo-A-001","decision":"APPROVE_ALTERNATIVE_ROUTE","routeId":"route-03"}
```

Every event is correlated by `shipmentId`. A `202` means it was durably accepted for publication, not that its worker has completed. Retrying the identical event ID and payload returns `202` with the same message identity and records one activity. Reusing an ID with different content, or submitting it in the wrong state, returns `409`. External event IDs are stored in a journal namespace separate from internal P2 activity IDs; `producerEventId` retains the source ID for traceability. The CLI uses stable IDs and polls for state changes.

The P3 arrival notification is created atomically with `ARRIVED` in `Registry.record_arrival`. Its outbox identity is `P3:ShipmentArrivalNotification:<shipmentId>:<arrivalEventId>` and it is distinct from the `ShipmentArrived` message consumed by P2. The default local `P3_ARRIVAL_URL` POSTs `{"event": {"deliveryId", "eventType", "shipmentId", "journeyId", "productId", "batchId", "arrival", "sourceEventId", "occurredAt", "recordedAt", optional "originalShipmentId" and "incidentId"}}` with an `Idempotency-Key` header. A 2xx marks delivery acknowledged; failed requests retry from the durable outbox until acknowledged. This is at-least-once HTTP delivery, and the receiver deduplicates `deliveryId`. Producer entry point: `deployer/registry.py:record_arrival` and `deployer/app.py:deliver_messages`; consumer entry point: `deployer/app.py:p3_arrival`.

The scripted route points are demo coordinates defined in `deployer/transport_integrations.py`; this is route simulation, not a vehicle navigation source. `TOMTOM_API_KEY` enables a traffic-aware TomTom route request from the current position to the route's final point. Without it, ETA uses a local distance/speed estimate and `/health` labels that mode. GPS and ETA activities are durably queued for `SIDDIHI_TELEMETRY_URL`, which defaults to the local Siddhi app. `siddhi-apps/ColdChainTelemetry.siddhi` logs telemetry and emits a risk to `/integrations/siddhi/delivery-risk` only after three distinct consecutive fresh late ETAs within 180 s for one shipment and route revision. Each ETA carries `observedAtMillis`, and readings older than 30 s are stale. At most one risk is sent per shipment, journey and route revision. For goods without an active or rejected incident, accepting a risk sets `qualityStatus: PENDING` with a `DELAY:<riskEventId>` incident. An active incident receives additional evidence. Terminal `REJECTED` status and its original incident/disposition remain unchanged; its handoff carries `incidentOpened: false`. In the same transaction it queues a durable `DeliveryRiskIncident` handoff for P4 (`p4-http`, `P4_INCIDENT_URL`). The hold remains if P4 is unavailable, and only a release of the current incident lets P3 admit. Route changes are planned from the current position along the chosen corridor to the recipient's location, so the destination never changes. The decision worker returns `routeId` and `routeRevision` to Camunda. P2 decision commands default to the local Siddhi `/route-command` HTTP service on port 8007; telemetry uses port 8006. Siddhi's JSON sink sends a null `routeId` as the string `"undefined"`, so the simulator receiver treats `"undefined"` and `""` as no route for `AcceptDelay`. Siddhi forwards each command to `/integrations/simulator/route-command`; that receiver checks the active shipment/risk and returns a stable command acknowledgement. P2 changes the route only after the matching `commandId` receives `simulatorAcknowledged: true`. This is a scripted local simulator acknowledgement, not an external vehicle integration. `GPS_TICK_SECONDS` controls scripted movement cadence. CLI option 11 remains a manual risk-injection test that bypasses Siddhi detection; use the short-deadline walkthrough in `docs/RUNNING.md` to exercise Siddhi-generated risk.

The committed live check is `python stage_tests/check_stage1.py`. It needs fresh paired version-3 data with all 100 crates, no shipments, both vehicles available and `P2_SIMULATOR_ENABLED=false`; receipt is recorded through P3. It verifies two separate P2 process instances, early receipt rejection, arrival-catch progression, the reserved vehicle at the receipt catch, exact retries, Zeebe end-event completion and reuse of a released vehicle. Output defaults to `runtime/verification-results/`: `execution-log.xes` and `verification.json` are P1 compliance evidence; `p2-completion.json` is P2 execution evidence only. P2 DCR rules, the current real execution log and a verification report are in `compliance/p2/`; local replay passes; newer review-18 logs also pass user-performed ProcessDoctor checks, recorded with screenshots and hashes in `compliance/foundation/review-18/processdoctor.json`. Earlier portal results retain their original scope. `stage_tests/check_p2_siddhi.py` is the live acceptance check for the Siddhi, route-command, arrival-closure and TomTom paths. The expanded route/risk offline regressions are in `stage_tests/test_stage1.py`; they stub TomTom, Siddhi and Zeebe. The current local acceptance results are recorded below. When changing or replacing an adapter/receiver, repeat live checks for telemetry delivery, acknowledgement, route movement/ETA, automatic arrival and receiver deduplication against that configuration.

#### Verified integration and remaining work

P2/P3 integration was merged through [PR #4](https://github.com/ash-01xor/FrostFlow/pull/4) and [PR #5](https://github.com/ash-01xor/FrostFlow/pull/5); the smoke helper and final evidence were merged through [PR #6](https://github.com/ash-01xor/FrostFlow/pull/6). The reports distinguish actual live execution, local DCR replay and historical external checks. See [the evidence table](RUNNING.md#evidence-what-was-verified-and-where) for tested revisions and commands.

| Implemented and verified boundary | Evidence |
| --- | --- |
| P2 arrival notification, retry deduplication and P3 receipt-gated release | Offline regressions, P1 regression and P2 live acceptance; separate business IDs and engine keys retained |
| Scripted GPS, automatic arrival and independent journeys | Three live P2 journeys in run `131337` |
| Siddhi distinct-ETA persistence and dispatcher decisions | Isolated rule regression and live P2 acceptance; unavailable/stale observations break the sequence |
| Acknowledged destination-preserving route changes | Live acceptance confirms simulator acknowledgement, subsequent movement and exported route variables |
| P3 inspection, exact receiving inventory, hold/current-incident release and rejection | Four live scenarios in run `9d1cd66e`, retry/inventory checks and merged-main smoke |
| P2/P3 DCR rules and current actual logs | Three P2 and four P3 traces pass local replay; synthetic violations are checked separately |

| Remaining work | Dependency / owner | Required result |
| --- | --- | --- |
| ProcessDoctor refresh | Completed manually by the user for review-18 logs | P2 3/3 and P3 4/4 conforming variants; zero violations. See `compliance/foundation/review-18/processdoctor.json` |
| Current live TomTom recheck | P2 owner; valid key and connectivity | Retain provider provenance and sanitized responses; failure must yield unavailable ETA. Earlier live provider evidence remains historical |
| P4 review and merge (consumer, dispositions, API check, full-stack repository-mode live run `ed88d16d` and ProcessDoctor 4/4 conforming done) | Abid; reviewers | PR #12 review approval and merge |
| External physical simulator and cooling/recovery CEP | Shared owners to be assigned | Remain future work. The foundation implements simulated container assessment and Open-Meteo current conditions; see the contract below |

The local P3 receiver contract is implemented and tested. If replacing it with an external receiver, repeat the delivery-ID retry, envelope validation and receipt-gate checks against that receiver. Offline tests stub external services; they do not establish a new provider or consumer connection.

P2 receives `shipmentId` and the complete `dispatchEvent` envelope at its start. Registration returns top-level `shipmentId`, `journeyId`, `vehicleId`, `recipientId`, `routeId`, `deliveryDeadline`, `quantity`, `productId`, `productName`, `batchId`, `quantityUnit`, `packDescription`, `packsPerCrate` and `temperatureProfileId` as engine variables. Replacement/incident references remain available in `dispatchEvent` and are preserved in P2's P3 arrival notification.

`Registry.advance` is specific to P1. P2 uses process-specific transactional methods for journey start, arrival recording and vehicle release. P3 has its own executable receiving model, durable P2 arrival receiver, receipt acknowledgement, inspection decision and separate inventory. Physical arrival does not complete transport; receipt does not permit stock admission by itself.

Read the current shipment through the state endpoint or shared registry; do not introduce a separate quality-status copy in another service or process variable. Every admission attempt must check permission in the same transaction/order as hold changes. P3's admission operation checks this transactionally. P4's DCR investigation consumer remains to be implemented.

Process definitions are deployed on service startup. After editing a model, restart the communication service; after Python changes, rebuild it with `docker compose up --build -d communication`. Existing instances stay on their original model version. Start fresh cases to test a changed definition, and preserve the existing P1 contract/tests.

## 4. Shared quality status and decisions

Delivery progress and quality permission are separate. Goods may be physically received while their quality status is `PENDING`.

| Quality status | Set when | Stock admission |
| --- | --- | --- |
| `CLEAR` | Shipment created, with no identified active concern | Allowed after receiving inspection passes |
| `PENDING` | A qualifying incident alert is accepted | Blocked |
| `RELEASED` | DCR confirms a release decision for the current incident | Allowed after inspection passes |
| `REJECTED` | DCR confirms replacement or return for the original goods | Blocked |

A small persistent shared registry holds shipment mappings and quality status. Camunda and DCR retain their own workflow state. P3 checks current permission transactionally at the admission action rather than trusting an old screen or process variable.

P3's internal quality-status receiver persists a hold/disposition in the shared registry and resumes a held P3 process after a current-incident release or rejection. The P4 incident handler (`deployer/quality.py`) that creates the DCR case and owns those decisions remains to be implemented. The registry serializes hold changes and admission decisions per shipment so two competing requests cannot bypass the rule.

There is at most one active investigation per shipment. Further alerts add evidence to that investigation. After closure and a newly qualifying pattern, create a new incident. Only a confirmed decision for the current incident can change its hold; a delayed release from an older incident cannot clear a newer one.

If a problem is discovered after admission, record a late-discovery exception and notify the receiver. Do not rewrite history as though admission had been blocked. Further food-use investigation is outside this prototype.

## 5. Commands and observable responses

| Command | Selected by | Simulator action | Evidence of completion |
| --- | --- | --- | --- |
| `AdjustCooling` | Quality officer through P4 | Change the future temperature profile | Acknowledgement, then sustained stable readings evaluated by Siddhi |
| `ChangeRoute` | Dispatcher through P2 | Switch to the predefined alternative route | Acknowledgement, changed future GPS positions, and updated route/ETA requests |

Both commands pass through Siddhi. The acknowledgement includes command ID, shipment, incident if relevant, and success/failure. Failed commands must not count as completed actions. Repeated command delivery returns the existing acknowledgement without applying the action again.

An acknowledgement proves the command was applied, not that the problem recovered. Stable readings are evidence for a human disposition, not automatic permission to use goods. TomTom and Open-Meteo supply read-only information; the simulator performs the physical response.

## 6. Repeated events, unavailable data, and test runs

- Reject malformed messages before invoking a process.
- Remember accepted event and command IDs across ordinary service restarts. Repeated handoffs must not create extra cases, shipments, or claims.
- Require adequate fresh samples for excursion/recovery detection. A gap invalidates the continuous evidence interval and is recorded as missing data.
- Show failed/stale API data as unavailable, not normal. Weather alone cannot determine product quality.
- Keep one agreed timing configuration for simulator cadence, Siddhi windows, and Camunda timers. Use short real-time durations for demonstrations.
- Store API responses for repeatable runs. A scenario identifies whether inputs are live or replayed; replay must not be presented as a live provider response.

Each owner maps business activity names to their compliance graph. Include the neighbouring events needed to check the rule: P3 needs hold/decision events; P5 needs authorisation; P6 needs its final supplier outcome. Keep deliberately violating test traces separate from real execution logs.

## 7. First setup session in the Project repository

1. Read this agreement and the implementation guide; assign the six owners and record any agreed corrections.
2. Inspect the sibling `POESS-example/EquipmentLoan`, `POESS-example/BPMN`, and `POESS-example/DCR` projects. Reuse working setup patterns and check their versions before choosing dependencies.
3. Create the minimal project folders, README, ignored secret/runtime files, and shared configuration. Do not copy generated logs, local credentials, or unrelated example code.
4. Verify Camunda startup, a minimal DCR case action, both API connections, and a tiny compliance-tool import. Record which checks passed and any access blockers.
5. Implement the first P1 milestone: create one shipment, verify loading, and dispatch with a recorded `ShipmentDispatched` event. Demonstrate that missing verification blocks dispatch.
6. Follow [the run guide](RUNNING.md) for the implemented stock-wait/cancellation paths, verified P2 handoff and DCR compliance checks. P1's course-tool check has passed; Ashvanth owns P1 and teammate review remains pending.

The team guide remains the work plan. This document is the shared connection reference. Individual owners choose internal task names and worker details during their process implementation, while preserving these agreed handoffs.

## P2/P3 repair contract (17 November 2026)

ETA retries are removed by `(shipmentId, journeyId, eventId)` before Siddhi classifies observations and evaluates its consecutive sequence. Only distinct readings count; freshness (30 seconds), the 180-second window, route revisions and sequence-breaking on-time, unavailable or stale observations retain their existing meaning.

A delivery-risk alert for `REJECTED` goods preserves the quality status, original incident and recorded disposition. The durable `DeliveryRiskIncident` handoff contains the new risk evidence, `qualityStatus: REJECTED` and `incidentOpened: false`. Transport decisions remain available; quality release and stock admission remain forbidden. This is a persisted handoff contract, not an implemented P4 investigation.

The P3 compliance runner waits for `AWAITING_LOADING` with no `pendingCommand` before requesting loading verification. The application guard remains enforced. Current live execution, local replay and external conformance results are recorded separately in the verification reports; see the run guide for commands and remaining checks.


## Pre-P4 monitoring and assessment contract

The [foundation specification](PRE-P4-FOUNDATION.md) defines the approved profile, provenance, weather eligibility and v1 assessment extension. It extends P1–P3 and the future P4 boundary; it creates no executable investigation or authorised decision workflow.

- `POST /shipments/<id>/simulation/temperature`: `commandId`, `actor`, and `scenario` (`normal`, `isolated-spike`, `sustained-excursion`, `missing-telemetry`). Exact retries return the accepted result; conflicting command reuse fails. Changes affect future readings and are journalled.
- `GET /shipments/<id>/monitoring/observations`: persisted observation/window/context references in sequence order. Raw samples are separate from compliance business activities.
- `POST /integrations/siddhi/container-assessment`: eventId, shipmentId, journeyId, sequence, evaluatedAtMillis, excursion and combined booleans. The production outbox consumes this same acknowledgement from Siddhi's synchronous `/container` response on port 8008. Stored evidence/profile must match. Out-of-order acknowledgements wait for all preceding sequences; risk effects and cursor advancement are atomic. Changed result reuse fails.
- `p4-http` / `P4_INCIDENT_URL`: existing `DeliveryRiskIncident` delay fields remain. Container/uncertainty reasons use `AssessmentRequested`, with the same identity/quality envelope and `assessment.schemaVersion: 1`. The consumer response must contain the matching `deliveryId` and `status: accepted` or `duplicate` after durable acceptance. Handoff payloads are immutable per delivery ID; unavailable consumer means PENDING, with hold intact.
- `POST /integrations/p4/quality-status`: monitored RELEASED/REJECTED dispositions require `expectedEvidenceVersion` and the current incident. This trusted-local test endpoint is a stand-in. Material new reason/episode/urgent escalation advances the version. Normal readings never release goods; terminal rejection retains its incident.

Loading verification starts a session with an immutable profile. Physical receipt closes it at a final sequence and promptly sends P2 acknowledgement. `p3-finalize-monitoring` keeps P3 at an explicit service task until evaluation reaches closure, or a 20-second timeout establishes a durable uncertainty hold. Admission rechecks closed/finalized monitoring and current quality permission in one transaction. Legacy shipments without a snapshot are labelled legacy/unmonitored and retain their original engine model.

See [Abid’s P4 foundation handover](P4-FOUNDATION-HANDOVER.md) for intake, durable acknowledgements, assessed evidence versions, fixtures and reintegration requirements. The replacement P4 PR implements that boundary; its behaviour is summarised in the [P4 guide](P4-QUALITY-INVESTIGATION.md#contract-v1-behaviour-enforced-by-the-service).

## P5 corrective fulfilment contract

Daniel’s branch `P5`, published in [PR #14](https://github.com/ash-01xor/FrostFlow/pull/14) (open, non-draft and not merged
as of 22 November 2026), adds `CorrectiveFulfilment` to the single communication service. An applied
P4 `REPLACE` or `RETURN` decision creates one `correctiveId` per incident and queues
`P5DispositionRecorded` in the same transaction as `DispositionRecorded` and the saved
P4 decision. The message carries `correctiveId` and the exact `authorizationEvent`.
P5 registers one engine instance and validates the durable authorization. P4 closure
is independent of P5 completion. Releases and `/integrations/p4/quality-status` test
inputs do not authorize P5. Previously finalized P4 decisions are not backfilled.

- `GET /corrective-fulfilments?originalShipmentId=<id>` lists cases (filter optional).
  `GET /corrective-fulfilments/<correctiveId>` shows the durable case, booking, accepted
  inputs, original-goods status, replacement result and engine key.
- `POST /corrective-fulfilments/<id>/arrange`: `eventId`, UTC `occurredAt`, `actor`,
  `collector`, `bookingReference`, UTC `scheduledAt`. For `REPLACE`, also send a
  `replacement` object containing only new `shipmentId`, new `journeyId`, `vehicleId`,
  `routeId` and UTC `deliveryDeadline`. Arrangement time cannot precede P4 authorization.
  P5 copies the original product, batch, complete
  crate quantity and recipient; substitutes, partial replacements and new batches are
  outside scope. `RETURN` refuses replacement fields. The transaction reserves the
  replacement identity and vehicle, creates the linked shipment, and queues
  `P5ArrangementRequested` with `correctiveId`/`correctiveInput`. No stock is allocated
  until P1 checks it. Ordinary `/shipments` rejects replacement links. The replacement's
  `replacementForCorrectiveId` preserves its parent independently of any later P4
  incident and corrective case of its own. P2's dispatch/arrival and P3's receiving
  events retain these references; the arrival receiver checks them against the durable
  P2 arrival snapshot so delayed retries remain valid after later quality changes.
- P5's arrangement worker records `CollectionBooked` and, for replacement,
  `ReplacementRequested`. A parallel branch calls the co-deployed `ShipmentPreparation`
  using deployment binding and an explicit child `shipmentId` mapping. Child variables
  do not overwrite parent variables. P1 keeps its loading/dispatch/stock-wait guards;
  P2 and P3 use their existing durable handoffs. Cancellation records a failed
  replacement result; dispatch records `ReplacementDispatched` and starts the wait for
  P3. P3 admission/rejection atomically queues `P5ReplacementOutcome` with the actual
  journal event. P5 checks that durable event and records `ReplacementAdmitted` or
  `ReplacementRejected`. Arrival, physical receipt and dispatch alone cannot fulfil it.
- `POST /corrective-fulfilments/<id>/collect`: `eventId`, UTC `occurredAt`, `actor`,
  `collector`, `bookingReference`, integer `quantityCollected`. It must match the
  processed booking and full original quantity. P3 must have physically received and
  rejected the original. Collection cannot precede booking or receipt. The receiver
  queues `P5ReturnCollected`; only its P5 worker records `ReturnCollected` and sets
  `originalGoodsStatus: COLLECTED`. This is a manual/simulated confirmation, not an
  external collection service or inventory credit.

Case states: `START_REQUESTED` → `AWAITING_ARRANGEMENT` → `ARRANGEMENT_REQUESTED` →
`IN_PROGRESS` → `COMPLETED` or `FAILED`. The BPMN parallel join requires original
collection and the replacement branch. Return needs no replacement. Replacement
success requires `ADMITTED`; `REJECTED`/`CANCELLED` produces
`CorrectiveFulfilmentFailed`, never `CorrectiveFulfilmentCompleted`. Both final outcomes
retain original collection evidence. Failed replacement follow-up remains explicit
future work; P5 creates no second replacement or supplier claim automatically.

Both POSTs return **202 after durable acceptance**. Poll the case for processed effects.
Each action is accepted once per case: identical event ID/time/payload retries reuse
its original message, including after completion; conflicting action/ID reuse returns
409. Invalid fields return 400; unknown cases return 404; not-ready returns 503.
Outbox identities and 24-hour Zeebe message retention follow the existing recovery
limits; do not retry uncertain engine starts by deleting case/shipment records.
