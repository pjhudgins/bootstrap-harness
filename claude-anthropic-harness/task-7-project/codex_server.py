"""The Codex App Server, as a subprocess speaking JSON-RPC over stdio (task 6).

Adapted from claude-codex-harness/task-5-subagent/codex_client.py (2026-09-25; the peer's
file is unchanged):
  - the protocol: one JSON message per line, with no "jsonrpc" field. A message with a method
    and an id is a server request; one with only an id is a response; one with only a method
    is a notification;
  - server requests are answered as they arrive, and an unhandled method gets -32601;
  - credential redaction by key name;
  - find_codex();
  - the before/after snapshot of CODEX_HOME.
Changes here:
  - asyncio instead of threads (the harness runs on one event loop);
  - server requests are served in their own tasks, because a harness tool may wait a long
    time (a subagent, a governor request) while Codex keeps talking;
  - every message is passed to `on_message` for recording, rather than recorded here.

CodexSettings holds what one launch needs to start App Servers:
  - the binary;
  - CODEX_HOME;
  - the per-agent state folder;
  - optional extra arguments (the offline fake model).
CodexHomeWatch records what changed under CODEX_HOME during a launch. For ~/.codex this is
a founder condition (2026-09-28): "Authorize, with ~/.codex diff". It lists paths and times,
never contents.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

STREAM_LIMIT = 64 * 1024 * 1024  # one JSON message per line; raw model items can be large
API_KEY_VARS = ("OPENAI_API_KEY", "CODEX_API_KEY")  # the ChatGPT login only; no API-key fallback

# Values under these keys (compared ignoring case, "_" and "-") never reach a record. Codex's
# managed login sends no credentials over this protocol; this is a guard. config/read, which
# may hold credentials under any key, is never recorded at all (see AppServer.request).
SENSITIVE_KEYS = {"email", "accesstoken", "refreshtoken", "idtoken", "apikey", "authorization", "password",
                  "secret", "cookie", "token"}


def _normalized(key: str) -> str:
    return key.lower().replace("_", "").replace("-", "")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "<redacted>" if _normalized(str(k)) in SENSITIVE_KEYS and v not in (None, "") else redact(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def utc_iso(seconds: float | None = None) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) if seconds is None else \
        datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc)
    return moment.isoformat(timespec="milliseconds")


def find_codex(explicit: str | None = None) -> str:
    """An explicit path, else codex on PATH, else the Codex desktop app's extracted CLI."""
    if explicit:
        return explicit
    found = shutil.which("codex")
    if found:
        return found
    # The desktop app copies its CLI into a content-hashed folder that changes on update; the
    # original inside the WindowsApps package refuses direct launch (claude-codex finding).
    local = os.environ.get("LOCALAPPDATA")
    if local:
        copies = sorted(Path(local, "OpenAI", "Codex", "bin").glob("*/codex.exe"), key=lambda p: p.stat().st_mtime)
        if copies:
            return str(copies[-1])
    raise FileNotFoundError("codex not found: install it, or pass its path")


# ---- CODEX_HOME changes -------------------------------------------------------------------

def snapshot_tree(root: Path) -> dict[str, float]:
    """{relative path: mtime} for every file under root; unreadable entries are skipped."""
    files: dict[str, float] = {}
    for folder, _, names in os.walk(root, onerror=lambda error: None):
        for name in names:
            path = os.path.join(folder, name)
            try:
                files[os.path.relpath(path, root)] = os.stat(path).st_mtime
            except OSError:
                pass
    return files


def diff_snapshots(before: dict[str, float], after: dict[str, float], start: float, end: float) -> list[dict[str, Any]]:
    """Changes between two snapshots. `while_running` is true when the file's mtime falls inside
    [start, end] (other processes, such as a Codex desktop app, can write there too), and None
    for removed files, whose removal time a snapshot cannot tell."""
    def entry(path: str, change: str) -> dict[str, Any]:
        mtime = after.get(path)
        return {"path": path, "change": change, "mtime": None if mtime is None else utc_iso(mtime),
                "while_running": None if mtime is None else start <= mtime <= end}
    changes = [entry(p, "new") for p in after if p not in before]
    changes += [entry(p, "changed") for p in after if p in before and after[p] != before[p]]
    changes += [entry(p, "removed") for p in before if p not in after]
    return sorted(changes, key=lambda c: c["path"])


