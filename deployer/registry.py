"""Transactional shipment state, demo inventory and a durable message outbox.

Zeebe owns control flow. SQLite stores business effects and retry identities.
Run one communication instance; preserve this database with its engine volumes.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def epoch_milliseconds(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(parsed.timestamp() * 1000)


class Conflict(Exception):
    pass


class Registry:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='inventory'"
            ).fetchone():
                columns = {row[1] for row in db.execute("PRAGMA table_info(inventory)")}
                if "product_id" not in columns:
                    raise ValueError(
                        "This registry uses legacy pooled stock. Preserve it with its engine "
                        "volumes and start a fresh Compose project/REGISTRY_DIR for the "
                        "five-product dataset; automatic conversion would invent product identities."
                    )
            db.execute(
                "CREATE TABLE IF NOT EXISTS shipments (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS results (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS inventory (product_id TEXT NOT NULL, "
                "batch_id TEXT NOT NULL, available INTEGER NOT NULL CHECK(available >= 0), "
                "PRIMARY KEY(product_id,batch_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS products (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            if not db.execute("SELECT 1 FROM products").fetchone():
                catalog = json.loads(
                    Path(__file__).with_name("catalog.json").read_text()
                )
                for product in catalog["products"]:
                    details = {
                        **product,
                        "quantityUnit": catalog["quantityUnit"],
                        "temperatureProfileId": catalog["temperatureProfileId"],
                    }
                    db.execute(
                        "INSERT INTO products VALUES (?,?)",
                        (product["productId"], json.dumps(details)),
                    )
                    db.execute(
                        "INSERT INTO inventory VALUES (?,?,?)",
                        (
                            product["productId"],
                            product["batchId"],
                            product["initialCrates"],
                        ),
                    )

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def read(db, shipment_id):
        row = db.execute(
            "SELECT data FROM shipments WHERE id=?", (shipment_id,)
        ).fetchone()
        if row is None:
            raise KeyError(shipment_id)
        return json.loads(row[0])

    @staticmethod
    def save(db, record):
        db.execute(
            "INSERT OR REPLACE INTO shipments VALUES (?,?)",
            (record["shipmentId"], json.dumps(record)),
        )

    @staticmethod
    def event(
        db,
        record,
        activity,
        actor="system",
        process="P1",
        event_id=None,
        payload=None,
        occurred_at=None,
        recorded_at=None,
        source=None,
        input_mode=None,
        producer_event_id=None,
    ):
        event_id = event_id or f"{process}:{record['shipmentId']}:{activity}"
        row = db.execute("SELECT data FROM events WHERE id=?", (event_id,)).fetchone()
        if row:
            return json.loads(row[0])
        stamp = now()
        identity = {
            k: record[k]
            for k in (
                "productId",
                "productName",
                "batchId",
                "quantityUnit",
                "packDescription",
                "packsPerCrate",
                "temperatureProfileId",
            )
        }
        event = dict(
            eventId=event_id,
            eventType=activity,
            shipmentId=record["shipmentId"],
            journeyId=record["journeyId"],
            occurredAt=occurred_at or stamp,
            recordedAt=recorded_at or stamp,
            source=source or ("preparation" if process == "P1" else "transport"),
            inputMode=input_mode or ("system" if actor == "system" else "manual"),
            actor=actor,
            role="integration" if actor == "system" else "operator",
            outcome="success",
            process=process,
            processInstanceKey=record.get("processInstanceKey")
            if process == "P1"
            else record.get("p2ProcessInstanceKey"),
            payload={
                **identity,
                **(
                    payload
                    if payload is not None
                    else {
                        k: record[k]
                        for k in (
                            "vehicleId",
                            "recipientId",
                            "routeId",
                            "deliveryDeadline",
                            "quantity",
                        )
                    }
                ),
            },
        )
        for optional in ("originalShipmentId", "incidentId"):
            if optional in record:
                event[optional] = record[optional]
        if producer_event_id is not None:
            event["producerEventId"] = producer_event_id
        db.execute("INSERT INTO events VALUES (?,?)", (event_id, json.dumps(event)))
        return event

    @staticmethod
    def transport_event_journal_id(shipment_id, event_type, producer_event_id):
        """Keep producer IDs in a namespace separate from internal activities."""
        return f"P2:external:{event_type}:{shipment_id}:{producer_event_id}"

    @classmethod
    def migrate_legacy_transport_event(
        cls, db, shipment_id, event_type, producer_event_id
    ):
        """Move a pre-namespace external event off a colliding internal ID."""
        legacy = db.execute(
            "SELECT data FROM events WHERE id=?", (producer_event_id,)
        ).fetchone()
        if legacy is None:
            return False
        event = json.loads(legacy[0])
        if event.get("shipmentId") != shipment_id or event.get("eventType") != event_type:
            return False
        journal_id = cls.transport_event_journal_id(
            shipment_id, event_type, producer_event_id
        )
        if db.execute("SELECT 1 FROM events WHERE id=?", (journal_id,)).fetchone():
            db.execute("DELETE FROM events WHERE id=?", (producer_event_id,))
            return True
        event["eventId"] = journal_id
        event["producerEventId"] = producer_event_id
        db.execute(
            "UPDATE events SET id=?, data=? WHERE id=?",
            (journal_id, json.dumps(event), producer_event_id),
        )
        return True

    @staticmethod
    def enqueue(
        db, message_id, name, correlation, variables, needs_ack=False,
        destination="zeebe",
    ):
        data = dict(
            messageId=message_id,
            name=name,
            correlationKey=correlation,
            variables=variables,
            status="PENDING",
            createdAt=now(),
            needsAcknowledgement=needs_ack,
            destination=destination,
            lastError=None,
        )
        db.execute(
            "INSERT OR IGNORE INTO outbox VALUES (?,?)", (message_id, json.dumps(data))
        )

    def get(self, shipment_id):
        with self.transaction() as db:
            return self.read(db, shipment_id)

    def inventory(self):
        with self.transaction() as db:
            products = [
                dict(json.loads(raw), available=available)
                for raw, available in db.execute(
                    "SELECT p.data,i.available FROM products p JOIN inventory i "
                    "ON p.id=i.product_id ORDER BY p.id,i.batch_id"
                )
            ]
            return {
                "datasetVersion": 3,
                "quantityUnit": "crate",
                "available": sum(p["available"] for p in products),
                "products": products,
                "shipmentCount": db.execute(
                    "SELECT count(*) FROM shipments"
                ).fetchone()[0],
            }

    @staticmethod
    def product(db, product_id, batch_id):
        row = db.execute(
            "SELECT data FROM products WHERE id=?", (product_id,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown productId; choose a product from GET /stock")
        details = json.loads(row[0])
        if details["batchId"] != batch_id:
            raise ValueError("batchId does not belong to the selected product")
        return details

    def create(self, data):
        with self.transaction() as db:
            details = self.product(db, data.get("productId"), data.get("batchId"))
            records = [
                json.loads(r[0]) for r in db.execute("SELECT data FROM shipments")
            ]
            if any(r["shipmentId"] == data["shipmentId"] for r in records):
                raise Conflict("shipmentId already exists; inspect its state")
            if any(r["journeyId"] == data["journeyId"] for r in records):
                raise Conflict("journeyId already exists")
            if any(
                r["vehicleId"] == data["vehicleId"]
                and not r.get("vehicleReleased", False)
                for r in records
            ):
                raise Conflict("vehicle is reserved by another shipment")
            record = dict(
                data,
                state="START_REQUESTED",
                qualityStatus="CLEAR",
                processInstanceKey=None,
                pendingCommand=None,
                stockReserved=False,
                vehicleReleased=False,
                stockAttempt=0,
                schemaVersion=3,
            )
            record.update({k: v for k, v in details.items() if k != "initialCrates"})
            self.save(db, record)
            self.event(db, record, "ShipmentCreated")

    def attach_key(self, shipment_id, key):
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            if record["processInstanceKey"] not in (None, str(key)):
                raise Conflict("shipment belongs to a different engine instance")
            record["processInstanceKey"] = str(key)
            self.save(db, record)
            row = db.execute(
                "SELECT data FROM events WHERE id=?",
                (f"P1:{shipment_id}:ShipmentCreated",),
            ).fetchone()
            if row:
                event = json.loads(row[0])
                event["processInstanceKey"] = str(key)
                db.execute(
                    "UPDATE events SET data=? WHERE id=?",
                    (json.dumps(event), event["eventId"]),
                )

    def check_stock(self, shipment_id, key, job_key):
        result_id = f"stock-check:{job_key}"
        with self.transaction() as db:
            previous = db.execute(
                "SELECT data FROM results WHERE id=?", (result_id,)
            ).fetchone()
            if previous:
                return json.loads(previous[0])
            record = self.read(db, shipment_id)
            if record["processInstanceKey"] not in (None, str(key)):
                raise Conflict("wrong engine instance")
            if record["state"] not in ("START_REQUESTED", "WAITING_STOCK"):
                raise Conflict("stock check requires a new shipment or stock wait")
            if (
                record["state"] == "WAITING_STOCK"
                and (record.get("pendingCommand") or {}).get("name")
                != "StockReplenished"
            ):
                raise Conflict("stock recheck requires an accepted replenishment")
            record["processInstanceKey"] = str(key)
            available = db.execute(
                "SELECT available FROM inventory WHERE product_id=? AND batch_id=?",
                (record["productId"], record["batchId"]),
            ).fetchone()[0]
            enough = available >= record["quantity"]
            record["stockAttempt"] += 1
            self.event(
                db,
                record,
                "StockChecked",
                event_id=f"P1:{shipment_id}:StockChecked:{job_key}",
                payload={
                    "available": available,
                    "required": record["quantity"],
                    "stockAvailable": enough,
                },
            )
            if enough:
                db.execute(
                    "UPDATE inventory SET available=available-? WHERE product_id=? AND batch_id=?",
                    (record["quantity"], record["productId"], record["batchId"]),
                )
                record.update(
                    state="STOCK_RESERVED", stockReserved=True, pendingCommand=None
                )
            else:
                record.update(
                    state="WAITING_STOCK",
                    pendingCommand=None,
                    stockWaitKey=f"{shipment_id}:stock:{record['stockAttempt']}",
                )
                self.event(
                    db,
                    record,
                    "StockUnavailable",
                    event_id=f"P1:{shipment_id}:StockUnavailable:{job_key}",
                )
            self.save(db, record)
            result = {
                "stockAvailable": enough,
                "stockWaitKey": record.get("stockWaitKey", ""),
            }
            db.execute(
                "INSERT INTO results VALUES (?,?)", (result_id, json.dumps(result))
            )
            return result

    def reserve_command(self, shipment_id, command, actor, replenishment=None):
        required = {
            "LoadingVerified": "AWAITING_LOADING",
            "DispatchApproved": "AWAITING_DISPATCH",
            "StockReplenished": "WAITING_STOCK",
            "ShipmentCancellationRequested": "WAITING_STOCK",
        }
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            # Repeating a replenishment ID is harmless only with identical content.
            if replenishment:
                external_id, quantity = replenishment
                event_id = f"SUPPLY:{external_id}"
                old = db.execute(
                    "SELECT data FROM events WHERE id=?", (event_id,)
                ).fetchone()
                if old:
                    event = json.loads(old[0])
                    if (
                        event["shipmentId"],
                        event["eventType"],
                        event["actor"],
                        event["payload"],
                    ) != (
                        shipment_id,
                        "StockReplenished",
                        actor,
                        {
                            **{
                                k: record[k]
                                for k in (
                                    "productId",
                                    "productName",
                                    "batchId",
                                    "quantityUnit",
                                    "packDescription",
                                    "packsPerCrate",
                                    "temperatureProfileId",
                                )
                            },
                            "quantity": quantity,
                        },
                    ):
                        raise Conflict(
                            "eventId reused with different replenishment data"
                        )
                    return record
            if record["pendingCommand"] or record["state"] != required[command]:
                raise Conflict(
                    f"{command} requires {required[command]} and no pending command"
                )
            record["pendingCommand"] = dict(
                name=command, actor=actor, requestedAt=now()
            )
            correlation = (
                record.get("stockWaitKey", shipment_id)
                if required[command] == "WAITING_STOCK"
                else shipment_id
            )
            message_id = f"{correlation}:{command}"
            if replenishment:
                external_id, quantity = replenishment
                event_id = f"SUPPLY:{external_id}"
                db.execute(
                    "UPDATE inventory SET available=available+? WHERE product_id=? AND batch_id=?",
                    (quantity, record["productId"], record["batchId"]),
                )
                self.event(
                    db,
                    record,
                    "StockReplenished",
                    actor,
                    event_id=event_id,
                    payload={"quantity": quantity},
                )
            self.enqueue(
                db, message_id, command, correlation, {"shipmentId": shipment_id}
            )
            self.save(db, record)
            return record

    def advance(self, shipment_id, key, expected, target, activity, actor="system"):
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            if record["processInstanceKey"] not in (None, str(key)):
                raise Conflict("wrong engine instance")
            if db.execute(
                "SELECT 1 FROM events WHERE id=?", (f"P1:{shipment_id}:{activity}",)
            ).fetchone():
                return record
            expected = (expected,) if isinstance(expected, str) else expected
            if record["state"] not in expected:
                raise Conflict(
                    f"{activity} requires {expected}, found {record['state']}"
                )
            commands = {
                "LoadingVerified": "LoadingVerified",
                "ShipmentDispatched": "DispatchApproved",
                "ShipmentCancelled": "ShipmentCancellationRequested",
            }
            if activity in commands:
                pending = record["pendingCommand"]
                if not pending or pending["name"] != commands[activity]:
                    raise Conflict("required command was not accepted")
                actor = pending["actor"]
            if (
                activity in ("StockAllocated", "LoadingVerified", "ShipmentDispatched")
                and not record["stockReserved"]
            ):
                raise Conflict("stock has not been reserved")
            record.update(
                state=target, processInstanceKey=str(key), pendingCommand=None
            )
            if activity == "ShipmentCancelled":
                record["vehicleReleased"] = True
            event = self.event(db, record, activity, actor)
            if activity == "ShipmentDispatched":
                self.enqueue(
                    db,
                    event["eventId"],
                    "ShipmentDispatched",
                    shipment_id,
                    {"shipmentId": shipment_id, "dispatchEvent": event},
                    needs_ack=True,
                )
                record["handoffStatus"] = "PENDING"
            self.save(db, record)
            return record

    def pending_messages(self, destination=None):
        with self.transaction() as db:
            pending = []
            for message_id, raw in db.execute(
                "SELECT id,data FROM outbox ORDER BY rowid"
            ).fetchall():
                data = json.loads(raw)
                if data["status"] != "PENDING":
                    continue
                if destination is not None and data.get("destination", "zeebe") != destination:
                    continue
                age = datetime.now(timezone.utc) - datetime.fromisoformat(
                    data["createdAt"].replace("Z", "+00:00")
                )
                if (
                    data.get("destination", "zeebe") == "zeebe"
                    and age.total_seconds() >= 86400
                ):
                    data.update(
                        status="EXPIRED",
                        lastError="Uncertain message expired; inspect engine before recovery",
                    )
                    db.execute(
                        "UPDATE outbox SET data=? WHERE id=?",
                        (json.dumps(data), message_id),
                    )
                else:
                    pending.append(data)
            return pending

    def message_result(self, message_id, error=None):
        with self.transaction() as db:
            row = db.execute(
                "SELECT data FROM outbox WHERE id=?", (message_id,)
            ).fetchone()
            data = json.loads(row[0])
            if data["status"] == "ACKNOWLEDGED":
                return
            status = "PENDING" if error else (
                "ACKNOWLEDGED" if data.get("destination") in ("p3-http", "siddhi-http") else "PUBLISHED"
            )
            data.update(status=status, lastError=error)
            db.execute(
                "UPDATE outbox SET data=? WHERE id=?", (json.dumps(data), message_id)
            )

    def start_transport(self, shipment_id, key, dispatch_event):
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            saved = db.execute(
                "SELECT data FROM events WHERE id=?",
                (f"P1:{shipment_id}:ShipmentDispatched",),
            ).fetchone()
            if (
                record["state"] != "DISPATCHED"
                or not saved
                or json.loads(saved[0]) != dispatch_event
            ):
                raise Conflict("transport requires the recorded dispatch event")
            if record.get("p2ProcessInstanceKey") not in (None, str(key)):
                raise Conflict(
                    "duplicate transport instance blocked by persistent inbox"
                )
            if record.get("p2ProcessInstanceKey") is None:
                record.update(
                    p2ProcessInstanceKey=str(key),
                    transportState="AWAITING_JOURNEY_START",
                )
            # An acknowledgement retry must preserve progress made by P2 after
            # its first registration, including eventual vehicle release.
            record["handoffStatus"] = "ACKNOWLEDGED"
            self.event(db, record, "TransportStarted", process="P2")
            self.save(db, record)
            row = db.execute(
                "SELECT data FROM outbox WHERE id=?", (dispatch_event["eventId"],)
            ).fetchone()
            data = json.loads(row[0])
            data["status"] = "ACKNOWLEDGED"
            db.execute(
                "UPDATE outbox SET data=? WHERE id=?",
                (json.dumps(data), dispatch_event["eventId"]),
            )
            return {
                k: record[k]
                for k in (
                    "shipmentId",
                    "journeyId",
                    "vehicleId",
                    "recipientId",
                    "routeId",
                    "deliveryDeadline",
                    "quantity",
                    "productId",
                    "productName",
                    "batchId",
                    "quantityUnit",
                    "packDescription",
                    "packsPerCrate",
                    "temperatureProfileId",
                )
            }

    def queue_transport_event(
        self, shipment_id, event_type, event_id, occurred_at, payload, source,
        input_mode="manual",
    ):
        """Persist one manual/test P2 message and its retry identity atomically."""
        expected = {
            "ShipmentArrived": "IN_TRANSIT",
            "ReceiptAcknowledged": "ARRIVED",
        }
        if event_type not in expected:
            raise ValueError("unsupported P2 transport event")
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            identity = f"transport-input:{shipment_id}:{event_type}"
            old = db.execute(
                "SELECT data FROM results WHERE id=?", (identity,)
            ).fetchone()
            if old:
                accepted = json.loads(old[0])
                previous = accepted["event"]
                same = all(
                    previous.get(field) == value
                    for field, value in {
                        "eventId": event_id,
                        "eventType": event_type,
                        "shipmentId": shipment_id,
                        "journeyId": record["journeyId"],
                        "occurredAt": occurred_at,
                        "source": source,
                        "inputMode": input_mode,
                        "payload": payload,
                    }.items()
                )
                if not same:
                    raise Conflict(
                        f"{event_type} already accepted; eventId or payload differs"
                    )
                return accepted

            if not record.get("p2ProcessInstanceKey"):
                raise Conflict("transport process has not registered this shipment")
            if record.get("transportState") != expected[event_type]:
                raise Conflict(
                    f"{event_type} requires {expected[event_type]}, "
                    f"found {record.get('transportState')}"
                )
            if payload.get("recipientId") != record["recipientId"]:
                raise Conflict("recipientId must match the dispatched shipment")
            if (
                event_type == "ReceiptAcknowledged"
                and payload.get("quantityReceived") != record["quantity"]
            ):
                raise Conflict(
                    "quantityReceived must match the dispatched crate quantity"
                )
            for (raw,) in db.execute(
                "SELECT data FROM results WHERE id LIKE 'transport-input:%'"
            ):
                queued_event = json.loads(raw)["event"]
                if queued_event["eventId"] == event_id:
                    raise Conflict("eventId is already queued for another event")

            event = dict(
                eventId=event_id,
                eventType=event_type,
                shipmentId=shipment_id,
                journeyId=record["journeyId"],
                occurredAt=occurred_at,
                recordedAt=now(),
                source=source,
                inputMode=input_mode,
                payload=payload,
            )
            variable = (
                "arrivalEvent"
                if event_type == "ShipmentArrived"
                else "receiptEvent"
            )
            message_id = f"P2:{shipment_id}:{event_type}:{event_id}"
            accepted = {"messageId": message_id, "event": event}
            self.enqueue(
                db,
                message_id,
                event_type,
                shipment_id,
                {"shipmentId": shipment_id, variable: event},
            )
            db.execute(
                "INSERT INTO results VALUES (?,?)",
                (identity, json.dumps(accepted)),
            )
            return accepted

    @staticmethod
    def require_transport_event(db, shipment_id, event_type, event):
        identity = f"transport-input:{shipment_id}:{event_type}"
        row = db.execute("SELECT data FROM results WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise Conflict(f"{event_type} was not accepted by the manual/test input")
        accepted = json.loads(row[0])["event"]
        if accepted != event:
            raise Conflict(f"{event_type} does not match its accepted message")
        return accepted

    @staticmethod
    def require_transport_instance(record, key):
        if record.get("p2ProcessInstanceKey") != str(key):
            raise Conflict("wrong P2 engine instance")

    def start_journey(
        self, shipment_id, key, position=None, eta=None, eta_source=None,
        route_coordinates=None,
    ):
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            self.require_transport_instance(record, key)
            if db.execute(
                "SELECT 1 FROM events WHERE id=?",
                (f"P2:{shipment_id}:JourneyStarted",),
            ).fetchone():
                return record
            if record.get("transportState") != "AWAITING_JOURNEY_START":
                raise Conflict(
                    "journey can start only after transport handoff registration"
                )
            record["transportState"] = "IN_TRANSIT"
            if position is not None:
                record.update(
                    currentPosition=position,
                    routeCoordinates=route_coordinates,
                    routeIndex=0,
                    eta=eta,
                    etaSource=eta_source,
                    telemetryUpdatedAt=now(),
                )
            self.event(db, record, "JourneyStarted", process="P2")
            self.save(db, record)
            return record

    def in_transit_shipments(self):
        with self.transaction() as db:
            return [
                json.loads(raw) for (raw,) in db.execute("SELECT data FROM shipments")
                if json.loads(raw).get("transportState") == "IN_TRANSIT"
            ]

    def advance_telemetry(self, shipment_id, position, route_index, eta, eta_source):
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            if record.get("transportState") != "IN_TRANSIT":
                return record
            record.update(
                currentPosition=position,
                routeIndex=route_index,
                eta=eta,
                etaSource=eta_source,
                telemetryUpdatedAt=now(),
            )
            revision = record.get("routeRevision", 0)
            gps_event = self.event(
                db, record, "GPSPositionUpdated", process="P2",
                event_id=f"P2:{shipment_id}:GPSPositionUpdated:{revision}:{route_index}",
                payload={"position": position, "routeId": record["routeId"], "routeIndex": route_index},
            )
            record["lastGpsEventOccurredAt"] = gps_event["occurredAt"]
            self.enqueue(db, gps_event["eventId"], "TelemetryUpdate", shipment_id,
                         {"event": gps_event}, destination="siddhi-http")
            eta_event = self.event(
                db, record, "ETAUpdated", process="P2",
                event_id=f"P2:{shipment_id}:ETAUpdated:{revision}:{route_index}",
                payload={
                    "eta": eta,
                    "etaSource": eta_source,
                    "etaMillis": epoch_milliseconds(eta),
                    "deliveryDeadline": record["deliveryDeadline"],
                    "deliveryDeadlineMillis": epoch_milliseconds(record["deliveryDeadline"]),
                    "routeId": record["routeId"],
                    "routeRevision": revision,
                },
            )
            self.enqueue(db, eta_event["eventId"], "TelemetryUpdate", shipment_id,
                         {"event": eta_event}, destination="siddhi-http")
            self.save(db, record)
            return record

    def queue_delivery_risk(self, shipment_id, event_id, payload):
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            identity = f"delivery-risk:{shipment_id}:{event_id}"
            old = db.execute("SELECT data FROM results WHERE id=?", (identity,)).fetchone()
            if not record.get("p2ProcessInstanceKey"):
                raise Conflict("transport process has not registered this shipment")
            event = {
                "eventId": event_id, "eventType": "DeliveryRiskDetected",
                "shipmentId": shipment_id, "journeyId": record["journeyId"],
                "occurredAt": payload["occurredAt"], "payload": payload,
            }
            if old:
                accepted = json.loads(old[0])
                if accepted["event"] == event:
                    return accepted
                raise Conflict("eventId was already used with different risk details")
            if record.get("transportState") != "IN_TRANSIT":
                raise Conflict("delivery risk requires an in-transit shipment")
            if record.get("pendingDeliveryRisk"):
                raise Conflict("a delivery-risk decision is already pending")
            message_id = f"P2:{shipment_id}:DeliveryRiskDetected:{event_id}"
            accepted = {"messageId": message_id, "event": event}
            record["pendingDeliveryRisk"] = event_id
            self.enqueue(db, message_id, "DeliveryRiskDetected", shipment_id,
                         {"shipmentId": shipment_id, "deliveryRiskEvent": event})
            db.execute("INSERT INTO results VALUES (?,?)", (identity, json.dumps(accepted)))
            self.save(db, record)
            return accepted

    def record_delivery_risk(self, shipment_id, key, risk_event):
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            self.require_transport_instance(record, key)
            row = db.execute("SELECT data FROM results WHERE id=?",
                             (f"delivery-risk:{shipment_id}:{risk_event['eventId']}",)).fetchone()
            if not row or json.loads(row[0])["event"] != risk_event:
                raise Conflict("delivery risk was not accepted by the integration endpoint")
            event_id = f"P2:{shipment_id}:DeliveryRiskDetected:{risk_event['eventId']}"
            self.event(db, record, "DeliveryRiskDetected", process="P2", event_id=event_id,
                       payload=risk_event["payload"], occurred_at=risk_event["occurredAt"],
                       producer_event_id=risk_event["eventId"])
            self.save(db, record)
            return record

    def queue_dispatcher_decision(self, shipment_id, event_id, decision, route_id=None):
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            risk_id = record.get("pendingDeliveryRisk")
            if not risk_id or record.get("transportState") != "IN_TRANSIT":
                for (raw,) in db.execute(
                    "SELECT data FROM results WHERE id LIKE ?",
                    (f"dispatcher-decision:{shipment_id}:%",),
                ):
                    accepted = json.loads(raw)
                    event = accepted["event"]
                    if event["eventId"] == event_id and event["decision"] == decision and event.get("routeId") == route_id:
                        if accepted.get("status") == "SUPERSEDED":
                            raise Conflict("arrival closed the delivery-risk decision")
                        return accepted
                raise Conflict("no delivery-risk decision is waiting")
            for (raw,) in db.execute(
                "SELECT data FROM results WHERE id LIKE ?",
                (f"dispatcher-decision:{shipment_id}:%",),
            ):
                previous = json.loads(raw)["event"]
                if previous["eventId"] == event_id and previous["riskEventId"] != risk_id:
                    raise Conflict("eventId was already used for another risk decision")
            identity = f"dispatcher-decision:{shipment_id}:{risk_id}"
            old = db.execute("SELECT data FROM results WHERE id=?", (identity,)).fetchone()
            if old:
                accepted = json.loads(old[0])
                if accepted["event"]["eventId"] == event_id and accepted["event"]["decision"] == decision and accepted["event"].get("routeId") == route_id:
                    return accepted
                raise Conflict("a dispatcher decision is already accepted for this alert")
            if decision not in ("ACCEPT_DELAY", "APPROVE_ALTERNATIVE_ROUTE"):
                raise ValueError("decision must be ACCEPT_DELAY or APPROVE_ALTERNATIVE_ROUTE")
            if decision == "APPROVE_ALTERNATIVE_ROUTE":
                if route_id not in ("route-01", "route-02", "route-03") or route_id == record["routeId"]:
                    raise ValueError("routeId must name a different configured route")
            elif route_id is not None:
                raise ValueError("routeId is only valid for an alternative-route decision")
            event = {"eventId": event_id, "shipmentId": shipment_id,
                     "journeyId": record["journeyId"], "riskEventId": risk_id,
                     "decision": decision, "routeId": route_id, "occurredAt": now()}
            message_id = f"P2:{shipment_id}:DispatcherDecisionRecorded:{event_id}"
            accepted = {"messageId": message_id, "event": event}
            self.enqueue(db, message_id, "DispatcherDecisionRecorded", shipment_id,
                         {"shipmentId": shipment_id, "dispatcherDecision": event})
            db.execute("INSERT INTO results VALUES (?,?)", (identity, json.dumps(accepted)))
            return accepted

    def apply_dispatcher_decision(
        self, shipment_id, key, decision_event, acknowledgement,
        route_coordinates=None, eta=None, eta_source=None,
    ):
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            self.require_transport_instance(record, key)
            row = db.execute("SELECT data FROM results WHERE id=?",
                             (f"dispatcher-decision:{shipment_id}:{decision_event['riskEventId']}",)).fetchone()
            if not row or json.loads(row[0])["event"] != decision_event:
                raise Conflict("dispatcher decision was not accepted")
            if acknowledgement.get("commandId") != decision_event["eventId"] or acknowledgement.get("simulatorAcknowledged") is not True:
                raise Conflict("simulator acknowledgement is required before applying the decision")
            if db.execute("SELECT 1 FROM events WHERE id=?",
                          (f"P2:{shipment_id}:DispatcherDecision:{decision_event['eventId']}",)).fetchone():
                return record
            if record.get("transportState") != "IN_TRANSIT" or record.get("pendingDeliveryRisk") != decision_event["riskEventId"]:
                raise Conflict("decision no longer matches the pending delivery risk")
            if decision_event["decision"] == "APPROVE_ALTERNATIVE_ROUTE":
                route_coordinates = list(route_coordinates or [])
                if record.get("currentPosition") and route_coordinates:
                    route_coordinates[0] = record["currentPosition"]
                record.update(
                    routeId=decision_event["routeId"], routeIndex=0,
                    routeCoordinates=route_coordinates,
                    routeRevision=record.get("routeRevision", 0) + 1,
                    eta=eta, etaSource=eta_source,
                )
            record.pop("pendingDeliveryRisk", None)
            self.event(db, record, "DispatcherDecisionRecorded", process="P2",
                       event_id=f"P2:{shipment_id}:DispatcherDecision:{decision_event['eventId']}",
                       payload={"decision": decision_event["decision"], "routeId": decision_event.get("routeId"),
                                "simulatorAcknowledgement": acknowledgement})
            if decision_event["decision"] == "APPROVE_ALTERNATIVE_ROUTE":
                self.event(
                    db, record, "RouteChangeAcknowledged", process="P2",
                    event_id=f"P2:{shipment_id}:RouteChangeAcknowledged:{decision_event['eventId']}",
                    payload={"commandId": decision_event["eventId"],
                             "routeId": decision_event["routeId"],
                             "simulatorAcknowledgement": acknowledgement},
                )
            self.save(db, record)
            return record

    def acknowledge_simulator_command(self, command):
        """Durably accept one route command for the local scripted simulator."""
        with self.transaction() as db:
            record = self.read(db, command["shipmentId"])
            identity = f"simulator-command:{command['commandId']}"
            old = db.execute("SELECT data FROM results WHERE id=?", (identity,)).fetchone()
            if old:
                accepted = json.loads(old[0])
                if accepted["command"] != command:
                    raise Conflict("commandId was already used with different command data")
                return accepted["acknowledgement"]
            if record.get("transportState") != "IN_TRANSIT":
                raise Conflict("simulator route command requires an in-transit shipment")
            if record.get("journeyId") != command["journeyId"]:
                raise Conflict("journeyId does not match the shipment")
            if record.get("pendingDeliveryRisk") != command["riskEventId"]:
                raise Conflict("route command does not match the pending risk decision")
            if command["command"] == "ChangeRoute":
                if command.get("routeId") not in ("route-01", "route-02", "route-03"):
                    raise ValueError("routeId must name a configured route")
                if command["routeId"] == record.get("routeId"):
                    raise Conflict("route command must select a different route")
            elif command["command"] == "AcceptDelay":
                if command.get("routeId") is not None:
                    raise ValueError("AcceptDelay must not include routeId")
            else:
                raise ValueError("command must be AcceptDelay or ChangeRoute")
            acknowledgement = {
                "commandId": command["commandId"],
                "simulatorAcknowledged": True,
                "simulator": "scripted-demo",
            }
            db.execute(
                "INSERT INTO results VALUES (?,?)",
                (identity, json.dumps({"command": command, "acknowledgement": acknowledgement})),
            )
            return acknowledgement

    def record_arrival(self, shipment_id, key, arrival_event):
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            self.require_transport_instance(record, key)
            accepted = self.require_transport_event(
                db, shipment_id, "ShipmentArrived", arrival_event
            )
            journal_id = self.transport_event_journal_id(
                shipment_id, "ShipmentArrived", accepted["eventId"]
            )
            self.migrate_legacy_transport_event(
                db, shipment_id, "ShipmentArrived", accepted["eventId"]
            )
            if db.execute(
                "SELECT 1 FROM events WHERE id=?", (journal_id,)
            ).fetchone():
                return record
            if record.get("transportState") != "IN_TRANSIT":
                raise Conflict(
                    f"arrival recording requires IN_TRANSIT, "
                    f"found {record.get('transportState')}"
                )
            record["transportState"] = "ARRIVED"
            risk_id = record.pop("pendingDeliveryRisk", None)
            if risk_id:
                decision_row = db.execute(
                    "SELECT data FROM results WHERE id=?",
                    (f"dispatcher-decision:{shipment_id}:{risk_id}",),
                ).fetchone()
                if decision_row:
                    decision = json.loads(decision_row[0])
                    decision["status"] = "SUPERSEDED"
                    db.execute(
                        "UPDATE results SET data=? WHERE id=?",
                        (json.dumps(decision), f"dispatcher-decision:{shipment_id}:{risk_id}"),
                    )
                    outbox_id = decision.get("messageId")
                    if outbox_id:
                        outbox_row = db.execute(
                            "SELECT data FROM outbox WHERE id=?", (outbox_id,)
                        ).fetchone()
                        if outbox_row:
                            outbox_data = json.loads(outbox_row[0])
                            if outbox_data.get("status") == "PENDING":
                                outbox_data.update(status="SUPERSEDED", lastError="arrival closed the decision")
                                db.execute(
                                    "UPDATE outbox SET data=? WHERE id=?",
                                    (json.dumps(outbox_data), outbox_id),
                                )
                self.event(
                    db, record, "DeliveryRiskClosedAtArrival", process="P2",
                    event_id=f"P2:{shipment_id}:DeliveryRiskClosedAtArrival:{risk_id}",
                    payload={"riskEventId": risk_id},
                )
            self.event(
                db,
                record,
                "ShipmentArrived",
                actor=accepted["source"],
                process="P2",
                event_id=journal_id,
                payload=accepted["payload"],
                occurred_at=accepted["occurredAt"],
                recorded_at=accepted["recordedAt"],
                source=accepted["source"],
                input_mode=accepted["inputMode"],
                producer_event_id=accepted["eventId"],
            )
            self.save(db, record)
            notification_id = f"P3:ShipmentArrivalNotification:{shipment_id}:{accepted['eventId']}"
            notification = {
                "deliveryId": notification_id,
                "eventType": "ShipmentArrivalNotification",
                "shipmentId": shipment_id,
                "journeyId": record["journeyId"],
                "productId": record["productId"],
                "batchId": record["batchId"],
                "arrival": accepted["payload"],
                "sourceEventId": accepted["eventId"],
                "occurredAt": accepted["occurredAt"],
                "recordedAt": accepted["recordedAt"],
            }
            for ref in ("originalShipmentId", "incidentId"):
                if ref in record:
                    notification[ref] = record[ref]
            self.enqueue(db, notification_id, "ShipmentArrivalNotification", shipment_id,
                         {"event": notification}, needs_ack=True, destination="p3-http")
            return record

    def release_vehicle(self, shipment_id, key, receipt_event):
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            self.require_transport_instance(record, key)
            accepted = self.require_transport_event(
                db, shipment_id, "ReceiptAcknowledged", receipt_event
            )
            receipt_journal_id = self.transport_event_journal_id(
                shipment_id, "ReceiptAcknowledged", accepted["eventId"]
            )
            migrated_legacy_receipt = self.migrate_legacy_transport_event(
                db, shipment_id, "ReceiptAcknowledged", accepted["eventId"]
            )
            if record.get("vehicleReleased"):
                vehicle_event = db.execute(
                    "SELECT data FROM events WHERE id=?",
                    (f"P2:{shipment_id}:VehicleReleased",),
                ).fetchone()
                if vehicle_event is None:
                    receipt_row = db.execute(
                        "SELECT data FROM events WHERE id=?", (receipt_journal_id,)
                    ).fetchone()
                    legacy_receipt = (
                        json.loads(receipt_row[0]) if receipt_row is not None else {}
                    )
                    verified_legacy_collision = (
                        migrated_legacy_receipt
                        and record.get("transportState") == "COMPLETED"
                        and legacy_receipt.get("eventType") == "ReceiptAcknowledged"
                        and legacy_receipt.get("shipmentId") == shipment_id
                        and legacy_receipt.get("producerEventId") == accepted["eventId"]
                        and all(
                            legacy_receipt.get("payload", {}).get(field) == value
                            for field, value in accepted["payload"].items()
                        )
                        and legacy_receipt.get("occurredAt") == accepted["occurredAt"]
                    )
                    if not verified_legacy_collision:
                        raise Conflict("vehicle release state has no recorded activity")
                    self.event(db, record, "VehicleReleased", process="P2")
                    self.save(db, record)
                    return record
                if json.loads(vehicle_event[0]).get("eventType") != "VehicleReleased":
                    self.migrate_legacy_transport_event(
                        db,
                        shipment_id,
                        json.loads(vehicle_event[0]).get("eventType"),
                        json.loads(vehicle_event[0]).get("producerEventId")
                        or json.loads(vehicle_event[0]).get("eventId"),
                    )
                    self.event(db, record, "VehicleReleased", process="P2")
                    self.save(db, record)
                return record
            if record.get("transportState") != "ARRIVED":
                raise Conflict(
                    "vehicle can be released only after arrival and receipt acknowledgement"
                )
            record.update(transportState="COMPLETED", vehicleReleased=True)
            self.event(
                db,
                record,
                "ReceiptAcknowledged",
                actor=accepted["source"],
                process="P2",
                event_id=receipt_journal_id,
                payload=accepted["payload"],
                occurred_at=accepted["occurredAt"],
                recorded_at=accepted["recordedAt"],
                source=accepted["source"],
                input_mode=accepted["inputMode"],
                producer_event_id=accepted["eventId"],
            )
            self.event(db, record, "VehicleReleased", process="P2")
            self.save(db, record)
            return record

    def events(self, shipment_id):
        with self.transaction() as db:
            self.read(db, shipment_id)
            return [
                event
                for (raw,) in db.execute("SELECT data FROM events ORDER BY rowid")
                if (event := json.loads(raw))["shipmentId"] == shipment_id
            ]
