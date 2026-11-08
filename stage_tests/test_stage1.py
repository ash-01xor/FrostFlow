"""Offline API/business-effect checks. Engine calls are substituted."""

import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "deployer")]
IMPORT_DIR = tempfile.TemporaryDirectory()
os.environ["REGISTRY_PATH"] = str(Path(IMPORT_DIR.name) / "import.sqlite")
import app as service
from registry import Conflict, Registry
from pyzeebe.grpc_internals.types import CreateProcessInstanceResponse
from compliance.check import Graph, replay, export_xes

MODEL = ROOT / "compliance/p1/rules.xml"


def sample(
    shipment="S-test",
    journey="J-test",
    vehicle="V-01",
    quantity=3,
    product="PRD-001",
    batch="DEMO-MILK-001",
):
    return dict(
        shipmentId=shipment,
        journeyId=journey,
        vehicleId=vehicle,
        recipientId="C-01",
        routeId="route-01",
        quantity=quantity,
        productId=product,
        batchId=batch,
        deliveryDeadline="2026-12-01T12:00:00Z",
    )


class Preparation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        service.registry = Registry(Path(self.temp.name) / "registry.sqlite")
        self.db = service.registry
        self.http = service.app.test_client()
        self.key = 123456789
        service.ready.set()
        self.addCleanup(service.ready.clear)

        class Engine:
            def run_process(inner, *args, **kwargs):
                return CreateProcessInstanceResponse(
                    1, service.PROCESS_ID, 1, self.key, "<default>"
                )

        for target, replacement in [
            ("client", Engine()),
            ("engine_call", lambda value: value),
        ]:
            patcher = patch.object(service, target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def create(self, **kwargs):
        response = self.http.post("/shipments", json=sample(**kwargs))
        self.assertEqual(response.status_code, 202, response.json)

    def available(self, product="PRD-001"):
        return next(
            p["available"]
            for p in self.db.inventory()["products"]
            if p["productId"] == product
        )

    def check(self, sid="S-test", job=1):
        return self.db.check_stock(sid, self.key, job)

    def loading_wait(self):
        self.create()
        self.check()
        self.db.advance(
            "S-test", self.key, "STOCK_RESERVED", "STOCK_ALLOCATED", "StockAllocated"
        )
        self.db.advance(
            "S-test",
            self.key,
            "STOCK_ALLOCATED",
            "AWAITING_LOADING",
            "TransportAssigned",
        )

    def wait_shortage(self):
        self.create(quantity=15)
        self.check()
        self.create(shipment="S-two", journey="J-two", vehicle="V-02", quantity=10)
        self.assertFalse(self.check("S-two", 2)["stockAvailable"])

    def verify(self, sid="S-test"):
        return self.http.post(
            f"/shipments/{sid}/verify-loading",
            json=dict(
                actor="warehouse",
                quantityChecked=True,
                packagingChecked=True,
                vehicleReady=True,
            ),
        )

    def test_dispatch_requires_processed_loading(self):
        self.loading_wait()

        def dispatch():
            return self.http.post(
                "/shipments/S-test/dispatch", json={"actor": "dispatcher"}
            )

        self.assertEqual(dispatch().status_code, 409)
        self.assertEqual(self.verify().status_code, 202)
        self.assertEqual(dispatch().status_code, 409)
        self.db.advance(
            "S-test",
            self.key,
            "AWAITING_LOADING",
            "AWAITING_DISPATCH",
            "LoadingVerified",
        )
        self.assertEqual(dispatch().status_code, 202)
        self.assertEqual(dispatch().status_code, 409)
        self.db.advance(
            "S-test", self.key, "AWAITING_DISPATCH", "DISPATCHED", "ShipmentDispatched"
        )
        self.assertEqual(dispatch().status_code, 409)
        events = self.db.events("S-test")
        self.assertTrue(replay(MODEL, events)["accepting"])
        handoffs = [
            m for m in self.db.pending_messages() if m["name"] == "ShipmentDispatched"
        ]
        self.assertEqual(len(handoffs), 1)
        self.db.advance(
            "S-test", self.key, "AWAITING_DISPATCH", "DISPATCHED", "ShipmentDispatched"
        )
        self.assertEqual(
            len(
                [
                    m
                    for m in self.db.pending_messages()
                    if m["name"] == "ShipmentDispatched"
                ]
            ),
            1,
        )

    def test_shortage_replenishment_recheck_and_deduplication(self):
        self.wait_shortage()
        with self.assertRaises(Conflict):
            self.db.advance(
                "S-two", self.key, "WAITING_STOCK", "STOCK_ALLOCATED", "StockAllocated"
            )
        self.assertEqual(self.verify("S-two").status_code, 409)
        self.assertEqual(
            self.http.post(
                "/shipments/S-two/dispatch", json={"actor": "dispatcher"}
            ).status_code,
            409,
        )
        old_key = self.db.get("S-two")["stockWaitKey"]
        payload = dict(actor="warehouse", quantity=1, eventId="replenish-1")
        for _ in range(2):
            self.assertEqual(
                self.http.post(
                    "/shipments/S-two/replenish-stock", json=payload
                ).status_code,
                202,
            )
        self.assertEqual(self.available(), 6)
        self.assertFalse(self.check("S-two", 3)["stockAvailable"])
        self.assertNotEqual(self.db.get("S-two")["stockWaitKey"], old_key)
        self.assertEqual(
            self.http.post(
                "/shipments/S-two/replenish-stock", json={**payload, "quantity": 2}
            ).status_code,
            409,
        )
        self.assertEqual(
            self.http.post(
                "/shipments/S-two/replenish-stock",
                json={**payload, "eventId": "replenish-2", "quantity": 4},
            ).status_code,
            202,
        )
        self.assertTrue(self.check("S-two", 4)["stockAvailable"])
        self.assertEqual(self.available(), 0)
        self.assertEqual(
            Registry(self.db.path).check_stock("S-two", self.key, 4)["stockAvailable"],
            True,
        )
        self.assertEqual(self.available(), 0)

    def test_cancellation_only_while_waiting_releases_vehicle(self):
        self.wait_shortage()
        self.assertEqual(
            self.http.post(
                "/shipments/S-test/cancel", json={"actor": "dispatcher"}
            ).status_code,
            409,
        )
        self.assertEqual(
            self.http.post(
                "/shipments/S-two/cancel", json={"actor": "dispatcher"}
            ).status_code,
            202,
        )
        self.db.advance(
            "S-two", self.key, "WAITING_STOCK", "CANCELLED", "ShipmentCancelled"
        )
        self.assertEqual(
            self.http.post(
                "/shipments/S-two/dispatch", json={"actor": "dispatcher"}
            ).status_code,
            409,
        )
        self.assertEqual(
            self.http.post(
                "/shipments/S-two/cancel", json={"actor": "dispatcher"}
            ).status_code,
            409,
        )
        self.assertEqual(self.available(), 5)
        self.create(shipment="S-three", journey="J-three", vehicle="V-02")
        self.assertTrue(replay(MODEL, self.db.events("S-two"))["accepting"])

    def test_competing_replenishment_and_cancellation_have_one_winner(self):
        self.wait_shortage()

        def attempt(command):
            try:
                self.db.reserve_command(
                    "S-two",
                    command,
                    "operator",
                    ("supply-race", 10) if command == "StockReplenished" else None,
                )
                return "accepted"
            except Conflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(
                list(
                    pool.map(
                        attempt, ["StockReplenished", "ShipmentCancellationRequested"]
                    )
                ),
                ["accepted", "conflict"],
            )

    def test_restart_preserves_pending_commands_and_reservations(self):
        self.loading_wait()
        self.verify()
        reopened = Registry(self.db.path)
        self.assertEqual(reopened.inventory()["available"], 97)
        self.assertEqual(
            reopened.get("S-test")["pendingCommand"]["name"], "LoadingVerified"
        )
        self.assertEqual(len(reopened.pending_messages()), 1)
        self.assertEqual(self.http.post("/shipments", json=sample()).status_code, 409)

    def test_transport_inbox_protects_duplicate_effects_and_ack_race(self):
        self.loading_wait()
        self.verify()
        self.db.advance(
            "S-test",
            self.key,
            "AWAITING_LOADING",
            "AWAITING_DISPATCH",
            "LoadingVerified",
        )
        self.db.reserve_command("S-test", "DispatchApproved", "dispatcher")
        self.db.advance(
            "S-test", self.key, "AWAITING_DISPATCH", "DISPATCHED", "ShipmentDispatched"
        )
        event = self.db.events("S-test")[-1]
        for _ in range(2):
            variables = self.db.start_transport("S-test", 999, event)
            self.assertEqual(variables["productId"], "PRD-001")
            self.assertEqual(variables["batchId"], "DEMO-MILK-001")
            self.assertEqual(variables["productName"], "Fresh milk")
        self.db.message_result(
            event["eventId"]
        )  # Publisher response must not overwrite a fast ACK.
        self.assertEqual(self.db.get("S-test")["handoffStatus"], "ACKNOWLEDGED")
        self.assertFalse(
            any(m["name"] == "ShipmentDispatched" for m in self.db.pending_messages())
        )
        self.assertEqual(
            sum(e["eventType"] == "TransportStarted" for e in self.db.events("S-test")),
            1,
        )
        with self.assertRaises(Conflict):
            self.db.start_transport("S-test", 1000, event)
        with self.assertRaises(Conflict):
            self.db.start_transport("S-test", 999, {**event, "shipmentId": "other"})

    def test_stock_and_worker_retry_do_not_double_reserve(self):
        self.create(quantity=15)
        self.check()
        self.check()
        self.assertEqual(self.available(), 5)
        self.db.advance(
            "S-test", self.key, "STOCK_RESERVED", "STOCK_ALLOCATED", "StockAllocated"
        )
        self.db.advance(
            "S-test", self.key, "STOCK_RESERVED", "STOCK_ALLOCATED", "StockAllocated"
        )
        self.assertEqual(len(self.db.events("S-test")), 3)
        with self.assertRaises(Conflict):
            self.db.attach_key("S-test", 999)

    def test_transport_registration_retry_preserves_later_progress(self):
        self.loading_wait()
        self.verify()
        self.db.advance(
            "S-test",
            self.key,
            "AWAITING_LOADING",
            "AWAITING_DISPATCH",
            "LoadingVerified",
        )
        self.db.reserve_command("S-test", "DispatchApproved", "dispatcher")
        self.db.advance(
            "S-test", self.key, "AWAITING_DISPATCH", "DISPATCHED", "ShipmentDispatched"
        )
        event = self.db.events("S-test")[-1]
        self.db.start_transport("S-test", 999, event)
        # Simulate a future P2 worker completing transport after registration.
        with self.db.transaction() as db:
            record = self.db.read(db, "S-test")
            record.update(transportState="COMPLETED", vehicleReleased=True)
            self.db.save(db, record)
        Registry(self.db.path).start_transport("S-test", 999, event)
        record = self.db.get("S-test")
        self.assertEqual(record["transportState"], "COMPLETED")
        self.assertTrue(record["vehicleReleased"])
        self.assertEqual(record["p2ProcessInstanceKey"], "999")
        self.assertEqual(
            sum(e["eventType"] == "TransportStarted" for e in self.db.events("S-test")),
            1,
        )

    def test_p2_manual_arrival_and_receipt_gate_vehicle_release(self):
        self.loading_wait()
        self.verify()
        self.db.advance(
            "S-test",
            self.key,
            "AWAITING_LOADING",
            "AWAITING_DISPATCH",
            "LoadingVerified",
        )
        self.db.reserve_command("S-test", "DispatchApproved", "dispatcher")
        self.db.advance(
            "S-test", self.key, "AWAITING_DISPATCH", "DISPATCHED", "ShipmentDispatched"
        )
        dispatch_event = next(
            event
            for event in self.db.events("S-test")
            if event["eventType"] == "ShipmentDispatched"
        )
        self.db.start_transport("S-test", 999, dispatch_event)
        self.db.start_journey("S-test", 999)
        self.assertEqual(self.db.get("S-test")["transportState"], "IN_TRANSIT")
        self.assertFalse(self.db.get("S-test")["vehicleReleased"])

        receipt = dict(
            eventId="receipt-1",
            occurredAt="2026-12-01T10:24:00Z",
            recipientId="C-01",
            quantityReceived=3,
        )
        self.assertEqual(
            self.http.post(
                "/shipments/S-test/acknowledge-receipt", json=receipt
            ).status_code,
            409,
        )

        arrival = dict(
            eventId="arrival-1",
            eventType="ShipmentArrived",
            shipmentId="S-test",
            journeyId="J-test",
            occurredAt="2026-12-01T10:22:00Z",
            source="p2-manual-test",
            inputMode="manual",
            payload={
                "recipientId": "C-01",
                "latitude": 48.8566,
                "longitude": 2.3522,
            },
        )
        for _ in range(2):
            response = self.http.post("/shipments/S-test/arrive", json=arrival)
            self.assertEqual(response.status_code, 202, response.json)
        self.assertEqual(
            self.http.post(
                "/shipments/S-test/arrive",
                json={**arrival, "payload": {**arrival["payload"], "recipientId": "C-02"}},
            ).status_code,
            409,
        )
        arrival_message = next(
            message
            for message in self.db.pending_messages()
            if message["name"] == "ShipmentArrived"
        )
        arrival_event = arrival_message["variables"]["arrivalEvent"]
        self.db.record_arrival("S-test", 999, arrival_event)
        record = self.db.get("S-test")
        self.assertEqual(record["transportState"], "ARRIVED")
        self.assertFalse(record["vehicleReleased"])

        response = self.http.post(
            "/shipments/S-test/acknowledge-receipt", json=receipt
        )
        self.assertEqual(response.status_code, 202, response.json)
        receipt_message = next(
            message
            for message in self.db.pending_messages()
            if message["name"] == "ReceiptAcknowledged"
        )
        receipt_event = receipt_message["variables"]["receiptEvent"]
        self.db.release_vehicle("S-test", 999, receipt_event)
        record = self.db.get("S-test")
        self.assertEqual(record["transportState"], "COMPLETED")
        self.assertTrue(record["vehicleReleased"])
        self.db.release_vehicle("S-test", 999, receipt_event)
        self.assertEqual(
            sum(
                event["eventType"] == "VehicleReleased"
                for event in self.db.events("S-test")
            ),
            1,
        )
        self.create(shipment="S-next", journey="J-next", vehicle="V-01")

    def test_p2_manual_event_rejects_invalid_time_and_mismatched_receipt(self):
        self.loading_wait()
        self.verify()
        self.db.advance(
            "S-test",
            self.key,
            "AWAITING_LOADING",
            "AWAITING_DISPATCH",
            "LoadingVerified",
        )
        self.db.reserve_command("S-test", "DispatchApproved", "dispatcher")
        self.db.advance(
            "S-test", self.key, "AWAITING_DISPATCH", "DISPATCHED", "ShipmentDispatched"
        )
        dispatch_event = next(
            event
            for event in self.db.events("S-test")
            if event["eventType"] == "ShipmentDispatched"
        )
        self.db.start_transport("S-test", 999, dispatch_event)
        self.db.start_journey("S-test", 999)
        arrival = dict(
            eventId="arrival-bad-time",
            occurredAt="not-a-timestamp",
            recipientId="C-01",
        )
        self.assertEqual(
            self.http.post("/shipments/S-test/arrive", json=arrival).status_code,
            400,
        )
        arrival["occurredAt"] = "2026-12-01T10:22:00Z"
        self.assertEqual(
            self.http.post("/shipments/S-test/arrive", json=arrival).status_code,
            202,
        )
        arrival_event = next(
            message["variables"]["arrivalEvent"]
            for message in self.db.pending_messages()
            if message["name"] == "ShipmentArrived"
        )
        self.db.record_arrival("S-test", 999, arrival_event)
        bad_receipt = dict(
            eventId="receipt-wrong-count",
            occurredAt="2026-12-01T10:24:00Z",
            recipientId="C-01",
            quantityReceived=2,
        )
        self.assertEqual(
            self.http.post(
                "/shipments/S-test/acknowledge-receipt", json=bad_receipt
            ).status_code,
            409,
        )
        self.assertFalse(self.db.get("S-test")["vehicleReleased"])

    def test_invalid_input(self):
        for change in (
            {"quantity": True},
            {"quantity": 0},
            {"quantity": 101},
            {"vehicleId": "V-03"},
            {"shipmentId": " "},
            {"deliveryDeadline": "tomorrow"},
            {"originalShipmentId": "old"},
            {"productId": "UNKNOWN"},
            {"batchId": "DEMO-YOGHURT-001"},
            {"productId": None},
            {"batchId": ""},
        ):
            self.assertEqual(
                self.http.post("/shipments", json={**sample(), **change}).status_code,
                400,
            )

        self.assertEqual(self.http.post("/shipments", json=[]).status_code, 400)
        self.loading_wait()
        for value in (False, "true", 1, None):
            self.assertEqual(
                self.http.post(
                    "/shipments/S-test/verify-loading",
                    json=dict(
                        actor="warehouse",
                        quantityChecked=value,
                        packagingChecked=True,
                        vehicleReady=True,
                    ),
                ).status_code,
                400,
            )

    def test_unroutable_shipment_ids_have_no_side_effects(self):
        with patch.object(service, "engine_call") as engine:
            for shipment_id in (
                "S/001",
                "S%2F001",
                "S\\001",
                ".",
                "..",
                "S?001",
                "S#001",
                "S 001",
                "S\n001",
                "S\x00001",
                "S-é",
            ):
                with self.subTest(shipment_id=shipment_id):
                    response = self.http.post(
                        "/shipments", json=sample(shipment=shipment_id)
                    )
                    self.assertEqual(response.status_code, 400, response.json)
                    self.assertIn("shipmentId", response.json["error"])
            engine.assert_not_called()
        with self.db.transaction() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM shipments").fetchone()[0], 0
            )
            self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], 0)
        self.assertEqual(self.available(), 20)
        self.create()

    def test_supported_shipment_id_is_routable(self):
        shipment_id = "S_001.v2-3"
        self.create(shipment=shipment_id)
        self.assertEqual(self.http.get(f"/shipments/{shipment_id}").status_code, 200)
        self.assertEqual(
            self.http.get(f"/shipments/{shipment_id}/events").status_code, 200
        )
        response = self.http.post(
            f"/shipments/{shipment_id}/dispatch", json={"actor": "dispatcher"}
        )
        self.assertEqual(response.status_code, 409)

    def test_uncertain_start_stays_visible_and_blocks_duplicate(self):
        with (
            patch.object(service, "engine_call", side_effect=TimeoutError),
            self.assertLogs(service.app.logger, level="ERROR"),
        ):
            self.assertEqual(
                self.http.post("/shipments", json=sample()).status_code, 502
            )
        self.assertEqual(self.http.post("/shipments", json=sample()).status_code, 409)

    def test_supply_id_cannot_shadow_a_future_business_activity(self):
        self.wait_shortage()
        # Supply IDs must not collide with internal activity IDs.
        self.db.reserve_command(
            "S-two", "StockReplenished", "warehouse", ("P1:S-two:ShipmentCancelled", 1)
        )
        self.check("S-two", 3)
        self.db.reserve_command("S-two", "ShipmentCancellationRequested", "dispatcher")
        self.db.advance(
            "S-two", self.key, "WAITING_STOCK", "CANCELLED", "ShipmentCancelled"
        )
        self.assertEqual(self.db.get("S-two")["state"], "CANCELLED")

    def test_expired_uncertain_messages_are_not_republished(self):
        self.loading_wait()
        self.verify()
        with self.db.transaction() as db:
            import json

            message_id, raw = db.execute("SELECT id,data FROM outbox").fetchone()
            data = json.loads(raw)
            data["createdAt"] = "2020-01-01T00:00:00Z"
            db.execute(
                "UPDATE outbox SET data=? WHERE id=?", (json.dumps(data), message_id)
            )
        self.assertEqual(self.db.pending_messages(), [])
        with self.db.transaction() as db:
            self.assertIn(
                "EXPIRED", db.execute("SELECT data FROM outbox").fetchone()[0]
            )

    def test_catalog_contains_five_distinct_products_and_preserves_stock_on_reopen(
        self,
    ):
        stock = self.http.get("/stock").json
        self.assertEqual(stock["datasetVersion"], 3)
        self.assertEqual(stock["available"], 100)
        self.assertEqual(stock["shipmentCount"], 0)
        self.assertEqual(len(stock["products"]), 5)
        self.assertEqual(len({p["productId"] for p in stock["products"]}), 5)
        self.assertTrue(
            all(
                p["available"] == 20 and p["packsPerCrate"] == 12
                for p in stock["products"]
            )
        )
        self.create(quantity=15)
        self.check()
        reopened = Registry(self.db.path).inventory()
        self.assertEqual(reopened["available"], 85)
        self.assertEqual(reopened["shipmentCount"], 1)
        self.assertEqual(
            next(
                p["available"]
                for p in reopened["products"]
                if p["productId"] == "PRD-001"
            ),
            5,
        )

    def test_other_products_cannot_satisfy_milk_shortage(self):
        self.wait_shortage()
        self.assertEqual(self.available(), 5)
        self.assertEqual(self.available("PRD-002"), 20)
        self.assertEqual(self.db.inventory()["available"], 85)
        self.assertEqual(self.db.get("S-two")["state"], "WAITING_STOCK")
        self.db.reserve_command(
            "S-two", "StockReplenished", "warehouse", ("milk-supply", 5)
        )
        self.assertTrue(self.check("S-two", 3)["stockAvailable"])
        self.assertEqual(self.available(), 0)
        for product in self.db.inventory()["products"][1:]:
            self.assertEqual(product["available"], 20)

    def test_different_products_reserve_independently(self):
        self.create(quantity=15)
        self.check()
        self.create(
            shipment="S-two",
            journey="J-two",
            vehicle="V-02",
            quantity=10,
            product="PRD-005",
            batch="DEMO-SALAD-001",
        )
        self.assertTrue(self.check("S-two", 2)["stockAvailable"])
        self.assertEqual(self.available(), 5)
        self.assertEqual(self.available("PRD-005"), 10)
        self.assertEqual(self.available("PRD-004"), 20)

    def test_simultaneous_orders_cannot_overdraw_one_product(self):
        self.create(quantity=15)
        self.create(shipment="S-two", journey="J-two", vehicle="V-02", quantity=15)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda item: self.db.check_stock(item[0], self.key, item[1]),
                    [("S-test", 10), ("S-two", 11)],
                )
            )
        self.assertCountEqual([r["stockAvailable"] for r in results], [True, False])
        self.assertEqual(self.available(), 5)

    def test_replenishment_rejects_other_product_or_batch_without_side_effects(self):
        self.wait_shortage()
        for change in ({"productId": "PRD-002"}, {"batchId": "DEMO-YOGHURT-001"}):
            response = self.http.post(
                "/shipments/S-two/replenish-stock",
                json={
                    "actor": "warehouse",
                    "eventId": "wrong-product",
                    "quantity": 5,
                    **change,
                },
            )
            self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(self.available(), 5)
        self.assertIsNone(self.db.get("S-two")["pendingCommand"])

    def test_product_validation_does_not_start_engine_or_reserve_vehicle(self):
        with patch.object(service, "engine_call") as engine:
            for change in (
                {"productId": "PRD-006"},
                {"batchId": "OTHER-BATCH"},
                {"productId": "PRD-002"},
                {"productId": None},
            ):
                response = self.http.post("/shipments", json={**sample(), **change})
                self.assertEqual(response.status_code, 400, response.json)
            missing = sample()
            del missing["batchId"]
            self.assertEqual(
                self.http.post("/shipments", json=missing).status_code, 400
            )
            engine.assert_not_called()
        self.assertEqual(self.db.inventory()["shipmentCount"], 0)
        self.create()

    def test_activity_and_xes_export_preserve_product_batch_identity(self):
        import xml.etree.ElementTree as ET

        self.loading_wait()
        events = self.db.events("S-test")
        self.assertTrue(
            all(
                e["payload"]["productId"] == "PRD-001"
                and e["payload"]["batchId"] == "DEMO-MILK-001"
                for e in events
            )
        )
        path = Path(self.temp.name) / "events.xes"
        export_xes(path, {"S-test": events})
        ns = {"x": "http://www.xes-standard.org/"}
        root = ET.parse(path).getroot()
        self.assertEqual(
            root.find("x:trace/x:string[@key='productId']", ns).get("value"), "PRD-001"
        )
        self.assertEqual(
            root.find("x:trace/x:event/x:string[@key='batchId']", ns).get("value"),
            "DEMO-MILK-001",
        )

    def test_legacy_pooled_registry_is_rejected_without_rewriting_it(self):
        import sqlite3

        path = Path(self.temp.name) / "legacy.sqlite"
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE inventory (id INTEGER PRIMARY KEY, available INTEGER NOT NULL)"
            )
            db.execute("INSERT INTO inventory VALUES (1,17)")
        db.close()
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "legacy pooled stock"):
            Registry(path)
        self.assertEqual(path.read_bytes(), before)

    def test_demo_menu_can_select_a_different_chilled_product(self):
        from tools import demo

        def local_http(base, path, data=None):
            response = (
                self.http.get(path) if data is None else self.http.post(path, json=data)
            )
            return response.status_code, response.json

        with (
            patch.object(demo, "http", side_effect=local_http),
            patch.object(demo, "wait_for_action"),
            patch("builtins.input", side_effect=["A", "4"]),
            patch("builtins.print"),
        ):
            demo.perform("http://example.test", "1")
        record = self.db.get("demo-A")
        self.assertEqual(record["productId"], "PRD-004")
        self.assertEqual(record["batchId"], "DEMO-CHICKEN-001")
        self.assertEqual(record["quantity"], 15)


