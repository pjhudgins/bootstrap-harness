"""The Codex backend: runs one GPT agent through the Codex App Server (task 6).

Drawn from the two Codex swimlanes' task-5 work (read-only there; adapted here, 2026-09-28):
  - claude-codex-harness/task-5-subagent/codex_client.py: the stdio transport, redaction,
    find_codex and the CODEX_HOME snapshot (now codex_server.py);
  - gpt-codex-harness/task-5-subagent/policy.py and runtime.py:
      - the restriction set: features off, agents off, web search off;
      - the preflight: account, features, models;
      - the exec host kept on only for code-mode-only models;
      - raw-call monitoring, approval declines and the restriction caveat.
Changes here: asyncio; harness tools from the shared registry (tools.ToolDef), served through
AgentCore.call_tool (the same checks and records as Claude agents); agent text as its own
ledger entries; and the additions below, from captures of codex 0.158 against a fake model
(tests/test_codex.py re-checks them).

Restrictions: what is requested, and what is checked before the first turn (fail closed)
  Requested (at process start with -c, and again in thread/start's config):
    - features off: the peers' list, plus goals, sleep_tool, image_generation, view_image,
      skill_search, collaboration_modes, multi_agent_v2 (0.158 offered goal and sleep tools);
    - agents.enabled=false and web_search="disabled";
    - no instruction files (project_doc_max_bytes=0), and no Codex permission,
      collaboration-mode or apps text;
    - no turn-end notification program (notify=[]; the live config sets one);
    - every skill Codex lists disabled by name, so the user's skills are not advertised;
    - MCP servers and plugins configured in CODEX_HOME disabled by name.
  thread/start:
    - environments=[] (no execution environment). Without it, 0.158 offers apply_patch and
      view_image whatever the feature flags say; with it, neither is offered;
    - sandbox read-only, approvals "untrusted", ephemeral.
  Checked (fail closed):
    - the model Codex selected is the one requested;
    - no instruction files were loaded;
    - every requested feature reads back off at thread level, with one known exception:
      unified_exec reads on whatever the flag says (recorded as a caveat; no shell tool is
      offered, which the capture test checks).
  Code-mode-only models (gpt-6-*, gpt-5.6-*) reach harness tools only from JavaScript inside
  Codex's `exec` tool, a V8 isolate that states it has no fs or network. The exec host stays
  on for them (rules.md 5g): restricted where possible, documented here.

What is prevented, and what is only detected
  - Harness tools. Every call arrives as item/tool/call and goes through AgentCore.call_tool
    (allowlist, onboarding gate, bounds, records): prevented, as for Claude agents.
  - Approval requests (commands, patches, permissions, MCP elicitation). They are declined,
    which prevents them, and the agent is stopped.
  - Model output that calls anything other than harness tools or the runtime helpers
    (RUNTIME_CALLS), and any thread item other than messages, reasoning and harness tool
    calls. These are detected after the fact, from raw events and item notifications, and
    the agent is stopped: the turn is interrupted and its App Server closed. This is
    detection, not prevention (founder decision, 2026-09-28).
  - Codex's user-input tools cannot be switched off, but neither reaches the harness (0.158):
    Codex refuses request_user_input outside Plan mode, and turns request_user_input_async
    into an ordinary agent message (recorded as the agent's text). A request that did reach
    the harness would be recorded and answered with no answers.

Records (all through AgentCore.pub, so every one carries the agent and turn):
  codex_start, codex_rpc (every protocol message both ways, except the delta streams opted out
  at initialize), codex_preflight, codex_restrictions, codex_stderr, codex_exit, model_call,
  native_call_stop, approval_declined, user_input_declined, turn_limit, usage, rate_limit.
  In codex_rpc records:
    - text that has its own entry (prompts, agent replies) is replaced by its [[link]];
    - long strings (the system prompt echo, encrypted reasoning, big tool outputs) become
      digests;
    - credentials are redacted, and config/read results are never recorded.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from agent import AgentCore, TurnResult
from codex_server import AppServer, CodexHomeWatch, CodexSettings, ReplyError, RpcError, redact
from tools import result_text

# ---- policy -------------------------------------------------------------------------------

DISABLED_FEATURES = (
    # gpt-codex-harness/task-5-subagent/policy.py DISABLED
    "shell_tool", "unified_exec", "unified_exec_tty", "shell_snapshot", "code_mode", "js_repl", "multi_agent",
    "apps", "hooks", "plugins", "remote_plugin", "skill_mcp_dependency_install", "tool_suggest", "browser_use",
    "browser_use_external", "browser_use_full_cdp_access", "in_app_browser", "computer_use",
    "in_app_local_automation", "worktrees",
    # added here after the 0.158 capture
    "multi_agent_v2", "goals", "sleep_tool", "image_generation", "view_image", "skill_search",
    "collaboration_modes", "memories", "tool_search", "request_permissions_tool",
)
KNOWN_STAYS_ON = {"unified_exec"}  # reads back on whatever the flag says (0.158); no shell tool is offered
RESTRICTIONS: dict[str, Any] = {
    **{f"features.{name}": False for name in DISABLED_FEATURES},
    "agents.enabled": False, "web_search": "disabled",
    "project_doc_max_bytes": 0,
    "include_permissions_instructions": False,
    "include_collaboration_mode_instructions": False,
    "include_apps_instructions": False,
    "notify": [],  # the user's config may run a program after every turn (live ~/.codex does)
}
# Calls the model may make besides harness tools: Codex's runtime helpers, all offered by 0.158
# under RESTRICTIONS. Anything else in the model's output stops the agent.
RUNTIME_CALLS = {"exec", "functions.exec", "wait", "functions.wait", "request_user_input",
                 "functions.request_user_input", "request_user_input_async", "functions.request_user_input_async"}
# Thread items a restricted agent produces. Any other kind (commandExecution, fileChange,
# mcpToolCall, collabAgentToolCall, subAgentActivity, webSearch, imageView, ...) stops the agent.
ALLOWED_ITEMS = {"userMessage", "agentMessage", "reasoning", "dynamicToolCall", "contextCompaction"}
APPROVAL_DECLINES = {  # from gpt-codex-harness (policy.py; its task 6 adds permissions)
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"},
    "mcpServer/elicitation/request": {"action": "decline", "content": None, "_meta": None},
    "execCommandApproval": {"decision": "abort"},
    "applyPatchApproval": {"decision": "abort"},
}
OPT_OUT_NOTIFICATIONS = [  # delta streams: the completed items carry the same content
    "item/agentMessage/delta", "item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded",
    "item/reasoning/textDelta", "item/plan/delta", "item/commandExecution/outputDelta",
    "item/fileChange/outputDelta", "command/exec/outputDelta", "process/outputDelta",
]
SERVER_DESCRIPTIONS = {  # namespace descriptions Codex shows above each server's tools
    "calc": "Harness calculator.",
    "fs": "Harness file tools, bounded by fs.read and fs.write.",
    "exec": "Harness script runner, bounded by fs.exec.",
    "ledger": "Harness ledger tools, bounded by ledger.read and ledger.write.",
    "agents": "Harness subagents.",
    "gov": "Governance: requests to the governor.",
}
LONG_STRING = 4000  # strings longer than this are recorded as digests in codex_rpc records
CLIENT_INFO = {"name": "nimoi_claude_anthropic_harness", "title": "NIMOI claude-anthropic-harness task 6",
               "version": "0.6.0"}
STOP_GRACE_S = 20  # after an interrupt, how long to wait for the turn to end before closing Codex


def call_identity(item: dict[str, Any]) -> str | None:
    """The dotted name of a call in the model's output, or None (gpt-codex policy.py)."""
    kind = item.get("type", "")
    if kind in ("function_call", "custom_tool_call"):
        name = item.get("name", "")
        return f"{item['namespace']}.{name}" if item.get("namespace") else name
    if kind.endswith("_call"):
        return kind[:-5]
    return None


