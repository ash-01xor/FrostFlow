"""P4 quality incident investigation executed in DCR (foundation contract v1).

See docs/P4-FOUNDATION-HANDOVER.md and docs/P4-QUALITY-INVESTIGATION.md.

- Intake: the foundation outbox posts `{"event": ...}` to P4_INCIDENT_URL with
  `Idempotency-Key: <event.deliveryId>`. P4 accepts `DeliveryRiskDetected` (legacy, or
  with the v1 `assessment` extension) and `AssessmentRequested` (temperature excursion,
  combined transport risk, telemetry unavailable, evaluation unavailable). The full
  request is persisted before P4 answers `{"deliveryId", "status": accepted|duplicate}`.
  The first message of an incident opens a DCR case (OpenIncident); later messages are
  AddAlertEvidence, which makes a new assessment due. A closed incident only records it.
- DCR decides which investigation activities are enabled. If DCR is unreachable the
  request stays persisted and case opening is retried in the background.
- AssessEvidence records the evidence version the request saw before DCR executed it;
  evidence arriving meanwhile makes a reassessment due. AssessEvidence and decisions are
  refused while new evidence is still being added to the DCR case.
- A decision is checked with the registry first (Registry.check_quality_status, read
  only), then executed in DCR, and only then applied through
  Registry.accept_quality_status (the same operation as POST
  /integrations/p4/quality-status) with the assessed version as expectedEvidenceVersion.
  Quality permission therefore never exists before DCR has executed the decision. A
  refusal (newer evidence, wrong incident, terminal rejection, DCR no longer enabling
  it) is recorded as unapplied and requires reassessment; if the registry refuses after
  DCR executed the decision, the investigation continues in a new DCR case. A transient
  failure keeps a durable pending decision that is retried with the same identity; a
  lost DCR response is reconciled on retry (see _finish_decision).
- Officer requests with an eventId are idempotent: identical retries return the original
  outcome, reuse with a different activity, actor, reason or note is a 409 conflict.
- The registry stays the authority for quality permission and admission.
- Cooling commands, simulator results and Siddhi recovery evidence: see p4_cooling.py
  and docs/P4-COOLING-CONTRACT.md.
"""

import json
import threading
from datetime import datetime

from flask import Blueprint, jsonify, request

from dcr_client import DCRRefused, DCRUnavailable
from p4_cooling import INTEGRATION_RESULTS, CoolingMixin
from registry import Conflict, Registry, now

# DCR event id -> role. Event ids are also the logged activity names.
ROLES = {
    "OpenIncident": "Integration",
    "AddAlertEvidence": "Integration",
    "ReviewTemperatureLog": "QualityOfficer",
    "ReviewWeatherData": "QualityOfficer",
    "ReviewRouteAndETA": "QualityOfficer",
    "RequestCoolingAdjustment": "QualityOfficer",
    "RecordCoolingAcknowledgement": "Integration",
    "RecordCoolingFailure": "Integration",
    "RecordRecoveryEvidence": "Integration",
    "RequestAdditionalCheck": "QualityOfficer",
    "CompleteAdditionalCheck": "Receiver",
    "AssessEvidence": "QualityOfficer",
    "DecideRelease": "QualityOfficer",
    "DecideReplace": "QualityOfficer",
    "DecideReturn": "QualityOfficer",
    "CloseInvestigation": "QualityOfficer",
}
INTEGRATION_ONLY = {"OpenIncident", "AddAlertEvidence"}
DECISIONS = {
    "DecideRelease": ("RELEASE", "RELEASED"),
    "DecideReplace": ("REPLACE", "REJECTED"),
    "DecideReturn": ("RETURN", "REJECTED"),
}
# Not executed while new evidence is still being added to the DCR case.
ASSESSMENT_GATED = {"AssessEvidence", *DECISIONS}
EVENT_TYPES = {"DeliveryRiskDetected", "AssessmentRequested"}
ASSESSMENT_REASONS = {
    "DeliveryRiskDetected": {"delivery-risk"},
    "AssessmentRequested": {"temperature-excursion", "combined-transport-risk",
                            "container-telemetry-unavailable",
                            "monitoring-evaluation-unavailable"},
}


class UnknownIncident(KeyError):
    pass


class Blocked(Conflict):
    """DCR does not currently enable the requested activity."""

    def __init__(self, activity, enabled, detail=None):
        super().__init__(detail or f"DCR does not enable {activity} in this investigation now")
        self.enabled = enabled


class StaleDecision(Conflict):
    """The registry refused the disposition; the officer must reassess."""

    def __init__(self, message, current_version):
        super().__init__(message)
        self.current_version = current_version


