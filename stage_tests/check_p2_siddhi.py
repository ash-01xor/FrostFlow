"""Live P2 acceptance check through the local Siddhi app.

Run against a fresh dataset with the full Compose stack (including the
``siddhi`` service) and a short GPS tick, for example GPS_TICK_SECONDS=10.

Two shipments travel at the same time:

* A has a one-minute deadline. Its first late ETA must reach Siddhi, come back
  as a SIDDHI:RISK alert, wait for a dispatcher decision, and an approved route
  change must be acknowledged through Siddhi before GPS/ETA use the new route.
* B has a generous deadline and must complete without any risk alert.

Both must stay ARRIVED (vehicle reserved) until receipt is acknowledged, then
complete and release their vehicles. Evidence is written to
runtime/verification-results/p2-siddhi.json.
"""

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = "http://localhost:5001"
EVIDENCE = Path("runtime/verification-results/p2-siddhi.json")
ROUTES = ("route-01", "route-02", "route-03")


def utc(delta=timedelta()):
    return (datetime.now(timezone.utc) + delta).isoformat().replace("+00:00", "Z")


def http(path, data=None):
    request = Request(
        BASE + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        response = urlopen(request, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read()
        return response.status, json.loads(raw) if raw else {}


def get(shipment_id):
    status, record = http(f"/shipments/{shipment_id}")
    assert status == 200, record
    return record


def events(shipment_id):
    status, result = http(f"/shipments/{shipment_id}/events")
    assert status == 200, result
    return result


def post(path, data):
    status, result = http(path, data)
    assert status == 202, f"{path} -> HTTP {status}: {result}"
    return result


def wait(description, predicate, timeout=120):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {description}")


def prepare(shipment_id, vehicle, recipient, route, product, deadline):
    post("/shipments", dict(
        shipmentId=shipment_id, journeyId="journey-" + shipment_id,
        vehicleId=vehicle, recipientId=recipient, routeId=route, quantity=5,
        productId=product["productId"], batchId=product["batchId"],
        deliveryDeadline=deadline,
    ))
    wait(f"{shipment_id} loading", lambda: get(shipment_id)["state"] == "AWAITING_LOADING")
    post(f"/shipments/{shipment_id}/verify-loading", dict(
        actor="warehouse-check", quantityChecked=True,
        packagingChecked=True, vehicleReady=True,
    ))
    wait(f"{shipment_id} dispatch-ready",
         lambda: get(shipment_id)["state"] == "AWAITING_DISPATCH")
    post(f"/shipments/{shipment_id}/dispatch", dict(actor="dispatcher-check"))
    wait(f"{shipment_id} handoff",
         lambda: get(shipment_id).get("handoffStatus") == "ACKNOWLEDGED")


def acknowledge_receipt(record):
    shipment_id = record["shipmentId"]
    post(f"/shipments/{shipment_id}/acknowledge-receipt", dict(
        eventId=f"check-receipt-{shipment_id}", occurredAt=utc(),
        recipientId=record["recipientId"], quantityReceived=record["quantity"],
        receiptReference=f"CHECK-{shipment_id}",
    ))
    return wait(
        f"{shipment_id} completion",
        lambda: (lambda r: r if r.get("transportState") == "COMPLETED"
                 and r.get("vehicleReleased") is True else None)(get(shipment_id)),
    )


def main():
    status, health = http("/health")
    assert status == 200 and health.get("status") == "ready", health
    integrations = health.get("integrations", {})
    assert integrations.get("siddhiTelemetry") == "enabled", integrations
    assert integrations.get("siddhiCommands") == "enabled", integrations
    assert integrations.get("gps") == "scripted-demo", integrations
    status, stock = http("/stock")
    assert stock.get("datasetVersion") == 3 and stock.get("shipmentCount") == 0, (
        "use a fresh dataset; see docs/RUNNING.md"
    )
    products = stock["products"]
    run = datetime.now(timezone.utc).strftime("%H%M%S")
    a, b = f"S-SID-A-{run}", f"S-SID-B-{run}"

    prepare(a, "V-01", "C-01", "route-01", products[0], utc(timedelta(minutes=1)))
    prepare(b, "V-02", "C-02", "route-02", products[1], utc(timedelta(minutes=60)))
    print(f"Dispatched {a} (short deadline) and {b} (generous deadline)")

    # 1. Siddhi detects A's late ETA and P2 waits for a dispatcher decision.
    risk_id = wait("Siddhi risk alert for A", lambda: get(a).get("pendingDeliveryRisk"))
    assert risk_id.startswith("SIDDHI:RISK:P2:" + a + ":ETAUpdated:0:"), risk_id
    wait("P2 DeliveryRiskDetected activity", lambda: any(
        e["eventType"] == "DeliveryRiskDetected" and e.get("producerEventId") == risk_id
        for e in events(a)))
    print(f"Risk alert from Siddhi recorded by P2: {risk_id}")

    # 2. Approve an alternative route; the acknowledgement must come back through Siddhi.
    before = get(a)
    target = next(r for r in ROUTES if r != before["routeId"])
    decision_id = f"check-decision-{risk_id}"
    post(f"/shipments/{a}/dispatcher-decision", dict(
        eventId=decision_id, decision="APPROVE_ALTERNATIVE_ROUTE", routeId=target))
    ack_event = wait("RouteChangeAcknowledged", lambda: next(
        (e for e in events(a) if e["eventType"] == "RouteChangeAcknowledged"), None))
    ack = ack_event["payload"]["simulatorAcknowledgement"]
    assert ack.get("commandId") == decision_id and ack.get("simulatorAcknowledged") is True, ack
    assert "messageId" in ack and ack.get("mode") != "local-demo", (
        f"acknowledgement did not pass through Siddhi's http-service bridge: {ack}")
    changed = get(a)
    assert changed["routeId"] == target and changed.get("routeRevision") == 1, changed
    print(f"Route change {before['routeId']} -> {target} acknowledged via Siddhi")

    # 3. Later telemetry follows the new route revision.
    wait("GPS/ETA on the new route", lambda: (
        any(e["eventType"] == "GPSPositionUpdated" and e["eventId"].startswith(f"P2:{a}:GPSPositionUpdated:1:")
            and e["payload"]["routeId"] == target for e in events(a))
        and any(e["eventType"] == "ETAUpdated" and e["eventId"].startswith(f"P2:{a}:ETAUpdated:1:")
                and e["payload"]["routeId"] == target for e in events(a))))
    print("GPS and ETA updates now use the new route revision")

    # 4. Further late ETAs on the new revision may raise one more alert; accept the delay.
    accepted = []

    def arrived_a():
        record = get(a)
        pending = record.get("pendingDeliveryRisk")
        if pending and pending not in accepted:
            post(f"/shipments/{a}/dispatcher-decision", dict(
                eventId=f"check-decision-{pending}", decision="ACCEPT_DELAY"))
            accepted.append(pending)
        return record if record.get("transportState") == "ARRIVED" else None

    for shipment_id, check in ((a, arrived_a), (b, None)):
        record = wait(f"{shipment_id} automatic arrival", check or (
            lambda s=shipment_id: (lambda r: r if r.get("transportState") == "ARRIVED" else None)(get(s))),
            timeout=180)
        time.sleep(2)
        record = get(shipment_id)
        assert record["transportState"] == "ARRIVED" and record["vehicleReleased"] is False, (
            f"{shipment_id} must wait for receipt: {record}")
    print("Both shipments arrived automatically and are blocked until receipt")

    completed = {s: acknowledge_receipt(get(s)) for s in (a, b)}
    b_events = events(b)
    assert not any(e["eventType"] == "DeliveryRiskDetected" for e in b_events), "B raised a risk"
    print("Both transports completed after receipt; vehicles released")

    evidence = {
        "checkedAt": utc(),
        "health": health,
        "shipments": {s: {"record": completed[s], "events": events(s)} for s in (a, b)},
        "riskAlertFromSiddhi": risk_id,
        "routeChange": {"from": before["routeId"], "to": target, "acknowledgement": ack},
        "additionalAcceptedDelays": accepted,
    }
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2))
    print(f"PASS - evidence written to {EVIDENCE}")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as error:
        print("FAIL:", error)
        sys.exit(1)
