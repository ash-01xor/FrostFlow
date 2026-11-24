"""P1–P3 delivery, P4 DCR investigation and P5 corrective fulfilment."""

import asyncio
import math
import os
import re
import threading
import urllib.error
from datetime import datetime, timezone

import grpc
from flask import Flask, jsonify, request
from pyzeebe import Job, ZeebeClient, ZeebeWorker
from pyzeebe.errors import MessageAlreadyExistsError

from dcr_client import make_dcr
from quality import QualityService, create_blueprint
from corrective import create_blueprint as corrective_blueprint
from registry import Conflict, Registry
from weather import WeatherAdapter
from transport_integrations import TransportIntegrations, post_json, validate_p4_acknowledgement

PROCESS_ID = "ShipmentPreparation"
JOB_TYPES = [
    "p1-check-stock",
    "p1-allocate-stock",
    "p1-assign-transport",
    "p1-record-loading",
    "p1-dispatch",
    "p1-cancel",
    "p2-start-transport",
    "p2-start-journey",
    "p2-record-arrival",
    "p2-release-vehicle",
    "p2-record-delivery-risk",
    "p2-apply-dispatcher-decision",
    "p3-record-arrival",
    "p3-record-receipt",
    "p3-record-inspection",
    "p3-admit-stock",
    "p3-record-quality-status",
    "p3-finalize-monitoring",
    "p5-register",
    "p5-arrange",
    "p5-record-collection",
    "p5-record-dispatch",
    "p5-record-outcome",
    "p5-complete",
    "p6-assemble-evidence",
    "p6-submit-claim",
    "p6-close-claim",
]
app = Flask(__name__)
registry = Registry(os.getenv("REGISTRY_PATH", "runtime/registry.sqlite"))
transport_integrations = TransportIntegrations()
weather_adapter = WeatherAdapter(os.path.join(os.path.dirname(registry.path), "open-meteo-responses.jsonl"))
for stored_shipment in registry.monitored_shipments():
    weather_adapter.restore(stored_shipment["journeyId"], stored_shipment.get("weather"))
ready = threading.Event()
_dcr_engines = {}


def current_dcr():
    """DCR repository when configured, otherwise the local engine on this registry."""
    if registry.path not in _dcr_engines:
        _dcr_engines[registry.path] = make_dcr(registry.path)
    return _dcr_engines[registry.path]


# P4: quality investigation executed in DCR (deployer/quality.py).
quality = QualityService(lambda: registry, current_dcr)
app.register_blueprint(create_blueprint(quality))
app.register_blueprint(corrective_blueprint(lambda: registry, ready))
client = None
event_loop = None


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("send a JSON object")
    return data


def text_field(data, field, max_length=100):
    value = data.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(
            f"{field} must be a non-empty string of at most {max_length} characters"
        )
    return value.strip()


def utc_timestamp(data, field):
    value = text_field(data, field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO 8601 UTC timestamp") from error
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field} must include UTC timezone Z or +00:00")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def manual_event_payload(data, shipment_id, event_type):
    record = registry.get(shipment_id)
    if "eventType" in data and data["eventType"] != event_type:
        raise ValueError(f"eventType must be {event_type}")
    if "shipmentId" in data and data["shipmentId"] != shipment_id:
        raise Conflict("shipmentId must match the URL")
    if "journeyId" in data and data["journeyId"] != record["journeyId"]:
        raise Conflict("journeyId must match the dispatched shipment")
    if "inputMode" in data and data["inputMode"] != "manual":
        raise ValueError("these endpoints accept manual input only")
    payload = data.get("payload", data)
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    return payload


def engine_call(coro):
    # A timeout can occur after acceptance. Keep the pending record for inspection.
    return asyncio.run_coroutine_threadsafe(coro, event_loop).result(timeout=30)


@app.errorhandler(ValueError)
def invalid(error):
    return jsonify(error=str(error)), 400


@app.errorhandler(Conflict)
def conflict(error):
    return jsonify(error=str(error)), 409


@app.errorhandler(KeyError)
def missing(error):
    return jsonify(error="unknown shipment"), 404


@app.get("/health")
def health():
    return jsonify(
        status="ready" if ready.is_set() else "starting",
        stage=3,
        processId=PROCESS_ID,
        processIds=[PROCESS_ID, "ShipmentTransport", "ShipmentReceiving", "CorrectiveFulfilment", "SupplierClaimResolution"],
        jobTypes=JOB_TYPES,
        integrations={
            "gps": "scripted-demo" if os.getenv("P2_SIMULATOR_ENABLED", "true").lower() == "true" else "disabled",
            "containerMonitoring": "simulated-independent",
            "containerEvaluation": "siddhi" if os.getenv("SIDDIHI_CONTAINER_URL") else "unavailable-hold-on-timeout",
            "weather": "open-meteo-current-model",
            "tomtom": "enabled" if os.getenv("TOMTOM_API_KEY") else "local-demo-estimate",
            "siddhiTelemetry": "enabled" if os.getenv("SIDDIHI_TELEMETRY_URL") else "not-configured",
            "siddhiCommands": "enabled" if os.getenv("SIDDIHI_COMMAND_URL") else "local-demo-acknowledgement",
            "p3ArrivalNotification": "enabled" if os.getenv("P3_ARRIVAL_URL") else "queued-awaiting-endpoint",
            "p4IncidentHandoff": "enabled" if os.getenv("P4_INCIDENT_URL") else "queued-awaiting-p4",
            "p4DcrEngine": current_dcr().mode,
            "p4CoolingCommands": "siddhi-command-bridge" if os.getenv("P4_COOLING_COMMAND_URL")
            else "manual-test-input",
        },
    ), 200 if ready.is_set() else 503


