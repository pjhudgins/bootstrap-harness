"""One live turn with any model, recorded in the task-6 ledger (phase 1: the first live GPT turn).

    python live_turn.py --model gpt-5.6-sol "your message"
    python live_turn.py --model claude-sonnet-5 "your message"
    python live_turn.py --model gpt-5.6-sol --fake-model "anything"   # the surface check, below

What it does:
  1. opens a new session in ledger/claude-anthropic-harness-t6;
  2. runs the top-level agent (bounds.txt) for one message;
  3. prints its reply and a summary;
  4. records what changed under CODEX_HOME (~/.codex) while it ran (founder condition, 2026-09-28:
     "Authorize, with ~/.codex diff"). Other Codex processes, such as the desktop app or other
     lanes, may write there too; each change says whether its time falls inside this run;
  5. closes the session.
Afterwards: python audit.py

--fake-model: the same run with ~/.codex's real configuration, but the model is the local fake
(tests/fake_model.py), so nothing reaches a model service. It prints what Codex offered the model
and added to its input, and fails if anything beyond the harness tools and Codex's runtime
helpers was offered. This checks, against the real configuration, what tests/test_codex.py
checks against an isolated one.
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
from harness import (BUDGET_USD, DRIVER_AUTHOR, HARNESS_AUTHOR, LEDGER_NAME, LEDGER_ROOT, MAX_TURNS,  # noqa: E402
                     RUNTIME, build_harness, load_top_bounds)
from ledgerlog import LedgerLog  # noqa: E402
from session import AgentSession  # noqa: E402


def surface(fake, top) -> dict:
    """What the fake model was offered and given, and anything unexpected."""
    from tests.fake_model import input_summary, tool_names
    bodies = [r["body"] for r in fake.requests if isinstance(r.get("body"), dict)]
    if not bodies:
        return {"error": "no model request"}
    offered = tool_names(bodies[0])
    harness = {f"functions.exec>{t.server}__{t.name}" for t in top.tool_defs} | \
        {f"{t.server}.{t.name}" for t in top.tool_defs}
    runtime = {"functions.exec", "functions.wait", "functions.request_user_input", "functions.request_user_input_async",
               "functions.exec>clock__curr_time", "request_user_input"}
    return {"offered": offered, "unexpected": sorted(set(offered) - harness - runtime),
            "harness_missing": sorted({t.full_name for t in top.tool_defs}
                                      - {f"mcp__{n.split('>')[-1].replace('.', '__')}" for n in offered}),
            "input": [(s["type"], s["role"], s["chars"], s["head"][:60]) for s in input_summary(bodies[0])]}


async def one_turn(model: str, text: str, max_turns: int, wait_s: float, fake_model: bool = False) -> int:
    ledger = scribe.Scribe.open(LEDGER_ROOT, LEDGER_NAME, session_author=HARNESS_AUTHOR, create=True)
    try:
        bus = EventBus(LedgerLog(scribe, ledger, HARNESS_AUTHOR))
        bus.bind_loop(asyncio.get_running_loop())
        if ledger.findings:
            bus.publish("ledger_findings", findings=[str(f) for f in ledger.findings])
        fake = None
        try:
            codex = live_settings(RUNTIME, find_codex())
        except FileNotFoundError:
            codex = None
        if fake_model:
            from tests.fake_model import FakeModel, message
            fake = FakeModel(lambda body: [message("surface check: no model was called")])
            codex.extra_args, codex.require_chatgpt = tuple(fake.codex_args()), False
            bus.publish("surface_check", agent=None, note="real ~/.codex configuration, local fake model")
        env, top = build_harness(scribe, ledger, bus, model=model, top_bounds=load_top_bounds(), budget_usd=BUDGET_USD,
                                 max_turns=max_turns, codex=codex)
        session = AgentSession(top)
        bus.publish("run_driver", agent=None, driver="live_turn.py", author=DRIVER_AUTHOR,
                    note="The message is written by the developer agent (a Claude Code session) under the founder's "
                         "authorization, not typed by a human; its text entry is authored accordingly.")
        try:
            await session.start(timeout=180)
            refused = session.send(text, author=DRIVER_AUTHOR)
            if refused:
                print(f"message refused: {refused}", file=sys.stderr)
            for _ in range(int(wait_s * 10)):
                if session.state != "busy":
                    break
                await asyncio.sleep(0.1)
            else:
                print(f"no reply after {wait_s:.0f}s; interrupting", file=sys.stderr)
        finally:
            await session.stop()
            changes = record_codex_home_changes(env)
            if fake is not None:
                fake.close()
        checked = surface(fake, top) if fake is not None else None
        if checked is not None:
            bus.publish("surface_check_result", agent=top.agent_id, **checked)
        usage = [r for r in bus.history if r["kind"] == "usage"]
        calls = [(r["tool_name"], r.get("policy_allow")) for r in bus.history
                 if r["kind"] == "tool_call" and r.get("phase") == "pre"]
        stops = [r["reason"] for r in bus.history if r["kind"] == "native_call_stop"]
        print("=" * 72)
        print(top.last_text or "(no reply)")
        print("=" * 72)
        print(json.dumps({
            "ledger": LEDGER_NAME, "session": ledger.session, "model": model, "state": session.state,
            "usage": {k: usage[-1].get(k) for k in ("subtype", "num_turns", "agent_tokens", "agent_cost_usd",
                                                     "duration_s", "tool_time_s")} if usage else None,
            "tool_calls": calls, "native_call_stops": stops, "surface_check": checked,
            "codex_home_changes": None if changes is None else {
                "files_before": changes["files_before"], "files_after": changes["files_after"],
                "changes": [(c["change"], c["path"], c["while_running"]) for c in changes["changes"]]},
        }, indent=1))
        if checked is not None and (checked.get("error") or checked["unexpected"] or checked["harness_missing"]):
            return 1
        return 0 if session.state == "stopped" and usage and not usage[-1].get("is_error") else 1
    finally:
        ledger.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS, help="model rounds for the message")
    parser.add_argument("--wait", type=float, default=900, help="seconds to wait for the reply")
    parser.add_argument("--fake-model", action="store_true", help="real ~/.codex configuration, fake model")
    parser.add_argument("message")
    args = parser.parse_args()
    return asyncio.run(one_turn(args.model, args.message, args.max_turns, args.wait, args.fake_model))


if __name__ == "__main__":
    sys.exit(main())
