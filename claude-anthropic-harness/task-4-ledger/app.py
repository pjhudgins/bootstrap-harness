"""task-4-ledger: the task-3 web UI, with the wiki ledger as the record.

Each launch opens a new session in one persistent ledger (founder decision, 2026-09-24):
    ledger/claude-anthropic-harness/<session stamp>.ledger
and starts a new conversation. Every record the UI shows is a ledger entry
(ledgerlog.py). The agent can read the ledger and write its own entries
(ledger_tools.py), and read files under nimoi except candidate_repos/ (policy.py).

Usage:
    python app.py                        # opens http://127.0.0.1:8765 in the browser
    python app.py --port 8800 --no-browser --budget 5 --model claude-sonnet-5

End the session with the UI's "End session" button (or POST /api/shutdown), or
Ctrl+C. Either closes the ledger session with a trailer and releases its lease.
If the process is killed instead, lease.json stays and the next launch is refused
until a human clears it (see the message printed then).

HTTP surface (extend here):
    GET  /               UI page (static/index.html)
    GET  /api/state      session snapshot
    GET  /api/events     SSE stream: `hello` {conversation}, then every record
    POST /api/send       {"text": "..."}  -> 202, or 409 with a reason
    POST /api/interrupt
    POST /api/shutdown   end the session cleanly and stop the server
"""

import argparse
import asyncio
import contextlib
import json
import sys
import webbrowser
from pathlib import Path
from typing import Any, Callable, Protocol

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from events import EventBus
from scribe_import import NIMOI_ROOT, SCRIBE_DIR, import_scribe

TASK_DIR = Path(__file__).resolve().parent
STATIC_DIR = TASK_DIR / "static"
LEDGER_ROOT = TASK_DIR / "ledger"
LEDGER_NAME = "claude-anthropic-harness"
SYSTEM_PROMPT_FILE = TASK_DIR / "system_prompt.md"
HARNESS_AUTHOR = "harness:claude-anthropic-harness/task-4-ledger"
MODEL = "claude-sonnet-5"  # same pin as tasks 1-3 (founder decision, 2026-09-23)
BUDGET_USD = 5.00  # per launch (founder decision, 2026-09-23)
MAX_TURNS = 20  # agent turns per user message; reading onboarding takes several
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
KEEPALIVE_S = 15


def agent_author(model: str) -> str:
    """The agent's ledger author: its role, model and harness. Set by the harness, never the model."""
    return f"pilot:{model}@claude-anthropic-harness"


class Session(Protocol):
    """What the web layer needs from a session; tests substitute a fake."""
    def snapshot(self) -> dict[str, Any]: ...
    def send(self, text: str) -> str | None: ...
    async def interrupt(self) -> bool: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


def create_app(session: Session, bus: EventBus, *, port: int, conversation_id: str = "test",
               on_shutdown: Callable[[], None] = lambda: None) -> Starlette:
    allowed_origins = {f"http://{h}:{port}" for h in ALLOWED_HOSTS}

    def origin_ok(request: Request) -> bool:
        origin = request.headers.get("origin")
        return origin is None or origin in allowed_origins

    async def read_json(request: Request) -> tuple[dict | None, Response | None]:
        if not origin_ok(request):
            return None, JSONResponse({"error": "origin not allowed"}, status_code=403)
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return None, JSONResponse({"error": "expected application/json"}, status_code=415)
        try:
            body = await request.json()
        except ValueError:
            return None, JSONResponse({"error": "invalid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return None, JSONResponse({"error": "expected a JSON object"}, status_code=400)
        return body, None

    async def index(request: Request) -> Response:
        return FileResponse(STATIC_DIR / "index.html")

    async def state(request: Request) -> Response:
        return JSONResponse(session.snapshot())

    async def send(request: Request) -> Response:
        body, err = await read_json(request)
        if err:
            return err
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            return JSONResponse({"error": "text must be a non-empty string"}, status_code=400)
        refusal = session.send(text)
        if refusal:
            return JSONResponse({"error": refusal, **session.snapshot()}, status_code=409)
        return JSONResponse(session.snapshot(), status_code=202)

    async def interrupt(request: Request) -> Response:
        _, err = await read_json(request)
        if err:
            return err
        return JSONResponse({"interrupted": await session.interrupt(), **session.snapshot()})

    async def shutdown(request: Request) -> Response:
        _, err = await read_json(request)
        if err:
            return err
        bus.publish("shutdown_requested", via="api")
        on_shutdown()
        return JSONResponse({"shutting_down": True}, status_code=202)

    async def events(request: Request) -> Response:
        after = request.headers.get("last-event-id") or request.query_params.get("after") or "0"
        try:
            after_seq = int(after)
        except ValueError:
            after_seq = 0
        return StreamingResponse(sse_stream(bus, after_seq, conversation_id), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        bus.bind_loop(asyncio.get_running_loop())
        await session.start()
        try:
            yield
        finally:
            bus.close_streams()
            await session.stop()

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/state", state),
            Route("/api/events", events),
            Route("/api/send", send, methods=["POST"]),
            Route("/api/interrupt", interrupt, methods=["POST"]),
            Route("/api/shutdown", shutdown, methods=["POST"]),
            Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
        ],
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)],
        lifespan=lifespan,
    )


