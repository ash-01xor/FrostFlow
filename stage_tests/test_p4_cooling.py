"""Offline checks for P4's side of the cooling loop (issue #17, docs/P4-COOLING-CONTRACT.md).

Same harness as test_p4.py: real registry/monitoring on new monitored shipments, Zeebe
substituted, P4 on the local DCR engine. The simulator's results and Siddhi's recovery
evaluations are posted by the test as the contract describes; the simulator applying a
command to its readings and Siddhi's recovery query are the other owner's part and are
NOT exercised here (readings are entered explicitly, as in test_monitoring.py).
"""

import os
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "deployer"), str(ROOT / "stage_tests")]
import test_p4 as p4  # noqa: E402

from compliance.check import replay  # noqa: E402

service = p4.service
URL = {"P4_COOLING_COMMAND_URL": "http://siddhi:8007/cooling-command",
       "P4_COOLING_ACK_TIMEOUT_SECONDS": "45"}


class Flow(unittest.TestCase):
    """P4Flow's helpers without its test methods."""

    for _name, _value in vars(p4.P4Flow).items():
        if not _name.startswith("test_") and not _name.startswith("__"):
            locals()[_name] = _value


class CoolingLoop(Flow):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, URL)
        env.start()
        self.addCleanup(env.stop)
        self.start_p2_transport()
        self.readings(10, 10, 10)                 # sustained excursion -> AssessmentRequested
        self.deliver_p4()
        self.run_path(["ReviewTemperatureLog"])

    # -- helpers --------------------------------------------------------------
    def commands(self):
        return self.db.pending_messages(destination="p4-cooling-command")

    def request_cooling(self):
        response = self.act("RequestCoolingAdjustment", note="lower setpoint")
        self.assertEqual(response.status_code, 200, response.json)
        return response.json["event"]["payload"]["commandId"]

    def command(self, command_id=None):
        commands = self.incident()["cooling"]["commands"]
        return next(c for c in commands if c["commandId"] == command_id) if command_id \
            else commands[-1]

    def result(self, command_id, status="APPLIED", result_id=None, **changes):
        record = self.db.get("S-test")
        event = dict(schemaVersion=1, eventType="CoolingCommandResult",
                     resultId=result_id or f"SIM:{command_id}:{status}",
                     commandId=command_id, incidentId=self.incident_id(),
                     shipmentId="S-test", journeyId=record["journeyId"],
                     sessionId=record["monitoring"]["sessionId"], status=status,
                     occurredAt=p4.iso(self.clock), source="container-simulator",
                     inputMode="simulated")
        if status == "APPLIED":
            event.update(appliedAt=p4.iso(self.clock),
                         effectiveFromSequence=record["monitoring"]["sequence"] + 1)
        else:
            event["failureReason"] = "session-closed"
        event.update(changes)
        return self.http.post("/integrations/p4/cooling-result", json={"event": event},
                              headers={"Idempotency-Key": event["resultId"]})

    def recovery(self, command_id, first, last, outcome="RECOVERED", recovery_id=None,
                 qualified_at=None, **changes):
        record = self.db.get("S-test")
        event = dict(schemaVersion=1, eventType="RecoveryEvaluated",
                     recoveryId=recovery_id or f"SIDDHI:RECOVERY:{command_id}:{last}",
                     commandId=command_id, incidentId=self.incident_id(),
                     shipmentId="S-test", journeyId=record["journeyId"],
                     sessionId=record["monitoring"]["sessionId"], outcome=outcome,
                     firstSequence=first, lastSequence=last, readingCount=last - first + 1,
                     qualifiedAt=qualified_at or p4.iso(self.clock),
                     criteria=self.command(command_id)["recoveryCriteria"],
                     occurredAt=p4.iso(self.clock), source="siddhi", inputMode="simulated")
        event.update(changes)
        return self.http.post("/integrations/p4/recovery", json={"event": event},
                              headers={"Idempotency-Key": event["recoveryId"]})

    def sequence(self):
        return self.db.get("S-test")["monitoring"]["sequence"]

    # -- scenarios ------------------------------------------------------------
    def test_command_ack_recovery_then_officer_release(self):
        command_id = self.request_cooling()
        [message] = self.commands()
        self.assertEqual(message["messageId"], command_id)
        sent = message["variables"]["event"]
        self.assertEqual((sent["eventType"], sent["command"], sent["incidentId"]),
                         ("CoolingAdjustmentRequested", "AdjustCooling", self.incident_id()))
        self.assertEqual(sent["sessionId"], self.db.get("S-test")["monitoring"]["sessionId"])
        self.assertEqual(sent["recoveryCriteria"]["thresholdC"], 5.0)
        # Delivery receipt is not application, and manual entry is refused.
        service.quality.cooling_command_delivered(command_id, {"commandId": command_id,
                                                               "status": "received"})
        self.assertEqual(self.command()["status"], "DELIVERED")
        self.assertNotIn("RecordCoolingAcknowledgement", self.trace())
        self.assertEqual(self.act("RecordCoolingAcknowledgement").status_code, 409)
        self.assertEqual(self.act("RequestCoolingAdjustment").status_code, 409)  # outstanding
        self.assertEqual(self.act("CloseInvestigation").status_code, 409)

        applied = self.result(command_id)
        self.assertEqual((applied.status_code, applied.json["effect"]),
                         (200, "acknowledgement-queued"), applied.json)
        self.assertEqual(self.trace()[-1], "RecordCoolingAcknowledgement")
        effective = self.command()["result"]["effectiveFromSequence"]
        self.readings(3, 3, 3)
        recovered = self.recovery(command_id, effective, self.sequence())
        self.assertEqual(recovered.json["effect"], "recovery-queued", recovered.json)
        self.assertEqual(self.trace()[-1], "RecordRecoveryEvidence")
        # Recovery never releases goods and makes a new assessment due.
        self.assertEqual(self.db.get("S-test")["qualityStatus"], "PENDING")
        blocked = self.act("DecideRelease", reason="recovered")
        self.assertEqual(blocked.status_code, 409)
        self.run_path(["AssessEvidence", "DecideRelease", "CloseInvestigation"])
        self.assertEqual(self.db.get("S-test")["qualityStatus"], "RELEASED")
        ack = next(e for e in self.db.events("S-test")
                   if e["eventType"] == "RecordCoolingAcknowledgement")
        self.assertEqual((ack["inputMode"], ack["source"], ack["payload"]["commandId"]),
                         ("simulated", "container-simulator", command_id))
        self.assertTrue(replay(p4.MODEL, self.trace())["accepting"])

    def test_duplicate_and_conflicting_results_and_identity_checks(self):
        command_id = self.request_cooling()
        first = self.result(command_id)
        again = self.result(command_id)
        self.assertEqual((first.json["status"], again.json["status"]), ("accepted", "duplicate"))
        self.assertEqual(self.trace().count("RecordCoolingAcknowledgement"), 1)
        self.assertEqual(self.result(command_id, appliedAt="2026-12-01T09:00:00Z").status_code, 409)
        self.assertEqual(self.result(command_id, result_id="r2", sessionId="CONTAINER:x:y")
                         .status_code, 409)
        self.assertEqual(self.result(command_id, result_id="r3", shipmentId="S-other")
                         .status_code, 409)
        self.assertEqual(self.result("P4:COOL:nope:1", result_id="r4").status_code, 409)
        self.assertEqual(self.result(command_id, result_id="r5", incidentId="ASSESS:none")
                         .status_code, 404)
        late = self.result(command_id, status="FAILED", result_id="r6")
        self.assertEqual(late.json["effect"], "late-result-recorded")
        self.assertEqual(self.command()["status"], "APPLIED")       # first result stands
        bad = self.http.post("/integrations/p4/cooling-result", json={"event": {}},
                             headers={"Idempotency-Key": "x"})
        self.assertEqual(bad.status_code, 400)

    def test_lost_ack_times_out_and_late_ack_creates_no_recovery(self):
        command_id = self.request_cooling()
        self.assertEqual(service.quality.expire_cooling_commands(), [])
        self.tick(46)
        self.assertEqual(service.quality.expire_cooling_commands(), [command_id])
        self.assertEqual(self.commands(), [])                         # undelivered: cancelled
        command = self.command()
        self.assertEqual((command["status"], command["result"]["failureReason"]),
                         ("FAILED", "acknowledgement-timeout"))
        self.assertEqual(self.trace()[-1], "RecordCoolingFailure")
        late = self.result(command_id)
        self.assertEqual(late.json["effect"], "late-result-recorded")
        self.assertNotIn("RecordCoolingAcknowledgement", self.trace())
        self.readings(3, 3, 3)
        refused = self.recovery(command_id, self.sequence() - 2, self.sequence())
        self.assertEqual(refused.json["effect"], "recovery-refused")
        self.assertNotIn("RecordRecoveryEvidence", self.trace())
        # A new request is possible after the failure; it gets a new identity.
        self.assertEqual(self.request_cooling(), command_id[:-1] + "2")

    def test_simulator_failure_and_definite_refusal(self):
        command_id = self.request_cooling()
        failed = self.result(command_id, status="FAILED")
        self.assertEqual(failed.json["effect"], "failure-queued")
        self.assertEqual(self.trace()[-1], "RecordCoolingFailure")
        second = self.request_cooling()
        service.quality.cooling_command_refused(second, "HTTP 409: CONFLICT")
        self.assertEqual(self.command(second)["result"]["failureReason"], "command-refused")
        self.assertEqual(self.trace().count("RecordCoolingFailure"), 2)
        self.assertEqual(self.commands(), [])
        self.run_path(["AssessEvidence", "DecideReplace", "CloseInvestigation"])
        self.assertEqual(self.db.get("S-test")["qualityStatus"], "REJECTED")

    def test_recovery_needs_fresh_distinct_readings_after_application(self):
        command_id = self.request_cooling()
        self.readings(3)                                   # before application
        before = self.sequence()
        self.result(command_id)
        effective = self.command()["result"]["effectiveFromSequence"]
        self.readings(3)
        one = self.recovery(command_id, effective, effective, recovery_id="one")
        self.assertIn("at least 3", one.json["refusal"])
        self.readings(3, 3)
        cases = {
            "early": dict(first=before, last=self.sequence()),
            "criteria": dict(first=effective, last=self.sequence(),
                             criteria={"thresholdC": 99}),
            "stale": dict(first=effective, last=self.sequence(),
                          qualified_at=p4.iso(self.clock + timedelta(seconds=60))),
        }
        for name, case in cases.items():
            response = self.recovery(command_id, case.pop("first"), case.pop("last"),
                                     recovery_id=name, **case)
            self.assertEqual(response.json["effect"], "recovery-refused", name)
        self.readings(9, 3, 3)
        warm = self.recovery(command_id, self.sequence() - 2, self.sequence(), recovery_id="warm")
        self.assertIn("above the threshold", warm.json["refusal"])
        not_yet = self.recovery(command_id, effective, self.sequence(), outcome="NOT_RECOVERED",
                                recovery_id="no")
        self.assertEqual(not_yet.json["effect"], "not-recovered-recorded")
        self.assertNotIn("RecordRecoveryEvidence", self.trace())
        self.assertEqual(self.db.get("S-test")["qualityStatus"], "PENDING")

    def test_gap_in_telemetry_is_not_recovery(self):
        command_id = self.request_cooling()
        self.result(command_id)
        self.readings(3)
        self.tick(30)                                      # silence longer than maxGap
        self.readings(3, 3)
        gap = self.recovery(command_id, self.sequence() - 2, self.sequence())
        self.assertIn("gap", gap.json["refusal"])

    def test_cooling_refused_after_receipt_and_in_flight_result(self):
        command_id = self.request_cooling()
        self.arrive_and_receive()                          # closes the monitoring session
        failed = self.result(command_id, status="FAILED")  # simulator: session-closed
        self.assertEqual(failed.json["effect"], "failure-queued")
        refused = self.act("RequestCoolingAdjustment")
        self.assertEqual(refused.status_code, 409)
        self.assertIn("monitoring session is active", refused.json["error"])
        self.assertEqual(self.db.admit_p3_stock("S-test", self.p3_key),
                         {"admissionOutcome": "HELD"})

    def test_restart_keeps_command_identity(self):
        command_id = self.request_cooling()
        restarted = p4.quality.QualityService(service.quality.get_registry,
                                              service.quality.get_dcr)
        with patch.object(service, "quality", restarted):
            restarted.retry_unfinished()
            [message] = self.commands()
            self.assertEqual(message["messageId"], command_id)
            self.assertEqual(self.result(command_id).json["effect"], "acknowledgement-queued")
        self.assertEqual(self.trace().count("RequestCoolingAdjustment"), 1)

    def test_bad_delivery_answer_is_not_receipt(self):
        command_id = self.request_cooling()
        with self.assertRaises(ValueError):
            service.quality.cooling_command_delivered(command_id, {"status": "applied"})
        self.assertEqual(self.command()["status"], "REQUESTED")


class ManualTestStandIn(Flow):
    """Without P4_COOLING_COMMAND_URL the existing manual path stays, clearly labelled."""

    def test_manual_inputs_are_labelled(self):
        with patch.dict(os.environ, {"P4_COOLING_COMMAND_URL": ""}):
            self.start_p2_transport()
            self.readings(10, 10, 10)
            self.deliver_p4()
            self.run_path(["ReviewTemperatureLog", "RequestCoolingAdjustment",
                           "RecordCoolingAcknowledgement", "RecordRecoveryEvidence"])
            self.assertEqual(self.db.pending_messages(destination="p4-cooling-command"), [])
            modes = {e["eventType"]: e["inputMode"] for e in self.db.events("S-test")}
            self.assertEqual(modes["RecordCoolingAcknowledgement"], "manual-test")
            self.assertEqual(modes["RecordRecoveryEvidence"], "manual-test")
            self.assertEqual(modes["RequestCoolingAdjustment"], "manual")
            self.assertEqual(self.incident()["cooling"]["commands"][0]["mode"], "manual-test")
            self.assertEqual(self.db.get("S-test")["qualityStatus"], "PENDING")


if __name__ == "__main__":
    unittest.main()
