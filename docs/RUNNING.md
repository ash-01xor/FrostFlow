# Running, testing and understanding P1

Run commands from the repository root. P1 responsible student: **Ashvanth**. The implemented scope includes shipment preparation, a P2 transport workflow exercised through manual arrival/receipt inputs, and the P1 stock shortage/cancellation paths.

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

All products carry the placeholder `temperatureProfileId: CHILLED-DEMO`. It identifies the agreed shared configurable demonstration profile for future CEP integration. No numeric temperature range, validated product-specific storage rule or food-safety suitability is asserted; future temperature settings must be documented before telemetry/CEP is implemented.

`GET /stock` lists each product/batch, pack definition, initial crates and current availability. Its top-level `available` is only the sum across products; shipment eligibility uses the selected product/batch. The response also contains `datasetVersion: 3` and `shipmentCount`.

The local dataset stores product definitions, batch inventory, shipment/vehicle reservations, states and P1/P2 engine keys, activity history, pending messages/acknowledgements and retry identities in SQLite under the selected `REGISTRY_DIR`. Zeebe/Elasticsearch store engine execution and inspection records in the matching Docker volumes. Generated databases and live-run evidence are ignored under `runtime/`. The committed `compliance/p1/` files are P1 conformance evidence, not P2 execution evidence.

## Start the application

Start Docker Desktop, then:

```bash
docker compose up --build -d --wait --wait-timeout 180
curl -s http://localhost:5001/health
```

Wait for `status: ready` and `stage: 2`. Both BPMN resources must deploy before readiness. Operate is at <http://localhost:8081> (`demo` / `demo`). Open [P1](../bpmn/shipment_preparation.bpmn) in Camunda Modeler alongside its [PDF](../bpmn/shipment_preparation.pdf). The [P2 model](../bpmn/transport.bpmn) and [PDF](../bpmn/transport.pdf) show the receiving boundary.

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

This terminal menu sends the same application requests as the commands below. It waits for state changes and prints P1/P2 instance keys to find in Operate. Choose an action number, then shipment **A**, **B** or **C**. Creation also offers a choice of all five products; press Enter to use milk for the shortage scenario. Actions **9** and **10** submit the manual arrival and receipt events. Arrival polls until `ARRIVED` while the vehicle remains reserved; receipt polls until `COMPLETED` with `vehicleReleased: true`. Stable event IDs and timestamps make retrying the same menu action an exact retry. `python tools/demo.py --check` checks readiness and stock without changing data.

For the normal P1 demonstration:

| Menu choice | What happens | What to explain/show |
| --- | --- | --- |
| 1, then A | Create 15-crate milk order (product 1); reserve stock and await loading | Find P1's printed key in Operate; it is waiting for a warehouse confirmation |
| 4, then A | Dispatch is rejected | Loading verification is required |
| 3, then A | Confirm the simulated loading checks | P1 moves to the dispatch-approval wait |
| 4, then A | Dispatch; register P2 | P1 completes; P2 starts the journey and waits at `WaitForShipmentArrived` |
| 9, then A | Submit manual/test arrival | P2 records arrival, remains active at `WaitForReceiptAcknowledged`, and keeps V-01 reserved |
| 10, then A | Submit manual/test receipt acknowledgement | P2 reaches `TransportCompleted`; registry shows `COMPLETED` and releases V-01 |

For a stock exception, choose **1, B**, then milk again: 10 milk crates are requested while only five milk crates remain. The other four products still have 20 crates each; they cannot satisfy this order. Show the stock-wait gateway, then choose either **5, B** to replenish five milk crates and continue preparation, or **6, B** to cancel. After cancelling B, **1, C** creates another stock-wait case on the released vehicle if you want to rehearse the other branch. Menu **2** displays state/keys; **7** displays the activity history; **8** lists product/batch stock; **0** exits without stopping the stack.

Operate displays engine execution. The terminal represents the person submitting actions; this setup has no Tasklist forms for those actions. Keep both windows open, refresh Operate when needed, and use its instance key and diagram to explain the current wait. The menu explicitly confirms demonstration loading checks; it does not inspect physical goods.

The dataset must be prepared before recording/presenting. The menu preserves existing data and does not reset containers or reservations. A used dataset may have unavailable vehicles even if stock remains; use the isolated-dataset instructions below to rehearse again.

