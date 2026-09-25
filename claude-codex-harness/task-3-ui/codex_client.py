# Copied from ../task-2-tooling/codex_client.py on 2026-09-24 (rules.md: a task's code
# lives in its folder). Changes made here belong to task 3.
"""Codex App Server stdio client with an append-only JSONL journal.

Used by the task-2 driver and meant to be reused by task 3. Python standard library only.
Protocol: JSON-RPC 2.0, one message per line, no "jsonrpc" field. Types for the installed
version: ../ref/app-server-0.155.0-alpha.9.2/ts-experimental/
"""

import collections
import datetime
import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# Values under these keys (compared ignoring case, "_" and "-") never reach the journal.
# Codex's managed login does not send credentials over this protocol; this is a guard.
# Payloads that may hold credentials under arbitrary keys (config/read) are omitted by the
# caller instead: see AppServer.request(journal_result=False).
SENSITIVE_KEYS = {"email", "accesstoken", "refreshtoken", "idtoken", "apikey",
                  "authorization", "password", "secret", "cookie"}


def _normalized(key):
    return key.lower().replace("_", "").replace("-", "")


def redact(value):
    if isinstance(value, dict):
        return {k: "<redacted>" if _normalized(k) in SENSITIVE_KEYS and v not in (None, "")
                else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


class Journal:
    """Append-only JSONL record of one run: never overwrites, flushes every record."""

    def __init__(self, directory, label):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = directory / f"{stamp}-{label}-{secrets.token_hex(3)}.jsonl"
        self._file = open(self.path, "x", encoding="utf-8")
        self._lock = threading.Lock()
        self._seq = 0

    def write(self, kind, **fields):
        with self._lock:
            if self._file.closed:
                return None
            self._seq += 1
            record = {"seq": self._seq, "t": utc_now(), "kind": kind, **redact(fields)}
            self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._file.flush()
            return record

    def close(self):
        with self._lock:
            self._file.close()


def snapshot_tree(root):
    """{relative path: mtime} for every file under root; unreadable entries are skipped."""
    files = {}
    for folder, _, names in os.walk(root, onerror=lambda error: None):
        for name in names:
            path = os.path.join(folder, name)
            try:
                files[os.path.relpath(path, root)] = os.stat(path).st_mtime
            except OSError:
                pass
    return files


def diff_snapshots(before, after, start, end):
    """Changes between two snapshots. `while_running` is true when the file's mtime falls
    inside [start, end] (the child's lifetime; other processes can write there too), and
    None for removed files, whose removal time is unknowable from a snapshot."""
    def entry(path, change):
        mtime = after.get(path)
        return {"path": path, "change": change,
                "mtime": None if mtime is None else utc_from_epoch(mtime),
                "while_running": None if mtime is None else start <= mtime <= end}
    changes = [entry(p, "new") for p in after if p not in before]
    changes += [entry(p, "changed") for p in after if p in before and after[p] != before[p]]
    changes += [entry(p, "removed") for p in before if p not in after]
    return sorted(changes, key=lambda c: c["path"])


def utc_from_epoch(seconds):
    return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).isoformat(
        timespec="milliseconds")


def find_codex(explicit=None):
    """--codex, else codex on PATH, else the Codex desktop app's extracted CLI."""
    if explicit:
        return explicit
    found = shutil.which("codex")
    if found:
        return found
    # The desktop app copies its CLI into a content-hashed folder that changes on update;
    # the original inside the WindowsApps package refuses direct launch (access denied).
    local = os.environ.get("LOCALAPPDATA")
    if local:
        copies = sorted(Path(local, "OpenAI", "Codex", "bin").glob("*/codex.exe"),
                        key=lambda p: p.stat().st_mtime)
        if copies:
            return str(copies[-1])
    raise FileNotFoundError("codex not found; pass --codex PATH")


class ProtocolError(Exception):
    pass


class Timeout(ProtocolError):
    """No message arrived before the deadline (task 3: the UI polls with short deadlines)."""


class RpcError(ProtocolError):
    def __init__(self, method, error):
        super().__init__(f"{method} failed: {json.dumps(error)}")
        self.error = error