@app.post("/claims")
def create_claim():
    data = body()
    refs = data.get("evidenceRefs")
    if not isinstance(refs, list) or not 1 <= len(refs) <= 50:
        raise ValueError("evidenceRefs must contain 1 to 50 references")
    loss = {
        "incidentId": text_field(data, "incidentId", max_length=1000),
        "originalShipmentId": text_field(data, "originalShipmentId"),
        "lossDescription": text_field(data, "lossDescription", 1000),
        "actor": text_field(data, "actor"),
        "evidenceRefs": [
            text_field({"reference": ref}, "reference", 300) for ref in refs
        ],
    }
    return jsonify(registry.accept_claim(loss)), 202


@app.get("/claims/<claim_id>")
def get_claim(claim_id):
    try:
        return jsonify(registry.get_claim(claim_id))
    except KeyError:
        return jsonify(error="unknown claim"), 404


@app.post("/claims/<claim_id>/supplier-response")
def record_supplier_response(claim_id):
    data = body()
    outcome = text_field(data, "outcome")
    if outcome not in ("SETTLED", "REJECTED"):
        raise ValueError("outcome must be SETTLED or REJECTED")
    response = {
        "outcome": outcome,
        "reason": text_field(data, "reason", 1000),
        "actor": text_field(data, "actor"),
    }
    try:
        return jsonify(registry.accept_supplier_response(claim_id, response)), 202
    except KeyError:
        return jsonify(error="unknown claim"), 404


@app.get("/shipments/<shipment_id>")
def shipment(shipment_id):
    return jsonify(registry.get(shipment_id))


@app.get("/shipments/<shipment_id>/events")
def events(shipment_id):
    return jsonify(registry.events(shipment_id))


@app.get("/shipments/<shipment_id>/monitoring/observations")
def container_observations(shipment_id):
    return jsonify(registry.container_observations(shipment_id))


@app.get("/stock")
def stock():
    return jsonify(registry.inventory())


@app.get("/receiving-stock")
def receiving_stock():
    recipient_id = request.args.get("recipientId")
    if recipient_id is not None and recipient_id not in ("C-01", "C-02", "C-03"):
        raise ValueError("recipientId must be C-01, C-02 or C-03")
    return jsonify(registry.receiving_inventory(recipient_id))


@app.post("/integrations/p3/arrival")
def p3_arrival():
    """Receive the durable P2 arrival notification that starts P3."""
    data = body()
    notification = data.get("event")
    if not isinstance(notification, dict):
        raise ValueError("event must be a JSON object")
    delivery_id = text_field(notification, "deliveryId", max_length=256)
    if request.headers.get("Idempotency-Key") != delivery_id:
        raise ValueError("Idempotency-Key must match event.deliveryId")
    if notification.get("eventType") != "ShipmentArrivalNotification":
        raise ValueError("eventType must be ShipmentArrivalNotification")
    clean = {
        "deliveryId": delivery_id,
        "eventType": "ShipmentArrivalNotification",
        "shipmentId": text_field(notification, "shipmentId"),
        "journeyId": text_field(notification, "journeyId"),
        "productId": text_field(notification, "productId"),
        "batchId": text_field(notification, "batchId"),
        "arrival": notification.get("arrival"),
        "sourceEventId": text_field(notification, "sourceEventId", max_length=256),
        "occurredAt": utc_timestamp(notification, "occurredAt"),
        "recordedAt": utc_timestamp(notification, "recordedAt"),
    }
    if not isinstance(clean["arrival"], dict):
        raise ValueError("arrival must be a JSON object")
    clean["arrival"] = {
        "recipientId": text_field(clean["arrival"], "recipientId"),
    }
    for field in ("originalShipmentId", "incidentId", "correctiveId", "replacementForCorrectiveId"):
        if field in notification:
            clean[field] = text_field(notification, field, max_length=1000 if field == "incidentId" else 100)
    if ("originalShipmentId" in clean) != ("incidentId" in clean):
        raise ValueError("replacement links require both originalShipmentId and incidentId")
    accepted = registry.accept_p3_arrival(clean)
    return jsonify(
        status="accepted",
        shipmentId=clean["shipmentId"],
        deliveryId=delivery_id,
        messageId=accepted["messageId"],
    ), 202


