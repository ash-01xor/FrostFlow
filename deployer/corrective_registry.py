"""P5 durable effects. Zeebe owns the parallel collection/replacement workflow."""

import hashlib
import json


class CorrectiveRegistry:
    @staticmethod
    def read_corrective(db, corrective_id):
        row = db.execute("SELECT data FROM corrective_cases WHERE id=?", (corrective_id,)).fetchone()
        if row is None:
            raise KeyError(corrective_id)
        return json.loads(row[0])

    @staticmethod
    def save_corrective(db, case):
        db.execute("INSERT INTO corrective_cases VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                   (case["correctiveId"], case["incidentId"], json.dumps(case)))

    def corrective_cases(self, shipment_id=None):
        with self.transaction() as db:
            cases = [json.loads(row[0]) for row in db.execute("SELECT data FROM corrective_cases ORDER BY rowid")]
            return [c for c in cases if shipment_id is None or c["originalShipmentId"] == shipment_id]

    def get_corrective(self, corrective_id):
        with self.transaction() as db:
            return self.read_corrective(db, corrective_id)

    def corrective_journal(self, db, case, activity, actor="system", payload=None, input_event=None):
        record = self.read(db, case["originalShipmentId"])
        event = self.event(
            db, record, activity, actor=actor, process="P5",
            event_id=f"P5:{case['correctiveId']}:{activity}",
            payload={"correctiveId": case["correctiveId"], "incidentId": case["incidentId"],
                     "quantity": record["quantity"], "recipientId": record["recipientId"],
                     "action": case["action"], **(payload or {})},
            occurred_at=input_event["occurredAt"] if input_event else None,
            input_mode="manual" if input_event else "system", source="corrective-fulfilment",
            producer_event_id=input_event["eventId"] if input_event else None,
        )
        event.update(correctiveId=case["correctiveId"], incidentId=case["incidentId"],
                     processInstanceKey=case["processInstanceKey"])
        db.execute("UPDATE events SET data=? WHERE id=?", (json.dumps(event), event["eventId"]))
        return event

    def authorize_corrective(self, db, record, incident, disposition):
        """Called only in P4's applied-disposition transaction, including recovery."""
        from registry import Conflict, now

        decision = incident.get("decision") or {}
        action = decision.get("decision")
        if action not in ("REPLACE", "RETURN"):
            return None
        if (record.get("qualityStatus") != "REJECTED"
                or record.get("qualityIncidentId") != incident["incidentId"]
                or decision.get("qualityStatus") != "REJECTED"
                or disposition["payload"].get("decision") != action):
            raise Conflict("P5 requires an applied P4 replacement/return disposition")
        corrective_id = "CF-" + hashlib.sha256(incident["incidentId"].encode()).hexdigest()[:24]
        existing = db.execute("SELECT data FROM corrective_cases WHERE id=?", (corrective_id,)).fetchone()
        if existing:
            case = json.loads(existing[0])
            if case["authorization"] != disposition:
                raise Conflict("incident already authorizes different corrective work")
            return case
        case = dict(
            correctiveId=corrective_id, incidentId=incident["incidentId"],
            originalShipmentId=record["shipmentId"], action=action,
            status="START_REQUESTED", processInstanceKey=None, createdAt=now(),
            authorization=disposition, inputs={}, arrangement=None,
            originalGoodsStatus="AWAITING_COLLECTION", replacementShipmentId=None,
            replacementOutcome="AWAITING_ARRANGEMENT" if action == "REPLACE" else "NOT_REQUIRED",
            completedAt=None,
        )
        self.save_corrective(db, case)
        record["correctiveId"] = corrective_id
        self.save(db, record)
        self.enqueue(db, f"P5:{corrective_id}:DispositionRecorded", "P5DispositionRecorded", corrective_id,
                     {"correctiveId": corrective_id, "authorizationEvent": disposition})
        return case

    @staticmethod
    def require_corrective_instance(case, key):
        from registry import Conflict

        if case["processInstanceKey"] != str(key):
            raise Conflict("wrong P5 engine instance")

    def register_corrective(self, corrective_id, key, authorization):
        from registry import Conflict

        with self.transaction() as db:
            case = self.read_corrective(db, corrective_id)
            if case["authorization"] != authorization:
                raise Conflict("authorization does not match the applied P4 disposition")
            if case["processInstanceKey"] not in (None, str(key)):
                raise Conflict("corrective case belongs to a different P5 engine instance")
            if case["processInstanceKey"] is None:
                case.update(processInstanceKey=str(key), status="AWAITING_ARRANGEMENT")
                self.corrective_journal(db, case, "CorrectiveWorkAuthorized",
                                        payload={"dispositionEventId": authorization["eventId"]})
                self.save_corrective(db, case)
            return {"correctiveAction": case["action"]}

    def queue_corrective_input(self, corrective_id, kind, event_id, occurred_at, payload):
        """Reserve each manual action once; exact retries remain valid after completion."""
        from registry import Conflict, now
        from monitoring import seconds

        if kind not in ("ArrangementRequested", "ReturnCollected"):
            raise ValueError("unsupported P5 input")
        with self.transaction() as db:
            case = self.read_corrective(db, corrective_id)
            for previous_kind, previous in case["inputs"].items():
                event = previous["event"]
                if previous_kind == kind or event["eventId"] == event_id:
                    if (previous_kind != kind or event["eventId"] != event_id
                            or event["occurredAt"] != occurred_at or event["payload"] != payload):
                        raise Conflict("P5 action/eventId already accepted with different data")
                    return previous
            original = self.read(db, case["originalShipmentId"])
            if (original.get("qualityStatus") != "REJECTED"
                    or original.get("qualityIncidentId") != case["incidentId"]):
                raise Conflict("original goods must retain their authorized rejection")
            if kind == "ArrangementRequested":
                if case["status"] != "AWAITING_ARRANGEMENT":
                    raise Conflict("arrangement requires AWAITING_ARRANGEMENT")
                if seconds(occurred_at) < seconds(case["authorization"]["occurredAt"]):
                    raise Conflict("arrangement cannot precede the P4 authorization")
                if case["action"] == "REPLACE":
                    replacement = payload.get("replacement")
                    if not isinstance(replacement, dict):
                        raise ValueError("replacement details are required for REPLACE")
                    data = {**replacement, **{k: original[k] for k in (
                        "productId", "batchId", "quantity", "recipientId")},
                        "originalShipmentId": original["shipmentId"], "incidentId": case["incidentId"],
                        "replacementForCorrectiveId": corrective_id}
                    # Reserve vehicle and child identity atomically with acceptance: a
                    # later competing order cannot strand the arrangement worker.
                    self.create_in_transaction(db, data)
                    case.update(replacementShipmentId=data["shipmentId"], replacementOutcome="PREPARING")
                elif "replacement" in payload:
                    raise ValueError("RETURN does not create replacement goods")
                case["status"] = "ARRANGEMENT_REQUESTED"
            else:
                if case["status"] != "IN_PROGRESS" or not case["arrangement"]:
                    raise Conflict("collection requires a processed arrangement")
                booking = case["arrangement"]["payload"]
                if (payload["bookingReference"] != booking["bookingReference"]
                        or payload["collector"] != booking["collector"]
                        or payload["quantityCollected"] != original["quantity"]):
                    raise Conflict("collection must match the booking, collector and full original quantity")
                if original.get("p3Status") != "REJECTED" or original.get("receiptStatus") != "RECEIVED":
                    raise Conflict("original goods must be physically received and rejected by P3")
                if seconds(occurred_at) < max(seconds(case["arrangement"]["occurredAt"]), seconds(original["receivedAt"])):
                    raise Conflict("collection cannot precede booking or physical receipt")
            event = dict(eventId=event_id, eventType=kind, correctiveId=corrective_id,
                         shipmentId=original["shipmentId"], journeyId=original["journeyId"],
                         incidentId=case["incidentId"], occurredAt=occurred_at, recordedAt=now(),
                         source="corrective-fulfilment", inputMode="manual", payload=payload)
            message_id = f"P5:{corrective_id}:{kind}"
            accepted = {"messageId": message_id, "event": event}
            case["inputs"][kind] = accepted
            self.enqueue(db, message_id, "P5" + kind, corrective_id,
                         {"correctiveId": corrective_id, "correctiveInput": event})
            self.save_corrective(db, case)
            return accepted

    @staticmethod
    def require_corrective_input(case, kind, event):
        from registry import Conflict

        if (case["inputs"].get(kind) or {}).get("event") != event:
            raise Conflict("P5 input was not durably accepted")

    def arrange_corrective(self, corrective_id, key, event):
        from registry import Conflict

        with self.transaction() as db:
            case = self.read_corrective(db, corrective_id)
            self.require_corrective_instance(case, key)
            self.require_corrective_input(case, "ArrangementRequested", event)
            if case["arrangement"] is None:
                if case["status"] != "ARRANGEMENT_REQUESTED":
                    raise Conflict("arrangement was not requested")
                case.update(arrangement=event, status="IN_PROGRESS")
                self.corrective_journal(db, case, "CollectionBooked", event["payload"]["actor"],
                                        event["payload"], event)
                if case["action"] == "REPLACE":
                    self.corrective_journal(db, case, "ReplacementRequested", event["payload"]["actor"],
                                            {"replacementShipmentId": case["replacementShipmentId"]})
                self.save_corrective(db, case)
            return {"correctiveAction": case["action"], "replacementShipmentId": case["replacementShipmentId"]}

    def record_corrective_collection(self, corrective_id, key, event):
        with self.transaction() as db:
            case = self.read_corrective(db, corrective_id)
            self.require_corrective_instance(case, key)
            self.require_corrective_input(case, "ReturnCollected", event)
            if case["originalGoodsStatus"] != "COLLECTED":
                case.update(originalGoodsStatus="COLLECTED", collectedAt=event["occurredAt"])
                self.corrective_journal(db, case, "ReturnCollected", event["payload"]["actor"],
                                        event["payload"], event)
                self.save_corrective(db, case)
            return {}

    def record_replacement_dispatch(self, corrective_id, key):
        from registry import Conflict

        with self.transaction() as db:
            case = self.read_corrective(db, corrective_id)
            self.require_corrective_instance(case, key)
            child = self.read(db, case["replacementShipmentId"])
            if child["state"] == "CANCELLED":
                case["replacementOutcome"] = "CANCELLED"
                self.corrective_journal(db, case, "ReplacementCancelled",
                                        payload={"replacementShipmentId": child["shipmentId"]})
                dispatched = False
            elif db.execute("SELECT 1 FROM events WHERE id=?",
                            (f"P1:{child['shipmentId']}:ShipmentDispatched",)).fetchone():
                if case["replacementOutcome"] == "PREPARING":
                    case["replacementOutcome"] = "AWAITING_RECEIVING"
                self.corrective_journal(db, case, "ReplacementDispatched",
                                        payload={"replacementShipmentId": child["shipmentId"],
                                                 "replacementJourneyId": child["journeyId"]})
                dispatched = True
            else:
                raise Conflict("P1 has not dispatched or cancelled the replacement")
            self.save_corrective(db, case)
            return {"replacementDispatched": dispatched}

    def notify_corrective_receiving(self, db, record, outcome):
        """P3 calls atomically with admission/rejection, before any P5 engine wait."""
        if not record.get("originalShipmentId") or not record.get("replacementForCorrectiveId"):
            return
        # A replacement may acquire its own P4 incident/correctiveId. Preserve the
        # case it fulfils independently, so its rejection still reaches its parent.
        case = self.read_corrective(db, record["replacementForCorrectiveId"])
        if case["replacementShipmentId"] != record["shipmentId"]:
            from registry import Conflict
            raise Conflict("replacement does not belong to the corrective case")
        event_id = f"P3:{record['shipmentId']}:" + ("StockAdmitted" if outcome == "ADMITTED" else "GoodsRejected")
        event = json.loads(db.execute("SELECT data FROM events WHERE id=?", (event_id,)).fetchone()[0])
        self.enqueue(db, f"P5:{case['correctiveId']}:ReplacementOutcome", "P5ReplacementOutcome",
                     case["correctiveId"], {"correctiveId": case["correctiveId"], "replacementOutcomeEvent": event})

    def record_replacement_outcome(self, corrective_id, key, event):
        from registry import Conflict

        with self.transaction() as db:
            case = self.read_corrective(db, corrective_id)
            self.require_corrective_instance(case, key)
            child = self.read(db, case["replacementShipmentId"])
            outcome = {"StockAdmitted": "ADMITTED", "GoodsRejected": "REJECTED"}.get(event.get("eventType"))
            row = db.execute("SELECT data FROM outbox WHERE id=?",
                             (f"P5:{corrective_id}:ReplacementOutcome",)).fetchone()
            if (not row or json.loads(row[0])["variables"]["replacementOutcomeEvent"] != event
                    or event.get("shipmentId") != child["shipmentId"]
                    or child.get("admissionStatus") != outcome or outcome is None
                    or case["replacementOutcome"] not in ("AWAITING_RECEIVING", outcome)):
                raise Conflict("replacement outcome must match its durable P3 result after dispatch")
            case["replacementOutcome"] = outcome
            self.corrective_journal(db, case, "ReplacementAdmitted" if outcome == "ADMITTED" else "ReplacementRejected",
                                    payload={"replacementShipmentId": child["shipmentId"], "receivingEventId": event["eventId"]})
            self.save_corrective(db, case)
            return {}

    def complete_corrective(self, corrective_id, key):
        from registry import Conflict, now

        with self.transaction() as db:
            case = self.read_corrective(db, corrective_id)
            self.require_corrective_instance(case, key)
            if case["status"] not in ("COMPLETED", "FAILED"):
                if case["status"] != "IN_PROGRESS" or case["originalGoodsStatus"] != "COLLECTED":
                    raise Conflict("completion requires acknowledged original collection")
                outcome = case["replacementOutcome"]
                if outcome not in ("NOT_REQUIRED", "ADMITTED", "REJECTED", "CANCELLED"):
                    raise Conflict("replacement must be admitted, rejected or cancelled before completion")
                success = outcome in ("NOT_REQUIRED", "ADMITTED")
                case.update(status="COMPLETED" if success else "FAILED", completedAt=now())
                self.corrective_journal(db, case, "CorrectiveFulfilmentCompleted" if success else "CorrectiveFulfilmentFailed",
                                        payload={"originalGoodsStatus": case["originalGoodsStatus"],
                                                 "replacementShipmentId": case["replacementShipmentId"],
                                                 "replacementOutcome": outcome})
                self.save_corrective(db, case)
            return {"correctiveSucceeded": case["status"] == "COMPLETED"}
