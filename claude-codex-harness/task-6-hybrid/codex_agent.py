# From ../task-5-subagent/agent.py: the Codex engine. Task 6 adds rule 5g: a code-mode
# model keeps its JavaScript exec host (policy.disable_flags(code_mode=True)).
"""An agent on Codex App Server: its own app-server process, Codex thread and SQLite
folder. Our tools are the thread's dynamic tools; each call comes back to this client
(item/tool/call) and runs in the Toolbox. Every raw model output item that calls a tool
goes through Agent.model_call, so exec's JavaScript is recorded and a call to anything
unreviewed (Codex's own collaboration.* sub-agents) stops the turn.
"""

import json
import time
from pathlib import Path

import codex_client as cc
import ledger_log
import policy
from agent import INTERRUPT_GRACE, Agent, TurnTimeout

TASK_DIR = Path(__file__).resolve().parent
CLIENT_INFO = {"name": "nimoi_claude_codex_hybrid",
               "title": "NIMOI bootstrap-harness claude-codex task-6-hybrid",
               "version": "0.1.0"}
# Agent-message fragments are streamed to the page; reasoning fragments are not asked for.
OPT_OUT = ["item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded",
           "item/reasoning/textDelta", "item/plan/delta"]
FRAGMENT = "item/agentMessage/delta"
UNRECORDED = (FRAGMENT,)  # summarised per message instead (message_stream)


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


class CodexAgent(Agent):
    engine = "codex"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.server = self.thread_id = None
        self.code_mode = policy.code_mode_model(self.conv.catalog.get(self.model))

    # ---- lifecycle ---------------------------------------------------------------------
    def start(self, instructions):
        """Start this agent's app-server and thread. Returns (model, restriction evidence)."""
        conv = self.conv
        runtime = conv.runtime_dir / self.id
        runtime.mkdir(parents=True, exist_ok=True)
        command = [conv.codex, "app-server", "--listen", "stdio://",
                   "-c", f"sqlite_home={json.dumps(str(runtime))}",
                   *policy.disable_flags(self.code_mode), *conv.extra_codex_args]
        self.server = cc.AppServer(command, ledger_log.AgentRecord(self.record, self.id),
                                   env=conv.codex_env, cwd=TASK_DIR, unrecorded=UNRECORDED)
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
            "agent_started", agent=self.id, layer=self.layer, engine=self.engine,
            parent=self.parent.id if self.parent else None, depth=self.depth,
            model=thread.get("model"), author=self.author, bounds=self.bounds.as_dict(),
            tools=self.toolbox.names, instructions=self.instructions_name,
            instructions_id=self.instructions_id, developer_instructions=instructions,
            thread_id=self.thread_id, code_mode=self.code_mode)
        self.state = "idle"
        return thread.get("model"), restrictions

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
        disabled = [n for n in policy.DISABLED_FEATURES
                    if not (self.code_mode and n in policy.CODE_MODE_KEEPS)]
        result = {"engine": self.engine, "code_mode": self.code_mode, "catalog_entry": entry,
                  "features_still_on": sorted(n for n in disabled if features.get(n)),
                  "mcp_configured": mcp_names, "mcp_servers": servers,
                  "mcp_with_tools": [s["name"] for s in servers if s["tools"]]}
        self.record.write("restrictions", agent=self.id, thread_id=self.thread_id, **result)
        return result

    def close(self):
        if self.server is not None:
            code = self.server.close()
            self.server = None
            self.state = "closed" if self.state in ("idle", "running") else self.state
            return code
        return None

    # ---- one turn ------------------------------------------------------------------------
    def _engine_turn(self, text, cap, idle):
        started = self.server.request("turn/start", {
            "threadId": self.thread_id,
            "input": [{"type": "text", "text": text, "text_elements": []}]})
        turn_id = started["turn"]["id"]
        self.publish("turn_started", turn_id=turn_id)
        interrupted, grace, reason = False, None, None
        while True:
            if self.stop_requested() and not interrupted:
                interrupted, grace, reason = True, time.monotonic() + INTERRUPT_GRACE, self._stop_reason
                self.record.write("interrupt", agent=self.id, turn_id=turn_id, reason=reason)
                self.publish("notice", text=f"Interrupting {self.id}'s turn: {reason}.")
                self.server.request("turn/interrupt", {"threadId": self.thread_id,
                                                       "turnId": turn_id})
            deadline = grace if interrupted else self.deadline(cap, idle)
            try:
                message = self.server.next_notification(min(deadline, time.monotonic() + 0.25))
            except cc.Timeout:
                if time.monotonic() >= deadline:
                    if interrupted:
                        raise TurnTimeout(f"{self.id}'s turn did not end {INTERRUPT_GRACE} s "
                                          "after it was interrupted") from None
                    self.interrupt(self.limit_reason(cap, idle))
                continue
            self.touch()
            self._on_notification(message)
            params = message.get("params") or {}
            if (message.get("method") == "turn/completed"
                    and (params.get("turn") or {}).get("id") == turn_id):
                turn = params["turn"]
                return {"id": turn_id, "status": turn["status"], "error": turn.get("error"),
                        "duration_ms": turn.get("durationMs"),
                        "interrupted_because": reason if interrupted else None}

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
            self._stream_fragment(params.get("itemId"), params.get("delta", ""))
        elif method == "thread/tokenUsage/updated":
            usage = self.stats["usage"] = params["tokenUsage"]
            self.record.write("usage", agent=self.id, token_usage=usage)
            self.publish("usage", last=usage.get("last"), total=usage.get("total"),
                         context_window=usage.get("modelContextWindow"))
        elif method == "rawResponse/completed":
            self.record.write("response_usage", agent=self.id,
                              response_id=params.get("responseId"), usage=params.get("usage"))
        elif method == "rawResponseItem/completed":
            item = params.get("item") or {}
            name = call_identity(item)
            if name is not None:
                self.model_call(name, kind=item.get("type"), call_id=item.get("call_id"),
                                payload=item.get("arguments", item.get("input", item.get("action"))))
        elif method == "error":
            self.publish("error", message=(params.get("error") or {}).get("message"),
                         will_retry=params.get("willRetry"))
        elif method == "item/completed":
            item = params["item"]
            kind = item.get("type")
            if kind == "agentMessage":
                self._message_done(item.get("id"), item_text(item), item.get("phase"))
            elif kind in policy.TOOL_ITEMS:
                self.record.write("tool_item", agent=self.id, item=item)
                self.publish("tool_item", item_type=kind, status=item.get("status"),
                             execution=kind in policy.EXECUTION_ITEMS,
                             detail=json.dumps(item)[:500])

    # ---- server-to-client requests ----------------------------------------------------
    def _on_tool_call(self, params):
        success, output = self.tool_call(params.get("tool"), params.get("arguments"),
                                         namespace=params.get("namespace"),
                                         call_id=params.get("callId"), turn_id=params.get("turnId"))
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
