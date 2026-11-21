"""Four real P5 cases on fresh matched data; P1/P2/P3/P5 in Zeebe, P4 in DCR.

Requires simulator on, GPS_TICK_SECONDS=2, local Siddhi and the local P4 handoff.
Original holds come from sustained simulated container excursions evaluated by Siddhi.
Collection, loading, receipt and inspection are manual/simulated confirmations.
No quality-status test stand-in, fabricated stock mutation or direct registry edit.
"""

import argparse
import hashlib
import json
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from check_p3_compliance import http, require, timestamp, wait_for
from check_p4_investigation import BATCHES, Case, dispatch, iso

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "live HTTP P5 run; P1/P2/P3/P5 in Zeebe; P4 in DCR; container evaluation in Siddhi"
SCENARIOS = {
    "return-collected": ("PRD-001", 1, "RETURN", None),
    "replacement-admitted": ("PRD-002", 1, "REPLACE", "ADMITTED"),
    "replacement-rejected": ("PRD-003", 1, "REPLACE", "REJECTED"),
    "replacement-cancelled": ("PRD-004", 20, "REPLACE", "CANCELLED"),
}


def engine_record(es, key, element, intent="ELEMENT_COMPLETED"):
    query = {"query": {"bool": {"filter": [
        {"term": {"value.processInstanceKey": int(key)}},
        {"term": {"value.elementId": element}}, {"term": {"intent": intent}},
    ]}}, "sort": [{"position": "desc"}], "size": 1}
    status, result = http(es, "/zeebe-record_process-instance*/_search", query)
    hits = result.get("hits", {}).get("hits", []) if status == 200 else []
    return hits[0]["_source"] if hits else None


def engine_end(es, key, element):
    return wait_for(f"exported engine end {key}/{element}", lambda: engine_record(es, key, element))


def load_and_dispatch(base, sid, excursion=False):
    path = "/shipments/" + sid
    wait_for("loading ready", lambda: (r := http(base, path)[1]).get("state") == "AWAITING_LOADING"
             and not r.get("pendingCommand"))
    require(http(base, path + "/verify-loading", dict(actor="p5-runner", quantityChecked=True,
                                                       packagingChecked=True, vehicleReady=True)), 202, "replacement loading")
    wait_for("replacement loading processed", lambda: http(base, path)[1].get("state") == "AWAITING_DISPATCH")
    if excursion:
        require(http(base, path + "/simulation/temperature", dict(commandId="temperature-" + sid,
                     actor="p5-runner", scenario="sustained-excursion")), 202, "replacement excursion")
    require(http(base, path + "/dispatch", {"actor": "p5-runner"}), 202, "replacement dispatch")


def receive(base, sid, quantity, passed=True):
    path = "/shipments/" + sid
    wait_for("P3 physical receipt readiness", lambda: http(base, path)[1].get("p3Status") == "AWAITING_RECEIPT", timeout=420)
    require(http(base, path + "/receiving/receipt", dict(eventId="receipt-" + sid, occurredAt=timestamp(),
             recipientId="C-01", quantityReceived=quantity, receiver="p5-receiver")), 202, "physical receipt")
    wait_for("inspection readiness", lambda: http(base, path)[1].get("p3Status") == "AWAITING_INSPECTION")
    require(http(base, path + "/receiving/inspection", dict(eventId="inspect-" + sid, occurredAt=timestamp(),
             inspector="p5-inspector", passed=passed)), 202, "inspection")