@app.post("/shipments/<shipment_id>/receiving/receipt")
def receiving_receipt(shipment_id):
    data = body()
    event_id = text_field(data, "eventId")
    occurred_at = utc_timestamp(data, "occurredAt")
    payload = {
        "recipientId": text_field(data, "recipientId"),
        "quantityReceived": data.get("quantityReceived"),
        "receiver": text_field(data, "receiver"),
    }
    if (
        type(payload["quantityReceived"]) is not int
        or payload["quantityReceived"] < 1
    ):
        raise ValueError("quantityReceived must be a positive integer number of crates")
    if "receiptReference" in data:
        payload["receiptReference"] = text_field(data, "receiptReference")
    accepted = registry.queue_p3_input(
        shipment_id, "GoodsReceived", event_id, occurred_at, payload
    )
    return jsonify(
        status="accepted",
        shipmentId=shipment_id,
        eventId=event_id,
        messageId=accepted["messageId"],
    ), 202


@app.post("/shipments/<shipment_id>/receiving/inspection")
def receiving_inspection(shipment_id):
    data = body()
    passed = data.get("passed")
    if type(passed) is not bool:
        raise ValueError("passed must be a boolean")
    payload = {
        "inspector": text_field(data, "inspector"),
        "passed": passed,
    }
    if "notes" in data:
        payload["notes"] = text_field(data, "notes")
    accepted = registry.queue_p3_input(
        shipment_id,
        "GoodsInspected",
        text_field(data, "eventId"),
        utc_timestamp(data, "occurredAt"),
        payload,
    )
    return jsonify(
        status="accepted",
        shipmentId=shipment_id,
        eventId=accepted["event"]["eventId"],
        messageId=accepted["messageId"],
    ), 202


@app.post("/integrations/p4/quality-status")
def p4_quality_status():
    """Receive a P4 disposition; this internal demo endpoint is not authenticated."""
    data = body()
    accepted = registry.accept_quality_status(
        text_field(data, "shipmentId"),
        text_field(data, "eventId", max_length=1000),
        utc_timestamp(data, "occurredAt"),
        text_field(data, "qualityStatus"),
        text_field(data, "incidentId", max_length=1000),
        data.get("expectedEvidenceVersion"),
    )
    return jsonify(
        status="accepted",
        shipmentId=data["shipmentId"],
        eventId=data["eventId"],
        messageId=accepted["messageId"],
    ), 202


@app.post("/shipments/<shipment_id>/simulation/temperature")
def temperature_scenario(shipment_id):
    data = body()
    return jsonify(registry.temperature_scenario(shipment_id, text_field(data, "commandId"),
                   text_field(data, "actor"), text_field(data, "scenario"))), 202


@app.post("/integrations/siddhi/container-assessment")
def container_assessment():
    data = body()
    acknowledgement = data.get("event", data)
    if not isinstance(acknowledgement, dict):
        raise ValueError("container acknowledgement must be an object")
    for field in ("shipmentId", "journeyId", "eventId"):
        text_field(acknowledgement, field, max_length=1000 if field == "eventId" else 100)
    return jsonify(registry.acknowledge_container(acknowledgement)), 202


@app.post("/shipments")
def create_shipment():
    data = body()
    fields = (
        "shipmentId",
        "journeyId",
        "vehicleId",
        "recipientId",
        "routeId",
        "deliveryDeadline",
        "productId",
        "batchId",
    )
    clean = {field: text_field(data, field) for field in fields}
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", clean["shipmentId"]):
        raise ValueError(
            "shipmentId must start with a letter or digit and contain only "
            "ASCII letters, digits, dots, underscores or hyphens"
        )
    if clean["vehicleId"] not in ("V-01", "V-02"):
        raise ValueError("vehicleId must be V-01 or V-02")
    if clean["recipientId"] not in ("C-01", "C-02", "C-03"):
        raise ValueError("recipientId must be C-01, C-02 or C-03")
    if clean["routeId"] not in ("route-01", "route-02"):
        raise ValueError("routeId must be route-01 or route-02 (demo route labels)")
    deadline = datetime.fromisoformat(clean["deliveryDeadline"].replace("Z", "+00:00"))
    if deadline.utcoffset() is None or deadline.utcoffset().total_seconds() != 0:
        raise ValueError("deliveryDeadline must include UTC timezone Z or +00:00")
    quantity = data.get("quantity")
    if type(quantity) is not int or not 1 <= quantity <= 100:
        raise ValueError("quantity must be an integer from 1 to 100 crates")
    clean["quantity"] = quantity
    for field in ("originalShipmentId", "incidentId"):
        if field in data:
            clean[field] = text_field(data, field)
    if ("originalShipmentId" in clean) != ("incidentId" in clean):
        raise ValueError(
            "replacement links require both originalShipmentId and incidentId"
        )
    if not ready.is_set():
        return jsonify(error="wait for /health to report ready"), 503
    registry.create(clean)
    try:
        result = engine_call(
            client.run_process(
                PROCESS_ID,
                variables={
                    k: clean[k]
                    for k in ("shipmentId", "productId", "batchId", "quantity")
                },
            )
        )
        registry.attach_key(clean["shipmentId"], result.process_instance_key)
    except Exception:
        app.logger.exception("Engine start outcome uncertain")
        return jsonify(
            error="engine start outcome uncertain; inspect logs and Operate before recovery",
            shipmentId=clean["shipmentId"],
        ), 502
    return jsonify(registry.get(clean["shipmentId"])), 202


