"""Transactional shipment state, demo inventory and a durable message outbox.

Zeebe owns control flow. SQLite stores business effects and retry identities.
Run one communication instance; preserve this database with its engine volumes.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from claim_registry import ClaimRegistry
from monitoring import profile, seconds
from monitoring_registry import MonitoringRegistry
from corrective_registry import CorrectiveRegistry


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def epoch_milliseconds(value):
    if value is None:  # unavailable ETA: Siddhi ignores events without etaMillis
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(parsed.timestamp() * 1000)


class Conflict(Exception):
    pass


class Registry(MonitoringRegistry, CorrectiveRegistry, ClaimRegistry):
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            self.init_claims(db)
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
                "CREATE TABLE IF NOT EXISTS receiving_inventory ("
                "recipient_id TEXT NOT NULL, product_id TEXT NOT NULL, "
                "batch_id TEXT NOT NULL, available INTEGER NOT NULL CHECK(available >= 0), "
                "PRIMARY KEY(recipient_id,product_id,batch_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS products (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS container_observations ("
                       "shipment_id TEXT NOT NULL, sequence INTEGER NOT NULL, data TEXT NOT NULL, ack TEXT, "
                       "PRIMARY KEY(shipment_id,sequence))")
            db.execute("CREATE TABLE IF NOT EXISTS corrective_cases ("
                       "id TEXT PRIMARY KEY, incident_id TEXT NOT NULL UNIQUE, data TEXT NOT NULL)")
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
            source=source or {
                "P1": "preparation",
                "P2": "transport",
                "P3": "receiving",
            }.get(process, process.lower()),
            inputMode=input_mode or ("system" if actor == "system" else "manual"),
            actor=actor,
            role="integration" if actor == "system" else "operator",
            outcome="success",
            process=process,
            processInstanceKey={
                "P1": record.get("processInstanceKey"),
                "P2": record.get("p2ProcessInstanceKey"),
                "P3": record.get("p3ProcessInstanceKey"),
                "P6": record.get("p6ProcessInstanceKey"),
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
        for optional in ("originalShipmentId", "incidentId", "correctiveId", "replacementForCorrectiveId", "claimId"):
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
            record = self.read(db, shipment_id)
            session = record.get("monitoring", {})
            record["monitoringStatus"] = session.get("state", "configured-awaiting-loading" if "monitoringProfile" in record else "legacy/unmonitored")
            last = session.get("lastValidAt")
            record["containerReadingFresh"] = bool(last and 0 <= seconds(now()) - seconds(last) <= record["monitoringProfile"]["freshSeconds"])
            return record

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

    def receiving_inventory(self, recipient_id=None):
        with self.transaction() as db:
            query = (
                "SELECT r.recipient_id,r.product_id,r.batch_id,r.available,p.data "
                "FROM receiving_inventory r JOIN products p ON p.id=r.product_id"
            )
            values = ()
            if recipient_id is not None:
                query += " WHERE r.recipient_id=?"
                values = (recipient_id,)
            query += " ORDER BY r.recipient_id,r.product_id,r.batch_id"
            items = []
            for recipient, product_id, batch_id, available, raw in db.execute(
                query, values
            ):
                product = json.loads(raw)
                product.pop("initialCrates", None)
                items.append(
                    {
                        "recipientId": recipient,
                        **product,
                        "productId": product_id,
                        "batchId": batch_id,
                        "available": available,
                    }
                )
            return {
                "quantityUnit": "crate",
                "available": sum(item["available"] for item in items),
                "items": items,
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
            if any(field in data for field in ("originalShipmentId", "incidentId", "correctiveId", "replacementForCorrectiveId")):
                raise Conflict("linked replacements must be arranged through P5")
            return self.create_in_transaction(db, data)

    def create_in_transaction(self, db, data):
        """Create a shipment inside its caller's atomic business transaction."""
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
        record["monitoringProfile"] = profile(record["productId"])
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
            if activity == "LoadingVerified":
                self.monitoring_start(record, now())
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
                "ACKNOWLEDGED" if data.get("destination") in ("p3-http", "siddhi-http", "p4-http", "container-siddhi", "p4-cooling-command") else "PUBLISHED"
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
            if event_type == "ReceiptAcknowledged" and record.get("p3ArrivalDeliveryId"):
                raise Conflict(
                    "P3 is receiving this shipment; receipt must be recorded through P3"
                )
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
            observed_at = now()
            eta_event = self.event(
                db, record, "ETAUpdated", process="P2",
                event_id=f"P2:{shipment_id}:ETAUpdated:{revision}:{route_index}",
                occurred_at=observed_at,
                payload={
                    "observedAtMillis": epoch_milliseconds(observed_at),
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
            previous_eta = record.get("etaAssessment", {})
            if eta_event["eventId"] in record.setdefault("seenEtaEventIds", []):
                self.save(db, record)
                return record
            record["seenEtaEventIds"].append(eta_event["eventId"])
            late = eta is not None and epoch_milliseconds(eta) > epoch_milliseconds(record["deliveryDeadline"])
            consecutive = previous_eta.get("times", []) if previous_eta.get("revision") == revision and previous_eta.get("late") else []
            observed_at = eta_event["occurredAt"]
            current_ms = epoch_milliseconds(observed_at)
            consecutive = [t for t in consecutive if 0 <= current_ms - t <= 180000] if late else []
            if late:
                consecutive = (consecutive + [current_ms])[-3:]
            record["etaAssessment"] = {"revision": revision, "late": late, "observedAt": observed_at,
                                       "times": consecutive, "qualified": len(consecutive) == 3,
                                       "eta": eta, "inputMode": eta_source, "eventId": eta_event["eventId"]}
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
            accepted["incidentId"] = self.establish_delivery_risk_hold(db, record, event)
            self.enqueue(db, message_id, "DeliveryRiskDetected", shipment_id,
                         {"shipmentId": shipment_id, "deliveryRiskEvent": event})
            db.execute("INSERT INTO results VALUES (?,?)", (identity, json.dumps(accepted)))
            self.save(db, record)
            return accepted

    def establish_delivery_risk_hold(self, db, record, risk_event):
        """Persist the quality hold before handing the delay to P4 (same transaction).

        The agreement requires a delivery risk to establish quality assessment and
        hold admission. A new incident is opened unless one is already active, in
        which case the alert is added to it as evidence. The P4 handoff is a durable
        outbox entry: if P4 is unavailable it stays PENDING and the hold remains.
        Only P4 releasing this incident (RELEASED) lets P3 admit the stock.
        """
        if "monitoringProfile" in record:
            return self.assessment_hold(db, record, "delivery-risk", {**risk_event, "eta": risk_event["payload"]})
        shipment_id = record["shipmentId"]
        if record.get("qualityStatus") in ("PENDING", "REJECTED") and record.get("qualityIncidentId"):
            incident_id, opened = record["qualityIncidentId"], False
        else:
            incident_id, opened = f"DELAY:{risk_event['eventId']}", True
            record.update(qualityStatus="PENDING", qualityIncidentId=incident_id)
            self.event(
                db, record, "QualityHoldEstablished", process="P4",
                event_id=f"P4:{shipment_id}:QualityHoldEstablished:{incident_id}",
                source="delivery-risk-integration", input_mode="system",
                occurred_at=risk_event["occurredAt"],
                payload={"incidentId": incident_id, "qualityStatus": "PENDING",
                         "reason": "delivery risk", "riskEventId": risk_event["eventId"]},
            )
        handoff_id = f"P4:DeliveryRiskIncident:{shipment_id}:{risk_event['eventId']}"
        self.enqueue(db, handoff_id, "DeliveryRiskIncident", shipment_id, {"event": {
            "deliveryId": handoff_id, "eventType": "DeliveryRiskDetected",
            "shipmentId": shipment_id, "journeyId": record["journeyId"],
            "incidentId": incident_id, "incidentOpened": opened,
            "qualityStatus": record["qualityStatus"], "riskEvent": risk_event,
            "productId": record["productId"], "batchId": record["batchId"],
            **({"assessment": record["lastAssessment"]} if "lastAssessment" in record else {}),
        }}, needs_ack=True, destination="p4-http")
        return incident_id

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
            if db.execute(
                "SELECT 1 FROM results WHERE id=?",
                (f"transport-input:{shipment_id}:ShipmentArrived",),
            ).fetchone():
                # The vehicle is already at its destination; P2 closes this alert
                # when it records the arrival instead of applying a late decision.
                raise Conflict("vehicle has already arrived; arrival closes this delivery-risk alert")
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
                    etaAssessment={"qualified": False},
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
            for ref in ("originalShipmentId", "incidentId", "correctiveId", "replacementForCorrectiveId"):
                if ref in record:
                    notification[ref] = record[ref]
            self.enqueue(db, notification_id, "ShipmentArrivalNotification", shipment_id,
                         {"event": notification}, needs_ack=True, destination="p3-http")
            return record

    def accept_p3_arrival(self, notification):
        """Durably accept one P2 arrival notification and queue one P3 start."""
        delivery_id = notification["deliveryId"]
        shipment_id = notification["shipmentId"]
        identity = f"p3-arrival:{delivery_id}"
        with self.transaction() as db:
            previous = db.execute(
                "SELECT data FROM results WHERE id=?", (identity,)
            ).fetchone()
            if previous:
                accepted = json.loads(previous[0])
                if accepted["notification"] != notification:
                    raise Conflict("deliveryId reused with different arrival data")
                return accepted

            record = self.read(db, shipment_id)
            if record.get("transportState") not in ("ARRIVED", "COMPLETED"):
                raise Conflict(
                    "P3 arrival notification requires a recorded P2 arrival"
                )
            for field in ("journeyId", "productId", "batchId"):
                if notification[field] != record[field]:
                    raise Conflict(f"{field} does not match the dispatched shipment")
            if notification["arrival"].get("recipientId") != record["recipientId"]:
                raise Conflict("arrival recipientId does not match the shipment")
            arrival_id = self.transport_event_journal_id(
                shipment_id, "ShipmentArrived", notification["sourceEventId"]
            )
            arrival_row = db.execute(
                "SELECT data FROM events WHERE id=?", (arrival_id,)
            ).fetchone()
            if arrival_row is None:
                raise Conflict("P2 arrival activity is not recorded")
            arrival = json.loads(arrival_row[0])
            if (
                arrival.get("eventType") != "ShipmentArrived"
                or arrival.get("journeyId") != notification["journeyId"]
                or arrival.get("producerEventId") != notification["sourceEventId"]
                or arrival.get("occurredAt") != notification["occurredAt"]
                or arrival.get("recordedAt") != notification["recordedAt"]
                or any(arrival.get(ref) != notification.get(ref) for ref in (
                    "originalShipmentId", "incidentId", "correctiveId", "replacementForCorrectiveId"))
                or any(
                    arrival.get("payload", {}).get(field) != value
                    for field, value in notification["arrival"].items()
                )
            ):
                raise Conflict(
                    "P3 arrival notification does not match the recorded P2 arrival"
                )
            if record.get("p3ArrivalDeliveryId") not in (None, delivery_id):
                raise Conflict("P3 already accepted a different arrival for this shipment")

            message_id = f"P3:start:{delivery_id}"
            accepted = {
                "deliveryId": delivery_id,
                "messageId": message_id,
                "notification": notification,
            }
            record.update(
                p3ArrivalDeliveryId=delivery_id,
                p3StartStatus="PENDING",
                p3Status="STARTING",
            )
            self.save(db, record)
            self.enqueue(
                db,
                message_id,
                "P3ShipmentArrived",
                shipment_id,
                {"shipmentId": shipment_id, "arrivalNotification": notification},
            )
            db.execute(
                "INSERT INTO results VALUES (?,?)", (identity, json.dumps(accepted))
            )
            return accepted

    def record_p3_arrival(self, shipment_id, key, notification):
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            accepted_row = db.execute(
                "SELECT data FROM results WHERE id=?",
                (f"p3-arrival:{notification.get('deliveryId')}",),
            ).fetchone()
            if accepted_row is None:
                raise Conflict("P3 arrival notification was not accepted")
            accepted = json.loads(accepted_row[0])["notification"]
            if accepted != notification or notification["shipmentId"] != shipment_id:
                raise Conflict("P3 arrival notification does not match its accepted delivery")
            if record.get("p3ProcessInstanceKey") not in (None, str(key)):
                raise Conflict("shipment belongs to a different P3 engine instance")
            if record.get("p3ArrivalDeliveryId") != notification["deliveryId"]:
                raise Conflict("P3 arrival notification is not the active shipment delivery")
            event_id = f"P3:{shipment_id}:ArrivalRecorded"
            existing = db.execute(
                "SELECT 1 FROM events WHERE id=?", (event_id,)
            ).fetchone()
            if existing:
                return record
            record.update(
                p3ProcessInstanceKey=str(key),
                p3StartStatus="STARTED",
                p3Status="AWAITING_RECEIPT",
                p3ArrivalRecordedAt=now(),
            )
            self.event(
                db,
                record,
                "ShipmentArrivalRecorded",
                process="P3",
                event_id=event_id,
                payload={
                    "deliveryId": notification["deliveryId"],
                    "sourceEventId": notification["sourceEventId"],
                    "recipientId": record["recipientId"],
                },
                occurred_at=notification["occurredAt"],
                recorded_at=notification["recordedAt"],
                source="p2",
                input_mode="system",
            )
            self.save(db, record)
            return record

    def queue_p3_input(self, shipment_id, event_type, event_id, occurred_at, payload):
        """Queue a retry-safe physical receipt or inspection input for P3."""
        expected = {
            "GoodsReceived": ("AWAITING_RECEIPT", "receiptEvent"),
            "GoodsInspected": ("AWAITING_INSPECTION", "inspectionEvent"),
        }
        if event_type not in expected:
            raise ValueError("unsupported P3 receiving event")
        status, variable = expected[event_type]
        identity = f"p3-input:{shipment_id}:{event_type}"
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            old = db.execute(
                "SELECT data FROM results WHERE id=?", (identity,)
            ).fetchone()
            if old:
                accepted = json.loads(old[0])
                event = accepted["event"]
                if (
                    event["eventId"] != event_id
                    or event["occurredAt"] != occurred_at
                    or event["payload"] != payload
                ):
                    raise Conflict(f"{event_type} already accepted with different data")
                return accepted
            if not record.get("p3ProcessInstanceKey"):
                raise Conflict("P3 has not recorded this shipment arrival")
            if record.get("p3Status") != status:
                raise Conflict(f"{event_type} requires P3 status {status}")
            if event_type == "GoodsReceived":
                if payload["recipientId"] != record["recipientId"]:
                    raise Conflict("recipientId must match the shipment")
                if payload["quantityReceived"] != record["quantity"]:
                    raise Conflict("quantityReceived must match the dispatched crates")
            event = {
                "eventId": event_id,
                "eventType": event_type,
                "shipmentId": shipment_id,
                "journeyId": record["journeyId"],
                "occurredAt": occurred_at,
                "recordedAt": now(),
                "source": "p3-receiving",
                "inputMode": "manual",
                "payload": payload,
            }
            message_id = f"P3:{shipment_id}:{event_type}:{event_id}"
            accepted = {"messageId": message_id, "event": event}
            self.enqueue(
                db,
                message_id,
                event_type,
                shipment_id,
                {"shipmentId": shipment_id, variable: event},
            )
            db.execute(
                "INSERT INTO results VALUES (?,?)", (identity, json.dumps(accepted))
            )
            return accepted

    def record_p3_receipt(self, shipment_id, key, receipt_event):
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            if record.get("p3ProcessInstanceKey") != str(key):
                raise Conflict("wrong P3 engine instance")
            identity = f"p3-input:{shipment_id}:GoodsReceived"
            row = db.execute(
                "SELECT data FROM results WHERE id=?", (identity,)
            ).fetchone()
            if row is None or json.loads(row[0])["event"] != receipt_event:
                raise Conflict("receipt was not accepted by the P3 receiving endpoint")
            event_id = f"P3:{shipment_id}:GoodsReceived"
            existing = db.execute(
                "SELECT 1 FROM events WHERE id=?", (event_id,)
            ).fetchone()
            if existing:
                return record
            if record.get("p3Status") != "AWAITING_RECEIPT":
                raise Conflict("physical receipt can only be recorded once")
            payload = receipt_event["payload"]
            record.update(
                p3Status="AWAITING_INSPECTION",
                receiptStatus="RECEIVED",
                receiver=payload["receiver"],
                receivedAt=receipt_event["occurredAt"],
            )
            self.event(
                db,
                record,
                "GoodsReceived",
                actor=payload["receiver"],
                process="P3",
                event_id=event_id,
                payload=payload,
                occurred_at=receipt_event["occurredAt"],
                recorded_at=receipt_event["recordedAt"],
                source=receipt_event["source"],
                input_mode=receipt_event["inputMode"],
                producer_event_id=receipt_event["eventId"],
            )
            self.monitoring_close(db, record, now())
            p2_receipt = {
                "eventId": f"P3:{shipment_id}:ReceiptAcknowledged",
                "eventType": "ReceiptAcknowledged",
                "shipmentId": shipment_id,
                "journeyId": record["journeyId"],
                "occurredAt": receipt_event["occurredAt"],
                "recordedAt": now(),
                "source": "p3-receiving",
                "inputMode": "system",
                "actor": payload["receiver"],
                "payload": {
                    "recipientId": record["recipientId"],
                    "quantityReceived": record["quantity"],
                    **(
                        {"receiptReference": payload["receiptReference"]}
                        if payload.get("receiptReference")
                        else {}
                    ),
                },
            }
            p2_receipt_identity = f"p3-receipt-ack:{shipment_id}"
            ack = {
                "messageId": p2_receipt["eventId"],
                "event": p2_receipt,
            }
            db.execute(
                "INSERT OR REPLACE INTO results VALUES (?,?)",
                (p2_receipt_identity, json.dumps(ack)),
            )
            self.enqueue(
                db,
                p2_receipt["eventId"],
                "ReceiptAcknowledged",
                shipment_id,
                {"shipmentId": shipment_id, "receiptEvent": p2_receipt},
            )
            self.save(db, record)
            return record

    def record_p3_inspection(self, shipment_id, key, inspection_event):
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            if record.get("p3ProcessInstanceKey") != str(key):
                raise Conflict("wrong P3 engine instance")
            identity = f"p3-input:{shipment_id}:GoodsInspected"
            row = db.execute(
                "SELECT data FROM results WHERE id=?", (identity,)
            ).fetchone()
            if row is None or json.loads(row[0])["event"] != inspection_event:
                raise Conflict("inspection was not accepted by the P3 receiving endpoint")
            event_id = f"P3:{shipment_id}:GoodsInspected"
            if db.execute(
                "SELECT 1 FROM events WHERE id=?", (event_id,)
            ).fetchone():
                return {
                    "inspectionPassed": record.get("inspectionStatus") == "PASSED",
                    **record,
                }
            if record.get("p3Status") != "AWAITING_INSPECTION":
                raise Conflict("goods must be received before inspection")
            payload = inspection_event["payload"]
            passed = payload["passed"]
            record.update(
                p3Status="AWAITING_ADMISSION" if passed else "REJECTED",
                inspectionStatus="PASSED" if passed else "FAILED",
                inspector=payload["inspector"],
                inspectedAt=inspection_event["occurredAt"],
            )
            self.event(
                db,
                record,
                "GoodsInspected",
                actor=payload["inspector"],
                process="P3",
                event_id=event_id,
                payload=payload,
                occurred_at=inspection_event["occurredAt"],
                recorded_at=inspection_event["recordedAt"],
                source=inspection_event["source"],
                input_mode=inspection_event["inputMode"],
                producer_event_id=inspection_event["eventId"],
            )
            if not passed:
                record["admissionStatus"] = "REJECTED"
                self.event(
                    db,
                    record,
                    "GoodsRejected",
                    actor=payload["inspector"],
                    process="P3",
                    event_id=f"P3:{shipment_id}:GoodsRejected",
                    payload={"reason": "inspection failed"},
                )
            if not passed:
                self.notify_corrective_receiving(db, record, "REJECTED")
            self.save(db, record)
            return {"inspectionPassed": passed, **record}

    def admit_p3_stock(self, shipment_id, key):
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            if record.get("p3ProcessInstanceKey") != str(key):
                raise Conflict("wrong P3 engine instance")
            if record.get("inspectionStatus") != "PASSED":
                raise Conflict("stock admission requires a passed inspection")
            admitted_event_id = f"P3:{shipment_id}:StockAdmitted"
            admitted = db.execute(
                "SELECT 1 FROM events WHERE id=?", (admitted_event_id,)
            ).fetchone()
            if admitted:
                return {"admissionOutcome": "ADMITTED"}

            session = record.get("monitoring")
            if "monitoringProfile" in record:
                if not session or session.get("state") != "CLOSED":
                    raise Conflict("stock admission requires closed monitoring")
                self.monitoring_watchdog(db, record, now())
                self.monitoring_finalize(db, record)
                if session["finalization"] not in ("FINALIZED", "HELD"):
                    raise Conflict("stock admission requires monitoring finalization")
            quality_status = record.get("qualityStatus", "CLEAR")
            if quality_status == "PENDING":
                record.update(p3Status="ON_HOLD", admissionStatus="HELD")
                self.event(
                    db,
                    record,
                    "StockAdmissionHeld",
                    process="P3",
                    event_id=f"P3:{shipment_id}:StockAdmissionHeld",
                    payload={
                        "qualityStatus": quality_status,
                        "incidentId": record.get("qualityIncidentId"),
                    },
                )
                self.save(db, record)
                return {"admissionOutcome": "HELD"}
            if quality_status == "REJECTED":
                record.update(p3Status="REJECTED", admissionStatus="REJECTED")
                self.event(
                    db,
                    record,
                    "GoodsRejected",
                    process="P3",
                    event_id=f"P3:{shipment_id}:GoodsRejected",
                    payload={
                        "reason": "quality disposition rejected the goods",
                        "incidentId": record.get("qualityIncidentId"),
                    },
                )
                self.notify_corrective_receiving(db, record, "REJECTED")
                self.save(db, record)
                return {"admissionOutcome": "REJECTED"}
            if quality_status not in ("CLEAR", "RELEASED"):
                raise Conflict(f"unsupported quality status {quality_status}")

            db.execute(
                "INSERT INTO receiving_inventory VALUES (?,?,?,?) "
                "ON CONFLICT(recipient_id,product_id,batch_id) DO UPDATE "
                "SET available=available+excluded.available",
                (
                    record["recipientId"],
                    record["productId"],
                    record["batchId"],
                    record["quantity"],
                ),
            )
            record.update(
                p3Status="ADMITTED",
                admissionStatus="ADMITTED",
                admittedAt=now(),
            )
            self.event(
                db,
                record,
                "StockAdmitted",
                process="P3",
                event_id=admitted_event_id,
                payload={
                    "recipientId": record["recipientId"],
                    "quantity": record["quantity"],
                    "quantityUnit": record["quantityUnit"],
                    **(
                        {"incidentId": record["qualityIncidentId"]}
                        if record.get("qualityIncidentId")
                        else {}
                    ),
                },
            )
            self.notify_corrective_receiving(db, record, "ADMITTED")
            self.save(db, record)
            return {"admissionOutcome": "ADMITTED"}

    @staticmethod
    def quality_status_guard(record, status, incident_id, expected_evidence_version=None):
        """Raise if a quality status change is not allowed for this shipment now."""
        if "monitoringProfile" in record and status in ("RELEASED", "REJECTED"):
            if type(expected_evidence_version) is not int or expected_evidence_version != record.get("evidenceVersion", 0):
                raise Conflict("disposition requires current expectedEvidenceVersion")
        previous = record.get("qualityStatus", "CLEAR")
        allowed = {
            "CLEAR": {"PENDING"},
            "RELEASED": {"PENDING"},
            "PENDING": {"RELEASED", "REJECTED"},
        }
        if status not in allowed.get(previous, set()):
            raise Conflict(
                f"quality status cannot change from {previous} to {status}"
            )
        if record.get("admissionStatus") == "ADMITTED":
            raise Conflict("quality disposition must precede stock admission")
        if status == "PENDING" and not incident_id:
            raise ValueError("incidentId is required for a PENDING quality status")
        if status in ("RELEASED", "REJECTED") and incident_id != record.get(
            "qualityIncidentId"
        ):
            raise Conflict("quality disposition does not match the active incident")

    def check_quality_status(
        self, shipment_id, event_id, status, incident_id, expected_evidence_version=None
    ):
        """Read-only check that accept_quality_status would accept this change now.

        Returns True when the same eventId was already accepted. Changes nothing, so a
        caller (P4) can validate a disposition before executing it in DCR and apply it
        only afterwards; accept_quality_status re-checks under its own transaction.
        """
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            if db.execute("SELECT 1 FROM results WHERE id=?",
                          (f"p4-quality:{shipment_id}:{event_id}",)).fetchone():
                return True
            self.quality_status_guard(record, status, incident_id, expected_evidence_version)
            return False

    def accept_quality_status(
        self, shipment_id, event_id, occurred_at, status, incident_id, expected_evidence_version=None
    ):
        if status not in ("PENDING", "RELEASED", "REJECTED"):
            raise ValueError("qualityStatus must be PENDING, RELEASED or REJECTED")
        identity = f"p4-quality:{shipment_id}:{event_id}"
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            existing = db.execute(
                "SELECT data FROM results WHERE id=?", (identity,)
            ).fetchone()
            payload = {"qualityStatus": status, "incidentId": incident_id}
            if expected_evidence_version is not None:
                payload["expectedEvidenceVersion"] = expected_evidence_version
            if existing:
                accepted = json.loads(existing[0])
                if (
                    accepted["event"]["occurredAt"] != occurred_at
                    or accepted["event"]["payload"] != payload
                ):
                    raise Conflict("eventId reused with different quality data")
                return accepted

            self.quality_status_guard(record, status, incident_id, expected_evidence_version)

            event = {
                "eventId": event_id,
                "eventType": "QualityStatusChanged",
                "shipmentId": shipment_id,
                "journeyId": record["journeyId"],
                "occurredAt": occurred_at,
                "recordedAt": now(),
                "source": "p4-quality",
                "inputMode": "system",
                "payload": payload,
            }
            message_id = f"P4:{shipment_id}:QualityStatusChanged:{event_id}"
            accepted = {"messageId": message_id, "event": event}
            record.update(qualityStatus=status, qualityIncidentId=incident_id)
            if status == "PENDING":
                record.update(evidenceVersion=record.get("evidenceVersion", 0) + 1,
                              assessmentReasons=[], assessmentEvidenceKeys=[], assessmentPriority="normal")
            self.save(db, record)
            db.execute(
                "INSERT INTO results VALUES (?,?)", (identity, json.dumps(accepted))
            )
            if record.get("p3Status") == "ON_HOLD":
                self.enqueue(
                    db,
                    message_id,
                    "QualityStatusChanged",
                    shipment_id,
                    {"shipmentId": shipment_id, "qualityStatusEvent": event},
                )
            return accepted

    def record_p3_quality_status(self, shipment_id, key, quality_status_event):
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            if record.get("p3ProcessInstanceKey") != str(key):
                raise Conflict("wrong P3 engine instance")
            identity = (
                f"p4-quality:{shipment_id}:{quality_status_event.get('eventId')}"
            )
            row = db.execute(
                "SELECT data FROM results WHERE id=?", (identity,)
            ).fetchone()
            if row is None or json.loads(row[0])["event"] != quality_status_event:
                raise Conflict("quality status was not accepted by the P4 integration")
            event_id = (
                f"P3:{shipment_id}:QualityStatusChanged:"
                f"{quality_status_event['eventId']}"
            )
            if not db.execute(
                "SELECT 1 FROM events WHERE id=?", (event_id,)
            ).fetchone():
                self.event(
                    db,
                    record,
                    "QualityStatusChanged",
                    process="P3",
                    event_id=event_id,
                    payload=quality_status_event["payload"],
                    occurred_at=quality_status_event["occurredAt"],
                    recorded_at=quality_status_event["recordedAt"],
                    source=quality_status_event["source"],
                    input_mode=quality_status_event["inputMode"],
                    producer_event_id=quality_status_event["eventId"],
                )
            status = quality_status_event["payload"]["qualityStatus"]
            record["p3Status"] = (
                "REJECTED" if status == "REJECTED" else "AWAITING_ADMISSION"
            )
            self.save(db, record)
            return record

    def release_vehicle(self, shipment_id, key, receipt_event):
        with self.transaction() as db:
            record = self.read(db, shipment_id)
            self.require_transport_instance(record, key)
            p3_receipt = db.execute(
                "SELECT data FROM results WHERE id=?",
                (f"p3-receipt-ack:{shipment_id}",),
            ).fetchone()
            if p3_receipt is not None:
                accepted = json.loads(p3_receipt[0])["event"]
                if accepted != receipt_event:
                    raise Conflict(
                        "ReceiptAcknowledged does not match the P3 receipt event"
                    )
            else:
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
                actor=accepted.get("actor", accepted["source"]),
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
