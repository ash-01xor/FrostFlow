"""Build the P2 DCR compliance graph and export a reviewed P2 execution log.

Usage (from the repository root):

    python compliance/p2/build_p2_evidence.py rules
    python compliance/p2/build_p2_evidence.py log runtime/verification-results/p2-siddhi.json

``rules`` (re)writes compliance/p2/rules.xml in the same portal-compatible
layout as P1. ``log`` converts the evidence JSON written by
stage_tests/check_p2_siddhi.py into compliance/p2/execution-log.xes, keeping
P2 business activities plus the neighbouring P1 ShipmentDispatched handoff.
High-frequency GPSPositionUpdated/ETAUpdated telemetry is excluded: it is
sensor data evaluated by Siddhi, not a business activity in this graph.
"""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from compliance.check import export_xes  # noqa: E402

HERE = Path(__file__).parent

# (activity, x, y) — layout for the ProcessDoctor/DCR editor canvas.
EVENTS = [
    ("ShipmentDispatched", 60, 60),
    ("TransportStarted", 320, 60),
    ("JourneyStarted", 580, 60),
    ("ShipmentArrived", 840, 60),
    ("ReceiptAcknowledged", 1100, 60),
    ("VehicleReleased", 1360, 60),
    ("DeliveryRiskDetected", 580, 340),
    ("DispatcherDecisionRecorded", 840, 340),
    ("RouteChangeAcknowledged", 1100, 340),
    ("DeliveryRiskClosedAtArrival", 840, 620),
]
NAMES = [name for name, _, _ in EVENTS]
TELEMETRY = {"GPSPositionUpdated", "ETAUpdated"}

CONDITIONS = [
    ("ShipmentDispatched", "TransportStarted"),
    ("TransportStarted", "JourneyStarted"),
    ("JourneyStarted", "ShipmentArrived"),
    ("JourneyStarted", "DeliveryRiskDetected"),
    ("ShipmentArrived", "ReceiptAcknowledged"),
    # Transport cannot finish (release the vehicle) without receipt.
    ("ReceiptAcknowledged", "VehicleReleased"),
    ("DeliveryRiskDetected", "DispatcherDecisionRecorded"),
    ("DispatcherDecisionRecorded", "RouteChangeAcknowledged"),
    ("DeliveryRiskDetected", "DeliveryRiskClosedAtArrival"),
]
RESPONSES = [
    ("ShipmentDispatched", "TransportStarted"),
    ("TransportStarted", "VehicleReleased"),
    ("ShipmentArrived", "ReceiptAcknowledged"),
    ("ReceiptAcknowledged", "VehicleReleased"),
    # Every accepted delivery-risk alert needs a dispatcher decision...
    ("DeliveryRiskDetected", "DispatcherDecisionRecorded"),
]
INCLUDES = [
    ("DeliveryRiskDetected", "DispatcherDecisionRecorded"),
    # ...and only a decision makes a route-change acknowledgement possible.
    ("DispatcherDecisionRecorded", "RouteChangeAcknowledged"),
    # Arrival before a decision closes the alert instead (recorded just before arrival).
    ("DeliveryRiskDetected", "DeliveryRiskClosedAtArrival"),
]
ONCE = ["ShipmentDispatched", "TransportStarted", "JourneyStarted", "ShipmentArrived",
        "ReceiptAcknowledged", "VehicleReleased", "DispatcherDecisionRecorded",
        "RouteChangeAcknowledged", "DeliveryRiskClosedAtArrival"]
EXCLUDES = [(name, name) for name in ONCE] + [
    # A new alert invalidates any route acknowledgement left from an older decision.
    ("DeliveryRiskDetected", "RouteChangeAcknowledged"),
    # Arrival closes risk handling; an undecided alert is superseded, not owed.
    ("ShipmentArrived", "DeliveryRiskDetected"),
    ("ShipmentArrived", "DispatcherDecisionRecorded"),
    ("ShipmentArrived", "RouteChangeAcknowledged"),
    ("ShipmentArrived", "DeliveryRiskClosedAtArrival"),
    # A decision and an arrival-closure are alternative outcomes of one alert.
    ("DispatcherDecisionRecorded", "DeliveryRiskClosedAtArrival"),
    ("DeliveryRiskClosedAtArrival", "DispatcherDecisionRecorded"),
] + [("VehicleReleased", name) for name in NAMES if name != "VehicleReleased"]
MILESTONES = [
    # No second alert is accepted while a decision is still pending.
    ("DispatcherDecisionRecorded", "DeliveryRiskDetected"),
    # The vehicle is not released while a decision is pending.
    ("DispatcherDecisionRecorded", "VehicleReleased"),
]
INITIAL_INCLUDED = [n for n in NAMES
                    if n not in ("DispatcherDecisionRecorded", "RouteChangeAcknowledged",
                                  "DeliveryRiskClosedAtArrival")]
INITIAL_PENDING = ["ShipmentDispatched"]


def build_rules(path=HERE / "rules.xml"):
    graph = ET.Element("dcrgraph", title="P2 transport and delivery compliance — Rahul Shanbhag",
                       dataTypesStatus="hide")
    spec = ET.SubElement(graph, "specification")
    resources = ET.SubElement(spec, "resources")
    events = ET.SubElement(resources, "events")
    for name, x, y in EVENTS:
        event = ET.SubElement(events, "event", id=name)
        custom = ET.SubElement(event, "custom")
        ET.SubElement(custom, "eventData")
        visual = ET.SubElement(custom, "visualization")
        ET.SubElement(visual, "location", xLoc=str(x), yLoc=str(y))
        ET.SubElement(visual, "size", width="130", height="150")
    labels = ET.SubElement(resources, "labels")
    mappings = ET.SubElement(resources, "labelMappings")
    for name in NAMES:
        ET.SubElement(labels, "label", id=name)
        ET.SubElement(mappings, "labelMapping", eventId=name, labelId=name)
    ET.SubElement(resources, "subProcesses")
    constraints = ET.SubElement(spec, "constraints")
    for plural, singular, pairs in [
        ("conditions", "condition", CONDITIONS), ("responses", "response", RESPONSES),
        ("includes", "include", INCLUDES), ("excludes", "exclude", EXCLUDES),
        ("milestones", "milestone", MILESTONES),
    ]:
        group = ET.SubElement(constraints, plural)
        for source, target in pairs:
            ET.SubElement(group, singular, sourceId=source, targetId=target)
    marking = ET.SubElement(ET.SubElement(graph, "runtime"), "marking")
    ET.SubElement(marking, "executed")
    for tag, names in (("included", INITIAL_INCLUDED), ("pendingResponses", INITIAL_PENDING)):
        group = ET.SubElement(marking, tag)
        for name in names:
            ET.SubElement(group, "event", id=name)
    ET.indent(graph, space="  ")
    ET.ElementTree(graph).write(path, encoding="utf-8", xml_declaration=True)
    return path


def build_log(evidence_path, path=HERE / "execution-log.xes"):
    evidence = json.loads(Path(evidence_path).read_text())
    cases = {}
    for shipment_id, data in evidence["shipments"].items():
        cases[shipment_id] = [
            event for event in data["events"]
            if event["eventType"] not in TELEMETRY
            and (event["process"] == "P2" or event["eventType"] == "ShipmentDispatched")
        ]
    export_xes(path, cases)
    return path


if __name__ == "__main__":
    if sys.argv[1:2] == ["rules"]:
        print(build_rules())
    elif sys.argv[1:2] == ["log"] and len(sys.argv) == 3:
        print(build_log(sys.argv[2]))
    else:
        sys.exit(__doc__)
