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
        db, record, activity, actor="system", process="P1", event_id=None, payload=None
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
            occurredAt=stamp,
            recordedAt=stamp,
            source={
                "P1": "preparation",
                "P2": "transport",
                "P3": "receiving",
            }.get(process, process.lower()),
            inputMode="system" if actor == "system" else "manual",
            actor=actor,
            role="integration" if actor == "system" else "operator",
            outcome="success",
            process=process,
            processInstanceKey={
                "P1": record.get("processInstanceKey"),
                "P2": record.get("p2ProcessInstanceKey"),
                "P3": record.get("p3ProcessInstanceKey"),
            }.get(process),
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
        db.execute("INSERT INTO events VALUES (?,?)", (event_id, json.dumps(event)))
        return event

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

    def record_receipt(self, shipment_id, receiver):
        with self.transaction() as db:
            record = self.read(db, shipment_id)

            if record.get("receiptStatus") == "RECEIVED":
                return record

            if not receiver or not receiver.strip():
                raise ValueError("receiver is required")

            record.update(
                receiptStatus="RECEIVED",
                receiver=receiver.strip(),
                receivedAt=now(),
            )

            self.event(
                db,
                record,
                "GoodsReceived",
                actor=receiver.strip(),
                process="P3",
            )

            self.save(db, record)
            return record 

    def record_inspection(self, shipment_id, inspector, passed):
        with self.transaction() as db:
            record = self.read(db, shipment_id)

            if record.get("receiptStatus") != "RECEIVED":
                raise Conflict("goods must be received before inspection")

            if not inspector or not inspector.strip():
                raise ValueError("inspector is required")

            record.update(
                inspectionStatus="PASSED" if passed else "FAILED",
                inspector=inspector.strip(),
                inspectedAt=now(),
            )

            self.event(
                db,
                record,
                "GoodsInspected",
                actor=inspector.strip(),
                process="P3",
                payload={"passed": bool(passed)},
            )

            self.save(db, record)
            return record  

    def admit_stock(self, shipment_id, receiver):
        with self.transaction() as db:
            record = self.read(db, shipment_id)

            if record.get("receiptStatus") != "RECEIVED":
                raise Conflict("goods must be received before admission")

            if record.get("inspectionStatus") != "PASSED":
                raise Conflict("goods must pass inspection before admission")

            if record.get("admissionStatus") == "ADMITTED":
                return record

            if record.get("qualityStatus") not in ("CLEAR", "RELEASED"):
                raise Conflict("goods are blocked by quality status")

            if not receiver or not receiver.strip():
                raise ValueError("receiver is required")

            cursor = db.execute(
                "UPDATE inventory SET available=available+? "
                "WHERE product_id=? AND batch_id=?",
                (
                    record["quantity"],
                    record["productId"],
                    record["batchId"],
                ),
            )

            if cursor.rowcount != 1:
                raise Conflict("inventory record for product and batch not found")

            record.update(
                admissionStatus="ADMITTED",
                admittedBy=receiver.strip(),
                admittedAt=now(),
            )

            self.event(
                db,
                record,
                "StockAdmitted",
                actor=receiver.strip(),
                process="P3",
            )

            self.save(db, record)
            return record    

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
                    transportState="AWAITING_TRANSPORT_IMPLEMENTATION",
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

    def events(self, shipment_id):
        with self.transaction() as db:
            self.read(db, shipment_id)
            return [
                event
                for (raw,) in db.execute("SELECT data FROM events ORDER BY rowid")
                if (event := json.loads(raw))["shipmentId"] == shipment_id
            ]
