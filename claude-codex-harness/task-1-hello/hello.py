"""Prompt a Codex agent through Codex App Server to say hello world, print its reply, exit.

Swimlane claude-codex-harness, task-1-hello. Python standard library only.
Bar: one real agent response on stdout; any failure exits nonzero with enough
context on stderr to diagnose it. Not a production client.
Protocol reference, generated from the installed binary: ../ref/app-server-*/ts/
"""

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = TASK_DIR / ".runtime"
PROMPT = "Say hello world. Reply with only the greeting and do not use any tools."
TIMEOUT_SECONDS = 180
CLIENT_INFO = {
    "name": "nimoi_claude_codex_hello",
    "title": "NIMOI bootstrap-harness claude-codex task-1-hello",
    "version": "0.1.0",
}
# Server-to-client requests answered with a refusal; any other server request gets a
# JSON-RPC error. Either way the turn continues and the request is reported on stderr.
DECLINES = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "mcpServer/elicitation/request": {"action": "decline", "content": None, "_meta": None},
}


class HarnessError(Exception):
    pass


def log(text):
    print(f"hello: {text}", file=sys.stderr, flush=True)


def redact(value):
    """Mask account email addresses in --verbose output."""
    if isinstance(value, dict):
        return {k: "<redacted>" if k == "email" and v else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def find_codex(explicit):
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
    raise HarnessError("codex not found; pass --codex PATH")


class AppServer:
    """codex app-server over stdio: JSON-RPC 2.0 messages, one per line, no "jsonrpc" field."""

    def __init__(self, command, env, verbose):
        self.verbose = verbose
        self.deadline = time.monotonic() + TIMEOUT_SECONDS
        self.next_id = 0
        self.backlog = []
        self.lines = queue.Queue()
        self.proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
            env=env, cwd=TASK_DIR, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def send(self, message):
        if self.verbose:
            print(">> " + json.dumps(redact(message)), file=sys.stderr, flush=True)
        self.proc.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        self.proc.stdin.flush()

    def _read(self):
        """Next notification or response. Server requests are answered as they arrive."""
        while True:
            try:
                line = self.lines.get(timeout=max(0.0, self.deadline - time.monotonic()))
            except queue.Empty:
                raise HarnessError(f"no result within {TIMEOUT_SECONDS} s") from None
            if line is None:
                raise HarnessError(f"app-server closed its stdout (exit code {self.proc.poll()}); "
                                   "its own stderr is above")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                log(f"ignored non-JSON stdout line: {line[:300]!r}")
                continue
            if self.verbose:
                print("<< " + json.dumps(redact(message)), file=sys.stderr, flush=True)
            if "method" in message and "id" in message:
                self._answer(message)
                continue
            return message

    def _answer(self, request):
        method = request["method"]
        detail = json.dumps(request.get("params"))[:300]
        if method in DECLINES:
            log(f"declined server request {method}: {detail}")
            self.send({"id": request["id"], "result": DECLINES[method]})
        else:
            log(f"refused unsupported server request {method}: {detail}")
            self.send({"id": request["id"], "error": {
                "code": -32601, "message": f"{method} is not supported by this hello-world client"}})

    def request(self, method, params):
        self.next_id += 1
        request_id = self.next_id
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = self._read()
            if "method" not in message and message.get("id") == request_id:
                if "error" in message:
                    raise HarnessError(f"{method} failed: {json.dumps(message['error'])}")
                return message["result"]
            self.backlog.append(message)  # notifications that raced ahead of the response

    def next_message(self):
        return self.backlog.pop(0) if self.backlog else self._read()

    def close(self):
        try:
            self.proc.stdin.close()  # EOF asks app-server to shut down
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
            log("app-server ignored stdin EOF for 10 s and was killed; its children may survive")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--codex", help="path to the codex executable")
    parser.add_argument("--codex-home",
                        help="Codex home to run against (default: $CODEX_HOME, else ~/.codex)")
    parser.add_argument("--verbose", action="store_true",
                        help="echo raw protocol messages to stderr")
    args = parser.parse_args()
    # A reply containing characters outside the Windows ANSI code page must not crash
    # the print after a successful turn.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")

    codex = find_codex(args.codex)
    env = os.environ.copy()
    if args.codex_home:
        env["CODEX_HOME"] = str(Path(args.codex_home).resolve())
    else:
        # In the gpt-codex swimlane, codex could not find its home when CODEX_HOME was unset.
        env.setdefault("CODEX_HOME", str(Path.home() / ".codex"))
    RUNTIME_DIR.mkdir(exist_ok=True)
    # Keep this client's SQLite state beside the script, apart from the desktop app's.
    command = [codex, "app-server", "--listen", "stdio://",
               "-c", f"sqlite_home={json.dumps(str(RUNTIME_DIR))}"]
    log(f"codex={codex}")
    server = AppServer(command, env, args.verbose)
    try:
        init = server.request("initialize", {"clientInfo": CLIENT_INFO, "capabilities": None})
        server.send({"method": "initialized"})
        log(f"server {init.get('userAgent')}, CODEX_HOME={init.get('codexHome')}")

        auth = server.request("account/read", {"refreshToken": False})
        account = auth.get("account") or {}
        log(f"account type={account.get('type')} plan={account.get('planType')}")
        if not account and auth.get("requiresOpenaiAuth"):
            # Not fatal: the server's own turn error is the more informative failure.
            log("warning: no saved Codex login in this CODEX_HOME; the turn will fail")

        thread = server.request("thread/start", {
            "cwd": str(TASK_DIR),
            "approvalPolicy": "untrusted",  # anything not known-safe asks first; we decline
            "approvalsReviewer": "user",  # route approvals here, never to an auto-reviewer
            "sandbox": "read-only",
            "ephemeral": True,  # no session rollout file under CODEX_HOME
        })
        thread_id = thread["thread"]["id"]
        log(f"thread {thread_id}: model={thread.get('model')} "
            f"provider={thread.get('modelProvider')} effort={thread.get('reasoningEffort')}")
        log(f"approvals={thread.get('approvalPolicy')} reviewer={thread.get('approvalsReviewer')} "
            f"sandbox={json.dumps(thread.get('sandbox'))}")
        log(f"instruction files loaded: {thread.get('instructionSources')}")

        turn_id = server.request("turn/start", {
            "threadId": thread_id,
            "input": [{"type": "text", "text": PROMPT, "text_elements": []}],
        })["turn"]["id"]
        replies = []
        while True:
            message = server.next_message()
            method, params = message.get("method"), message.get("params") or {}
            if params.get("threadId") != thread_id:
                continue
            if method == "item/completed" and params["item"].get("type") == "agentMessage":
                replies.append(params["item"])
            elif method == "error":
                log(f"error notification (willRetry={params.get('willRetry')}): "
                    f"{params.get('error', {}).get('message')}")
            elif method == "turn/completed" and params["turn"]["id"] == turn_id:
                turn = params["turn"]
                break
        if turn["status"] != "completed":
            raise HarnessError(f"turn {turn['status']}: {json.dumps(turn.get('error'))}")
        # phase is optional; when no message is marked final_answer, all of them are the reply.
        final = [r for r in replies if r.get("phase") == "final_answer"] or replies
        for r in replies:
            if r not in final:
                log(f"agent commentary, not printed: {r['text']!r}")
        text = "\n".join(r["text"] for r in final)
        if not text:
            raise HarnessError("turn completed without an agent message")
        print(text)
    finally:
        server.close()


if __name__ == "__main__":
    try:
        main()
    except (HarnessError, OSError) as error:
        log(f"FAILED: {error}")
        sys.exit(1)
