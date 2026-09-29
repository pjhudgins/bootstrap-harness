"""task-5-subagent: task 4's ledger-backed web UI, with bounded write, execute and subagents.

Each launch opens a new session in this task's own ledger (founder decision, 2026-09-25):
    ledger/claude-anthropic-harness-t5/<session stamp>.ledger
and starts a new conversation. Every record the UI shows is a ledger entry (ledgerlog.py).
The top-level agent's permissions are the bounds in bounds.txt (notation: bounds.py). The
launch is refused if they break the write/exec invariant.

Usage:
    python app.py                        # opens http://127.0.0.1:8767 in the browser
    python app.py --port 8800 --no-browser --budget 5 --model claude-sonnet-5
    (8765 is the ledger reader's default and 8766 is task 4's.)
After a session:
    python audit.py                      # checks the latest session's records (audit.py)

End the session with the UI's "End session" button (or POST /api/shutdown), or with Ctrl+C.
Either closes the ledger session with a trailer and releases its lease. If the process is
killed instead, lease.json stays and the next launch is refused until a human clears it.

Modules: web.py (HTTP), harness.py (wiring), agent.py (each agent), session.py (the
conversation), subagents.py, files.py, exec_tool.py, ledger_tools.py, ledgerlog.py,
events.py, bounds.py, prompts.py.
"""

import argparse
import asyncio
import socket
import sys
import webbrowser

import uvicorn

from bounds import BoundsError
from events import EventBus
from harness import (BOUNDS_FILE, BUDGET_USD, HARNESS_AUTHOR, LEDGER_NAME, LEDGER_ROOT, MAX_TURNS, MODEL,
                     build_harness, load_top_bounds)
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
    parser = argparse.ArgumentParser(description="task-5-subagent: web UI over a wiki ledger, bounded agents")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--budget", type=float, default=BUDGET_USD, help="USD cap for this launch")
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS, help="agent turns per user message")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    # Nothing that would make the launch pointless may leave a ledger session behind
    # (first live launch, 2026-09-25, opened a session and then failed to bind).
    if not port_free(args.port):
        print(f"Port {args.port} on 127.0.0.1 is in use; choose another with --port. No ledger session opened.",
              file=sys.stderr)
        return 2
    try:
        top_bounds = load_top_bounds()
    except (OSError, BoundsError) as e:
        print(f"{BOUNDS_FILE} is not usable: {e}. No ledger session opened.", file=sys.stderr)
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
    try:
        bus = EventBus(LedgerLog(scribe, ledger, HARNESS_AUTHOR))
        if ledger.findings:  # e.g. `unclosed` from an earlier crash: record, never repair
            bus.publish("ledger_findings", findings=[str(f) for f in ledger.findings])
        _, top = build_harness(scribe, ledger, bus, model=args.model, top_bounds=top_bounds,
                               budget_usd=args.budget, max_turns=args.max_turns)
        session = AgentSession(top)

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
                print(f"task-5-subagent: {url}  ledger {LEDGER_NAME} session {ledger.session}", flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
            await task

        asyncio.run(serve())
        if not server.started:
            bus.publish("server_failed", port=args.port,
                        detail="uvicorn did not start; its error went to stderr, not the ledger")
    finally:
        try:
            ledger.close()
            print(f"ledger session {ledger.session} closed", flush=True)
        except scribe.ScribeError as e:
            print(f"ledger close failed: {e}", file=sys.stderr)
    return 0 if server is not None and server.started else 1


if __name__ == "__main__":
    sys.exit(main())
