"""Interactive demonstration controls for P1–P5.

Uses the application's HTTP API; no Docker commands or data resets are performed.
Run from the repository root: python tools/demo.py
"""

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

CASES = {
    "A": ("demo-A", "V-01", 15),
    "B": ("demo-B", "V-02", 10),
    "C": ("demo-C", "V-02", 10),
}
LOCATIONS = {
    "START_REQUESTED": "P1 is starting; refresh shortly.",
    "STOCK_RESERVED": "P1 has reserved stock and is continuing preparation.",
    "STOCK_ALLOCATED": "P1 is continuing to transport assignment.",
    "AWAITING_LOADING": "In Operate, find P1 waiting at 'Await loading verification'.",
    "WAITING_STOCK": "In Operate, find P1 at 'Await stock or cancellation'.",
    "AWAITING_DISPATCH": "In Operate, find P1 waiting at 'Await dispatch approval'.",
    "DISPATCHED": "P1 dispatched; waiting for P2 to acknowledge the handoff.",
    "CANCELLED": "P1 should finish at 'Shipment cancelled'; no P2 is started.",
}
TRANSPORT_LOCATIONS = {
    "AWAITING_JOURNEY_START": "P2 is registering the transport handoff.",
    "IN_TRANSIT": "In Operate, P2 should be waiting at 'Wait for shipment arrival'.",
    "ARRIVED": "In Operate, P2 should be waiting at 'Wait for receipt acknowledgement'; the vehicle is still reserved.",
    "COMPLETED": "P2 has completed after receipt acknowledgement; the vehicle is released.",
}


