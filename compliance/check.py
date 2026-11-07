"""Replay flat DCR graphs (conditions, responses, includes, excludes, milestones).

This local checker reads the submitted XML; it is independent of the HTTP guards.
It does not implement DCR data expressions, hierarchy or timed relations.
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


class Graph:
    def __init__(self, model):
        root = ET.parse(model).getroot()
        if root.tag != "dcrgraph":
            raise ValueError("expected a namespace-free dcrgraph model")
        event_nodes = root.findall("./specification/resources/events/event")
        event_ids = [e.get("id", "") for e in event_nodes]
        if not event_ids or any(not name.strip() for name in event_ids):
            raise ValueError("DCR model needs named events")
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("duplicate DCR event IDs")
        if any(e.findall(".//event") for e in event_nodes):
            raise ValueError("nested DCR events are not supported")
        self.events = set(event_ids)
        self.relations = {}
        relation_types = [
            ("conditions", "condition"),
            ("responses", "response"),
            ("includes", "include"),
            ("excludes", "exclude"),
            ("milestones", "milestone"),
        ]
        for plural, singular in relation_types:
            self.relations[plural] = [
                (e.get("sourceId"), e.get("targetId"))
                for e in root.findall(
                    f"./specification/constraints/{plural}/{singular}"
                )
            ]
        allowed = dict(relation_types)
        if root.find("./specification/constraints") is None:
            raise ValueError("DCR model needs a constraints section")
        for group in root.findall("./specification/constraints/*"):
            if group.tag not in allowed:
                raise ValueError(f"unsupported relation group: {group.tag}")
            if any(
                relation.tag != allowed[group.tag]
                or not relation.get("sourceId")
                or not relation.get("targetId")
                for relation in group
            ):
                raise ValueError(f"unsupported or malformed relation in {group.tag}")
        for marking in ("executed", "included", "pendingResponses"):
            group = root.find(f"./runtime/marking/{marking}")
            if group is None:
                raise ValueError(f"DCR model needs an initial {marking} marking")
            if any(e.tag != "event" or e.get("id") not in self.events for e in group):
                raise ValueError("unsupported or unknown event in initial marking")
        if any(
            e.attrib.keys() - {"sourceId", "targetId"}
            for e in root.findall("./specification/constraints/*/*")
        ):
            raise ValueError("data/timed constraints are not supported")
        if any(
            a not in self.events or b not in self.events
            for pairs in self.relations.values()
            for a, b in pairs
        ):
            raise ValueError("unknown relation endpoint")
        self.included = {
            e.attrib["id"] for e in root.findall("./runtime/marking/included/event")
        }
        self.executed = {
            e.attrib["id"] for e in root.findall("./runtime/marking/executed/event")
        }
        self.pending = {
            e.attrib["id"]
            for e in root.findall("./runtime/marking/pendingResponses/event")
        }

    def execute(self, event):
        if event not in self.events:
            return "unknown activity"
        if event not in self.included:
            return "activity is excluded"
        for source, target in self.relations["conditions"]:
            if (
                target == event
                and source in self.included
                and source not in self.executed
            ):
                return f"condition not met: {source}"
        for source, target in self.relations["milestones"]:
            if target == event and source in self.included and source in self.pending:
                return f"pending milestone: {source}"
        self.executed.add(event)
        self.pending.discard(event)
        self.pending.update(b for a, b in self.relations["responses"] if a == event)
        self.included.difference_update(
            b for a, b in self.relations["excludes"] if a == event
        )
        self.included.update(b for a, b in self.relations["includes"] if a == event)
        return None


def replay(model, events):
    graph = Graph(model)
    for index, event in enumerate(events):
        name = event["eventType"] if isinstance(event, dict) else event
        reason = graph.execute(name)
        if reason:
            return dict(
                valid=False,
                accepting=False,
                violationIndex=index,
                event=name,
                reason=reason,
            )
    outstanding = sorted(graph.pending & graph.included)
    return dict(valid=True, accepting=not outstanding, pending=outstanding)


def export_xes(path, cases):
    root = ET.Element(
        "log", {"xes.version": "1.0", "xmlns": "http://www.xes-standard.org/"}
    )
    for prefix, uri, name in [
        ("concept", "http://www.xes-standard.org/concept.xesext", "Concept"),
        ("time", "http://www.xes-standard.org/time.xesext", "Time"),
    ]:
        ET.SubElement(root, "extension", {"name": name, "prefix": prefix, "uri": uri})
    for shipment_id, events in cases.items():
        trace = ET.SubElement(root, "trace")
        ET.SubElement(trace, "string", {"key": "concept:name", "value": shipment_id})
        if events:
            for key in (
                "productId",
                "productName",
                "batchId",
                "quantityUnit",
                "temperatureProfileId",
            ):
                value = events[0].get("payload", {}).get(key)
                if value is not None:
                    ET.SubElement(trace, "string", {"key": key, "value": str(value)})
        for activity in events:
            event = ET.SubElement(trace, "event")
            for key, value in [
                ("concept:name", activity["eventType"]),
                ("org:resource", activity["actor"]),
                ("eventId", activity["eventId"]),
                ("processInstanceKey", activity["processInstanceKey"] or ""),
            ]:
                ET.SubElement(event, "string", {"key": key, "value": str(value)})
            for key in ("productId", "batchId", "quantityUnit"):
                value = activity.get("payload", {}).get(key)
                if value is not None:
                    ET.SubElement(event, "string", {"key": key, "value": str(value)})
            ET.SubElement(
                event,
                "date",
                {"key": "time:timestamp", "value": activity["occurredAt"]},
            )
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def read_traces(path):
    if path.suffix.lower() == ".xes":
        root = ET.parse(path).getroot()
        ns = {"x": "http://www.xes-standard.org/"}
        if root.tag != "{http://www.xes-standard.org/}log":
            raise ValueError(
                "expected an XES log in namespace http://www.xes-standard.org/"
            )

        def name(element):
            label = element.find("x:string[@key='concept:name']", ns)
            if label is None or not label.get("value", "").strip():
                raise ValueError(
                    "each XES trace and event needs a non-empty concept:name"
                )
            return label.attrib["value"]

        traces = {}
        for trace in root.findall("x:trace", ns):
            case = name(trace)
            if case in traces:
                raise ValueError(f"duplicate XES trace name: {case}")
            traces[case] = [name(event) for event in trace.findall("x:event", ns)]
    else:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            traces = {"trace": data}
        elif isinstance(data, dict):
            traces = {"trace": data["events"]} if "events" in data else data
        else:
            raise ValueError(
                "expected a JSON event list or an object containing traces"
            )
    if not traces:
        raise ValueError("no recognized traces found; compliance was not checked")
    if any(not isinstance(events, list) for events in traces.values()):
        raise ValueError("each trace must contain a list of events")
    for events in traces.values():
        for event in events:
            activity = event.get("eventType") if isinstance(event, dict) else event
            if not isinstance(activity, str) or not activity.strip():
                raise ValueError(
                    "each event needs a non-empty activity name or eventType"
                )
    return traces


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument(
        "--model", type=Path, default=Path(__file__).parent / "p1/rules.xml"
    )
    args = parser.parse_args()
    try:
        results = {
            case: replay(args.model, events)
            for case, events in read_traces(args.trace).items()
        }
    except (ValueError, OSError, ET.ParseError) as error:
        print(f"Cannot verify compliance: {error}", file=sys.stderr)
        return 2
    print(json.dumps(results, indent=2))
    return 0 if all(r["valid"] and r["accepting"] for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
