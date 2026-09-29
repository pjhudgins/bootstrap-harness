"""A scripted live run of the three layers, recorded in the task-6 ledger (phase 3).

    python live_institution.py "your message to the governor" [--budget 5] [--wait 1800]

What it does:
  1. opens a new session in ledger/claude-anthropic-harness-t6;
  2. starts the governor (claude-opus-5-5) with governor_bounds.txt and owner_ceiling.txt, and
     Codex on ~/.codex for GPT agents;
  3. sends the message, authored by harness.DRIVER_AUTHOR: the developer agent writes it under
     the founder's authorization, and the ledger says so (a `run_driver` record);
  4. waits until the governor and every owner are idle or ended, with nothing queued;
  5. stops every owner, and records what changed under ~/.codex (founder condition);
  6. prints a summary, and closes the session.

Nobody is at the UI to approve a promotion, so the driver denies any approval card with a
note, recorded as decided by the driver, not the human. To have a human decide, use app.py.
Afterwards: python audit.py
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402

from scribe_import import import_scribe  # noqa: E402

scribe = import_scribe()

from backend_codex import live_settings, record_codex_home_changes  # noqa: E402
from codex_server import find_codex  # noqa: E402
from events import EventBus  # noqa: E402
from harness import (BUDGET_USD, CEILING_FILE, DRIVER_AUTHOR, GOVERNOR_BOUNDS_FILE, HARNESS_AUTHOR,  # noqa: E402
                     LEDGER_NAME, LEDGER_ROOT, MAX_TURNS, RUNTIME, build_institution, load_bounds)
from ledgerlog import LedgerLog  # noqa: E402
from session import AgentSession  # noqa: E402

SETTLED_CHECKS = 20  # consecutive half-second checks with nothing running


def settled(session: AgentSession) -> bool:
    owners_busy = any(r.state in ("starting", "working", "waiting") for r in session.institution.owners.values())
    return (session.state not in ("busy", "starting") and not session._human and not session._events
            and not owners_busy)


async def run(text: str, budget: float, wait_s: float) -> int:
    ledger = scribe.Scribe.open(LEDGER_ROOT, LEDGER_NAME, session_author=HARNESS_AUTHOR, create=True)
    try:
        bus = EventBus(LedgerLog(scribe, ledger, HARNESS_AUTHOR))
        bus.bind_loop(asyncio.get_running_loop())
        if ledger.findings:
            bus.publish("ledger_findings", findings=[str(f) for f in ledger.findings])
        env, governor, institution = build_institution(
            scribe, ledger, bus, governor_bounds=load_bounds(GOVERNOR_BOUNDS_FILE), ceiling=load_bounds(CEILING_FILE),
            budget_usd=budget, max_turns=MAX_TURNS, codex=live_settings(RUNTIME, find_codex()))
        bus.publish("run_driver", agent=None, driver="live_institution.py", author=DRIVER_AUTHOR,
                    note="The message is written by the developer agent (a Claude Code session) under the founder's "
                         "authorization, not typed by a human. Approval cards are denied by the driver.")
        session = AgentSession(governor, institution)
        denied: set[str] = set()
        try:
            await session.start(timeout=180)
            refused = session.send(text, author=DRIVER_AUTHOR)
            if refused:
                print(f"message refused: {refused}", file=sys.stderr)
            steady = 0
            for _ in range(int(wait_s * 2)):
                for approval in list(institution.approvals.values()):
                    if approval.state == "pending" and approval.id not in denied:
                        denied.add(approval.id)
                        session.decide(approval.id, False, "Scripted run: no human is at the UI to review a "
                                                           "promotion, so the driver denies it.",
                                       decided_by=DRIVER_AUTHOR)
                steady = steady + 1 if settled(session) else 0
                if steady >= SETTLED_CHECKS or session.state in ("stopped", "failed", "ledger_failed", "over_budget"):
                    break
                await asyncio.sleep(0.5)
            else:
                print(f"not settled after {wait_s:.0f}s; stopping", file=sys.stderr)
        finally:
            await session.stop()
            changes = record_codex_home_changes(env)
        print("=" * 72)
        for t in [r for r in bus.history if r["kind"] == "text" and r.get("agent") == "governor"]:
            who = "GOVERNOR" if t["direction"] == "from_agent" else t["author"]
            print(f"--- {who}:\n{t['text']}\n")
        print("=" * 72)
        owners = {o: {"model": r.core.model, "state": r.state, "turns": r.turns, "cost_usd": round(r.core.cost_usd, 4),
                      "tokens": r.core.tokens, "last_status": r.last_status,
                      "reply": (r.core.last_text or "")[:600]}
                  for o, r in institution.owners.items()}
        models = {r["child"]: r["model"] for r in bus.history if r["kind"] == "subagent_start"}
        subagents = [(r["child"], models.get(r["child"]), r["status"]) for r in bus.history if r["kind"] == "subagent_end"]
        print(json.dumps({
            "ledger": LEDGER_NAME, "session": ledger.session, "governor_state": session.state,
            "launch_cost_usd": round(env.budget.spent, 4), "tokens_by_agent": env.budget.tokens_by_agent(),
            "owners": owners, "subagents": subagents,
            "requests": [(q.id, q.owner, q.kind, q.state) for q in institution.requests.values()],
            "native_call_stops": [r["reason"] for r in bus.history if r["kind"] == "native_call_stop"],
            "codex_home_changes": None if changes is None else [(c["change"], c["path"], c["while_running"])
                                                                for c in changes["changes"]],
        }, indent=1, ensure_ascii=False))
        return 0 if session.state == "stopped" else 1
    finally:
        ledger.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("message")
    parser.add_argument("--budget", type=float, default=BUDGET_USD)
    parser.add_argument("--wait", type=float, default=1800, help="seconds to wait for everything to settle")
    args = parser.parse_args()
    return asyncio.run(run(args.message, args.budget, args.wait))


if __name__ == "__main__":
    sys.exit(main())