def http(base, path, data=None):
    request = Request(
        base + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        response = urlopen(request, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        body = response.read()
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {
                "error": body.decode("utf-8", errors="replace").strip()
                or f"HTTP {response.status} returned no JSON response"
            }
        return response.status, payload


def show(record):
    print(f"\nShipment: {record['shipmentId']} | State: {record['state']}")
    print(
        f"Goods: {record['quantity']} crates of {record['productName']} "
        f"({record['productId']}, batch {record['batchId']})"
    )
    print(f"P1 instance key: {record.get('processInstanceKey') or 'not assigned yet'}")
    session = record.get("monitoring")
    print("Container monitoring:", json.dumps(session or {"state": "legacy/unmonitored"}, indent=2))
    print("Container reading fresh:", record.get("containerReadingFresh", False))
    print("Monitoring profile:", record.get("monitoringProfile", "legacy/unmonitored"))
    print("Outdoor conditions:", record.get("weather", "unavailable"))
    print("Assessment:", record.get("assessmentReasons", []), record.get("assessmentPriority"),
          "evidence version", record.get("evidenceVersion", 0))
    if record.get("p2ProcessInstanceKey"):
        print(f"P2 instance key: {record['p2ProcessInstanceKey']}")
        print(f"Dispatch handoff: {record.get('handoffStatus')}")
        print(f"P2 transport state: {record.get('transportState', 'not started')}")
        print(f"Vehicle released: {record.get('vehicleReleased', False)}")
        print(f"Current route: {record.get('routeId')}")
        if record.get("currentPosition"):
            print(f"Current position: {record['currentPosition']} | ETA: {record.get('eta')} ({record.get('etaSource')})")
        if record.get("pendingDeliveryRisk"):
            print(f"Dispatcher decision pending for alert: {record['pendingDeliveryRisk']}")
    if record.get("p3ProcessInstanceKey") or record.get("p3Status"):
        print(f"P3 instance key: {record.get('p3ProcessInstanceKey') or 'starting'}")
        receiving_status = record.get("p3Status", "starting")
        print(
            f"P3 receiving status: {receiving_status} | "
            f"Inspection: {record.get('inspectionStatus', 'pending')} | "
            f"Admission: {record.get('admissionStatus', 'pending')}"
        )
        if receiving_status == "AWAITING_RECEIPT":
            print("Next P3 action: choose 14 to confirm physical receipt.")
        elif receiving_status == "AWAITING_INSPECTION":
            print("Next P3 action: choose 15 to inspect the goods.")
        elif receiving_status == "ON_HOLD":
            print("P3 is waiting for a quality disposition before stock admission.")
            print("Next action: choose 17 to release the current quality incident (P4 stand-in).")
    if record.get("qualityStatus") and record.get("qualityStatus") != "CLEAR":
        print(
            f"Quality status: {record['qualityStatus']} | "
            f"incident: {record.get('qualityIncidentId')}"
        )
    if record.get("pendingCommand"):
        print("The accepted action is still being processed.")
    hint = TRANSPORT_LOCATIONS.get(record.get("transportState")) or LOCATIONS.get(
        record["state"], "Inspect the process in Operate."
    )
    print(hint)
    print("Operate may take a few seconds to display the latest engine records.")


def manual_event_time(base, shipment_id, minutes_after_creation):
    """Use a stable simulated timestamp so retrying the same event is exact."""
    status, events = http(base, f"/shipments/{shipment_id}/events")
    if status != 200:
        raise RuntimeError(events.get("error", "Cannot read shipment events"))
    created = next(
        (event for event in events if event.get("eventType") == "ShipmentCreated"),
        None,
    )
    if created is None:
        raise RuntimeError("ShipmentCreated activity is not available yet")
    occurred_at = datetime.fromisoformat(
        created["occurredAt"].replace("Z", "+00:00")
    ) + timedelta(minutes=minutes_after_creation)
    return occurred_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def wait_for_action(base, shipment_id, action, old_wait_key=None, target_route_id=None):
    end = time.monotonic() + 45
    while time.monotonic() < end:
        status, record = http(base, f"/shipments/{shipment_id}")
        if status != 200:
            raise RuntimeError(record.get("error", "Cannot read shipment"))
        if action == "create":
            finished = record["state"] in ("AWAITING_LOADING", "WAITING_STOCK")
        elif action == "dispatch":
            finished = record.get("handoffStatus") == "ACKNOWLEDGED"
        elif action == "verify-loading":
            finished = record["state"] == "AWAITING_DISPATCH"
        elif action == "arrive":
            finished = record.get("transportState") in ("ARRIVED", "COMPLETED")
        elif action == "acknowledge-receipt":
            finished = (
                record.get("transportState") == "COMPLETED"
                and record.get("vehicleReleased") is True
            )
        elif action == "delivery-risk":
            finished = bool(record.get("pendingDeliveryRisk")) or record.get(
                "transportState"
            ) in ("ARRIVED", "COMPLETED")
        elif action == "dispatcher-decision":
            if target_route_id:
                event_status, events = http(base, f"/shipments/{shipment_id}/events")
                finished = (
                    not record.get("pendingDeliveryRisk")
                    and record.get("routeId") == target_route_id
                    and record.get("routeIndex", 0) >= 1
                    and event_status == 200
                    and any(
                        event.get("eventType") == "RouteChangeAcknowledged"
                        and event.get("payload", {}).get("routeId") == target_route_id
                        for event in events
                    )
                )
            else:
                finished = not record.get("pendingDeliveryRisk")
        elif action == "receiving-receipt":
            finished = record.get("p3Status") in (
                "AWAITING_INSPECTION",
                "REJECTED",
            )
        elif action == "receiving-inspection":
            finished = record.get("p3Status") in (
                "ADMITTED",
                "ON_HOLD",
                "REJECTED",
            )
        elif action == "cancel":
            finished = record["state"] == "CANCELLED"
        else:
            finished = not record.get("pendingCommand") and (
                record["state"] == "AWAITING_LOADING"
                or record.get("stockWaitKey") != old_wait_key
            )
        if finished:
            show(record)
            return
        time.sleep(0.5)
    show(record)
    print(
        "Still waiting after 45 seconds. Inspect Operate and communication logs before retrying."
    )


def release_quality_hold(base, shipment_id):
    """Test stand-in: release the shipment's CURRENT quality incident.

    This bypasses the P4 DCR investigation (option 19) and is kept for P3-only
    checks. A later P4 decision for that incident is then recorded as stale.
    """
    status, record = http(base, f"/shipments/{shipment_id}")
    if status != 200:
        print("Not available:", record.get("error"))
        return
    incident = record.get("qualityIncidentId")
    if record.get("qualityStatus") != "PENDING" or not incident:
        print(f"No quality hold to release (quality status: {record.get('qualityStatus')}).")
        return
    status, response = http(base, "/integrations/p4/quality-status", {
        "shipmentId": shipment_id,
        "eventId": f"demo-release-{incident}",
        "occurredAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "qualityStatus": "RELEASED",
        "incidentId": incident,
        "expectedEvidenceVersion": record.get("evidenceVersion", 0),
    })
    if status != 202:
        print(f"\nAction rejected (HTTP {status}): {response.get('error', response)}")
        return
    print(f"Released incident {incident} (P4 stand-in). Waiting for P3...")
    end = time.monotonic() + 45
    while time.monotonic() < end:
        record = http(base, f"/shipments/{shipment_id}")[1]
        if record.get("p3Status") in ("ADMITTED", "REJECTED") or (
            record.get("qualityStatus") == "RELEASED" and record.get("p3Status") != "ON_HOLD"
        ):
            break
        time.sleep(0.5)
    show(record)


def investigate_quality(base, shipment_id):
    """P4: show the current DCR investigation and execute one enabled activity."""
    status, quality = http(base, f"/shipments/{shipment_id}/quality")
    if status != 200:
        print("Not available:", quality.get("error"))
        return
    incident_id = quality.get("qualityIncidentId")
    print(f"Quality status: {quality.get('qualityStatus')} | incident: {incident_id}")
    if not incident_id:
        print("No quality incident for this shipment.")
        return
    status, incident = http(base, f"/quality/incidents/{quote(incident_id, safe='')}")
    if status != 200:
        print("The investigation has not reached P4 yet:", incident.get("error"))
        return
    print(f"Investigation: {incident['status']} ({incident['dcrMode']}, case "
          f"{incident.get('dcrCaseId')})")
    if incident.get("decision"):
        print(f"Decision: {incident['decision']['decision']} by "
              f"{incident['decision']['officer']}")
    dcr = incident.get("dcr") or {}
    if dcr.get("unavailable"):
        print("DCR unavailable:", dcr["unavailable"])
        return
    enabled = [e["id"] for e in dcr.get("events", []) if e["enabled"]]
    pending = [e["id"] for e in dcr.get("events", []) if e["pending"] and e["included"]]
    if pending:
        print("Still required:", ", ".join(pending))
    choices = [e for e in enabled if e not in ("OpenIncident", "AddAlertEvidence")]
    if not choices:
        print("No officer activity is enabled now.")
        return
    for number, activity in enumerate(choices, start=1):
        print(f"  {number} {activity}")
    picked = input("Activity number (Enter = none): ").strip()
    if not picked:
        return
    if not picked.isdigit() or not 1 <= int(picked) <= len(choices):
        raise ValueError("Choose one of the listed activity numbers.")
    activity = choices[int(picked) - 1]
    data = {"actor": input("Actor [quality-officer]: ").strip() or "quality-officer"}
    if activity.startswith("Decide"):
        data["reason"] = input("Reason for the decision: ").strip()
    status, response = http(
        base,
        f"/quality/incidents/{quote(incident_id, safe='')}/activities/{activity}",
        data,
    )
    if status != 200:
        print(f"\nDCR refused (HTTP {status}): {response.get('error', response)}")
        if response.get("enabled"):
            print("Enabled now:", ", ".join(response["enabled"]))
        return
    print(f"{activity} executed in DCR.")
    show(http(base, f"/shipments/{shipment_id}")[1])


def choose_case(base=None):
    print("A = 15 crates / V-01 | B = 10 crates / V-02 | C = 10 crates / V-02")
    label = input("Shipment [A/B/C or existing shipment ID]: ").strip()
    if label.upper() in CASES:
        return CASES[label.upper()]
    if base:
        status, record = http(base, "/shipments/" + quote(label, safe=""))
        if status == 200:
            return record["shipmentId"], record["vehicleId"], record["quantity"]
    raise ValueError("Choose A, B, C or an existing shipment ID.")


def inventory(base):
    status, stock = http(base, "/stock")
    if status != 200 or stock.get("datasetVersion") != 3:
        raise RuntimeError(
            "Use the updated five-product application and a fresh dataset; see docs/RUNNING.md."
        )
    print(
        f"\nAvailable stock: {stock['available']} crates across five products (12 retail packs per crate)."
    )
    for index, product in enumerate(stock["products"], 1):
        print(
            f"{index}. {product['productId']} | {product['productName']} | "
            f"{product['available']} crates | batch {product['batchId']}"
        )
    return stock


def receiving_inventory(base):
    status, stock = http(base, "/receiving-stock")
    if status != 200:
        raise RuntimeError(stock.get("error", "Cannot read receiving inventory"))
    print(f"\nReceiving stock: {stock['available']} crates")
    if not stock["items"]:
        print("No goods have been admitted yet.")
    for item in stock["items"]:
        print(
            f"{item['recipientId']} | {item['productName']} "
            f"({item['productId']}, batch {item['batchId']}): "
            f"{item['available']} crates"
        )
    return stock


def perform(base, choice, deadline_minutes=60):
    if choice == "8":
        inventory(base)
        return
    if choice == "16":
        receiving_inventory(base)
        return
    shipment_id, vehicle, quantity = choose_case(base)
    path = f"/shipments/{shipment_id}"
    if choice == "18":
        scenario = input("Scenario (normal/isolated-spike/sustained-excursion/missing-telemetry): ").strip()
        status, result = http(base, path + "/simulation/temperature", {
            "commandId": "temperature-" + str(time.time_ns()), "actor": "demo-operator", "scenario": scenario})
        print(status, result)
        return
    if choice == "17":
        release_quality_hold(base, shipment_id)
        return
    if choice == "19":
        investigate_quality(base, shipment_id)
        return
    if choice == "20":
        corrective_fulfilment(base, shipment_id, deadline_minutes)
        return
    if choice in ("2", "7"):
        status, record = http(base, path if choice == "2" else path + "/events")
        if status != 200:
            print("Not available:", record.get("error"))
        elif choice == "2":
            show(record)
        else:
            for event in record:
                print(f"{event['process']}: {event['eventType']}")
        return
    old_wait_key = None
    if choice == "1":
        stock = inventory(base)
        selected = (
            input("Product [1-5, Enter=1 for milk shortage scenario]: ").strip() or "1"
        )
        if selected not in ("1", "2", "3", "4", "5"):
            raise ValueError("Choose a product from 1 to 5.")
        product = stock["products"][int(selected) - 1]
        action = "create"
        path = "/shipments"
        deadline = (
            datetime.now(timezone.utc) + timedelta(minutes=deadline_minutes)
        ).isoformat()
        data = dict(
            shipmentId=shipment_id,
            journeyId="journey-" + shipment_id,
            vehicleId=vehicle,
            recipientId="C-01",
            routeId="route-01",
            quantity=quantity,
            productId=product["productId"],
            batchId=product["batchId"],
            deliveryDeadline=deadline,
        )
    elif choice in ("9", "10", "11", "12", "13", "14", "15"):
        status, record = http(base, path)
        if status != 200:
            print("Not available:", record.get("error"))
            return
        if choice == "9":
            action = "arrive"
            data = {
                "eventId": f"demo-arrival-{shipment_id}",
                "occurredAt": manual_event_time(base, shipment_id, 5),
                "recipientId": record["recipientId"],
            }
        elif choice == "10":
            action = "acknowledge-receipt"
            data = {
                "eventId": f"demo-receipt-{shipment_id}",
                "occurredAt": manual_event_time(base, shipment_id, 10),
                "recipientId": record["recipientId"],
                "quantityReceived": record["quantity"],
                "receiptReference": f"DEMO-{shipment_id}",
            }
        elif choice == "14":
            action = "receiving-receipt"
            data = {
                "eventId": f"demo-physical-receipt-{shipment_id}",
                "occurredAt": manual_event_time(base, shipment_id, 15),
                "recipientId": record["recipientId"],
                "quantityReceived": record["quantity"],
                "receiver": "cafeteria-demo",
                "receiptReference": f"DEMO-{shipment_id}",
            }
        elif choice == "15":
            action = "receiving-inspection"
            data = {
                "eventId": f"demo-inspection-{shipment_id}",
                "occurredAt": manual_event_time(base, shipment_id, 20),
                "inspector": "quality-demo",
                "passed": True,
            }
        elif choice == "11":
            action = "delivery-risk"
            status, events = http(base, path + "/events")
            if status != 200:
                raise RuntimeError(events.get("error", "Cannot read shipment events"))
            episode = 1 + sum(event.get("eventType") == "DeliveryRiskDetected" for event in events)
            deadline = datetime.fromisoformat(record["deliveryDeadline"].replace("Z", "+00:00"))
            eta = (deadline + timedelta(seconds=3600)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            data = {
                "shipmentId": shipment_id,
                "eventId": f"demo-risk-{record['journeyId']}-episode-{episode}",
                "occurredAt": manual_event_time(base, shipment_id, 20 + episode),
                "eta": eta,
                "deliveryDeadline": record["deliveryDeadline"],
                "reason": "CLI simulated delivery delay",
                "estimatedDelaySeconds": 3600,
            }
            path = "/integrations/siddhi/delivery-risk"
        else:
            action = "dispatcher-decision"
            status, events = http(base, path + "/events")
            if status != 200:
                raise RuntimeError(events.get("error", "Cannot read shipment events"))
            latest_risk = next(
                (event.get("producerEventId") or event.get("eventId")
                 for event in reversed(events)
                 if event.get("eventType") == "DeliveryRiskDetected"),
                record["journeyId"],
            )
            data = {"eventId": f"demo-decision-{record.get('pendingDeliveryRisk') or latest_risk}"}
            if choice == "12":
                data["decision"] = "ACCEPT_DELAY"
            else:
                data["decision"] = "APPROVE_ALTERNATIVE_ROUTE"
                data["routeId"] = input("Alternative route [route-01/route-02/route-03]: ").strip()
    else:
        action = {
            "3": "verify-loading",
            "4": "dispatch",
            "5": "replenish-stock",
            "6": "cancel",
        }[choice]
        data = {
            "actor": "warehouse-demo" if choice in ("3", "5") else "dispatcher-demo"
        }
        if choice == "3":
            print(
                "For this simulation, confirming quantity, packaging and vehicle readiness."
            )
            data.update(quantityChecked=True, packagingChecked=True, vehicleReady=True)
        if choice == "5":
            status, record = http(base, path)
            if status != 200:
                print("Not available:", record.get("error"))
                return
            old_wait_key = record.get("stockWaitKey")
            # Stable within a wait episode, so repeating this action cannot add
            # another delivery after a lost reply or a demo-tool restart.
            data.update(
                quantity=5,
                eventId=f"demo-supply-{old_wait_key}",
                productId=record["productId"],
                batchId=record["batchId"],
            )
            print(
                f"Recording 5 crates of {record['productName']} for batch {record['batchId']}."
            )
    if choice in ("14", "15"):
        path += (
            "/receiving/receipt"
            if choice == "14"
            else "/receiving/inspection"
        )
    elif choice not in ("1", "11"):
        path += "/" + action
    status, response = http(base, path, data)
    if status != 202:
        print(f"\nAction rejected (HTTP {status}): {response.get('error', response)}")
        if choice == "10" and status == 409:
            print(
                "With P3 receiving enabled, P2 only completes after P3 records receipt: use 14."
            )
        if choice == "4" and status == 409:
            print(
                "This demonstrates the dispatch restriction: stock and loading steps must be complete."
            )
        return
    print("Action accepted. Waiting for the workflow to process it...")
    wait_for_action(
        base, shipment_id, action, old_wait_key,
        target_route_id=data.get("routeId") if choice == "13" else None,
    )


def corrective_fulfilment(base, shipment_id, deadline_minutes=60):
    status, cases = http(base, "/corrective-fulfilments?originalShipmentId=" + quote(shipment_id, safe=""))
    if status != 200 or not cases:
        print("No corrective case. P4 must apply a replacement or return decision first.")
        return
    case = cases[-1]
    path = "/corrective-fulfilments/" + case["correctiveId"]
    print(json.dumps(case, indent=2))
    action = input("P5 action (view/arrange/collect): ").strip().lower()
    if action == "view":
        return
    moment = datetime.now(timezone.utc)
    data = {"eventId": "p5-" + str(time.time_ns()),
            "occurredAt": moment.isoformat().replace("+00:00", "Z"), "actor": "demo-operator"}
    if action == "arrange":
        data.update(collector=input("Collector: ").strip(),
                    bookingReference=input("Collection booking reference: ").strip(),
                    scheduledAt=input("Scheduled collection (UTC ISO 8601): ").strip())
        if case["action"] == "REPLACE":
            replacement_id = input("New replacement shipment ID: ").strip()
            data["replacement"] = dict(
                shipmentId=replacement_id, journeyId="J-" + replacement_id,
                vehicleId=input("Free vehicle (V-01/V-02): ").strip(),
                routeId=input("Route (route-01/route-02): ").strip(),
                deliveryDeadline=(moment + timedelta(minutes=deadline_minutes)).isoformat().replace("+00:00", "Z"))
        expected = lambda c: c["status"] in ("IN_PROGRESS", "COMPLETED", "FAILED")
    elif action == "collect":
        booking = (case.get("arrangement") or {}).get("payload", {})
        data.update(collector=booking.get("collector", ""), bookingReference=booking.get("bookingReference", ""),
                    quantityCollected=int(input("Original crates physically collected: ")))
        expected = lambda c: c["originalGoodsStatus"] == "COLLECTED"
    else:
        raise ValueError("choose view, arrange or collect")
    status, result = http(base, path + "/" + action, data)
    print(status, json.dumps(result, indent=2))
    if status == 202:
        until = time.monotonic() + 30
        while time.monotonic() < until:
            code, current = http(base, path)
            if code == 200 and expected(current):
                print(json.dumps(current, indent=2))
                if current.get("replacementShipmentId"):
                    print("Use actions 3/4 and 14/15 on replacement", current["replacementShipmentId"],
                          "to verify loading, dispatch, receive and inspect through P1–P3.")
                return
            time.sleep(0.5)
        print("P5 input remains accepted. Inspect the case and Operate before retrying.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:5001")
    parser.add_argument(
        "--deadline-minutes", type=int, default=60,
        help="delivery deadline offset for new demo shipments (short values trigger Siddhi risk)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check readiness and inventory without changing data",
    )
    args = parser.parse_args()
    if args.deadline_minutes <= 0:
        parser.error("--deadline-minutes must be greater than zero")
    base = args.base_url.rstrip("/")
    try:
        status, health = http(base, "/health")
        if status != 200 or health.get("stage") != 3:
            print(
                "The updated application is not ready. Start the stack and check its logs."
            )
            return 1
        stock = inventory(base)
        print("Operate: http://localhost:8081 | login: demo / demo")
        print(
            "These controls use simulated orders and human confirmations; the workflow runs in Zeebe."
        )
        if stock["shipmentCount"]:
            print(
                "This dataset has been used. Reuse its existing cases or start a fresh demo dataset as described in docs/RUNNING.md."
            )
        if args.check:
            return 0
        while True:
            print("\n1 Create shipment        2 Show status and instance keys")
            print(
                "3 Verify loading         4 Dispatch (also use this to demonstrate a blocked attempt)"
            )
            print("5 Replenish 5 crates      6 Cancel a stock-waiting shipment")
            print("7 Show activity history  8 Show product inventory")
            print("9 Force manual arrival 10 P2 test receipt (bypasses P3) 0 Exit")
            print("11 Simulate delivery risk 12 Accept delay 13 Approve route change")
            print("14 Confirm physical receipt 15 Inspect goods 16 Show receiving stock")
            print("17 Release quality hold (test stand-in, bypasses P4)")
            print("18 Change simulated temperature scenario")
            print("19 Quality investigation (P4, executed in DCR)")
            print("20 Corrective fulfilment (P5, booking and collection)")
            choice = input("Action: ").strip()
            if choice == "0":
                return 0
            if choice not in {str(value) for value in range(1, 21)}:
                print("Choose an action from 0 to 20.")
                continue
            try:
                perform(base, choice, args.deadline_minutes)
            except (ValueError, RuntimeError) as error:
                print("Cannot perform action:", error)
    except (URLError, TimeoutError, OSError, RuntimeError) as error:
        print("Cannot use the application:", error)
        print(
            "Check Docker startup. If an action was already submitted, inspect its status before retrying."
        )
        return 1
    except (EOFError, KeyboardInterrupt):
        print(
            "\nDemo controls closed. Workflow instances and containers are preserved."
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
