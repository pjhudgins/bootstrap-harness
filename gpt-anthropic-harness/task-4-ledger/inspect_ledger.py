"""Read-only validation of a closed task-4 ledger; optional directed-smoke checks."""
import argparse
import json
from pathlib import Path

from ledger import AGENT_AUTHOR, AGENT_TAGS, scribe


def inspect(root, name, smoke=False):
    view = scribe.load(root, name)
    problems = [str(f) for f in view.findings]
    if not view.head_closed or view.is_leased():
        problems.append("Expected a closed, unleased ledger")
    harness = [view.current(n) for n in view.names() if n.startswith("harness/")]
    if [e.body.get("seq") for e in harness] != list(range(1, len(harness)+1)):
        problems.append("Harness event sequence is not contiguous")
    for entry in harness:
        if entry.author != "harness" or view.labels(entry.name) != {"harness", "log." + entry.body["kind"]}:
            problems.append("Harness author/tag mismatch: " + entry.name)
    notes = [view.current(n) for n in view.names() if n.startswith("pilot/")]
    for entry in notes:
        if entry.author != AGENT_AUTHOR or not view.labels(entry.name) or view.labels(entry.name) - AGENT_TAGS:
            problems.append("Agent author/tag mismatch: " + entry.name)
    events = [e.body for e in harness]
    completed = [e for e in events if e["kind"] == "turn_complete"]
    results = [e for e in events if e["kind"] == "python_tool_result"]
    errors = [e for e in events if e["kind"] == "python_tool_error"]
    usage = [e for e in events if e["kind"] == "usage"]
    if smoke:
        if len(completed) != 2 or len({e["session_id"] for e in completed}) != 1:
            problems.append("Expected two completed turns in the same SDK session")
        expected_tools = {"add", "fs_read", "fs_list", "ledger_read", "ledger_write"}
        if {e["tool"] for e in results} != expected_tools:
            problems.append("Expected successful results from all five tools")
        if not any(e["tool"] == "add" and e["result"] == {"sum": 42.0} for e in results):
            problems.append("Missing actual Python sum")
        if len(view.history("harness/00000001")) != 1:
            problems.append("Protected harness entry history changed")
        if not any(e["tool"] == "ledger_write" and "Agent names must be under pilot/" in e["error"] for e in errors):
            problems.append("Missing directed overwrite refusal")
        history = view.history("pilot/smoke")
        if len(history) != 2 or history[1].prev != history[0].id:
            problems.append("Expected two linked pilot/smoke revisions")
        if not any(e["tool"] == "fs_read" and e["result"].get("path", "").startswith("origins/onboarding_")
                   and e["result"].get("next_line") is None for e in results):
            problems.append("Missing complete onboarding read")
        if any(e["kind"] in {"turn_failed", "conversation_error"} for e in events):
            problems.append("Unexpected conversation failure")
    return {"ledger": name, "closed": view.head_closed, "lease_present": view.is_leased(),
            "harness_events": len(harness), "agent_notes": len(notes),
            "completed_turns": len(completed), "successful_tools": sorted({e["tool"] for e in results}),
            "tool_refusals": len(errors), "final_sdk_cost_usd": usage[-1]["total_cost_usd"] if usage else None,
            "problems": problems}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", help="Ledger directory name under this task's ledgers/")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    report = inspect(Path(__file__).resolve().parent / "ledgers", args.ledger, args.smoke)
    print(json.dumps(report, indent=2))
    raise SystemExit(1 if report["problems"] else 0)
