# P4 handover to Abid: monitoring foundation v1

> **Status (replacement P4 PR, branch `feat/p4-foundation-integration`):** implemented on foundation main `ae941cc` by reverting `31ad23b` and adapting P4 to this contract. How each boundary below is met and the separately recorded offline, live and external results are listed in the [P4 guide](P4-QUALITY-INVESTIGATION.md#contract-v1-behaviour-enforced-by-the-service) and [sequencing checkpoint 4](P4-INTEGRATION-SEQUENCING.md#checkpoint-4--replacement-p4-pr). With a compatible consumer now present, the single Compose `P4_INCIDENT_URL` entry defaults to it; set it empty to keep handoffs pending.

Abid owns P4. [Original PR #10](https://github.com/ash-01xor/FrostFlow/pull/10) and its models, code and historical evidence remain in Git history. [Revert PR #11](https://github.com/ash-01xor/FrostFlow/pull/11) is merged as `f475904`; removal commit `31ad23b` is retained. Foundation [PR #9](https://github.com/ash-01xor/FrostFlow/pull/9) passed review and merged as `ae941cc`; replacement PR #12 implements this handover. Its integration verification is recorded separately from the foundation checks; PR #12 merged as `70d1a1b` and its exact merged-main smoke passed (see [record](../compliance/merged-main-verification.json)).

P5 subsequently adds an atomic applied-disposition handoff without changing this v1
assessment intake contract; see the [P5 guide](P5-CORRECTIVE-FULFILMENT.md). P6 and
automatic cooling/recovery remain future work.

## Restoration instructions (historical handover)

The instructions in this section describe the original reintegration steps, now implemented by PR #12. Its current Compose default is the compatible local P4 consumer, and CLI 19 runs investigation; the empty default below describes the foundation-only checkpoint.

Start a new branch from the verified merged foundation commit. Normally restore the original P4 by reverting `31ad23b` on that feature branch, then resolve conflicts and adapt contracts. Reopening the old merged-and-reverted branch alone will not restore it. Preserve useful DCR models, attribution and historical evidence. Use new monitored shipments in P4 tests; never remove `monitoringProfile`, skip finalization or bypass guards to make them pass.

Keep exactly one Compose `P4_INCIDENT_URL` entry. The foundation default is empty, leaving durable delivery pending until a compatible consumer is deliberately configured. CLI **18** remains temperature simulation; assign **19** to the P4 investigation. Cooling/recovery needs its own agreed contracts; P5/P6 remain later work.

## Intake and durable acknowledgement

The sender is `MonitoringRegistry.assessment_hold` in [monitoring_registry.py](../deployer/monitoring_registry.py), called by ordered container evaluation, watchdogs and the delivery-risk integration. The application outbox loop posts the stored `variables` object, containing `event`, to `P4_INCIDENT_URL` with `Idempotency-Key: <event.deliveryId>`. Retries preserve the immutable payload and delivery ID. An unavailable or unconfigured consumer never removes the hold.

| Boundary | Required P4 behavior |
| --- | --- |
| Delivery risk | Accept `eventType: DeliveryRiskDetected`, including existing `riskEvent`; monitored shipments also include `assessment`. Preserve support for legacy delivery-risk messages without the extension. |
| Other assessments | Accept `eventType: AssessmentRequested` with assessment reason `temperature-excursion`, `combined-transport-risk`, `container-telemetry-unavailable` or `monitoring-evaluation-unavailable`. |
| Durable acceptance | Persist the full request and deduplication identity before returning a successful JSON response. Return top-level `deliveryId` matching the request and `status: accepted` or `duplicate`. A bare 2xx or the old nested `handoff` result is insufficient. |
| Duplicate request | Identical delivery ID and content returns durable acknowledgement without a second case or repeated business effects; conflicting reuse must fail. DCR unavailability must leave a durable request/case-opening retry and must not lose the request. |
| Evidence | Retain incident and shipment/journey/product/batch identity, schemaVersion, evidenceVersion, reason, priority, interval, profile/session/observation references, ETA/weather context, missingEvidence, source/inputMode and timestamps. Preserve the complete payload; do not reduce it to a risk event ID. |

The outbox message name is `DeliveryRiskIncident` for delivery risk, `AssessmentRequested` otherwise. The wire event type is as above; the original handoff name is not replaced by a temperature-specific POST route. The body is not an unwrapped assessment object.

For monitored messages, `assessment.schemaVersion` is **1**. `reason` identifies the material addition in this immutable message; it is not a cumulative reason list. Collect the incident's evidence across versions. `priority` is `normal` or `urgent` and does not fall within an incident. `evidenceVersion` advances on new material reason/episode; continuous qualifying readings coalesce. Null context and explicit `missingEvidence` are meaningful uncertainty, not permission to release. Weather alone never holds or rejects goods. Urgent combined risk requires rising container readings plus fresh persistent current-route ETA lateness and eligible hot weather. The [profile requirements](PRE-P4-FOUNDATION.md) describe the demonstration assumptions and thresholds.

## Assessment, dispositions and receiving

Capture the evidence version when the officer actually reviews and assesses the evidence. Persist that assessed version with the decision. If a later message advances the incident's evidence version, require reassessment. Never read the latest version at decision submission and silently label an older review current.

Post to `POST /integrations/p4/quality-status` with `shipmentId`, unique `eventId`, UTC `occurredAt`, `qualityStatus: RELEASED` or `REJECTED`, current `incidentId` and the **assessed** integer `expectedEvidenceVersion`. The monitored-shipment contract rejects stale/missing versions and wrong incidents with HTTP 409. A 202 is durable acceptance, not completed admission. Poll actual state with a bounded timeout. Identical accepted event retries reuse their original result; conflicting reuse fails.

If the disposition conflicts, record it as unapplied/stale and require reassessment; do not mark it successfully applied or close the investigation as if release occurred. Keep durable retries for transient failures separate from a stale-decision conflict. The registry remains the authority for quality permission and admission; DCR state cannot override it.

Physical receipt may release the vehicle while receiving admission is blocked. Container sampling continues after GPS arrival until processed physical receipt. P3 then waits for evaluation through the closing sequence; a closing-evaluation timeout establishes a durable uncertainty hold before finalizing. Stock admission checks finalization, passing inspection and current quality permission transactionally. A P4 release does not bypass monitoring finalization or turn receipt into stock admission.

`REJECTED` is terminal. Later evidence retains the rejected incident and disposition, may advance its evidence version, and sends `qualityStatus: REJECTED`, `incidentOpened: false`. Record the evidence without reopening admission permission. Cooling or recovery evidence can inform reassessment but must never automatically release goods.

## Reviewable contract examples

[Fixtures](../stage_tests/fixtures/p4-contract/) are sanitized synthetic producer examples, not live logs or DCR runs. Their fixed IDs and timestamps make changes reviewable. They show a single incident evolving from delay evidence through additional reasons, then rejection. Context represents recorded fixtures, not live providers. Some examples exercise producer construction directly; the existing monitoring suite separately exercises real stored observation windows and Siddhi acknowledgements.

| Example | What it shows |
| --- | --- |
| [delivery-risk.json](../stage_tests/fixtures/p4-contract/delivery-risk.json) | Monitored delay handoff retaining `riskEvent` and v1 extension |
| [initial-assessment.json](../stage_tests/fixtures/p4-contract/initial-assessment.json) | First temperature assessment added to the same incident |
| [urgent-escalation.json](../stage_tests/fixtures/p4-contract/urgent-escalation.json) | Combined-risk material addition and urgent priority |
| [uncertainty.json](../stage_tests/fixtures/p4-contract/uncertainty.json) | Missing telemetry and explicit missing evidence |
| [evaluation-unavailable.json](../stage_tests/fixtures/p4-contract/evaluation-unavailable.json) | Closing-evaluation uncertainty |
| [duplicate-delivery.json](../stage_tests/fixtures/p4-contract/duplicate-delivery.json) | Exact retry body, Idempotency-Key and valid duplicate acknowledgement |
| [newer-evidence.json](../stage_tests/fixtures/p4-contract/newer-evidence.json) | Two versions plus a stale disposition that must conflict |
| [rejected-goods.json](../stage_tests/fixtures/p4-contract/rejected-goods.json) | Current-version rejection followed by evidence retaining terminal status |

Run `.venv/bin/python -m unittest discover -s stage_tests -p 'test_*.py' -v`. `test_p4_contract.py` compares fixtures with the real foundation producer, validates the duplicate acknowledgement, and checks stale-version rejection and terminal rejection on genuinely monitored shipments. Existing `test_mock_consumer_retry_receives_same_delivery_and_payload` in `test_monitoring.py` sends HTTP to a local mock: 503 retains the hold/outbox; retry sends the same identity/payload and a valid durable acknowledgement clears delivery. It does not prove a real P4 consumer. To intentionally regenerate examples after a contract change, run `.venv/bin/python stage_tests/p4_contract_examples.py`, review the JSON diff and update affected consumers/docs/tests together.

## Why PR #10 needs adaptation

Source inspection of `af3c68c` confirms three contract failures reported as reproduced in the supplied sequencing plan: intake accepts only `DeliveryRiskDetected`; acknowledgement lacks top-level matching deliveryId/status; dispositions omit expectedEvidenceVersion and set dispositionApplied after a conflict. All three were freshly reproduced offline against the real foundation registry using temporary SQLite, seeded engine state and substituted DCR calls. See the [results](../compliance/foundation/review-18/legacy-contract-reproduction.json) and [runnable reproduction](../compliance/foundation/review-18/legacy-contract-reproduction.py): `.venv/bin/python compliance/foundation/review-18/legacy-contract-reproduction.py`. This is not live P4/DCR execution or a merged-branch run. The [sequencing record](P4-INTEGRATION-SEQUENCING.md) preserves the earlier source review and its limits. The branches also conflict on the Compose P4 default (do not retain duplicate mapping entries) and CLI option 18.

A replacement P4 PR must demonstrate release, replacement/return rejection, newer-evidence reassessment, duplicate delivery, recovery after unavailable DCR and physical receipt during a hold. Record fresh combined foundation/P4 execution and keep original P4 results historical. If integration exposes a foundation defect, fix it with a meaningful regression instead of weakening the contract.