def send_action(shipment_id, message, actor):
    if not ready.is_set():
        return jsonify(error="worker service is not ready"), 503
    record = registry.reserve_command(shipment_id, message, actor)
    return jsonify(
        status="accepted",
        shipmentId=shipment_id,
        processInstanceKey=record["processInstanceKey"],
        message=message,
    ), 202


@app.post("/shipments/<shipment_id>/cancel")
def cancel(shipment_id):
    return send_action(
        shipment_id, "ShipmentCancellationRequested", text_field(body(), "actor")
    )


@app.post("/shipments/<shipment_id>/replenish-stock")
def replenish(shipment_id):
    data = body()
    actor = text_field(data, "actor")
    event_id = text_field(data, "eventId")
    # This endpoint replenishes the addressed shipment's exact product/batch.
    # Reject conflicting labels rather than silently crediting another item.
    record = registry.get(shipment_id)
    for field in ("productId", "batchId"):
        if field in data and data[field] != record[field]:
            raise ValueError(f"{field} must match the shipment being replenished")
    quantity = data.get("quantity")
    if type(quantity) is not int or not 1 <= quantity <= 10000:
        raise ValueError("quantity must be an integer from 1 to 10000 crates")
    if not ready.is_set():
        return jsonify(error="worker service is not ready"), 503
    record = registry.reserve_command(
        shipment_id, "StockReplenished", actor, (event_id, quantity)
    )
    return jsonify(
        status="accepted", shipmentId=shipment_id, state=record["state"]
    ), 202


@app.post("/shipments/<shipment_id>/verify-loading")
def verify_loading(shipment_id):
    data = body()
    actor = text_field(data, "actor")
    if any(
        data.get(check) is not True
        for check in ("quantityChecked", "packagingChecked", "vehicleReady")
    ):
        raise ValueError(
            "quantityChecked, packagingChecked and vehicleReady must all be JSON true"
        )
    return send_action(shipment_id, "LoadingVerified", actor)


@app.post("/shipments/<shipment_id>/dispatch")
def dispatch(shipment_id):
    return send_action(shipment_id, "DispatchApproved", text_field(body(), "actor"))


@app.post("/shipments/<shipment_id>/arrive")
def manual_arrival(shipment_id):
    """Manual/test input for ShipmentArrived until the P2 simulator is connected."""
    data = body()
    event_id = text_field(data, "eventId")
    occurred_at = utc_timestamp(data, "occurredAt")
    input_payload = manual_event_payload(data, shipment_id, "ShipmentArrived")
    payload = {"recipientId": text_field(input_payload, "recipientId")}
    has_latitude = "latitude" in input_payload
    has_longitude = "longitude" in input_payload
    if has_latitude != has_longitude:
        raise ValueError("latitude and longitude must be supplied together")
    if has_latitude:
        latitude, longitude = input_payload["latitude"], input_payload["longitude"]
        if (
            type(latitude) not in (int, float)
            or type(longitude) not in (int, float)
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
            or not math.isfinite(latitude)
            or not math.isfinite(longitude)
        ):
            raise ValueError("latitude/longitude must be valid coordinate numbers")
        payload.update(latitude=latitude, longitude=longitude)
    if not ready.is_set():
        return jsonify(error="worker service is not ready"), 503
    accepted = registry.queue_transport_event(
        shipment_id,
        "ShipmentArrived",
        event_id,
        occurred_at,
        payload,
        source="p2-manual-test",
    )
    return jsonify(
        status="accepted",
        inputMode="manual",
        shipmentId=shipment_id,
        eventId=event_id,
        messageId=accepted["messageId"],
    ), 202


@app.post("/integrations/siddhi/delivery-risk")
def siddhi_delivery_risk():
    """Receive a Siddhi alert, with eventId as the idempotency key."""
    envelope = body()
    data = envelope.get("event", envelope)
    if not isinstance(data, dict):
        raise ValueError("event must be an object")
    shipment_id = text_field(data, "shipmentId")
    event_id = text_field(data, "eventId")
    occurred_at = utc_timestamp(data, "occurredAt")
    payload = {
        "eta": utc_timestamp(data, "eta"),
        "deliveryDeadline": utc_timestamp(data, "deliveryDeadline"),
        "reason": text_field(data, "reason"),
        "occurredAt": occurred_at,
    }
    delay = data.get("estimatedDelaySeconds")
    if delay is not None:
        if type(delay) is not int or delay < 0:
            raise ValueError("estimatedDelaySeconds must be a non-negative integer")
        payload["estimatedDelaySeconds"] = delay
    accepted = registry.queue_delivery_risk(shipment_id, event_id, payload)
    return jsonify(status="accepted", shipmentId=shipment_id, eventId=event_id,
                   messageId=accepted["messageId"]), 202