def needs_exec(catalogue_entry: dict[str, Any] | None) -> bool:
    """Code-mode-only models reach tools only through exec; an unknown model keeps it, with the caveat."""
    return catalogue_entry is None or catalogue_entry.get("tool_mode") == "code_mode_only"


def digest(text: str) -> dict[str, Any]:
    return {"digest": {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "chars": len(text),
                       "preview": text[:300]}}


def dynamic_tools(core: AgentCore) -> list[dict[str, Any]]:
    """The agent's tools as Codex dynamic tools: one namespace per server. Codex reserves names
    starting mcp__, so the model sees <server>.<name> (direct) or tools.<server>__<name> (exec);
    calls come back as (namespace, tool) and map to the harness name mcp__<server>__<name>."""
    servers: dict[str, list[dict[str, Any]]] = {}
    for t in core.tool_defs:
        servers.setdefault(t.server, []).append({"type": "function", "name": t.name, "description": t.description,
                                                 "inputSchema": t.input_schema})
    return [{"type": "namespace", "name": s, "description": SERVER_DESCRIPTIONS.get(s, f"Harness tools: {s}."),
             "tools": tools} for s, tools in servers.items()]


async def paged(server: AppServer, method: str, params: dict[str, Any]) -> list[Any]:
    out, cursor = [], None
    while True:
        page = await server.request(method, {**params, "cursor": cursor, "limit": 100})
        out += page.get("data") or []
        cursor = page.get("nextCursor")
        if not cursor:
            return out


