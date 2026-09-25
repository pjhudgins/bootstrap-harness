# Task 4 version of ../task-3-ui/conversation.py: the record goes to the wiki ledger,
# the agent gets ledger and filesystem tools, and the test-pilot instructions.
"""One conversation with a restricted Codex agent, published as a stream of UI events.

Owns the app-server process and its one thread (new and ephemeral on every launch), and
one scribe session on the harness's ledger (a new session file per chat). Turns run on
a worker thread, which is the only reader of the app-server after setup. Every protocol
message and every derived record is written to the ledger by the harness (ledger_log.py);
what a person needs to see is also published as small typed events on an EventLog, which
ui.py streams to the browser. A new kind of event = one publish() here plus one renderer
in static/app.js (unknown types are still shown, generically).
"""

import hashlib
import json
import os
import queue
import subprocess
import threading
import time
import traceback
from pathlib import Path

import codex_client as cc
import fake_model
import ledger_log
import policy
import prompt
import tools

TASK_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = TASK_DIR / ".runtime"
CLIENT_INFO = {
    "name": "nimoi_claude_codex_ledger",
    "title": "NIMOI bootstrap-harness claude-codex task-4-ledger",
    "version": "0.1.0",
}
# Agent-message deltas are streamed to the page; reasoning deltas are not shown.
OPT_OUT = ["item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded",
           "item/reasoning/textDelta", "item/plan/delta"]
TURN_TIMEOUT = 600
# Tool outputs are recorded whole in the protocol log (the harness's `send` record); the
# derived tool_call record keeps a digest past this length instead of a third copy.
TOOL_RECORD_CHARS = 2000


class EventLog:
    """Append-only, in-memory list of UI events; readers wait for anything newer."""

    def __init__(self):
        self._events = []
        self._cond = threading.Condition()

    def publish(self, kind, **data):
        with self._cond:
            event = {"seq": len(self._events) + 1, "type": kind, "t": cc.utc_now(), **data}
            self._events.append(event)
            self._cond.notify_all()
            return event

    def after(self, seq, timeout):
        """Events with seq greater than `seq`, waiting up to `timeout` s for one."""
        with self._cond:
            self._cond.wait_for(lambda: len(self._events) > seq, timeout)
            return self._events[seq:]


def call_identity(item):
    """Tool name for a raw model output item that calls a tool, else None."""
    kind = item.get("type") or ""
    if kind in ("function_call", "custom_tool_call"):
        namespace = item.get("namespace")
        return f"{namespace}.{item.get('name')}" if namespace else item.get("name")
    if kind.endswith("_call"):  # web_search_call, local_shell_call, ...
        return kind[: -len("_call")]
    return None


def item_text(item):
    if item.get("type") == "agentMessage":
        return item.get("text", "")
    return " ".join(c.get("text", "") for c in item.get("content") or [] if c.get("type") == "text")


