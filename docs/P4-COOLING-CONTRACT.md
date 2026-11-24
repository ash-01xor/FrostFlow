# P4 cooling command, acknowledgement and recovery contract (draft v1)

**Status: DRAFT for agreement** between Abid (P4) and Rahul (simulator/Siddhi), then review by Ashvanth (integration verification) and Arunima (P3 receipt/admission boundary). Tracks [issue #17](https://github.com/ash-01xor/FrostFlow/issues/17). Field names, endpoints, timeout and recovery criteria below are proposals until both owners confirm them on the issue or PR.

**Implemented so far (P4 side only):** [`deployer/p4_cooling.py`](../deployer/p4_cooling.py), the two receivers in [`deployer/quality.py`](../deployer/quality.py), command delivery in [`deployer/app.py`](../deployer/app.py) and offline regressions in [`stage_tests/test_p4_cooling.py`](../stage_tests/test_p4_cooling.py). The simulator applying a command to its readings, the Siddhi cooling-command bridge and the Siddhi recovery query (Rahul's tasks) **do not exist yet**, so nothing below has run end-to-end; the offline tests post the simulator and Siddhi messages themselves.

## Flow

```mermaid
sequenceDiagram
    participant O as Quality officer
    participant P4 as P4 (DCR + registry)
    participant S as Siddhi
    participant Sim as Scripted simulator (communication service)
    O->>P4: RequestCoolingAdjustment (DCR)
    P4->>P4: same transaction: journal + command + outbox (commandId)
    P4->>S: POST P4_COOLING_COMMAND_URL {"event": CoolingAdjustmentRequested}, Idempotency-Key = commandId
    S->>Sim: forward command
    Sim-->>S: {commandId, status: received|duplicate}
    S-->>P4: same answer (receipt only, NOT application)
    Sim->>Sim: next readings use the new setpoint
    Sim->>P4: POST /integrations/p4/cooling-result {"event": CoolingCommandResult APPLIED|FAILED}
    P4->>P4: RecordCoolingAcknowledgement or RecordCoolingFailure (DCR)
    S->>S: fresh distinct readings after application qualify recovery
    S->>P4: POST /integrations/p4/recovery {"event": RecoveryEvaluated}
    P4->>P4: re-check stored readings, then RecordRecoveryEvidence (DCR)
    Note over P4: hold stays PENDING; DCR makes AssessEvidence due; officer decides
```

## 1. Command: `CoolingAdjustmentRequested` (P4 → Siddhi bridge → simulator)

Produced when DCR has executed `RequestCoolingAdjustment`, in the same SQLite transaction as its journal entry. Delivered by the communication service's outbox loop (destination `p4-cooling-command`) to `P4_COOLING_COMMAND_URL` with `Idempotency-Key: <commandId>`; retried with the same body until a definite answer or until P4 times the command out.

| Field | Meaning |
| --- | --- |
| `schemaVersion` | `1` |
| `eventType` | `CoolingAdjustmentRequested` |
| `commandId` | `P4:COOL:<incidentId>:<n>`, stable across retries/restarts; `n` counts cooling requests of the incident |
| `command` | `AdjustCooling` |
| `shipmentId`, `journeyId`, `sessionId`, `incidentId`, `productId`, `batchId` | Correlation; `sessionId` is the container-monitoring session (`CONTAINER:<shipment>:<journey>`) active when P4 accepted the request |
| `targetC` | Profile normal temperature (3.0 °C in `CHILLED-DEMO-v1`) |
| `evidenceVersion` | Registry evidence version the officer saw |
| `requestedBy`, `requestedAt` | Officer and UTC time |
| `expiresAt` | `requestedAt` + `P4_COOLING_ACK_TIMEOUT_SECONDS` (default 45 s). **The simulator must refuse (result `FAILED`, `expired`) and must not apply a command received after this time.** |
| `recoveryCriteria` | Snapshot that Siddhi must use and echo (section 3) |
| `source`, `inputMode` | `p4-quality-investigation`, `manual` (officer decision) |

**Receiver answer (bridge/simulator):** `200 {"commandId": ..., "status": "received" | "duplicate"}` after the simulator has durably stored the command. This is receipt, not application. An identical retry answers `duplicate` and never applies the command twice. A changed body with the same `commandId` must be refused (409).

**P4 behaviour:**
- One outstanding command per incident: a second `RequestCoolingAdjustment` is refused (409) until the first has a result.
- With `P4_COOLING_COMMAND_URL` set, P4 refuses `RequestCoolingAdjustment` before DCR (409) unless the shipment's monitoring session is `ACTIVE` (before physical receipt).
- HTTP 400/404/409/422 from the receiver is a definite refusal: P4 fails the command (`command-refused`) and executes `RecordCoolingFailure`. Other errors are retried.
- No result by `expiresAt`: P4 fails the command (`acknowledgement-timeout`), executes `RecordCoolingFailure` and cancels the outbox message if it was never delivered. A later result is kept only as late evidence.

## 2. Result: `CoolingCommandResult` (simulator → P4)

`POST /integrations/p4/cooling-result`, body `{"event": {...}}`, header `Idempotency-Key: <resultId>`. Proposed producer: the communication service's simulator, through its own durable outbox.

| Field | Meaning |
| --- | --- |
| `schemaVersion`, `eventType` | `1`, `CoolingCommandResult` |
| `resultId` | Stable result identity (e.g. `SIM:<commandId>:APPLIED`) |
| `commandId`, `incidentId`, `shipmentId`, `journeyId`, `sessionId` | Must equal the command's values |
| `status` | `APPLIED` or `FAILED` |
| `appliedAt`, `effectiveFromSequence` | `APPLIED` only: UTC time and the **first container-reading sequence produced under the new setpoint** |
| `failureReason`, `detail` | `FAILED` only. Proposed reasons: `session-closed` (receipt closed the session while in flight), `expired`, `wrong-session`, `simulator-error` |
| `occurredAt`, `source`, `inputMode` | UTC time; e.g. `container-simulator`, `simulated` |

**P4 answer:** `200 {"resultId", "status": "accepted" | "duplicate", "effect"}` after the result is persisted. `effect` is `acknowledgement-queued`, `failure-queued` or `late-result-recorded` (the command already had a result or timed out, or its DCR case was superseded/closed). `400` malformed; `404` unknown incident; `409` unknown command, identity mismatch (other shipment/journey/session) or a changed body for a used `resultId`. Only the first result of a command counts; it also cancels any still-pending command delivery.

## 3. Recovery: `RecoveryEvaluated` (Siddhi → P4)

`POST /integrations/p4/recovery`, body `{"event": {...}}`, header `Idempotency-Key: <recoveryId>`. Siddhi may post directly or relay through the communication service; delivery must be retried until P4 answers 200.

| Field | Meaning |
| --- | --- |
| `schemaVersion`, `eventType` | `1`, `RecoveryEvaluated` |
| `recoveryId` | Stable identity (e.g. `SIDDHI:RECOVERY:<commandId>:<lastSequence>`) |
| `commandId`, `incidentId`, `shipmentId`, `journeyId`, `sessionId` | Must equal the command's values |
| `outcome` | `RECOVERED` or `NOT_RECOVERED` |
| `firstSequence`, `lastSequence`, `readingCount`, `qualifiedAt` | `RECOVERED`: the qualifying readings and evaluation time (UTC) |
| `criteria` | Exactly the command's `recoveryCriteria` |
| `reason`, `occurredAt`, `source`, `inputMode` | e.g. `siddhi`, `simulated` |

**Proposed demo recovery criteria** (snapshotted from the shipment's monitoring profile when the command is created): `thresholdC` = profile threshold (5 °C, chicken 4 °C), `minReadings` 3, `minSpanSeconds` 10, `maxGapSeconds` 10, `freshSeconds` 15. These are demonstration settings, not validated safety limits.

**P4 re-checks every `RECOVERED` evaluation against the stored readings** (`container_observations`) before executing anything. It counts only if: the command is integrated and `APPLIED`; `criteria` equals the snapshot; `firstSequence ≥ effectiveFromSequence` and the first reading is not older than `appliedAt`; the sequences are consecutive, all stored and from the command's session; `readingCount` = `lastSequence − firstSequence + 1` ≥ `minReadings`; every reading ≤ `thresholdC`; each gap is 0 < gap ≤ `maxGapSeconds`; the span is ≥ `minSpanSeconds`; and `qualifiedAt` is within `freshSeconds` after the last reading and not in the future. One normal reading, missing data, a telemetry gap, readings from before application, another session or weather can therefore never become recovery.

**P4 answer:** `200 {"recoveryId", "status": "accepted" | "duplicate", "effect"[, "refusal"]}` after persisting. `effect`: `recovery-queued` (executes `RecordRecoveryEvidence`), `recovery-refused` (journalled `RecoveryEvidenceRefused` with the reason; no DCR effect), `not-recovered-recorded` (`RecoveryNotQualified`), or `evidence-recorded` (a further recovery for the same command, or its DCR case was superseded/closed). `400`/`404`/`409` as for results.

## 4. Guards that do not change

- Recovery never changes `qualityStatus`. `RecordRecoveryEvidence` makes `AssessEvidence` due in DCR (response + milestone), so a decision needs a fresh assessment; the registry's `expectedEvidenceVersion` and current-incident checks still apply. `REJECTED` stays terminal.
- Monitoring still ends at physical receipt and vehicle release stays independent. A command in flight at receipt must end as `FAILED` (`session-closed`); P4 refuses new commands once the session is closed. No readings are invented after receipt.
- If a refused decision moved the investigation to a new DCR case, results/recovery for a command of the old case are recorded as late evidence only.

## 5. Manual test input (retained)

With `P4_COOLING_COMMAND_URL` empty (the Compose default until the simulator/Siddhi side exists), `RequestCoolingAdjustment` creates a `manual-test` command that is not sent anywhere, and the officer may enter `RecordCoolingAcknowledgement`, `RecordCoolingFailure` and `RecordRecoveryEvidence` manually. Those journal entries carry `inputMode: manual-test` and `manualTestInput: true`. For an integrated command, manual entry of these three activities is refused (409).

## 6. Open points for agreement

1. Endpoint of the Siddhi cooling-command bridge (proposal: `http://siddhi:8007/cooling-command`, like `/route-command`) and whether Siddhi answers synchronously with the simulator's receipt.
2. Timeout (45 s) against simulator cadence (5 s sampling) and how the simulator defines `effectiveFromSequence`.
3. Recovery criteria values and whether Siddhi also emits `NOT_RECOVERED` (e.g. after a maximum wait).
4. Who relays recovery durably (Siddhi `http` sink with retries, or the communication service's outbox).
5. P3 boundary (Arunima): confirm no receiving-side cooling and that the admission check needs no change.
