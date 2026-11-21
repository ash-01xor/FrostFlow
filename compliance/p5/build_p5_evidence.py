"""Build flat P5 DCR rules and project real live-run journals to a separate XES log.

    .venv/bin/python compliance/p5/build_p5_evidence.py rules
    .venv/bin/python compliance/p5/build_p5_evidence.py live runtime/p5-compliance/evidence.json

The live command requires all four passing engine-backed cases. It writes only after
local replay and forbidden traces pass. It refuses to overwrite existing output.
Use --output-dir to preserve earlier evidence. ProcessDoctor is a separate manual check.
"""

import argparse
import copy
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "stage_tests")]
from compliance.check import export_xes, replay
from check_p5_fulfilment import SCENARIOS, SOURCE

HERE = Path(__file__).parent
EVENTS = ["ReplaceDisposition", "ReturnDisposition", "ReplacementAuthorized", "ReturnAuthorized",
          "CollectionBooked", "ReplacementRequested", "ReplacementDispatched", "ReplacementCancelled",
          "ReturnCollected", "ReplacementAdmitted", "ReplacementRejected",
          "CorrectiveFulfilmentCompleted", "CorrectiveFulfilmentFailed"]
REPLACEMENT = {"ReplacementAuthorized", "ReplacementRequested", "ReplacementDispatched", "ReplacementCancelled",
               "ReplacementAdmitted", "ReplacementRejected", "CorrectiveFulfilmentFailed"}
CONDITIONS = [
    ("ReplaceDisposition", "ReplacementAuthorized"), ("ReturnDisposition", "ReturnAuthorized"),
    ("ReplacementAuthorized", "CollectionBooked"), ("ReturnAuthorized", "CollectionBooked"),
    ("ReplacementAuthorized", "ReplacementRequested"), ("CollectionBooked", "ReplacementRequested"),
    ("CollectionBooked", "ReturnCollected"), ("ReplacementRequested", "ReplacementDispatched"),
    ("ReplacementRequested", "ReplacementCancelled"), ("ReplacementDispatched", "ReplacementAdmitted"),
    ("ReplacementDispatched", "ReplacementRejected"), ("ReturnCollected", "CorrectiveFulfilmentCompleted"),
    ("ReplacementAdmitted", "CorrectiveFulfilmentCompleted"), ("ReturnCollected", "CorrectiveFulfilmentFailed"),
    ("ReplacementRejected", "CorrectiveFulfilmentFailed"), ("ReplacementCancelled", "CorrectiveFulfilmentFailed"),
]
RESPONSES = [
    ("ReplaceDisposition", "ReplacementAuthorized"), ("ReturnDisposition", "ReturnAuthorized"),
    ("ReplacementAuthorized", "CollectionBooked"), ("ReturnAuthorized", "CollectionBooked"),
    ("ReplacementAuthorized", "ReplacementRequested"), ("CollectionBooked", "ReturnCollected"),
    ("ReplacementRequested", "ReplacementDispatched"), ("ReplacementRequested", "ReplacementCancelled"),
    ("ReplacementDispatched", "ReplacementAdmitted"), ("ReplacementDispatched", "ReplacementRejected"),
    *[(d, terminal) for d in ("ReplaceDisposition", "ReturnDisposition")
      for terminal in ("CorrectiveFulfilmentCompleted", "CorrectiveFulfilmentFailed")],
]
EXCLUDES = [
    ("ReplaceDisposition", "ReturnDisposition"), ("ReplaceDisposition", "ReturnAuthorized"),
    ("ReturnDisposition", "ReplaceDisposition"), *[("ReturnDisposition", event) for event in sorted(REPLACEMENT)],
    ("ReplacementDispatched", "ReplacementCancelled"),
    *[("ReplacementCancelled", event) for event in ("ReplacementDispatched", "ReplacementAdmitted", "ReplacementRejected", "CorrectiveFulfilmentCompleted")],
    ("ReplacementAdmitted", "ReplacementRejected"), ("ReplacementAdmitted", "CorrectiveFulfilmentFailed"),
    ("ReplacementRejected", "ReplacementAdmitted"), ("ReplacementRejected", "CorrectiveFulfilmentCompleted"),
    *[(event, event) for event in EVENTS],
    *[(terminal, event) for terminal in ("CorrectiveFulfilmentCompleted", "CorrectiveFulfilmentFailed")
      for event in EVENTS if event != terminal],
]

