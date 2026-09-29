"""An agent on the Claude Agent SDK (rules.md 6b: the lineage this swimlane had not used;
drawn from claude-anthropic-harness and gpt-anthropic-harness).

Each agent has one ClaudeSDKClient, which runs the SDK's bundled Claude CLI in a process
of its own. Every Claude agent of a conversation shares one asyncio loop, in a thread of
its own (ClaudeLoop). The SDK requires a client to be entered and left by the same task
(both peer lanes found this), so each agent has one driver task that holds its client
and takes turns from a queue; only interrupt() comes from outside it.

Our tools are one in-process MCP server; each call runs in the Toolbox on a worker
thread, so a tool that blocks (subagent_wait, governor_request) never stalls the loop.
Isolation (policy.py): no setting sources, strict MCP config, auto memory off, no
built-in tools, prompts passed verbatim (no @path expansion), no session persistence.
The init message's tool inventory is checked, a PreToolUse hook and can_use_tool refuse
anything that is not ours, and a model's use of one stops the turn.
"""

import asyncio
import concurrent.futures
import json
import threading
import time
from pathlib import Path

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient,
                              HookMatcher, PermissionResultAllow, PermissionResultDeny,
                              RateLimitEvent, ResultMessage, SdkMcpTool, StreamEvent,
                              SystemMessage, TextBlock, ThinkingBlock, ToolUseBlock,
                              create_sdk_mcp_server)

import ledger_log
import policy
from agent import INTERRUPT_GRACE, Agent, TurnTimeout

TASK_DIR = Path(__file__).resolve().parent
START_TIMEOUT = 120       # seconds for the CLI to start and answer the first requests
CLOSE_TIMEOUT = 30
MAX_MODEL_CALLS = 200     # the SDK's max_turns: model requests within one of our turns
INIT_FIELDS = ("model", "tools", "mcp_servers", "permissionMode", "cwd", "claude_code_version",
               "apiKeySource", "memory_paths", "agents", "skills", "slash_commands",
               "output_style")


