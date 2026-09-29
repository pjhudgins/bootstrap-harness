"""One Codex agent in the harness's tree: the root test pilot, or a subagent.

Each agent has its own app-server process, Codex thread and SQLite folder, and a Toolbox
bound to its own bounds. So a parent blocked in subagent_wait holds only its own
connection, and the child it waits for keeps running. The root takes turns from the
page; a subagent runs exactly one turn, its instructions, and is then done.

A turn ends when Codex completes it, or is interrupted: on Stop, when the agent calls a
tool that was never reviewed, when nothing happens for `idle` seconds, or at its `cap`.
Streamed message fragments are shown on the page but not recorded one by one: each
message's fragments are summarised in one `message_stream` record (founder, 2026-09-25).
"""

import hashlib
import json
import threading
import time
from pathlib import Path

import codex_client as cc
import ledger_log
import policy
import tools

TASK_DIR = Path(__file__).resolve().parent
CLIENT_INFO = {"name": "nimoi_claude_codex_subagents",
               "title": "NIMOI bootstrap-harness claude-codex task-5-subagent",
               "version": "0.2.0"}
# Agent-message fragments are streamed to the page; reasoning fragments are not asked for.
OPT_OUT = ["item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded",
           "item/reasoning/textDelta", "item/plan/delta"]
FRAGMENT = "item/agentMessage/delta"
UNRECORDED = (FRAGMENT,)  # summarised per message instead (message_stream)
TOOL_RECORD_CHARS = 2000
INTERRUPT_GRACE = 30      # seconds a turn may take to end after an interrupt


def call_identity(item):
    """Tool name for a raw model output item that calls a tool, else None."""
    kind = item.get("type") or ""
    if kind in ("function_call", "custom_tool_call"):
        namespace = item.get("namespace")
        return f"{namespace}.{item.get('name')}" if namespace else item.get("name")
    if kind.endswith("_call"):
        return kind[: -len("_call")]
    return None


def item_text(item):
    if item.get("type") == "agentMessage":
        return item.get("text", "")
    return " ".join(c.get("text", "") for c in item.get("content") or [] if c.get("type") == "text")


def limits_view(snapshot):
    snapshot = snapshot or {}
    view = {"limit_id": snapshot.get("limitId"), "plan": snapshot.get("planType")}
    for window in ("primary", "secondary"):
        w = snapshot.get(window) or {}
        view[window] = {"used_percent": w.get("usedPercent"),
                        "window_mins": w.get("windowDurationMins"),
                        "resets_at": w.get("resetsAt")} if w else None
    return view


class Stream:
    """The fragments of one agent message, as they arrive."""

    def __init__(self, turn_started):
        self.fragments, self.chars, self.parts = 0, 0, []
        self.turn_started = turn_started
        self.first = self.last = None
        self.first_at = self.last_at = None

    def add(self, delta):
        now, now_at = time.monotonic(), cc.utc_now()
        if self.first is None:
            self.first, self.first_at = now, now_at
        self.last, self.last_at = now, now_at
        self.fragments += 1
        self.chars += len(delta)
        self.parts.append(delta)

    def text(self):
        return "".join(self.parts)

    def summary(self, final_text):
        """The metadata recorded for the message: never the fragments themselves."""
        if not self.fragments:
            return {"fragments": 0}
        return {"fragments": self.fragments, "chars": self.chars,
                "first_fragment_at": self.first_at, "last_fragment_at": self.last_at,
                "first_fragment_after_ms": round((self.first - self.turn_started) * 1000),
                "streamed_ms": round((self.last - self.first) * 1000),
                "matches_text": None if final_text is None else self.text() == final_text}


