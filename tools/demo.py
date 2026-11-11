"""Interactive demonstration controls for the manual-input P1/P2 workflow.

Uses the application's HTTP API; no Docker commands or data resets are performed.
Run from the repository root: python tools/demo.py
"""

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
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
    if record.get("p2ProcessInstanceKey"):
        print(f"P2 instance key: {record['p2ProcessInstanceKey']}")
        print(f"Dispatch handoff: {record.get('handoffStatus')}")
        print(f"P2 transport state: {record.get('transportState', 'not started')}")
        print(f"Vehicle released: {record.get('vehicleReleased', False)}")
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


def wait_for_action(base, shipment_id, action, old_wait_key=None):
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


def choose_case():
    print("A = 15 crates / V-01 | B = 10 crates / V-02 | C = 10 crates / V-02")
    label = input("Shipment [A/B/C]: ").strip().upper()
    if label not in CASES:
        raise ValueError("Choose A, B or C.")
    return CASES[label]


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


def perform(base, choice):
    if choice == "8":
        inventory(base)
        return
    shipment_id, vehicle, quantity = choose_case()
    path = f"/shipments/{shipment_id}"
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
        deadline = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
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
    elif choice in ("9", "10"):
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
        else:
            action = "acknowledge-receipt"
            data = {
                "eventId": f"demo-receipt-{shipment_id}",
                "occurredAt": manual_event_time(base, shipment_id, 10),
                "recipientId": record["recipientId"],
                "quantityReceived": record["quantity"],
                "receiptReference": f"DEMO-{shipment_id}",
            }
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
    if choice != "1":
        path += "/" + action
    status, response = http(base, path, data)
    if status != 202:
        print(f"\nAction rejected (HTTP {status}): {response.get('error', response)}")
        if choice == "4" and status == 409:
            print(
                "This demonstrates the dispatch restriction: stock and loading steps must be complete."
            )
        return
    print("Action accepted. Waiting for the workflow to process it...")
    wait_for_action(base, shipment_id, action, old_wait_key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:5001")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check readiness and inventory without changing data",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    try:
        status, health = http(base, "/health")
        if status != 200 or health.get("stage") != 2:
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
            print("9 Record manual arrival 10 Acknowledge receipt 0 Exit")
            choice = input("Action: ").strip()
            if choice == "0":
                return 0
            if choice not in {str(value) for value in range(1, 11)}:
                print("Choose an action from 0 to 10.")
                continue
            try:
                perform(base, choice)
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