@app.post("/integrations/simulator/route-command")
def simulator_route_command():
    """Local scripted simulator receiver used by Siddhi's command bridge."""
    envelope = body()
    data = envelope.get("event", envelope)
    if not isinstance(data, dict):
        raise ValueError("event must be an object")
    route_id = data.get("routeId")
    # Siddhi's JSON sink serialises a null string attribute as "undefined";
    # AcceptDelay carries no route, so treat that (and "") as absent.
    if route_id in ("undefined", ""):
        route_id = None
    command = {
        "commandId": text_field(data, "commandId"),
        "shipmentId": text_field(data, "shipmentId"),
        "journeyId": text_field(data, "journeyId"),
        "riskEventId": text_field(data, "riskEventId"),
        "command": text_field(data, "command"),
        "routeId": route_id,
    }
    if command["routeId"] is not None and not isinstance(command["routeId"], str):
        raise ValueError("routeId must be a string")
    if command["command"] == "ChangeRoute" and command["routeId"]:
        check_route_keeps_destination(command["shipmentId"], command["routeId"])
    return jsonify(registry.acknowledge_simulator_command(command)), 200


def check_route_keeps_destination(shipment_id, route_id):
    """Reject a route that is unknown or would not end at the shipment's destination."""
    record = registry.get(shipment_id)
    planned = transport_integrations.route(route_id, record["recipientId"])
    current = record.get("routeCoordinates")
    if current and planned[-1] != current[-1]:
        raise Conflict("route would change the delivery destination")


@app.post("/shipments/<shipment_id>/dispatcher-decision")
def dispatcher_decision(shipment_id):
    """Accept a human decision for the currently unresolved delivery-risk alert."""
    data = body()
    route_id = data.get("routeId")
    if route_id is not None and not isinstance(route_id, str):
        raise ValueError("routeId must be a string")
    if route_id is not None:
        check_route_keeps_destination(shipment_id, route_id)
    accepted = registry.queue_dispatcher_decision(
        shipment_id, text_field(data, "eventId"), text_field(data, "decision"), route_id
    )
    return jsonify(status="accepted", shipmentId=shipment_id,
                   eventId=accepted["event"]["eventId"],
                   messageId=accepted["messageId"]), 202


@app.post("/shipments/<shipment_id>/acknowledge-receipt")
def manual_receipt_acknowledgement(shipment_id):
    """Manual/test P2 input for the receipt acknowledgement normally sent by P3.

    Only for P2-only runs (P3_ARRIVAL_URL unset). With P3 receiving enabled,
    P2 completes solely from P3's ReceiptAcknowledged after P3 records receipt.
    """
    if os.getenv("P3_ARRIVAL_URL"):
        raise Conflict(
            "P3 receiving is enabled: record receipt with "
            "POST /shipments/<id>/receiving/receipt; P2 completes from P3's acknowledgement"
        )
    data = body()
    event_id = text_field(data, "eventId")
    occurred_at = utc_timestamp(data, "occurredAt")
    input_payload = manual_event_payload(
        data, shipment_id, "ReceiptAcknowledged"
    )
    payload = {
        "recipientId": text_field(input_payload, "recipientId"),
        "quantityReceived": input_payload.get("quantityReceived"),
    }
    if (
        type(payload["quantityReceived"]) is not int
        or payload["quantityReceived"] < 1
    ):
        raise ValueError(
            "quantityReceived must be a positive integer number of crates"
        )
    if "receiptReference" in input_payload:
        payload["receiptReference"] = text_field(input_payload, "receiptReference")
    if not ready.is_set():
        return jsonify(error="worker service is not ready"), 503
    accepted = registry.queue_transport_event(
        shipment_id,
        "ReceiptAcknowledged",
        event_id,
        occurred_at,
        payload,
        source="p3-manual-test",
    )
    return jsonify(
        status="accepted",
        inputMode="manual",
        shipmentId=shipment_id,
        eventId=event_id,
        messageId=accepted["messageId"],
    ), 202


async def check_stock(shipmentId, job: Job):
    result = registry.check_stock(shipmentId, job.process_instance_key, job.key)
    # Call-activity children have no HTTP run_process response to attach their key.
    registry.attach_key(shipmentId, job.process_instance_key)
    return result


async def allocate_stock(shipmentId, job: Job):
    registry.advance(
        shipmentId,
        job.process_instance_key,
        "STOCK_RESERVED",
        "STOCK_ALLOCATED",
        "StockAllocated",
    )
    return {}


async def assign_transport(shipmentId, job: Job):
    registry.advance(
        shipmentId,
        job.process_instance_key,
        "STOCK_ALLOCATED",
        "AWAITING_LOADING",
        "TransportAssigned",
    )
    return {}


async def record_loading(shipmentId, job: Job):
    registry.advance(
        shipmentId,
        job.process_instance_key,
        "AWAITING_LOADING",
        "AWAITING_DISPATCH",
        "LoadingVerified",
    )
    return {}


async def dispatch_shipment(shipmentId, job: Job):
    registry.advance(
        shipmentId,
        job.process_instance_key,
        "AWAITING_DISPATCH",
        "DISPATCHED",
        "ShipmentDispatched",
    )
    return {"dispatched": True}


async def cancel_shipment(shipmentId, job: Job):
    registry.advance(
        shipmentId,
        job.process_instance_key,
        "WAITING_STOCK",
        "CANCELLED",
        "ShipmentCancelled",
    )
    return {}


