# Cold-chain: shared integration agreement

**Purpose:** define how the six processes exchange information before their owners implement the connections. Read the [team implementation guide](COLD-CHAIN-IMPLEMENTATION-GUIDE.md) for scope, assignments, and stages.

**Status:** proposed implementation baseline. Review together when assigning owners. Changes to shared messages must be reflected here and communicated to affected owners.

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
| `ShipmentArrived` | Simulator through P2 → P3 | Arrival time and destination | Record arrival and start receiving once. |
| `ReceiptAcknowledged` | P3 → P2 | Receiver and receipt time | Allow transport completion; no quality release is implied. |
| `TemperatureExcursionDetected` | Siddhi → P4 integration | Pattern start/end, temperature evidence | Establish hold, then open/update investigation. |
| `DeliveryRiskDetected` | Siddhi → P2 and P4 integration | Latest ETA, deadline, supporting updates | Request dispatcher decision and establish quality assessment/hold. |
| `RecoveryVerified` | Siddhi → P4 | Incident, cooling command, stable-reading interval | Add evidence; do not automatically release goods. |
| `DispositionRecorded` | P4 → P3 and P5 | Incident, decision, officer, reason, evidence references | P3 updates admission permission; P5 starts only for replacement/return. |
| `StockAdmitted` | P3 → shared log; P5 when replacement | Admission time and receiver; original link if applicable | Record stock admission; allow replacement fulfilment to progress. |
| `GoodsRejected` | P3 → shared log; P5 when replacement | Incident and reason | Record rejection; a rejected replacement cannot count as successful fulfilment. |
| `ReturnCollected` | Manual/simulated collection → P5 | Incident, collector, collection time | Complete acknowledged return after matching the authorised booking. |
| `CorrectiveFulfilmentCompleted` | P5 → shared log | Incident, action, replacement/return references, original-goods outcome | Record corrective outcome separately from investigation and claim closure. |
| `ClaimableLossRecorded` | Explicit user decision → P6 | Incident, loss description, evidence references | Create claim; an incident alone does not trigger a claim. |
| `SupplierResponseRecorded` | Manual/simulated supplier → P6 | Claim, settlement/rejection, reason | Record outcome and permit claim closure. |

`DispositionRecorded.decision` is `RELEASE`, `REPLACE`, or `RETURN`. Evidence and decisions may be passed as references to stored records rather than copying full histories into every message.

**Receipt ownership:** P3 records one acknowledgement and informs P2. P2 cannot complete just because GPS shows arrival.

### P2–P3 event contract proposal (pending P3 review)

Use the common event envelope above for both messages. `eventId`, `eventType`, `shipmentId`, `journeyId`, `occurredAt`, `recordedAt`, `source`, `inputMode`, and `payload` are required. Timestamps are ISO 8601 UTC. `occurredAt` is the physical event time; `recordedAt` is when the producer accepted the event. `source` identifies the producer and `inputMode` is `manual` for the initial test endpoints.

Correlate each Zeebe message subscription using `shipmentId`, matching the P2 instance started for that shipment. `journeyId` is required for validation and traceability, but is not the correlation key. Keep message buffering enabled so a valid message that arrives just before its catch event is subscribed is not lost. Each producer assigns a stable `eventId`; an exact retry must not repeat the business effect, and reusing an ID with different content must be rejected. P3 emits one `ReceiptAcknowledged` only after it has recorded the physical receipt.

The manual/test HTTP endpoints accept either the full envelope or a compact request containing `eventId`, `occurredAt` and `payload` (the event-specific fields may also be sent at the top level). The route supplies `eventType` and `shipmentId`; P2 checks/derives `journeyId` from its registry and stamps `recordedAt`, `source` and `inputMode: manual`. The routes are `POST /shipments/<shipmentId>/arrive` and `POST /shipments/<shipmentId>/acknowledge-receipt`.

`ShipmentArrived` — P2 to P3. Required payload: `recipientId`, identifying the expected destination. Optional payload: `latitude` and `longitude`, supplied together when a measured arrival position is available. The common `occurredAt` is the arrival time. Do not require coordinates for the manual/test input.

