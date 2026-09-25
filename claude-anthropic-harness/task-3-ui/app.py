"""task-3-ui: local web UI for a single conversation with a Claude agent.

Each launch starts a new conversation and a new log: runs/conv-<UTC>.jsonl.log (gitignored).
Everything the UI shows comes from the same records the log holds (events.py).

Usage:
    python app.py                        # opens http://127.0.0.1:8765 in the browser
    python app.py --port 8800 --no-browser --budget 5 --model claude-sonnet-5

Stop with Ctrl+C. The server binds 127.0.0.1 only; Host and Origin are checked
so other sites open in the same browser cannot drive it.

HTTP surface (small on purpose; extend here):
    GET  /               UI page (static/index.html)
    GET  /api/state      session snapshot
    GET  /api/events     SSE stream of every record; replays history, honours Last-Event-ID / ?after=
    POST /api/send       {"text": "..."}  -> 202, or 409 with a reason
    POST /api/interrupt
"""

import argparse
import asyncio
import contextlib
import datetime
import json
import sys
import webbrowser
from pathlib import Path
from typing import Any, Protocol

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from events import EventBus
from runlog import RunLog

TASK_DIR = Path(__file__).resolve().parent
STATIC_DIR = TASK_DIR / "static"
WORKSPACE = TASK_DIR / "workspace"
MODEL = "claude-sonnet-5"  # same pin as tasks 1-2 (founder decision, 2026-09-23)
BUDGET_USD = 5.00  # per launch (founder decision, 2026-09-23)
MAX_TURNS = 10  # per user message
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
KEEPALIVE_S = 15


class Session(Protocol):
    """What the web layer needs from a session; tests substitute a fake."""
    def snapshot(self) -> dict[str, Any]: ...
    def send(self, text: str) -> str | None: ...
    async def interrupt(self) -> bool: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


def create_app(session: Session, bus: EventBus, *, port: int, conversation_id: str = "test") -> Starlette:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="task-3-ui: single-conversation web UI")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--budget", type=float, default=BUDGET_USD, help="USD cap for this launch")
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS, help="agent turns per user message")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    from session import AgentSession  # imported here so tests can import app without the SDK session

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log = RunLog(TASK_DIR / "runs" / f"conv-{stamp}.jsonl.log")
    bus = EventBus(log)
    session = AgentSession(bus, model=args.model, workspace=WORKSPACE,
                           budget_usd=args.budget, max_turns=args.max_turns)
    app = create_app(session, bus, port=args.port, conversation_id=stamp)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port,
                                           log_level="warning", timeout_graceful_shutdown=3))
    url = f"http://127.0.0.1:{args.port}/"

    async def serve() -> None:
        task = asyncio.create_task(server.serve())
        while not server.started and not task.done():
            await asyncio.sleep(0.1)
        if server.started:
            print(f"task-3-ui: {url}  (log: {log.path})  Ctrl+C to stop", flush=True)
            if not args.no_browser:
                webbrowser.open(url)
        await task

    try:
        asyncio.run(serve())
    finally:
        log.close()
    return 0 if server.started else 1


if __name__ == "__main__":
    sys.exit(main())
