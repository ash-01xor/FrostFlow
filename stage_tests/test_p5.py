"""P5 atomic business effects with real SQLite/local DCR; Zeebe is substituted."""

import json
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "deployer"), str(ROOT / "stage_tests")]
import test_p4 as p4
from monitoring import evaluate, seconds
from registry import Conflict, Registry

service = p4.service


class CorrectiveEffects(unittest.TestCase):
    # Reuse the monitored P1/P2/P3/P4 fixtures without duplicating their test suite.
    for _name, _value in vars(p4.P4Flow).items():
        if callable(_value) and not _name.startswith(("test_", "__")):
            locals()[_name] = _value

    def authorize(self, action="REPLACE", received=True):
        self.start_p2_transport()
        self.readings(3, 3, 3)
        self.risk_at_index(2)
        self.deliver_p4()
        if received:
            self.arrive_and_receive()
            self.db.admit_p3_stock("S-test", self.p3_key)
        self.run_path(["ReviewTemperatureLog", "AssessEvidence", "Decide" + action.title()])
        if received:
            self.resume_p3()
        [case] = self.db.corrective_cases()
        self.cid = case["correctiveId"]
        return case

    def register(self, case):
        return self.db.register_corrective(self.cid, 555, case["authorization"])

    def arrangement(self, case, replacement="S-replacement"):
        payload = dict(actor="planner", collector="demo-collector", bookingReference="BOOK-1",
                       scheduledAt="2026-12-02T12:00:00Z")
        if case["action"] == "REPLACE":
            payload["replacement"] = dict(shipmentId=replacement, journeyId="J-" + replacement,
                                          vehicleId="V-02", routeId="route-01",
                                          deliveryDeadline="2026-12-02T14:00:00Z")
        return payload

    def arrange(self, case):
        self.register(case)
        payload = self.arrangement(case)
        accepted = self.db.queue_corrective_input(self.cid, "ArrangementRequested", "arrange-1", p4.iso(self.clock), payload)
        self.db.arrange_corrective(self.cid, 555, accepted["event"])
        return accepted

    def collect(self):
        accepted = self.db.queue_corrective_input(self.cid, "ReturnCollected", "collect-1", p4.iso(self.clock),
                                                  dict(actor="receiver", collector="demo-collector",
                                                       bookingReference="BOOK-1", quantityCollected=3))
        self.db.record_corrective_collection(self.cid, 555, accepted["event"])
        return accepted

    def child_outcome(self, passed=True, quality_rejected=False):
        """Run actual P1/P2/P3 registry effects on the linked monitored replacement."""
        sid, key, p2key, p3key = "S-replacement", 666, 888, 9999
        self.db.check_stock(sid, key, 66601)
        self.db.advance(sid, key, "STOCK_RESERVED", "STOCK_ALLOCATED", "StockAllocated")
        self.db.advance(sid, key, "STOCK_ALLOCATED", "AWAITING_LOADING", "TransportAssigned")
        self.db.reserve_command(sid, "LoadingVerified", "loader")
        self.db.advance(sid, key, "AWAITING_LOADING", "AWAITING_DISPATCH", "LoadingVerified")
        self.db.reserve_command(sid, "DispatchApproved", "dispatcher")
        self.db.advance(sid, key, "AWAITING_DISPATCH", "DISPATCHED", "ShipmentDispatched")
        dispatch = next(e for e in self.db.events(sid) if e["eventType"] == "ShipmentDispatched")
        self.db.start_transport(sid, p2key, dispatch)
        points = [{"lat": 55.7, "lon": 12.5}, {"lat": 55.8, "lon": 12.6}]
        self.db.start_journey(sid, p2key, points[0], "2026-12-01T12:00:00Z", "local-demo-estimate", points)
        for _ in range(3):
            self.tick()
            self.db.sample_container(sid, p4.iso(self.clock), 3)
            with self.db.transaction() as db:
                item = json.loads(db.execute("SELECT data FROM container_observations WHERE shipment_id=? ORDER BY sequence DESC",
                                             (sid,)).fetchone()[0])
            observation = item["observation"]
            excursion, rising = evaluate(item["window"], self.db.get(sid)["monitoringProfile"], observation["occurredAt"])
            self.db.acknowledge_container({**{k: observation[k] for k in ("shipmentId", "journeyId", "eventId", "sequence")},
                                          "evaluatedAtMillis": int(seconds(observation["occurredAt"]) * 1000),
                                          "excursion": excursion, "combined": False})
        if quality_rejected:
            record = self.db.get(sid)
            self.db.queue_delivery_risk(sid, "risk-child", dict(eta="2026-12-03T14:00:00Z",
                deliveryDeadline=record["deliveryDeadline"], occurredAt=p4.iso(self.clock),
                reason="test replacement quality hold"))
            self.deliver_p4()
        arrival = self.db.queue_transport_event(sid, "ShipmentArrived", "arr-child", p4.iso(self.clock),
                                               {"recipientId": "C-01"}, source="p2-simulator")
        self.db.record_arrival(sid, p2key, arrival["event"])
        notification = next(m["variables"]["event"] for m in self.db.pending_messages("p3-http")
                            if m["variables"]["event"]["shipmentId"] == sid)
        self.assertEqual(notification["replacementForCorrectiveId"], self.cid)
        with self.assertRaises(Conflict):
            self.db.accept_p3_arrival({**notification, "replacementForCorrectiveId": "forged"})
        response = self.http.post("/integrations/p3/arrival", json={"event": notification},
                                  headers={"Idempotency-Key": notification["deliveryId"]})
        self.assertEqual(response.status_code, 202, response.json)
        self.db.record_p3_arrival(sid, p3key, notification)
        receipt = self.db.queue_p3_input(sid, "GoodsReceived", "receipt-child", p4.iso(self.clock),
                                          dict(recipientId="C-01", quantityReceived=3, receiver="receiver"))
        self.db.record_p3_receipt(sid, p3key, receipt["event"])
        inspection = self.db.queue_p3_input(sid, "GoodsInspected", "inspect-child", p4.iso(self.clock),
                                             dict(passed=passed, inspector="inspector"))
        self.db.record_p3_inspection(sid, p3key, inspection["event"])
        if passed:
            self.db.finalize_monitoring(sid)
            self.db.admit_p3_stock(sid, p3key)
        if quality_rejected:
            incident = self.db.get(sid)["qualityIncidentId"]
            self.run_path(["ReviewTemperatureLog", "AssessEvidence", "DecideReturn"], incident=incident)
            status = next(m["variables"]["qualityStatusEvent"] for m in self.db.pending_messages()
                          if m["name"] == "QualityStatusChanged" and m["correlationKey"] == sid)
            self.db.record_p3_quality_status(sid, p3key, status)
            self.db.admit_p3_stock(sid, p3key)
        return next(m["variables"]["replacementOutcomeEvent"] for m in self.db.pending_messages()
                    if m["name"] == "P5ReplacementOutcome")

    def test_release_and_quality_status_stand_in_do_not_authorize_p5(self):
        self.start_p2_transport()
        self.readings(3, 3, 3)
        self.risk_at_index(2)
        self.deliver_p4()
        self.run_path(["ReviewTemperatureLog", "AssessEvidence", "DecideRelease"])
        self.assertEqual(self.db.corrective_cases(), [])
        # Public order creation cannot manufacture a linked replacement.
        data = p4.stage.sample(shipment="forged", journey="forged", vehicle="V-02")
        data.update(originalShipmentId="S-test", incidentId=self.incident_id())
        self.assertEqual(self.http.post("/shipments", json=data).status_code, 409)

    def test_return_waits_for_booking_and_collection_and_retries_after_restart(self):
        case = self.authorize("RETURN")
        self.register(case)
        self.assertEqual(self.db.register_corrective(self.cid, 555, case["authorization"]), {"correctiveAction": "RETURN"})
        with self.assertRaises(Conflict):
            self.db.register_corrective(self.cid, 556, case["authorization"])
        with self.assertRaises(Conflict):
            self.db.register_corrective(self.cid, 555, {**case["authorization"], "eventId": "forged"})
        with self.assertRaises(Conflict):
            self.collect()
        arrangement = self.arrange(case)
        with self.assertRaises(Conflict):
            self.db.complete_corrective(self.cid, 555)
        collection = self.collect()
        self.db = service.registry = Registry(self.db.path)
        self.assertEqual(self.db.complete_corrective(self.cid, 555), {"correctiveSucceeded": True})
        self.assertEqual(self.db.complete_corrective(self.cid, 555), {"correctiveSucceeded": True})
        self.assertEqual(self.db.queue_corrective_input(self.cid, "ReturnCollected", "collect-1", p4.iso(self.clock),
                                                       collection["event"]["payload"]), collection)
        self.db.arrange_corrective(self.cid, 555, arrangement["event"])
        self.db.record_corrective_collection(self.cid, 555, collection["event"])
        self.assertEqual(self.db.get_corrective(self.cid)["status"], "COMPLETED")
        self.assertEqual(self.db.receiving_inventory()["available"], 0)
        self.assertEqual(self.db.inventory()["available"], 97)

    def test_replacement_admission_is_required_and_original_collection_is_independent(self):
        case = self.authorize()
        self.arrange(case)
        child = self.db.get("S-replacement")
        original = self.db.get("S-test")
        for field in ("quantity", "productId", "batchId", "recipientId"):
            self.assertEqual(child[field], original[field])
        self.assertEqual((child["originalShipmentId"], child["incidentId"]), ("S-test", case["incidentId"]))
        self.assertNotEqual(child["journeyId"], original["journeyId"])
        with self.assertRaises(Conflict):
            self.db.record_replacement_dispatch(self.cid, 555)
        outcome = self.child_outcome()
        # Outcome can arrive before the P5 dispatch worker/catch; it stays durable.
        self.assertEqual(self.db.record_replacement_dispatch(self.cid, 555), {"replacementDispatched": True})
        self.db.record_replacement_outcome(self.cid, 555, outcome)
        with self.assertRaises(Conflict):
            self.db.complete_corrective(self.cid, 555)
        self.collect()
        self.db.complete_corrective(self.cid, 555)
        self.assertEqual(self.db.get_corrective(self.cid)["status"], "COMPLETED")
        self.assertEqual(self.db.receiving_inventory()["available"], 3)
        self.assertEqual(self.db.inventory()["available"], 94)
        self.assertEqual(self.db.get("S-test")["admissionStatus"], "REJECTED")
        self.db.record_replacement_outcome(self.cid, 555, outcome)
        self.assertEqual(self.db.receiving_inventory()["available"], 3)

    def test_rejected_replacement_never_completes_successfully(self):
        case = self.authorize()
        self.arrange(case)
        self.collect()
        outcome = self.child_outcome(passed=False)
        self.db.record_replacement_dispatch(self.cid, 555)
        with self.assertRaises(Conflict):
            self.db.record_replacement_outcome(self.cid, 555, {**outcome, "eventType": "StockAdmitted"})
        self.db.record_replacement_outcome(self.cid, 555, outcome)
        self.assertEqual(self.db.complete_corrective(self.cid, 555), {"correctiveSucceeded": False})
        self.assertEqual(self.db.get_corrective(self.cid)["status"], "FAILED")
        self.assertEqual(self.db.receiving_inventory()["available"], 0)

    def test_replacement_own_p4_return_preserves_parent_fulfilment_link(self):
        case = self.authorize()
        self.arrange(case)
        outcome = self.child_outcome(quality_rejected=True)
        child = self.db.get("S-replacement")
        self.assertEqual(child["replacementForCorrectiveId"], self.cid)
        self.assertNotEqual(child["correctiveId"], self.cid)
        self.assertEqual(len(self.db.corrective_cases()), 2)
        self.db.record_replacement_dispatch(self.cid, 555)
        self.db.record_replacement_outcome(self.cid, 555, outcome)
        self.collect()
        self.assertEqual(self.db.complete_corrective(self.cid, 555), {"correctiveSucceeded": False})
        self.assertEqual(self.db.get_corrective(child["correctiveId"])["originalShipmentId"], "S-replacement")

    def test_cancelled_shortage_replacement_is_failed_and_releases_vehicle(self):
        case = self.authorize()
        self.arrange(case)
        with self.db.transaction() as db:
            db.execute("UPDATE inventory SET available=0 WHERE product_id='PRD-001'")
        self.db.check_stock("S-replacement", 666, 66601)
        self.db.reserve_command("S-replacement", "ShipmentCancellationRequested", "planner")
        self.db.advance("S-replacement", 666, "WAITING_STOCK", "CANCELLED", "ShipmentCancelled")
        self.assertEqual(self.db.record_replacement_dispatch(self.cid, 555), {"replacementDispatched": False})
        self.collect()
        self.assertEqual(self.db.complete_corrective(self.cid, 555), {"correctiveSucceeded": False})
        self.assertTrue(self.db.get("S-replacement")["vehicleReleased"])

    def test_concurrent_arrangements_reserve_exactly_one_child_and_conflicting_reuse_fails(self):
        case = self.authorize()
        self.register(case)
        payload = self.arrangement(case)
        def queue(_):
            return self.db.queue_corrective_input(self.cid, "ArrangementRequested", "a1", p4.iso(self.clock), payload)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(queue, range(4)))
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(self.db.inventory()["shipmentCount"], 2)
        with self.assertRaises(Conflict):
            self.db.queue_corrective_input(self.cid, "ArrangementRequested", "a1", p4.iso(self.clock), {**payload, "collector": "other"})
        with self.assertRaises(Conflict):
            self.db.queue_corrective_input(self.cid, "ReturnCollected", "a1", p4.iso(self.clock), payload)

    def test_refused_arrangement_rolls_back_child_and_does_not_reserve_input(self):
        case = self.authorize(received=False)
        self.register(case)
        payload = self.arrangement(case)
        from datetime import datetime, timedelta
        before_authorization = p4.iso(datetime.fromisoformat(case["authorization"]["occurredAt"].replace("Z", "+00:00")) - timedelta(seconds=1))
        with self.assertRaises(Conflict):
            self.db.queue_corrective_input(self.cid, "ArrangementRequested", "a1", before_authorization, payload)
        payload["replacement"]["vehicleId"] = "V-01"  # still reserved by the moving original
        with self.assertRaises(Conflict):
            self.db.queue_corrective_input(self.cid, "ArrangementRequested", "a1", p4.iso(self.clock), payload)
        self.assertEqual(self.db.inventory()["shipmentCount"], 1)
        self.assertEqual(self.db.get_corrective(self.cid)["inputs"], {})
        self.assertEqual(self.db.get_corrective(self.cid)["status"], "AWAITING_ARRANGEMENT")
        payload["replacement"]["vehicleId"] = "V-02"
        self.db.queue_corrective_input(self.cid, "ArrangementRequested", "a1", p4.iso(self.clock), payload)
        self.assertEqual(self.db.inventory()["shipmentCount"], 2)

    def test_p4_transaction_failure_rolls_back_authorization_and_retry_recovers(self):
        self.start_p2_transport()
        self.readings(3, 3, 3)
        self.risk_at_index(2)
        self.deliver_p4()
        self.run_path(["ReviewTemperatureLog", "AssessEvidence"])
        with patch.object(self.db, "authorize_corrective", side_effect=RuntimeError("crash before commit")):
            with self.assertRaises(RuntimeError):
                service.quality.execute(self.incident_id(), "DecideReturn", {"actor": "officer", "reason": "unsafe", "eventId": "d1"})
        self.assertEqual(self.db.corrective_cases(), [])
        self.assertFalse(any(e["eventType"] == "DispositionRecorded" for e in self.db.events("S-test")))
        service.quality.retry_unfinished()
        [case] = self.db.corrective_cases()
        self.assertEqual(case["action"], "RETURN")
        self.assertEqual(len([m for m in self.db.pending_messages() if m["name"] == "P5DispositionRecorded"]), 1)

    def test_collection_requires_full_matching_booking_and_physically_rejected_original(self):
        case = self.authorize("RETURN", received=False)
        self.arrange(case)
        with self.assertRaises(Conflict):
            self.collect()
        self.arrive_and_receive()
        self.db.admit_p3_stock("S-test", self.p3_key)
        for change in ({"collector": "other"}, {"quantityCollected": 2}, {"bookingReference": "unknown"}):
            payload = dict(actor="receiver", collector="demo-collector", bookingReference="BOOK-1", quantityCollected=3)
            with self.assertRaises(Conflict):
                self.db.queue_corrective_input(self.cid, "ReturnCollected", "c1", p4.iso(self.clock), {**payload, **change})
        self.collect()

    def test_api_validates_arrangements_and_preserves_identity_on_retry(self):
        case = self.authorize()
        self.register(case)
        path = f"/corrective-fulfilments/{self.cid}/arrange"
        data = dict(eventId="arrange-api", occurredAt=p4.iso(self.clock), **self.arrangement(case))
        for replacement in (None, {}, {**data["replacement"], "quantity": 1},
                            {**data["replacement"], "vehicleId": "V-99"},
                            {**data["replacement"], "shipmentId": "../invalid"},
                            {**data["replacement"], "deliveryDeadline": "2026-12-01T12:00:00"}):
            self.assertEqual(self.http.post(path, json={**data, "replacement": replacement}).status_code, 400)
        response = self.http.post(path, json=data)
        self.assertEqual(response.status_code, 202, response.json)
        self.assertEqual(self.http.post(path, json=data).json, response.json)
        self.assertEqual(self.http.post(path, json={**data, "collector": "other"}).status_code, 409)
        self.assertEqual(self.http.get("/corrective-fulfilments?originalShipmentId=S-test").json[0]["correctiveId"], self.cid)
        self.assertEqual(self.http.get("/corrective-fulfilments?originalShipmentId=unrelated").json, [])
        self.assertEqual(self.http.get("/corrective-fulfilments/unknown").status_code, 404)
        service.ready.clear()
        self.assertEqual(self.http.post(path, json=data).status_code, 503)

    def test_api_collection_requires_processed_booking_and_valid_quantity(self):
        case = self.authorize("RETURN")
        self.arrange(case)
        path = f"/corrective-fulfilments/{self.cid}/collect"
        data = dict(eventId="collect-api", occurredAt=p4.iso(self.clock), actor="receiver", collector="demo-collector",
                    bookingReference="BOOK-1", quantityCollected=3)
        for quantity in (True, 0, -1, 1.5, "3"):
            self.assertEqual(self.http.post(path, json={**data, "quantityCollected": quantity}).status_code, 400)
        self.assertEqual(self.http.post(path, json={**data, "quantityCollected": 1}).status_code, 409)
        self.assertEqual(self.http.post(path, json={**data, "occurredAt": "2026-12-01T08:00:00Z"}).status_code, 409)
        self.assertEqual(self.http.post(path, json=data).status_code, 202)
        self.assertEqual(self.db.get_corrective(self.cid)["originalGoodsStatus"], "AWAITING_COLLECTION")

    def test_api_return_refuses_replacement_and_both_paths_require_authorization(self):
        case = self.authorize("RETURN")
        self.register(case)
        data = dict(eventId="a-api", occurredAt=p4.iso(self.clock), **self.arrangement(case))
        data["replacement"] = self.arrangement({"action": "REPLACE"})["replacement"]
        self.assertEqual(self.http.post(f"/corrective-fulfilments/{self.cid}/arrange", json=data).status_code, 400)
        self.assertEqual(self.http.post("/corrective-fulfilments/unknown/arrange", json=data).status_code, 404)
        self.assertEqual(self.db.inventory()["shipmentCount"], 1)

    def test_quality_status_stand_in_rejection_cannot_authorize_corrective_work(self):
        self.start_p2_transport()
        self.readings(3, 3, 3)
        self.risk_at_index(2)
        record = self.db.get("S-test")
        response = self.http.post("/integrations/p4/quality-status", json=dict(
            shipmentId="S-test", eventId="stand-in-reject", occurredAt=p4.iso(self.clock),
            qualityStatus="REJECTED", incidentId=record["qualityIncidentId"],
            expectedEvidenceVersion=record["evidenceVersion"]))
        self.assertEqual(response.status_code, 202, response.json)
        self.assertEqual(self.db.corrective_cases(), [])
        self.assertFalse(any(m["name"] == "P5DispositionRecorded" for m in self.db.pending_messages()))