async def start_transport(shipmentId, dispatchEvent, job: Job):
    return registry.start_transport(shipmentId, job.process_instance_key, dispatchEvent)


async def start_journey(shipmentId, job: Job):
    record = registry.get(shipmentId)
    points = transport_integrations.route(record["routeId"], record["recipientId"])
    eta, source = await asyncio.to_thread(
        transport_integrations.eta, points, record["routeId"], 0
    )
    registry.start_journey(
        shipmentId, job.process_instance_key, points[0], eta, source, points
    )
    return {}


async def record_arrival(shipmentId, arrivalEvent, job: Job):
    registry.record_arrival(shipmentId, job.process_instance_key, arrivalEvent)
    return {}


async def release_vehicle(shipmentId, receiptEvent, job: Job):
    registry.release_vehicle(shipmentId, job.process_instance_key, receiptEvent)
    return {}


async def p3_record_arrival(shipmentId, arrivalNotification, job: Job):
    registry.record_p3_arrival(
        shipmentId, job.process_instance_key, arrivalNotification
    )
    return {}


async def p3_record_receipt(shipmentId, receiptEvent, job: Job):
    registry.record_p3_receipt(shipmentId, job.process_instance_key, receiptEvent)
    return {}


async def p3_record_inspection(shipmentId, inspectionEvent, job: Job):
    return registry.record_p3_inspection(
        shipmentId, job.process_instance_key, inspectionEvent
    )


async def p3_admit_stock(shipmentId, job: Job):
    return registry.admit_p3_stock(shipmentId, job.process_instance_key)


async def p3_record_quality_status(shipmentId, qualityStatusEvent, job: Job):
    registry.record_p3_quality_status(
        shipmentId, job.process_instance_key, qualityStatusEvent
    )
    return {}


async def p3_finalize_monitoring(shipmentId, job: Job):
    # Keep the engine on this explicit service task while durable evaluation is
    # pending. The watchdog converts unresolved closure to a hold after 20 s.
    while not registry.finalize_monitoring(shipmentId):
        await asyncio.sleep(0.5)
    return {}


async def p5_register(correctiveId, authorizationEvent, job: Job):
    return registry.register_corrective(correctiveId, job.process_instance_key, authorizationEvent)


async def p5_arrange(correctiveId, correctiveInput, job: Job):
    return registry.arrange_corrective(correctiveId, job.process_instance_key, correctiveInput)


async def p5_record_collection(correctiveId, correctiveInput, job: Job):
    return registry.record_corrective_collection(correctiveId, job.process_instance_key, correctiveInput)


async def p5_record_dispatch(correctiveId, job: Job):
    return registry.record_replacement_dispatch(correctiveId, job.process_instance_key)


async def p5_record_outcome(correctiveId, replacementOutcomeEvent, job: Job):
    return registry.record_replacement_outcome(correctiveId, job.process_instance_key, replacementOutcomeEvent)


async def p5_complete(correctiveId, job: Job):
    return registry.complete_corrective(correctiveId, job.process_instance_key)


async def record_delivery_risk(shipmentId, deliveryRiskEvent, job: Job):
    registry.record_delivery_risk(shipmentId, job.process_instance_key, deliveryRiskEvent)
    return {}


async def apply_dispatcher_decision(shipmentId, dispatcherDecision, job: Job):
    route_id = dispatcherDecision.get("routeId")
    if route_id:
        record = registry.get(shipmentId)
        # The alternative starts where the vehicle is now and must end where the
        # current route ends: a route change never changes the delivery destination.
        points = transport_integrations.route(
            route_id, record["recipientId"], start=record["currentPosition"]
        )
        if record.get("routeCoordinates") and points[-1] != record["routeCoordinates"][-1]:
            raise Conflict("alternative route does not end at the shipment's destination")
        eta, eta_source = await asyncio.to_thread(
            transport_integrations.eta, points, route_id, 0
        )
    else:
        points, eta, eta_source = None, None, None
    acknowledgement = await asyncio.to_thread(
        transport_integrations.route_command,
        {"commandId": dispatcherDecision["eventId"], "shipmentId": shipmentId,
         "journeyId": dispatcherDecision["journeyId"], "riskEventId": dispatcherDecision["riskEventId"],
         "command": "ChangeRoute" if route_id else "AcceptDelay", "routeId": route_id},
    )
    updated = registry.apply_dispatcher_decision(
        shipmentId, job.process_instance_key, dispatcherDecision,
        acknowledgement, points, eta, eta_source,
    )
    # Keep Camunda's process variables in step with the registry after a change.
    return {
        "simulatorAcknowledged": True,
        "routeId": updated["routeId"],
        "routeRevision": updated.get("routeRevision", 0),
    }