class DecisionPending(RuntimeError):
    """The disposition is durably recorded and will be retried."""


class ActivityPending(DecisionPending):
    """DCR's answer for an officer activity was lost; it is settled on retry."""


def ensure_schema(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS incidents (id TEXT PRIMARY KEY, "
        "shipment_id TEXT NOT NULL, data TEXT NOT NULL)"
    )


def read_incident(db, incident_id):
    ensure_schema(db)
    row = db.execute("SELECT data FROM incidents WHERE id=?", (incident_id,)).fetchone()
    if row is None:
        raise UnknownIncident(incident_id)
    return json.loads(row[0])


def find_incident(db, incident_id):
    try:
        return read_incident(db, incident_id)
    except UnknownIncident:
        return None


def save_incident(db, incident):
    db.execute(
        "INSERT OR REPLACE INTO incidents VALUES (?,?,?)",
        (incident["incidentId"], incident["shipmentId"], json.dumps(incident)),
    )


def journal(db, record, incident, activity, actor="system", payload=None,
            event_id=None, occurred_at=None, input_mode=None, source=None):
    """Record one P4 activity in the shared journal (idempotent by event id)."""
    if event_id is None:
        incident["activityCount"] += 1
        event_id = f"P4:{incident['incidentId']}:{incident['activityCount']:03d}:{activity}"
    event = Registry.event(
        db, record, activity, actor=actor, process="P4", event_id=event_id,
        payload={"incidentId": incident["incidentId"], **(payload or {})},
        occurred_at=occurred_at, source=source or "quality-investigation",
        input_mode=input_mode,
    )
    # P4 keeps its own role names and links each activity to its DCR case.
    patch = dict(incidentId=incident["incidentId"],
                 role=ROLES.get(activity, event["role"]),
                 processInstanceKey=incident.get("dcrCaseId"))
    if any(event.get(k) != v for k, v in patch.items()):
        event.update(patch)
        db.execute("UPDATE events SET data=? WHERE id=?", (json.dumps(event), event_id))
    return event


def text(data, field, required=True, limit=500):
    value = data.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must be a non-empty string of at most {limit} characters")
    return value.strip()


def utc(value, field):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO 8601 UTC timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO 8601 UTC timestamp") from error
    if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field} must be a UTC timestamp")
    return value


def parse_handoff(data, idempotency_key):
    """Validate the wire envelope and return a summary; the full body is kept as is."""
    event = data.get("event") if isinstance(data, dict) else None
    if not isinstance(event, dict):
        raise ValueError("send the foundation envelope {\"event\": {...}}")
    delivery_id = text(event, "deliveryId", limit=1000)
    if idempotency_key != delivery_id:
        raise ValueError("Idempotency-Key must match event.deliveryId")
    event_type = event.get("eventType")
    if event_type not in EVENT_TYPES:
        raise ValueError(f"eventType must be one of {sorted(EVENT_TYPES)}")
    if not isinstance(event.get("incidentOpened"), bool):
        raise ValueError("incidentOpened must be a boolean")
    summary = dict(
        deliveryId=delivery_id, eventType=event_type,
        shipmentId=text(event, "shipmentId", limit=100),
        journeyId=text(event, "journeyId", limit=100),
        incidentId=text(event, "incidentId", limit=1000),
        incidentOpened=event["incidentOpened"],
        qualityStatus=text(event, "qualityStatus", limit=20),
        reason="delivery-risk", evidenceVersion=None, priority="normal", occurredAt=None,
    )
    assessment = event.get("assessment")
    if event_type == "AssessmentRequested" and not isinstance(assessment, dict):
        raise ValueError("AssessmentRequested needs the assessment extension")
    if assessment is not None:
        if not isinstance(assessment, dict) or assessment.get("schemaVersion") != 1:
            raise ValueError("assessment.schemaVersion must be 1")
        if assessment.get("reason") not in ASSESSMENT_REASONS[event_type]:
            raise ValueError(f"assessment.reason not valid for {event_type}")
        version = assessment.get("evidenceVersion")
        if type(version) is not int or version < 1:
            raise ValueError("assessment.evidenceVersion must be a positive integer")
        if assessment.get("priority") not in ("normal", "urgent"):
            raise ValueError("assessment.priority must be normal or urgent")
        summary.update(reason=assessment["reason"], evidenceVersion=version,
                       priority=assessment["priority"],
                       occurredAt=utc(assessment.get("occurredAt"), "assessment.occurredAt"))
    if event_type == "DeliveryRiskDetected":
        risk = event.get("riskEvent")
        if not isinstance(risk, dict):
            raise ValueError("DeliveryRiskDetected needs riskEvent")
        text(risk, "eventId", limit=256)
        summary["occurredAt"] = summary["occurredAt"] or utc(risk.get("occurredAt"),
                                                             "riskEvent.occurredAt")
    return summary