FORBIDDEN = {
    "work-without-disposition": ["ReplacementAuthorized"],
    "booking-without-authorization": ["CollectionBooked"],
    "collection-before-booking": ["ReturnDisposition", "ReturnAuthorized", "ReturnCollected"],
    "return-completion-without-collection": ["ReturnDisposition", "ReturnAuthorized", "CollectionBooked", "CorrectiveFulfilmentCompleted"],
    "replacement-dispatch-is-not-delivery": ["ReplaceDisposition", "ReplacementAuthorized", "CollectionBooked", "ReplacementRequested",
                                             "ReplacementDispatched", "ReturnCollected", "CorrectiveFulfilmentCompleted"],
    "rejected-replacement-is-not-success": ["ReplaceDisposition", "ReplacementAuthorized", "CollectionBooked", "ReplacementRequested",
                                            "ReplacementDispatched", "ReplacementRejected", "ReturnCollected", "CorrectiveFulfilmentCompleted"],
    "cancelled-replacement-is-not-success": ["ReplaceDisposition", "ReplacementAuthorized", "CollectionBooked", "ReplacementRequested",
                                             "ReplacementCancelled", "ReturnCollected", "CorrectiveFulfilmentCompleted"],
    "return-cannot-request-replacement": ["ReturnDisposition", "ReturnAuthorized", "CollectionBooked", "ReplacementRequested"],
    "failure-needs-negative-outcome": ["ReplaceDisposition", "ReplacementAuthorized", "CollectionBooked", "ReplacementRequested",
                                       "ReturnCollected", "CorrectiveFulfilmentFailed"],
    "booking-cannot-repeat": ["ReturnDisposition", "ReturnAuthorized", "CollectionBooked", "CollectionBooked"],
}


