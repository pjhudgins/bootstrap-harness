"""The Claude backend: runs one agent through the Claude Agent SDK (task 5's AgentCore, split out in task 6).

Isolation recipe (tasks 2-5):
  - setting_sources=[] and strict_mcp_config=True;
  - CLAUDE_CODE_DISABLE_AUTO_MEMORY=1;
  - tools=[]: no built-ins; every tool is harness-owned;
  - --no-session-persistence.
Tools are the agent's ToolDefs, grouped into in-process MCP servers. The PreToolUse hook
and can_use_tool both call AgentCore.check, and the hooks record every call (pre, post,
failure).

The SDK client's context must be entered and exited by the same task (its anyio task group),
so `async with backend:` is used by the task that runs the agent: the session worker or the
spawn call.

Model rounds (task 6): the harness counts the model's responses in a turn (distinct
AssistantMessage.message_id) and interrupts the turn once they exceed core.max_turns. That
limit can be raised while the turn runs (governance.py, more_budget). The SDK's own
max_turns, fixed when the session starts, is only a tripwire at TURNS_CAP.
"""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    RateLimitEvent,
    ResultMessage,
    SystemMessage,
    ToolPermissionContext,
    UserMessage,
    create_sdk_mcp_server,
)

from agent import AgentCore, TurnResult, compact
from ledgerlog import to_jsonable

TURNS_CAP = 200  # the SDK's max_turns: a tripwire above any limit the harness sets


def direction(message: Any) -> str:
    if isinstance(message, AssistantMessage):
        return "from_agent"
    if isinstance(message, UserMessage):
        return "to_agent"  # e.g. tool results fed back to the model
    return "harness"