class CodexHomeWatch:
    """Snapshot CODEX_HOME once, before the launch's first App Server; diff it at the end."""

    def __init__(self, home: Path):
        self.home = home
        self.before: dict[str, float] | None = None
        self.started: float | None = None

    def start(self) -> None:
        if self.before is None:
            self.started = time.time()
            self.before = snapshot_tree(self.home)

    def finish(self) -> dict[str, Any] | None:
        if self.before is None:
            return None  # no App Server ran
        end = time.time()
        after = snapshot_tree(self.home)
        changes = diff_snapshots(self.before, after, self.started, end)
        return {"codex_home": str(self.home), "started": utc_iso(self.started), "finished": utc_iso(end),
                "files_before": len(self.before), "files_after": len(after), "changes": changes}


# ---- settings -------------------------------------------------------------------------------

def _run_codex(binary: str, args: list[str], env: dict[str, str], timeout: float = 60) -> str:
    result = subprocess.run([binary, *args], env=env, capture_output=True, timeout=timeout,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return result.stdout.decode("utf-8-sig", "replace")


@dataclass
class CodexSettings:
    binary: str
    home: Path  # CODEX_HOME: ~/.codex for live runs, an isolated folder for offline tests
    state_dir: Path  # per-agent App Server state (sqlite_home) goes under here
    extra_args: tuple[str, ...] = ()  # e.g. the fake model provider for offline tests
    require_chatgpt: bool = True  # live: refuse unless account/read says the ChatGPT login
    turn_timeout_s: float = 900.0  # per turn, not counting time spent inside harness tools
    watch: CodexHomeWatch = field(init=False)
    _catalogue: dict[str, dict[str, Any]] | None = field(default=None, init=False)
    _version: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.watch = CodexHomeWatch(self.home)

    @property
    def live_home(self) -> bool:
        return self.home.resolve() == (Path.home() / ".codex").resolve()

    def env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in API_KEY_VARS}
        env["CODEX_HOME"] = str(self.home)
        return env

    def version(self) -> str:
        if self._version is None:
            self._version = _run_codex(self.binary, ["--version"], self.env(), timeout=20).strip()
        return self._version

    def catalogue(self) -> dict[str, dict[str, Any]]:
        """Tool-relevant model metadata from `codex debug models` (never the instruction payloads,
        which that listing also carries): tool_mode, multi_agent_version, shell_type, visibility."""
        if self._catalogue is None:
            self.watch.start()  # the listing may refresh a cache under CODEX_HOME
            try:
                models = json.loads(_run_codex(self.binary, ["debug", "models"], self.env()))["models"]
                self._catalogue = {m["slug"]: {k: m.get(k) for k in ("tool_mode", "multi_agent_version",
                                                                     "shell_type", "visibility")}
                                   for m in models}
            except (OSError, subprocess.SubprocessError, ValueError, KeyError):
                self._catalogue = {}
        return self._catalogue

    def command(self, sqlite_home: Path, overrides: dict[str, Any]) -> list[str]:
        cmd = [self.binary, "app-server", "--listen", "stdio://"]
        for key, value in {**overrides, "sqlite_home": str(sqlite_home)}.items():
            cmd += ["-c", f"{key}={json.dumps(value)}"]
        return cmd + list(self.extra_args)


# ---- the connection -------------------------------------------------------------------------

class RpcError(Exception):
    """An error response from the App Server."""

    def __init__(self, method: str, error: Any):
        super().__init__(f"{method} failed: {json.dumps(error)[:2000]}")
        self.error = error


class ServerClosed(Exception):
    """The App Server's stdout ended."""


