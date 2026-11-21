"""Manual P5 arrangements and collection acknowledgements for the local demo."""

import re
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from quality import text, utc


def timestamp(data, field):
    value = utc(data.get(field), field)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def create_blueprint(get_registry, ready):
    bp = Blueprint("corrective", __name__)

    @bp.get("/corrective-fulfilments")
    def cases():
        return jsonify(get_registry().corrective_cases(request.args.get("originalShipmentId")))

    @bp.get("/corrective-fulfilments/<corrective_id>")
    def case(corrective_id):
        return jsonify(get_registry().get_corrective(corrective_id))

    def manual_input(corrective_id, kind):
        if not ready.is_set():
            return jsonify(error="wait for /health to report ready"), 503
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("send a JSON object")
        common = {"eventId", "occurredAt", "actor", "collector", "bookingReference"}
        allowed = common | ({"scheduledAt", "replacement"} if kind == "ArrangementRequested" else {"quantityCollected"})
        if data.keys() - allowed:
            raise ValueError("unsupported P5 fields: " + ", ".join(sorted(data.keys() - allowed)))
        payload = {k: text(data, k, limit=100) for k in ("actor", "collector", "bookingReference")}
        if kind == "ArrangementRequested":
            payload["scheduledAt"] = timestamp(data, "scheduledAt")
            if "replacement" in data:
                replacement = data["replacement"]
                fields = {"shipmentId", "journeyId", "vehicleId", "routeId", "deliveryDeadline"}
                if not isinstance(replacement, dict) or replacement.keys() != fields:
                    raise ValueError("replacement requires only shipmentId, journeyId, vehicleId, routeId and deliveryDeadline")
                clean = {k: text(replacement, k, limit=100) for k in fields}
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", clean["shipmentId"]):
                    raise ValueError("replacement shipmentId must use ASCII letters, digits, dots, underscores or hyphens")
                if clean["vehicleId"] not in ("V-01", "V-02"):
                    raise ValueError("vehicleId must be V-01 or V-02")
                if clean["routeId"] not in ("route-01", "route-02"):
                    raise ValueError("routeId must be route-01 or route-02")
                clean["deliveryDeadline"] = timestamp(replacement, "deliveryDeadline")
                payload["replacement"] = clean
        else:
            quantity = data.get("quantityCollected")
            if type(quantity) is not int or quantity < 1:
                raise ValueError("quantityCollected must be a positive integer number of crates")
            payload["quantityCollected"] = quantity
        accepted = get_registry().queue_corrective_input(
            corrective_id, kind, text(data, "eventId", limit=100), timestamp(data, "occurredAt"), payload)
        return jsonify(status="accepted", **accepted), 202

    @bp.post("/corrective-fulfilments/<corrective_id>/arrange")
    def arrange(corrective_id):
        return manual_input(corrective_id, "ArrangementRequested")

    @bp.post("/corrective-fulfilments/<corrective_id>/collect")
    def collect(corrective_id):
        return manual_input(corrective_id, "ReturnCollected")

    return bp