def run_case(base, es, run_id, index, scenario, definition):
    product, quantity, action, outcome = definition
    sid = f"p5live-{run_id}-{index}"
    original = Case(base, sid)
    if quantity == 1:
        dispatch(original, product, "V-01", "sustained-excursion")
    else:
        # The 20-crate original exhausts its product naturally, forcing replacement P1 to wait for stock.
        require(http(base, "/shipments", dict(shipmentId=sid, journeyId="journey-" + sid,
                 vehicleId="V-01", recipientId="C-01", routeId="route-01", quantity=quantity,
                 productId=product, batchId=BATCHES[product],
                 deliveryDeadline=iso(datetime.now(timezone.utc) + timedelta(hours=2)))), 202, "create full-product original")
        wait_for("original loading ready", lambda: original.get().get("state") == "AWAITING_LOADING"
                 and not original.get().get("pendingCommand"))
        require(http(base, original.url + "/verify-loading", dict(actor="p5-runner", quantityChecked=True,
                     packagingChecked=True, vehicleReady=True)), 202, "original loading")
        wait_for("original loading processed", lambda: original.get().get("state") == "AWAITING_DISPATCH")
        original.temperature("sustained-excursion")
        require(http(base, original.url + "/dispatch", {"actor": "p5-runner"}), 202, "original dispatch")
        original.incident_id = wait_for("Siddhi excursion hold", lambda: original.get().get("qualityIncidentId"))
        wait_for("P4 opened", lambda: original.incident().get("status") == "OPEN")
    receive(base, sid, quantity)
    wait_for("P3 hold after monitoring finalization", lambda: original.get().get("p3Status") == "ON_HOLD")
    original.step("ReviewTemperatureLog")
    original.step("AssessEvidence")
    original.step("Decide" + action.title())
    original.step("CloseInvestigation")
    wait_for("original rejection", lambda: original.get().get("p3Status") == "REJECTED")
    cases = wait_for("P5 case creation", lambda: http(base, "/corrective-fulfilments?originalShipmentId=" + sid)[1])
    assert len(cases) == 1
    cid = cases[0]["correctiveId"]
    path = "/corrective-fulfilments/" + cid
    get_case = lambda: http(base, path)[1]
    wait_for("P5 authorization registered", lambda: get_case()["status"] == "AWAITING_ARRANGEMENT")
    refused = []
    def refuse(suffix, data, expected=409):
        result = require(http(base, path + suffix, data), expected, "refused P5 " + suffix)
        refused.append(dict(action=suffix, expectedStatus=expected, error=result["error"]))
    collection = dict(eventId="collect-" + sid, occurredAt=timestamp(), actor="p5-receiver",
                      collector="demo-collector", bookingReference="BOOK-" + sid, quantityCollected=quantity)
    refuse("/collect", collection)
    replacement_id = sid + "-replacement"
    arrangement = dict(eventId="arrange-" + sid, occurredAt=timestamp(), actor="p5-planner",
                       collector="demo-collector", bookingReference="BOOK-" + sid,
                       scheduledAt=iso(datetime.now(timezone.utc) + timedelta(minutes=5)))
    if action == "REPLACE":
        arrangement["replacement"] = dict(shipmentId=replacement_id, journeyId="journey-" + replacement_id,
            vehicleId="V-02", routeId="route-01", deliveryDeadline=iso(datetime.now(timezone.utc) + timedelta(hours=2)))
    accepted = require(http(base, path + "/arrange", arrangement), 202, "arrange corrective work")
    assert require(http(base, path + "/arrange", arrangement), 202, "exact arrangement retry") == accepted
    refuse("/arrange", {**arrangement, "collector": "conflicting-collector"})
    wait_for("arrangement worker", lambda: get_case()["status"] == "IN_PROGRESS")
    collection["occurredAt"] = timestamp()
    refuse("/collect", {**collection, "bookingReference": "WRONG"})
    replacement, child_ends, followup = None, {}, None
    if action == "REPLACE":
        if outcome == "CANCELLED":
            wait_for("replacement P1 shortage wait", lambda: http(base, "/shipments/" + replacement_id)[1].get("state") == "WAITING_STOCK")
            require(http(base, "/shipments/" + replacement_id + "/cancel", {"actor": "p5-runner"}), 202, "cancel shortage replacement")
            wait_for("P5 cancellation processed", lambda: get_case()["replacementOutcome"] == "CANCELLED")
        else:
            load_and_dispatch(base, replacement_id, excursion=outcome == "REJECTED")
            wait_for("P5 dispatched replacement", lambda: get_case()["replacementOutcome"] == "AWAITING_RECEIVING")
            assert get_case()["status"] == "IN_PROGRESS", "dispatch cannot complete corrective work"
            if outcome == "REJECTED":
                child_case = Case(base, replacement_id)
                child_case.incident_id = wait_for("replacement Siddhi hold", lambda: child_case.get().get("qualityIncidentId"))
                wait_for("replacement P4 opened", lambda: child_case.incident().get("status") == "OPEN")
                receive(base, replacement_id, quantity)
                wait_for("replacement P3 on hold", lambda: child_case.get().get("p3Status") == "ON_HOLD")
                for step in ("ReviewTemperatureLog", "AssessEvidence", "DecideReturn", "CloseInvestigation"):
                    child_case.step(step)
            else:
                receive(base, replacement_id, quantity)
            wait_for("P5 receiving outcome", lambda: get_case()["replacementOutcome"] == outcome)
        assert get_case()["status"] == "IN_PROGRESS", "replacement outcome cannot replace original collection"
        replacement = require(http(base, "/shipments/" + replacement_id), 200, "replacement view")
        assert replacement["originalShipmentId"] == sid and replacement["incidentId"] == original.incident_id
        for field in ("productId", "batchId", "recipientId", "quantity"):
            assert replacement[field] == original.get()[field], field
        assert replacement["replacementForCorrectiveId"] == cid
        child_ends["p1"] = engine_end(es, replacement["processInstanceKey"],
                                     "ShipmentCancelled" if outcome == "CANCELLED" else "PreparationCompleted")
        activated = wait_for("P1 called child export", lambda: engine_record(es, replacement["processInstanceKey"],
                            "ShipmentPreparation", "ELEMENT_ACTIVATED"))
        assert str(activated["value"]["parentProcessInstanceKey"]) == get_case()["processInstanceKey"]
        child_ends["callActivityChild"] = activated
        if outcome != "CANCELLED":
            child_ends["p3"] = engine_end(es, replacement["p3ProcessInstanceKey"], "StockAdmitted" if outcome == "ADMITTED" else "GoodsRejected")
            child_ends["p2"] = engine_end(es, replacement["p2ProcessInstanceKey"], "TransportCompleted")
        if outcome == "REJECTED":
            # P4's return for the replacement creates its own corrective case. Finish
            # that collection too, while proving the parent receives its failed result.
            nested_id = replacement["correctiveId"]
            assert nested_id != cid
            nested_path = "/corrective-fulfilments/" + nested_id
            nested_view = lambda: http(base, nested_path)[1]
            wait_for("follow-up return registered", lambda: nested_view()["status"] == "AWAITING_ARRANGEMENT")
            booking = dict(eventId="nested-arrange-" + sid, occurredAt=timestamp(), actor="p5-planner",
                           collector="demo-collector", bookingReference="NESTED-" + sid,
                           scheduledAt=iso(datetime.now(timezone.utc) + timedelta(minutes=5)))
            require(http(base, nested_path + "/arrange", booking), 202, "book rejected replacement return")
            wait_for("follow-up return booked", lambda: nested_view()["status"] == "IN_PROGRESS")
            require(http(base, nested_path + "/collect", dict(eventId="nested-collect-" + sid,
                         occurredAt=timestamp(), actor="p5-receiver", collector="demo-collector",
                         bookingReference="NESTED-" + sid, quantityCollected=quantity)), 202, "collect rejected replacement")
            nested = wait_for("follow-up return complete", lambda: c if (c := nested_view())["status"] == "COMPLETED" else None)
            followup = dict(scenario="replacement-rejected-followup-return", correctiveId=nested_id, case=nested,
                            investigation=child_case.incident(),
                            events=require(http(base, child_case.url + "/events"), 200, "follow-up journal"),
                            engineRecords=dict(p5=engine_end(es, nested["processInstanceKey"], "FulfilmentCompleted")))
    collection["occurredAt"] = timestamp()
    collected = require(http(base, path + "/collect", collection), 202, "acknowledge original collection")
    terminal = "COMPLETED" if outcome in (None, "ADMITTED") else "FAILED"
    final = wait_for("P5 " + terminal, lambda: c if (c := get_case())["status"] == terminal else None)
    assert require(http(base, path + "/collect", collection), 202, "collection retry after completion") == collected
    assert require(http(base, path + "/arrange", arrangement), 202, "arrangement retry after completion") == accepted
    original_final = original.get()
    assert original_final["qualityStatus"] == "REJECTED" and original_final["admissionStatus"] == "REJECTED"
    ends = dict(p5=engine_end(es, final["processInstanceKey"], "FulfilmentCompleted" if terminal == "COMPLETED" else "FulfilmentFailed"),
                originalP3=engine_end(es, original_final["p3ProcessInstanceKey"], "GoodsRejected"),
                originalP2=engine_end(es, original_final["p2ProcessInstanceKey"], "TransportCompleted"), replacement=child_ends)
    return dict(scenario=scenario, correctiveId=cid, case=final, original=original_final,
                replacement=replacement, investigation=original.incident(), refusedActions=refused,
                events=require(http(base, original.url + "/events"), 200, "original journal"),
                replacementEvents=require(http(base, "/shipments/" + replacement_id + "/events"), 200, "replacement journal") if replacement else [],
                engineRecords=ends, followup=followup)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost:5001")
    parser.add_argument("--elasticsearch", default="http://localhost:9200")
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/p5-compliance/evidence.json")
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("Evidence output already exists; preserve it and select a new --output")
    health = require(http(args.base, "/health"), 200, "health")
    assert "CorrectiveFulfilment" in health.get("processIds", [])
    assert health["integrations"]["gps"] == "scripted-demo"
    assert health["integrations"]["containerEvaluation"] == "siddhi"
    assert health["integrations"]["p4IncidentHandoff"] == "enabled"
    stock = require(http(args.base, "/stock"), 200, "fresh warehouse")
    receiving = require(http(args.base, "/receiving-stock"), 200, "fresh receiving inventory")
    assert stock["datasetVersion"] == 3 and stock["shipmentCount"] == 0
    assert all(p["available"] == 20 for p in stock["products"]) and receiving["available"] == 0
    paths = [*sorted((ROOT / "deployer").glob("*.py")), *sorted((ROOT / "bpmn").glob("*.bpmn")),
             ROOT / "dcr/p4_quality_investigation.xml", ROOT / "siddhi-apps/ColdChainTelemetry.siddhi", Path(__file__),
             ROOT / "compliance/p5/rules.xml", ROOT / "compliance/p5/build_p5_evidence.py", ROOT / "docker-compose.yml",
             ROOT / "deployer/catalog.json", ROOT / "deployer/requirements.txt"]
    evidence = dict(source=SOURCE, status="running", runId=uuid.uuid4().hex[:8], capturedAt=timestamp(),
                    testedCommit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                    sourceHashes={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
                    health=health, warehouseBefore=stock, receivingBefore=receiving, cases=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        args.output.write_text(json.dumps(evidence, indent=2) + "\n")
    save()
    started = time.monotonic()
    try:
        for index, (name, definition) in enumerate(SCENARIOS.items(), 1):
            evidence["cases"].append(run_case(args.base, args.elasticsearch, evidence["runId"], index, name, definition))
            save()
            print("PASS", name, flush=True)
        after = require(http(args.base, "/stock"), 200, "final warehouse")
        receiving_after = require(http(args.base, "/receiving-stock"), 200, "final receiving")
        assert after["available"] == stock["available"] - 25
        assert receiving_after["available"] == 1
        expected = {"PRD-001": 19, "PRD-002": 18, "PRD-003": 18, "PRD-004": 0, "PRD-005": 20}
        assert {p["productId"]: p["available"] for p in after["products"]} == expected
        evidence.update(status="passed", warehouseAfter=after, receivingAfter=receiving_after,
                        durationSeconds=round(time.monotonic() - started), finishedAt=timestamp())
    except Exception as error:
        evidence.update(status="failed", error=str(error), finishedAt=timestamp())
        raise
    finally:
        save()
    print(json.dumps(dict(status=evidence["status"], runId=evidence["runId"], output=str(args.output)), indent=2))


if __name__ == "__main__":
    main()
