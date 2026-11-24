"""P4 side of the cooling-command / acknowledgement / recovery loop (issue #17).

Contract: docs/P4-COOLING-CONTRACT.md (draft v1, to be agreed with the simulator/Siddhi
owner). This module is mixed into quality.QualityService and only touches P4 state:

- RequestCoolingAdjustment (officer, DCR) creates one durable cooling command in the same
  transaction that journals the DCR activity. With P4_COOLING_COMMAND_URL set the command
  is queued in the registry outbox (destination "p4-cooling-command") and delivered to the
  Siddhi command bridge with Idempotency-Key = commandId. Without it the command is a
  labelled manual-test command and the officer enters the result by hand as before.
- The simulator's result (APPLIED/FAILED) arrives at POST /integrations/p4/cooling-result.
  It is persisted before P4 answers and executes RecordCoolingAcknowledgement or
  RecordCoolingFailure in DCR through the same durable system-event queue as alerts.
  Sending a command is never treated as acknowledgement of application.
- A command without a result by its expiresAt is failed by P4 (acknowledgement-timeout);
  an undelivered outbox message is cancelled. A later result is kept as late evidence.
- Siddhi's recovery evaluation arrives at POST /integrations/p4/recovery. P4 re-checks it
  against the stored container readings (session, after the matching application,
  distinct consecutive sequences, threshold, gaps, span, freshness, snapshotted criteria)
  before executing RecordRecoveryEvidence. Recovery never changes quality status: the
  hold stays until a valid officer decision, and DCR makes a new assessment due.
"""

import json
import math
import os
from datetime import datetime, timedelta, timezone

import registry as registry_module
from registry import Conflict, Registry

COMMAND_DESTINATION = "p4-cooling-command"
INTEGRATION_RESULTS = {"RecordCoolingAcknowledgement", "RecordCoolingFailure",
                       "RecordRecoveryEvidence"}
OUTSTANDING = ("REQUESTED", "DELIVERED")
RESULT_STATUSES = ("APPLIED", "FAILED")
RECOVERY_OUTCOMES = ("RECOVERED", "NOT_RECOVERED")
MIN_RECOVERY_READINGS = 3
CRITERIA_KEYS = ("profileId", "thresholdC", "minReadings", "minSpanSeconds",
                 "maxGapSeconds", "freshSeconds")


def _now():
    return registry_module.now()  # patched by the offline tests' clock


