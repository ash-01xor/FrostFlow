"""Real P1/P2 engine scenarios and DCR replay. Requires fresh inventory/vehicles.

Run with the project's Python environment. Results go to ignored runtime/ by
default; copy reviewed evidence into compliance/p1 for a submission snapshot.
"""

import argparse
import asyncio
import hashlib
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from compliance.check import export_xes, replay


def http(url, data=None):
    req = Request(
        url,
        data=None if data is None else json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        response = urlopen(req, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


def wait(description, operation):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        result = operation()
        if result:
            return result
        time.sleep(1)
    raise RuntimeError("Timed out: " + description)


def engine_records(key, element, intent=None):
    filters = [
        {"term": {"value.processInstanceKey": int(key)}},
        {"term": {"value.elementId": element}},
    ]
    if intent:
        filters.append({"term": {"intent": intent}})
    status, result = http(
        "http://localhost:9200/zeebe-record_process-instance*/_search",
        {
            "query": {"bool": {"filter": filters}},
            "sort": [{"position": "desc"}],
            "size": 1,
        },
    )
    return result["hits"]["hits"] if status == 200 else []


async def duplicate_dispatch(shipment_id, event):
    import grpc
    from pyzeebe import ZeebeClient

    async with grpc.aio.insecure_channel("localhost:26501") as channel:
        client = ZeebeClient(grpc_channel=channel)
        # A new ID deliberately bypasses message-ID deduplication. Active-start
        # correlation must still prevent another P2; TTL 0 avoids later buffering.
        await client.publish_message(
            name="ShipmentDispatched",
            correlation_key=shipment_id,
            message_id="duplicate-test-" + uuid.uuid4().hex,
            time_to_live_in_milliseconds=0,
            variables={"shipmentId": shipment_id, "dispatchEvent": event},
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "runtime/verification-results"
    )
    args = parser.parse_args()
    base = "http://localhost:5001"
    model = ROOT / "compliance/p1/rules.xml"
    assert http(base + "/health")[1].get("stage") == 2, "Start the updated stack first"
    stock = http(base + "/stock")[1]
    assert (
        stock.get("datasetVersion") == 3
        and stock["shipmentCount"] == 0
        and len(stock["products"]) == 5
        and all(p["available"] == 20 for p in stock["products"])
    ), "Use fresh five-product data (20 crates each, no shipments, vehicles free)"

    def milk_available():
        return next(
            p["available"]
            for p in http(base + "/stock")[1]["products"]
            if p["productId"] == "PRD-001"
        )

    run = uuid.uuid4().hex[:8]
    cases = []

    def create(number, vehicle, quantity):
        sid = f"p1-{run}-{number}"
        url = base + "/shipments/" + sid
        data = dict(
            shipmentId=sid,
            journeyId=f"J-{run}-{number}",
            vehicleId=vehicle,
            recipientId="C-01",
            routeId="route-01",
            quantity=quantity,
            productId="PRD-001",
            batchId="DEMO-MILK-001",
            deliveryDeadline="2026-12-01T12:00:00Z",
        )
        status, record = http(base + "/shipments", data)
        assert status == 202, record
        assert http(base + "/shipments", data)[0] == 409
        cases.append((sid, url, record["processInstanceKey"]))
        return sid, url, record["processInstanceKey"]

    def state(url, expected):
        wait(expected, lambda: http(url)[1]["state"] == expected)

    def finish(url):
        assert http(url + "/dispatch", {"actor": "dispatcher-demo"})[0] == 409
        assert (
            http(
                url + "/verify-loading",
                dict(
                    actor="warehouse-demo",
                    quantityChecked=True,
                    packagingChecked=True,
                    vehicleReady=True,
                ),
            )[0]
            == 202
        )
        state(url, "AWAITING_DISPATCH")
        assert http(url + "/dispatch", {"actor": "dispatcher-demo"})[0] == 202
        state(url, "DISPATCHED")
        wait(
            "P2 acknowledgement",
            lambda: http(url)[1].get("handoffStatus") == "ACKNOWLEDGED",
        )
        assert http(url + "/dispatch", {"actor": "dispatcher-demo"})[0] == 409
        record = http(url)[1]
        wait(
            "P1 engine completion",
            lambda: engine_records(
                record["processInstanceKey"], "ShipmentPreparation", "ELEMENT_COMPLETED"
            ),
        )
        wait(
            "P2 waiting for arrival",
            lambda: engine_records(
                record["p2ProcessInstanceKey"],
                "WaitForShipmentArrived",
                "ELEMENT_ACTIVATED",
            ),
        )
        record = http(url)[1]
        assert record["transportState"] == "IN_TRANSIT", record
        assert record["vehicleReleased"] is False, record
        for field, expected in (("productId", "PRD-001"), ("batchId", "DEMO-MILK-001")):

            def exported_identity():
                status, result = http(
                    "http://localhost:9200/zeebe-record_variable*/_search",
                    {
                        "query": {
                            "bool": {
                                "filter": [
                                    {
                                        "term": {
                                            "value.processInstanceKey": int(
                                                record["p2ProcessInstanceKey"]
                                            )
                                        }
                                    },
                                    {"term": {"value.name": field}},
                                    {"term": {"value.value": json.dumps(expected)}},
                                ]
                            }
                        },
                        "size": 1,
                    },
                )
                return status == 200 and result["hits"]["hits"]

            wait(f"P2 {field} exported", exported_identity)
        return http(url)[1]

    def assert_single_active_p2(sid, url, record):
        event = next(
            e
            for e in http(url + "/events")[1]
            if e["eventType"] == "ShipmentDispatched" and e["process"] == "P1"
        )
        asyncio.run(duplicate_dispatch(sid, event))
        time.sleep(2)
        status, result = http(
            "http://localhost:9200/zeebe-record_variable*/_search",
            {
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"value.name": "shipmentId"}},
                            {"term": {"value.value": json.dumps(sid)}},
                            {"term": {"value.bpmnProcessId": "ShipmentTransport"}},
                        ]
                    }
                },
                "size": 100,
            },
        )
        assert status == 200, result
        keys = {
            str(h["_source"]["value"]["processInstanceKey"])
            for h in result["hits"]["hits"]
        }
        assert keys == {record["p2ProcessInstanceKey"]}, keys
        assert (
            sum(
                e["eventType"] == "TransportStarted"
                for e in http(url + "/events")[1]
            )
            == 1
        )

    def complete_transport(sid, url, record, other=None):
        p2_key = record["p2ProcessInstanceKey"]
        timeline = datetime.now(timezone.utc).replace(microsecond=0)
        arrival = {
            "eventId": f"arrival-{run}-{sid}",
            "occurredAt": (timeline + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            "recipientId": record["recipientId"],
        }
        receipt = {
            "eventId": f"receipt-{run}-{sid}",
            "occurredAt": (timeline + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
            "recipientId": record["recipientId"],
            "quantityReceived": record["quantity"],
            "receiptReference": f"RCPT-{run}-{sid}",
        }
        assert http(url + "/acknowledge-receipt", receipt)[0] == 409
        for _ in range(2):
            assert http(url + "/arrive", arrival)[0] == 202
        arrived = wait(
            f"{sid} reaches receipt wait",
            lambda: (
                current
                if (current := http(url)[1]).get("transportState") == "ARRIVED"
                else None
            ),
        )
        assert arrived["vehicleReleased"] is False, arrived
        wait(
            f"{sid} receipt catch activated",
            lambda: engine_records(
                p2_key, "WaitForReceiptAcknowledged", "ELEMENT_ACTIVATED"
            ),
        )
        wait(
            f"{sid} arrival catch completed",
            lambda: engine_records(
                p2_key, "WaitForShipmentArrived", "ELEMENT_COMPLETED"
            ),
        )
        if other is not None:
            other_url, other_key = other
            other_record = http(other_url)[1]
            assert other_record["transportState"] == "IN_TRANSIT", other_record
            assert other_record["vehicleReleased"] is False, other_record
            wait(
                "independent shipment remains at arrival catch",
                lambda: engine_records(
                    other_key, "WaitForShipmentArrived", "ELEMENT_ACTIVATED"
                ),
            )
        for _ in range(2):
            assert http(url + "/acknowledge-receipt", receipt)[0] == 202
        completed = wait(
            f"{sid} transport completion and vehicle release",
            lambda: (
                current
                if (current := http(url)[1]).get("transportState") == "COMPLETED"
                and current.get("vehicleReleased") is True
                else None
            ),
        )
        wait(
            f"{sid} P2 end event completion",
            lambda: engine_records(p2_key, "TransportCompleted", "ELEMENT_COMPLETED"),
        )
        events = http(url + "/events")[1]
        counts = {
            name: sum(event["eventType"] == name for event in events)
            for name in (
                "ShipmentArrived",
                "ReceiptAcknowledged",
                "VehicleReleased",
            )
        }
        assert counts == {
            "ShipmentArrived": 1,
            "ReceiptAcknowledged": 1,
            "VehicleReleased": 1,
        }, counts
        return {
            "shipmentId": sid,
            "p2ProcessInstanceKey": p2_key,
            "transportState": completed["transportState"],
            "vehicleReleased": completed["vehicleReleased"],
            "activityCounts": counts,
            "events": [event for event in events if event["process"] == "P2"],
        }

    first, first_url, _ = create(1, "V-01", 15)
    state(first_url, "AWAITING_LOADING")
    first_record = finish(first_url)
    assert_single_active_p2(first, first_url, first_record)
    cancelled, cancel_url, cancel_key = create(2, "V-02", 10)
    state(cancel_url, "WAITING_STOCK")
    wait(
        "stock gateway",
        lambda: engine_records(cancel_key, "WaitForStockDecision", "ELEMENT_ACTIVATED"),
    )
    assert (
        http(
            cancel_url + "/verify-loading",
            dict(
                actor="warehouse-demo",
                quantityChecked=True,
                packagingChecked=True,
                vehicleReady=True,
            ),
        )[0]
        == 409
    )
    assert http(cancel_url + "/dispatch", {"actor": "dispatcher-demo"})[0] == 409
    assert http(cancel_url + "/cancel", {"actor": "dispatcher-demo"})[0] == 202
    state(cancel_url, "CANCELLED")
    assert http(cancel_url + "/dispatch", {"actor": "dispatcher-demo"})[0] == 409
    assert http(cancel_url + "/cancel", {"actor": "dispatcher-demo"})[0] == 409
    assert http(first_url + "/cancel", {"actor": "dispatcher-demo"})[0] == 409
    wait(
        "cancelled P1 engine completion",
        lambda: engine_records(cancel_key, "ShipmentPreparation", "ELEMENT_COMPLETED"),
    )
    recovered, recovery_url, recovery_key = create(3, "V-02", 10)
    state(recovery_url, "WAITING_STOCK")
    initial_wait = http(recovery_url)[1]["stockWaitKey"]
    payload = dict(actor="warehouse-demo", eventId=f"replenish-{run}-1", quantity=1)
    for _ in range(2):
        assert http(recovery_url + "/replenish-stock", payload)[0] == 202
    wait(
        "insufficient replenishment recheck",
        lambda: http(recovery_url)[1]["stockWaitKey"] != initial_wait,
    )
    state(recovery_url, "WAITING_STOCK")
    assert milk_available() == 6, "Duplicate supply must not add crates twice"
    payload.update(eventId=f"replenish-{run}-2", quantity=4)
    assert http(recovery_url + "/replenish-stock", payload)[0] == 202
    state(recovery_url, "AWAITING_LOADING")
    assert http(recovery_url + "/cancel", {"actor": "dispatcher-demo"})[0] == 409
    recovery_record = finish(recovery_url)
    assert_single_active_p2(recovered, recovery_url, recovery_record)
    assert milk_available() == 0
    p2_evidence = []
    p2_evidence.append(
        complete_transport(
            first,
            first_url,
            first_record,
            other=(recovery_url, recovery_record["p2ProcessInstanceKey"]),
        )
    )
    p2_evidence.append(complete_transport(recovered, recovery_url, recovery_record))

    # Both vehicles have completed transport. Reusing V-01 for a new shipment
    # must now succeed; milk is exhausted, so cancel its stock-waiting case.
    reuse_id, reuse_url, reuse_key = create(4, "V-01", 10)
    state(reuse_url, "WAITING_STOCK")
    reuse_record = http(reuse_url)[1]
    assert reuse_record["vehicleId"] == "V-01"
    assert reuse_record["vehicleReleased"] is False
    assert http(reuse_url + "/cancel", {"actor": "dispatcher-demo"})[0] == 202
    state(reuse_url, "CANCELLED")
    assert http(reuse_url)[1]["vehicleReleased"] is True

    assert http(base + "/stock")[1]["available"] == 80
    assert all(
        p["available"] == 20
        for p in http(base + "/stock")[1]["products"]
        if p["productId"] != "PRD-001"
    )
    logs = {
        sid: [e for e in http(url + "/events")[1] if e["process"] == "P1"]
        for sid, url, key in cases
    }
    for sid, url, key in cases:
        record = http(url)[1]
        assert all(
            e["payload"]["productId"] == "PRD-001"
            and e["payload"]["batchId"] == "DEMO-MILK-001"
            for e in logs[sid]
        )
        if sid in {cancelled, reuse_id}:
            assert "p2ProcessInstanceKey" not in record
            continue
    results = {sid: replay(model, events) for sid, events in logs.items()}
    assert all(r["valid"] and r["accepting"] for r in results.values()), results
    invalid = json.loads((ROOT / "compliance/p1/invalid-trace.json").read_text())
    invalid_result = replay(model, invalid["events"])
    assert not invalid_result["valid"]
    args.output.mkdir(parents=True, exist_ok=True)
    export_xes(args.output / "execution-log.xes", logs)
    report = dict(
        observedAt=datetime.now(timezone.utc).isoformat(),
        evidenceType="real Zeebe execution and application activity logs",
        datasetVersion=3,
        initialInventory=stock,
        finalInventory=http(base + "/stock")[1],
        modelSha256=hashlib.sha256(model.read_bytes()).hexdigest(),
        bpmnSha256={
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT / "bpmn").glob("*.bpmn"))
        },
        sourceSha256={
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "deployer/app.py",
                "deployer/registry.py",
                "deployer/catalog.json",
                "compliance/check.py",
                "tools/demo.py",
                "stage_tests/check_stage1.py",
                "deployer/requirements.txt",
                "docker-compose.yml",
            )
        },
        executionLogSha256=hashlib.sha256(
            (args.output / "execution-log.xes").read_bytes()
        ).hexdigest(),
        cases={
            sid: {
                field: value
                for field, value in http(url)[1].items()
                if field
                in {
                    "state",
                    "processInstanceKey",
                    "p2ProcessInstanceKey",
                    "handoffStatus",
                    "stockAttempt",
                }
            }
            for sid, url, key in cases
        },
        compliance=results,
        deliberatelyInvalidTrace=invalid_result,
        checks=[
            "shortage wait",
            "premature actions blocked",
            "cancellation ends P1 and releases vehicle",
            "insufficient supply rechecks",
            "replenishment deduplicated",
            "product/batch identity exported to P2 and P1 activities",
            "other four products retain 20 crates each",
            "two dispatched P1 completions",
            "two distinct P2 instances completed only after receipt",
            "duplicate P2 start suppressed",
            "early receipt rejected for both shipments",
            "arrival and receipt exact retries recorded once",
            "P2 reaches its end event and releases each vehicle",
            "released vehicle can be reserved by a new shipment",
            "actual P1 logs conform and are accepting",
            "deliberately invalid trace rejected",
        ],
        processDoctor="Not run; import rules.xml and execution-log.xes for course-tool confirmation",
    )
    (args.output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    p2_report = dict(
        observedAt=datetime.now(timezone.utc).isoformat(),
        evidenceType="live Zeebe P2 execution using manual/test event endpoints",
        datasetVersion=3,
        cases=p2_evidence,
        compliance="P2 DCR model/log verification is outstanding; this is execution evidence only.",
    )
    (args.output / "p2-completion.json").write_text(
        json.dumps(p2_report, indent=2) + "\n"
    )
    print(
        "PASS: P1 stock wait/recheck/cancellation and DCR replay; two independent P2 completions, retries and vehicle reuse"
    )
    print(json.dumps(results, indent=2))
    print("P1 compliance evidence:", args.output / "execution-log.xes")
    print("P2 execution evidence:", args.output / "p2-completion.json")


if __name__ == "__main__":
    main()