class ReplyError(Exception):
    """Raised by a request handler to answer a server request with a JSON-RPC error."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


OMITTED = "<omitted at source: this payload may hold credentials>"

OnMessage = Callable[[str, dict[str, Any]], None]  # (direction "send"/"recv", message)
OnRequest = Callable[[dict[str, Any]], Awaitable[Any]]  # server request -> result, or raise ReplyError
OnNotification = Callable[[dict[str, Any]], None]


class AppServer:
    """One `codex app-server` over stdio.

    `on_message` sees every message both ways (the caller records them). A message sent as a
    request with record_result=False has its result replaced by OMITTED before on_message sees
    it. `on_request` answers server requests, each in its own task; a raised ReplyError becomes
    a JSON-RPC error, and any other exception becomes -32603 so the server is never left
    waiting. `on_notification` sees notifications, after on_message.
    """

    def __init__(self, *, on_message: OnMessage, on_request: OnRequest, on_notification: OnNotification,
                 on_stderr: Callable[[str], None], on_error: Callable[[str, BaseException], None]):
        self.on_message = on_message
        self.on_request = on_request
        self.on_notification = on_notification
        self.on_stderr = on_stderr
        self.on_error = on_error
        self.proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, tuple[str, asyncio.Future]] = {}
        self._omit_results: set[int] = set()
        self._write_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._readers: list[asyncio.Task] = []
        self.closed = asyncio.Event()

    async def start(self, command: list[str], *, env: dict[str, str], cwd: Path) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env, cwd=str(cwd), limit=STREAM_LIMIT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._readers = [asyncio.create_task(self._read_stdout(), name="codex-stdout"),
                         asyncio.create_task(self._read_stderr(), name="codex-stderr")]

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    # ---- reading ----------------------------------------------------------------------------

    async def _read_stdout(self) -> None:
        try:
            while line := await self.proc.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self.on_message("recv", {"unparsed": line.decode("utf-8", "replace")[:20000]})
                    continue
                self._dispatch(message)
        except Exception as e:  # e.g. a line over STREAM_LIMIT: the connection is unusable
            self.on_error("stdout", e)
        finally:
            self.closed.set()
            for method, future in self._pending.values():
                if not future.done():
                    future.set_exception(ServerClosed(f"app-server closed before answering {method}"))

    async def _read_stderr(self) -> None:
        try:
            while line := await self.proc.stderr.readline():
                self.on_stderr(line.decode("utf-8", "replace").rstrip("\r\n"))
        except Exception as e:
            self.on_error("stderr", e)

    def _dispatch(self, message: dict[str, Any]) -> None:
        method, msg_id = message.get("method"), message.get("id")
        if method is None and msg_id in self._omit_results:
            self._omit_results.discard(msg_id)
            shown = {k: v for k, v in message.items() if k != "result"}
            self.on_message("recv", {**shown, "result": OMITTED} if "result" in message else shown)
        else:
            self.on_message("recv", message)
        if method is not None and msg_id is not None:
            task = asyncio.create_task(self._answer(message), name=f"codex-request-{method}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif method is not None:
            try:
                self.on_notification(message)
            except Exception as e:  # a recording bug must not stop the reader
                self.on_error(f"notification {method}", e)
        elif msg_id in self._pending:
            _, future = self._pending.pop(msg_id)
            if not future.done():
                future.set_result(message)

    async def _answer(self, request: dict[str, Any]) -> None:
        try:
            reply = {"id": request["id"], "result": await self.on_request(request)}
        except ReplyError as e:
            reply = {"id": request["id"], "error": {"code": e.code, "message": str(e)}}
        except asyncio.CancelledError:
            reply = {"id": request["id"], "error": {"code": -32603, "message": "cancelled: the agent is stopping"}}
        except Exception as e:  # a handler bug must not leave the server waiting
            self.on_error(f"request {request.get('method')}", e)
            reply = {"id": request["id"], "error": {"code": -32603, "message": f"client handler failed: {e!r}"}}
        try:
            await self.send(reply)
        except (OSError, RuntimeError, ConnectionError):
            pass  # the server is gone; nothing is waiting for this answer

    # ---- writing ----------------------------------------------------------------------------

    async def send(self, message: dict[str, Any]) -> None:
        if self.proc is None or self.proc.stdin is None or self.proc.stdin.is_closing():
            raise ConnectionError("app-server stdin is closed")
        data = json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        async with self._write_lock:
            self.on_message("send", message)
            self.proc.stdin.write(data)
            await self.proc.stdin.drain()

    async def request(self, method: str, params: Any = None, *, timeout: float = 120,
                      record_result: bool = True) -> Any:
        """Send a request and wait for its response. Raises RpcError on an error response."""
        if self.closed.is_set():
            raise ServerClosed(f"app-server is closed; cannot send {method}")
        self._next_id += 1
        msg_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = (method, future)
        if not record_result:
            self._omit_results.add(msg_id)
        message: dict[str, Any] = {"id": msg_id, "method": method}
        if params is not None:
            message["params"] = params
        try:
            await self.send(message)
            reply = await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(msg_id, None)
        if "error" in reply:
            raise RpcError(method, reply["error"])
        return reply.get("result")

    async def notify(self, method: str, params: Any = None) -> None:
        message: dict[str, Any] = {"method": method}
        if params is not None:
            message["params"] = params
        await self.send(message)

    # ---- closing ----------------------------------------------------------------------------

    async def close(self, timeout: float = 10) -> dict[str, Any]:
        """Close stdin (the App Server exits on EOF), wait, kill if needed; cancel open handlers."""
        if self.proc is None:
            return {"code": None, "killed": False}
        killed = False
        try:
            if self.proc.stdin and not self.proc.stdin.is_closing():
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            code = await asyncio.wait_for(self.proc.wait(), timeout)
        except asyncio.TimeoutError:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
            killed = True
            code = await self.proc.wait()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, *self._readers, return_exceptions=True)
        return {"code": code, "killed": killed}
