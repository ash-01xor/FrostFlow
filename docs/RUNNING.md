# Running and testing P1–P6

**P4 reintegration (replacement PR, branch `feat/p4-foundation-integration`):** Abid’s P4 from [PR #10](https://github.com/ash-01xor/FrostFlow/pull/10) was reverted by PR #11 so foundation [PR #9](https://github.com/ash-01xor/FrostFlow/pull/9) could merge first (merged as `ae941cc`). [PR #12](https://github.com/ash-01xor/FrostFlow/pull/12) restores P4 by reverting `31ad23b` and adapts it to the [foundation contract v1](P4-FOUNDATION-HANDOVER.md); see [sequencing](P4-INTEGRATION-SEQUENCING.md#checkpoint-4--replacement-p4-pr) and the [P4 guide](P4-QUALITY-INVESTIGATION.md). Review, merge and post-merge verification outcomes are recorded on PR #12.

Run commands from the repository root. P1 responsible student: **Ashvanth**. The implemented scope includes shipment preparation, scripted P2 GPS/ETA and dispatcher decisions, P2-to-P3 arrival handoff, P3 receipt/inspection and separate receiving inventory, and the P1 stock shortage/cancellation paths. TomTom is optional; current deterministic verification uses local demo ETAs. P4's quality investigation runs as a DCR case ([P4 guide](P4-QUALITY-INVESTIGATION.md)); P5 handles authorized corrective work ([P5 guide](P5-CORRECTIVE-FULFILMENT.md)); the physical/external simulator remains team integration work.

## Demonstration dataset

Team decision, 1 November 2026: use five distinct chilled items instead of unnamed pooled crates. The seed data is [deployer/catalog.json](../deployer/catalog.json); it is copied into SQLite only when a fresh registry is created.

| Product ID | Product | Retail pack | Packs per crate | Batch ID | Initial stock |
| --- | --- | --- | --- | --- | --- |
| PRD-001 | Fresh milk | 1 litre carton | 12 | DEMO-MILK-001 | 20 crates |
| PRD-002 | Plain yoghurt | 500 gram tub | 12 | DEMO-YOGHURT-001 | 20 crates |
| PRD-003 | Fresh cheese | 250 gram sealed pack | 12 | DEMO-CHEESE-001 | 20 crates |
| PRD-004 | Raw chicken breast | 500 gram sealed pack | 12 | DEMO-CHICKEN-001 | 20 crates |
| PRD-005 | Prepared salad | 250 gram sealed bag | 12 | DEMO-SALAD-001 | 20 crates |

A fresh dataset has **100 crates / 1,200 retail packs in total**, zero shipments, two available vehicles (V-01/V-02) and three recipient labels (C-01/C-02/C-03). A crate is a counting/handling unit, not a common weight or volume across products. These quantities and pack sizes are fictional demonstration assumptions.

One shipment carries one product and one batch; a vehicle carries one active shipment. Stock is reserved atomically by `(productId, batchId)`. Replenishment credits that exact pair; it cannot borrow from other products or batches. Reopening a registry does not restore seed stock. Seed changes apply only to new datasets. The current API uses one seeded batch per product; supplier purchasing, new-batch creation, mixed loads, shelf life, hygiene/segregation and physical vehicle capacity are outside this increment.

All products carry `temperatureProfileId: CHILLED-DEMO`. New shipments snapshot the approved configurable `CHILLED-DEMO-v1` assessment settings described in [the foundation contract](PRE-P4-FOUNDATION.md). These numeric triggers are demonstration settings, not validated product-specific storage rules or food-safety suitability.

`GET /stock` lists each product/batch, pack definition, initial crates and current availability. Its top-level `available` is only the sum across products; shipment eligibility uses the selected product/batch. The response also contains `datasetVersion: 3` and `shipmentCount`.

The local dataset stores product definitions, warehouse batch inventory, separate recipient/product/batch receiving inventory, shipment/vehicle reservations, states and P1/P2/P3/P5 engine keys, corrective cases/collection bookings, activity history, pending messages/acknowledgements and retry identities in SQLite under the selected `REGISTRY_DIR`. Zeebe/Elasticsearch store engine execution and inspection records in the matching Docker volumes. Generated databases and live-run evidence are ignored under `runtime/`. The committed `compliance/p1/` files are P1 conformance evidence, not P2 execution evidence.

## Start the application

Start Docker Desktop, then:

```bash
docker compose up --build -d --wait --wait-timeout 180
curl -s http://localhost:5001/health
```

Wait for `status: ready` and `stage: 3`. All four BPMN resources (P1/P2/P3/P5) must deploy before readiness. Operate is at <http://localhost:8081> (`demo` / `demo`). Open [P1](../bpmn/shipment_preparation.bpmn), [P2](../bpmn/transport.bpmn), [P3](../bpmn/receiving.bpmn) and [P5](../bpmn/corrective_fulfilment.bpmn) in Camunda Modeler.

### P2 integration settings

The default stack runs the scripted route with a local ETA estimate and a local simulated route-command acknowledgement. `/health` reports these modes. `P2_SIMULATOR_ENABLED` defaults to `true`; set it to `false` only when running the legacy live check that drives arrival through the manual endpoint. To connect real integrations, set these variables in PowerShell before `docker compose up`:

```powershell
$env:TOMTOM_API_KEY = "<TomTom key>"
$env:SIDDIHI_TELEMETRY_URL = "http://<siddhi-host>:<port>/<telemetry-path>"
$env:SIDDIHI_COMMAND_URL = "http://<siddhi-host>:<port>/<command-path>"
# Optional: the local P3 receiver is the default.
# $env:P3_ARRIVAL_URL = "http://<p3-host>:<port>/integrations/p3/arrival"
$env:GPS_TICK_SECONDS = "5"
$env:P2_SIMULATOR_ENABLED = "true"
docker compose up --build -d --wait --wait-timeout 180
Invoke-RestMethod http://localhost:5001/health
```

With `TOMTOM_API_KEY` set, each ETA is a traffic-aware TomTom route from the current position (`etaSource: tomtom-traffic`). If TomTom fails or returns unusable data, the ETA is recorded as unavailable (`tomtom-unavailable`, no ETA value): it is never replaced by an estimate, and Siddhi does not compare it with the deadline. At the last route point the source is `at-destination`. Each request is appended, without the key, to `tomtom-responses.jsonl` beside the registry under `runtime/`. Never commit the key; set it only in your shell.

The communication container must be able to reach configured hostnames. Siddhi telemetry is sent as `{"event": <activity>}` with an `Idempotency-Key` header. The command endpoint receives `commandId`, `shipmentId`, `journeyId`, `riskEventId`, `command`, and `routeId`; it must return the same `commandId` and `simulatorAcknowledged: true` only after the simulator applies the command. By default P2 sends its durable arrival notification to the local `POST /integrations/p3/arrival` receiver, including `{"event": <notification>}` and an `Idempotency-Key` equal to `event.deliveryId`. Override `P3_ARRIVAL_URL` only for an external receiver that honors this contract.

Published localhost ports: Python 5001, Operate 8081, Elasticsearch 9200, Zeebe gRPC 26501 and broker management 9601. Stop another stack using these ports before starting this one. For startup problems:

```bash
docker compose ps
docker compose logs --tail=100 communication zeebe
```

The walkthrough needs fresh inventory (20 crates of each of five products; 100 total) and available vehicles. The automated check completes two P2 transports and exercises a released vehicle; the manual scenario reserves its vehicle until receipt acknowledgement. Previous local demonstrations may already have consumed these resources. See persistence/reset below; changing shipment IDs alone does not release a vehicle.

## Presentation controls

After starting a fresh stack, run:

```bash
python tools/demo.py
```

This terminal menu sends the same application requests as the API. It waits for state changes and prints P1/P2/P3 instance keys to find in Operate. Choose an action number, then shipment **A**, **B**, **C** or an existing shipment ID (including a replacement). Creation also offers a choice of all five products; press Enter to use milk for the shortage scenario. After dispatch, the scripted route updates position/ETA and sends arrival automatically; the local P3 process starts from that durable notification and waits for physical receipt. Action **9** is an optional manual/test P2 arrival. Action **10** is a P2-only receipt shortcut for runs with P3 switched off. While P3 receiving is enabled (the default), it is refused with 409 and the CLI points you to **14**. Action **11** creates a test risk alert; **12** accepts the delay and **13** approves a different route. Action **14** confirms physical receipt in P3 and acknowledges P2, releasing the vehicle; action **15** submits a passing P3 inspection and attempts admission; action **16** only displays the separate receiving inventory—it does not trigger receipt or inspection. Use `python tools/demo.py --check` to check readiness and warehouse stock without changing data.

P3 endpoints are `POST /shipments/<id>/receiving/receipt` (`eventId`, UTC `occurredAt`, `recipientId`, `quantityReceived`, `receiver`, optional `receiptReference`) and `POST /shipments/<id>/receiving/inspection` (`eventId`, UTC `occurredAt`, `inspector`, boolean `passed`, optional `notes`). Physical receipt must match the shipment's recipient and crate count. Only passed inspections with `qualityStatus` `CLEAR` or `RELEASED` add stock. `GET /receiving-stock` lists admitted goods, optionally filtered with `?recipientId=C-01`. `POST /integrations/p4/quality-status` accepts `PENDING`, `RELEASED` or `REJECTED` dispositions; `PENDING` holds stock admission and a subsequent release/rejection resumes the P3 process. This integration endpoint is internal-demo only and has no authentication.

Suggested P1–P3 path for a fresh demo: create A, verify loading (3), dispatch (4), wait for P2 arrival, confirm physical receipt (14), inspect (15), then view receiving inventory (16). P2's vehicle may be released after step 14 while P3 still waits for inspection; receiving-stock remains unchanged until step 15 completes admission.

For the normal P1 demonstration:

| Menu choice | What happens | What to explain/show |
| --- | --- | --- |
| 1, then A | Create 15-crate milk order (product 1); reserve stock and await loading | Find P1's printed key in Operate; it is waiting for a warehouse confirmation |
| 4, then A | Dispatch is rejected | Loading verification is required |
| 3, then A | Confirm the simulated loading checks | P1 moves to the dispatch-approval wait |
| 4, then A | Dispatch; register P2 | P1 completes; P2 starts the scripted journey and records position/ETA updates |
| while in transit: 11, then A; 12 or 13, then A | Optional manual risk and dispatcher decision | Use a different compatible route for route approval; verify acknowledgement and subsequent movement. For actual Siddhi detection, use the short-deadline walkthrough instead |
| wait, then 2, then A | Observe automatic arrival and P3 starting | P2 waits for receipt; V-01 stays reserved; P3 reaches `AWAITING_RECEIPT` |
| 14, then A | Record physical receipt through P3 | P3 acknowledges P2; transport completes and V-01 is released |
| 15, then A | Submit passing receiving inspection | Clear goods are admitted; risk goods stay on hold until current-incident release |
| 16 | Inspect receiving inventory | Only admitted goods appear; receipt alone does not add stock |

For a stock exception, choose **1, B**, then milk again: 10 milk crates are requested while only five milk crates remain. The other four products still have 20 crates each; they cannot satisfy this order. Show the stock-wait gateway, then choose either **5, B** to replenish five milk crates and continue preparation, or **6, B** to cancel. After cancelling B, **1, C** creates another stock-wait case on the released vehicle if you want to rehearse the other branch. Menu **2** displays state/keys; **7** displays the activity history; **8** lists product/batch stock; **0** exits without stopping the stack.

Operate displays engine execution. The terminal represents the person submitting actions; this setup has no Tasklist forms for those actions. Keep both windows open, refresh Operate when needed, and use its instance key and diagram to explain the current wait. The menu explicitly confirms demonstration loading checks; it does not inspect physical goods.

The dataset must be prepared before recording/presenting. The menu preserves existing data and does not reset containers or reservations. A used dataset may have unavailable vehicles even if stock remains; use the isolated-dataset instructions below to rehearse again.

For the final **three-minute** project demo, reserve roughly 25 seconds for preparation. Build one connected story as later parts become available: dispatch → arrival/receipt → temperature incident → investigation/hold → authorised resolution → corrective work/claim → compliance results. Show process behaviour and evidence at each transition. P3 receiving, Abid’s P4 investigation (CLI **19**) and P5 corrective fulfilment (CLI **20**) are implemented. P6 claims are implemented through the [claim API](#p6-supplier-claims); automatic cooling/recovery is in progress (P4 side implemented offline, simulator/Siddhi side pending; [draft contract](P4-COOLING-CONTRACT.md)).

### P3 compliance evidence

`compliance/p3/` contains the P3 DCR graph, an XES log captured from four live P1/P2/P3 runs, and the local replay/forbidden-admission result. The scenarios cover normal receipt/admission, quality hold → release, inspection failure/rejection and quality rejection. The hold traces include the accepted PENDING integration request and P3's `StockAdmissionHeld` event, plus neighbouring dispatch, arrival, receipt, inspection and disposition events. P4 dispositions were sent by the test harness to the P3 integration endpoint; this does not claim that a P4 DCR process is deployed or that cross-process P4 integration is complete.

Stop the active project first. To reproduce with the alternate-port overlay, use a new project name and fresh registry together; on subsequent runs change both `cold-chain-p3-compliance` and `p3-compliance-run-01`:

```powershell
$env:REGISTRY_DIR = '.\runtime\p3-compliance-run-01'
docker compose -p cold-chain-p3-compliance -f docker-compose.yml -f stage_tests\p3-compliance.compose.yml up --build -d --wait --wait-timeout 240
python stage_tests\check_p3_compliance.py --base http://localhost:5011 --output runtime\p3-compliance\evidence.json
python compliance\p3\build_p3_evidence.py log runtime\p3-compliance\evidence.json
python compliance\p3\build_p3_evidence.py verify compliance\p3\execution-log.xes
docker compose -p cold-chain-p3-compliance -f docker-compose.yml -f stage_tests\p3-compliance.compose.yml down
```

The log/verification builder commands overwrite their target artifacts. Preserve historical external results and add the new tested revision, environment, hashes and actual outcomes before committing regenerated evidence. Do not run them for documentation-only edits.

Choose a new project name and empty `REGISTRY_DIR` together each time you repeat this run; preserve the old registry with its matching engine volumes. The runner requires an empty version-3 registry and exercises the real engine and durable event registry. The DCR replay must accept all four actual traces and reject the synthetic attempt to admit stock while a quality hold is unresolved. `compliance/p3/verification.json` records the current local replay, forbidden-admission check and external-check status. Historical ProcessDoctor results apply only to their original logs; repeat the external check whenever the model or log changes. The runner waits for `AWAITING_LOADING` with no pending command before verification, using bounded polling.

## Manual walkthrough

Use a fresh paired dataset and the default local P3 receiver. Set example delivery deadlines to a future UTC time for your run, and use actual UTC observation/receipt times. Example timestamps below are illustrative. The default simulator publishes arrival automatically.

### 1. Reserve most of the milk stock

Create shipment S-001 for 15 crates of milk, batch DEMO-MILK-001:

```bash
curl -s -i -X POST http://localhost:5001/shipments \
  -H 'Content-Type: application/json' \
  --data-binary @event_streams/shipment-request.json
curl -s http://localhost:5001/shipments/S-001
curl -s http://localhost:5001/stock
```

Creation returns **202**. Poll until `AWAITING_LOADING` with no `pendingCommand`; milk stock should be **5**, while total availability is **85**. The engine ran `CheckStock`, took the available branch, allocated stock and assigned transport. Checking and reserving are one transaction so another shipment cannot take the same crates between those steps.

Shipment IDs are at most 100 characters, start with an ASCII letter or digit, and contain only ASCII letters, digits, `.`, `_` or `-`. IDs containing slashes, percent escapes or other URL-special characters are rejected before reserving a vehicle or starting a process.

Try dispatch before loading verification:

```bash
curl -s -i -X POST http://localhost:5001/shipments/S-001/dispatch \
  -H 'Content-Type: application/json' -d '{"actor":"dispatcher-demo"}'
```

Expect **409**. A request does not skip the process's required steps.

### 2. Encounter a shortage and cancel

Create a second shipment for 10 crates of the same milk batch:

```bash
curl -s -i -X POST http://localhost:5001/shipments \
  -H 'Content-Type: application/json' \
  -d '{"shipmentId":"S-002","journeyId":"J-002","vehicleId":"V-02","recipientId":"C-02","routeId":"route-01","quantity":10,"productId":"PRD-001","batchId":"DEMO-MILK-001","deliveryDeadline":"2026-12-01T12:00:00Z"}'
curl -s http://localhost:5001/shipments/S-002
```

Poll until `WAITING_STOCK`. This is a running BPMN instance at the event-based gateway **Await stock or cancellation**. No stock is allocated to this shipment. Loading verification and dispatch both return 409.

```bash
curl -s -i -X POST http://localhost:5001/shipments/S-002/cancel \
  -H 'Content-Type: application/json' -d '{"actor":"dispatcher-demo"}'
curl -s http://localhost:5001/shipments/S-002
```

Expect **202**, then `CANCELLED`. In Operate, P1 ends at **Shipment cancelled** and no P2 is created. Cancellation releases the planned vehicle reservation; milk inventory remains five crates (85 crates total). Repeated cancellation and dispatch after cancellation return 409. Cancellation is supported only during stock waiting, not after allocation/loading/dispatch.

### 3. Replenish and check again

Create S-003 using the previous create command with `shipmentId: S-003` and `journeyId: J-003`. V-02 is available because S-002 was cancelled. It also reaches `WAITING_STOCK`.

```bash
curl -s -i -X POST http://localhost:5001/shipments/S-003/replenish-stock \
  -H 'Content-Type: application/json' \
  -d '{"eventId":"supply-001","actor":"warehouse-demo","quantity":1}'
curl -s http://localhost:5001/shipments/S-003
```

Expect **202**. The replenishment increases milk stock to six crates (86 total) and wakes this shipment. The engine loops back through **Check and reserve stock**; six is still insufficient for the ten-crate order, so it waits again with a new `stockWaitKey`. It does not assume that every replenishment makes stock sufficient.

Repeating the exact supply-001 request returns 202 without adding stock twice. Reusing that ID with different content returns 409. Supply IDs persist across service restarts.

```bash
curl -s -i -X POST http://localhost:5001/shipments/S-003/replenish-stock \
  -H 'Content-Type: application/json' \
  -d '{"eventId":"supply-002","actor":"warehouse-demo","quantity":4}'
```

Poll until S-003 reaches `AWAITING_LOADING` with no `pendingCommand`. It has now reserved ten milk crates; available milk stock is zero. The other four products still have 20 crates each, so total availability is 80. A replenishment updates the shared inventory but wakes only its addressed shipment. Other waiting shipments need their own accepted replenishment action. This prototype does not implement supplier purchasing or warehouse stock optimisation.

### 4. Verify loading and dispatch

For S-003:

```bash
curl -s -i -X POST http://localhost:5001/shipments/S-003/verify-loading \
  -H 'Content-Type: application/json' \
  -d '{"actor":"warehouse-demo","quantityChecked":true,"packagingChecked":true,"vehicleReady":true}'
curl -s http://localhost:5001/shipments/S-003
```

Poll until `AWAITING_DISPATCH`, then:

```bash
curl -s -i -X POST http://localhost:5001/shipments/S-003/dispatch \
  -H 'Content-Type: application/json' -d '{"actor":"dispatcher-demo"}'
curl -s http://localhost:5001/shipments/S-003
curl -s http://localhost:5001/shipments/S-003/events
```

P1 reaches `DISPATCHED`. Poll until `handoffStatus` is `ACKNOWLEDGED`: the record now includes a distinct `p2ProcessInstanceKey`; P2 starts the journey and waits at **Wait for shipment arrival** with `transportState: IN_TRANSIT`. Inspect P2 variables for shipment, journey, product, batch, unit/pack definition, vehicle, recipient, route, quantity and deadline. Repeat verification/dispatch attempts return 409.

### 5. Receive through P3, then inspect

The default integrated flow records receipt in P3. CLI option **10** and `POST /shipments/<id>/acknowledge-receipt` are P2-only test shortcuts and return **409** while P3 receiving is enabled, even after arrival. Use option **14** or the receiving endpoint below.

If S-003 is still in transit, an early P3 receipt must return **409** because P3 has not recorded arrival:

```bash
curl -s -i -X POST http://localhost:5001/shipments/S-003/receiving/receipt \
  -H 'Content-Type: application/json' \
  -d '{"eventId":"receipt-S-003-001","occurredAt":"2026-11-15T10:24:00Z","recipientId":"C-02","quantityReceived":10,"receiver":"cafeteria-demo","receiptReference":"RCPT-003"}'
```

Wait for automatic arrival and P3's `AWAITING_RECEIPT`. P2 remains `ARRIVED` and `vehicleReleased: false`. The following PowerShell helper bounds each wait; keep it available for the later steps:

```powershell
function Wait-Shipment {
  param([scriptblock]$Condition, [string]$Description)
  $deadline = (Get-Date).AddSeconds(120)
  do {
    $record = Invoke-RestMethod http://localhost:5001/shipments/S-003
    if (& $Condition $record) { return $record }
    Start-Sleep -Seconds 1
  } while ((Get-Date) -lt $deadline)
  throw "Timed out waiting for $Description; last state: $($record | ConvertTo-Json -Compress)"
}
$record = Wait-Shipment { param($r) $r.transportState -eq "ARRIVED" -and $r.p3Status -eq "AWAITING_RECEIPT" } "arrival and P3 receipt readiness"
$record | Format-List shipmentId,transportState,p3Status,vehicleReleased
```

In Operate, P2 waits at `WaitForReceiptAcknowledged` and P3 waits for physical receipt. Submit the receipt command above now; expect **202**. If the earlier command was rejected, it did not consume the event identity. Use actual UTC receipt time, and keep the same accepted ID/payload for retries.

```powershell
$record = Wait-Shipment { param($r) $r.transportState -eq "COMPLETED" -and $r.vehicleReleased -and $r.p3Status -eq "AWAITING_INSPECTION" } "P2 completion and P3 inspection readiness"
$record | Format-List shipmentId,transportState,p3Status,vehicleReleased
```

P2 reaches `TransportCompleted` and the vehicle becomes available. P3 has not admitted goods yet. Submit a passing inspection (CLI option **15**):

```bash
curl -s -i -X POST http://localhost:5001/shipments/S-003/receiving/inspection \
  -H 'Content-Type: application/json' \
  -d '{"eventId":"inspection-S-003-001","occurredAt":"2026-11-15T10:25:00Z","inspector":"cafeteria-demo","passed":true}'
```

Expect **202**, then inspect the admission result:

```powershell
$record = Wait-Shipment { param($r) $r.p3Status -in @("ADMITTED", "ON_HOLD", "REJECTED") } "inspection and admission outcome"
$record | Format-List shipmentId,p3Status,qualityStatus,qualityIncidentId
```

```bash
curl -s 'http://localhost:5001/receiving-stock?recipientId=C-02'
curl -s http://localhost:5001/stock
```

For clear goods and a passing inspection, P3 reaches `ADMITTED` and C-02 receives ten milk crates. Warehouse availability remains 80; receiving never credits warehouse stock. A risk shipment remains `ON_HOLD` until the P4 investigation (CLI **19**) releases its current incident with the assessed evidence version (option **17** is a stand-in that bypasses P4); rejection never adds receiving stock. See the risk walkthrough for hold/release and `check_p3_compliance.py` for all four admission/rejection scenarios.

An exact retry of an accepted receipt or inspection returns **202** without repeating stock or journal effects. Changed content under the accepted identity returns **409**. The optional `/arrive` endpoint remains a manual/test input for simulator-off runs; automatic arrival is used in this walkthrough. P2's durable arrival notification and P3 receipt acknowledgement are distinct messages, and damaged-packaging loading exceptions remain outside this increment.

## What the BPMN and Python each do

| BPMN element | Worker/message | Business effect |
| --- | --- | --- |
| CheckStock | `p1-check-stock` | Atomically check/reserve inventory; return boolean `stockAvailable` |
| StockAvailable | Exclusive gateway | Available branch or default shortage branch |
| WaitForStockDecision | Event-based gateway | First accepted replenishment or cancellation wins |
| WaitForReplenishment | `StockReplenished` | Return to stock check |
| WaitForCancellation | `ShipmentCancellationRequested` | Run cancellation worker and end P1 |
| AllocateStock | `p1-allocate-stock` | Record `StockAllocated` for reserved stock |
| AssignTransport | `p1-assign-transport` | Record transport assignment; await loading |
| RecordLoading | `p1-record-loading` | Record validated human confirmation |
| DispatchShipment | `p1-dispatch` | Record dispatch and enqueue P2 handoff atomically |
| CancelShipment | `p1-cancel` | Record cancellation and release vehicle |
| P2 StartTransport | `p2-start-transport` | Validate dispatch, remember P2 key, acknowledge handoff |

The API validates actions and queues messages; **202 means accepted**, not that the engine has consumed them. Workers update the registry just before completing their jobs. Confirm completion in Operate/exported engine records as well as the state endpoint.

Messages for a stock wait correlate by its episode-specific `stockWaitKey`; old messages cannot satisfy a later stock wait. Loading/dispatch messages correlate by `shipmentId`. SQLite serializes competing commands, so replenishment and cancellation cannot both be accepted for one waiting episode. Actors and stock/readiness checks are demonstration inputs, not authenticated identities or physical measurements.

The durable outbox survives restarts and retries failed publications with stable message IDs. A received dispatch is acknowledged by P2's worker; its persistent inbox prevents a second business effect and rejects a different engine instance for the same shipment. Published messages are not periodically resent. Pending uncertain publications stop retrying after 24 hours; inspect engine state before recovery. This avoids claiming unbounded exactly-once delivery from finite engine message retention. See [Camunda's message behaviour](https://docs.camunda.io/docs/8.8/components/modeler/bpmn/message-events/).

## DCR compliance: learn from this process

The [P1 DCR XML](../compliance/p1/rules.xml) is a compliance model, separate from the BPMN that runs P1. An **event** is an observed business activity, such as `StockAllocated` or `ShipmentDispatched`. The graph's marking remembers which events were executed, which are included, and which responses remain pending.

| Relationship | Meaning | P1 example |
| --- | --- | --- |
| Condition | An included prerequisite must already have occurred | StockAllocated → LoadingVerified; LoadingVerified → ShipmentDispatched |
| Response | Execution creates an obligation | ShipmentCreated → ShipmentDispatched |
| Include | Make an activity relevant/available | StockUnavailable includes replenishment and cancellation |
| Exclude | Remove an activity from the current scope | Cancellation excludes all later preparation actions |
| Milestone | Block an action while an included response is pending | Pending replenishment blocks allocation and dispatch |

Conditions and milestones inspect **included** prerequisites. The graph combines these relationships: an allocated/verified activity excludes itself after execution, while its successful execution is retained in the marking. Replenishment includes a new stock check and makes it pending, so allocation cannot skip the recheck. Cancellation excludes pending dispatch/replenishment obligations, making cancellation a legitimate terminal outcome. Dispatch excludes all P1 events to prevent further preparation. These are standard flat DCR relationships; see the [DCR XML/marking reference](https://documentation.dcr.design/documentation/dcr-xml/) and [original representation](https://raghavamukkamala.github.io/files/pubs/2013_DCRGraphs_BPM2013.pdf).

Compare these traces:

```text
Valid:
ShipmentCreated → StockChecked → StockUnavailable → StockReplenished
→ StockChecked → StockAllocated → TransportAssigned → LoadingVerified
→ ShipmentDispatched

Valid cancellation:
ShipmentCreated → StockChecked → StockUnavailable → ShipmentCancelled

Deliberately invalid:
ShipmentCreated → StockChecked → StockUnavailable → ShipmentCancelled
→ ShipmentDispatched  [excluded: cancellation already ended preparation]
```

**Valid** means every observed event was enabled. **Accepting** also means no included obligation remains pending. A trace ending at stock waiting can contain only valid events and still be unfinished. The local checker reads the XML relationships directly; it does not use the HTTP state guards.

Replay the saved **actual execution log**:

```bash
python3 compliance/check.py compliance/p1/execution-log.xes
```

Expect three valid, accepting cases: ordinary dispatch, cancellation and dispatch after repeated replenishment. Then replay the separate synthetic negative example:

```bash
python3 compliance/check.py compliance/p1/invalid-trace.json
```

Expect `valid: false`, `ShipmentDispatched`, and `activity is excluded`; exit code **1 is expected**. The invalid trace is explicitly labelled and is not part of the execution log.

Exit code **2** means the evidence could not be checked. Empty logs, unsupported XES namespaces, missing activity/case names and duplicate XES case names are rejected rather than reported as compliant. XES input must use the namespace `http://www.xes-standard.org/`.

`compliance/check.py` implements flat conditions, responses, includes, excludes and milestones, with no data/timing/hierarchy extensions. Unsupported/malformed model structures are rejected; logged activity names must match the XML event IDs. The graph checks sequencing and obligations; numeric stock quantities and real-world loading truth remain application/evidence checks. The graph and saved log have also been checked on **processdoctor.compute.dtu.dk** using the workflow below. This is P1 compliance checking; P4 still needs its separate executable investigation in a DCR PAIS.

## ProcessDoctor conformance check

Use <https://processdoctor.compute.dtu.dk/>. Docker can remain stopped because this check uses the saved model and log files.

1. On the home screen choose **Conformance**. If another workspace is open, use the home icon first. Log Generation is a separate workspace.
2. Expand **Open Model** and choose **Open DCR Solution XML**. Select `compliance/p1/rules.xml`. **Open Editor XML** expects ProcessDoctor's own editor format.
3. Select **Upload Log** and choose `compliance/p1/execution-log.xes`. It should display **3 traces | 3 trace variants**.
4. Keep **Coverage Threshold** at **100** and click **Check!**.
5. The reviewed result is **3 Conforming**, **0 Partially Violating**, **0 Violating** and **0 structural violations**. All three variants have green checks. Save any repeated run's actual results rather than assuming they will match.

The [positive screenshot](../compliance/p1/processdoctor-conforming.png) and [verification report](../compliance/p1/verification.json) record a real headless-browser check of the site. The separate synthetic cancellation-then-dispatch trace was classified **Partially Violating** with **one structural violation**; see the [negative screenshot](../compliance/p1/processdoctor-negative.png). It was checked separately and is not included in the real execution log. The local checker reports that same synthetic trace as invalid; the tools use different result classifications.

The DCR Solution importer requires portal metadata in addition to the logical graph: event `custom/eventData`, visualization coordinates and a `subProcesses` resource section. The corrected XML includes these fields. Its nine events, 53 relationships and initial marking are unchanged. A regression test checks the required metadata, and the saved real traces pass both local replay and ProcessDoctor. No application, BPMN workflow or original execution log was changed by this compatibility correction.

## Automated verification

Reuse the existing `.venv` for offline checks. If it is absent, create a Python 3.12 environment and install the application dependencies:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r deployer/requirements.txt
.venv/bin/python -m unittest discover -s stage_tests -p 'test_*.py' -v
docker compose config --quiet
npx --yes @camunda8/cli@4.6.1 bpmn lint bpmn/shipment_preparation.bpmn
npx --yes @camunda8/cli@4.6.1 bpmn lint bpmn/transport.bpmn
npx --yes @camunda8/cli@4.6.1 bpmn lint bpmn/receiving.bpmn
npx --yes @camunda8/cli@4.6.1 bpmn lint bpmn/corrective_fulfilment.bpmn
```

The offline suite includes the original **57 tests**, foundation monitoring/weather, P4 fault/race and P5 corrective/compliance regressions (P1, P2, P3, TomTom adapter, route model, quality hold and P2 DCR replay). The tests stub engine calls and external services; they do not replace the live runs below. On Windows without a local `grpc` install, run them in the service image: `docker run --rm -v "${PWD}:/repo" --entrypoint sh <project>-communication -c "cp -r /repo /tmp/r && cd /tmp/r && python -m unittest discover -s stage_tests -p 'test_*.py'"`.

The Siddhi delivery-risk rule has its own check, which needs only Docker: `python stage_tests/check_siddhi_risk_rule.py`. It starts a throwaway Siddhi runner and proves that repeated delivery of one ETA event, an isolated late ETA, stale late ETAs and a run broken by an unavailable ETA raise no alert, while three distinct consecutive fresh late ETAs raise exactly one alert per route revision.

Use one verification project at a time. Inspect `docker ps -a`, `docker inspect <container>` (mounts), the matching SQLite shipment count and engine volumes before selecting data. Existing test registries with shipments cannot satisfy fresh-data checks. Preserve their matching engine volumes and registry files. Stop any active project before starting this one.

The following POSIX-shell commands reuse one stack and change **both** its registry and named engine volumes between fresh suites. Choose a new suffix (`p1-01`, `p2-01`, `p3-01`) on subsequent runs; reusing a consumed suffix is not a reset. `verification.compose.yml` only selects paired named volumes. All data remains available after `stop`; use the same variables to resume it.

```bash
export COMPOSE_PROJECT_NAME=frostflow-repair
export VERIFY_DATASET=p1-01 REGISTRY_DIR=./runtime/repair/p1-01
export P2_SIMULATOR_ENABLED=false TOMTOM_API_KEY=
docker compose -f docker-compose.yml -f stage_tests/verification.compose.yml up --build -d --wait --wait-timeout 240
.venv/bin/python stage_tests/check_stage1.py
# Stop before changing datasets. Do not remove volumes.
docker compose -f docker-compose.yml -f stage_tests/verification.compose.yml stop

export VERIFY_DATASET=p2-01 REGISTRY_DIR=./runtime/repair/p2-01
export P2_SIMULATOR_ENABLED=true GPS_TICK_SECONDS=10
docker compose -f docker-compose.yml -f stage_tests/verification.compose.yml up -d --wait --wait-timeout 240
.venv/bin/python stage_tests/check_p2_siddhi.py
docker compose -f docker-compose.yml -f stage_tests/verification.compose.yml stop

export VERIFY_DATASET=p3-01 REGISTRY_DIR=./runtime/repair/p3-01 GPS_TICK_SECONDS=2
docker compose -f docker-compose.yml -f stage_tests/verification.compose.yml up -d --wait --wait-timeout 240
.venv/bin/python stage_tests/check_p3_compliance.py --base http://localhost:5001
.venv/bin/python compliance/p3/build_p3_evidence.py log runtime/p3-compliance/evidence.json
.venv/bin/python compliance/p3/build_p3_evidence.py verify compliance/p3/execution-log.xes
```

Expected results: P1 verifies shortage/replenishment/cancellation, two independent P2 completions, exact retries and vehicle reuse; P2 verifies Siddhi persistence, route acknowledgement/destination, exported Camunda variables, arrival closing an undecided alert, receipt-gated release and current-incident quality release; P3 verifies admission, hold/release, inspection rejection and quality rejection. P1 requires all 100 initial crates. The other suites also require fresh registries. P2/P3 use local demo ETAs here (`TOMTOM_API_KEY=`); live TomTom evidence is identified separately.

After merging, smoke-test an existing healthy default-port stack with free vehicles and at least one salad and one chicken crate:

```bash
.venv/bin/python stage_tests/check_integration_smoke.py
```

This creates two new shipments and checks admission and quality hold/rejection, Zeebe end events and exact warehouse/receiving deltas. It writes `runtime/verification-results/main-smoke.json`. It deliberately supports consumed inventory and does not rerun the fresh-data suites. The quality disposition comes from the test stand-in endpoint, so P4 cases opened by this smoke stay open; use `check_p4_investigation.py` for P4.

P1 writes `runtime/verification-results/{execution-log.xes,verification.json,p2-completion.json}`. P2 writes `runtime/verification-results/p2-siddhi.json`. P3 writes `runtime/p3-compliance/evidence.json`. Copy reviewed evidence and regenerate logs only after successful execution. Raw histories stay under ignored `runtime/`.

Remove obsolete stopped **containers** only after inspecting mounts and preserving needed evidence. Use explicit container names or the identified project's `docker compose down` without `-v`; retain the paired registry and named volumes. Do not broadly prune or delete unverified volumes. Rebuild only communication when its Python code changes; BPMN and Siddhi sources are mounted. Ordinary resume uses `start`, and recreation uses `up` with the same project, dataset and registry variables.

### Live Siddhi risk and route-command walkthrough

The local Siddhi app consumes the telemetry outbox and logs telemetry. It sends a `DeliveryRiskDetected` alert only after **three distinct consecutive ETA readings for the same shipment and route revision are each late (after the deadline) and fresh (computed at most 30 s before Siddhi evaluates them), within 180 s**. An on-time, unavailable or stale reading breaks the run. At most one alert is sent per shipment/journey/route revision. For terminally rejected goods, later alerts preserve rejection and its incident, and queue evidence with `incidentOpened: false`; transport decisions cannot grant quality release. Otherwise, accepting an alert places the shipment's quality status on hold (`PENDING`, with a `DELAY:` incident) and queues a durable handoff for P4. The service is not an external vehicle simulator: Siddhi forwards a decision command to the communication service's idempotent scripted-simulator endpoint, which acknowledges it before P2 changes route. The Compose defaults connect both telemetry and commands to the local Siddhi service. `/health` should report `siddhiTelemetry: "enabled"` and `siddhiCommands: "enabled"`.

Use a fresh demo dataset for a clear run. Set a slower GPS tick before starting the stack, then create a shipment whose deadline is short enough for its route ETA to exceed it:

```powershell
$env:GPS_TICK_SECONDS = "15"
$env:P2_SIMULATOR_ENABLED = "true"
docker compose up --build -d --wait --wait-timeout 180
Invoke-RestMethod http://localhost:5001/health | ConvertTo-Json -Depth 5
python tools/demo.py --deadline-minutes 1
```

In the CLI, create a shipment with **1**, verify loading with **3**, then dispatch with **4**. Use **2** to poll the shipment. After the third consecutive late ETA reaches Siddhi, its `COLD-CHAIN-RISK` log and P2 `DeliveryRiskDetected` activity should appear, option **2** shows `Quality status: PENDING`, and the instance waits at the dispatcher decision. Choose **13** and select a route different from the current route. A successful decision records the simulator acknowledgement before updating `routeId`. The new route starts at the vehicle's current position and ends at the **same destination**; Operate's `routeId`/`routeRevision` variables match the shipment API. In Operate, confirm P2 still waits for receipt after automatic arrival, then use **14** to confirm physical receipt in P3. P3's acknowledgement completes P2 and releases the vehicle. After inspection (**15**), a risk shipment stays `ON_HOLD`; release its current incident with **17** (P4 stand-in) to admit it. `docker compose logs -f siddhi` shows telemetry and risk events; `docker compose logs -f communication` shows message delivery and worker errors.

Option **11** remains a manual test alert and bypasses Siddhi's risk detection; do not use it for this walkthrough. A local route-command acknowledgement verifies the Siddhi-to-scripted-simulator path only. It does not prove integration with a physical/external simulator. P3 receiving is integrated: arrival starts P3, and P3's receipt is the only way to complete P2. **P4** consumes the hold's durable handoff by default and decides in a DCR case (CLI option 19; see [P4 investigation live run](#p4-investigation-live-run)). Health reads `p4IncidentHandoff: enabled`; with `P4_INCIDENT_URL=` the handoff stays queued (`queued-awaiting-p4`). The stand-in `POST /integrations/p4/quality-status` / CLI option 17 bypasses P4 and remains for P3-only checks.

**Decide before the truck arrives.** While a decision is pending, the scripted truck keeps moving. Each route has seven GPS ticks and the alert comes on the third, leaving about four ticks: roughly 60 s at `GPS_TICK_SECONDS=15`, or 4 minutes at 60. If it reaches its destination first, P2 takes the **Arrived before decision** branch. The alert is closed (`DeliveryRiskClosedAtArrival`), the route is unchanged, and a later option **12**/**13** is refused with *vehicle has already arrived*. That is the intended outcome, not a failure. After an approved route change, the deadline has usually already passed, so expect a second alert on the new route revision and answer it too.

Siddhi listens on **8006** for telemetry, **8007** for route commands and **8008** for container assessment. Its `http` and `http-service` sources cannot share one listener. If an alert is missing, check `docker compose logs siddhi` for `Siddhi App ColdChainTelemetry deployed successfully` first.

**Automated live check.** Select fresh paired P2 registry/engine data using the [sequential setup](#automated-verification), with simulator on, `GPS_TICK_SECONDS=10` and `TOMTOM_API_KEY=`. Stop the previous dataset before switching; changing only the registry directory is insufficient. Then run:

```bash
.venv/bin/python stage_tests/check_p2_siddhi.py
```

It dispatches two shipments at once. A has a one-minute deadline:
- Siddhi may raise the alert only after three late ETAs, and accepting it must place a quality hold.
- An approved route change must be acknowledged through Siddhi, keep the destination, and appear in Operate's variables.
- A must arrive at its original destination.
- A second alert on the new revision is answered with an accepted delay. B has a 60-minute deadline and must raise no alert. C then gets an alert that is deliberately left undecided: its arrival must close the alert, and a late decision must be refused. All must stay `ARRIVED` with their vehicle reserved until P3 records receipt; the manual P2 receipt is refused. After inspection, B is admitted. A and C stay `ON_HOLD` until their **current** incident is released; a release naming any other incident is refused. Evidence is written to `runtime/verification-results/p2-siddhi.json`. The reviewed result is in [compliance/p2/verification.json](../compliance/p2/verification.json).

### Evidence: what was verified and where

The latest pre-merge refresh is [18 November review verification](../compliance/foundation-review-18.json), tested at `8d13c3e`: **82 offline tests**, fresh sequential P1/P2/P3 runs, all four container scenarios, six local Siddhi weather comparisons, closing-evaluation outage/restore and 21-second communication restart pass. Exported P2/P3 end states and exact warehouse/receiving effects were checked. New real business logs and local replay results are retained separately under `compliance/foundation/review-18/`; the earlier reviewed logs/reports are unchanged. Three original-P4 contract failures were also reproduced offline with temporary SQLite and substituted DCR, separately from live engine execution. Actual Open-Meteo and TomTom calls succeeded separately from deterministic local-ETA/recorded-weather scenarios. The user subsequently completed all four ProcessDoctor checks manually: P1 3/3, P2 3/3, P3 4/4 and container 5/5 conforming variants, with zero violations at coverage 100. [Screenshots, trace counts and model/log hashes](../compliance/foundation/review-18/processdoctor.json) record the results; the agent’s earlier browser attempt remains a failed access attempt.

The table below preserves the **17 November** evidence and its original tested revisions. Foundation results are recorded with exact revisions, source/model/log hashes, image identities and input modes in [foundation verification](../compliance/foundation-verification.json). The current P2/P3 reports reference the refreshed logs. Original repair artifacts are preserved under each process's `history/552945d/`; the original merged-main smoke is preserved in [the historical report](../compliance/historical/merged-main-ad47a3c.json). The [current merged-main report](../compliance/merged-main-verification.json) records the integrated P6 main smoke at `35d2d0b`; the P4 merge result is preserved in [its historical report](../compliance/historical/merged-main-70d1a1b.json).

| Evidence | Kind | Tested code | Result |
| --- | --- | --- | --- |
| Offline suite | SQLite/API/provider regressions with substituted engine calls | See foundation report | 79 passing tests |
| P1 regression | Fresh paired data, simulator off | See foundation report | Stock waits/cancellation, independent transports, retries, vehicle reuse and local replay |
| P2 acceptance | Fresh paired data, simulator on, local demo ETAs | `fb06a44` | Run `151259`: three completed journeys, acknowledged route revision, arrival/receipt gates and incident/version release |
| Four standard P3 scenarios | Fresh paired data, simulator on | `83ec452` | Run `53e0ffb2`: admission, hold/release, inspection rejection, quality rejection; new MonitoringFinalized activity |
| Four container scenarios | Real engine and local Siddhi | `7be2210` | Normal, isolated spike, sustained excursion and missing telemetry; exact inventory and exported end states |
| Container weather comparison | Real local Siddhi; recorded ETA/weather, temporary SQLite | `7be2210` | Six cases distinguish urgent hot-weather assessment from cool/unavailable weather and normal-priority standalone excursion |
| Evaluation outage/restart | Real engine; retained matched data | `1b40a93` | Closing-evaluation hold after 20 s; P2 vehicle release precedes finalization; 21-second service restart retains profile/session and detects silence |
| Integration smoke | Branch smoke, real engine end states and exact stock deltas | `1b40a93` application bytes | Passing admission and quality rejection; this is pre-merge verification |
| Local DCR replay | Actual business logs, raw samples excluded | Current rules/log hashes in reports | P2: three traces; P3: four; foundation: six; forbidden admission without finalization/during hold rejected separately |
| Pinned receiving BPMN lint/PDF | Structural/layout validation | Current receiving model | Zero errors/warnings; PDF regenerated and rendered diagram inspected |
| Open-Meteo and TomTom | Separate actual provider calls | Source/response hashes in foundation report | Usable model-based current weather; TomTom HTTP 200 traffic ETA |
| ProcessDoctor | External conformance of changed logs/model | Current foundation evidence | Pending: browser access host unavailable; historical results apply only to archived artifacts |

No merge or post-merge smoke is claimed for the foundation branch. Review and explicit merge authorization remain required; repeat the smoke against merged main after an authorised merge. The flat DCR checker does not implement timed/data/hierarchical semantics or a real P4 investigation.

**P4:** Abid’s DCR investigation consumes the durable handoff and applies dispositions with the assessed evidence version. Offline results are in [compliance/p4/verification.json](../compliance/p4/verification.json); the combined live run `ed88d16d` passed on the final PR #12 code (see [P4 investigation live run](#p4-investigation-live-run)). Earlier runs `d2558070` and `fd021988` (earlier PR #12 revisions) and `900821e9` (PR #10, pre-foundation) are kept there as historical evidence only.

### P2 DCR compliance

[compliance/p2/rules.xml](../compliance/p2/rules.xml) checks P2's recorded activities, plus the neighbouring P1 `ShipmentDispatched`. `VehicleReleased` has `ReceiptAcknowledged` as a condition. A `DeliveryRiskDetected` alert includes a `DispatcherDecisionRecorded` response and makes it pending. That pending decision is a milestone blocking a second alert and the vehicle release. `RouteChangeAcknowledged` is only included by a decision. If the truck arrives before a decision, P2 records `DeliveryRiskClosedAtArrival` just before `ShipmentArrived`. It is an alternative to the decision: each excludes the other, and it clears the pending obligation. Arrival then excludes all further risk activity. GPS/ETA telemetry is sensor data for Siddhi and is left out of the log.

```bash
python compliance/check.py compliance/p2/execution-log.xes --model compliance/p2/rules.xml
```

Expect all three real traces to be valid and accepting. The quality hold is journalled as a P4 stand-in activity (`QualityHoldEstablished`, process `P4`), so it is not part of the P2 log. Each synthetic violation in [invalid-traces.json](../compliance/p2/invalid-traces.json) must be rejected; the offline suite checks this. To regenerate the evidence after a new live run, use `python compliance/p2/build_p2_evidence.py log runtime/verification-results/p2-siddhi.json`. Flat DCR cannot see whether a decision was a route change or an accepted delay; the application checks that from the decision data.

The reviewed five-product [execution log](../compliance/p1/execution-log.xes) and [verification report](../compliance/p1/verification.json) contain actual execution evidence and source/model hashes from this checkpoint. Regenerate and review them when implementation or models change. The report also records successful ProcessDoctor import/conformance and the metadata-only model correction. Repeat external conformance checking when the rules or log change. P1 ownership is recorded in the project documentation. The BPMN source and PDF omit student attribution at the owner's request. The DCR display title includes the owner; its semantics are unchanged and the report preserves the hashes used in the original checks. Model PDFs can be exported in Modeler or with [bpmn-to-image](https://github.com/bpmn-io/bpmn-to-image):

```bash
npx --yes @camunda8/cli@4.6.1 bpmn lint bpmn/receiving.bpmn
npx --yes @camunda8/cli@4.6.1 bpmn lint bpmn/corrective_fulfilment.bpmn
npx --yes bpmn-to-image@0.10.0 --title='P1 Shipment preparation' bpmn/shipment_preparation.bpmn:bpmn/shipment_preparation.pdf
npx --yes bpmn-to-image@0.10.0 bpmn/transport.bpmn:bpmn/transport.pdf
npx --yes bpmn-to-image@0.10.0 --title='P3 Receiving and stock admission' bpmn/receiving.bpmn:bpmn/receiving.pdf
npx --yes bpmn-to-image@0.10.0 --min-dimensions=2400x800 --title='P5 Corrective fulfilment' bpmn/corrective_fulfilment.bpmn:bpmn/corrective_fulfilment.pdf
```

### P4 investigation live run

Use a fresh paired dataset (new suffix), the simulator on and local Siddhi. `GPS_TICK_SECONDS=15` gives the runner time to raise a delay risk while the vehicle is in transit; container sampling continues after GPS arrival until physical receipt.

```bash
export COMPOSE_PROJECT_NAME=frostflow-p4 VERIFY_DATASET=p4-02 REGISTRY_DIR=./runtime/repair/p4-02
export P2_SIMULATOR_ENABLED=true GPS_TICK_SECONDS=15 TOMTOM_API_KEY=
docker compose -f docker-compose.yml -f stage_tests/verification.compose.yml up --build -d --wait --wait-timeout 300
.venv/bin/python stage_tests/check_p4_investigation.py --base http://localhost:5001
.venv/bin/python compliance/p4/build_p4_evidence.py --live runtime/p4-compliance/evidence.json
docker compose -f docker-compose.yml -f stage_tests/verification.compose.yml stop
```

The runner needs only the Python standard library. It runs four investigations on new monitored shipments:

1. Delay → cooling acknowledged, recovery evidence → release → admitted.
2. Container excursion (`AssessmentRequested`) → failed cooling → replace.
3. Delay → additional check → return.
4. Delay → assessed → new container evidence → refused decision → reassessment → release.

Each case checks that physical receipt released the vehicle while quality was held, that monitoring finalized, that forbidden actions were refused (409) and the P3 outcome. The runner enters the cooling acknowledgement/failure and recovery evidence by hand, so run it with `P4_COOLING_COMMAND_URL` empty (the Compose default); those entries are journalled as `inputMode: manual-test`. It takes roughly 15–20 minutes.

**P4 cooling loop (issue #17, P4 side only):** with `P4_COOLING_COMMAND_URL` set, `RequestCoolingAdjustment` queues a durable `CoolingAdjustmentRequested` command (outbox destination `p4-cooling-command`, `Idempotency-Key` = `commandId`); the simulator's result arrives at `POST /integrations/p4/cooling-result` and Siddhi's recovery evaluation at `POST /integrations/p4/recovery`. A command without a result after `P4_COOLING_ACK_TIMEOUT_SECONDS` (default 45) fails. `/health` reports `integrations.p4CoolingCommands` (`siddhi-command-bridge` or `manual-test-input`). The simulator/Siddhi side is not implemented yet, so leave the URL empty for live runs; see the [draft contract](P4-COOLING-CONTRACT.md). Offline: `.venv/bin/python -m unittest stage_tests/test_p4_cooling.py -v`. The builder writes `compliance/p4/execution-log.xes` and the live section of `compliance/p4/verification.json`. If `.env` holds `DCR_*` credentials, P4 executes in the dcrgraphs.net repository (`/health` → `p4DcrEngine: dcr-repository`); otherwise in the local engine. Recorded result: run `ed88d16d` passed on 19 November 2026 at `35d154a` (454 s, macOS arm64, fresh `p4-04` data, P4 in the dcrgraphs.net repository, graph `2015919`; 4 traces valid and accepting). Earlier runs `d2558070` (`3576812`) and `fd021988` (local engine, `95e4a85`) are historical.

## Persistence, isolated checks and troubleshooting

Ordinary `docker compose stop` / `docker compose start` preserves the SQLite registry and named engine volumes. Existing P1 cases retain the BPMN version on which they started; there is no automatic engine-instance migration. The five-product service refuses to open a legacy pooled-stock registry. Old crates cannot be assigned truthful product/batch identities automatically. Preserve the old registry with its engine volumes; use a fresh matching project and registry directory. Existing evidence is not relabelled as five-product execution.

For an existing P2 registry written before external event IDs were namespaced, a receipt ID such as `P2:<shipmentId>:VehicleReleased` may have occupied the journal key for the internal release activity. On a retry of that accepted receipt's release handler, the updated registry verifies and moves the legacy receipt entry, then restores the missing `VehicleReleased` activity in the same transaction. The upgrade regression check is:

```powershell
python -m unittest stage_tests.test_stage1.Preparation.test_upgrade_repairs_legacy_receipt_vehicle_release_collision -v
```

It should pass and leave exactly one `ReceiptAcknowledged` and one `VehicleReleased` activity. It tests the stored-registry repair; it does not run the engine or alter a live dataset.

To verify changes without replacing existing demo data, first stop the current stack, then select a fresh project name and registry directory. Find the active name with `docker compose ls`; replace `CURRENT_PROJECT` below with that name:

```bash
docker compose -p CURRENT_PROJECT stop
# Use a new name/directory for the five-product version:
REGISTRY_DIR=./runtime/five-products-01 docker compose -p cold-chain-five-products-01 up --build -d --wait --wait-timeout 180
```

Use a new matching name/directory for a new isolated run. For this example, pause/resume using the same project name:

```bash
docker compose -p cold-chain-five-products-01 stop
docker compose -p cold-chain-five-products-01 start
```

`start` reuses the existing mounts; it does not recreate containers. For `up`/rebuild/recreation, also supply `REGISTRY_DIR=./runtime/five-products-01` again. Other stacks should stay stopped to avoid port conflicts. Do not point a fresh registry at an existing engine or discard one without the other. Raw generated data stays under ignored `runtime/`; reviewed evidence is retained under the appropriate `compliance/p1/`, `compliance/p2/` or `compliance/p3/` directory, with shared smoke evidence in `compliance/merged-main-verification.json`.

For disposable data only, `docker compose down -v` removes that project's engine volumes. After saving needed evidence, move its matching registry directory out of the way before starting fresh. This is a deliberate reset, not a normal startup step.

- **400:** malformed input, missing field, non-integer quantity or loading check not JSON `true`.
- **404:** unknown shipment.
- **409:** duplicate business identity, reserved vehicle, wrong state, changed supply ID payload or competing command.
- **502 on creation:** engine-start outcome uncertain; inspect engine/logs before retrying. The registry entry is retained.
- **A pending command/handoff does not advance:** check `docker compose logs communication` and Operate for publication/worker incidents. Published messages expire after 24 hours if unconsumed; recovery is manual.

One communication instance is supported. Authenticated users, stock procurement and the shared logger remain outside this increment; the cooling/recovery loop has a [draft contract](P4-COOLING-CONTRACT.md) with only its P4 side implemented. P3 receipt/admission and the durable delivery-risk quality hold are implemented. P1 external conformance checking is recorded above.


## Container monitoring foundation

Use one matched registry/engine stack, with the same sequential dataset commands above. Compose loads both Siddhi apps from `/apps`; container assessment uses `SIDDIHI_CONTAINER_URL=http://siddhi:8008/container` (host port 8008). Sampling and the durable watchdog run independently from GPS ticks, HTTP delivery and Open-Meteo refresh. Both normal GPS-disabled P1 runs and arrivals awaiting receipt continue container monitoring.

`MONITORING_PROFILE_JSON` accepts JSON overrides for the numeric defaults and profile ID in `deployer/monitoring.py`; invalid/unknown settings are refused at shipment creation. New shipments snapshot settings, so changing configuration affects only later shipments. Defaults: normal 3°C, fault 10°C, chicken assessment above 4°C, others above 5°C, 5 s samples, three readings spanning at least 10 s, gap ≤10 s, freshness ≤15 s, silence/finalization timeouts 20 s. Ten seconds demonstrates persistence, not spoilage. No product safety certification is implied.

Use CLI option 18 or:

```bash
curl -s -X POST http://localhost:5001/shipments/S-DEMO/simulation/temperature \
  -H 'Content-Type: application/json' \
  -d '{"commandId":"temperature-demo-1","actor":"demo-operator","scenario":"sustained-excursion"}'
```

The shipment must have processed loading verification. `normal` emits 3°C; `isolated-spike` emits one fault reading then normal; `sustained-excursion` emits a three-reading rising ramp (3, 3.25, 3.5°C by default), then 10°C; `missing-telemetry` emits no readings. Scenario changes are durable journal entries. They are scripted inputs, not cooling commands or refrigeration physics.

Shipment views show monitoring session/profile, sequence/evaluation cursor, closing sequence/finalization, last reading/freshness, weather, assessment reasons/priority and evidence version. Inspect all stored windows via `GET /shipments/<id>/monitoring/observations`. Raw container observations live in `container_observations`; compliance exports include only business effects such as `MonitoringFinalized`, holds and admission. Existing shipments without a profile snapshot show `legacy/unmonitored`; no migration of their running instances or invented samples occurs.

Open-Meteo requests current temperature, precipitation, wind speed and weather code at the simulated position. Responses retain requested/provider coordinates, UTC model-data time, retrieval time, units and model-based provenance; sanitized JSONL is saved beside the ignored registry. Success caches for 10 minutes by 0.05° grid; at most one refresh attempt per journey per minute. Combined risk requires data age ≤30 minutes and distance ≤10 km, ≥30°C outdoors, current-revision qualified and still-fresh late ETA, plus three rising fresh container readings spanning 10 s and rising ≥0.5°C. Failed/stale data is unavailable. Repeated/cached provider observations retain their identity, and weather alone cannot hold/reject goods.

P3 receipt releases the vehicle independently of assessment. Admission waits for closing evaluation or a durable timeout hold. Unavailable Siddhi or silence cannot silently permit admission. Dispositions must name `expectedEvidenceVersion` as well as the current incident; P4 sends the version the officer assessed, and CLI option 17 (stand-in) fetches the current one. New material evidence invalidates older dispositions and requires reassessment in P4. Compose sends handoffs to the local P4 consumer by default; an empty `P4_INCIDENT_URL` leaves immutable handoffs pending and the hold intact.

```bash
# Existing healthy stack, simulator enabled; four milk crates and a free vehicle:
.venv/bin/python stage_tests/check_foundation.py
# Same local Siddhi, isolated temporary SQLite fixtures; no shared engine data:
.venv/bin/python stage_tests/check_container_assessment.py
```

These checks distinguish live Siddhi/engine behavior from recorded ETA/weather inputs. Actual Open-Meteo and TomTom checks are recorded separately in [foundation verification](../compliance/foundation-verification.json). The schema fixture and sanitized captured Open-Meteo response in `stage_tests/fixtures/` are labelled separately. Manual external ProcessDoctor checks for the review-18 logs passed; see [the current record](../compliance/foundation/review-18/processdoctor.json). Older screenshots apply only to their original inputs.

For a live service-restart watchdog check on the named verification stack (one free vehicle and one salad crate), run `.venv/bin/python stage_tests/check_monitoring_restart.py --communication-container frostflow-foundation-communication-1`. It deliberately stops only communication for 21 seconds and starts that same container with its original matched data. It proves the restored session/profile survives, the silence hold precedes resumed sampling, and P3 receipt releases the vehicle during the hold.

To test closing evaluation failure, recreate **only communication** with the original project/registry/overlay variables and `SIDDIHI_CONTAINER_URL=http://127.0.0.1:9/container`, then run `.venv/bin/python stage_tests/check_monitoring_outage.py`. The runner expects a 20-second closure hold and verifies vehicle release precedes finalization. Restore the normal container URL using the same variables afterward. Late unavailable acknowledgements belong to that closing-sequence assessment; they cannot silently clear its hold or reopen the same released uncertainty episode. The endpoint remains a labelled test disposition, not an investigation.


### Resume the 18 November review dataset

The retained review stack is `frostflow-foundation` with dataset `p3-18`, simulator on, GPS tick 2 seconds, local ETA estimates and default local Siddhi evaluation. Its registry is consumed; do not rerun fresh-data suites on it. Preserve all earlier paired datasets. To recreate/resume this same dataset, first inspect active stacks and stop any other project, then use:

```bash
export COMPOSE_PROJECT_NAME=frostflow-foundation
export VERIFY_DATASET=p3-18 REGISTRY_DIR=./runtime/foundation/p3-18
export P2_SIMULATOR_ENABLED=true GPS_TICK_SECONDS=2 TOMTOM_API_KEY=
export SIDDIHI_CONTAINER_URL=http://siddhi:8008/container
docker compose -f docker-compose.yml -f stage_tests/verification.compose.yml up -d --wait --wait-timeout 240
curl http://localhost:5001/health
```

Use new paired suffixes for any future fresh-data suite. Foundation PR #9 merged as `ae941cc`; P4 PR #12 merged as `70d1a1b`. The exact P4 merged revision passed the admission/hold-rejection smoke on the retained matched `review21` dataset (project `frostflow-p4-final`, local DCR/Siddhi, GPS tick 15 seconds and local demo ETAs); see [the historical report](../compliance/historical/merged-main-70d1a1b.json). This smoke uses labelled test dispositions; the separate [four-case P4 live verification](../compliance/p4/integration-review21.json) exercises actual investigation decisions. After each approved merge, rebuild from merged main using the same paired-dataset variables plus `up --build`, run `stage_tests/check_integration_smoke.py` with sufficient salad/chicken stock and free vehicles, and record the exact merged revision. Until that smoke passes, this is branch verification, not the final handover readiness claim. See [Abid's P4 handover](P4-FOUNDATION-HANDOVER.md).

## P5 corrective fulfilment

P5 merged through PR #14 as `b3f6102`. The latest integrated main `35d2d0b` passed the post-P6 admission/rejection and claim smoke; see [the current record](../compliance/merged-main-verification.json). This smoke uses labelled quality dispositions; full P4/P5 investigation/corrective paths retain their separately recorded live evidence and source hashes.

P5 deploys with P1/P2/P3 at startup (`P5_BPMN_FILE`, default
`bpmn/corrective_fulfilment.bpmn`; Compose uses `/bpmn/corrective_fulfilment.bpmn`).
There is no extra service, port or credential. New applied P4 replacement/return
decisions create one case each. Historical decisions are not automatically backfilled;
quality-status test inputs and releases do not start P5.

Use CLI **20** on the original shipment to view the case, arrange collection and, for
replacement, specify a new shipment/journey and free vehicle. P5 fixes product, batch,
full quantity and recipient to the original. Use the existing shipment ID prompt and
CLI **3**, **4**, **14**, **15** on the replacement to verify loading, dispatch, receive
and inspect. P1 shortages retain their replenishment/cancellation wait. Use CLI **20**
on the original again to acknowledge collection after P3 physical receipt and rejection.
Booking reference, collector and full quantity must match. These are manual/simulated
confirmations. Stock admission and original collection are independent requirements;
P5 reports `FAILED` for cancelled/rejected replacements, even after original collection.
A replacement's own P4 return has a separate P5 case; its parent reference is retained.

Read the case via `GET /corrective-fulfilments?originalShipmentId=<id>` and
`GET /corrective-fulfilments/<correctiveId>`. Inputs are
`POST /corrective-fulfilments/<id>/arrange` and `POST .../<id>/collect`; exact fields and
retry rules are in the [integration contract](COLD-CHAIN-INTEGRATION-AGREEMENT.md#p5-corrective-fulfilment-contract).
A 202 is accepted, so poll the case for `IN_PROGRESS`, `originalGoodsStatus: COLLECTED`
and eventually `COMPLETED`/`FAILED` with a bounded timeout. The CLI uses 30 seconds;
live runners use named bounded waits and verify exported engine ends too.

For the four-scenario fresh-data acceptance suite, use the [P5 sequential stack commands](P5-CORRECTIVE-FULFILMENT.md#live-verification-commands).
It also completes the rejected replacement's separate return (five real P5 instances),
checks P1 call-activity parent identity and exact -25 warehouse/+1 receiving effects.
Run `9347c2c8` passed at `d118870`: 136 offline tests, five accepted local DCR traces and
4/4 external conforming variants (zero structural violations; coverage 100). The separate
synthetic dispatch-only trace produces one external structural violation as expected.
The current P5 [verification report](../compliance/p5/verification.json) identifies code,
model/log hashes and input modes. Local DCR replay uses:

```bash
.venv/bin/python compliance/check.py compliance/p5/execution-log.xes --model compliance/p5/rules.xml
```

P5 rules project the actual P4 disposition and P5 authorization into branch-specific
activity names, preserving their journal identities/times. Failed corrective cases are
valid compliance outcomes when all original collection and negative replacement
obligations are recorded. Synthetic forbidden traces remain separate. Existing P1–P4
reports retain their original scope; P5 evidence does not validate P6 or future recovery.

## P6 supplier claims

Independent verification on PR code `163bd16` passed all 140 offline tests, four P5 scenarios/five corrective completions, and five P6 claims on fresh matched `full-r1` data (project `frostflow-final-review`). P1/P2/P3/P5/P6 executed in Zeebe, container assessments in Siddhi and P4 in the local DCR engine; GPS tick was 2 seconds, ETAs were local demo estimates, and physical/supplier actions were simulated. The fifth claim used a real 116-character container incident. Exported engine ends, source/image hashes and exact stock effects are in the [supplement](../compliance/p6/review22/verification.json). Original reports and their ProcessDoctor results remain unchanged; the supplemental log has local replay only. PR #15 subsequently merged as `35d2d0b`; all 140 tests and its exact post-merge smoke passed on fresh matched `smoke-r1` data, including normal admission, held-goods rejection and a P6 settlement with zero additional stock change. See [the current merged-main report](../compliance/merged-main-verification.json).

Rebuild communication on the matching Compose dataset so it deploys [supplier_claim.bpmn](../bpmn/supplier_claim.bpmn) with the other models. Health includes SupplierClaimResolution and retains stage 3 for existing clients. Use a shipment with a recorded qualityIncidentId, such as a quality-rejection case produced by the integrated smoke runner.

1. POST to /claims with {"incidentId":"<incident>","originalShipmentId":"<shipment>","lossDescription":"One rejected crate","actor":"demo operator","evidenceRefs":["<journal event ID>"]}. Save the returned claimId. HTTP 202 means durably accepted.
2. Poll GET /claims/<claimId> until status is SUBMITTED.
3. POST to /claims/<claimId>/supplier-response with {"outcome":"SETTLED","actor":"simulated supplier","reason":"Demo settlement"}. Use REJECTED for the other branch. Poll until CLOSED with the expected outcome.

For verification, use four shipment IDs with distinct recorded quality incidents, the API on port 5001 and the Elasticsearch exporter on port 9200:

```bash
.venv/bin/python -m unittest stage_tests.test_p6_claims -v
npx --yes @camunda8/cli@4.6.1 bpmn lint bpmn/supplier_claim.bpmn
npx --yes bpmn-to-image@0.10.0 'bpmn/supplier_claim.bpmn:bpmn/supplier_claim.pdf'
.venv/bin/python stage_tests/check_p6_claims.py SETTLEMENT_SHIPMENT REJECTION_SHIPMENT --extra-shipments INVALID_RESPONSE_SHIPMENT CONFLICTING_RESPONSE_SHIPMENT
.venv/bin/python compliance/check.py runtime/verification-results/p6/execution-log.xes --model compliance/p6/rules.xml
```

On Windows Git Bash, use .venv/Scripts/python and quote the export argument as 'bpmn/supplier_claim.bpmn;bpmn/supplier_claim.pdf'. Use a fresh third shipment to test an invalid response before settlement. The runner checks exported Zeebe end events, replays the actual P6 journal and writes XES for ProcessDoctor. Use a stack built from the revision under test and record the deployed revision and hashes before promoting output to reviewed evidence. Import the P6 rules and XES log together for external conformance. Offline P6 regressions pass. The PDF is in bpmn/supplier_claim.pdf. The four-case live results, XES log and ProcessDoctor screenshot are in compliance/p6. ProcessDoctor accepted one trace variant covering all four cases.
