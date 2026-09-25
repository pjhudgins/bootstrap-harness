"""App Server transport adapted from the task-2 prototype; local to task 4."""

import json
import math
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

from privacy import redact


TASK = Path(__file__).resolve().parent
RESTRICTIONS = {
    "features.shell_tool": False,
    "features.unified_exec": False,
    "experimental_use_unified_exec_tool": False,
    "features.code_mode.enabled": False,
    "features.js_repl": False,
    "features.multi_agent": False,
    "agents.enabled": False,
    "features.apps": False,
    "features.hooks": False,
    "features.shell_snapshot": False,
    "features.skill_mcp_dependency_install": False,
    "features.computer_use": False,
    "features.browser_use": False,
    "features.browser_use_full_cdp_access": False,
    "features.browser_use_external": False,
    "features.plugins": False,
    "features.tool_suggest": False,
    "web_search": "disabled",
}
ADD = {
    "type": "function", "name": "add",
    "description": "Add two finite numbers in Python and return their sum.",
    "inputSchema": {
        "type": "object", "properties": {
            "a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"], "additionalProperties": False,
    },
}



def add(arguments):
    if not isinstance(arguments, dict) or set(arguments) != {"a", "b"}:
        raise ValueError("Expected exactly a and b.")
    for value in arguments.values():
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("a and b must be finite numbers, not booleans or strings.")
    result = arguments["a"] + arguments["b"]
    if not math.isfinite(result):
        raise ValueError("Sum must be finite.")
    return result


class SessionStopped(Exception):
    pass


class RpcError(RuntimeError):
    """An explicit server error response, distinct from a broken transport."""


# New Python tools belong here with their schema and a validating handler.
TOOLS = {"add": (ADD, lambda arguments: {"sum": add(arguments)})}


class Client:
    def __init__(self, journal, command, env, stop_event=None, tool_registry=None):
        self.tools = tool_registry or TOOLS
        self.journal = journal
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env, cwd=TASK,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.queue = queue.Queue()
        self.next_id = 0
        self.methods = {}
        self.thread_id = None
        self.human_message_id = None
        self.turns = {}
        self.items = []
        self.calls = []
        self.usage = []
        self.deadline = time.monotonic() + 180
        self.stop_event = stop_event or threading.Event()
        self.readers = [threading.Thread(target=self.read_stream, args=(stream, kind),
                                        daemon=True)
                        for stream, kind in [(self.process.stdout, "stdout"),
                                             (self.process.stderr, "stderr")]]
        for reader in self.readers:
            reader.start()

    def read_stream(self, stream, kind):
        try:
            for line in stream:
                self.queue.put((kind, line))
        finally:
            self.queue.put((kind, None))

    def send(self, message):
        self.journal.write("send", message, human_id=self.human_message_id)
        self.process.stdin.write(json.dumps(message, allow_nan=False) + "\n")
        self.process.stdin.flush()

    def receive(self, idle=False):
        while True:
            if self.stop_event.is_set():
                raise SessionStopped()
            try:
                kind, line = self.queue.get(timeout=0.2)
            except queue.Empty:
                if idle:
                    return None
                if time.monotonic() < self.deadline:
                    continue
                raise RuntimeError("App Server deadline exceeded (180 seconds).") from None
            if kind == "stderr":
                if line:
                    self.journal.write("stderr", line.rstrip())
                continue
            if line is None:
                raise RuntimeError("App Server closed stdout before completion.")
            message = json.loads(line)
            method = message.get("method")
            self.log_message(message)
            if method and "id" in message:
                self.handle_request(message)
            params = message.get("params", {})
            if method == "item/completed":
                self.items.append(params)
            if method == "turn/completed":
                self.turns[params["turn"]["id"]] = params["turn"]
            if method == "thread/tokenUsage/updated":
                self.usage.append(params)
            if method in ("item/started", "item/completed") and params.get("item", {}).get("type") in {
                    "commandExecution", "fileChange", "mcpToolCall", "collabToolCall"}:
                raise RuntimeError("Unexpected restricted tool activity; session stopped. See log.")
            return message

    def log_message(self, message, kind="receive"):
        response_to = self.methods.get(message.get("id")) if "method" not in message else None
        if response_to == "config/read":
            self.journal.write(kind, {"id": message["id"],
                "note": "Config payload omitted: may contain credentials."})
        elif response_to == "account/read" and "result" in message:
            account = message["result"].get("account") or {}
            self.journal.write("account", {key: account.get(key)
                                          for key in ("type", "planType")})
        else:
            self.journal.write(kind, message, human_id=self.human_message_id)

    def handle_request(self, message):
        params = message.get("params", {})
        if (message["method"] != "item/tool/call" or params.get("tool") not in self.tools
                or params.get("namespace") not in (None, "")
                or params.get("threadId") != self.thread_id):
            self.send({"id": message["id"], "error": {
                "code": -32601, "message": "Only this thread's registered Python tools are supported."}})
            raise RuntimeError("Unexpected server request; rejected. See journal.")
        try:
            body = self.tools[params["tool"]][1](params["arguments"])
            success = True
        except (ValueError, OverflowError) as error:
            body, success = {"error": str(error)}, False
        call = {"tool": params["tool"], "callId": params["callId"], "turnId": params["turnId"],
                "arguments": params["arguments"], "output": body, "success": success}
        self.calls.append(call)
        self.journal.write("python_tool", call)
        self.send({"id": message["id"], "result": {
            "contentItems": [{"type": "inputText", "text": json.dumps(body)}],
            "success": success,
        }})

    def request(self, method, params):
        self.deadline = time.monotonic() + 180
        self.next_id += 1
        request_id = self.next_id
        self.methods[request_id] = method
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = self.receive()
            if "method" not in message and message.get("id") == request_id:
                if "error" in message:
                    raise RpcError(f"{method}: {message['error']['message']}")
                return message["result"]

    def run_turn(self, prompt, message_id=None):
        self.human_message_id = message_id
        self.deadline = time.monotonic() + 180
        result = self.request("turn/start", {"threadId": self.thread_id,
            "input": [{"type": "text", "text": prompt}]})
        turn_id = result["turn"]["id"]
        while turn_id not in self.turns:
            self.receive()
        turn = self.turns[turn_id]
        if turn["status"] != "completed":
            raise RuntimeError(f"Turn {turn['status']}: {turn.get('error')}")
        replies = [entry["item"]["text"] for entry in self.items
                   if entry.get("turnId") == turn_id
                   and entry["item"]["type"] == "agentMessage"]
        if not replies:
            raise RuntimeError("Completed turn has no recorded agent message.")
        return turn_id, replies

    def close(self):
        forced = False
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            forced = True
            self.process.terminate()
            self.process.wait(timeout=5)
        for reader in self.readers:
            reader.join(timeout=2)
        self.process.stdout.close()
        self.process.stderr.close()
        if self.journal.failed:
            return  # The scribe is dead. Do not attempt any further appends.
        # Preserve queued shutdown messages even after the turn has finished.
        while not self.queue.empty():
            kind, line = self.queue.get_nowait()
            if line:
                if kind == "stdout":
                    try:
                        self.log_message(json.loads(line), "shutdown_receive")
                    except json.JSONDecodeError:
                        self.journal.write("shutdown_protocol_error", "Non-JSON stdout omitted.")
                else:
                    self.journal.write("shutdown_stderr", line.rstrip())
        self.journal.write("server_exit", {"returncode": self.process.returncode,
                                           "termination_requested": forced})