For the final **three-minute** project demo, reserve roughly 25 seconds for preparation. Build one connected story as later parts become available: dispatch → arrival/receipt → temperature incident → investigation/hold → authorised resolution → corrective work/claim → compliance results. Show process behaviour and evidence at each transition. The GPS simulator and P3–P6 workflows remain planned.

## Manual walkthrough

### 1. Reserve most of the milk stock

Create shipment S-001 for 15 crates of milk, batch DEMO-MILK-001:

```bash
curl -s -i -X POST http://localhost:5001/shipments \
  -H 'Content-Type: application/json' \
  --data-binary @event_streams/shipment-request.json
curl -s http://localhost:5001/shipments/S-001
curl -s http://localhost:5001/stock
```

Creation returns **202**. Poll until `AWAITING_LOADING`; milk stock should be **5**, while total availability is **85**. The engine ran `CheckStock`, took the available branch, allocated stock and assigned transport. Checking and reserving are one transaction so another shipment cannot take the same crates between those steps.

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

Poll until S-003 reaches `AWAITING_LOADING`. It has now reserved ten milk crates; available milk stock is zero. The other four products still have 20 crates each, so total availability is 80. A replenishment updates the shared inventory but wakes only its addressed shipment. Other waiting shipments need their own accepted replenishment action. This prototype does not implement supplier purchasing or warehouse stock optimisation.

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

For a manual P2 run, the menu above polls after arrival and receipt. The equivalent API inputs are below. First, an early receipt must be rejected with HTTP **409** while transport is `IN_TRANSIT`:

```bash
curl -s -i -X POST http://localhost:5001/shipments/S-001/acknowledge-receipt \
  -H 'Content-Type: application/json' \
  -d '{"eventId":"receipt-S-001-001","occurredAt":"2026-11-15T10:24:00Z","recipientId":"C-01","quantityReceived":15,"receiptReference":"RCPT-001"}'
```

After arrival is accepted with HTTP **202**, poll `GET /shipments/S-001` until `transportState` is `ARRIVED` and `vehicleReleased` is `false`. In Windows PowerShell:

```powershell
do {
  $record = Invoke-RestMethod http://localhost:5001/shipments/S-001
  if ($record.transportState -ne "ARRIVED") { Start-Sleep -Seconds 1 }
} while ($record.transportState -ne "ARRIVED")
$record | Format-List shipmentId,transportState,vehicleReleased
```

In Operate, the P2 instance passes the `WaitForShipmentArrived` message catch and activates `WaitForReceiptAcknowledged`; it cannot reach the `TransportCompleted` end event yet.

```bash
curl -s -i -X POST http://localhost:5001/shipments/S-001/arrive \
  -H 'Content-Type: application/json' \
  -d '{"eventId":"arr-S-001-001","occurredAt":"2026-11-15T10:22:00Z","recipientId":"C-01"}'
```

Submit the same receipt payload after arrival; expect **202**:

```bash
curl -s -i -X POST http://localhost:5001/shipments/S-001/acknowledge-receipt \
  -H 'Content-Type: application/json' \
  -d '{"eventId":"receipt-S-001-001","occurredAt":"2026-11-15T10:24:00Z","recipientId":"C-01","quantityReceived":15,"receiptReference":"RCPT-001"}'
```

Then poll until `transportState` is `COMPLETED` and `vehicleReleased` is `true`:

```powershell
do {
  $record = Invoke-RestMethod http://localhost:5001/shipments/S-001
  if ($record.transportState -ne "COMPLETED" -or -not $record.vehicleReleased) {
    Start-Sleep -Seconds 1
  }
} while ($record.transportState -ne "COMPLETED" -or -not $record.vehicleReleased)
$record | Format-List shipmentId,transportState,vehicleReleased
```

In Operate, confirm `ReleaseVehicle` completes and the process reaches `TransportCompleted`.

Both endpoints accept manual/test inputs only. `ShipmentArrived` and `ReceiptAcknowledged` correlate by `shipmentId`; the receipt endpoint stands in for P3 and does not send a P3 notification. Retrying the exact same event ID and payload returns **202** and does not duplicate journal activities. Reusing an event ID with different content, or submitting an event in the wrong state, returns **409**. A new live check below exercises two shipments independently, their early-receipt rejection, exact retries, engine completion and vehicle reuse. P2 DCR model/log verification remains outstanding; the live JSON report is execution evidence only. The GPS simulator and live P3 connection remain follow-up work. Damaged-packaging handling is outside this increment: all loading checks must be true for verification.

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