class Compliance(unittest.TestCase):
    def test_portal_xml_has_required_editor_metadata(self):
        import xml.etree.ElementTree as ET

        root = ET.parse(MODEL).getroot()
        resources = root.find("./specification/resources")
        self.assertIsNotNone(resources.find("subProcesses"))
        for event in resources.findall("events/event"):
            with self.subTest(event=event.get("id")):
                self.assertIsNotNone(event.find("custom/eventData"))
                location = event.find("custom/visualization/location")
                size = event.find("custom/visualization/size")
                self.assertIsNotNone(location)
                self.assertIsNotNone(size)
                self.assertGreaterEqual(int(location.get("xLoc")), 0)
                self.assertGreaterEqual(int(location.get("yLoc")), 0)
                self.assertGreater(int(size.get("width")), 0)
                self.assertGreater(int(size.get("height")), 0)

    def test_unsupported_models_are_rejected_before_replay(self):
        import xml.etree.ElementTree as ET

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.xml"
            for fault in (
                "root",
                "events",
                "nested",
                "relation",
                "endpoint",
                "marking",
            ):
                with self.subTest(fault=fault):
                    tree = ET.parse(MODEL)
                    root = tree.getroot()
                    if fault == "root":
                        root.tag = "{urn:unsupported}dcrgraph"
                    elif fault == "events":
                        root.find("./specification/resources/events").clear()
                    elif fault == "nested":
                        ET.SubElement(
                            root.find("./specification/resources/events/event"),
                            "event",
                            id="nested",
                        )
                    elif fault == "relation":
                        root.find(
                            "./specification/constraints/conditions/condition"
                        ).tag = "{urn:unsupported}condition"
                    elif fault == "endpoint":
                        del root.find(
                            "./specification/constraints/conditions/condition"
                        ).attrib["sourceId"]
                    else:
                        marking = root.find("./runtime/marking")
                        marking.remove(marking.find("pendingResponses"))
                    tree.write(path)
                    with self.assertRaises(ValueError):
                        Graph(path)

    def test_cli_rejects_empty_or_unsupported_evidence(self):
        namespace = 'xmlns="http://www.xes-standard.org/"'
        trace = '<trace><string key="concept:name" value="case"/></trace>'
        inputs = (
            ("empty.xes", f"<log {namespace}/>", "no recognized traces"),
            ("empty.json", "{}", "no recognized traces"),
            (
                "namespace-free.xes",
                '<log><trace><string key="concept:name" value="case"/>'
                '<event><string key="concept:name" value="ShipmentDispatched"/></event>'
                "</trace></log>",
                "expected an XES log in namespace",
            ),
            (
                "wrong-namespace.xes",
                '<log xmlns="urn:unsupported"><trace/></log>',
                "expected an XES log in namespace",
            ),
            (
                "duplicate.xes",
                f"<log {namespace}>{trace}{trace}</log>",
                "duplicate XES trace",
            ),
            ("unnamed.xes", f"<log {namespace}><trace/></log>", "concept:name"),
            ("empty-file.xes", "", "Cannot verify compliance"),
            ("null.json", "null", "expected a JSON event list"),
            ("missing-activity.json", "[{}]", "non-empty activity name"),
            ("numeric-activity.json", "[42]", "non-empty activity name"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for filename, content, message in inputs:
                with self.subTest(filename=filename):
                    path = Path(directory) / filename
                    path.write_text(content)
                    result = subprocess.run(
                        [sys.executable, str(ROOT / "compliance/check.py"), str(path)],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    self.assertEqual(result.returncode, 2, result)
                    self.assertIn(message, result.stderr)
                    self.assertEqual(result.stdout, "")

    def test_cli_checks_saved_execution_evidence(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "compliance/check.py"),
                str(ROOT / "compliance/p1/execution-log.xes"),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        import json

        cases = json.loads(result.stdout)
        self.assertEqual(len(cases), 3)
        self.assertTrue(
            all(case["valid"] and case["accepting"] for case in cases.values())
        )

    def test_valid_replenishment_and_cancellation(self):
        happy = [
            "ShipmentCreated",
            "StockChecked",
            "StockAllocated",
            "TransportAssigned",
            "LoadingVerified",
            "ShipmentDispatched",
        ]
        recovery = [
            "ShipmentCreated",
            "StockChecked",
            "StockUnavailable",
            "StockReplenished",
            "StockChecked",
            "StockUnavailable",
            "StockReplenished",
        ] + happy[1:]
        for events in (
            happy,
            recovery,
            [
                "ShipmentCreated",
                "StockChecked",
                "StockUnavailable",
                "ShipmentCancelled",
            ],
        ):
            self.assertEqual(
                replay(MODEL, events), dict(valid=True, accepting=True, pending=[])
            )

    def test_invalid_order_cancellation_and_unfinished_trace(self):
        traces = [
            ["ShipmentCreated", "LoadingVerified"],
            ["ShipmentCreated", "StockChecked", "ShipmentDispatched"],
            [
                "ShipmentCreated",
                "StockChecked",
                "StockUnavailable",
                "ShipmentCancelled",
                "ShipmentDispatched",
            ],
            ["ShipmentCreated", "StockChecked", "StockUnavailable", "StockAllocated"],
            [
                "ShipmentCreated",
                "StockChecked",
                "StockUnavailable",
                "StockReplenished",
                "StockAllocated",
            ],
            ["ShipmentCreated", "StockChecked", "StockAllocated", "ShipmentCancelled"],
        ]
        for events in traces:
            self.assertFalse(replay(MODEL, events)["valid"], events)
        for events in (
            [],
            ["ShipmentCreated"],
            ["ShipmentCreated", "StockChecked", "StockUnavailable"],
        ):
            self.assertFalse(replay(MODEL, events)["accepting"], events)


if __name__ == "__main__":
    unittest.main()