def read_catalog(codex, env):
    """Tool-relevant fields of every model in Codex's catalogue (`codex debug models`)."""
    try:
        out = subprocess.run([codex, "debug", "models"], env=env, cwd=TASK_DIR, timeout=60,
                             capture_output=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        models = json.loads(out.decode("utf-8-sig"))["models"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return {}
    fields = ("tool_mode", "multi_agent_version", "shell_type")
    return {m["slug"]: {f: m.get(f) for f in fields} for m in models if "slug" in m}


def limits_view(snapshot):
    """The parts of a RateLimitSnapshot the page shows."""
    snapshot = snapshot or {}
    view = {"limit_id": snapshot.get("limitId"), "plan": snapshot.get("planType")}
    for window in ("primary", "secondary"):
        w = snapshot.get(window) or {}
        view[window] = {"used_percent": w.get("usedPercent"),
                        "window_mins": w.get("windowDurationMins"),
                        "resets_at": w.get("resetsAt")} if w else None
    return view


def fake_reply(body):
    """Scripted stand-in model for --fake-model. A user message `tool: <name> <json args>`
    makes it call that tool once and then report the result; anything else is echoed.
    It calls nothing unless told to, so tests and demos decide every tool call."""
    inputs = body.get("input") or []
    last = inputs[-1] if inputs else {}
    if last.get("type") == "function_call_output":
        output = last.get("output")
        if isinstance(output, list):
            output = " ".join(c.get("text", "") for c in output if isinstance(c, dict))
        return [fake_model.message(f"(scripted fake model) Tool result: {str(output)[:600]}")]
    users = [i for i in inputs if i.get("role") == "user"]
    text = " ".join(c.get("text", "") for c in (users[-1].get("content") or [])
                    if isinstance(c, dict)) if users else ""
    if text.startswith("tool:"):
        name, _, raw = text[len("tool:"):].strip().partition(" ")
        try:
            arguments = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return [fake_model.message(f"(scripted fake model) Bad JSON arguments: {raw}")]
        return [fake_model.function_call("call_fake_1", name, arguments)]
    return [fake_model.message(f"(scripted fake model) You said: {text}")]


class Conversation:
    """start() once, send() per user message, interrupt() to stop a turn, close() once."""

    def __init__(self, codex=None, codex_home=None, model=None, fake=False,
                 ledger_root=ledger_log.LEDGER_ROOT, fs_root=ledger_log.NIMOI_ROOT,
                 runtime_dir=RUNTIME_DIR):
        self.events = EventLog()
        # Codex's SQLite state. Tests pass their own, so they never share it with a live
        # conversation running at the same time.
        self.runtime_dir = Path(runtime_dir)
        # Opening the ledger takes its lease: a second harness on it is refused here.
        self.journal = ledger_log.LedgerJournal(ledger_root)
        self.codex, self.codex_home = codex, codex_home
        self.model = model or policy.MODEL
        self.fake = fake
        self.agent_author = ledger_log.agent_author(self.model)
        self.toolbox = tools.Toolbox(self.journal.scribe, self.agent_author, fs_root)
        self.allowed_calls = set(self.toolbox.names) | set(policy.REVIEWED_MODEL_TOOLS)
        self.instructions = prompt.developer_instructions(self.agent_author)
        self.state = "starting"
        self.stats = {"turns": 0, "tool_calls": 0, "model_calls": [], "unreviewed_calls": [],
                      "declined": [], "usage": None}
        self.server = self.fake_model = self.worker = self.thread_id = None
        self._inbox = queue.Queue()
        self._stop_turn = threading.Event()
        self._send_lock = threading.Lock()
        self._closed = False
        self._offered_published = False

    # ---- setup -------------------------------------------------------------------
    def start(self):
        env = os.environ.copy()
        if self.codex_home:
            env["CODEX_HOME"] = str(Path(self.codex_home).resolve())
        else:  # in the gpt-codex swimlane codex could not find its home without this
            env.setdefault("CODEX_HOME", str(Path.home() / ".codex"))
        self.env = env
        codex = cc.find_codex(self.codex)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        command = [codex, "app-server", "--listen", "stdio://",
                   "-c", f"sqlite_home={json.dumps(str(self.runtime_dir))}",
                   *policy.disable_flags()]
        if self.fake:
            self.fake_model = fake_model.FakeModel(fake_reply, on_request=self._on_model_request)
            command += self.fake_model.codex_args()
        self.journal.write("run", harness="task-4-ledger", model=self.model,
                           fake_model=self.fake, codex_home=env["CODEX_HOME"],
                           ledger=self.journal.scribe.ledger, session=self.journal.session,
                           harness_author=self.journal.author, agent_author=self.agent_author,
                           fs_root=str(self.toolbox.fs_root),
                           disabled_features=policy.DISABLED_FEATURES,
                           thread_params=policy.THREAD_PARAMS, tools=self.toolbox.specs,
                           developer_instructions=self.instructions)
        # Founder's condition for live runs: record every change under CODEX_HOME.
        self.home_before, self.started = cc.snapshot_tree(env["CODEX_HOME"]), time.time()
        catalog = read_catalog(codex, env)
        self.server = cc.AppServer(command, self.journal, env=env, cwd=TASK_DIR)
        for method, response in policy.APPROVAL_DECLINES.items():
            self.server.handlers[method] = self._decliner(method, response)
        self.server.handlers["item/tool/call"] = self._on_tool_call
        self.server.handlers["item/tool/requestUserInput"] = self._on_user_input_request

        init = self.server.request("initialize", {"clientInfo": CLIENT_INFO, "capabilities": {
            "experimentalApi": True, "requestAttestation": False,
            "optOutNotificationMethods": OPT_OUT}})
        self.server.notify("initialized")
        account = self.server.request("account/read", {"refreshToken": False}).get("account") or {}
        self._read_rate_limits("start")
        # MCP server names only; the config payload is kept out of the journal.
        config = self.server.request("config/read", {"cwd": str(TASK_DIR)},
                                     journal_result=False)["config"]
        mcp_names = sorted((config.get("mcp_servers") or {}).keys())
        self.journal.write("config_extract", mcp_server_names=mcp_names)
        params = {"cwd": str(TASK_DIR), **policy.THREAD_PARAMS,
                  "dynamicTools": self.toolbox.specs, "model": self.model,
                  "developerInstructions": self.instructions}
        if mcp_names:
            params["config"] = policy.mcp_overrides(mcp_names)
        thread = self.server.request("thread/start", params)
        self.thread_id = thread["thread"]["id"]
        if thread.get("model") != self.model:  # the attested author names self.model
            self.events.publish("notice", text=f"Codex chose model {thread.get('model')}, "
                                               f"not {self.model}; the pilot's ledger author "
                                               f"still names {self.model}.")
        restrictions = self._restrictions(catalog, thread.get("model"), mcp_names)
        self.state = "idle"
        self.events.publish(
            "session", model=thread.get("model"), effort=thread.get("reasoningEffort"),
            thread_id=self.thread_id, server=init.get("userAgent"),
            account={"type": account.get("type"), "plan": account.get("planType")},
            fake_model=self.fake, journal=str(self.journal.path),
            ledger={"name": self.journal.scribe.ledger, "session": self.journal.session,
                    "sessions": len(self.journal.scribe.sessions),
                    "harness_author": self.journal.author, "agent_author": self.agent_author},
            tools=self.toolbox.names,
            reviewed_tools=policy.REVIEWED_MODEL_TOOLS, restrictions=restrictions)
        self.events.publish("state", state="idle")
        self.worker = threading.Thread(target=self._work, name="conversation", daemon=True)
        self.worker.start()

    def _restrictions(self, catalog, model, mcp_names):
        """What can be checked before any turn (task 2, layer 0 and 1)."""
        entry = catalog.get(model)
        features, cursor = {}, None
        while True:
            page = self.server.request("experimentalFeature/list",
                                       {"threadId": self.thread_id, "cursor": cursor})
            features.update({f["name"]: f["enabled"] for f in page["data"]})
            cursor = page.get("nextCursor")
            if not cursor:
                break
        status = self.server.request("mcpServerStatus/list",
                                     {"threadId": self.thread_id, "detail": "toolsAndAuthOnly"})
        servers = [{"name": s["name"], "status": s.get("runtimeStatus"),
                    "tools": sorted(s.get("tools") or {})} for s in status["data"]]
        result = {
            "model_tool_mode_direct": entry is not None
            and entry.get("tool_mode") not in policy.CODE_MODE_TOOL_MODES
            and not entry.get("multi_agent_version"),
            "catalog_entry": entry,
            "features_still_on": sorted(n for n in policy.DISABLED_FEATURES if features.get(n)),
            "mcp_configured": mcp_names,
            "mcp_servers": servers,
            "mcp_with_tools": [s["name"] for s in servers if s["tools"]],
        }
        self.journal.write("restrictions", thread_id=self.thread_id, **result)
        return result

    # ---- the conversation ----------------------------------------------------------
    def send(self, text):
        """Queue one user message. False if a turn is already running or it has ended."""
        text = (text or "").strip()
        if not text:
            raise ValueError("empty message")
        with self._send_lock:
            if self._closed or self.state != "idle":
                return False
            self.state = "running"
        self.events.publish("user_message", text=text)
        self.events.publish("state", state="running")
        self._inbox.put(text)
        return True

    def interrupt(self):
        self._stop_turn.set()

    def _work(self):
        while True:
            text = self._inbox.get()
            if text is None:
                return
            try:
                self._run_turn(text)
            except ledger_log.LedgerFailure as error:
                # The record can no longer be kept: stop taking messages (spec §5.4).
                self.events.publish("error", message=f"Ledger failure, conversation stopped: "
                                                     f"{error}")
                self.state = "failed"
                self.events.publish("state", state="failed")
                return
            except Exception as error:
                self.events.publish("error", message=repr(error))
                try:
                    self.journal.write("error", error=repr(error),
                                       traceback=traceback.format_exc())
                except ledger_log.LedgerFailure:
                    pass  # reported by the next write or at close
            finally:
                if not self._closed and self.state != "failed":
                    self.state = "idle"
                    self.events.publish("state", state="idle")

    def _run_turn(self, text):
        self._stop_turn.clear()
        self.stats["turns"] += 1
        # The human's text, under the human's name, before it goes to the agent.
        self.journal.message("user", text, ledger_log.HUMAN_AUTHOR, turn=self.stats["turns"])
        started = self.server.request("turn/start", {
            "threadId": self.thread_id,
            "input": [{"type": "text", "text": text, "text_elements": []}]})
        turn_id = started["turn"]["id"]
        self.events.publish("turn_started", turn_id=turn_id)
        deadline, interrupted = time.monotonic() + TURN_TIMEOUT, False
        while True:
            if self._stop_turn.is_set() and not interrupted:
                interrupted = True
                self.journal.write("interrupt", turn_id=turn_id)
                self.events.publish("notice", text="Stop requested: interrupting the turn.")
                self.server.request("turn/interrupt", {"threadId": self.thread_id,
                                                       "turnId": turn_id})
            try:
                message = self.server.next_notification(min(deadline, time.monotonic() + 0.25))
            except cc.Timeout:
                if time.monotonic() >= deadline:
                    raise
                continue
            self._on_notification(message)
            params = message.get("params") or {}
            if (message.get("method") == "turn/completed"
                    and (params.get("turn") or {}).get("id") == turn_id):
                turn = params["turn"]
                self.journal.write("turn", turn_id=turn_id, status=turn["status"],
                                   error=turn.get("error"), duration_ms=turn.get("durationMs"))
                self.events.publish("turn_completed", turn_id=turn_id, status=turn["status"],
                                    error=(turn.get("error") or {}).get("message"),
                                    duration_ms=turn.get("durationMs"))
                return

    def _on_notification(self, message):
        method, params = message.get("method"), message.get("params") or {}
        if method == "account/rateLimits/updated":
            self.journal.write("rate_limits", source="update", snapshot=params)
            self.events.publish("rate_limits", source="update",
                                **limits_view(params.get("rateLimits")))
            return
        if method in ("warning", "configWarning", "deprecationNotice"):
            self.events.publish("notice", text=params.get("message") or json.dumps(params)[:300])
            return
        if params.get("threadId") != self.thread_id:
            return
        if method == "thread/tokenUsage/updated":
            usage = self.stats["usage"] = params["tokenUsage"]
            self.journal.write("usage", token_usage=usage)
            self.events.publish("usage", last=usage.get("last"), total=usage.get("total"),
                                context_window=usage.get("modelContextWindow"))
        elif method == "rawResponse/completed":
            self.journal.write("response_usage", response_id=params.get("responseId"),
                               usage=params.get("usage"))
        elif method == "rawResponseItem/completed":
            item = params.get("item") or {}
            name = call_identity(item)
            if name is not None:
                reviewed = name in self.allowed_calls
                self.stats["model_calls"].append(name)
                if not reviewed:
                    self.stats["unreviewed_calls"].append(name)
                payload = item.get("arguments", item.get("input", item.get("action")))
                self.journal.write("model_call", name=name, type=item.get("type"),
                                   call_id=item.get("call_id"), payload=payload, reviewed=reviewed)
                self.events.publish("model_call", name=name, reviewed=reviewed,
                                    payload=json.dumps(payload)[:2000])
        elif method == "item/agentMessage/delta":
            self.events.publish("agent_delta", item_id=params.get("itemId"),
                                delta=params.get("delta", ""))
        elif method == "error":
            self.events.publish("error", message=(params.get("error") or {}).get("message"),
                                will_retry=params.get("willRetry"))
        elif method == "item/completed":
            item = params["item"]
            kind = item.get("type")
            if kind == "agentMessage":
                # The agent's text, under the agent's name; the harness's message record
                # links to it. (A userMessage item is Codex's echo of text the harness
                # already recorded in _run_turn; it stays in the raw recv record only.)
                self.journal.message("agent", item_text(item), self.agent_author,
                                     turn=self.stats["turns"], item_id=item.get("id"),
                                     phase=item.get("phase"))
                self.events.publish("agent_message", item_id=item.get("id"),
                                    text=item_text(item), phase=item.get("phase"))
            elif kind in policy.TOOL_ITEMS:
                self.journal.write("tool_item", item=item)
                self.events.publish("tool_item", item_type=kind, status=item.get("status"),
                                    execution=kind in policy.EXECUTION_ITEMS,
                                    detail=json.dumps(item)[:500])

    # ---- server-to-client requests ----------------------------------------------------
    def _on_tool_call(self, params):
        success, output = self.toolbox.call(params.get("tool"), params.get("namespace"),
                                            params.get("arguments"))
        recorded = output if len(output) <= TOOL_RECORD_CHARS else {
            "first_chars": output[:TOOL_RECORD_CHARS], "chars": len(output),
            "sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "whole_output": "in the next harness send record (the tool response)"}
        record = {"call_id": params.get("callId"), "turn_id": params.get("turnId"),
                  "tool": params.get("tool"), "namespace": params.get("namespace"),
                  "arguments": params.get("arguments"), "success": success,
                  "output": recorded}
        self.stats["tool_calls"] += 1
        self.journal.write("tool_call", source="python", **record)
        self.events.publish("tool_call", tool=record["tool"], arguments=record["arguments"],
                            output=output[:TOOL_RECORD_CHARS]
                            + ("…" if len(output) > TOOL_RECORD_CHARS else ""),
                            success=success)
        return {"contentItems": [{"type": "inputText", "text": output}], "success": success}

    def _decliner(self, method, response):
        def handle(params):
            self.stats["declined"].append(method)
            self.journal.write("declined", method=method, params=params)
            self.events.publish("declined", method=method, detail=json.dumps(params)[:500])
            return response
        return handle

    def _on_user_input_request(self, params):
        questions = [q.get("question") for q in params.get("questions") or []]
        self.journal.write("user_input_request", params=params)
        self.events.publish("notice", text="The agent asked for input this page cannot answer "
                                           f"yet, and got none: {questions}")
        return {"answers": {}}

    def _on_model_request(self, entry):
        """Fake-model runs only: the exact tools Codex offered (called on the fake's thread)."""
        body = entry["body"] if isinstance(entry["body"], dict) else {}
        names = fake_model.tool_names(body)
        self.journal.write("model_request", path=entry["path"], model=body.get("model"),
                           tool_names=names)
        if not self._offered_published:
            self._offered_published = True
            unreviewed = [n for n in names if n not in self.allowed_calls]
            self.events.publish("offered_tools", names=names, unreviewed=unreviewed)

    def _read_rate_limits(self, when):
        try:
            result = self.server.request("account/rateLimits/read")
        except cc.RpcError as error:
            self.journal.write("rate_limits", source=when, error=error.error)
            self.events.publish("notice", text=f"Rate limits unavailable: "
                                               f"{error.error.get('message')}")
            return
        self.journal.write("rate_limits", source=when, snapshot=result)
        self.events.publish("rate_limits", source=when, **limits_view(result.get("rateLimits")))

    # ---- shutdown ---------------------------------------------------------------------
    def close(self):
        """End the conversation: stop the worker, the app-server and the fake model, record
        CODEX_HOME changes and a summary, and close the journal. Safe to call twice."""
        with self._send_lock:
            if self._closed:
                return
            self._closed = True
            self.state = "ended"
        try:
            self._close()
        finally:
            self.journal.close()  # trailer and lease release, even if shutdown failed

    def _close(self):
        worker_done = True
        if self.worker is not None:
            self._stop_turn.set()
            self._inbox.put(None)
            self.worker.join(timeout=30)
            worker_done = not self.worker.is_alive()
        code = None
        if self.server is not None:
            if worker_done and self.thread_id:
                try:
                    self._read_rate_limits("end")
                except cc.ProtocolError:
                    pass
            code = self.server.close()
            changes = cc.diff_snapshots(self.home_before, cc.snapshot_tree(self.env["CODEX_HOME"]),
                                        self.started, time.time())
            self.journal.write("codex_home_changes", codex_home=self.env["CODEX_HOME"],
                               files_before=len(self.home_before), changes=changes,
                               note="while_running = mtime within the app-server's lifetime; "
                                    "other Codex processes (e.g. the desktop app) may also write")
        if self.fake_model is not None:
            self.fake_model.close()
        summary = {"turns": self.stats["turns"], "tool_calls": self.stats["tool_calls"],
                   "model_calls": self.stats["model_calls"],
                   "unreviewed_calls": self.stats["unreviewed_calls"],
                   "declined": self.stats["declined"],
                   "token_usage_total": (self.stats["usage"] or {}).get("total"),
                   "app_server_exit": code, "worker_finished": worker_done}
        try:
            self.journal.write("summary", **summary)
        finally:
            self.events.publish("ended", journal=str(self.journal.path), summary=summary)