def format_sse(record: dict[str, Any]) -> str:
    return f"id: {record['seq']}\ndata: {json.dumps(record, ensure_ascii=False)}\n\n"


async def sse_stream(bus: EventBus, after_seq: int, conversation_id: str):
    backlog, q = bus.subscribe(after_seq)
    try:
        # First frame names the conversation. A tab left open across a relaunch reconnects
        # with the old Last-Event-ID; the page sees a new id here and reloads from seq 0.
        yield f"event: hello\ndata: {json.dumps({'conversation': conversation_id})}\n\n"
        for record in backlog:
            yield format_sse(record)
        while True:
            try:
                record = await asyncio.wait_for(q.get(), KEEPALIVE_S)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if record is None:  # shutdown
                return
            yield format_sse(record)
    finally:
        bus.unsubscribe(q)


def fill_system_prompt(template: str, **values: str) -> str:
    """Replace {placeholders}; unknown braces are left alone (the prompt has none, but be safe)."""
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


def main() -> int:
    parser = argparse.ArgumentParser(description="task-4-ledger: single-conversation web UI over a wiki ledger")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--budget", type=float, default=BUDGET_USD, help="USD cap for this launch")
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS, help="agent turns per user message")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    scribe = import_scribe()
    from ledger_tools import LedgerGuard  # imported after scribe_import for a clear failure order
    from ledgerlog import LedgerLog
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
        log = LedgerLog(scribe, ledger, HARNESS_AUTHOR)
        bus = EventBus(log)
        if ledger.findings:  # e.g. `unclosed` from an earlier crash: record, never repair
            bus.publish("ledger_findings", findings=[str(f) for f in ledger.findings])
        author = agent_author(args.model)
        guard = LedgerGuard(scribe, ledger, author)
        prompt = fill_system_prompt(
            SYSTEM_PROMPT_FILE.read_text(encoding="utf-8"),
            nimoi_root=str(NIMOI_ROOT), ledger=LEDGER_NAME, session=ledger.session,
            agent_author=author, model=args.model,
        )
        facts = {"ledger": LEDGER_NAME, "ledger_session": ledger.session, "ledger_sessions": len(ledger.sessions),
                 "scribe_id": scribe.SCRIBE_ID, "ledger_version": scribe.LEDGER_VERSION,
                 "harness_author": HARNESS_AUTHOR}
        session = AgentSession(bus, guard, model=args.model, root=NIMOI_ROOT, system_prompt=prompt,
                               budget_usd=args.budget, max_turns=args.max_turns, ledger_facts=facts)

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
                print(f"task-4-ledger: {url}  ledger {LEDGER_NAME} session {ledger.session}", flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
            await task

        asyncio.run(serve())
    finally:
        try:
            ledger.close()
            print(f"ledger session {ledger.session} closed", flush=True)
        except scribe.ScribeError as e:
            print(f"ledger close failed: {e}", file=sys.stderr)
    return 0 if server is not None and server.started else 1


if __name__ == "__main__":
    sys.exit(main())