class CorrectiveCompliance(unittest.TestCase):
    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("p5_evidence", ROOT / "compliance/p5/build_p5_evidence.py")
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        self.rules = ROOT / "compliance/p5/rules.xml"

    def test_success_failure_and_both_parallel_orderings_conform(self):
        from compliance.check import replay
        start = ["ReplaceDisposition", "ReplacementAuthorized", "CollectionBooked", "ReplacementRequested"]
        success = ["ReplacementDispatched", "ReplacementAdmitted"]
        rejection = ["ReplacementDispatched", "ReplacementRejected"]
        for middle, end in ((success, "CorrectiveFulfilmentCompleted"),
                            (rejection, "CorrectiveFulfilmentFailed"),
                            (["ReplacementCancelled"], "CorrectiveFulfilmentFailed")):
            for events in (start + ["ReturnCollected"] + middle + [end], start + middle + ["ReturnCollected", end]):
                self.assertTrue(replay(self.rules, events)["accepting"], events)
        self.assertTrue(replay(self.rules, ["ReturnDisposition", "ReturnAuthorized", "CollectionBooked",
                                           "ReturnCollected", "CorrectiveFulfilmentCompleted"])["accepting"])

    def test_forbidden_actions_fail_and_unfinished_work_stays_pending(self):
        from compliance.check import replay
        for label, events in self.builder.FORBIDDEN.items():
            self.assertFalse(replay(self.rules, events)["valid"], label)
        result = replay(self.rules, ["ReturnDisposition", "ReturnAuthorized", "CollectionBooked"])
        self.assertTrue(result["valid"])
        self.assertFalse(result["accepting"])

    def test_builder_refuses_partial_or_synthetic_evidence(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "partial.json"
            evidence.write_text(json.dumps(dict(source=self.builder.SOURCE, status="failed", cases=[])))
            with self.assertRaises(ValueError):
                self.builder.live(evidence, Path(directory) / "output")
            self.assertFalse((Path(directory) / "output/execution-log.xes").exists())

    def test_tracked_rules_match_reproducible_builder(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory) / "rules.xml"
            self.builder.build_rules(generated)
            self.assertEqual(generated.read_bytes(), self.rules.read_bytes())


if __name__ == "__main__":
    unittest.main()