def build_rules(path=HERE / "rules.xml"):
    root = ET.Element("dcrgraph", title="P5 corrective fulfilment compliance", dataTypesStatus="hide")
    resources = ET.SubElement(ET.SubElement(root, "specification"), "resources")
    events = ET.SubElement(resources, "events")
    labels, mappings = ET.SubElement(resources, "labels"), ET.SubElement(resources, "labelMappings")
    for i, name in enumerate(EVENTS):
        event = ET.SubElement(events, "event", id=name)
        custom = ET.SubElement(event, "custom")
        ET.SubElement(custom, "eventData")
        visual = ET.SubElement(custom, "visualization")
        ET.SubElement(visual, "location", xLoc=str((i % 4) * 280 + 40), yLoc=str((i // 4) * 230 + 40))
        ET.SubElement(visual, "size", width="170", height="150")
        ET.SubElement(labels, "label", id=name)
        ET.SubElement(mappings, "labelMapping", eventId=name, labelId=name)
    ET.SubElement(resources, "subProcesses")
    constraints = ET.SubElement(root.find("specification"), "constraints")
    for plural, singular, pairs in (("conditions", "condition", CONDITIONS), ("responses", "response", RESPONSES),
                                   ("includes", "include", []), ("excludes", "exclude", EXCLUDES)):
        group = ET.SubElement(constraints, plural)
        for source, target in sorted(set(pairs)):
            ET.SubElement(group, singular, sourceId=source, targetId=target)
    marking = ET.SubElement(ET.SubElement(root, "runtime"), "marking")
    ET.SubElement(marking, "executed")
    included = ET.SubElement(marking, "included")
    for name in EVENTS:
        ET.SubElement(included, "event", id=name)
    ET.SubElement(marking, "pendingResponses")
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)


def projected_events(item):
    case = item["case"]
    authorization = case["authorization"]
    events = item["events"]
    if authorization not in events or authorization.get("eventType") != "DispositionRecorded":
        raise ValueError("case authorization must be the actual P4 journal event")
    if item["investigation"].get("decision", {}).get("decision") != case["action"]:
        raise ValueError("case action does not match the applied P4 decision")
    result = []
    for event in events:
        if event == authorization:
            name = "ReplaceDisposition" if case["action"] == "REPLACE" else "ReturnDisposition"
        elif event.get("process") == "P5" and event.get("correctiveId") == case["correctiveId"]:
            name = event["eventType"]
            if name == "CorrectiveWorkAuthorized":
                name = "ReplacementAuthorized" if case["action"] == "REPLACE" else "ReturnAuthorized"
            if name not in EVENTS:
                raise ValueError("unknown P5 activity: " + name)
        else:
            continue
        projected = copy.deepcopy(event)
        projected["eventType"] = name
        result.append(projected)
    return result


def live(evidence_path, output_dir):
    evidence = json.loads(evidence_path.read_text())
    cases = evidence.get("cases", [])
    if (evidence.get("source") != SOURCE or evidence.get("status") != "passed"
            or not evidence.get("runId") or not evidence.get("testedCommit")
            or len(cases) != 4 or {c["scenario"] for c in cases} != set(SCENARIOS)):
        raise ValueError("requires all four passing live P5 scenarios, with revision and input modes")
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = [output_dir / name for name in ("execution-log.xes", "invalid-traces.json", "verification.json")]
    if any(p.exists() for p in outputs):
        raise ValueError("refusing to overwrite evidence; select a new --output-dir")
    rules = HERE / "rules.xml"
    traces, results = {}, {}
    all_cases = []
    for item in cases:
        all_cases.append(item)
        if item["scenario"] == "replacement-rejected":
            if not item.get("followup"):
                raise ValueError("rejected replacement must include its separately completed P4-authorized return")
            all_cases.append(item["followup"])
    for item in all_cases:
        case = item["case"]
        expected_success = item["scenario"] in ("return-collected", "replacement-admitted", "replacement-rejected-followup-return")
        expected_end = "FulfilmentCompleted" if expected_success else "FulfilmentFailed"
        engine = item.get("engineRecords", {}).get("p5", {})
        if (case["status"] != ("COMPLETED" if expected_success else "FAILED")
                or case["originalGoodsStatus"] != "COLLECTED"
                or engine.get("intent") != "ELEMENT_COMPLETED"
                or engine.get("value", {}).get("elementId") != expected_end
                or str(engine.get("value", {}).get("processInstanceKey")) != case["processInstanceKey"]):
            raise ValueError("missing matching P5 engine completion or original collection")
        traces[item["correctiveId"]] = projected_events(item)
        result = replay(rules, traces[item["correctiveId"]])
        if not result["accepting"]:
            raise ValueError(f"live P5 trace failed local replay: {item['scenario']}: {result}")
        results[item["scenario"]] = result
    negatives = {name: {"events": trace, "result": replay(rules, trace)} for name, trace in FORBIDDEN.items()}
    if any(test["result"]["valid"] for test in negatives.values()):
        raise ValueError("forbidden trace unexpectedly passed")
    export_xes(outputs[0], traces)
    outputs[1].write_text(json.dumps(negatives, indent=2) + "\n")
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    report = dict(status="passed", runId=evidence["runId"], capturedAt=evidence["capturedAt"],
                  testedCommit=evidence["testedCommit"], sourceHashes=evidence["sourceHashes"],
                  evidenceHash=sha(evidence_path), modelHash=sha(rules), logHash=sha(outputs[0]),
                  command=f".venv/bin/python compliance/p5/build_p5_evidence.py live {evidence_path.relative_to(ROOT)}",
                  source=SOURCE, modes=evidence["health"]["integrations"], localReplay=results,
                  forbiddenTraces={name: test["result"] for name, test in negatives.items()},
                  scenarios=[dict(scenario=c["scenario"], correctiveId=c["correctiveId"],
                                  refusedActions=c.get("refusedActions", []), engineRecords=c["engineRecords"]) for c in all_cases],
                  warehouseBefore=evidence["warehouseBefore"], warehouseAfter=evidence["warehouseAfter"],
                  receivingBefore=evidence["receivingBefore"], receivingAfter=evidence["receivingAfter"],
                  processDoctor=dict(status="pending", reason="new P5 rules/log require manual external conformance checking"),
                  limitations=["Collection and physical actions are manual/simulated; no external collection service.",
                               "P6 claims and automatic repeated replacement remain unimplemented.",
                               "Existing P1–P4 evidence is not revalidated by this P5 result."])
    outputs[2].write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("rules", "live"))
    parser.add_argument("evidence", type=Path, nargs="?")
    parser.add_argument("--output-dir", type=Path, default=HERE)
    args = parser.parse_args()
    if args.command == "rules":
        build_rules()
    elif args.evidence is None:
        parser.error("live requires an evidence JSON file")
    else:
        report = live(args.evidence.resolve(), args.output_dir)
        print(json.dumps({"status": report["status"], "runId": report["runId"], "localReplay": report["localReplay"]}, indent=2))


if __name__ == "__main__":
    main()
