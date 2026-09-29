"""Directed, paid live hybrid test. Preserves evidence; never substitutes models."""
import json
from pathlib import Path
import threading
import time

from conversation import Conversation, worker
from ledger import LedgerLog, scribe

TASK = Path(__file__).resolve().parent
PROMPT = """Bar: useful prototype evidence and truthful surviving records, not production.
This is one authorized directed harness test. Complete onboarding, then dispatch
one GPT gpt-6-sol task OWNER with the following brief. You are the governor:
do not perform or quality-grade the owner's work. Pass the owner all six of your
bounds unchanged and use pilot/owner-result. Tell the human when dispatched,
then FINISH THIS TURN. Do not poll the owner. Subsequent harness notifications
automatically start another governor turn for its request and completion.
Owner brief (include this Bar line):
Bar: useful prototype evidence and truthful surviving records, not production.
Complete onboarding. Write your own justification note requesting governor
confirmation that running scripts:/safe_test.py is authorized by this directed
test, then call governor_request with its pinned ID and pilot/governor-answer.
Wait for the governor's decision. Approval here never changes bounds.
After approval, execute scripts:/safe_test.py with output_name pilot/script-output.
Write pilot/draft with body print('draft') and export its pinned body to
workspace:/smoke-draft.py. Attempt to execute workspace:/smoke-draft.py once,
with output_name pilot/denied-output; expect a refusal and continue.
Dispatch one Claude sonnet WORKER, result_name pilot/worker-result, with this brief:
Bar: evidence from the specified arithmetic and boundary test only.
Complete onboarding. Call add(19,23). Try fs_read bootstrap-harness/rules.md once;
your restricted bounds should refuse it. Return a brief report of actual results.
Give the worker these bounds exactly:
{"fs.read":["origins/**"],"fs.write":[],"fs.execute":[],
"ledger.read":["pilot/**"],"ledger.write":[],"ledger.tags":[]}
Wait for the worker with agent_status (wait_seconds up to 10); read its result
and the script output; evaluate your own task evidence and return a short report.
Do not initiate extra tests.
Governor: when the owner's confirmation request arrives, this human test prompt
already authorizes that approved script; write a decision note and resolve approve
without asking the human again. On owner completion, relay its report without
grading it. Finish this directed test with no further dispatches.
"""

def wait_for(state, predicate, seconds=600):
    deadline = time.monotonic() + seconds
    prior = None
    while time.monotonic() < deadline:
        snapshot = state.snapshot()
        if snapshot["error"]:
            raise RuntimeError(snapshot["error"])
        progress = (snapshot["status"], [(k,v["status"]) for k,v in snapshot["children"].items()],
                    [(k,v["status"]) for k,v in snapshot["requests"].items()])
        if progress != prior:
            print(json.dumps({"progress": progress}), flush=True)
            prior = progress
        if predicate(snapshot):
            return snapshot
        time.sleep(.25)
    raise TimeoutError("Hybrid smoke did not reach expected state.")

def main():
    with LedgerLog(TASK / "ledgers") as log:
        state = Conversation(log)
        thread = threading.Thread(target=worker, args=(state, TASK))
        thread.start()
        print(json.dumps({"ledger": log.name}), flush=True)
        try:
            wait_for(state, lambda s: s["status"] == "ready")
            state.submit(PROMPT)
            final = wait_for(state, lambda s: bool(s["children"]) and
                             all(c["status"] not in {"running","blocked","waiting_workers"} for c in s["children"].values())
                             and s["status"] == "ready")
            # Allow the serialized owner-finished notification to be processed.
            time.sleep(1)
            final = wait_for(state, lambda s: s["status"] == "ready")
            print(json.dumps({"children": final["children"], "requests": final["requests"],
                              "family_cost_usd": final["family_cost_usd"],
                              "actors": list(final["agent_usage"])}), flush=True)
            (TASK / (log.name + "-snapshot.json")).write_text(json.dumps(final, indent=2), encoding="utf-8")
            assert len(final["children"]) == 2, "Expected one owner and one worker."
            assert all(c["status"] == "completed" for c in final["children"].values()), "Actor failed."
            assert any(r["status"] == "resolved" for r in final["requests"].values()), "Missing governor response."
            assert set(final["agent_usage"]) == {"governor","owner-1","owner-1.worker-1"}
        finally:
            state.stop()
            thread.join(timeout=45)
            if thread.is_alive():
                log.failed = True
                raise RuntimeError("Shutdown incomplete; preserve ledger and lease.")
        if state.snapshot()["error"]:
            raise RuntimeError(state.snapshot()["error"])
    loaded = scribe.load(log.root, log.name)
    assert not loaded.findings, loaded.findings
    print(json.dumps({"closed_ledger": log.name, "findings": loaded.findings}), flush=True)

if __name__ == "__main__":
    main()
