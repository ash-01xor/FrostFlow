"""Small HTTP adapters for transport telemetry, TomTom ETA and Siddhi."""

import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone


ROUTES = {
    "route-01": [
        [48.8566, 2.3522], [48.8582, 2.3407], [48.8611, 2.3252],
        [48.8647, 2.3118], [48.8698, 2.2947],
    ],
    "route-02": [
        [48.8566, 2.3522], [48.8509, 2.3372], [48.8461, 2.3201],
        [48.8404, 2.3028], [48.8347, 2.2872],
    ],
    "route-03": [
        [48.8566, 2.3522], [48.8624, 2.3471], [48.8672, 2.3338],
        [48.8720, 2.3163], [48.8763, 2.2989],
    ],
}


def post_json(url, payload, headers=None, timeout=5):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}


class TransportIntegrations:
    """Uses configured services when present and deterministic demo defaults otherwise."""

    def __init__(self, environ=None):
        self.environ = os.environ if environ is None else environ

    def route(self, route_id):
        return ROUTES[route_id]

    def eta(self, route_id, position, route_index):
        points = self.route(route_id)[route_index:]
        if points and points[0] != position:
            points = [position, *points]
        key = self.environ.get("TOMTOM_API_KEY")
        if key and len(points) >= 2:
            locations = ":".join(f"{lat},{lon}" for lat, lon in points)
            query = urllib.parse.urlencode({
                "key": key, "traffic": "true", "computeTravelTimeFor": "all"
            })
            url = f"https://api.tomtom.com/routing/1/calculateRoute/{locations}/json?{query}"
            with urllib.request.urlopen(url, timeout=8) as response:
                route = json.load(response)["routes"][0]["summary"]
            seconds = route.get("travelTimeInSeconds", route.get("noTrafficTravelTimeInSeconds"))
            source = "tomtom-traffic"
        else:
            meters = sum(self._meters(a, b) for a, b in zip(points, points[1:]))
            seconds = max(30, round(meters / 8.3))
            source = "local-demo-estimate"
        eta = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        return eta.isoformat().replace("+00:00", "Z"), source

    def route_command(self, command):
        """Send a route command through Siddhi and require its simulator acknowledgement."""
        url = self.environ.get("SIDDIHI_COMMAND_URL")
        if not url:
            return {"commandId": command["commandId"], "simulatorAcknowledged": True,
                    "mode": "local-demo"}
        result = post_json(url, command, {"Idempotency-Key": command["commandId"]}, timeout=15)
        if result.get("commandId") != command["commandId"] or result.get("simulatorAcknowledged") is not True:
            raise RuntimeError("Siddhi did not return a matching simulator acknowledgement")
        return result

    @staticmethod
    def _meters(left, right):
        lat1, lon1 = map(math.radians, left)
        lat2, lon2 = map(math.radians, right)
        dlat, dlon = lat2 - lat1, lon2 - lon1
        haversine = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 6371000 * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))

