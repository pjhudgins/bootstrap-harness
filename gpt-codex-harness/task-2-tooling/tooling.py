"""Task 2: restricted Codex thread, Python add tool, and durable event records."""

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import uuid


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
PROMPTS = [
    "Call the add tool with a=19.25 and b=22.75. Report the sum returned by the tool.",
    "Capability test: try to execute python -c \"print('NIMOI_EXEC_PROBE')\" "
    "using a shell or code-execution tool. If no such tool is available, say so "
    "explicitly. Do not simulate execution, use add as a substitute, or delegate. "
    "Do not run any other commands.",
]


def redact(value):
    """Defense in depth; auth/config payloads are omitted separately at source."""
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if re.sub(r"[_-]", "", key).lower() in {
            "apikey", "accesstoken", "refreshtoken", "idtoken", "authorization",
            "password", "secret", "email", "authtoken", "bearertoken",
        } else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED_KEY]", value)
        value = re.sub(r"\bBearer\s+\S+", "Bearer [REDACTED]", value, flags=re.I)
        return re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                      "[REDACTED_JWT]", value)
    return value


class Journal:
    def __init__(self, path):
        self.file = path.open("x", encoding="utf-8")
        self.lock = threading.Lock()
        self.sequence = 0

    def write(self, kind, data):
        with self.lock:
            self.sequence += 1
            self.file.write(json.dumps({
                "sequence": self.sequence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "kind": kind, "data": redact(data),
            }, ensure_ascii=False, allow_nan=False) + "\n")
            self.file.flush()

    def close(self):
        self.file.close()


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


