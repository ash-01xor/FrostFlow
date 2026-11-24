# Cold-chain delivery: team implementation guide

**P4 reintegration (replacement PR, branch `feat/p4-foundation-integration`):** Abid’s P4 from [PR #10](https://github.com/ash-01xor/FrostFlow/pull/10) was reverted by PR #11 so foundation [PR #9](https://github.com/ash-01xor/FrostFlow/pull/9) could merge first (merged as `ae941cc`). [PR #12](https://github.com/ash-01xor/FrostFlow/pull/12) restores P4 by reverting `31ad23b` and adapts it to the [foundation contract v1](P4-FOUNDATION-HANDOVER.md); see [sequencing](P4-INTEGRATION-SEQUENCING.md#checkpoint-4--replacement-p4-pr) and the [P4 guide](P4-QUALITY-INVESTIGATION.md). Review, merge and post-merge verification outcomes are recorded on PR #12.

**Project:** refrigerated-food delivery and quality incident management

**Team:** six students, one process owner each

**Implementation:** five BPMN processes in Camunda and one DCR process

**Submission:** 15 December 2026, 23:59, according to the course specification

**Ownership:** P1 — Ashvanth; P2 — Rahul Shanbhag; P3 — Arunima Garg; P4 — Abid; P5 — Daniel; P6 - Sai Shashank Maktala

**Implemented checkpoint:** P1 preparation, P2 transport/Siddhi ETA risk, P3 receiving,
P4 DCR investigation and P5 corrective fulfilment are executable. Risks establish a
shared hold and durable P4 handoff; applied replacement/return dispositions authorize
one P5 case. P5 requires original collection and, for replacement success, admission
of a new linked shipment through P1–P3. Rejected/cancelled replacements fail fulfilment.
Container excursion/combined-risk CEP, independent Open-Meteo current conditions and P3
monitoring finalization are implemented in the [foundation](PRE-P4-FOUNDATION.md).
Cooling/recovery automation remains planned. P6 supplier claims are implemented; the [independent integration supplement](../compliance/p6/review22/verification.json) records five live claim completions and 140 passing offline tests at `163bd16`. See the [P5 guide](P5-CORRECTIVE-FULFILMENT.md)
and [verification commands/evidence](RUNNING.md#automated-verification) for tested revisions and limits.

## 1. What problem are we solving?

A food distributor delivers refrigerated goods to campus cafeterias. Delivery records, vehicle updates, temperature readings, and quality decisions are kept separately. This creates three main problems:

1. **Problems are discovered late.** A temperature problem or delivery delay may go unnoticed until arrival.
2. **Goods can enter stock before investigation.** A cafeteria may accept goods without knowing that their transport conditions are being questioned.
3. **Follow-up is difficult to track.** Replacement goods, returns, and supplier claims may remain unresolved.

Our application will connect these activities. It will detect transport problems, open a quality investigation, prevent stock admission while a decision is pending, and track the agreed resolution.

**Example:** temperature stays above the configured limit during transport → an alert opens an investigation → the cafeteria can acknowledge receipt but cannot admit the goods to stock → the quality officer reviews the evidence → the goods are released, replaced, or returned → any eligible supplier claim is resolved.

The original manual process is an assumed coursework scenario, not findings from interviews. Temperature limits are configurable demonstration settings, not universal food-safety rules.

## 2. What will we build?

We will use two vehicles, three cafeterias, five chilled products, and one shipment per vehicle journey. Each shipment contains one product and one batch. The team's 1 November 2026 dataset decision replaces the earlier single-product assumption: milk, yoghurt, fresh cheese, chicken breast and prepared salad, initially 20 crates each. See [the dataset reference](RUNNING.md#demonstration-dataset) and [catalogue](../deployer/catalog.json) for exact units and batch IDs. Three route corridors are implemented; an approved alternative is planned from the current position to the same recipient destination.

| Part | How we will implement it |
| --- | --- |
| Routine workflows | Five executable BPMN models in Camunda: preparation, transport, receiving, corrective fulfilment, and claims |
| Quality investigation | One executable DCR model: evidence can be gathered in different orders, while required checks and decisions remain enforced |
| Vehicle and refrigeration data | A Python simulator sends GPS positions and container-temperature readings; commands change its subsequent behaviour |
| Weather | Live Open-Meteo data provides outdoor conditions for the investigation |
| Travel time | Live TomTom routing provides traffic-aware estimated arrival times (ETA) |
| Event processing | Siddhi detects patterns in readings and estimates, then sends alerts to the workflows |
| Integration | Python connects Siddhi, Camunda, DCR, the simulator, and the external APIs |
| Evidence | A shared logger records business activities and exports logs for compliance checking |

**BPMN** describes the workflow steps and branches. **DCR** describes which activities are allowed and which obligations remain pending. **Complex Event Processing (CEP)** combines events over time to identify a meaningful pattern. We use Siddhi for CEP.

We will reuse the existing Equipment Loan and DCR examples for setup and integration patterns. The team will share one application setup; six processes do not require six separate services.

We will not build route optimisation, physical sensors, detailed refrigeration physics, partial replacements, a full inventory/accounting system, or a polished dashboard. A simple command-line menu will provide the human actions needed for the demonstration.

## 3. How the system will work

This is the target P1–P6 design. The implemented checkpoint above distinguishes implemented P1–P6 behavior from future cooling/recovery automation. Independent live verification and its exact scope are recorded in the [P6 integration supplement](../compliance/p6/review22/verification.json); recorded external conformance retains its original log/model scope.

1. The warehouse prepares a shipment and confirms loading before dispatch.
2. During transport, simulated GPS and temperature data enter Siddhi alongside weather and ETA updates.
3. Siddhi detects a sustained temperature problem or persistent delivery risk.
4. The application opens a DCR investigation and places the shipment on quality hold. Transport decisions, such as changing route, are handled in Camunda.
5. The quality officer reviews evidence, requests checks if needed, and records a decision. Physical receipt is allowed during a hold; admission to stock is blocked.
6. A selected cooling adjustment or route change passes through Siddhi to the simulator. The simulator acknowledges the command and changes its readings or movement.
7. Siddhi checks the later data for recovery. Recovery evidence supports the officer's decision; it does not automatically release the goods.
8. If required, the application tracks replacement/return and a supplier claim through to completion.

This demonstrates communication in both directions: **events affect processes, and process decisions affect later events.**

## 4. Who will do what?

P1 is owned by Ashvanth, P2 by Rahul Shanbhag and P3 by Arunima Garg. Abid owns P4 and Daniel owns P5; Sai Shashank Maktala owns P6 and shared integration ownership remains open. Every owner delivers the model, working implementation, checks and a short explanation. Shared tasks are coordinated by the process owner with help from teammates.

### Ashvanth — P1: Shipment preparation · Camunda

**Build:** accepted order → check stock → allocate stock → assign vehicle and route → verify loading → dispatch. If stock is unavailable, wait for replenishment and recheck, or cancel the shipment.

- Use simple predefined stock and vehicle records. Replenishment is an explicit action; cancellation is allowed only while waiting for stock. Keep damaged-packaging exception handling outside this increment.
- Provide an action to confirm loading verification.
- Send the dispatch event that starts P2.
- Coordinate the shared Docker setup, using the existing reference stack.

**Must prove:** dispatch is blocked before loading verification and succeeds afterwards.

**Hand over to P2:** shipment ID, product and batch IDs, crate quantity, vehicle, destination, journey, route, and delivery deadline.

### Rahul Shanbhag — P2: Transport and delivery · Camunda

**Build:** dispatched shipment → monitor journey → handle delay/route decision if needed → arrive → receive acknowledgement → complete transport.

- Build scripted GPS movement along the predefined routes.
- Connect TomTom and update the remaining travel time from the current position.
- Let the dispatcher accept a delay or approve the alternative route.
- Make an approved route change affect future GPS positions and ETA requests.

**Must prove:** transport cannot finish without receipt acknowledgement; a route change produces observable changes.

**Hand over to P3:** arrival and receipt events. Send delivery-risk alerts to P4 through the shared integration.

### Arunima Garg — P3: Receiving and stock admission · Camunda

**Build:** arrival → acknowledge receipt → inspect goods → check quality status → admit, hold, or reject.

- Coordinate one receipt acknowledgement shared with P2.
- Check the current quality hold when admission is attempted.
- Allow admission after an authorised release; keep rejected goods blocked.
- Build the simple demo menu with actions supplied by all process owners.

**Must prove:** receiving goods does not automatically admit them to stock; admission fails during an active hold.

**Coordinate with P4:** read the same recorded quality status, so the receiving process cannot overlook an investigation.

### Abid — P4: Quality incident investigation · DCR

Abid is the confirmed P4 owner. **Status:** restored and adapted to the foundation contract v1 in the replacement PR — [P4 guide](P4-QUALITY-INVESTIGATION.md), [`deployer/quality.py`](../deployer/quality.py), [`dcr/`](../dcr/), [`compliance/p4/`](../compliance/p4/). Offline tests, the dcrgraphs.net simulation, the dcrgraphs.net API check and the combined live run `ed88d16d` (P4 executing in the dcrgraphs.net repository, on the final PR #12 code) pass, and ProcessDoctor reports 4/4 conforming variants (0 violations) for its log. Cooling/recovery: P4's side of the [draft contract](P4-COOLING-CONTRACT.md) (issue #17) is implemented and tested offline; until the simulator/Siddhi side exists, acknowledgement/recovery inputs are entered manually and labelled `manual-test`.

**Build:** open incident → review evidence → request/complete additional checks if needed → decide release, replacement, or return → close investigation.

- Create and execute a real DCR case for a temperature or delay alert.
- Allow evidence activities in different orders; require evidence assessment before the decision.
- Prevent closure while requested checks or the disposition decision remain outstanding.
- Record the shipment hold immediately when an incident is accepted, and communicate the confirmed decision to P3 and P5.
- Let the officer request a cooling adjustment during investigation.

**Must prove:** required activities are enforced by DCR; a recorded release permits admission, while replacement/return keeps the original goods out of stock.

A delay alone does not prove damaged goods. The officer assesses the evidence. Closing the investigation does not mean a replacement or claim has also finished.

### Daniel — P5: Corrective fulfilment · Camunda

**Implemented in [PR #14](https://github.com/ash-01xor/FrostFlow/pull/14) (merged as `b3f6102`):** applied P4 replacement/return → explicit collection booking → original collection acknowledgement in parallel with replacement preparation/transport/receiving → completed or failed outcome. See the [P5 guide](P5-CORRECTIVE-FULFILMENT.md), [model](../bpmn/corrective_fulfilment.bpmn)/[PDF](../bpmn/corrective_fulfilment.pdf) and [compliance artifacts](../compliance/p5/). Daniel is the confirmed P5 owner.

- Reject corrective work without an authorised quality decision.
- For replacement, create a new shipment linked to the original and reuse P1–P3.
- For return, record collection arrangements and acknowledgement.
- Track the original goods' disposition separately from replacement delivery.
- The foundation already implements excursion/combined-risk/ETA detection and Open-Meteo current conditions. Automatic cooling/recovery and external simulator coordination remain separate shared tasks, with owners unconfirmed.

**Must prove:** replacement/return follows authorisation and requires completion acknowledgement.

### Sai Shashank Maktala — P6: Supplier claim resolution · Camunda

**Build:** record claimable loss → assemble evidence → submit claim → receive settlement/rejection → close.

- Link the claim to the original shipment and incident.
- Simulate supplier submission and response; no real supplier system is required.
- Support settlement and rejection, demonstrating one outcome in the main incident run.
- Coordinate the shared logger, JSON/XES export, and compliance-tool import. Each owner supplies their own process events and rules.

**Must prove:** a claim cannot close without a recorded outcome. An incident does not automatically make a loss claimable.

## 5. Shared rules everyone must follow

The [shared integration agreement](COLD-CHAIN-INTEGRATION-AGREEMENT.md) defines the identifiers, message format, process handoffs, quality states, and command responses below. Review it together before implementing the connections:

- **Identifiers:** use the same shipment and journey IDs across messages. Give each investigation its own incident ID. Link replacement goods to the original shipment.
- **Messages:** agree names, required fields, and who sends/receives each message. Every recorded business activity needs its identity, time, actor, and outcome.
- **Quality status:** P3 and P4 share one recorded hold status. If investigation creation fails, the hold stays active. A later incident can impose a new hold after an earlier release. Rejection is terminal: later risk evidence retains the rejected incident and cannot restore admission permission.
- **Repeated events:** a repeated reading, alert, or user action must not create duplicate incidents or replacement shipments.
- **Input labels:** identify live API data, recorded API responses, simulated telemetry, and human decisions in the logs.

Each person also models their process's compliance rules as a DCR graph and checks execution logs using **processdoctor.compute.dtu.dk**. For example, P3's log must include hold/release events as well as admission, so the rule can actually be checked.

## 6. The three event patterns

| Pattern | What Siddhi checks | Result |
| --- | --- | --- |
| Temperature excursion (implemented assessment) | Three distinct consecutive fresh readings above the snapshotted threshold span 10 s, with at most 10 s gaps | Persist assessment hold and versioned P4 handoff; P4 opens/updates its DCR case |
| Combined transport risk (implemented assessment) | Current-revision persistent fresh late ETA, three rising readings over 10 s (≥0.5°C rise), eligible outdoor temperature ≥30°C | Urgent assessment, coalescing/escalating the current incident |
| Missing container data (implemented watchdog) | No valid reading for 20 s, including after restart | Assessment hold; missing data never means normal |
| Delivery risk | Repeated fresh ETA updates predict arrival after the delivery deadline | Start delay handling and quality assessment |
| Recovery (in progress, issue #17: P4 receiver implemented, Siddhi detection pending; manual-test input until then) | After an acknowledged cooling command, readings remain within the configured range for a configured duration | Send recovery evidence to the investigation; officer still decides disposition |

Missing readings cannot establish normal conditions or recovery. Failed/stale API updates must be shown as unavailable. Weather is supporting evidence, not container temperature or an automatic rejection rule.

Use short, consistent demonstration durations. Save API responses for repeatable tests; live weather and traffic cannot be expected to trigger the same branch every run.

## 7. What we will demonstrate

### A. Normal delivery

Prepare and dispatch a shipment, show GPS/weather/ETA, record receipt and inspection, and admit goods. This demonstrates P1–P3 working together.

### B. Temperature incident and resolution

Inject sustained high temperature. Show the alert, DCR case, and blocked admission. Review evidence and request a cooling adjustment; show acknowledgement and later recovery evidence. Demonstrate release in one run. In another, authorise replacement/return and complete corrective handling and an eligible claim. These runs cover P4–P6 without forcing every branch into one story.

### C. Delivery delay

Inject a stop or slow movement and use live or clearly labelled recorded ETA responses. Show delay detection, assessment, dispatcher approval, and a route change or accepted delay, followed by receipt.

Also check repeated alerts, two independent shipments, missing readings, and a new incident after clearance. Compare a short scripted manual-process baseline with the improved run using the same recorded inputs: detection delay, premature/blocked admission, and time to decision.

## 8. When is each person's work finished?

Each owner provides:

1. Their process model in source format and PDF, with their name identified.
2. Working execution in Camunda or DCR, connected to the relevant neighbouring processes.
3. One successful run and one check showing that a forbidden action is prevented.
4. A DCR compliance graph, matching activity names in the logs, and recorded conformance results.
5. Short run instructions and an explanation that another teammate can follow.

A process is complete when another teammate can run and explain it. Every member must also understand the overall event-to-process-to-simulator loop.

## 9. Final submission

Submit one ZIP containing:

- **`report.pdf`** — maximum ten pages; include the link to a screencast of maximum three minutes.
- **`source.zip`** — process definitions and PDF exports, CEP code, services, configuration, scenarios, checks, and run instructions.
- **`presentation.pdf`** — slides for the ten-minute group presentation; every member presents.

Follow the signed contract's AI-use declaration requirements and record actual assistance. Keep assumptions, simulations, and observed results clearly distinguished.

**Separately supplied reference documents:** the course specification (`project.pdf`), cold-chain proposal (`01-cold-chain-2.pdf`) and signed team contract (`Project Contract 02268 - Group 12_signed.pdf`). Obtain these from the course materials or the team's shared documents. They are intentionally excluded from Git and the Git-based source archive; they are not repository links. Project model PDF exports under `bpmn/` are included.