Create a Python 3.12 environment and install the application dependencies:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r deployer/requirements.txt
.venv/bin/python -m unittest discover -s stage_tests -p 'test_*.py' -v
docker compose config --quiet
npx --yes @camunda8/cli@4.6.1 bpmn lint bpmn/shipment_preparation.bpmn
npx --yes @camunda8/cli@4.6.1 bpmn lint bpmn/transport.bpmn
```

The offline suite contains **31 tests** covering five-product seed data, product/batch validation, isolated inventory, concurrent reservation, targeted supply, restart persistence, legacy-data protection, XES traceability, retry identities, cancellation, outbox handling, P2 state guards and DCR replay. They stub engine calls; they do not replace the live P2 completion check below.

Against a fresh, healthy stack:

```bash
.venv/bin/python stage_tests/check_stage1.py
```

This runs the stock-wait/cancellation/replenishment P1 scenarios, completes two independent P2 transports through the manual endpoints, and verifies the P2 end events, exact retry counts, early-receipt rejection and reuse of a released vehicle. It requires a fresh version-3 dataset with all 100 crates and both vehicles available. It writes P1 compliance artifacts to `runtime/verification-results/execution-log.xes` and `verification.json`; the XES contains P1 activities only. Separate P2 execution evidence is written to `runtime/verification-results/p2-completion.json`. That P2 report is not DCR conformance evidence; a P2 DCR model/log has not yet been delivered.

The reviewed five-product [execution log](../compliance/p1/execution-log.xes) and [verification report](../compliance/p1/verification.json) contain actual execution evidence and source/model hashes from this checkpoint. Regenerate and review them when implementation or models change. The report also records successful ProcessDoctor import/conformance and the metadata-only model correction. Repeat external conformance checking when the rules or log change. P1 ownership is recorded in the project documentation. The BPMN source and PDF omit student attribution at the owner's request. The DCR display title includes the owner; its semantics are unchanged and the report preserves the hashes used in the original checks. Model PDFs can be exported in Modeler or with [bpmn-to-image](https://github.com/bpmn-io/bpmn-to-image):

```bash
npx --yes bpmn-to-image@0.10.0 --title 'P1 Shipment preparation' bpmn/shipment_preparation.bpmn:bpmn/shipment_preparation.pdf
npx --yes bpmn-to-image@0.10.0 bpmn/transport.bpmn:bpmn/transport.pdf
```

## Persistence, isolated checks and troubleshooting

Ordinary `docker compose stop` / `docker compose start` preserves the SQLite registry and named engine volumes. Existing P1 cases retain the BPMN version on which they started; there is no automatic engine-instance migration. The five-product service refuses to open a legacy pooled-stock registry. Old crates cannot be assigned truthful product/batch identities automatically. Preserve the old registry with its engine volumes; use a fresh matching project and registry directory. Existing evidence is not relabelled as five-product execution.

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

`start` reuses the existing mounts; it does not recreate containers. For `up`/rebuild/recreation, also supply `REGISTRY_DIR=./runtime/five-products-01` again. Other stacks should stay stopped to avoid port conflicts. Do not point a fresh registry at an existing engine or discard one without the other. Raw generated data stays under ignored `runtime/`; only reviewed submission evidence is retained in `compliance/p1/`.

For disposable data only, `docker compose down -v` removes that project's engine volumes. After saving needed evidence, move its matching registry directory out of the way before starting fresh. This is a deliberate reset, not a normal startup step.

- **400:** malformed input, missing field, non-integer quantity or loading check not JSON `true`.
- **404:** unknown shipment.
- **409:** duplicate business identity, reserved vehicle, wrong state, changed supply ID payload or competing command.
- **502 on creation:** engine-start outcome uncertain; inspect engine/logs before retrying. The registry entry is retained.
- **A pending command/handoff does not advance:** check `docker compose logs communication` and Operate for publication/worker incidents. Published messages expire after 24 hours if unconsumed; recovery is manual.

One communication instance is supported. Live P3 integration, authenticated users, stock procurement, the shared logger, P4 quality holds, and simulator/CEP/API integrations remain outside this increment. P1 external conformance checking is recorded above.