async def advance_simulated_journeys():
    """Move scripted demo routes forward; arrival still enters P2 through its message catch."""
    while True:
        for record in registry.in_transit_shipments():
            try:
                points = record.get("routeCoordinates") or transport_integrations.route(
                    record["routeId"], record["recipientId"]
                )
                index = min(record.get("routeIndex", 0) + 1, len(points) - 1)
                position = points[index]
                # Off the event loop: a slow TomTom call must not stall Zeebe workers.
                eta, source = await asyncio.to_thread(
                    transport_integrations.eta, points[index:], record["routeId"], index
                )
                updated = registry.advance_telemetry(record["shipmentId"], position, index, eta, source)
                if index == len(points) - 1 and updated.get("transportState") == "IN_TRANSIT":
                    arrived = updated.get("lastGpsEventOccurredAt") or updated["telemetryUpdatedAt"]
                    registry.queue_transport_event(
                        record["shipmentId"], "ShipmentArrived",
                        f"SIM:{record['journeyId']}:ShipmentArrived", arrived,
                        {"recipientId": record["recipientId"], "latitude": position[0], "longitude": position[1]},
                        source="p2-simulator", input_mode="simulated",
                    )
            except Conflict as error:
                app.logger.info("Simulator skipped %s: %s", record["shipmentId"], error)
            except Exception:
                app.logger.exception("Simulator tick failed for %s", record["shipmentId"])
        await asyncio.sleep(max(0.1, float(os.getenv("GPS_TICK_SECONDS", "5"))))


async def monitor_containers():
    while True:
        try:
            await asyncio.to_thread(registry.tick_monitoring)
        except Exception:
            app.logger.exception("Container monitoring tick failed")
        await asyncio.sleep(0.5)


async def refresh_weather():
    async def refresh(record):
        try:
            context = await asyncio.to_thread(weather_adapter.current, record["journeyId"],
                                             record.get("currentPosition", [48.8566, 2.3522]))
            await asyncio.to_thread(registry.store_weather, record["shipmentId"], context)
        except Exception:
            app.logger.exception("Weather refresh failed")
    while True:
        await asyncio.gather(*(refresh(r) for r in registry.monitored_shipments()
                               if r["monitoring"]["state"] == "ACTIVE"))
        await asyncio.sleep(1)


async def deliver_container_observations():
    while True:
        url = os.getenv("SIDDIHI_CONTAINER_URL")
        if url:
            for message in registry.pending_messages(destination="container-siddhi"):
                try:
                    result = await asyncio.to_thread(post_json, url, message["variables"],
                                                     {"Idempotency-Key": message["messageId"]})
                    registry.acknowledge_container(result.get("event", result))
                    registry.message_result(message["messageId"])
                except Exception as error:
                    registry.message_result(message["messageId"], str(error))
        await asyncio.sleep(0.25)


async def deliver_messages():
    """Deliver durable Zeebe messages and optional HTTP integrations."""
    while True:
        for message in registry.pending_messages(destination="zeebe"):
            try:
                await asyncio.wait_for(
                    client.publish_message(
                        name=message["name"],
                        correlation_key=message["correlationKey"],
                        message_id=message["messageId"],
                        variables=message["variables"],
                        time_to_live_in_milliseconds=86400000,
                    ),
                    timeout=15,
                )
            except MessageAlreadyExistsError:
                registry.message_result(message["messageId"])
            except Exception as error:
                registry.message_result(message["messageId"], str(error))
                app.logger.warning(
                    "Message %s pending: %s", message["messageId"], error
                )
            else:
                registry.message_result(message["messageId"])
        destinations = {
            "p3-http": os.getenv("P3_ARRIVAL_URL"),
            "siddhi-http": os.getenv("SIDDIHI_TELEMETRY_URL"),
            # Durable P4 investigation handoff; stays PENDING while P4_INCIDENT_URL is
            # empty or the P4 consumer is unavailable.
            "p4-http": os.getenv("P4_INCIDENT_URL"),
        }
        for destination, url in destinations.items():
            if not url:
                continue
            for message in registry.pending_messages(destination=destination):
                try:
                    acknowledgement = await asyncio.to_thread(
                        post_json, url, message["variables"],
                        {"Idempotency-Key": message["messageId"]},
                    )
                    if destination == "p4-http":
                        validate_p4_acknowledgement(acknowledgement, message["messageId"])
                except Exception as error:
                    registry.message_result(message["messageId"], str(error))
                    app.logger.warning("%s delivery %s pending: %s", destination,
                                       message["messageId"], error)
                else:
                    registry.message_result(message["messageId"])
        await deliver_cooling_commands()
        try:
            # P4: retry DCR cases or dispositions that could not complete earlier.
            await asyncio.to_thread(quality.retry_unfinished)
        except Exception as error:
            app.logger.warning("P4 retry pending: %s", error)
        await asyncio.sleep(1)


async def deliver_cooling_commands():
    """P4 cooling commands -> Siddhi command bridge (docs/P4-COOLING-CONTRACT.md).

    A 200 answer is receipt of the command, not its application: the simulator's
    APPLIED/FAILED result arrives separately at /integrations/p4/cooling-result. A
    definite refusal (HTTP 400/404/409/422) fails the command; other errors are retried
    with the same commandId until P4 times the command out.
    """
    url = os.getenv("P4_COOLING_COMMAND_URL")
    if not url:
        return
    for message in registry.pending_messages(destination="p4-cooling-command"):
        command_id = message["messageId"]
        try:
            response = await asyncio.to_thread(
                post_json, url, message["variables"], {"Idempotency-Key": command_id}, 15)
            await asyncio.to_thread(quality.cooling_command_delivered, command_id, response)
        except urllib.error.HTTPError as error:
            if error.code in (400, 404, 409, 422):
                await asyncio.to_thread(quality.cooling_command_refused, command_id,
                                        f"HTTP {error.code}: {error.reason}")
            else:
                registry.message_result(command_id, str(error))
        except Exception as error:
            registry.message_result(command_id, str(error))
            app.logger.warning("cooling command %s pending: %s", command_id, error)
        else:
            registry.message_result(command_id)


