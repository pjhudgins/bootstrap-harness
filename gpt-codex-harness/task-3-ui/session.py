"""One App Server owner thread and one conversation for each application launch."""

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import threading
import uuid

from protocol import Client, Journal, RESTRICTIONS, RpcError, SessionStopped, TASK, TOOLS, redact


class Conversation:
    def __init__(self, root=TASK):
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        self.root = root
        (root / "runs").mkdir(parents=True, exist_ok=True)
        self.log_path = root / "runs" / (self.run_id + ".jsonl")
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.commands = queue.Queue()
        self.state = {"run_id": self.run_id, "status": "starting", "thread_id": None,
                      "model": None, "messages": [], "tools": [], "usage": None,
                      "limits": {}, "error": None, "notices": [], "revision": 0}
        self.journal = Journal(self.log_path, self.observe)
        self.worker = threading.Thread(target=self.run, name="codex-conversation", daemon=True)

    def start(self):
        self.worker.start()

    def update(self, **changes):
        with self.lock:
            self.state.update(changes)
            self.state["revision"] += 1

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def submit(self, prompt):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
            raise ValueError("Enter a message between 1 and 16,000 characters.")
        with self.lock:
            if self.state["status"] != "ready":
                raise RuntimeError("The conversation is not ready for another message.")
            self.state["status"] = "busy"
            self.state["revision"] += 1
        # Log before dispatch; this also updates the UI projection. Redact recognizable
        # credential forms before they can become a retained conversation message.
        prompt = redact(prompt.strip())
        self.journal.write("user_message", {"id": uuid.uuid4().hex, "text": prompt})
        self.commands.put(prompt)

    def observe(self, kind, data):
        with self.lock:
            if kind == "user_message":
                self.state["messages"].append({**data, "role": "user", "complete": True})
            elif kind == "python_tool":
                self.state["tools"].append(data)
            elif kind in ("rate_limits", "rate_limits_before", "rate_limits_after"):
                buckets = data.get("rateLimitsByLimitId")
                if buckets:
                    self.state["limits"] = buckets
                elif data.get("rateLimits"):
                    bucket = data["rateLimits"]
                    self.state["limits"][bucket.get("limitId") or "codex"] = bucket
            elif kind == "restriction_caveat":
                self.state["notices"].append(data["note"])
            elif kind == "receive":
                method = data.get("method")
                params = data.get("params", {})
                if method == "item/agentMessage/delta":
                    message = self.agent_message(params["itemId"])
                    message["text"] += params["delta"]
                elif method == "item/completed" and params.get("item", {}).get("type") == "agentMessage":
                    item = params["item"]
                    self.agent_message(item["id"]).update(text=item["text"],
                        phase=item.get("phase"), complete=True)
                elif method == "thread/tokenUsage/updated":
                    self.state["usage"] = params["tokenUsage"]
                elif method == "account/rateLimits/updated" and params.get("rateLimits"):
                    bucket = params["rateLimits"]
                    self.state["limits"][bucket.get("limitId") or "codex"] = bucket
                else:
                    return
            else:
                return
            self.state["revision"] += 1

    def agent_message(self, item_id):
        # Called only while self.lock is held.
        for message in self.state["messages"]:
            if message["id"] == item_id:
                return message
        message = {"id": item_id, "role": "assistant", "text": "", "complete": False}
        self.state["messages"].append(message)
        return message

    def read_limits(self, client):
        # An unavailable quota endpoint should be visible without discarding a reply.
        try:
            limits = client.request("account/rateLimits/read", {})
            self.journal.write("rate_limits", limits)
        except RpcError as error:
            self.journal.write("rate_limits_unavailable", {"error": str(error)})
            with self.lock:
                self.state["notices"].append("Account limits could not be refreshed; shown values may be stale.")
                self.state["revision"] += 1

    def initialize(self, client):
        client.request("initialize", {
            "clientInfo": {"name": "nimoi_local_ui", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True}})
        client.send({"method": "initialized", "params": {}})
        account = client.request("account/read", {"refreshToken": False}).get("account")
        if not account or account.get("type") != "chatgpt":
            raise RuntimeError("Sign in with ChatGPT using codex login, then relaunch. No API-key fallback is used.")
        config = client.request("config/read", {"includeLayers": False, "cwd": str(TASK)})["config"]
        feature_states, cursor = {}, None
        while True:
            page = client.request("experimentalFeature/list", {"limit": 100, "cursor": cursor})
            feature_states.update({item["name"]: item["enabled"] for item in page["data"]})
            cursor = page.get("nextCursor")
            if not cursor:
                break
        self.journal.write("preflight", {"features": feature_states})
        for name in ("shell_tool", "js_repl", "code_mode", "multi_agent", "apps", "hooks",
                     "plugins", "browser_use", "computer_use"):
            if feature_states.get(name) is not False:
                raise RuntimeError(f"Required restriction not confirmed disabled: {name}")
        if feature_states.get("unified_exec") is not False:
            self.journal.write("restriction_caveat", {
                "note": "The runtime reports unified_exec enabled despite its override. Shell/code tools are disabled and this conversation has no environment access; this is a prototype restriction, not a proven security boundary."})
        overrides = dict(RESTRICTIONS)
        for server in config.get("mcp_servers") or {}:
            overrides[f"mcp_servers.{server}.enabled"] = False
        for plugin in config.get("plugins") or {}:
            overrides[f"plugins.{plugin}.enabled"] = False
        thread = client.request("thread/start", {
            "cwd": str(TASK), "sandbox": "read-only", "approvalPolicy": "never",
            "ephemeral": True, "environments": [], "config": overrides,
            "dynamicTools": [spec for spec, handler in TOOLS.values()],
        })
        client.thread_id = thread["thread"]["id"]
        self.update(thread_id=client.thread_id, model=thread.get("model"))
        self.read_limits(client)

    def run(self):
        client = None
        try:
            runtime = self.root / ".runtime"
            runtime.mkdir(exist_ok=True)
            env = os.environ.copy()
            env.setdefault("CODEX_HOME", str(Path.home() / ".codex"))
            env.pop("CODEX_API_KEY", None)
            env.pop("OPENAI_API_KEY", None)
            command = ["codex", "app-server", "--listen", "stdio://"]
            for key, value in {**RESTRICTIONS, "sqlite_home": str(runtime)}.items():
                command.extend(["-c", f"{key}={json.dumps(value)}"])
            self.journal.write("session_start", {"run_id": self.run_id, "command": command})
            client = Client(self.journal, command, env, self.stop_event)
            self.initialize(client)
            self.update(status="ready")
            while not self.stop_event.is_set():
                try:
                    prompt = self.commands.get_nowait()
                except queue.Empty:
                    client.receive(idle=True)
                    continue
                turn_id, replies = client.run_turn(prompt)
                self.journal.write("ui_turn_complete", {"turn_id": turn_id})
                self.read_limits(client)
                self.update(status="ready")
        except SessionStopped:
            self.journal.write("session_stop_requested", {"reason": "User ended the session."})
        except Exception as error:
            message = redact(str(error))
            self.journal.write("session_failed", {"type": type(error).__name__, "error": message})
            self.update(status="error", error=message)
        finally:
            try:
                if client:
                    client.close()
            except Exception as error:
                self.journal.write("cleanup_failed", {"error": str(error)})
                self.update(status="error", error=redact(str(error)))
            if self.snapshot()["status"] != "error":
                self.update(status="stopped")
            self.journal.write("session_end", {"status": self.snapshot()["status"]})
            self.journal.close()

    def stop(self):
        self.stop_event.set()
        with self.lock:
            if self.worker.is_alive() and self.state["status"] in ("starting", "ready", "busy"):
                self.state["status"] = "stopping"
                self.state["revision"] += 1

    def wait(self):
        self.worker.join(timeout=20)
