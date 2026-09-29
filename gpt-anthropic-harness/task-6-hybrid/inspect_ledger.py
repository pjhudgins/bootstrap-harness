"""Read-only ledger invariants and directed mixed-provider smoke assertions."""
import argparse
import json
from pathlib import Path
from bounds import Bounds, Mounts
from ledger import scribe, AGENT_AUTHOR

def inspect(root, name, smoke=False, closed=False):
    view = scribe.load(root, name)
    problems = [str(f) for f in view.findings if closed or f.code != "unclosed"]
    if closed and (not view.head_closed or view.is_leased()):
        problems.append("Expected closed, unleased ledger")
    entries = [view.current(n) for n in view.names() if n.startswith("harness/")]
    events = [e.body for e in entries]
    for entry in entries:
        if entry.author != "harness" or view.labels(entry.name) != {"harness", "log." + entry.body["kind"]}:
            problems.append("Event author/tag mismatch: " + entry.name)
        for ref in entry.body.get("text_entries", []):
            text = view.line(ref["id"])
            if not text or text.get("name") != ref["name"] or not isinstance(text.get("body"), str):
                problems.append("Invalid prose reference: " + entry.id)
            if "author" in ref and text and text["author"] != ref["author"]:
                problems.append("Prose author mismatch: " + entry.id)
    starts = {e["agent_id"]:e for e in events if e["kind"] == "agent_started"}
    configs = {e["actor"]:e for e in events if e["kind"] == "configuration"}
    ends = {e["agent_id"]:e for e in events if e["kind"] in {"agent_completed","agent_failed","agent_cancelled"}}
    for key, start in starts.items():
        parent_author = AGENT_AUTHOR if start["parent"] == "governor" else starts[start["parent"]]["author"]
        config = configs.get(parent_author, {})
        if not config:
            problems.append("Missing parent configuration: " + key)
            continue
        mounts = Mounts(**{k:v for k,v in config["mounts"].items() if k in {"workspace","scripts"}})
        if not Bounds.parse(start["bounds"], mounts).subset_of(Bounds.parse(config["bounds"], mounts)):
            problems.append("Expanded bounds: " + key)
        if start["role"] == "worker" and start["parent"] not in starts:
            problems.append("Worker without owner: " + key)
        if closed and key not in ends:
            problems.append("Missing terminal actor: " + key)
        if key in ends and start["role"] == "owner":
            for child_id, child in starts.items():
                if child["parent"] == key and child_id in ends and ends[child_id]["seq"] > ends[key]["seq"]:
                    problems.append("Owner finalized before worker: " + key)
    for request in [e for e in events if e["kind"] == "governor_request"]:
        reply = next((e for e in events if e["kind"] in {"governor_response","governor_request_cancelled"}
                      and e["request_id"] == request["request_id"]), None)
        if closed and not reply:
            problems.append("Request without terminal outcome")
        actor = starts[request["owner_id"]]["author"]
        if reply and any(e["kind"] == "python_tool_result" and e.get("actor") == actor and
                         request["seq"] < e["seq"] < reply["seq"] for e in events):
            problems.append("Owner performed work while blocked")
    tools = [e for e in events if e["kind"] == "python_tool_result"]
    denials = [e for e in events if e["kind"] == "tool_denied"]
    turns = [e for e in events if e["kind"] == "turn_complete"]
    if smoke:
        expected = {"add","fs_read","fs_write","python_execute","ledger_read","ledger_write",
                    "agent_start","agent_status","governor_request","governor_resolve"}
        if not expected <= {e["tool"] for e in tools}:
            problems.append("Missing directed tools: " + str(expected - {e["tool"] for e in tools}))
        if len(ends) != 2 or any(e["kind"] != "agent_completed" for e in ends.values()):
            problems.append("Expected completed owner and worker")
        if len({e["session_id"] for e in turns}) != 3:
            problems.append("Expected three independent provider sessions")
        if not any(e["tool"] == "python_execute" and "Outside fs.execute" in e["error"] for e in denials):
            problems.append("Missing draft execute refusal")
        if not any(e["tool"] == "fs_read" and "Outside fs.read" in e["error"] for e in denials):
            problems.append("Missing worker read refusal")
        if not any(e["kind"] == "execution_complete" and e["exit_code"] == 0 and
                   '"sum": 42' in view.line(e["output"]["id"])["body"] for e in events):
            problems.append("Missing safe script output")
        if any(e["kind"] in {"turn_failed","conversation_error","agent_failed"} for e in events):
            problems.append("Unexpected runtime failure")
    return {"ledger":name, "closed":view.head_closed, "leased":view.is_leased(),
            "events":len(events), "successful_tools":sorted({e["tool"] for e in tools}),
            "completed_actors": [{k:e.get(k) for k in ("actor","session_id")} for e in turns],
            "refusals":[{k:e.get(k) for k in ("actor","tool","error")} for e in denials],
            "problems":problems}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--closed", action="store_true")
    args = parser.parse_args()
    report = inspect(Path(__file__).parent/"ledgers",args.ledger,args.smoke,args.closed)
    print(json.dumps(report,indent=2))
    raise SystemExit(bool(report["problems"]))

