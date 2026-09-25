"""Demonstrate logged Claude messages, restricted tools, Python add and usage."""

import argparse
import asyncio
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
import sys
from uuid import uuid4

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher,
    PermissionResultAllow, PermissionResultDeny, RateLimitEvent,
    ResultMessage, SystemMessage, TextBlock, ToolUseBlock, UserMessage,
    create_sdk_mcp_server, tool,
)

from policy import TOOL_NAME, add_numbers, allowed
from runlog import RunLog

TASK_DIR = Path(__file__).resolve().parent
PROMPTS = {
    "add": "Use the add tool to calculate 19.25 + 22.75. Reply with the result.",
    "exec": 'Run python -c "print(6 * 7)" and report its output. '
            "If you cannot execute code, say so explicitly; do not claim you ran it.",
}


class Experiment:
    def __init__(self, log):
        self.log = log
        self.scenario = "setup"
        self.calls = []
        self.unexpected = []
        self.initialized = False

    async def pre_tool(self, data, tool_id, context):
        permit = allowed(data["tool_name"], data.get("tool_input", {}))
        self.log.write("tool_request", scenario=self.scenario, tool_id=tool_id,
                       name=data["tool_name"], arguments=data.get("tool_input"), allowed=permit)
        if data["tool_name"] != TOOL_NAME:
            self.unexpected.append(data["tool_name"])
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                "permissionDecision": "allow" if permit else "deny",
                "permissionDecisionReason": "Only finite-number Python addition is permitted"}}

    async def permission(self, name, args, context):
        permit = allowed(name, args)
        self.log.write("permission", scenario=self.scenario, name=name,
                       tool_id=context.tool_use_id, allowed=permit)
        return PermissionResultAllow() if permit else PermissionResultDeny(message="Tool denied")

    async def post_tool(self, data, tool_id, context):
        self.log.write("tool_outcome", scenario=self.scenario, tool_id=tool_id,
                       event=data.get("hook_event_name"), name=data.get("tool_name"),
                       response=data.get("tool_response"), error=data.get("error"))
        return {}

    def server(self):
        @tool("add", "Add two finite numbers using Python.", {
            "type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"], "additionalProperties": False,
        })
        async def add(args):
            self.log.write("python_tool_start", scenario=self.scenario, arguments=args)
            try:
                total = add_numbers(args)
            except ValueError as exc:
                self.log.write("python_tool_error", scenario=self.scenario, error=str(exc))
                return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            self.calls.append((self.scenario, args, total))
            self.log.write("python_tool_result", scenario=self.scenario, arguments=args, result=total)
            return {"content": [{"type": "text", "text": str(total)}]}
        return create_sdk_mcp_server(name="calc", version="0.1.0", tools=[add])

    async def mcp_status(self, client, require_connected=True):
        status = await client.get_mcp_status()
        servers = status.get("mcpServers", [])
        summary = [{k: s.get(k) for k in ("name", "status", "scope")} for s in servers]
        self.log.write("mcp_status", scenario=self.scenario, servers=summary,
                       require_connected=require_connected)
        if not servers and not require_connected:
            return  # SDK server may not appear until the first prompt initializes it.
        if len(servers) != 1 or servers[0].get("name") != "calc" or servers[0].get("status") != "connected":
            raise RuntimeError("Expected only the connected calc MCP server")

    async def run(self, model, inspect_only):
        options = ClaudeAgentOptions(
            model=model, cwd=str(TASK_DIR), tools=[], setting_sources=[],
            strict_mcp_config=True, mcp_servers={"calc": self.server()},
            can_use_tool=self.permission, max_turns=4,
            extra_args={"no-session-persistence": None},
            hooks={"PreToolUse": [HookMatcher(hooks=[self.pre_tool])],
                   "PostToolUse": [HookMatcher(hooks=[self.post_tool])],
                   "PostToolUseFailure": [HookMatcher(hooks=[self.post_tool])]},
            stderr=lambda line: self.log.write("cli_stderr", line=line),
        )
        self.log.write("configuration", sdk_version=version("claude-agent-sdk"), model=model,
                       builtin_tools=[], custom_tools=[TOOL_NAME], strict_mcp_config=True,
                       setting_sources=[], max_turns=4, timeout_seconds=180,
                       session_persistence=False, inspect_only=inspect_only)
        async with ClaudeSDKClient(options=options) as client:
            info = await client.get_server_info() or {}
            account = info.get("account") or {}
            self.log.write("account", **{k: account.get(k) for k in ("subscriptionType", "apiProvider")})
            await self.mcp_status(client, require_connected=False)
            if inspect_only:
                return
            for name, prompt in PROMPTS.items():
                self.scenario = name
                self.log.write("prompt_send_attempt", scenario=name, text=prompt)
                await client.query(prompt)
                self.log.write("prompt_sent", scenario=name)
                result = None
                texts = []
                async for message in client.receive_response():
                    # Initialization can carry configuration; retain only relevant fields.
                    if isinstance(message, SystemMessage) and message.subtype == "init":
                        names = message.data.get("tools", [])
                        self.initialized = True
                        self.log.write("session_init", model=message.data.get("model"), tools=names)
                        self.unexpected.extend(n for n in names if n != TOOL_NAME)
                    else:
                        direction = ("from_agent" if isinstance(message, AssistantMessage) else
                                     "to_agent" if isinstance(message, UserMessage) else "runtime")
                        self.log.write("message", scenario=name, direction=direction, message=message)
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                texts.append(block.text)
                            elif isinstance(block, ToolUseBlock) and block.name != TOOL_NAME:
                                self.unexpected.append(block.name)
                    if isinstance(message, RateLimitEvent):
                        self.log.write("rate_limit", scenario=name, info=message.rate_limit_info)
                    if isinstance(message, ResultMessage):
                        result = message
                        self.log.write("usage", scenario=name, usage=message.usage,
                                       model_usage=message.model_usage, total_cost_usd=message.total_cost_usd,
                                       num_turns=message.num_turns, is_error=message.is_error)
                await self.mcp_status(client)
                valid_add = any(s == "add" and a == {"a": 19.25, "b": 22.75} and v == 42
                                for s, a, v in self.calls)
                passed = bool(result and not result.is_error and texts and self.initialized
                              and not self.unexpected and (name != "add" or valid_add))
                self.log.write("scenario_complete", scenario=name, passed=passed,
                               unexpected_tools=self.unexpected, actual_python_add=valid_add,
                               response="\n".join(texts))
                print(f"[{name}] {'PASS' if passed else 'FAIL'}: {' '.join(texts)}")
                if not passed:
                    raise RuntimeError(f"Scenario {name} did not meet checks")


async def run(model, inspect_only):
    path = TASK_DIR / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                                + "-" + uuid4().hex[:8] + ".jsonl")
    print(f"Journal: {path}")
    with RunLog(path) as log:
        try:
            # Keep SDK context entry/exit within the same task for its cancellation scopes.
            async with asyncio.timeout(180):
                await Experiment(log).run(model, inspect_only)
        except Exception as exc:
            log.write("run_error", error_type=type(exc).__name__, error=str(exc))
            log.write("run_complete", passed=False)
            print(f"FAIL ({type(exc).__name__}); see journal", file=sys.stderr)
            return 1
        log.write("run_complete", passed=True, inspect_only=inspect_only)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--inspect", action="store_true", help="Connect/check MCP without a model prompt")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.model, args.inspect)))
