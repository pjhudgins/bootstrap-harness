"""A fake Claude client for offline tests (peer review, 2026-09-25: tests stopped at the SDK boundary).

It stands in for ClaudeSDKClient through HarnessEnv.client_factory. For each tool call it
runs the harness's REAL hooks, permission callback and tool handlers, in the order the
CLI does:
    PreToolUse hook -> can_use_tool -> the tool handler -> PostToolUse hook
It yields the SDK's own message types: SystemMessage(init), AssistantMessage with
ToolUseBlock or TextBlock, UserMessage with ToolResultBlock, and ResultMessage. So the
recording, the checks, the onboarding gate and the tools are all exercised; only the model
and the CLI are missing.

Scripts:
  - top-level agents: FakeFactory(scripts={"pilot": [[steps for query 1], [steps for query 2]]});
  - subagents: steps are parsed from their instructions (the first message), one per line:
        tool: <tool name> <json input>
        say: <text>
A step is ("tool", name, input) or ("text", "...").
"""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultDeny,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

COST_PER_QUERY = 0.01


def parse_steps(text: str) -> list[tuple]:
    steps: list[tuple] = []
    for line in text.splitlines():
        if line.startswith("tool: "):
            name, _, arg = line[len("tool: "):].partition(" ")
            steps.append(("tool", name, json.loads(arg or "{}")))
        elif line.startswith("say: "):
            steps.append(("text", line[len("say: "):]))
    return steps


class FakeClient:
    def __init__(self, options: Any, core: Any, script: list[list[tuple]] | None = None,
                 init_tools: list[str] | None = None):
        self.options, self.core = options, core
        self.script = list(script) if script is not None else None  # one step list per query
        self.init_tools = init_tools  # to simulate a CLI that reports a different tool list
        self.queries: list[str] = []
        self.results: list[dict[str, Any]] = []  # every tool result, for assertions
        self.cost = 0.0
        self.interrupted = False

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def get_server_info(self) -> dict[str, Any]:
        return {"account": {"subscriptionType": "fake", "apiProvider": "fake", "email": "never-logged@example.com"}}

    async def query(self, text: str) -> None:
        self.queries.append(text)

    async def interrupt(self) -> None:
        self.interrupted = True

    async def get_mcp_status(self) -> dict[str, Any]:
        return {"mcpServers": [{"name": n, "scope": "dynamic", "status": "connected"} for n in self.options.mcp_servers]}

    async def get_context_usage(self) -> dict[str, Any]:
        return {"totalTokens": 10, "maxTokens": 200000, "categories": [{"name": "Messages", "tokens": 10}],
                "memoryFiles": [], "mcpTools": [{"name": t} for t in self.core.allowed_tools], "gridRows": [[1] * 999]}

    def _steps(self) -> list[tuple]:
        if self.script is None:
            return parse_steps(self.queries[-1])
        return self.script.pop(0) if self.script else []

    async def receive_response(self):
        tools = self.init_tools if self.init_tools is not None else sorted(self.core.allowed_tools)
        yield SystemMessage("init", {"tools": tools, "mcp_servers": [{"name": n, "status": "connected"}
                                                                     for n in self.options.mcp_servers]})
        n, last = 0, ""
        for step in self._steps():
            if step[0] == "text":
                last = step[1]
                yield AssistantMessage([TextBlock(step[1])], model=self.options.model)
                continue
            _, name, tool_input = step
            n += 1
            tool_use_id = f"toolu_fake_{self.core.agent_id}_{len(self.queries)}_{n}"
            yield AssistantMessage([ToolUseBlock(tool_use_id, name, tool_input)], model=self.options.model)
            result = await self._call(name, tool_input, tool_use_id)
            self.results.append({"tool": name, **result})
            yield UserMessage([ToolResultBlock(tool_use_id, result.get("content"), result.get("is_error"))])
        self.cost += COST_PER_QUERY
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=n + 1,
                            session_id="fake", total_cost_usd=self.cost, result=last or None,
                            usage={"input_tokens": 1, "output_tokens": 1}, model_usage={})

    async def _call(self, name: str, tool_input: dict[str, Any], tool_use_id: str) -> dict[str, Any]:
        hooks = self.options.hooks
        pre = await hooks["PreToolUse"][0].hooks[0]({"tool_name": name, "tool_input": tool_input}, tool_use_id, None)
        decision = (pre or {}).get("hookSpecificOutput") or {}
        if decision.get("permissionDecision") == "deny":
            return {"content": [{"type": "text", "text": decision["permissionDecisionReason"]}], "is_error": True}
        permission = await self.options.can_use_tool(name, tool_input, ToolPermissionContext(tool_use_id=tool_use_id))
        if isinstance(permission, PermissionResultDeny):
            return {"content": [{"type": "text", "text": permission.message}], "is_error": True}
        handler = self.core.tool_handlers.get(name)
        if handler is None:
            await hooks["PostToolUseFailure"][0].hooks[0](
                {"tool_name": name, "tool_input": tool_input, "error": "no such tool"}, tool_use_id, None)
            return {"content": [{"type": "text", "text": "no such tool"}], "is_error": True}
        out = await handler(tool_input)
        await hooks["PostToolUse"][0].hooks[0](
            {"tool_name": name, "tool_input": tool_input, "tool_response": out.get("content")}, tool_use_id, None)
        return out


class FakeFactory:
    """HarnessEnv.client_factory for tests. Keeps every client it made, keyed by agent id."""

    def __init__(self, scripts: dict[str, list[list[tuple]]] | None = None, init_tools: list[str] | None = None):
        self.scripts = scripts or {}
        self.init_tools = init_tools
        self.clients: dict[str, FakeClient] = {}

    def __call__(self, options: Any, core: Any) -> FakeClient:
        client = FakeClient(options, core, self.scripts.get(core.agent_id), self.init_tools)
        self.clients[core.agent_id] = client
        return client