class ClaudeLoop:
    """One asyncio loop in a thread of its own, for every Claude agent of a conversation."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        # Tool calls run on worker threads and may block for minutes (waits).
        self.loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(64, "claude-tool"))
        self.thread = threading.Thread(target=self.loop.run_forever, name="claude-loop",
                                       daemon=True)
        self.thread.start()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def call(self, fn, *args):
        self.loop.call_soon_threadsafe(fn, *args)

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)
        if not self.thread.is_alive():
            self.loop.close()  # its self-pipe sockets and its tool threads


class ClaudeAgent(Agent):
    engine = "claude"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._client = None
        self._turns = None           # asyncio.Queue on the loop: (text, Future) or None
        self._driver = None          # concurrent Future of the driver task
        self._ready = threading.Event()
        self._start_error = None
        self._evidence = {}
        self._current_message = None  # the API message now streaming (for fragments)
        self._own = {policy.claude_tool_name(n) for n in self.toolbox.names}

    # ---- lifecycle ---------------------------------------------------------------------
    def start(self, instructions):
        """Start the CLI and its session. Returns (model, restriction evidence)."""
        self._driver = self.conv.claude_loop.submit(self._drive(instructions))
        if not self._ready.wait(START_TIMEOUT):
            raise TimeoutError(f"{self.id}: the Claude CLI did not start in {START_TIMEOUT} s")
        if self._start_error is not None:
            raise self._start_error
        restrictions = {"engine": self.engine, **self._evidence}
        self.record.write("restrictions", agent=self.id, **restrictions)
        self.record.write(
            "agent_started", agent=self.id, layer=self.layer, engine=self.engine,
            parent=self.parent.id if self.parent else None, depth=self.depth,
            model=self.model, author=self.author, bounds=self.bounds.as_dict(),
            tools=self.toolbox.names, instructions=self.instructions_name,
            instructions_id=self.instructions_id, developer_instructions=instructions)
        self.state = "idle"
        return self.model, restrictions

    def _options(self, instructions):
        return ClaudeAgentOptions(
            model=self.model, system_prompt=instructions, cwd=str(TASK_DIR),
            env={**policy.CLAUDE_ENV, **self.conv.claude_env},
            setting_sources=[], strict_mcp_config=True, tools=[],
            mcp_servers={policy.MCP_SERVER: create_sdk_mcp_server(
                policy.MCP_SERVER, tools=[self._sdk_tool(t) for t in self.toolbox.tools])},
            allowed_tools=sorted(self._own), can_use_tool=self._can_use_tool,
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[self._pre_tool_use])]},
            include_partial_messages=True, verbatim_prompts=True, max_turns=MAX_MODEL_CALLS,
            extra_args={"no-session-persistence": None}, stderr=self._on_stderr)

    async def _drive(self, instructions):
        """The one task that holds this agent's client, from start to close."""
        self._turns = asyncio.Queue()
        try:
            async with ClaudeSDKClient(options=self._options(instructions)) as client:
                self._client = client
                self._evidence = await self._check_servers(client, when="start")
                info = await client.get_server_info() or {}
                account = info.get("account") if isinstance(info.get("account"), dict) else {}
                self._evidence["account"] = {k: account.get(k) for k in
                                             ("subscriptionType", "apiProvider") if k in account}
                self._ready.set()
                while True:
                    item = await self._turns.get()
                    if item is None:
                        return
                    text, future = item
                    try:
                        result = await self._turn(client, text)
                    except BaseException as error:  # the waiting thread re-raises it
                        future.set_exception(error)
                        if isinstance(error, asyncio.CancelledError):
                            raise
                    else:
                        future.set_result(result)
        except BaseException as error:
            if not self._ready.is_set():
                self._start_error = error
                self._ready.set()
            raise
        finally:
            self._client = None

    async def _check_servers(self, client, when):
        """Our one MCP server must be the only one, and connected."""
        status = await client.get_mcp_status()
        servers = [{"name": s.get("name"), "status": s.get("status")}
                   for s in (status or {}).get("mcpServers", [])]
        unexpected = [s["name"] for s in servers if s["name"] != policy.MCP_SERVER]
        if unexpected:
            self.record.write("restriction_failed", agent=self.id, when=when,
                              unexpected_mcp_servers=unexpected)
            self.interrupt(f"unexpected MCP servers {unexpected}")
        return {"mcp_servers": servers, "unexpected_mcp_servers": unexpected}

    def close(self):
        if self._driver is None:
            return None
        if self._turns is not None:
            self.conv.claude_loop.call(self._turns.put_nowait, None)
        try:
            self._driver.result(CLOSE_TIMEOUT)
        except concurrent.futures.TimeoutError:
            self._driver.cancel()
        except BaseException as error:  # a failed start or turn has been recorded already
            self.record.write("claude_closed", agent=self.id, error=repr(error))
        self._driver = None
        self.state = "closed" if self.state in ("idle", "running") else self.state
        return 0

    # ---- one turn ------------------------------------------------------------------------
    def _engine_turn(self, text, cap, idle):
        future = concurrent.futures.Future()
        self.conv.claude_loop.call(self._turns.put_nowait, (text, future))
        self.publish("turn_started", turn_id=None)
        interrupted, grace, reason = False, None, None
        while True:
            try:
                result = future.result(timeout=0.25)
            except concurrent.futures.TimeoutError:
                pass
            else:
                if interrupted:
                    result.update(interrupted_because=reason,
                                  status="interrupted" if result["status"] != "completed"
                                  else result["status"])
                return result
            if self.stop_requested() and not interrupted:
                interrupted, grace, reason = True, time.monotonic() + INTERRUPT_GRACE, self._stop_reason
                self.record.write("interrupt", agent=self.id, turn_id=None, reason=reason)
                self.publish("notice", text=f"Interrupting {self.id}'s turn: {reason}.")
                self.conv.claude_loop.submit(self._interrupt_client())
            deadline = grace if interrupted else self.deadline(cap, idle)
            if time.monotonic() >= deadline:
                if interrupted:
                    raise TurnTimeout(f"{self.id}'s turn did not end {INTERRUPT_GRACE} s after "
                                      "it was interrupted")
                self.interrupt(self.limit_reason(cap, idle))

    async def _interrupt_client(self):
        if self._client is not None:
            await self._client.interrupt()

    async def _turn(self, client, text):
        await client.query(text)
        result = None
        async for message in client.receive_response():
            self.touch()
            self._on_message(message)
            if isinstance(message, ResultMessage):
                result = message
        await self._check_servers(client, when="turn")
        return self._result(result)

    def _result(self, result):
        """The turn's outcome. ResultMessage is not taken at face value: a revoked login
        ends with subtype "success", is_error true and an api_error_status (peer finding)."""
        if result is None:
            return {"id": None, "status": "failed", "error": {"message": "no result message"}}
        usage = result.usage or {}
        self.record.write(
            "result", agent=self.id, session_id=result.session_id, subtype=result.subtype,
            is_error=result.is_error, api_error_status=result.api_error_status,
            num_turns=result.num_turns, duration_ms=result.duration_ms,
            duration_api_ms=result.duration_api_ms, stop_reason=result.stop_reason,
            terminal_reason=result.terminal_reason, total_cost_usd=result.total_cost_usd,
            usage=usage, model_usage=result.model_usage, errors=result.errors,
            permission_denials=result.permission_denials)
        tokens = {"inputTokens": usage.get("input_tokens", 0),
                  "cachedInputTokens": usage.get("cache_read_input_tokens", 0),
                  "cacheWriteInputTokens": usage.get("cache_creation_input_tokens", 0),
                  "outputTokens": usage.get("output_tokens", 0)}
        tokens["totalTokens"] = sum(tokens.values())
        previous = (self.stats["usage"] or {}).get("total") or {}
        total = {k: previous.get(k, 0) + v for k, v in tokens.items()}
        total["costUsd"] = result.total_cost_usd  # a running total already (peer finding)
        self.stats["usage"] = {"last": tokens, "total": total}
        self.publish("usage", last=tokens, total=total, context_window=None)
        failed = result.is_error or result.subtype != "success" or bool(result.api_error_status)
        error = None
        if failed:
            error = {"message": f"{result.subtype}; is_error {result.is_error}; api error "
                                f"{result.api_error_status}; {result.errors or result.result or ''}"[:500]}
        return {"id": result.session_id, "status": "failed" if failed else "completed",
                "error": error, "duration_ms": result.duration_ms}

    # ---- messages --------------------------------------------------------------------------
    def _on_message(self, message):
        if isinstance(message, StreamEvent):
            if message.parent_tool_use_id is None:
                self._on_stream_event(message.event or {})
        elif isinstance(message, AssistantMessage):
            self._on_assistant(message)
        elif isinstance(message, SystemMessage):
            self._on_system(message)
        elif isinstance(message, RateLimitEvent):
            info = message.rate_limit_info
            snapshot = {k: getattr(info, k, None) for k in
                        ("status", "resets_at", "rate_limit_type", "utilization",
                         "overage_status", "overage_resets_at")}
            self.record.write("rate_limits", agent=self.id, source="claude", snapshot=snapshot)
            self.publish("claude_rate_limits", **snapshot)

    def _on_stream_event(self, event):
        kind = event.get("type")
        if kind == "message_start":
            self._current_message = (event.get("message") or {}).get("id")
        elif kind == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta" and self._current_message:
                self._stream_fragment(self._current_message, delta.get("text", ""))

    def _on_assistant(self, message):
        if message.error:  # e.g. authentication_failed: the harness's words, not the model's
            self.record.write("api_error", agent=self.id, error=message.error, model=message.model)
            self.publish("error", message=f"{self.id}: {message.error}")
        texts = []
        for block in message.content or []:
            if isinstance(block, TextBlock):
                texts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                prefix = f"mcp__{policy.MCP_SERVER}__"
                name = block.name[len(prefix):] if block.name.startswith(prefix) else block.name
                self.model_call(name, kind="tool_use", call_id=block.id, payload=block.input)
            elif isinstance(block, ThinkingBlock):
                self.stats["thinking_blocks"] = self.stats.get("thinking_blocks", 0) + 1
        if message.usage:
            self.record.write("response_usage", agent=self.id, message_id=message.message_id,
                              usage=message.usage)
        if texts and message.error is None:
            self._message_done(message.message_id or self._current_message, "\n".join(texts))

    def _on_system(self, message):
        data = message.data or {}
        if message.subtype == "init":
            init = {k: data.get(k) for k in INIT_FIELDS if k in data}
            self.record.write("agent_init", agent=self.id, **init)
            others = sorted(set(data.get("tools") or []) - self._own)
            if others:
                self.record.write("restriction_failed", agent=self.id, when="init",
                                  unexpected_tools=others)
                self.interrupt(f"the CLI offered tools that are not ours: {others}")
        else:  # e.g. api_retry: kept whole unless it is large
            text = json.dumps(data, default=str)
            self.record.write("claude_system", agent=self.id, subtype=message.subtype,
                              data=json.loads(text) if len(text) <= 4000
                              else {"chars": len(text), "head": text[:1000]})

    def _on_stderr(self, line):
        self.record.write("stderr", agent=self.id, line=line.rstrip("\r\n")[:2000])

    # ---- our tools, and the gates ------------------------------------------------------------
    def _sdk_tool(self, tool):
        async def handle(args):
            success, output = await asyncio.to_thread(self.tool_call, tool.name, args)
            return {"content": [{"type": "text", "text": output}], "is_error": not success}
        return SdkMcpTool(name=tool.name, description=tool.description,
                          input_schema=tool.schema(), handler=handle)

    async def _pre_tool_use(self, hook_input, tool_use_id, context):
        name = hook_input.get("tool_name")
        if name in self._own:
            return {}
        self.record.write("tool_denied", agent=self.id, tool=name, by="PreToolUse hook")
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": "not a tool of this harness"}}

    async def _can_use_tool(self, name, tool_input, context):
        if name in self._own:
            return PermissionResultAllow()
        self.record.write("tool_denied", agent=self.id, tool=name, by="can_use_tool")
        return PermissionResultDeny(message="not a tool of this harness", interrupt=True)
