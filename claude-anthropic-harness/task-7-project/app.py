"""task-7-project: one project's server, the governor's web UI over that project's ledger (task 6's app).

launch.py normally starts one of these per configured project (rules 7a, 7b). It can also run alone:
    python app.py                          # the only project in projects.toml
    python app.py --project default        # one project of several
    python app.py --config other.toml --project x --no-browser
    python app.py --pilot                  # task 5's single agent instead of the governor

The project (project.py) decides everything project-specific:
  - its port;
  - its read root ("/" of the bounds notation);
  - the folder the agents write in;
  - its fixed onboarding;
  - its ledger, in <project>/ledger/<ledger name>/, where each launch opens a new session.
If the ledger directory is missing, the human is asked before it is created (rules 7d); without it
nothing starts. Nothing that would make the launch pointless may leave a ledger session behind: the
config, the port and the ledger directory are all checked before the ledger opens.

End the session with the UI's "End session" button (or POST /api/shutdown), or with Ctrl+C. Either:
  - stops every task owner;
  - records what changed under ~/.codex, if Codex ran;
  - closes the ledger session.
A killed process leaves lease.json, and the next launch is refused until a human clears it.
After a session: python audit.py --project <name>
"""

import sys

sys.dont_write_bytecode = True  # before the harness modules are imported

import argparse  # noqa: E402
import asyncio  # noqa: E402
import socket  # noqa: E402
import webbrowser  # noqa: E402

import uvicorn  # noqa: E402

from backend_codex import live_settings, record_codex_home_changes  # noqa: E402
from codex_server import find_codex  # noqa: E402
from events import EventBus  # noqa: E402
from harness import HARNESS_AUTHOR, MAX_TURNS, MODEL, build_harness, build_institution, codex_state_dir  # noqa: E402
from project import CONFIG_FILE, ConfigError, ensure_ledger_dir, load_config, select  # noqa: E402
from scribe_import import SCRIBE_DIR, import_scribe  # noqa: E402
from web import create_app  # noqa: E402


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="task-7-project: one project's governor UI over its ledger")
    parser.add_argument("--config", default=str(CONFIG_FILE), help="the projects file (default: projects.toml)")
    parser.add_argument("--project", help="which project (default: the only one in the config)")
    parser.add_argument("--port", type=int, help="override the project's port")
    parser.add_argument("--budget", type=float, help="override the project's launch budget (USD, Claude agents)")
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS,
                        help="model rounds per message for the governor (or pilot) and subagents")
    parser.add_argument("--pilot", action="store_true", help="task 5's single agent instead of the governor")
    parser.add_argument("--model", default=MODEL, help="with --pilot: the pilot's model")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-codex", action="store_true", help="no GPT agents (Codex is not started)")
    args = parser.parse_args()

    try:
        project = select(load_config(args.config), args.project)
    except ConfigError as e:
        print(f"The config is not usable: {e}\nNo ledger session opened.", file=sys.stderr)
        return 2
    port = args.port or project.port
    if not port_free(port):
        print(f"[{project.name}] port {port} on 127.0.0.1 is in use. No ledger session opened.", file=sys.stderr)
        return 2
    if not ensure_ledger_dir(project):
        print(f"[{project.name}] {project.ledger_dir} does not exist and was not created. No ledger session opened.",
              file=sys.stderr)
        return 2

    scribe = import_scribe()
    from ledgerlog import LedgerLog  # after scribe_import, for a clear failure order
    from session import AgentSession

    try:
        ledger = scribe.Scribe.open(project.ledger_dir, project.ledger_name, session_author=HARNESS_AUTHOR, create=True)
    except scribe.OpenRefused as e:
        print(f"[{project.name}] could not open ledger {project.ledger_dir / project.ledger_name}: {e}", file=sys.stderr)
        if e.code == "lease_held":
            print("A previous run did not close cleanly, or one is still running. A human:\n"
                  "  1. checks that the pid/host in lease.json are gone;\n"
                  f"  2. runs: python {SCRIBE_DIR / 'scribe.py'} check {project.ledger_dir} {project.ledger_name}\n"
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
                codex = live_settings(codex_state_dir(project), find_codex())
            except FileNotFoundError:
                bus.publish("codex_unavailable", agent=None, detail="codex not found: GPT agents cannot run")
        if args.pilot:
            env, top = build_harness(scribe, ledger, bus, project=project, model=args.model,
                                     max_turns=args.max_turns, budget_usd=args.budget, codex=codex)
            session = AgentSession(top)
        else:
            env, top, institution = build_institution(scribe, ledger, bus, project=project, max_turns=args.max_turns,
                                                      budget_usd=args.budget, codex=codex)
            session = AgentSession(top, institution)

        def request_exit() -> None:
            if server is not None:
                server.should_exit = True

        app = create_app(session, bus, port=port, conversation_id=ledger.session, on_shutdown=request_exit)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                               log_level="warning", timeout_graceful_shutdown=3))
        url = f"http://127.0.0.1:{port}/"

        async def serve() -> None:
            task = asyncio.create_task(server.serve())
            while not server.started and not task.done():
                await asyncio.sleep(0.1)
            if server.started:
                print(f"[{project.name}] {'pilot' if args.pilot else 'governor'} at {url}  ledger "
                      f"{project.ledger_dir / project.ledger_name} session {ledger.session}", flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
            await task

        asyncio.run(serve())
        if not server.started:
            bus.publish("server_failed", port=port,
                        detail="uvicorn did not start; its error went to stderr, not the ledger")
    finally:
        if env is not None:
            record_codex_home_changes(env)  # only if a Codex App Server ran this launch
        try:
            ledger.close()
            print(f"[{project.name}] ledger session {ledger.session} closed", flush=True)
        except scribe.ScribeError as e:
            print(f"[{project.name}] ledger close failed: {e}", file=sys.stderr)
    return 0 if server is not None and server.started else 1


if __name__ == "__main__":
    sys.exit(main())
