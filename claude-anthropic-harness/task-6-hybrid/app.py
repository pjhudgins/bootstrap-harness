"""task-6-hybrid: the governor's web UI over the task's wiki ledger.

Each launch opens a new session in this task's own ledger (founder decision, 2026-09-25):
    ledger/claude-anthropic-harness-t6/<session stamp>.ledger
and starts a new conversation. Every record the UI shows is a ledger entry (ledgerlog.py).

By default the human chats with the governor (claude-opus-5-5, rules.md 6c), which dispatches task
owners (Claude or GPT) that may spawn subagents. The UI shows every agent's activity, and asks
the human to approve what only the human may approve (script promotions). Bounds are
human-edited files (notation: bounds.py):
    governor_bounds.txt   the governor's
    owner_ceiling.txt     the most the governor may grant a task owner
A launch is refused if either breaks the write/exec invariant. With --pilot, the launch is
instead task 5's single agent, with bounds.txt.

Usage:
    python app.py                        # opens http://127.0.0.1:8768 in the browser
    python app.py --budget 5 --no-browser --port 8800
    python app.py --pilot --model claude-sonnet-5      # task 5's single agent
    (8765 is the ledger reader's default, 8766 is task 4's and 8767 is task 5's.)
After a session:
    python audit.py                      # checks the latest session's records (audit.py)

End the session with the UI's "End session" button (or POST /api/shutdown), or with Ctrl+C.
Either:
  - stops every task owner;
  - records what changed under ~/.codex, if Codex ran;
  - closes the ledger session with a trailer and releases its lease.
If the process is killed instead, lease.json stays and the next launch is refused until a human
clears it.

Modules: web.py (HTTP), harness.py (wiring), governance.py (governor and owners), agent.py
(each agent), backend_claude.py and backend_codex.py (+ codex_server.py), session.py (the
conversation), subagents.py, files.py, exec_tool.py, ledger_tools.py, ledgerlog.py, events.py,
bounds.py, prompts.py, tools.py.
"""

import argparse
import asyncio
import socket
import sys
import webbrowser

import uvicorn

from backend_codex import live_settings, record_codex_home_changes
from bounds import BoundsError
from codex_server import find_codex
from events import EventBus
from harness import (BOUNDS_FILE, BUDGET_USD, CEILING_FILE, GOVERNOR_BOUNDS_FILE, HARNESS_AUTHOR, LEDGER_NAME,
                     LEDGER_ROOT, MAX_TURNS, MODEL, RUNTIME, build_harness, build_institution, load_bounds)
from scribe_import import SCRIBE_DIR, import_scribe
from web import create_app


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="task-6-hybrid: the governor's web UI over a wiki ledger")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--budget", type=float, default=BUDGET_USD, help="USD cap for this launch (Claude agents)")
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS,
                        help="model rounds per message for the governor (or pilot) and subagents")
    parser.add_argument("--pilot", action="store_true", help="task 5's single agent instead of the governor")
    parser.add_argument("--model", default=MODEL, help="with --pilot: the pilot's model")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-codex", action="store_true", help="no GPT agents (Codex is not started)")
    args = parser.parse_args()

    # Nothing that would make the launch pointless may leave a ledger session behind
    # (first live launch, 2026-09-25, opened a session and then failed to bind).
    if not port_free(args.port):
        print(f"Port {args.port} on 127.0.0.1 is in use; choose another with --port. No ledger session opened.",
              file=sys.stderr)
        return 2
    try:
        files = [BOUNDS_FILE] if args.pilot else [GOVERNOR_BOUNDS_FILE, CEILING_FILE]
        bounds = [load_bounds(f) for f in files]
    except (OSError, BoundsError) as e:
        print(f"Bounds are not usable: {e}. No ledger session opened.", file=sys.stderr)
        return 2

    scribe = import_scribe()
    from ledgerlog import LedgerLog  # after scribe_import, for a clear failure order
    from session import AgentSession

    try:
        ledger = scribe.Scribe.open(LEDGER_ROOT, LEDGER_NAME, session_author=HARNESS_AUTHOR, create=True)
    except scribe.OpenRefused as e:
        print(f"Could not open ledger {LEDGER_ROOT / LEDGER_NAME}: {e}", file=sys.stderr)
        if e.code == "lease_held":
            print("A previous run did not close cleanly, or one is still running. A human:\n"
                  "  1. checks that the pid/host in lease.json are gone;\n"
                  f"  2. runs: python {SCRIBE_DIR / 'scribe.py'} check {LEDGER_ROOT} {LEDGER_NAME}\n"
                  "  3. deletes lease.json.", file=sys.stderr)
        return 2

    server: uvicorn.Server | None = None
    env = None
    try:
        bus = EventBus(LedgerLog(scribe, ledger, HARNESS_AUTHOR))
        if ledger.findings:  # e.g. `unclosed` from an earlier crash: record, never repair
            bus.publish("ledger_findings", findings=[str(f) for f in ledger.findings])
        codex = None
        if not args.no_codex:  # GPT agents use ~/.codex (founder: "Authorize, with ~/.codex diff")
            try:
                codex = live_settings(RUNTIME, find_codex())
            except FileNotFoundError:
                bus.publish("codex_unavailable", agent=None, detail="codex not found: GPT agents cannot run")
        if args.pilot:
            env, top = build_harness(scribe, ledger, bus, model=args.model, top_bounds=bounds[0],
                                     budget_usd=args.budget, max_turns=args.max_turns, codex=codex)
            session = AgentSession(top)
        else:
            env, top, institution = build_institution(scribe, ledger, bus, governor_bounds=bounds[0],
                                                      ceiling=bounds[1], budget_usd=args.budget,
                                                      max_turns=args.max_turns, codex=codex)
            session = AgentSession(top, institution)

        def request_exit() -> None:
            if server is not None:
                server.should_exit = True

        app = create_app(session, bus, port=args.port, conversation_id=ledger.session, on_shutdown=request_exit)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port,
                                               log_level="warning", timeout_graceful_shutdown=3))
        url = f"http://127.0.0.1:{args.port}/"

        async def serve() -> None:
            task = asyncio.create_task(server.serve())
            while not server.started and not task.done():
                await asyncio.sleep(0.1)
            if server.started:
                print(f"task-6-hybrid ({'pilot' if args.pilot else 'governor'}): {url}  ledger {LEDGER_NAME} "
                      f"session {ledger.session}", flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
            await task

        asyncio.run(serve())
        if not server.started:
            bus.publish("server_failed", port=args.port,
                        detail="uvicorn did not start; its error went to stderr, not the ledger")
    finally:
        if env is not None:
            record_codex_home_changes(env)  # only if a Codex App Server ran this launch
        try:
            ledger.close()
            print(f"ledger session {ledger.session} closed", flush=True)
        except scribe.ScribeError as e:
            print(f"ledger close failed: {e}", file=sys.stderr)
    return 0 if server is not None and server.started else 1


if __name__ == "__main__":
    sys.exit(main())
