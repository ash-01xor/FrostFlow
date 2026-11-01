"""Shipment preparation and the initial transport handoff."""

import asyncio
import os
import re
import threading
from datetime import datetime

import grpc
from flask import Flask, jsonify, request
from pyzeebe import Job, ZeebeClient, ZeebeWorker
from pyzeebe.errors import MessageAlreadyExistsError

from registry import Conflict, Registry

PROCESS_ID = "ShipmentPreparation"
JOB_TYPES = [
    "p1-check-stock",
    "p1-allocate-stock",
    "p1-assign-transport",
    "p1-record-loading",
    "p1-dispatch",
    "p1-cancel",
    "p2-start-transport",
]
app = Flask(__name__)
registry = Registry(os.getenv("REGISTRY_PATH", "runtime/registry.sqlite"))
ready = threading.Event()
client = None
event_loop = None


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("send a JSON object")
    return data


def text_field(data, field):
    value = data.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise ValueError(
            f"{field} must be a non-empty string of at most 100 characters"
        )
    return value.strip()


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
        stage=2,
        processId=PROCESS_ID,
        jobTypes=JOB_TYPES,
    ), 200 if ready.is_set() else 503


@app.get("/shipments/<shipment_id>")
def shipment(shipment_id):
    return jsonify(registry.get(shipment_id))


@app.get("/shipments/<shipment_id>/events")
def events(shipment_id):
    return jsonify(registry.events(shipment_id))


@app.get("/stock")
def stock():
    return jsonify(registry.inventory())


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


async def check_stock(shipmentId, job: Job):
    return registry.check_stock(shipmentId, job.process_instance_key, job.key)


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


async def deliver_messages():
    """Retry unsent durable commands; successful dispatch is acknowledged by P2's worker."""
    while True:
        for message in registry.pending_messages():
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
        await asyncio.sleep(1)


async def main():
    global client, event_loop
    event_loop = asyncio.get_running_loop()
    channel = grpc.aio.insecure_channel(os.getenv("ZEEBE_ADDRESS", "localhost:26501"))
    client = ZeebeClient(grpc_channel=channel)
    worker = ZeebeWorker(grpc_channel=channel)
    for job_type, handler in zip(
        JOB_TYPES,
        (
            check_stock,
            allocate_stock,
            assign_transport,
            record_loading,
            dispatch_shipment,
            cancel_shipment,
            start_transport,
        ),
    ):
        variables = (
            ["shipmentId", "dispatchEvent"]
            if job_type == "p2-start-transport"
            else ["shipmentId"]
        )
        worker.task(task_type=job_type, variables_to_fetch=variables)(handler)
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=5001, threaded=True), daemon=True
    ).start()
    try:
        for attempt in range(60):
            try:
                await client.deploy_resource(
                    os.getenv("BPMN_FILE", "bpmn/shipment_preparation.bpmn"),
                    os.getenv("P2_BPMN_FILE", "bpmn/transport.bpmn"),
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
            tasks.create_task(worker.work())
    finally:
        ready.clear()
        await channel.close()


if __name__ == "__main__":
    asyncio.run(main())