def _seconds(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _iso(moment):
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def ack_timeout_seconds(environ=None):
    env = os.environ if environ is None else environ
    value = float(env.get("P4_COOLING_ACK_TIMEOUT_SECONDS", "45"))
    if not math.isfinite(value) or value <= 0:
        raise ValueError("P4_COOLING_ACK_TIMEOUT_SECONDS must be positive")
    return value


def command_mode(environ=None):
    env = os.environ if environ is None else environ
    return "integration" if env.get("P4_COOLING_COMMAND_URL") else "manual-test"


def _text(data, field, limit=1000, required=True):
    value = data.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must be a non-empty string of at most {limit} characters")
    return value


def _utc(data, field):
    value = data.get(field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise ValueError(f"{field} must be an ISO 8601 UTC timestamp") from error
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field} must be a UTC timestamp")
    return value


def _envelope(data, id_field, idempotency_key, event_type):
    event = data.get("event") if isinstance(data, dict) else None
    if not isinstance(event, dict):
        raise ValueError("send the envelope {\"event\": {...}}")
    if event.get("schemaVersion") != 1:
        raise ValueError("event.schemaVersion must be 1")
    if event.get("eventType") != event_type:
        raise ValueError(f"eventType must be {event_type}")
    identity = _text(event, id_field)
    if idempotency_key != identity:
        raise ValueError(f"Idempotency-Key must match event.{id_field}")
    for field in ("commandId", "incidentId", "shipmentId", "journeyId", "sessionId",
                  "source", "inputMode"):
        _text(event, field)
    _utc(event, "occurredAt")
    return event


def recovery_criteria(record):
    """Demo recovery criteria snapshotted on the command from the shipment's profile."""
    settings = record["monitoringProfile"]
    return dict(profileId=settings["profileId"], thresholdC=settings["thresholdC"],
                minReadings=MIN_RECOVERY_READINGS, minSpanSeconds=settings["spanSeconds"],
                maxGapSeconds=settings["maxGapSeconds"], freshSeconds=settings["freshSeconds"])


def current_command(incident):
    commands = (incident.get("cooling") or {}).get("commands") or []
    return commands[-1] if commands else None


def find_command(incident, command_id):
    for command in (incident.get("cooling") or {}).get("commands") or []:
        if command["commandId"] == command_id:
            return command
    return None


class CoolingMixin:
    """Mixed into QualityService; uses its lock, sync queue and journal helpers."""

    # -- officer request ----------------------------------------------------
    def _check_cooling_request(self, incident, activity):
        """Refusals before DCR executes anything (HTTP 409)."""
        command = current_command(incident)
        if activity == "RequestCoolingAdjustment":
            if command and command["status"] in OUTSTANDING:
                raise Conflict(f"cooling command {command['commandId']} is still waiting "
                               "for its simulator result")
            if command_mode() == "integration":
                record = self.registry.get(incident["shipmentId"])
                if "monitoringProfile" not in record:
                    raise Conflict("cooling commands need a monitored shipment")
                if (record.get("monitoring") or {}).get("state") != "ACTIVE":
                    raise Conflict("cooling is only possible while the shipment's "
                                   "container-monitoring session is active (before "
                                   "physical receipt)")
            return command_mode()
        if activity in INTEGRATION_RESULTS and command and command["mode"] == "integration":
            raise Conflict(f"{activity} is recorded from the simulator/Siddhi result for "
                           f"{command['commandId']}; manual entry is refused")
        return None

    def _record_cooling_activity(self, db, record, incident, activity, pending, payload):
        """Called inside the journal transaction of an officer-executed activity."""
        cooling = incident.setdefault("cooling", {"commands": []})
        command = current_command(incident)
        at = _now()
        if activity == "RequestCoolingAdjustment":
            mode = pending.get("coolingMode") or "manual-test"
            command_id = f"P4:COOL:{incident['incidentId']}:{len(cooling['commands']) + 1}"
            session = record.get("monitoring") or {}
            command = dict(
                commandId=command_id, mode=mode, status="REQUESTED",
                requestedAt=at, requestedBy=pending["actor"],
                expiresAt=_iso(datetime.fromisoformat(at.replace("Z", "+00:00"))
                               + timedelta(seconds=ack_timeout_seconds()))
                if mode == "integration" else None,
                evidenceVersion=pending.get("seenVersion"), dcrCaseId=incident["dcrCaseId"],
                sessionId=session.get("sessionId"),
                recoveryCriteria=recovery_criteria(record)
                if "monitoringProfile" in record else None,
                result=None, recovery=None, lateEvidence=[])
            cooling["commands"].append(command)
            payload["commandId"] = command_id
            payload["commandMode"] = mode
            if mode == "integration":
                message = dict(
                    schemaVersion=1, eventType="CoolingAdjustmentRequested",
                    commandId=command_id, command="AdjustCooling",
                    shipmentId=record["shipmentId"], journeyId=record["journeyId"],
                    sessionId=command["sessionId"], incidentId=incident["incidentId"],
                    productId=record["productId"], batchId=record["batchId"],
                    targetC=record["monitoringProfile"]["normalC"],
                    evidenceVersion=command["evidenceVersion"],
                    requestedBy=pending["actor"], requestedAt=at,
                    expiresAt=command["expiresAt"],
                    recoveryCriteria=command["recoveryCriteria"],
                    source="p4-quality-investigation", inputMode="manual")
                Registry.enqueue(db, command_id, "CoolingAdjustmentRequested",
                                 record["shipmentId"], {"event": message},
                                 needs_ack=True, destination=COMMAND_DESTINATION)
            return "manual"
        if activity in INTEGRATION_RESULTS:
            # Manual-test stand-in (no integrated command): labelled as such.
            payload["manualTestInput"] = True
            if command is not None:
                payload["commandId"] = command["commandId"]
                if activity == "RecordCoolingAcknowledgement":
                    command.update(status="APPLIED", result=dict(
                        status="APPLIED", appliedAt=at, source="manual-test",
                        inputMode="manual-test"))
                elif activity == "RecordCoolingFailure":
                    command.update(status="FAILED", result=dict(
                        status="FAILED", failureReason="manual-test", source="manual-test",
                        inputMode="manual-test"))
                else:
                    command["recovery"] = dict(outcome="RECOVERED", source="manual-test",
                                               inputMode="manual-test", recordedAt=at)
            return "manual-test"
        return "manual"

    # -- command delivery (outbox loop in app.py) ---------------------------
    def cooling_command_delivered(self, command_id, response):
        """The bridge answered; this is receipt of the command, not its application."""
        if (not isinstance(response, dict) or response.get("commandId") != command_id
                or response.get("status") not in ("received", "duplicate")):
            raise ValueError("cooling command receiver must answer "
                             "{commandId, status: received|duplicate}")
        with self.registry.transaction() as db:
            incident = self._incident_for_command(db, command_id)
            command = find_command(incident, command_id)
            if command["status"] == "REQUESTED":
                command.update(status="DELIVERED", deliveredAt=_now())
                from quality import save_incident
                save_incident(db, incident)

    def cooling_command_refused(self, command_id, error):
        """A definite refusal (HTTP 4xx) of the command: record the failure."""
        incident_id = None
        with self.registry.transaction() as db:
            incident = self._incident_for_command(db, command_id)
            incident_id = incident["incidentId"]
            self._fail_command(db, incident, command_id, "command-refused", str(error)[:500],
                               "p4-cooling-delivery")
            self._close_outbox(db, command_id, "REFUSED", str(error)[:500])
        self.sync(incident_id)

    @staticmethod
    def _incident_for_command(db, command_id):
        from quality import ensure_schema
        ensure_schema(db)
        if not command_id.startswith("P4:COOL:") or command_id.count(":") < 3:
            raise Conflict("unknown cooling command")
        incident_id = command_id[len("P4:COOL:"):].rsplit(":", 1)[0]
        row = db.execute("SELECT data FROM incidents WHERE id=?", (incident_id,)).fetchone()
        incident = json.loads(row[0]) if row else None
        if incident is None or find_command(incident, command_id) is None:
            raise Conflict("unknown cooling command")
        return incident

    @staticmethod
    def _close_outbox(db, command_id, status, error=None):
        row = db.execute("SELECT data FROM outbox WHERE id=?", (command_id,)).fetchone()
        if row:
            data = json.loads(row[0])
            if data["status"] == "PENDING":
                data.update(status=status, lastError=error)
                db.execute("UPDATE outbox SET data=? WHERE id=?", (json.dumps(data), command_id))

    def _fail_command(self, db, incident, command_id, reason, detail, source):
        from quality import save_incident
        command = find_command(incident, command_id)
        if command["status"] not in OUTSTANDING:
            return False
        record = Registry.read(db, incident["shipmentId"])
        result = dict(status="FAILED", failureReason=reason, detail=detail, source=source,
                      inputMode="system", occurredAt=_now())
        command.update(status="FAILED", result=result)
        self._queue_result(db, record, incident, command, "RecordCoolingFailure", result,
                           f"P4:{incident['incidentId']}:cooling:{command_id}:{reason}")
        save_incident(db, incident)
        return True

    @staticmethod
    def _queue_result(db, record, incident, command, activity, result, journal_id):
        """Queue the DCR activity, or keep the result as late evidence when the command's
        DCR case was superseded (new case after a refused decision) or closed."""
        from quality import journal
        late = "LateRecoveryEvidenceRecorded" if activity == "RecordRecoveryEvidence" \
            else "LateCoolingResultRecorded"
        if command.get("dcrCaseId") != incident.get("dcrCaseId") or incident["status"] == "CLOSED":
            command["lateEvidence"].append(dict(activity=activity, result=result,
                                                reason="DCR case superseded or closed"))
            journal(db, record, incident, late, event_id=journal_id,
                    payload=dict(commandId=command["commandId"], result=result),
                    input_mode=result.get("inputMode", "system"), source=result.get("source"))
            return False
        incident["pendingSystemEvents"].append(dict(
            activity=activity, journalId=journal_id, late=late,
            payload=dict(commandId=command["commandId"], result=result),
            inputMode=result.get("inputMode", "system"), source=result.get("source")))
        return True

    def expire_cooling_commands(self):
        """Fail integrated commands whose result did not arrive by expiresAt."""
        from quality import ensure_schema
        expired = []
        with self.registry.transaction() as db:
            ensure_schema(db)
            rows = [json.loads(r[0]) for r in db.execute("SELECT data FROM incidents")]
        for incident in rows:
            command = current_command(incident)
            if (command and command["mode"] == "integration" and command["status"] in OUTSTANDING
                    and _seconds(_now()) >= _seconds(command["expiresAt"])):
                expired.append((incident["incidentId"], command["commandId"]))
        for incident_id, command_id in expired:
            with self.registry.transaction() as db:
                from quality import read_incident
                incident = read_incident(db, incident_id)
                if self._fail_command(db, incident, command_id, "acknowledgement-timeout",
                                      "no simulator result before expiresAt", "p4-timeout"):
                    self._close_outbox(db, command_id, "CANCELLED",
                                       "expired before delivery; not sent")
            self.sync(incident_id)
        return [c for _, c in expired]

    # -- simulator result ---------------------------------------------------
    def accept_cooling_result(self, data, idempotency_key):
        from quality import ensure_schema, journal, save_incident
        event = _envelope(data, "resultId", idempotency_key, "CoolingCommandResult")
        status = event.get("status")
        if status not in RESULT_STATUSES:
            raise ValueError(f"status must be one of {RESULT_STATUSES}")
        if status == "APPLIED":
            _utc(event, "appliedAt")
            if type(event.get("effectiveFromSequence")) is not int or event["effectiveFromSequence"] < 1:
                raise ValueError("APPLIED needs effectiveFromSequence (first reading sequence "
                                 "produced under the command)")
        else:
            _text(event, "failureReason", limit=200)
        key = f"p4-cooling-result:{event['resultId']}"
        with self.registry.transaction() as db:
            ensure_schema(db)
            saved = db.execute("SELECT data FROM results WHERE id=?", (key,)).fetchone()
            if saved:
                saved = json.loads(saved[0])
                if saved["request"] != data:
                    raise Conflict("resultId reused with different content")
                return dict(resultId=event["resultId"], status="duplicate", effect=saved["effect"])
            incident = self._matching_incident(db, event)
            command = find_command(incident, event["commandId"])
            record = Registry.read(db, incident["shipmentId"])
            result = {k: event.get(k) for k in (
                "resultId", "status", "appliedAt", "effectiveFromSequence", "failureReason",
                "detail", "occurredAt", "source", "inputMode") if event.get(k) is not None}
            result["recordedAt"] = _now()
            journal_id = f"P4:{incident['incidentId']}:cooling-result:{event['resultId']}"
            if command["mode"] == "integration" and command["status"] in OUTSTANDING:
                command.update(status=status, result=result)
                # The simulator answered: a delivery still pending (e.g. its receipt answer
                # was lost) must not be resent.
                self._close_outbox(db, command["commandId"], "RESOLVED",
                                   f"simulator result {event['resultId']} received")
                activity = "RecordCoolingAcknowledgement" if status == "APPLIED" \
                    else "RecordCoolingFailure"
                queued = self._queue_result(db, record, incident, command, activity, result,
                                            journal_id)
                effect = ("acknowledgement-queued" if status == "APPLIED" else "failure-queued") \
                    if queued else "late-result-recorded"
            else:
                command["lateEvidence"].append(dict(result=result, reason=(
                    f"command already {command['status']}" if command["mode"] == "integration"
                    else "manual-test command")))
                journal(db, record, incident, "LateCoolingResultRecorded",
                        payload=dict(commandId=command["commandId"], result=result,
                                     commandStatus=command["status"]),
                        event_id=journal_id, occurred_at=event["occurredAt"],
                        input_mode=event["inputMode"], source=event["source"])
                effect = "late-result-recorded"
            save_incident(db, incident)
            db.execute("INSERT INTO results VALUES (?,?)", (key, json.dumps(dict(
                request=data, incidentId=incident["incidentId"], effect=effect))))
        self.sync(incident["incidentId"])
        return dict(resultId=event["resultId"], status="accepted", effect=effect)

    @staticmethod
    def _matching_incident(db, event):
        from quality import read_incident
        incident = read_incident(db, event["incidentId"])  # UnknownIncident -> 404
        command = find_command(incident, event["commandId"])
        if command is None:
            raise Conflict("unknown cooling command for this incident")
        record = Registry.read(db, incident["shipmentId"])
        expected = dict(shipmentId=incident["shipmentId"], journeyId=record["journeyId"],
                        sessionId=command["sessionId"])
        wrong = [k for k, v in expected.items() if event[k] != v]
        if wrong:
            raise Conflict(f"{', '.join(wrong)} do not match the cooling command")
        return incident

    # -- Siddhi recovery evaluation -----------------------------------------
    def accept_recovery(self, data, idempotency_key):
        from quality import ensure_schema, journal, save_incident
        event = _envelope(data, "recoveryId", idempotency_key, "RecoveryEvaluated")
        if event.get("outcome") not in RECOVERY_OUTCOMES:
            raise ValueError(f"outcome must be one of {RECOVERY_OUTCOMES}")
        if not isinstance(event.get("criteria"), dict):
            raise ValueError("criteria must echo the command's recoveryCriteria")
        if event["outcome"] == "RECOVERED":
            for field in ("firstSequence", "lastSequence", "readingCount"):
                if type(event.get(field)) is not int or event[field] < 1:
                    raise ValueError(f"{field} must be a positive integer")
            _utc(event, "qualifiedAt")
        key = f"p4-recovery:{event['recoveryId']}"
        with self.registry.transaction() as db:
            ensure_schema(db)
            saved = db.execute("SELECT data FROM results WHERE id=?", (key,)).fetchone()
            if saved:
                saved = json.loads(saved[0])
                if saved["request"] != data:
                    raise Conflict("recoveryId reused with different content")
                return dict(recoveryId=event["recoveryId"], status="duplicate",
                            effect=saved["effect"])
            incident = self._matching_incident(db, event)
            command = find_command(incident, event["commandId"])
            record = Registry.read(db, incident["shipmentId"])
            journal_id = f"P4:{incident['incidentId']}:recovery:{event['recoveryId']}"
            evidence = {k: event.get(k) for k in (
                "recoveryId", "outcome", "firstSequence", "lastSequence", "readingCount",
                "qualifiedAt", "criteria", "reason", "occurredAt", "source", "inputMode")
                if event.get(k) is not None}
            evidence["recordedAt"] = _now()
            refusal = self._recovery_refusal(db, record, command, event)
            logged = dict(commandId=command["commandId"], recovery=evidence)
            if refusal is not None:
                activity, effect = "RecoveryEvidenceRefused", "recovery-refused"
            elif event["outcome"] == "NOT_RECOVERED":
                activity, effect = "RecoveryNotQualified", "not-recovered-recorded"
            elif command.get("recovery"):
                activity, effect = "LateRecoveryEvidenceRecorded", "evidence-recorded"
                logged["note"] = "recovery for this command is already recorded"
            else:
                activity, effect = None, "recovery-queued"
            if activity is None:
                command["recovery"] = evidence
                if not self._queue_result(db, record, incident, command,
                                          "RecordRecoveryEvidence", evidence, journal_id):
                    effect = "evidence-recorded"
            else:
                command["lateEvidence"].append(dict(recovery=evidence, refusal=refusal))
                journal(db, record, incident, activity,
                        payload=dict(logged, refusal=refusal) if refusal else logged,
                        event_id=journal_id, occurred_at=event["occurredAt"],
                        input_mode=event["inputMode"], source=event["source"])
            save_incident(db, incident)
            db.execute("INSERT INTO results VALUES (?,?)", (key, json.dumps(dict(
                request=data, incidentId=incident["incidentId"], effect=effect,
                refusal=refusal))))
        self.sync(incident["incidentId"])
        answer = dict(recoveryId=event["recoveryId"], status="accepted", effect=effect)
        if refusal:
            answer["refusal"] = refusal
        return answer

    @staticmethod
    def _recovery_refusal(db, record, command, event):
        """Why this evaluation cannot count as recovery for the command (None = valid)."""
        if command["mode"] != "integration":
            return "manual-test command; recovery is entered manually"
        if command["status"] != "APPLIED":
            return f"command is {command['status']}, not APPLIED: no recovery without a " \
                   "matching application acknowledgement"
        if event["criteria"] != command["recoveryCriteria"]:
            return "criteria do not match the snapshot on the command"
        if event["outcome"] != "RECOVERED":
            return None
        criteria, applied = command["recoveryCriteria"], command["result"]
        first, last, count = event["firstSequence"], event["lastSequence"], event["readingCount"]
        if first < applied["effectiveFromSequence"]:
            return "readings start before the command was applied"
        if last - first + 1 != count or count < criteria["minReadings"]:
            return f"need at least {criteria['minReadings']} distinct consecutive readings"
        rows = db.execute(
            "SELECT sequence,data FROM container_observations WHERE shipment_id=? "
            "AND sequence BETWEEN ? AND ? ORDER BY sequence",
            (record["shipmentId"], first, last)).fetchall()
        if [s for s, _ in rows] != list(range(first, last + 1)):
            return "readings are missing from the stored session"
        readings = [json.loads(raw)["observation"] for _, raw in rows]
        prefix = f"{command['sessionId']}:"
        if any(not o["eventId"].startswith(prefix) or o["journeyId"] != record["journeyId"]
               for o in readings):
            return "readings belong to another monitoring session"
        times = [_seconds(o["occurredAt"]) for o in readings]
        if times[0] < _seconds(applied["appliedAt"]):
            return "readings are older than the application acknowledgement"
        if any(o["celsius"] > criteria["thresholdC"] for o in readings):
            return "a reading is above the threshold"
        if any(not 0 < b - a <= criteria["maxGapSeconds"] for a, b in zip(times, times[1:])):
            return "readings have a gap larger than maxGapSeconds"
        if times[-1] - times[0] < criteria["minSpanSeconds"]:
            return "readings do not span minSpanSeconds"
        qualified = _seconds(event["qualifiedAt"])
        if not 0 <= qualified - times[-1] <= criteria["freshSeconds"]:
            return "evaluation is not fresh relative to its last reading"
        if qualified > _seconds(_now()) + 1:
            return "qualifiedAt is in the future"
        return None
