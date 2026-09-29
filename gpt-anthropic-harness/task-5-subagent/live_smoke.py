"""Directed live task-5 smoke. Uses real SDK/model sessions and preserves its ledger.

Bar: informative prototype evidence and surviving records, not production.
"""
import json
from pathlib import Path
import threading
import time

from conversation import Conversation, worker
from ledger import LedgerLog
from inspect_ledger import inspect

TASK = Path(__file__).resolve().parent
PROMPT = """Bar: useful prototype evidence and truthful surviving records, not production.
This is a directed test. Complete onboarding first. Then:
1. Write pilot/draft with body print('draft') and tag pilot.note. Export its pinned
body to workspace:/smoke-draft.py using fs_write.
2. Run scripts:/safe_test.py with output_name pilot/script-output and read its ledger body.
3. Try python_execute on workspace:/smoke-draft.py with output_name pilot/denied-output.
This is an expected bounds refusal. Report the refusal and continue.
4. Write pilot/instructions with these instructions (include the Bar line):
Bar: useful prototype evidence and truthful surviving records, not production.
Read your required onboarding. Call add with 19 and 23. Attempt fs_read on
bootstrap-harness/rules.md once; this should be refused by your bounds.
Write pilot/child/report using pilot.note with your finding, then return a short report.
5. Start a haiku child with that exact instruction ID, result_name pilot/child-result,
and these bounds:
{"fs.read":["origins/**"],"fs.write":[],"fs.execute":[],
"ledger.read":["pilot/**"],"ledger.write":["pilot/child/**"],"ledger.tags":["pilot.note"]}
6. Immediately call add(20,22) while the child runs. Query subagent_status with
wait_seconds 0. Report the results so far and finish this turn without waiting.
Do not initiate any additional tests."""
FOLLOWUP = """Bar: truthful verification of the directed test.
Use subagent_status to confirm the child is complete, read pilot/child-result,
pilot/child/report and pilot/script-output with ledger_read, then summarize the
actual evidence and any discrepancies. Do not run additional tests."""


def wait_for(state, predicate, seconds=260):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        snapshot = state.snapshot()
        if snapshot["status"] == "error":
            raise RuntimeError(snapshot["error"])
        if predicate(snapshot):
            return snapshot
        time.sleep(0.1)
    raise TimeoutError("Live smoke did not reach the expected state.")


def main():
    with LedgerLog(TASK / "ledgers") as log:
        state = Conversation(log, "sonnet")
        thread = threading.Thread(target=worker, args=(state, TASK))
        thread.start()
        print(json.dumps({"ledger": log.name}), flush=True)
        try:
            wait_for(state, lambda s: s["status"] == "ready")
            state.submit(PROMPT)
            wait_for(state, lambda s: s["status"] == "ready")
            wait_for(state, lambda s: bool(s["children"]) and all(c["status"] != "running" for c in s["children"].values()))
            state.submit(FOLLOWUP)
            final = wait_for(state, lambda s: s["status"] == "ready")
            print(json.dumps({"children": final["children"], "family_cost_usd": final["family_cost_usd"],
                              "actors": list(final["agent_usage"]), "workspace": final["workspace"]}), flush=True)
        finally:
            state.stop()
            thread.join(timeout=30)
            if thread.is_alive():
                log.failed = True
                raise RuntimeError("Worker did not stop; preserving ledger/lease.")
        if state.snapshot()["error"]:
            raise RuntimeError(state.snapshot()["error"])
    report = inspect(log.root, log.name, smoke=True, closed=True)
    print(json.dumps(report, indent=2), flush=True)
    return bool(report["problems"])


if __name__ == "__main__":
    raise SystemExit(main())
