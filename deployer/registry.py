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
            return
        event = json.loads(legacy[0])
        if event.get("shipmentId") != shipment_id or event.get("eventType") != event_type:
            return
        journal_id = cls.transport_event_journal_id(
            shipment_id, event_type, producer_event_id
        )
        if db.execute("SELECT 1 FROM events WHERE id=?", (journal_id,)).fetchone():
            db.execute("DELETE FROM events WHERE id=?", (producer_event_id,))
            return
        event["eventId"] = journal_id
        event["producerEventId"] = producer_event_id
        db.execute(
            "UPDATE events SET id=?, data=? WHERE id=?",
            (journal_id, json.dumps(event), producer_event_id),
        )

    @staticmethod
    def enqueue(db, message_id, name, correlation, variables, needs_ack=False):
        data = dict(
            messageId=message_id,
            name=name,
            correlationKey=correlation,
            variables=variables,
            status="PENDING",
            createdAt=now(),
            needsAcknowledgement=needs_ack,
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

    def pending_messages(self):
        with self.transaction() as db:
            pending = []
            for message_id, raw in db.execute(
                "SELECT id,data FROM outbox ORDER BY rowid"
            ).fetchall():
                data = json.loads(raw)
                if data["status"] != "PENDING":
                    continue
                age = datetime.now(timezone.utc) - datetime.fromisoformat(
                    data["createdAt"].replace("Z", "+00:00")
                )
                if age.total_seconds() >= 86400:
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
            data.update(status="PENDING" if error else "PUBLISHED", lastError=error)
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
        self, shipment_id, event_type, event_id, occurred_at, payload, source
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
                        "inputMode": "manual",
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
                inputMode="manual",
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

    def start_journey(self, shipment_id, key):
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
            self.event(db, record, "JourneyStarted", process="P2")
            self.save(db, record)
            return record

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
            self.migrate_legacy_transport_event(
                db, shipment_id, "ReceiptAcknowledged", accepted["eventId"]
            )
            if record.get("vehicleReleased"):
                vehicle_event = db.execute(
                    "SELECT data FROM events WHERE id=?",
                    (f"P2:{shipment_id}:VehicleReleased",),
                ).fetchone()
                if vehicle_event is None:
                    raise Conflict("vehicle release state has no recorded activity")
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