def new_incident(incident_id, shipment_id, dcr):
    return dict(
        incidentId=incident_id,
        shipmentId=shipment_id,
        status="OPENING",
        openedAt=now(),
        evidence=[],
        evidenceVersion=None,
        priority="normal",
        assessedEvidenceVersion=None,
        pendingSystemEvents=["OpenIncident"],
        dcrMode=dcr.mode,
        dcrGraphId=getattr(dcr, "graph_id", None),
        dcrCaseId=None,
        pendingDecision=None,
        decision=None,
        unappliedDecisions=[],
        reassessmentRequired=False,
        closedAt=None,
        lastError=None,
        activityCount=0,
    )


class QualityService(CoolingMixin):
    def __init__(self, get_registry, get_dcr):
        self.get_registry = get_registry
        self.get_dcr = get_dcr
        self.lock = threading.RLock()  # one DCR interaction at a time per service

    @property
    def registry(self):
        return self.get_registry()

    @property
    def dcr(self):
        return self.get_dcr()

    # -- intake -------------------------------------------------------------
    def accept_handoff(self, data, idempotency_key):
        """Durably accept one foundation handoff (at-least-once delivery)."""
        summary = parse_handoff(data, idempotency_key)
        delivery_id, incident_id = summary["deliveryId"], summary["incidentId"]
        key = f"p4-handoff:{delivery_id}"
        dcr = self.dcr  # resolved outside the transaction: LocalDCR shares the SQLite file
        with self.registry.transaction() as db:
            ensure_schema(db)
            row = db.execute("SELECT data FROM results WHERE id=?", (key,)).fetchone()
            if row:
                saved = json.loads(row[0])
                if saved["request"] != data:
                    raise Conflict("deliveryId reused with different content")
                return dict(deliveryId=delivery_id, status="duplicate",
                            incidentId=saved["incidentId"], effect=saved["effect"])
            produced = db.execute("SELECT data FROM outbox WHERE id=?", (delivery_id,)).fetchone()
            if not produced or json.loads(produced[0])["variables"] != data:
                raise Conflict("handoff does not match a foundation outbox message")
            record = Registry.read(db, summary["shipmentId"])
            if record["journeyId"] != summary["journeyId"]:
                raise Conflict("handoff journey does not match the shipment")
            evidence = dict(summary, receivedAt=now(), request=data["event"])
            incident = find_incident(db, incident_id)
            if incident is None and summary["qualityStatus"] != "PENDING":
                # Terminal/decided incident P4 never investigated (e.g. a test stand-in).
                journal(db, record, dict(incidentId=incident_id, activityCount=0),
                        "LateAlertEvidenceRecorded", payload=self._evidence_payload(summary),
                        event_id=f"P4:{incident_id}:late:{delivery_id}",
                        occurred_at=summary["occurredAt"])
                effect = "evidence-recorded"
            else:
                if incident is None:
                    incident, effect = new_incident(incident_id, record["shipmentId"], dcr), \
                        "incident-opened"
                elif incident["status"] == "CLOSED":
                    effect = "evidence-recorded"
                else:
                    incident["pendingSystemEvents"].append("AddAlertEvidence")
                    effect = "evidence-added"
                incident["evidence"].append(evidence)
                if summary["evidenceVersion"] is not None:
                    incident["evidenceVersion"] = max(incident["evidenceVersion"] or 0,
                                                      summary["evidenceVersion"])
                if summary["priority"] == "urgent":
                    incident["priority"] = "urgent"
                journal(db, record, incident,
                        "LateAlertEvidenceRecorded" if effect == "evidence-recorded"
                        else "QualityAlertReceived",
                        payload=self._evidence_payload(summary),
                        event_id=f"P4:{incident_id}:received:{delivery_id}",
                        occurred_at=summary["occurredAt"],
                        input_mode=(data["event"].get("assessment") or {}).get("inputMode",
                                                                              "system"),
                        source="p4-intake")
                save_incident(db, incident)
            db.execute("INSERT INTO results VALUES (?,?)", (key, json.dumps(dict(
                request=data, incidentId=incident_id, effect=effect))))
        if effect != "evidence-recorded" or incident is not None:
            self.sync(incident_id)
        return dict(deliveryId=delivery_id, status="accepted", incidentId=incident_id,
                    effect=effect)

    @staticmethod
    def _evidence_payload(summary):
        return {k: summary[k] for k in ("deliveryId", "eventType", "reason", "evidenceVersion",
                                         "priority", "qualityStatus", "incidentOpened")}

    # -- DCR synchronisation ------------------------------------------------
    def sync(self, incident_id):
        """Create the DCR case if missing, execute queued integration events and
        finish a durably pending decision."""
        with self.lock:
            incident = self._read(incident_id)
            pending = incident.get("pendingDecision")
            if pending and pending.get("dcrAttempted") and not pending.get("dcrExecuted"):
                # Settle a decision whose DCR response was lost before anything else
                # changes the case; otherwise "no longer enabled" would not prove it ran.
                try:
                    self._finish_decision(incident_id)
                except (StaleDecision, DecisionPending, Blocked, DCRUnavailable):
                    pass
                incident = self._read(incident_id)
                pending = incident.get("pendingDecision")
                if pending and pending.get("dcrAttempted") and not pending.get("dcrExecuted"):
                    return incident
            if incident.get("pendingActivity"):
                try:
                    self._finish_activity(incident_id)
                except (Blocked, ActivityPending, DCRUnavailable):
                    pass
                incident = self._read(incident_id)
                if incident.get("pendingActivity"):
                    return incident
            try:
                case = incident.get("dcrCaseId")
                if not case and incident["status"] != "CLOSED":
                    case = self.dcr.create_simulation(title=incident_id)
                    with self.registry.transaction() as db:
                        incident = read_incident(db, incident_id)
                        incident["dcrCaseId"] = case
                        save_incident(db, incident)
                while incident["pendingSystemEvents"]:
                    queued = incident["pendingSystemEvents"][0]
                    activity = queued["activity"] if isinstance(queued, dict) else queued
                    enabled = {e["id"] for e in self.dcr.events(case)["events"] if e["enabled"]}
                    # An attempt whose DCR answer was lost: if DCR no longer enables the
                    # event, that attempt executed it (e.g. OpenIncident excludes itself).
                    executed = activity not in enabled and incident.get(
                        "systemEventAttempt") == activity
                    if activity in enabled:
                        with self.registry.transaction() as db:
                            incident = read_incident(db, incident_id)
                            incident["systemEventAttempt"] = activity
                            save_incident(db, incident)
                        self.dcr.execute(case, activity)
                    with self.registry.transaction() as db:
                        incident = read_incident(db, incident_id)
                        incident["pendingSystemEvents"].pop(0)
                        incident["systemEventAttempt"] = None
                        record = Registry.read(db, incident["shipmentId"])
                        ran = activity in enabled or executed
                        if isinstance(queued, dict):
                            journal(db, record, incident,
                                    activity if ran else queued["late"],
                                    payload=queued.get("payload"),
                                    event_id=queued["journalId"] if ran
                                    else f"{queued['journalId']}:late",
                                    input_mode=queued.get("inputMode"),
                                    source=queued.get("source"))
                        else:
                            journal(db, record, incident,
                                    activity if ran else "LateAlertEvidenceRecorded")
                        save_incident(db, incident)
                if incident["status"] in ("OPENING", "DCR_UNAVAILABLE"):
                    with self.registry.transaction() as db:
                        incident = read_incident(db, incident_id)
                        incident.update(status="OPEN", lastError=None)
                        save_incident(db, incident)
            except DCRUnavailable as error:
                with self.registry.transaction() as db:
                    incident = read_incident(db, incident_id)
                    if incident["status"] != "CLOSED":
                        incident["status"] = "DCR_UNAVAILABLE"
                    incident["lastError"] = str(error)
                    save_incident(db, incident)
                return incident
            if incident.get("pendingDecision"):
                try:
                    self._finish_decision(incident_id)
                except (StaleDecision, DecisionPending, Blocked, DCRUnavailable):
                    pass  # recorded on the incident; the officer or the retry loop continues
            with self.registry.transaction() as db:
                return read_incident(db, incident_id)

    def retry_unfinished(self):
        """Background retry for cases waiting on DCR or on a pending decision, and the
        timeout of cooling commands without a simulator result."""
        self.expire_cooling_commands()
        with self.registry.transaction() as db:
            ensure_schema(db)
            waiting = [
                incident["incidentId"]
                for incident in (json.loads(r[0]) for r in db.execute("SELECT data FROM incidents"))
                if incident["status"] in ("OPENING", "DCR_UNAVAILABLE")
                or incident.get("pendingDecision") or incident.get("pendingActivity")
                or (incident["status"] == "OPEN" and incident.get("pendingSystemEvents"))
            ]
        for incident_id in waiting:
            self.sync(incident_id)

    # -- decisions ----------------------------------------------------------
    def _finish_decision(self, incident_id):
        """Check the disposition, execute it in DCR, then apply it in the registry.

        1. Registry.check_quality_status validates the disposition (current incident,
           assessed evidence version, allowed transition) without changing anything.
        2. DCR executes the decision. The attempt is made durable first. If the response
           is lost, a retry treats the decision as executed when DCR no longer enables
           it: only this service changes a case (under self.lock) and sync finishes an
           attempted decision before anything else, so nothing else could have disabled
           it. A definite refusal (DCRRefused) is not taken as executed.
        3. Only then accept_quality_status applies RELEASED/REJECTED with the assessed
           version as expectedEvidenceVersion. Quality permission therefore never exists
           before DCR has executed the decision; until step 3 succeeds the shipment stays
           PENDING and P3 keeps it on hold.
        If newer evidence reached the registry between steps 1 and 3 the registry
        refuses. The executed DCR case cannot take another decision (each decision
        excludes AssessEvidence and all decisions), so the investigation continues in a
        new DCR case for the same incident, which requires a fresh assessment.
        """
        incident = self._read(incident_id)
        pending = incident["pendingDecision"]
        if not pending.get("dcrExecuted"):
            if not pending.get("dcrAttempted"):
                try:
                    self.registry.check_quality_status(
                        incident["shipmentId"], pending["eventId"], pending["qualityStatus"],
                        incident_id, pending["expectedEvidenceVersion"])
                except Conflict as error:
                    self._refuse(incident_id, error)
                except Exception as error:  # e.g. a locked database: retried as is
                    self._keep_pending(incident_id, error)
            case, activity = incident["dcrCaseId"], pending["activity"]
            reconciled = False
            try:
                enabled = self._enabled(case)
                if activity in enabled:
                    self._mark_pending(incident_id, dcrAttempted=True)
                    self.dcr.execute(case, activity)
                elif pending.get("dcrAttempted"):
                    reconciled = True  # executed by the earlier attempt; response was lost
                else:
                    self._refuse(incident_id, Blocked(activity, enabled))
            except DCRRefused as error:
                self._mark_pending(incident_id, dcrAttempted=False)
                self._refuse(incident_id, Blocked(activity, [], str(error)))
            except DCRUnavailable as error:
                self._keep_pending(incident_id, error)
            with self.registry.transaction() as db:
                incident = read_incident(db, incident_id)
                record = Registry.read(db, incident["shipmentId"])
                pending = incident["pendingDecision"]
                payload = self._decision_payload(pending)
                if reconciled:
                    payload["reconciledAfterLostResponse"] = True
                event = journal(db, record, incident, activity, pending["officer"], payload,
                                event_id=pending.get("journalId"),
                                occurred_at=pending["occurredAt"], input_mode="manual")
                pending.update(dcrExecuted=True, journalEventId=event["eventId"])
                save_incident(db, incident)
        try:
            self.registry.accept_quality_status(
                incident["shipmentId"], pending["eventId"], pending["occurredAt"],
                pending["qualityStatus"], incident_id, pending["expectedEvidenceVersion"])
        except Conflict as error:
            self._refuse(incident_id, error, restart_case=True)
        except Exception as error:  # DCR executed; the registry write is retried as is
            self._keep_pending(incident_id, error)
        with self.registry.transaction() as db:
            incident = read_incident(db, incident_id)
            record = Registry.read(db, incident["shipmentId"])
            pending = incident["pendingDecision"]
            payload = self._decision_payload(pending)
            disposition = journal(db, record, incident, "DispositionRecorded", actor=pending["officer"],
                    payload=dict(payload, decision=pending["decision"],
                                 evidence=[e["deliveryId"] for e in incident["evidence"]]),
                    event_id=f"P4:{incident_id}:DispositionRecorded:{pending['eventId']}",
                    occurred_at=pending["occurredAt"])
            incident.update(pendingDecision=None, lastError=None, decision=dict(
                decision=pending["decision"], officer=pending["officer"],
                reason=pending["reason"], note=pending.get("note"),
                decidedAt=pending["occurredAt"], qualityStatus=pending["qualityStatus"],
                assessedEvidenceVersion=pending["expectedEvidenceVersion"],
                qualityStatusEventId=pending["eventId"]))
            save_incident(db, incident)
            self.registry.authorize_corrective(db, record, incident, disposition)
            event = json.loads(db.execute("SELECT data FROM events WHERE id=?",
                                          (pending["journalEventId"],)).fetchone()[0])
        return incident, event

    @staticmethod
    def _decision_payload(pending):
        payload = {k: pending[k] for k in ("reason", "qualityStatus",
                                           "expectedEvidenceVersion", "eventId")}
        if pending.get("note"):
            payload["note"] = pending["note"]
        return payload

    def _read(self, incident_id):
        with self.registry.transaction() as db:
            return read_incident(db, incident_id)

    def _enabled(self, case):
        return sorted(e["id"] for e in self.dcr.events(case)["events"] if e["enabled"])

    def _mark_pending(self, incident_id, **changes):
        with self.registry.transaction() as db:
            incident = read_incident(db, incident_id)
            incident["pendingDecision"].update(changes)
            save_incident(db, incident)

    def _keep_pending(self, incident_id, error):
        with self.registry.transaction() as db:
            incident = read_incident(db, incident_id)
            incident["lastError"] = f"disposition pending: {error}"
            save_incident(db, incident)
        raise DecisionPending(str(error)) from error

    def _refuse(self, incident_id, error, restart_case=False):
        """Drop the pending decision as unapplied; the officer must reassess."""
        with self.registry.transaction() as db:
            incident = read_incident(db, incident_id)
            record = Registry.read(db, incident["shipmentId"])
            current = record.get("evidenceVersion")
            pending = incident["pendingDecision"]
            refused = dict(pending, refusal=str(error), currentEvidenceVersion=current,
                           executedInDcr=bool(pending.get("dcrExecuted")))
            incident["unappliedDecisions"].append(refused)
            incident.update(pendingDecision=None, lastError=str(error),
                            reassessmentRequired=True)
            if restart_case:
                incident["previousDcrCases"] = (incident.get("previousDcrCases") or []) + [
                    incident["dcrCaseId"]]
                incident.update(dcrCaseId=None, status="OPENING",
                                pendingSystemEvents=["OpenIncident"])
            journal(db, record, incident, "StaleDispositionRefused",
                    actor=pending["officer"], payload=refused,
                    event_id=f"P4:{incident_id}:refused:{pending['eventId']}",
                    occurred_at=pending["occurredAt"])
            save_incident(db, incident)
        if restart_case:
            self.sync(incident_id)  # open the follow-up DCR case now if DCR is reachable
        if isinstance(error, Blocked):
            raise error
        raise StaleDecision(
            f"disposition not applied: {error}. Reassess the current evidence "
            f"(registry evidence version {current}).", current) from error

    # -- investigation activities -------------------------------------------
    def execute(self, incident_id, activity, data):
        if activity not in ROLES:
            raise ValueError(f"unknown investigation activity: {activity}")
        if activity in INTEGRATION_ONLY:
            raise ValueError(f"{activity} is performed by the integration service")
        actor = text(data, "actor", limit=100)
        reason = text(data, "reason", required=activity in DECISIONS)
        note = text(data, "note", required=False)
        external_id = text(data, "eventId", required=False, limit=100)
        journal_id = f"P4:{incident_id}:ext:{external_id}" if external_id else None
        details = dict(activity=activity, actor=actor, reason=reason, note=note)
        with self.lock:
            if journal_id:
                replay = self._replay_request(incident_id, journal_id, details)
                if replay is not None:
                    return replay
            incident = self._read(incident_id)
            if incident["status"] == "CLOSED":
                raise Conflict("investigation is closed")
            if incident.get("pendingSystemEvents") and incident["status"] == "OPEN":
                incident = self.sync(incident_id)  # bring new evidence into DCR first
            if incident["status"] != "OPEN":
                raise Conflict("DCR case is not open yet; retry POST .../sync first")
            if incident.get("pendingDecision"):
                raise Conflict("a decision is still being applied; retry POST .../sync")
            if incident.get("pendingActivity"):
                raise Conflict("an earlier activity is still being confirmed by DCR; "
                               "retry POST .../sync")
            if activity in ASSESSMENT_GATED and incident.get("pendingSystemEvents"):
                raise Conflict("new evidence is still being added to the DCR case; "
                               "retry POST .../sync")
            # The evidence this request sees. Intake may add newer evidence while DCR runs;
            # that must not be labelled as assessed.
            seen_version = incident["evidenceVersion"]
            case = incident["dcrCaseId"]
            enabled = self._enabled(case)
            if activity not in enabled:
                raise Blocked(activity, enabled)
            if activity in DECISIONS:
                return self._decide(incident, activity, actor, reason, note, journal_id)
            cooling_mode = self._check_cooling_request(incident, activity)
            with self.registry.transaction() as db:
                incident = read_incident(db, incident_id)
                incident["pendingActivity"] = dict(
                    activity=activity, actor=actor, reason=reason, note=note,
                    journalId=journal_id, seenVersion=seen_version, dcrCaseId=case,
                    coolingMode=cooling_mode)
                save_incident(db, incident)
            return self._execute_activity(incident_id, case, activity)

    def _execute_activity(self, incident_id, case, activity, reconciled=False):
        """Execute the recorded pending activity in DCR, then journal it.

        If DCR's answer is lost the pending activity stays (HTTP 503) and sync settles
        it: executing a DCR event twice gives the same marking as once (checked for
        every reachable marking of this graph), so a retry executes it again when DCR
        still enables it and otherwise records it as already executed.
        """
        if not reconciled:
            try:
                self.dcr.execute(case, activity)
            except DCRRefused as error:
                self._clear_activity(incident_id, error)
                raise Blocked(activity, [], str(error)) from error
            except DCRUnavailable as error:
                with self.registry.transaction() as db:
                    incident = read_incident(db, incident_id)
                    incident["lastError"] = f"activity not confirmed by DCR: {error}"
                    save_incident(db, incident)
                raise ActivityPending(str(error)) from error
        with self.registry.transaction() as db:
            incident = read_incident(db, incident_id)
            record = Registry.read(db, incident["shipmentId"])
            pending = incident["pendingActivity"]
            payload = {k: pending[k] for k in ("reason", "note") if pending.get(k)}
            if reconciled:
                payload["reconciledAfterLostResponse"] = True
            if activity == "AssessEvidence":
                seen = pending["seenVersion"]
                newer = (incident["evidenceVersion"] != seen
                         or bool(incident["pendingSystemEvents"]))
                incident["assessedEvidenceVersion"] = seen
                incident["reassessmentRequired"] = newer
                payload["assessedEvidenceVersion"] = seen
                if newer:
                    payload["newerEvidenceArrived"] = True
            input_mode = "manual"
            if activity == "RequestCoolingAdjustment" or activity in INTEGRATION_RESULTS:
                input_mode = self._record_cooling_activity(db, record, incident, activity,
                                                           pending, payload)
            event = journal(db, record, incident, activity, pending["actor"], payload,
                            event_id=pending["journalId"], input_mode=input_mode)
            if activity == "CloseInvestigation":
                incident.update(status="CLOSED", closedAt=event["occurredAt"])
            incident.update(pendingActivity=None, lastError=None)
            save_incident(db, incident)
        return incident, event

    def _finish_activity(self, incident_id):
        incident = self._read(incident_id)
        pending = incident["pendingActivity"]
        case, activity = pending["dcrCaseId"], pending["activity"]
        try:
            enabled = self._enabled(case)
        except DCRUnavailable as error:
            raise ActivityPending(str(error)) from error
        return self._execute_activity(incident_id, case, activity,
                                      reconciled=activity not in enabled)

    def _clear_activity(self, incident_id, error):
        with self.registry.transaction() as db:
            incident = read_incident(db, incident_id)
            incident.update(pendingActivity=None, lastError=str(error))
            save_incident(db, incident)

    def _replay_request(self, incident_id, journal_id, details):
        """Idempotency for officer requests that carry an eventId.

        The first request's activity, actor, reason and note are stored durably. Reusing
        the eventId with different details is a conflict (409); an identical retry gets
        the original outcome, or finishes a decision that is still being applied.
        """
        key = f"p4-activity:{journal_id}"
        with self.registry.transaction() as db:
            incident = read_incident(db, incident_id)
            row = db.execute("SELECT data FROM results WHERE id=?", (key,)).fetchone()
            if row is None:
                db.execute("INSERT INTO results VALUES (?,?)", (key, json.dumps(details)))
                return None
            if json.loads(row[0]) != details:
                raise Conflict("eventId reused with a different activity, actor, reason "
                               "or note")
            saved = db.execute("SELECT data FROM events WHERE id=?", (journal_id,)).fetchone()
        if (incident.get("pendingDecision") or {}).get("journalId") == journal_id:
            return self._finish_decision(incident_id)  # may already be journalled
        if saved:
            return incident, json.loads(saved[0])
        if (incident.get("pendingActivity") or {}).get("journalId") == journal_id:
            return self._finish_activity(incident_id)
        refused = [d for d in incident["unappliedDecisions"] if d.get("journalId") == journal_id]
        if refused:
            raise StaleDecision(f"this decision was not applied: {refused[-1]['refusal']}",
                                refused[-1].get("currentEvidenceVersion"))
        return None  # accepted before but never executed (e.g. blocked): try again

    def _decide(self, incident, activity, actor, reason, note, journal_id):
        incident_id = incident["incidentId"]
        if incident.get("reassessmentRequired") or (
                incident.get("evidenceVersion") != incident.get("assessedEvidenceVersion")):
            raise StaleDecision("the current evidence has not been assessed; execute "
                                "AssessEvidence first", incident.get("evidenceVersion"))
        decision, status = DECISIONS[activity]
        attempt = len(incident["unappliedDecisions"]) + 1
        with self.registry.transaction() as db:
            incident = read_incident(db, incident_id)
            incident["pendingDecision"] = dict(
                activity=activity, decision=decision, qualityStatus=status, officer=actor,
                reason=reason, note=note, occurredAt=now(), journalId=journal_id,
                eventId=f"P4:{incident_id}:{decision}:{attempt}",
                expectedEvidenceVersion=incident["assessedEvidenceVersion"],
                dcrCaseId=incident["dcrCaseId"], dcrAttempted=False, dcrExecuted=False)
            save_incident(db, incident)
        return self._finish_decision(incident_id)

    # -- views --------------------------------------------------------------
    def view(self, incident_id):
        with self.registry.transaction() as db:
            incident = read_incident(db, incident_id)
            record = Registry.read(db, incident["shipmentId"])
        dcr = None
        if incident.get("dcrCaseId") and incident["status"] != "CLOSED":
            try:
                dcr = self.dcr.events(incident["dcrCaseId"])
            except DCRUnavailable as error:
                dcr = {"unavailable": str(error)}
        current = record.get("evidenceVersion") if record.get(
            "qualityIncidentId") == incident_id else None
        return dict(incident, dcr=dcr, registry=dict(
            qualityStatus=record.get("qualityStatus", "CLEAR"),
            qualityIncidentId=record.get("qualityIncidentId"),
            evidenceVersion=current,
            reassessmentNeeded=current is not None
            and incident.get("assessedEvidenceVersion") is not None
            and current != incident["assessedEvidenceVersion"]
            and not incident.get("decision")))

    def for_shipment(self, shipment_id):
        with self.registry.transaction() as db:
            record = Registry.read(db, shipment_id)
            ensure_schema(db)
            incidents = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT data FROM incidents WHERE shipment_id=? ORDER BY rowid",
                    (shipment_id,),
                )
            ]
        return dict(
            shipmentId=shipment_id,
            qualityStatus=record.get("qualityStatus", "CLEAR"),
            qualityIncidentId=record.get("qualityIncidentId"),
            evidenceVersion=record.get("evidenceVersion"),
            incidents=incidents,
        )


