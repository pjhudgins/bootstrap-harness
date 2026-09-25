"""One App Server owner thread and one conversation for each application launch."""

import copy
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import queue
import threading
import uuid

from protocol import Client, RESTRICTIONS, RpcError, SessionStopped, TASK, redact
from ledger_store import AGENT_AUTHOR, HARNESS_AUTHOR, HUMAN_AUTHOR, LedgerJournal, SCRIBE_PATH, scribe
from agent_tools import registry


class Conversation:
    def __init__(self, root=TASK):
        self.run_id = "chat-" + datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz") + "-" + uuid.uuid4().hex[:8]
        self.root = root
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.commands = queue.Queue()
        self.state = {"run_id": self.run_id, "status": "starting", "thread_id": None,
                      "model": None, "messages": [], "tools": [], "usage": None,
                      "limits": {}, "error": None, "notices": [], "revision": 0}
        self.journal = LedgerJournal(root / "ledgers", self.run_id, self.observe)
        self.log_path = self.journal.path
        self.tools = registry(self.journal)
        self.state["ledger"] = {"name": self.run_id, "path": str(self.log_path),
                                "agent_author": AGENT_AUTHOR, "harness_author": HARNESS_AUTHOR,
                                "human_author": HUMAN_AUTHOR}
        self.state["tool_names"] = list(self.tools)
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
        message_id = uuid.uuid4().hex
        try:
            self.journal.write("user_message", {"id": message_id, "text": prompt})
        except Exception as error:
            self.update(status="error", error=redact(str(error)))
            self.stop_event.set()
            raise RuntimeError("Message was not dispatched: ledger recording failed.") from error
        self.commands.put((message_id, prompt))

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
            "clientInfo": {"name": "nimoi_ledger_ui", "version": "0.1.0"},
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
            "baseInstructions": (TASK / "system_prompt.md").read_text(encoding="utf-8"),
            "developerInstructions": "Follow the test-pilot system instructions. Do not initiate tests or perform filesystem writes or code execution. Ledger writes are allowed through the supplied tool.",
            "dynamicTools": [spec for spec, handler in self.tools.values()],
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
            self.journal.write("session_start", {"run_id": self.run_id, "command": command,
                "scribe": scribe.SCRIBE_ID, "ledger_version": scribe.LEDGER_VERSION,
                "scribe_sha256": hashlib.sha256(SCRIBE_PATH.read_bytes()).hexdigest(),
                "agent_author": AGENT_AUTHOR, "harness_author": HARNESS_AUTHOR,
                "human_author": HUMAN_AUTHOR, "message_storage": "authored-text-links-v1"})
            client = Client(self.journal, command, env, self.stop_event, self.tools)
            self.initialize(client)
            self.update(status="ready")
            while not self.stop_event.is_set():
                try:
                    message_id, prompt = self.commands.get_nowait()
                except queue.Empty:
                    client.receive(idle=True)
                    continue
                turn_id, replies = client.run_turn(prompt, message_id)
                self.journal.write("ui_turn_complete", {"turn_id": turn_id})
                self.read_limits(client)
                self.update(status="ready")
        except SessionStopped:
            self.record_if_available("session_stop_requested", {"reason": "Stop requested."})
        except Exception as error:
            message = redact(str(error))
            self.update(status="error", error=message)
            self.record_if_available("session_failed", {"type": type(error).__name__, "error": message})
        finally:
            try:
                if client:
                    client.close()
            except Exception as error:
                self.update(status="error", error=redact(str(error)))
                self.record_if_available("cleanup_failed", {"error": str(error)})
            if self.snapshot()["status"] != "error":
                self.update(status="stopped")
            self.record_if_available("session_end", {"status": self.snapshot()["status"]})
            try:
                self.journal.close()
            except Exception as error:
                self.update(status="error", error=redact(str(error)))

    def record_if_available(self, kind, data):
        # A dead scribe cannot record its own failure. Leave its partial file and
        # lease intact, surface the failure in UI, and do not create another log.
        if not self.journal.failed:
            try:
                self.journal.write(kind, data)
            except Exception as error:
                self.update(status="error", error=redact(str(error)))

    def stop(self):
        self.stop_event.set()
        with self.lock:
            if self.worker.is_alive() and self.state["status"] in ("starting", "ready", "busy"):
                self.state["status"] = "stopping"
                self.state["revision"] += 1

    def wait(self):
        self.worker.join(timeout=20)