class AppServer:
    """One `codex app-server` child process over stdio.

    Every message in either direction, and every line of the child's stderr, is journaled.
    Server-to-client requests are answered as they arrive: `handlers` maps a method to a
    function(params) -> result. Unhandled methods get a JSON-RPC "method not found" error.
    """

    def __init__(self, command, journal, env=None, cwd=None, echo_stderr=False):
        self.journal = journal
        self.echo_stderr = echo_stderr
        self.handlers = {}
        self.backlog = collections.deque()
        self._lines = queue.Queue()
        self._next_id = 0
        self._omit_results = set()
        self.stderr_lines = 0
        journal.write("spawn", command=command, cwd=str(cwd) if cwd else None)
        self.proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=cwd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._stdout_thread = threading.Thread(target=self._pump_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._pump_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _pump_stdout(self):
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _pump_stderr(self):
        for raw in self.proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            self.stderr_lines += 1
            self.journal.write("stderr", line=line)
            if self.echo_stderr:
                print(line, file=sys.stderr, flush=True)

    def send(self, message):
        self.journal.write("send", message=message)
        self.proc.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        self.proc.stdin.flush()

    def _journal_received(self, line):
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            self.journal.write("recv_unparsed", line=line.decode("utf-8", "replace"))
            return None
        if "method" not in message and message.get("id") in self._omit_results:
            self._omit_results.discard(message["id"])
            shown = {k: v for k, v in message.items() if k != "result"}
            shown["result"] = "<omitted at source: payload may hold credentials>"
            self.journal.write("recv", message=shown)
        else:
            self.journal.write("recv", message=message)
        return message

    def _read(self, deadline):
        """Next notification or response. Server requests are answered on arrival."""
        while True:
            try:
                line = self._lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                raise Timeout("timed out waiting for app-server") from None
            if line is None:
                self._lines.put(None)  # keep reporting EOF to later reads
                raise ProtocolError(f"app-server closed its stdout (exit code {self.proc.poll()})")
            message = self._journal_received(line)
            if message is None:
                continue
            if "method" in message and "id" in message:
                self._answer(message)
                continue
            return message

    def _answer(self, request):
        method = request["method"]
        handler = self.handlers.get(method)
        if handler is None:
            reply = {"id": request["id"], "error": {
                "code": -32601, "message": f"{method} is not handled by this client"}}
        else:
            try:
                reply = {"id": request["id"], "result": handler(request.get("params") or {})}
            except Exception as error:  # a handler bug must not leave the server waiting
                self.journal.write("handler_error", method=method, error=repr(error))
                reply = {"id": request["id"], "error": {
                    "code": -32603, "message": f"client handler failed: {error!r}"}}
        self.send(reply)

    def request(self, method, params=None, timeout=120, journal_result=True):
        """Send a request and wait for its response; notifications meanwhile go to backlog."""
        self._next_id += 1
        request_id = self._next_id
        if not journal_result:
            self._omit_results.add(request_id)
        message = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        deadline = time.monotonic() + timeout
        while True:
            reply = self._read(deadline)
            if "method" not in reply and reply.get("id") == request_id:
                if "error" in reply:
                    raise RpcError(method, reply["error"])
                return reply["result"]
            self.backlog.append(reply)

    def notify(self, method, params=None):
        message = {"method": method}
        if params is not None:
            message["params"] = params
        self.send(message)

    def next_notification(self, deadline):
        return self.backlog.popleft() if self.backlog else self._read(deadline)

    def close(self, timeout=10):
        """Close stdin (app-server exits on EOF), then journal everything still in flight."""
        killed = False
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            code = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            killed = True
            code = self.proc.wait(timeout=timeout)
        self._stdout_thread.join(timeout=5)
        self._stderr_thread.join(timeout=5)
        while True:
            try:
                line = self._lines.get_nowait()
            except queue.Empty:
                break
            if line is not None:
                self._journal_received(line)
        for stream in (self.proc.stdout, self.proc.stderr):
            stream.close()
        self.journal.write("exit", code=code, killed=killed)
        return code


def run_turn(server, thread_id, text, on_notification, timeout=300):
    """Start a turn and feed every notification to on_notification until this turn completes.

    Returns the completed Turn (its status may be failed or interrupted).
    """
    started = server.request("turn/start", {
        "threadId": thread_id,
        "input": [{"type": "text", "text": text, "text_elements": []}],
    })
    turn_id = started["turn"]["id"]
    deadline = time.monotonic() + timeout
    while True:
        message = server.next_notification(deadline)
        on_notification(message)
        params = message.get("params") or {}
        if message.get("method") == "turn/completed" and params.get("turn", {}).get("id") == turn_id:
            return params["turn"]