class Agent:
    def __init__(self, conv, agent_id, *, model, bounds, depth, role, parent=None,
                 instructions_name=None, instructions_id=None):
        self.conv, self.id, self.model, self.bounds, self.depth = conv, agent_id, model, bounds, depth
        self.parent = parent
        self.instructions_name, self.instructions_id = instructions_name, instructions_id
        self.author = ledger_log.agent_author(role, model, agent_id)
        self.record, self.scribe = conv.record, conv.record.scribe
        self.onboarding = conv.onboarding
        self.produced = set()        # ledger names its own tool calls created: always readable
        self.toolbox = tools.Toolbox(self, fs_root=conv.fs_root, workspace=conv.workspace,
                                     scripts=conv.scripts, max_depth=conv.max_depth)
        self.allowed_calls = set(self.toolbox.names) | set(policy.REVIEWED_MODEL_TOOLS)
        self.server = self.thread_id = None
        self.state = "starting"
        self.children = []
        self.finished = threading.Event()
        self.last_message = None     # (text, transcript name, text id)
        self.stats = {"turns": 0, "tool_calls": 0, "model_calls": [], "unreviewed_calls": [],
                      "declined": [], "usage": None}
        self.turn_cap_deadline = None
        self._turn_lock = threading.Lock()  # state, and the stop flag, around turn ends
        self._stop_turn = threading.Event()
        self._stop_reason = None
        self._activity = time.monotonic()
        self._turn_started = None
        self._streams = {}           # item id -> Stream, for the turn running now
        self._onboarding_lock = threading.Lock()
        self._onboarding_upto, self._onboarding_total = 0, None

    # ---- what the Toolbox asks of its agent ---------------------------------------------
    def onboarding_done(self):
        return self._onboarding_total is not None and self._onboarding_upto >= self._onboarding_total

    def note_onboarding_lines(self, start, end, total):
        with self._onboarding_lock:
            was = self.onboarding_done()
            if start <= self._onboarding_upto + 1:
                self._onboarding_upto = max(self._onboarding_upto, end)
            self._onboarding_total = total
            now = self.onboarding_done()
        if now and not was:
            self.record.write("onboarding_read", agent=self.id, path=self.onboarding, lines=total)
            self.publish("notice", text=f"{self.id} has read the onboarding; its tools are open.")

    def spawn(self, model, bounds, instructions_name, instructions_id):
        return self.conv.spawn(self, model, bounds, instructions_name, instructions_id)

    def wait(self, subagent, seconds):
        return self.conv.wait(self, subagent, seconds)

    def publish(self, kind, **data):
        self.conv.events.publish(kind, agent=self.id, **data)

    # ---- lifecycle ---------------------------------------------------------------------
    def start(self, instructions):
        """Start this agent's app-server and thread. Returns (thread, restriction evidence)."""
        conv = self.conv
        runtime = conv.runtime_dir / self.id
        runtime.mkdir(parents=True, exist_ok=True)
        command = [conv.codex, "app-server", "--listen", "stdio://",
                   "-c", f"sqlite_home={json.dumps(str(runtime))}", *policy.disable_flags(),
                   *conv.extra_codex_args]
        self.server = cc.AppServer(command, ledger_log.AgentRecord(self.record, self.id),
                                   env=conv.env, cwd=TASK_DIR, unrecorded=UNRECORDED)
        for method, response in policy.APPROVAL_DECLINES.items():
            self.server.handlers[method] = self._decliner(method, response)
        self.server.handlers["item/tool/call"] = self._on_tool_call
        self.server.handlers["item/tool/requestUserInput"] = self._on_user_input_request
        self.server.request("initialize", {"clientInfo": CLIENT_INFO, "capabilities": {
            "experimentalApi": True, "requestAttestation": False,
            "optOutNotificationMethods": OPT_OUT}})
        self.server.notify("initialized")
        # MCP server names only; the config payload is kept out of the ledger.
        config = self.server.request("config/read", {"cwd": str(TASK_DIR)},
                                     record_result=False)["config"]
        mcp_names = sorted((config.get("mcp_servers") or {}).keys())
        params = {"cwd": str(TASK_DIR), **policy.THREAD_PARAMS, "model": self.model,
                  "dynamicTools": self.toolbox.specs, "developerInstructions": instructions}
        if mcp_names:
            params["config"] = policy.mcp_overrides(mcp_names)
        thread = self.server.request("thread/start", params)
        self.thread_id = thread["thread"]["id"]
        restrictions = self._restrictions(thread.get("model"), mcp_names)
        self.record.write(
            "agent_started", agent=self.id, parent=self.parent.id if self.parent else None,
            depth=self.depth, model=thread.get("model"), author=self.author,
            bounds=self.bounds.as_dict(), tools=self.toolbox.names,
            instructions=self.instructions_name, instructions_id=self.instructions_id,
            developer_instructions=instructions, thread_id=self.thread_id)
        self.state = "idle"
        return thread, restrictions

    def _restrictions(self, model, mcp_names):
        entry = self.conv.catalog.get(model)
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
        result = {"model_tool_mode_direct": policy.direct_tool_model(entry),
                  "catalog_entry": entry,
                  "features_still_on": sorted(n for n in policy.DISABLED_FEATURES if features.get(n)),
                  "mcp_configured": mcp_names, "mcp_servers": servers,
                  "mcp_with_tools": [s["name"] for s in servers if s["tools"]]}
        self.record.write("restrictions", agent=self.id, thread_id=self.thread_id, **result)
        return result

    def interrupt(self, reason="stop requested"):
        """Stop the running turn, or the next one if none is running yet (the turn loop
        sends the interrupt)."""
        with self._turn_lock:
            if not self._stop_turn.is_set():
                self._stop_reason = reason
            self._stop_turn.set()

    def interrupt_if_running(self, reason):
        """Stop the turn running now, if one is; True if one was."""
        with self._turn_lock:
            if self.state != "running":
                return False
            if not self._stop_turn.is_set():
                self._stop_reason = reason
            self._stop_turn.set()
            return True

    def stop_requested(self):
        return self._stop_turn.is_set()

    def close(self):
        if self.server is not None:
            code = self.server.close()
            self.server = None
            self.state = "closed" if self.state in ("idle", "running") else self.state
            return code
        return None

    # ---- one turn ------------------------------------------------------------------------
    def run_turn(self, text, role, author, *, cap, idle, linked=None):
        """Record the incoming message, run one turn, return the Turn.

        The message is recorded under its writer; for a subagent's task, `linked` names the
        pinned instructions entry ({"name", "id", "by"}), which is linked, not copied.
        A stop asked for before the turn starts means it never starts: nothing is sent."""
        self.stats["turns"] += 1
        with self._turn_lock:
            self.state = "running"
        try:
            if self._stop_turn.is_set():
                self.record.write("interrupt", agent=self.id, turn_id=None,
                                  reason=self._stop_reason, before_start=True)
                self.publish("notice", text=f"{self.id} was stopped before its turn began: "
                                            f"{self._stop_reason}.")
                return {"id": None, "status": "interrupted"}
            turn = self.stats["turns"]
            if linked:
                self.record.link_message(self.id, role, linked["name"], linked["id"], author,
                                         turn=turn, instructed_by=linked["by"])
            else:
                self.record.message(self.id, role, text, author, turn=turn)
            return self._run_protocol_turn(text, cap, idle)
        finally:
            with self._turn_lock:  # a stop ends one turn, not the agent's next one
                self.turn_cap_deadline = None
                if self.state == "running":
                    self.state = "idle"
                self._stop_turn.clear()
                self._stop_reason = None

    def _run_protocol_turn(self, text, cap, idle):
        started = self.server.request("turn/start", {
            "threadId": self.thread_id,
            "input": [{"type": "text", "text": text, "text_elements": []}]})
        turn_id = started["turn"]["id"]
        self.publish("turn_started", turn_id=turn_id)
        self._turn_started = self._activity = time.monotonic()
        self.turn_cap_deadline = self._turn_started + cap
        interrupted, grace = False, None
        while True:
            if self._stop_turn.is_set() and not interrupted:
                interrupted, grace = True, time.monotonic() + INTERRUPT_GRACE
                self.record.write("interrupt", agent=self.id, turn_id=turn_id,
                                  reason=self._stop_reason)
                self.publish("notice", text=f"Interrupting {self.id}'s turn: "
                                            f"{self._stop_reason}.")
                self.server.request("turn/interrupt", {"threadId": self.thread_id,
                                                       "turnId": turn_id})
            deadline = grace if interrupted else min(self.turn_cap_deadline,
                                                     self._activity + idle)
            try:
                message = self.server.next_notification(min(deadline, time.monotonic() + 0.25))
            except cc.Timeout:
                if time.monotonic() >= deadline:
                    if interrupted:
                        self._flush_streams()
                        raise
                    capped = time.monotonic() >= self.turn_cap_deadline
                    self.interrupt(f"the turn reached its {cap} s cap" if capped
                                   else f"nothing happened for {idle} s")
                continue
            self._activity = time.monotonic()
            self._on_notification(message)
            params = message.get("params") or {}
            if (message.get("method") == "turn/completed"
                    and (params.get("turn") or {}).get("id") == turn_id):
                turn = params["turn"]
                self._flush_streams()
                self.record.write("turn", agent=self.id, turn_id=turn_id,
                                  status=turn["status"], error=turn.get("error"),
                                  duration_ms=turn.get("durationMs"),
                                  interrupted_because=self._stop_reason if interrupted else None)
                self.publish("turn_completed", turn_id=turn_id, status=turn["status"],
                             error=(turn.get("error") or {}).get("message"),
                             duration_ms=turn.get("durationMs"))
                return turn

    # ---- message streams ---------------------------------------------------------------
    def _message_done(self, item_id, text, phase):
        """A completed agent message: its text by the agent, then its stream's summary."""
        recorded = self.record.message(self.id, "agent", text, self.author,
                                       turn=self.stats["turns"], item_id=item_id, phase=phase)
        if recorded:
            self.last_message = (text, recorded[0], recorded[1])
        self._record_stream(item_id, recorded, text, complete=True)

    def _record_stream(self, item_id, recorded, final_text, *, complete):
        stream = self._streams.pop(item_id, None) or Stream(self._turn_started)
        fields = {"agent": self.id, "turn": self.stats["turns"], "item_id": item_id,
                  "complete": complete, **stream.summary(final_text)}
        if recorded:
            fields.update(text=f"[[{recorded[0]}]]", text_id=recorded[1])
        self.record.write("message_stream", **fields)

    def _flush_streams(self):
        """Messages still streaming when the turn ended (interrupted or failed): their
        partial text is recorded as the agent's, marked incomplete."""
        for item_id in list(self._streams):
            partial = self._streams[item_id].text()
            recorded = self.record.message(self.id, "agent", partial, self.author,
                                           turn=self.stats["turns"], item_id=item_id,
                                           complete=False)
            self._record_stream(item_id, recorded, None, complete=False)

    # ---- notifications -------------------------------------------------------------------
    def _on_notification(self, message):
        method, params = message.get("method"), message.get("params") or {}
        if method == "account/rateLimits/updated":
            self.record.write("rate_limits", agent=self.id, source="update", snapshot=params)
            self.publish("rate_limits", source="update", **limits_view(params.get("rateLimits")))
            return
        if method in ("warning", "configWarning", "deprecationNotice"):
            self.publish("notice", text=params.get("message") or json.dumps(params)[:300])
            return
        if params.get("threadId") != self.thread_id:
            return
        if method == FRAGMENT:
            item_id = params.get("itemId")
            self._streams.setdefault(item_id, Stream(self._turn_started)).add(
                params.get("delta", ""))
            self.publish("agent_delta", item_id=item_id, delta=params.get("delta", ""))
        elif method == "thread/tokenUsage/updated":
            usage = self.stats["usage"] = params["tokenUsage"]
            self.record.write("usage", agent=self.id, token_usage=usage)
            self.publish("usage", last=usage.get("last"), total=usage.get("total"),
                         context_window=usage.get("modelContextWindow"))
        elif method == "rawResponse/completed":
            self.record.write("response_usage", agent=self.id,
                              response_id=params.get("responseId"), usage=params.get("usage"))
        elif method == "rawResponseItem/completed":
            self._on_model_output(params.get("item") or {})
        elif method == "error":
            self.publish("error", message=(params.get("error") or {}).get("message"),
                         will_retry=params.get("willRetry"))
        elif method == "item/completed":
            item = params["item"]
            kind = item.get("type")
            if kind == "agentMessage":
                text = item_text(item)
                self._message_done(item.get("id"), text, item.get("phase"))
                self.publish("agent_message", item_id=item.get("id"), text=text,
                             phase=item.get("phase"))
            elif kind in policy.TOOL_ITEMS:
                self.record.write("tool_item", agent=self.id, item=item)
                self.publish("tool_item", item_type=kind, status=item.get("status"),
                             execution=kind in policy.EXECUTION_ITEMS,
                             detail=json.dumps(item)[:500])

    def _on_model_output(self, item):
        """Every tool call the model makes is checked against the reviewed list; one that
        was never reviewed stops the turn."""
        name = call_identity(item)
        if name is None:
            return
        reviewed = name in self.allowed_calls
        self.stats["model_calls"].append(name)
        payload = item.get("arguments", item.get("input", item.get("action")))
        self.record.write("model_call", agent=self.id, name=name, type=item.get("type"),
                          call_id=item.get("call_id"), payload=payload, reviewed=reviewed)
        self.publish("model_call", name=name, reviewed=reviewed, payload=json.dumps(payload)[:2000])
        if not reviewed:
            self.stats["unreviewed_calls"].append(name)
            self.interrupt(f"it called {name}, a tool that was never reviewed")

    # ---- server-to-client requests ----------------------------------------------------
    def _on_tool_call(self, params):
        self._activity = time.monotonic()
        try:
            success, output = self.toolbox.call(params.get("tool"), params.get("namespace"),
                                                params.get("arguments"))
        finally:
            self._activity = time.monotonic()
        recorded = output if len(output) <= TOOL_RECORD_CHARS else {
            "first_chars": output[:TOOL_RECORD_CHARS], "chars": len(output),
            "sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "whole_output": "in the next harness send record (the tool response)"}
        self.stats["tool_calls"] += 1
        self.record.write("tool_call", agent=self.id, source="python",
                          call_id=params.get("callId"), turn_id=params.get("turnId"),
                          tool=params.get("tool"), namespace=params.get("namespace"),
                          arguments=params.get("arguments"), success=success, output=recorded)
        self.publish("tool_call", tool=params.get("tool"), arguments=params.get("arguments"),
                     output=output[:TOOL_RECORD_CHARS] + ("…" if len(output) > TOOL_RECORD_CHARS
                                                          else ""), success=success)
        return {"contentItems": [{"type": "inputText", "text": output}], "success": success}

    def _decliner(self, method, response):
        def handle(params):
            self.stats["declined"].append(method)
            self.record.write("declined", agent=self.id, method=method, params=params)
            self.publish("declined", method=method, detail=json.dumps(params)[:500])
            return response
        return handle

    def _on_user_input_request(self, params):
        questions = [q.get("question") for q in params.get("questions") or []]
        self.record.write("user_input_request", agent=self.id, params=params)
        self.publish("notice", text=f"{self.id} asked for input this page cannot answer, and "
                                    f"got none: {questions}")
        return {"answers": {}}