def record_codex_home_changes(env: Any) -> dict[str, Any] | None:
    """At the end of a launch: what changed under CODEX_HOME (founder condition for ~/.codex)."""
    settings: CodexSettings | None = env.codex
    if settings is None:
        return None
    changes = settings.watch.finish()
    if changes is None:
        return None
    return env.bus.publish("codex_home_changes", agent=None, live_home=settings.live_home, **changes,
                           note="Other Codex processes (the desktop app, other lanes) may write here too: "
                                "while_running means the time overlaps this run, not that this run caused it. "
                                "This harness's agents keep their Codex state and logs in their own sqlite_home "
                                "under the task's .runtime/<project>/.")


class _Turn:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.done: asyncio.Future = loop.create_future()
        self.id: str | None = None
        self.started = loop.time()
        self.rounds = 0  # model responses in this turn (rawResponse/completed)
        self.token_updates = 0
        self.token_usage: dict[str, Any] | None = None
        self.tool_time = 0.0  # seconds spent inside harness tools: not model time
        self.tools_running: dict[str, float] = {}
        self.limited: str | None = None  # why the harness interrupted it
        self.limited_at: float | None = None


class CodexBackend:
    kind = "codex-app-server"

    def __init__(self, core: AgentCore):
        self.core = core
        self.settings: CodexSettings | None = core.env.codex
        self.server: AppServer | None = None
        self.thread_id: str | None = None
        self.stopped: str | None = None  # why the agent was stopped; no more turns
        self.info: dict[str, Any] = {}
        self._turn: _Turn | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---- lifecycle ----------------------------------------------------------------------------

    async def __aenter__(self) -> "CodexBackend":
        core, settings = self.core, self.settings
        if settings is None:
            raise RuntimeError(f"{core.model} runs through Codex, which is not configured for this launch")
        self._loop = asyncio.get_running_loop()
        settings.watch.start()  # before anything can touch CODEX_HOME
        catalogue = (await asyncio.to_thread(settings.catalogue)).get(core.model)  # cached after the first agent
        version = await asyncio.to_thread(settings.version)
        exec_host = needs_exec(catalogue)
        state = settings.state_dir / core.env.session / core.agent_id
        state.mkdir(parents=True, exist_ok=True)
        command = settings.command(state, RESTRICTIONS)
        core.pub("codex_start", command=command, cwd=str(state), codex_home=str(settings.home),
                 live_home=settings.live_home, version=version, model=core.model, catalogue=catalogue,
                 exec_host=exec_host)
        self.server = AppServer(on_message=self._record_rpc, on_request=self._on_request,
                                on_notification=self._on_notification,
                                on_stderr=lambda line: core.pub("codex_stderr", line=line),
                                on_error=lambda where, e: core.pub("codex_client_error", where=where, error=repr(e)))
        await self.server.start(command, env=settings.env(), cwd=state)
        try:
            await self._handshake(state, catalogue, exec_host)
        except BaseException:
            await self._close()
            raise
        core.active = True
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        try:
            await self._close()
        finally:
            self.core.active = False
        return False

    async def _close(self) -> None:
        if self.server is None:
            return
        server, self.server = self.server, None
        outcome = await server.close()
        self.core.pub("codex_exit", pid=server.pid, **outcome)

    async def server_info(self) -> dict[str, Any]:
        return self.info

    # ---- handshake and preflight --------------------------------------------------------------

    async def _handshake(self, state: Path, catalogue: dict[str, Any] | None, exec_host: bool) -> None:
        core, server, settings = self.core, self.server, self.settings
        init = await server.request("initialize", {"clientInfo": CLIENT_INFO, "capabilities": {
            "experimentalApi": True, "requestAttestation": False, "optOutNotificationMethods": OPT_OUT_NOTIFICATIONS}})
        await server.notify("initialized")
        account = (await server.request("account/read", {"refreshToken": False})).get("account") or {}
        if settings.require_chatgpt and account.get("type") != "chatgpt":
            raise RuntimeError(f"the ChatGPT login is required (account type {account.get('type')!r}); "
                               "there is no API-key fallback")
        # Founder decision (task 2): account type and provider only, never email or organization.
        self.info["account"] = {"subscriptionType": account.get("planType"),
                                "apiProvider": f"openai:{account.get('type')}"}
        config = (await server.request("config/read", {"includeLayers": False, "cwd": str(state)},
                                       record_result=False)).get("config") or {}
        features = {f["name"]: f["enabled"] for f in await paged(server, "experimentalFeature/list", {})}
        models = {m["model"]: m for m in await paged(server, "model/list", {"includeHidden": True})}
        if core.model not in models:
            raise RuntimeError(f"{core.model} is not in this Codex's model list")
        skills = sorted({s["name"] for entry in (await server.request(
            "skills/list", {"cwds": [str(state)], "forceReload": True})).get("data") or []
            for s in entry.get("skills") or []})
        overrides = {**RESTRICTIONS, "features.code_mode_host": exec_host,
                     "skills.config": [{"name": name, "enabled": False} for name in skills]}
        for key in ("mcp_servers", "plugins"):  # never recorded: only their names are used
            for name in config.get(key) or {}:
                overrides[f"{key}.{name}.enabled"] = False
        core.pub("codex_preflight", user_agent=init.get("userAgent"), platform=init.get("platformOs"),
                 account=self.info["account"], model_hidden=models[core.model].get("hidden"),
                 features_not_off=sorted(n for n in DISABLED_FEATURES if features.get(n) is True),
                 skills_disabled=skills, mcp_servers_disabled=sorted(config.get("mcp_servers") or {}),
                 plugins_disabled=sorted(config.get("plugins") or {}))

        thread = await server.request("thread/start", {
            "model": core.model, "cwd": str(state), "sandbox": "read-only", "approvalPolicy": "untrusted",
            "approvalsReviewer": "user", "ephemeral": True, "experimentalRawEvents": True, "environments": [],
            "config": overrides, "baseInstructions": core.system_prompt, "dynamicTools": dynamic_tools(core)})
        self.thread_id = thread["thread"]["id"]
        if thread.get("model") != core.model:
            raise RuntimeError(f"Codex selected {thread.get('model')!r}, not {core.model!r}")
        if thread.get("instructionSources"):
            raise RuntimeError(f"Codex loaded instruction files: {thread['instructionSources']}")
        effective = {f["name"]: f["enabled"] for f in await paged(
            server, "experimentalFeature/list", {"threadId": self.thread_id})}
        not_off = sorted(n for n in DISABLED_FEATURES if effective.get(n) is True and n not in KNOWN_STAYS_ON)
        if not_off:
            raise RuntimeError(f"requested restrictions not confirmed off: {not_off}")
        reported = effective.get("code_mode_host")
        if reported is not None and reported != exec_host:
            raise RuntimeError(f"exec host is {reported!r}, requested {exec_host!r}")
        try:
            limits = await server.request("account/rateLimits/read")
            core.pub("rate_limit", info=limits)
        except RpcError as e:
            core.pub("rate_limit", error=str(e))
        caveat = ("Codex's JavaScript exec host stays on: this model reaches harness tools only from inside exec "
                  "(rules.md 5g). " if exec_host else "The exec host is off: this model calls harness tools directly. ")
        core.pub("codex_restrictions", thread_id=self.thread_id, model=thread.get("model"),
                 provider=thread.get("modelProvider"), sandbox=thread.get("sandbox"),
                 approval_policy=thread.get("approvalPolicy"), multi_agent_mode=thread.get("multiAgentMode"),
                 instruction_sources=thread.get("instructionSources"), exec_host=exec_host,
                 reasoning_effort=thread.get("reasoningEffort"), service_tier=thread.get("serviceTier"),
                 disabled_plugin_ids=thread.get("disabledPluginIds"),
                 stays_on=sorted(n for n in KNOWN_STAYS_ON if effective.get(n)),
                 runtime_calls_permitted=sorted(RUNTIME_CALLS), items_permitted=sorted(ALLOWED_ITEMS),
                 caveat=caveat + "Shell, patch, native delegation, MCP, apps, web and browser tools are requested "
                        "off, and there is no execution environment. Approval requests are declined. Raw-call and "
                        "item monitoring stops the agent after the fact: detection, not prevention.")
        self.info["codex"] = {"thread_id": self.thread_id, "exec_host": exec_host, "version": settings.version()}  # cached

    # ---- a turn ---------------------------------------------------------------------------------

    async def run_turn(self, text: str, *, author: str, link: str | None = None,
                       link_id: str | None = None) -> TurnResult:
        core = self.core
        core.record_prompt(text, author=author, link=link, link_id=link_id)
        if self.stopped or self.server is None:
            core.pub("turn_refused", reason=self.stopped or "Codex is not running")
            return TurnResult(True, "stopped", core.last_text)
        turn = self._turn = _Turn(self._loop)
        try:
            response = await self.server.request("turn/start", {
                "threadId": self.thread_id, "environments": [],
                "input": [{"type": "text", "text": text, "text_elements": []}]})
            turn.id = turn.id or (response.get("turn") or {}).get("id")
            status, error = await self._wait(turn)
        except Exception as e:
            core.pub("turn_error", error=repr(e))
            status, error = "failed", repr(e)
        finally:
            self._turn = None
        usage = turn.token_usage or {}
        total = (usage.get("total") or {}).get("totalTokens")
        subtype = "success" if status == "completed" and not turn.limited else \
            f"stopped: {self.stopped}" if self.stopped else turn.limited or status
        core.record_usage(cumulative_tokens=total, num_turns=turn.rounds or turn.token_updates,
                          is_error=subtype != "success", subtype=subtype, turn_status=status, error=error,
                          token_usage=usage, duration_s=round(self._loop.time() - turn.started, 3),
                          tool_time_s=round(turn.tool_time, 3))
        if self.stopped:
            await self._close()  # stopping the agent includes whatever Codex was running for it
            return TurnResult(True, "stopped", core.last_text)
        return TurnResult(subtype != "success", subtype, core.last_text)

    async def _wait(self, turn: _Turn) -> tuple[str, Any]:
        """Wait for turn/completed. Model time (tools excluded) is capped by turn_timeout_s."""
        while not turn.done.done():
            if self.server is None or self.server.closed.is_set():
                return "failed", "Codex exited during the turn"
            now = self._loop.time()
            model_time = now - turn.started - turn.tool_time - sum(now - t for t in turn.tools_running.values())
            if turn.limited is None and model_time > self.settings.turn_timeout_s:
                await self._limit(turn, "timeout", model_time_s=round(model_time, 1))
            if turn.limited_at is not None and now - turn.limited_at > STOP_GRACE_S:
                self.core.pub("turn_abandoned", reason=turn.limited, grace_s=STOP_GRACE_S)
                await self._close()
                return "failed", f"no completion {STOP_GRACE_S}s after the interrupt"
            await asyncio.wait({turn.done}, timeout=1.0)
        completed = turn.done.result()
        return completed.get("status"), completed.get("error")

    async def _limit(self, turn: _Turn, reason: str, **details: Any) -> None:
        """The harness ends the turn: over a limit, or the agent stopped."""
        if turn.limited is not None:
            return
        turn.limited, turn.limited_at = reason, self._loop.time()
        if reason != "stopped":
            self.core.pub("turn_limit", reason=reason, **details)
        await self._interrupt_turn(turn)

    async def _interrupt_turn(self, turn: _Turn) -> None:
        for _ in range(50):  # the turn id arrives with turn/started or the turn/start response
            if turn.id or turn.done.done():
                break
            await asyncio.sleep(0.1)
        if turn.id and not turn.done.done() and self.server is not None:
            try:
                await self.server.request("turn/interrupt", {"threadId": self.thread_id, "turnId": turn.id}, timeout=15)
            except Exception as e:
                self.core.pub("interrupt_error", error=repr(e))

    async def interrupt(self) -> None:
        if self._turn is not None:
            await self._interrupt_turn(self._turn)

    def _stop(self, reason: str, **details: Any) -> None:
        """Stop this agent: record why, interrupt the turn; run_turn then closes Codex."""
        if self.stopped:
            return
        self.stopped = reason
        self.core.pub("native_call_stop", reason=reason, **details,
                      note="detected after the fact: detection, not prevention")
        if self._turn is not None:
            asyncio.ensure_future(self._limit(self._turn, "stopped"))

    # ---- messages from Codex ----------------------------------------------------------------------

    def _on_notification(self, message: dict[str, Any]) -> None:
        method, params = message["method"], message.get("params") or {}
        turn = self._turn
        if method == "turn/started" and turn is not None:
            turn.id = turn.id or (params.get("turn") or {}).get("id")
        elif method == "turn/completed" and turn is not None and params.get("threadId") == self.thread_id:
            if not turn.done.done():
                turn.done.set_result(params.get("turn") or {})
        elif method in ("item/started", "item/completed"):
            item = params.get("item") or {}
            if item.get("type") not in ALLOWED_ITEMS:
                self._stop(f"thread item {item.get('type')!r}", item_id=item.get("id"), method=method)
        elif method == "rawResponseItem/completed":
            name = call_identity(params.get("item") or {})
            if name is not None:
                permitted = name in RUNTIME_CALLS or name in self._harness_calls()
                self.core.pub("model_call", call=name, call_id=(params.get("item") or {}).get("call_id"),
                              permitted=permitted)  # not "name": records carry their ledger name there
                if not permitted:
                    self._stop(f"model called {name!r}", call_id=(params.get("item") or {}).get("call_id"))
        elif method == "rawResponse/completed" and turn is not None:
            turn.rounds += 1
            if turn.rounds > self.core.max_turns:  # read live: the governor may raise it mid-turn
                asyncio.ensure_future(self._limit(turn, "max_turns", rounds=turn.rounds,
                                                  max_turns=self.core.max_turns))
        elif method == "thread/tokenUsage/updated" and turn is not None:
            turn.token_updates += 1
            turn.token_usage = params.get("tokenUsage")
        elif method == "account/rateLimits/updated":
            self.core.pub("rate_limit", info=params.get("rateLimits"))

    def _harness_calls(self) -> set[str]:
        return {f"{t.server}.{t.name}" for t in self.core.tool_defs}

    async def _on_request(self, message: dict[str, Any]) -> Any:
        method, params = message["method"], message.get("params") or {}
        if method == "item/tool/call":
            return await self._tool_call(params)
        if method in APPROVAL_DECLINES:
            self.core.pub("approval_declined", method=method, params=params)
            self._stop(f"approval requested: {method}")
            return APPROVAL_DECLINES[method]
        if method == "item/tool/requestUserInput":
            self.core.pub("user_input_declined", questions=params.get("questions"),
                          note="request_user_input is not connected to anyone; answered with no answers")
            return {"answers": {}}
        if method == "currentTime/read":
            return {"currentTimeAt": int(time.time())}
        self.core.pub("codex_request_refused", method=method)
        raise ReplyError(-32601, f"{method} is not handled by this harness")

    async def _tool_call(self, params: dict[str, Any]) -> dict[str, Any]:
        def reply(text: str, ok: bool) -> dict[str, Any]:
            return {"contentItems": [{"type": "inputText", "text": text}], "success": ok}

        if params.get("threadId") != self.thread_id:
            raise ReplyError(-32602, "unknown thread")
        if self.stopped:
            return reply(f"error: this agent has been stopped ({self.stopped})", False)
        namespace, tool = params.get("namespace"), params.get("tool")
        name = f"mcp__{namespace}__{tool}" if namespace else str(tool)
        arguments = params.get("arguments")
        if isinstance(arguments, str):  # exec may pass a string
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                pass
        if not isinstance(arguments, dict):
            return reply("error: tool arguments must be an object", False)
        call_id = str(params.get("callId"))
        turn = self._turn
        started = self._loop.time()
        if turn is not None:
            turn.tools_running[call_id] = started
        try:
            out = await self.core.call_tool(name, arguments, call_id)
        finally:
            if turn is not None:
                turn.tools_running.pop(call_id, None)
                turn.tool_time += self._loop.time() - started
        return reply(result_text(out), not out.get("is_error"))

    # ---- recording ------------------------------------------------------------------------------

    def _record_rpc(self, direction: str, message: dict[str, Any]) -> None:
        """Every protocol message, both ways. Agent text is recorded first as its own entry."""
        core = self.core
        method = message.get("method")
        item = (message.get("params") or {}).get("item") or {}
        if method == "item/completed" and item.get("type") == "agentMessage" and item.get("text") \
                and not core.link_for(item["text"]):
            core.record_agent_text(item["text"])
        # Raw message items repeat text: the agent's (linked by now) and the harness's own inputs
        # (system prompt, context). Unlinked text there is digested, whatever its length.
        raw_message = method == "rawResponseItem/completed" and item.get("type") == "message"
        core.pub("codex_rpc", direction="to_codex" if direction == "send" else "from_codex",
                 rpc=method or ("error" if "error" in message else "result"), rpc_id=message.get("id"),
                 message=self._scrub(redact(message), limit=0 if raw_message else LONG_STRING))

    def _scrub(self, value: Any, limit: int = LONG_STRING, key: str | None = None) -> Any:
        """Linked text becomes its [[link]]; long strings (or, with limit 0, any message text) become digests."""
        if isinstance(value, str):
            link = self.core.link_for(value)
            if link:
                return link
            too_long = len(value) > LONG_STRING or (limit == 0 and key == "text" and value)
            return digest(value) if too_long else value
        if isinstance(value, dict):
            return {k: self._scrub(v, limit, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self._scrub(v, limit, key) for v in value]
        return value


def offline_settings(home: Path, state_dir: Path, extra_args: list[str], binary: str | None = None) -> CodexSettings:
    """Settings for tests: an isolated CODEX_HOME and a fake model provider (no login needed)."""
    from codex_server import find_codex
    home.mkdir(parents=True, exist_ok=True)
    return CodexSettings(binary=find_codex(binary), home=home, state_dir=state_dir, extra_args=tuple(extra_args),
                         require_chatgpt=False)


def live_settings(state_dir: Path, binary: str | None = None) -> CodexSettings:
    """Settings for live runs: ~/.codex and its ChatGPT login (founder: "Authorize, with ~/.codex diff")."""
    from codex_server import find_codex
    return CodexSettings(binary=find_codex(binary), home=Path.home() / ".codex", state_dir=state_dir)


__all__ = ["CodexBackend", "CodexHomeWatch", "offline_settings", "live_settings", "record_codex_home_changes",
           "RESTRICTIONS", "RUNTIME_CALLS", "ALLOWED_ITEMS", "call_identity", "needs_exec", "dynamic_tools"]