async def p6_assemble_evidence(claimId, job: Job):
    registry.assemble_claim(claimId, job.process_instance_key)
    return {}


async def p6_submit_claim(claimId, job: Job):
    registry.submit_claim(claimId, job.process_instance_key)
    return {}


async def p6_close_claim(claimId, job: Job):
    return {"claimOutcome": registry.close_claim(claimId, job.process_instance_key)}


async def main():
    global client, event_loop
    event_loop = asyncio.get_running_loop()
    channel = grpc.aio.insecure_channel(os.getenv("ZEEBE_ADDRESS", "localhost:26501"))
    # pyzeebe closes the shared channel after 10 failed calls by default. A fresh
    # Zeebe broker can take over 30 s to start, so keep retrying; the deploy loop
    # below still bounds startup.
    client = ZeebeClient(grpc_channel=channel, max_connection_retries=-1)
    worker = ZeebeWorker(grpc_channel=channel, max_connection_retries=-1)
    handlers = (
        check_stock,
        allocate_stock,
        assign_transport,
        record_loading,
        dispatch_shipment,
        cancel_shipment,
        start_transport,
        start_journey,
        record_arrival,
        release_vehicle,
        record_delivery_risk,
        apply_dispatcher_decision,
        p3_record_arrival,
        p3_record_receipt,
        p3_record_inspection,
        p3_admit_stock,
        p3_record_quality_status,
        p3_finalize_monitoring,
        p5_register,
        p5_arrange,
        p5_record_collection,
        p5_record_dispatch,
        p5_record_outcome,
        p5_complete,
        p6_assemble_evidence,
        p6_submit_claim,
        p6_close_claim,
    )
    variables_by_job = {
        "p2-start-transport": ["shipmentId", "dispatchEvent"],
        "p2-record-arrival": ["shipmentId", "arrivalEvent"],
        "p2-release-vehicle": ["shipmentId", "receiptEvent"],
        "p2-record-delivery-risk": ["shipmentId", "deliveryRiskEvent"],
        "p2-apply-dispatcher-decision": ["shipmentId", "dispatcherDecision"],
        "p3-record-arrival": ["shipmentId", "arrivalNotification"],
        "p3-record-receipt": ["shipmentId", "receiptEvent"],
        "p3-record-inspection": ["shipmentId", "inspectionEvent"],
        "p3-record-quality-status": ["shipmentId", "qualityStatusEvent"],
        "p5-register": ["correctiveId", "authorizationEvent"],
        "p5-arrange": ["correctiveId", "correctiveInput"],
        "p5-record-collection": ["correctiveId", "correctiveInput"],
        "p5-record-dispatch": ["correctiveId"],
        "p5-record-outcome": ["correctiveId", "replacementOutcomeEvent"],
        "p5-complete": ["correctiveId"],
    }
    for job_type in JOB_TYPES:
        if job_type.startswith("p6-"):
            variables_by_job[job_type] = ["claimId"]
    for job_type, handler in zip(JOB_TYPES, handlers, strict=True):
        variables = variables_by_job.get(job_type, ["shipmentId"])
        worker.task(task_type=job_type, variables_to_fetch=variables,
                    timeout_ms=60000 if job_type == "p3-finalize-monitoring" else 10000)(handler)
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=5001, threaded=True), daemon=True
    ).start()
    try:
        for attempt in range(60):
            try:
                await client.deploy_resource(
                    os.getenv("BPMN_FILE", "bpmn/shipment_preparation.bpmn"),
                    os.getenv("P2_BPMN_FILE", "bpmn/transport.bpmn"),
                    os.getenv("P3_BPMN_FILE", "bpmn/receiving.bpmn"),
                    os.getenv("P5_BPMN_FILE", "bpmn/corrective_fulfilment.bpmn"),
                    os.getenv("P6_BPMN_FILE", "bpmn/supplier_claim.bpmn"),
                )
                break
            except Exception as error:
                if attempt in (0, 59):
                    app.logger.warning(
                        "BPMN deployment attempt %s failed: %r", attempt + 1, error
                    )
                if attempt == 59:
                    raise
                await asyncio.sleep(2)
        ready.set()
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(deliver_messages())
            tasks.create_task(monitor_containers())
            tasks.create_task(refresh_weather())
            tasks.create_task(deliver_container_observations())
            if os.getenv("P2_SIMULATOR_ENABLED", "true").lower() == "true":
                tasks.create_task(advance_simulated_journeys())
            tasks.create_task(worker.work())
    finally:
        ready.clear()
        await channel.close()


if __name__ == "__main__":
    asyncio.run(main())
