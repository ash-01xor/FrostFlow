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

**Replacement ownership:** P5 requests a linked shipment through P1. It waits for that shipment's successful receiving outcome and for the recorded handling of the original goods. A replacement's dispatch alone is insufficient.

**Message timing:** configure buffering for Camunda messages that may arrive before a waiting subscription. Match messages to the intended process instance using the business ID; include incident and command IDs where episodes could otherwise be confused. Test this during the first process integration.

### Implemented preparation and handoff boundary

P1 accepts an order before checking stock. A shortage keeps a real P1 instance at an event-based gateway. `StockReplenished` returns it to the stock check; `ShipmentCancellationRequested` ends only a stock-waiting shipment and releases its planned vehicle. Cancellation and replenishment compete for one waiting episode. Each episode has a distinct `stockWaitKey`; loading/dispatch continue to correlate by shipment ID.

`POST /shipments/<id>/replenish-stock` accepts an external `eventId`, positive integer `quantity` and `actor`. It adds inventory for the addressed shipment's exact product and batch once, and queues the targeted notification in the same transaction. Optional `productId`/`batchId` labels must match that shipment. A supply of milk cannot clear a yoghurt shortage. Replenishment represents another receipt of the same demonstration batch; adding new batches is outside the current API. Exact retries do not add more stock; changed content under the same ID is rejected. API validation of a verification requires all loading checks to be true; no damaged-packaging branch is implemented.

Dispatch records the agreed `ShipmentDispatched` envelope and durable outbox entry together. Its payload is passed as `dispatchEvent` to P2's message start. P2's registration worker checks the event against the journal, persists its engine key and acknowledges the outbox. Repeated worker delivery reuses that registration; a different P2 instance is prevented from repeating the business effect. Message IDs and active-start correlation also suppress repeated engine starts within the supported retention period.

The current P2 instance waits at `WaitForTransportImplementation`. GPS movement, arrival, receipt and vehicle release still belong to P2's remaining implementation. Publication succeeds before acknowledgement; stock/loading/dispatch APIs therefore return accepted status while workers may still be processing. Published messages are not repeatedly sent; uncertain pending publications are retried for at most 24 hours, after which recovery requires engine inspection. The registry and engine data must be preserved together. Dataset version 3 uses five product/batch inventory rows; legacy pooled-stock datasets are preserved and rejected by the updated service until a fresh project/directory is selected. The [dataset reference](RUNNING.md#demonstration-dataset) defines the seed values. Product/batch identity is recorded in each activity payload and exported as XES attributes; the DCR graph continues to check ordering rather than numeric stock or temperature limits.

### P2 and P3 development handover

Use the existing Compose stack and shared Python service. Each owner can build/test their process on a feature branch without creating another copy of the application.

| Area | Files and current boundary | Next work |
| --- | --- | --- |
| P2 model | `bpmn/transport.bpmn`, process ID `ShipmentTransport` | Preserve the `ShipmentDispatched` message start and `p2-start-transport` registration task. Replace `WaitForTransportImplementation` and its reserved `TransportContinuation` message with actual journey/arrival/receipt handling. |
| P2 workers | `deployer/app.py`: `start_transport`, worker registration in `main` | Add handlers with matching BPMN job types. Register each handler and fetch the variables it needs. The existing `JOB_TYPES` list and handler sequence must stay in the same order. |
| Shared business state | `deployer/registry.py`: `start_transport`, transactions and activity journal | Keep P1's `state` as preparation status; store transport progress in `transportState`. Preserve reservations until P2's completion rule allows vehicle release. Use transactions and stable activity identities for retries. |
| P3 model/service | Not implemented | Add a receiving BPMN resource and deploy it from `main`, with matching workers and human-action endpoints. Consume `ShipmentArrived` once, record receipt, and send `ReceiptAcknowledged` to P2. |
| P3 quality permission | Only initial `qualityStatus: CLEAR` exists today | Implement the shared hold/admission transaction with P4; current CLEAR initialization is not an implemented quality-control lifecycle. Test PENDING, RELEASED and REJECTED, including a later incident and stale decisions. |
| Checks and demo | `stage_tests/`, `tools/demo.py` | Retain P1 regression coverage. Replace the live check's expectation of the temporary P2 wait when the new workflow is ready; extend controls/scenarios for the new human actions. Regenerate changed model PDFs. |

P2 receives `shipmentId` and the complete `dispatchEvent` envelope at its start. Registration returns top-level `shipmentId`, `journeyId`, `vehicleId`, `recipientId`, `routeId`, `deliveryDeadline`, `quantity`, `productId`, `productName`, `batchId`, `quantityUnit`, `packDescription`, `packsPerCrate` and `temperatureProfileId` as engine variables. Replacement/incident references remain available in `dispatchEvent`; preserve them when emitting later events. The registration worker acknowledges the outbox and records `p2ProcessInstanceKey`; retries preserve any subsequent transport progress.

`Registry.advance` is specific to P1: it validates P1's engine key, changes preparation state and records P1 activities. Add process-specific transactional methods for P2/P3 progress. `Registry.event` currently maps sources and engine keys for P1/P2 only; extend that mapping for P3's receiving source and its own engine key before recording receiving activities.

P2 and P3 should agree on arrival and receipt payloads before implementing their connection, using the message table above. Physical arrival does not complete transport; receipt does not permit stock admission by itself. For independent P3 development, clearly labelled manual/test arrival inputs can exercise receiving before P2's simulator exists. They must not be described as an implemented end-to-end simulator connection.

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