class ClaudeBackend:
    kind = "claude-agent-sdk"

    def __init__(self, core: AgentCore):
        self.core = core
        self.client: Any = None
        servers: dict[str, list[Any]] = {}
        for t in core.tool_defs:
            servers.setdefault(t.server, []).append(t.sdk_tool)
        self.mcp_servers = {s: create_sdk_mcp_server(name=s, version="0.1.0", tools=ts) for s, ts in servers.items()}

    async def __aenter__(self) -> "ClaudeBackend":
        self.client = self.core.env.client_factory(self.options(), self.core)
        await self.client.__aenter__()
        self.core.active = True
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        try:
            await self.client.__aexit__(*exc)
        finally:
            self.client = None
            self.core.active = False
        return False

    async def server_info(self) -> dict[str, Any]:
        return await self.client.get_server_info() or {}

    async def interrupt(self) -> None:
        if self.client is not None:
            await self.client.interrupt()

    # ---- a turn ---------------------------------------------------------------------------

    async def run_turn(self, text: str, *, author: str, link: str | None = None,
                       link_id: str | None = None) -> TurnResult:
        core = self.core
        core.record_prompt(text, author=author, link=link, link_id=link_id)
        result: ResultMessage | None = None
        rounds: set[str] = set()
        limited = False
        await self.client.query(text)
        async for message in self.client.receive_response():
            core.pub("message", direction=direction(message), message=self._message_body(message))
            if isinstance(message, AssistantMessage) and message.message_id:
                rounds.add(message.message_id)
                if len(rounds) > core.max_turns and not limited:
                    limited = True
                    core.pub("turn_limit", reason="max_turns", rounds=len(rounds), max_turns=core.max_turns)
                    asyncio.ensure_future(self.client.interrupt())
            if isinstance(message, SystemMessage) and message.subtype == "init":
                self._check_inventory(message.data)
            elif isinstance(message, RateLimitEvent):
                core.pub("rate_limit", info=message.rate_limit_info)
            elif isinstance(message, ResultMessage):
                result = message
                core.record_usage(
                    cumulative_usd=message.total_cost_usd, num_turns=message.num_turns, is_error=message.is_error,
                    subtype=message.subtype, usage=message.usage, model_usage=message.model_usage,
                    duration_ms=message.duration_ms, duration_api_ms=message.duration_api_ms,
                    permission_denials=message.permission_denials,
                    # subtype can say "success" on an API failure (task 4 finding), so keep these too.
                    api_error_status=message.api_error_status, stop_reason=message.stop_reason,
                    errors=message.errors,
                    result=(core.link_for(message.result) or message.result) if message.is_error else None)
        await self._record_session_facts()
        if result is None:
            return TurnResult(True, "no result", core.last_text)
        if limited:
            return TurnResult(True, "max_turns", core.last_text)
        return TurnResult(result.is_error, result.subtype, core.last_text)

    def _message_body(self, message: Any) -> Any:
        """The stream message as JSON, with agent text replaced by wikilinks to its own entries."""
        core = self.core
        body = to_jsonable(message)
        if isinstance(message, AssistantMessage):
            for block in body.get("content") or []:
                if block.get("_type") == "ToolUseBlock" and block.get("name") not in core.allowed_tools:
                    core.pub("unexpected_tool_use", tool_name=block.get("name"), tool_use_id=block.get("id"))
                if block.get("_type") == "TextBlock" and block.get("text"):
                    block["text"] = core.record_agent_text(block["text"])
        elif isinstance(message, ResultMessage) and core.link_for(message.result):
            body["result"] = core.link_for(message.result)
        return body

    def _check_inventory(self, data: dict[str, Any]) -> None:
        core = self.core
        reported = set(data.get("tools") or [])
        core.pub("session_tools", tools=sorted(reported), mcp_servers=data.get("mcp_servers"))
        if reported != set(core.allowed_tools):
            core.pub("tool_inventory_mismatch", expected=sorted(core.allowed_tools), reported=sorted(reported),
                     missing=sorted(set(core.allowed_tools) - reported), extra=sorted(reported - set(core.allowed_tools)))

    async def _record_session_facts(self) -> None:
        core = self.core
        # The init message under-reports MCP servers (connectors attach later); ask directly.
        status = await self.client.get_mcp_status()
        servers = [(s.get("name"), s.get("scope"), s.get("status")) for s in status.get("mcpServers", [])]
        core.pub("mcp_status", servers=servers,
                 unexpected=[srv for srv, _, _ in servers if srv not in self.mcp_servers])
        try:
            u = await self.client.get_context_usage()
            core.pub("context_usage", usage={  # a summary: the full report is ~10 KB per turn
                "totalTokens": u.get("totalTokens"), "maxTokens": u.get("maxTokens"),
                "categories": [{"name": c.get("name"), "tokens": c.get("tokens")} for c in u.get("categories", [])],
                "memoryFiles": u.get("memoryFiles"),  # how the auto-memory leak was found (task 4)
                "mcpTools": sorted({t.get("name") for t in u.get("mcpTools") or []})})
        except Exception as e:  # informative, not essential
            core.pub("context_usage", error=repr(e))

    # ---- SDK options ------------------------------------------------------------------

    def options(self) -> ClaudeAgentOptions:
        core = self.core

        async def pre_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
            decision = core.check(input_data["tool_name"], input_data.get("tool_input", {}))
            core.pub("tool_call", phase="pre", tool_use_id=tool_use_id, tool_name=input_data["tool_name"],
                     tool_input=input_data.get("tool_input"), policy_allow=decision.allow,
                     policy_reason=decision.reason)
            if decision.allow:
                return {}  # continue into normal permission flow
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": decision.reason}}

        async def post_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
            core.pub("tool_call", phase="post", tool_use_id=tool_use_id, tool_name=input_data["tool_name"],
                     tool_response=compact(input_data.get("tool_response")))
            return {}

        async def post_tool_use_failure(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
            core.pub("tool_call", phase="failure", tool_use_id=tool_use_id, tool_name=input_data["tool_name"],
                     error=input_data.get("error"), is_interrupt=input_data.get("is_interrupt"))
            return {}

        # Extension point: a UI approval step would await a user decision here.
        async def can_use_tool(tool_name: str, tool_input: dict, ctx: ToolPermissionContext):
            decision = core.check(tool_name, tool_input)
            core.pub("permission", tool_use_id=ctx.tool_use_id, tool_name=tool_name, allow=decision.allow,
                     reason=decision.reason, cli_decision_reason=ctx.decision_reason, blocked_path=ctx.blocked_path)
            if decision.allow:
                return PermissionResultAllow()
            return PermissionResultDeny(message=decision.reason)

        return ClaudeAgentOptions(
            model=core.model,
            cwd=str(core.env.root),
            system_prompt=core.system_prompt,
            tools=[],  # no built-in tools at all: every tool is harness-owned (files.py says why)
            mcp_servers=self.mcp_servers,
            strict_mcp_config=True,  # keep account claude.ai connectors and nimoi/.mcp.json out
            can_use_tool=can_use_tool,
            hooks={
                "PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_use])],
                "PostToolUse": [HookMatcher(matcher=None, hooks=[post_tool_use])],
                "PostToolUseFailure": [HookMatcher(matcher=None, hooks=[post_tool_use_failure])],
            },
            setting_sources=[],  # no CLAUDE.md, hooks or plugins from nimoi or the user
            # Auto memory loads regardless of setting_sources (task 4 finding); documented off switch.
            env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
            max_turns=TURNS_CAP,  # the harness enforces core.max_turns itself (see the module docstring)
            max_budget_usd=core.budget_usd,  # SDK tripwire; the harness's own checks are the real stop
            extra_args={"no-session-persistence": None},
            stderr=lambda line: core.pub("cli_stderr", line=line),
        )