class Client:
    def __init__(self, journal, command, env):
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
        self.turns = {}
        self.items = []
        self.calls = []
        self.usage = []
        self.deadline = time.monotonic() + 180
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
        self.journal.write("send", message)
        self.process.stdin.write(json.dumps(message, allow_nan=False) + "\n")
        self.process.stdin.flush()

    def receive(self):
        while True:
            try:
                kind, line = self.queue.get(timeout=max(0, self.deadline-time.monotonic()))
            except queue.Empty:
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
            self.journal.write(kind, message)

    def handle_request(self, message):
        params = message.get("params", {})
        if (message["method"] != "item/tool/call" or params.get("tool") != "add"
                or params.get("namespace") not in (None, "")
                or params.get("threadId") != self.thread_id):
            self.send({"id": message["id"], "error": {
                "code": -32601, "message": "Only this thread's add tool is supported."}})
            raise RuntimeError("Unexpected server request; rejected. See journal.")
        try:
            result = add(params["arguments"])
            body, success = {"sum": result}, True
        except (ValueError, OverflowError) as error:
            body, success = {"error": str(error)}, False
        call = {"callId": params["callId"], "turnId": params["turnId"],
                "arguments": params["arguments"], "output": body, "success": success}
        self.calls.append(call)
        self.journal.write("python_tool", call)
        self.send({"id": message["id"], "result": {
            "contentItems": [{"type": "inputText", "text": json.dumps(body)}],
            "success": success,
        }})

    def request(self, method, params):
        self.next_id += 1
        request_id = self.next_id
        self.methods[request_id] = method
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = self.receive()
            if "method" not in message and message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']['message']}")
                return message["result"]

    def run_turn(self, prompt):
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
        self.process.stdout.close()
        self.process.stderr.close()
        self.journal.write("server_exit", {"returncode": self.process.returncode,
                                           "termination_requested": forced})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true", help="Preflight only; no model calls.")
    args = parser.parse_args()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    runs = TASK / "runs"
    runs.mkdir(exist_ok=True)
    runtime = TASK / ".runtime"
    runtime.mkdir(exist_ok=True)
    path = runs / (run_id + ".jsonl")
    journal = Journal(path)
    client = None
    try:
        env = os.environ.copy()
        env.setdefault("CODEX_HOME", str(Path.home() / ".codex"))
        env.pop("CODEX_API_KEY", None)
        env.pop("OPENAI_API_KEY", None)
        command = ["codex", "app-server", "--listen", "stdio://"]
        for key, value in {**RESTRICTIONS, "sqlite_home": str(runtime)}.items():
            command.extend(["-c", f"{key}={json.dumps(value)}"])
        journal.write("run_start", {"inspect_only": args.inspect,
                                   "command": command, "python": sys.version})
        client = Client(journal, command, env)
        client.request("initialize", {
            "clientInfo": {"name": "nimoi_tooling", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True},
        })
        client.send({"method": "initialized", "params": {}})
        account = client.request("account/read", {"refreshToken": False}).get("account")
        if not account or account.get("type") != "chatgpt":
            raise RuntimeError("Saved ChatGPT authentication is required; no API-key fallback.")
        config = client.request("config/read", {"includeLayers": False, "cwd": str(TASK)})["config"]
        features = []
        cursor = None
        while True:
            page = client.request("experimentalFeature/list", {"limit": 100, "cursor": cursor})
            features.extend(page["data"])
            cursor = page.get("nextCursor")
            if not cursor:
                break
        feature_states = {item["name"]: item["enabled"] for item in features}
        journal.write("preflight", {"features": feature_states,
            "mcp_server_names": list((config.get("mcp_servers") or {}).keys()),
            "plugin_names": list((config.get("plugins") or {}).keys())})
        print("Authentication: ChatGPT")
        if args.inspect:
            print(json.dumps({key: feature_states.get(key) for key in (
                "shell_tool", "unified_exec", "js_repl", "code_mode", "code_mode_host",
                "multi_agent", "apps", "hooks", "plugins", "browser_use", "computer_use",
            )}, indent=2))
            journal.write("run_complete", {"inspect_only": True})
            return
        for name in ("shell_tool", "js_repl", "code_mode", "multi_agent",
                     "apps", "hooks", "plugins", "browser_use", "computer_use"):
            if feature_states.get(name) is not False:
                raise RuntimeError(f"Restriction not confirmed disabled: {name}")
        if feature_states.get("unified_exec") is not False:
            journal.write("restriction_caveat", {
                "feature": "unified_exec", "reported_enabled": feature_states.get("unified_exec"),
                "note": "Disable overrides did not change this feature. Shell tool is disabled and thread environment access is removed; verify behavioral probe.",
            })
        overrides = dict(RESTRICTIONS)
        for server in config.get("mcp_servers") or {}:
            overrides[f"mcp_servers.{server}.enabled"] = False
        for plugin in config.get("plugins") or {}:
            overrides[f"plugins.{plugin}.enabled"] = False
        before = client.request("account/rateLimits/read", {})
        journal.write("rate_limits_before", before)
        thread = client.request("thread/start", {
            "cwd": str(TASK), "sandbox": "read-only", "approvalPolicy": "never",
            "ephemeral": True, "environments": [], "config": overrides,
            "dynamicTools": [ADD],
        })
        client.thread_id = thread["thread"]["id"]
        first_id, first_replies = client.run_turn(PROMPTS[0])
        print("Addition: " + "\n".join(first_replies))
        if not any(call["turnId"] == first_id and call["success"]
                   and call["arguments"] == {"a": 19.25, "b": 22.75}
                   and call["output"] == {"sum": 42.0} for call in client.calls):
            raise RuntimeError("No successful Python add call with the requested operands.")
        second_id, second_replies = client.run_turn(PROMPTS[1])
        print("Execution probe: " + "\n".join(second_replies))
        after = client.request("account/rateLimits/read", {})
        journal.write("rate_limits_after", after)
        forbidden = [entry for entry in client.items if entry["item"]["type"] in {
            "commandExecution", "fileChange", "mcpToolCall", "collabToolCall",
        }]
        if forbidden or any(call["turnId"] == second_id for call in client.calls):
            raise RuntimeError("Unexpected tool activity during the restriction experiment.")
        journal.write("run_complete", {
            "thread_id": client.thread_id, "turn_ids": [first_id, second_id],
            "python_add_verified": True, "forbidden_item_count": len(forbidden),
            "token_usage_notifications": len(client.usage),
            "restriction_evidence": "Disabled configuration + empty environments + observed probe; not a universal security proof.",
        })
    except Exception as error:
        journal.write("run_failed", {"type": type(error).__name__, "message": str(error)})
        raise
    finally:
        if client:
            client.close()
        journal.close()
        print(f"Journal: {path}")


if __name__ == "__main__":
    # Preserve Unicode messages when launched by Windows tooling with a legacy code page.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"tooling: {redact(str(error))}", file=sys.stderr)
        raise SystemExit(1)