```json
{
  "eventId": "arr-S-001-001",
  "eventType": "ShipmentArrived",
  "shipmentId": "S-001",
  "journeyId": "J-001",
  "occurredAt": "2026-11-15T10:22:00Z",
  "recordedAt": "2026-11-15T10:22:03Z",
  "source": "p2-manual-test",
  "inputMode": "manual",
  "payload": {
    "recipientId": "C-01",
    "latitude": 48.8566,
    "longitude": 2.3522
  }
}
```

`ReceiptAcknowledged` — P3 to P2. Required payload: `recipientId` and `quantityReceived` (integer crates physically received). Optional payload: `receiptReference` (the receiver's receipt/slip ID). The common `occurredAt` is the physical receipt time. `recipientId` and `quantityReceived` must match the dispatched shipment for this milestone; discrepancies need an agreed exception path before changing that rule. This event means physical receipt was acknowledged; it does not mean goods passed quality inspection or were admitted to stock.

```json
{
  "eventId": "receipt-S-001-001",
  "eventType": "ReceiptAcknowledged",
  "shipmentId": "S-001",
  "journeyId": "J-001",
  "occurredAt": "2026-11-15T10:24:00Z",
  "recordedAt": "2026-11-15T10:24:02Z",
  "source": "p3-manual-test",
  "inputMode": "manual",
  "payload": {
    "recipientId": "C-01",
    "quantityReceived": 15,
    "receiptReference": "RCPT-001"
  }
}
```

These field requirements are a proposal, not a team-approved contract. P2 can implement them as the first manual-input baseline while P3 reviews them. If P3 needs to report partial quantities, damaged goods, or a mismatched destination, agree on the receiving exception behavior before expanding the schema.

**Replacement ownership:** P5 requests a linked shipment through P1. It waits for that shipment's successful receiving outcome and for the recorded handling of the original goods. A replacement's dispatch alone is insufficient.

**Message timing:** configure buffering for Camunda messages that may arrive before a waiting subscription. Match messages to the intended process instance using the business ID; include incident and command IDs where episodes could otherwise be confused. Test this during the first process integration.

### Implemented preparation and handoff boundary

P1 accepts an order before checking stock. A shortage keeps a real P1 instance at an event-based gateway. `StockReplenished` returns it to the stock check; `ShipmentCancellationRequested` ends only a stock-waiting shipment and releases its planned vehicle. Cancellation and replenishment compete for one waiting episode. Each episode has a distinct `stockWaitKey`; loading/dispatch continue to correlate by shipment ID.

`POST /shipments/<id>/replenish-stock` accepts an external `eventId`, positive integer `quantity` and `actor`. It adds inventory for the addressed shipment's exact product and batch once, and queues the targeted notification in the same transaction. Optional `productId`/`batchId` labels must match that shipment. A supply of milk cannot clear a yoghurt shortage. Replenishment represents another receipt of the same demonstration batch; adding new batches is outside the current API. Exact retries do not add more stock; changed content under the same ID is rejected. API validation of a verification requires all loading checks to be true; no damaged-packaging branch is implemented.

Dispatch records the agreed `ShipmentDispatched` envelope and durable outbox entry together. Its payload is passed as `dispatchEvent` to P2's message start. P2's registration worker checks the event against the journal, persists its engine key and acknowledges the outbox. Repeated worker delivery reuses that registration; a different P2 instance is prevented from repeating the business effect. Message IDs and active-start correlation also suppress repeated engine starts within the supported retention period.

The P2 manual receipt gate remains the terminal business control: arrival does not release the vehicle, and the receipt worker completes transport. On `p2-updates`, `start_journey` also initializes a scripted route, a background tick writes GPS/ETA activities and durable Siddhi telemetry, and the final route point queues `ShipmentArrived` for the existing shipment-correlated catch. TomTom remains optional. The local Siddhi app receives telemetry, detects late ETA, and emits correlated risk alerts; the local scripted simulator acknowledges route commands forwarded by Siddhi. P3 notifications remain pending until a receiver is configured. Each risk alert requires an explicit delay or alternative-route decision, and P2 applies route changes only after a matching simulator acknowledgement. The Siddhi risk and command payloads are implemented in the local app; the P3 payload remains a proposal until the P3 owner confirms it. This is still not full P2 completion: live TomTom/external simulator/P3 verification and P2 DCR verification remain outstanding. APIs return accepted status after durable queueing while workers may still be processing. Preserve the registry and engine data together. Dataset version 3 uses five product/batch inventory rows; legacy pooled-stock datasets are preserved and rejected by the updated service until a fresh project/directory is selected. The [dataset reference](RUNNING.md#demonstration-dataset) defines the seed values. Product/batch identity is recorded in activity payloads.

### P2 and P3 development handover

The P2 owner is **Rahul**. P3–P6 and external CEP/simulator owners remain to be assigned. P2's current implementation is in `bpmn/transport.bpmn`, `deployer/app.py`, `deployer/registry.py`, `deployer/transport_integrations.py`, `tools/demo.py` and `stage_tests/`.

#### P2 transport implementation

The `ShipmentTransport` process keeps the `ShipmentDispatched` message start. It registers transport, starts the journey, then waits at an event-based gateway for either `ShipmentArrived` or `DeliveryRiskDetected`. Arrival follows `p2-record-arrival` → `WaitForReceiptAcknowledged` → `p2-release-vehicle` → `TransportCompleted`. A risk follows `p2-record-delivery-risk` → `WaitForDispatcherDecision` → `p2-apply-dispatcher-decision`, then returns to the event-based gateway. Every message catch is correlated on `shipmentId`; duplicate risk IDs reuse their durable message and an unresolved risk allows only one decision.

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
| `POST /shipments/<id>/acknowledge-receipt` (manual/test, P3 stand-in) | `eventId`, UTC `occurredAt`, `recipientId`, `quantityReceived` equal to dispatched crates | `receiptReference` | Same response rules; receipt before arrival is `409` |
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

The P3 arrival notification is created atomically with `ARRIVED` in `Registry.record_arrival`. Its outbox identity is `P3:ShipmentArrivalNotification:<shipmentId>:<arrivalEventId>` and it is distinct from the `ShipmentArrived` message consumed by P2. With `P3_ARRIVAL_URL`, the communication service POSTs `{"event": {"deliveryId", "eventType", "shipmentId", "journeyId", "productId", "batchId", "arrival", "sourceEventId", "occurredAt", "recordedAt", optional "originalShipmentId" and "incidentId"}}` and an `Idempotency-Key` header. A 2xx marks that delivery acknowledged; failed requests retry from the durable outbox until acknowledged. This is at-least-once HTTP delivery, so the P3 receiver must deduplicate `deliveryId`. The P3 owner has not yet confirmed this contract, so the URL must remain unset until agreement. Producer entry point: `deployer/registry.py:record_arrival` and `deployer/app.py:deliver_messages`; consumer entry point: the P3 HTTP receiver.

The scripted route points are demo coordinates defined in `deployer/transport_integrations.py`; this is route simulation, not a vehicle navigation source. `TOMTOM_API_KEY` enables a traffic-aware TomTom route request from the current position to the route's final point. Without it, ETA uses a local distance/speed estimate and `/health` labels that mode. GPS and ETA activities are durably queued for `SIDDIHI_TELEMETRY_URL`, which defaults to the local Siddhi app. `siddhi-apps/ColdChainTelemetry.siddhi` logs telemetry and emits at most one late-ETA risk per shipment, journey and route revision to `/integrations/siddhi/delivery-risk`. P2 decision commands default to the local Siddhi `/route-command` HTTP service. Siddhi forwards each command to `/integrations/simulator/route-command`; that receiver checks the active shipment/risk and returns a stable command acknowledgement. P2 changes the route only after the matching `commandId` receives `simulatorAcknowledged: true`. This is a scripted local simulator acknowledgement, not an external vehicle integration. `GPS_TICK_SECONDS` controls scripted movement cadence. CLI option 11 remains a manual risk-injection test that bypasses Siddhi detection; use the short-deadline walkthrough in `docs/RUNNING.md` to exercise Siddhi-generated risk.

The committed live check is `python stage_tests/check_stage1.py`. It needs fresh version-3 data with all five products and both vehicles available. It verifies two separate P2 process instances, early receipt rejection, arrival-catch progression, the reserved vehicle at the receipt catch, exact retries, Zeebe end-event completion and reuse of a released vehicle. Output defaults to `runtime/verification-results/`: `execution-log.xes` and `verification.json` are P1 compliance evidence; `p2-completion.json` is P2 execution evidence only. No P2 DCR model or P2 DCR conformance log has been delivered, so P2 DCR verification remains outstanding. The expanded route/risk offline regressions are in `stage_tests/test_stage1.py`; they stub TomTom, Siddhi and Zeebe. A separate live acceptance run with configured services must confirm telemetry delivery, simulator acknowledgement, route movement/ETA, automatic arrival and P3 receiver deduplication.

#### Implemented in `p2-updates` and remaining acceptance work

The scripted route, position/ETA journal, optional TomTom adapter, durable Siddhi telemetry outbox, `DeliveryRiskDetected` and dispatcher decision BPMN branch, idempotent local route-change control, automatic arrival, and CLI controls are implemented in `deployer/transport_integrations.py`, `deployer/app.py`, `deployer/registry.py`, `bpmn/transport.bpmn`, and `tools/demo.py`. Offline regression coverage is in `stage_tests/test_stage1.py`. Actual live TomTom/Siddhi/simulator behavior has not been verified without credentials and endpoints.

| Remaining acceptance item | Entry points | Files to update if a follow-up fix is needed | Dependency / owner | Acceptance check |
| --- | --- | --- | --- | --- |
| Confirm P3 arrival receiver and payload | `Registry.record_arrival`, `deliver_messages`, P3 receiver | `deployer/registry.py`, `deployer/app.py`, agreed shared event schema, P3 receiver, `stage_tests/test_stage1.py`, `stage_tests/check_stage1.py`, this agreement, `project_notes/TASKS.md`, `README.md`, `docs/RUNNING.md` | P3 owner confirms event name/envelope, 2xx acknowledgement and receiver dedupe | Retry one `deliveryId`; P3 starts/advances once and retains shipment/journey/product/batch plus replacement/incident refs |
| Verify scripted GPS and automatic arrival | `start_journey`, `advance_simulated_journeys`, `Registry.advance_telemetry` | `deployer/app.py`, `deployer/registry.py`, `deployer/transport_integrations.py`, `bpmn/transport.bpmn`, `stage_tests/check_stage1.py`, `stage_tests/test_stage1.py`, `tools/demo.py`, this agreement, task board, README, `docs/RUNNING.md` | Fresh matching registry/Zeebe data; simulator enabled | Observe movement for two shipments, one arrival per shipment, P2 arrival catch and reserved vehicle until receipt |
| Verify TomTom ETA | `TransportIntegrations.eta` | `deployer/transport_integrations.py`, `deployer/app.py`, Compose configuration, `stage_tests/test_stage1.py`, live check, this agreement, task board, README, `docs/RUNNING.md` | Team TomTom key and connectivity | Confirm ETA requests use current location/remaining route, record `tomtom-traffic` source, and preserve local fallback when key is absent |
| Verify Siddhi telemetry and risk | `deliver_messages`, `siddhi-apps/ColdChainTelemetry.siddhi`, `/integrations/siddhi/delivery-risk` | `siddhi-apps/ColdChainTelemetry.siddhi`, `deployer/app.py`, `deployer/registry.py`, Compose configuration, `stage_tests/test_stage1.py`, live check, this agreement, task board, README, `docs/RUNNING.md` | Fresh live run with Siddhi enabled; use a deadline shorter than route ETA | Siddhi receives durable telemetry; one risk alert reaches the correct P2 instance and creates one pending decision |
| Verify acknowledged route change | `apply_dispatcher_decision`, Siddhi `/route-command`, local scripted simulator receiver | `siddhi-apps/ColdChainTelemetry.siddhi`, `bpmn/transport.bpmn`, `deployer/app.py`, `deployer/registry.py`, `deployer/transport_integrations.py`, Compose configuration, `stage_tests/test_stage1.py`, live check, this agreement, task board, README, `docs/RUNNING.md` | Matching command ID and simulator acknowledgement; external simulator contract is separate | Repeated command IDs apply once; missing/mismatched acknowledgement leaves the old route; later movement and ETA use the approved route |
| Rehearse dispatcher CLI controls | `tools/demo.py` actions 11–13 | `tools/demo.py`, API/registry files if a defect is found, `stage_tests/test_stage1.py`, this agreement, task board, README, `docs/RUNNING.md` | Running stack and an in-transit shipment | CLI can generate a risk, accept delay or approve an alternate route, then display its acknowledgement and updated position/ETA |
| Full P2 DCR verification | P2 activity journal and a P2-only compliance model | New `compliance/p2/rules.xml`, P2 execution log/report, `stage_tests/check_stage1.py`, `stage_tests/test_stage1.py`, `compliance/check.py` if required, this agreement, `project_notes/TASKS.md`, `README.md`, `docs/RUNNING.md` | Agree required/forbidden P2 activities and terminal obligations; P2 compliance owner | Replay actual P2 traces and publish conformance evidence separately from P1 |

No follow-up PR/issue links or owners have been agreed yet. Add links and named owners here when the team assigns them. Until then, treat the proposed P3 and Siddhi payloads as provisional and keep endpoints configurable.

P2 receives `shipmentId` and the complete `dispatchEvent` envelope at its start. Registration returns top-level `shipmentId`, `journeyId`, `vehicleId`, `recipientId`, `routeId`, `deliveryDeadline`, `quantity`, `productId`, `productName`, `batchId`, `quantityUnit`, `packDescription`, `packsPerCrate` and `temperatureProfileId` as engine variables. Replacement/incident references remain available in `dispatchEvent`; preserve them when the P3 notification and later events are implemented.

`Registry.advance` is specific to P1. P2 uses process-specific transactional methods for journey start, arrival recording and vehicle release. P3 still needs its own executable model/service and a durable producer/consumer contract. Physical arrival does not complete transport; receipt does not permit stock admission by itself.

Read the current shipment through the state endpoint or shared registry; do not introduce a separate quality-status copy in another service or process variable. Every admission attempt must check permission in the same transaction/order as hold changes. P4's incident handlers and that admission operation are still work to implement.

Process definitions are deployed on service startup. After editing a model, restart the communication service; after Python changes, rebuild it with `docker compose up --build -d communication`. Existing instances stay on their original model version. Start fresh cases to test a changed definition, and preserve the existing P1 contract/tests.

## 4. Shared quality status and decisions

Delivery progress and quality permission are separate. Goods may be physically received while their quality status is `PENDING`.

| Quality status | Set when | Stock admission |
| --- | --- | --- |
| `CLEAR` | Shipment created, with no identified active concern | Allowed after receiving inspection passes |
| `PENDING` | A qualifying incident alert is accepted | Blocked |
| `RELEASED` | DCR confirms a release decision for the current incident | Allowed after inspection passes |
| `REJECTED` | DCR confirms replacement or return for the original goods | Blocked |

A small persistent shared registry holds shipment mappings and quality status. Camunda and DCR retain their own workflow state. P3 must check current permission at the admission action, rather than trust an old screen or process variable.

The incident handler records the hold before creating the DCR case. If DCR is unavailable, the hold stays active and the integration failure is visible. The shared service processes hold changes and admission decisions in order for each shipment so two competing requests cannot bypass the rule.

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