def create_blueprint(service):
    bp = Blueprint("quality", __name__)

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("send a JSON object")
        return data

    @bp.errorhandler(UnknownIncident)
    def unknown(error):
        return jsonify(error="unknown incident"), 404

    @bp.errorhandler(Blocked)
    def blocked(error):
        return jsonify(error=str(error), enabled=error.enabled), 409

    @bp.errorhandler(StaleDecision)
    def stale(error):
        return jsonify(error=str(error), reassessmentRequired=True,
                       currentEvidenceVersion=error.current_version), 409

    @bp.errorhandler(DecisionPending)
    def pending(error):
        what = "activity" if isinstance(error, ActivityPending) else "decision"
        return jsonify(error=f"{what} recorded, not confirmed yet: {error}; "
                       "it is retried automatically"), 503

    @bp.errorhandler(DCRUnavailable)
    def unavailable(error):
        return jsonify(error=str(error)), 503

    @bp.post("/integrations/p4/incident")
    def handoff():
        """Foundation outbox consumer; internal, unauthenticated demo route."""
        return jsonify(service.accept_handoff(
            body(), request.headers.get("Idempotency-Key"))), 200

    @bp.post("/integrations/p4/cooling-result")
    def cooling_result():
        """Simulator result for a P4 cooling command (docs/P4-COOLING-CONTRACT.md)."""
        return jsonify(service.accept_cooling_result(
            body(), request.headers.get("Idempotency-Key"))), 200

    @bp.post("/integrations/p4/recovery")
    def recovery():
        """Siddhi recovery evaluation for a P4 cooling command."""
        return jsonify(service.accept_recovery(
            body(), request.headers.get("Idempotency-Key"))), 200

    @bp.get("/quality/incidents/<incident_id>")
    def incident(incident_id):
        return jsonify(service.view(incident_id))

    @bp.post("/quality/incidents/<incident_id>/sync")
    def sync(incident_id):
        return jsonify(service.sync(incident_id))

    @bp.post("/quality/incidents/<incident_id>/activities/<activity>")
    def activity(incident_id, activity):
        incident, event = service.execute(incident_id, activity, body())
        return jsonify(incident=incident, event=event), 200

    @bp.get("/shipments/<shipment_id>/quality")
    def shipment_quality(shipment_id):
        return jsonify(service.for_shipment(shipment_id))

    return bp
