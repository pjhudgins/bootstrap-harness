"""Read-only task-5 summary and directed smoke assertions; never reads lease contents."""
import argparse
import json
from pathlib import Path

from bounds import Bounds, Mounts, parent_bounds
from ledger import scribe, AGENT_AUTHOR


def inspect(root, name, smoke=False, closed=False):
    view = scribe.load(root, name)
    problems = [str(f) for f in view.findings if closed or f.code != "unclosed"]
    if closed and (not view.head_closed or view.is_leased()):
        problems.append("Expected closed, unleased session")
    entries = [view.current(n) for n in view.names() if n.startswith("harness/")]
    events = [e.body for e in entries]
    for entry in entries:
        if entry.author != "harness" or view.labels(entry.name) != {"harness", "log." + entry.body["kind"]}:
            problems.append("Event author/tag mismatch: " + entry.name)
        for ref in entry.body.get("text_entries", []):
            text = view.line(ref["id"])
            if not text or text.get("name") != ref["name"] or text.get("author") != ref["author"] or not isinstance(text.get("body"), str):
                problems.append("Invalid message reference: " + entry.id)
    starts = [e for e in events if e["kind"] == "subagent_started"]
    ends = [e for e in events if e["kind"] in {"subagent_completed", "subagent_failed", "subagent_cancelled"}]
    tools = [e for e in events if e["kind"] == "python_tool_result"]
    errors = [e for e in events if e["kind"] in {"python_tool_error", "tool_denied", "tool_invalid", "tool_failed"}]
    complete = [e for e in events if e["kind"] == "turn_complete"]
    for start in starts:
        config = next((e for e in events if e["kind"] == "configuration" and e.get("actor") == start["parent"]), {})
        recorded = config.get("mounts", {})
        mounts = Mounts(**{k: v for k, v in recorded.items() if k in {"workspace", "scripts"}})
        parent = Bounds.parse(config["bounds"], mounts) if config.get("bounds") else parent_bounds(mounts)
        if not Bounds.parse(start["bounds"], mounts).subset_of(parent):
            problems.append("Escalated child grant")
    if smoke:
        expected = {"add", "fs_list", "fs_read", "fs_write", "python_execute", "ledger_read", "ledger_write", "subagent_start", "subagent_status"}
        if not expected <= {e["tool"] for e in tools}:
            problems.append("Not all nine tools succeeded")
        if not starts or not any(e["kind"] == "subagent_completed" for e in ends):
            problems.append("Missing completed child")
        if not any(e["tool"] == "python_execute" and "Outside fs.execute" in e["error"] for e in errors):
            problems.append("Missing draft execution refusal")
        if not any(e["actor"] != AGENT_AUTHOR and e["tool"] == "fs_read" and "Outside fs.read" in e["error"] for e in errors):
            problems.append("Missing child read refusal")
        if len({e["session_id"] for e in complete}) < 2:
            problems.append("Missing independent completed SDK sessions")
        if starts and ends:
            first_start = next(i for i, e in enumerate(events) if e["kind"] == "subagent_started")
            first_end = next(i for i, e in enumerate(events) if e["kind"] in {"subagent_completed", "subagent_failed", "subagent_cancelled"})
            if not any(e["kind"] == "python_tool_result" and e.get("tool") == "add" and e.get("actor") == AGENT_AUTHOR
                       for e in events[first_start + 1:first_end]):
                problems.append("No parent work observed while child was running")
        successful_runs = [e for e in events if e["kind"] == "execution_complete" and e["exit_code"] == 0]
        if not successful_runs or not any('"sum": 42' in view.line(e["output"]["id"])["body"] for e in successful_runs):
            problems.append("Missing approved script output body with sum 42")
        if not view.current("pilot/child/report"):
            problems.append("Missing child note")
        if any(e["kind"] in {"turn_failed", "conversation_error", "subagent_failed"} for e in events):
            problems.append("Unexpected runtime failure")
    return {"ledger": name, "position": view.position, "well_formed": view.well_formed,
            "closed": view.head_closed, "leased": view.is_leased(), "events": len(events),
            "completed_turns": [{k: e.get(k) for k in ("actor", "session_id")} for e in complete],
            "children": [{k: e.get(k) for k in ("agent_id", "status", "model", "error", "result")} for e in ends],
            "successful_tools": sorted({e["tool"] for e in tools}),
            "refusals": [{k: e.get(k) for k in ("actor", "tool", "error")} for e in errors],
            "problems": problems}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--closed", action="store_true")
    args = parser.parse_args()
    result = inspect(Path(__file__).parent / "ledgers", args.ledger, args.smoke, args.closed)
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result["problems"]))
