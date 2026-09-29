"""The local web UI's HTTP layer (split out of app.py, 2026-09-25).

    GET  /               UI page (static/index.html, asset URLs versioned per launch)
    GET  /api/state      session snapshot
    GET  /api/events     SSE stream: `hello` {conversation}, then every record (replays history,
                         honours Last-Event-ID / ?after=)
    POST /api/send       {"text": "..."}  -> 202, or 409 with a reason (task 6: queued while a turn runs)
    POST /api/approve    {"approval": "approval.1", "decision": "approve"|"deny", "note": "..."}: the human's
                         decision on an approval card (task 6) -> 200, or 409 with a reason
    POST /api/interrupt
    POST /api/shutdown   end the session cleanly and stop the server

Only the local user can drive it:
  - the server binds 127.0.0.1;
  - TrustedHostMiddleware refuses foreign Host headers (DNS rebinding);
  - POSTs must be JSON from a same-origin page (cross-site form posts);
  - a POST's body is read BEFORE it is refused. On Windows, refusing an unread body resets
    the connection instead of delivering the error (found by two peer lanes).
Every page response carries Cache-Control: no-cache (a stale app.js once drew new records
with old code) and a Content-Security-Policy allowing only this origin.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any, Callable, Protocol

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from events import EventBus

STATIC_DIR = Path(__file__).resolve().parent / "static"
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
KEEPALIVE_S = 15
PAGE_HEADERS = [
    (b"cache-control", b"no-cache"),
    (b"content-security-policy",
     b"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
     b"frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
    (b"x-content-type-options", b"nosniff"),
]


class Session(Protocol):
    """What the web layer needs from a session; tests substitute a fake."""
    def snapshot(self) -> dict[str, Any]: ...
    def send(self, text: str) -> str | None: ...
    def decide(self, approval: str, approve: bool, note: str) -> str | None: ...
    async def interrupt(self) -> bool: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class PageHeaders:
    """ASGI middleware: the headers above on every non-API response."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        names = {k for k, _ in PAGE_HEADERS}

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() not in names]
                message = {**message, "headers": headers + PAGE_HEADERS}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_app(session: Session, bus: EventBus, *, port: int, conversation_id: str = "test",
               on_shutdown: Callable[[], None] = lambda: None) -> Starlette:
    allowed_origins = {f"http://{h}:{port}" for h in ALLOWED_HOSTS}

    async def read_json(request: Request) -> tuple[dict | None, Response | None]:
        raw = await request.body()  # read first: see the module docstring
        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed_origins:
            return None, JSONResponse({"error": "origin not allowed"}, status_code=403)
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return None, JSONResponse({"error": "expected application/json"}, status_code=415)
        try:
            body = json.loads(raw or b"null")
        except ValueError:
            return None, JSONResponse({"error": "invalid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return None, JSONResponse({"error": "expected a JSON object"}, status_code=400)
        return body, None

    async def index(request: Request) -> Response:
        # Version the asset URLs per launch, so a copy cached before no-cache existed is bypassed too.
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        for asset in ("/static/app.js", "/static/style.css"):
            html = html.replace(f'"{asset}"', f'"{asset}?v={conversation_id}"')
        return HTMLResponse(html)

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

    async def approve(request: Request) -> Response:
        body, err = await read_json(request)
        if err:
            return err
        approval, decision, note = body.get("approval"), body.get("decision"), body.get("note", "")
        if not isinstance(approval, str) or decision not in ("approve", "deny") or not isinstance(note, str):
            return JSONResponse({"error": "expected approval, decision (approve or deny) and an optional note"},
                                status_code=400)
        refusal = session.decide(approval, decision == "approve", note)
        if refusal:
            return JSONResponse({"error": refusal, **session.snapshot()}, status_code=409)
        return JSONResponse(session.snapshot())

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
        # End open event streams now; otherwise uvicorn waits out timeout_graceful_shutdown on them
        # (live 2026-09-25). Records written after this point are in the ledger but not on the page.
        bus.close_streams()
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
            Route("/api/approve", approve, methods=["POST"]),
            Route("/api/interrupt", interrupt, methods=["POST"]),
            Route("/api/shutdown", shutdown, methods=["POST"]),
            Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
        ],
        middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS), Middleware(PageHeaders)],
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
