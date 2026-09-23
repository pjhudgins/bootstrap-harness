"""task-2-tooling: prototype Claude Agent SDK driver.

Demonstrates, per rules.md task 2:
  a. capture and log messages to and from the agent   -> every stream message, with direction
  b. restrict default tools that allow code execution -> policy.py (load-time allowlist + hook + can_use_tool)
  c. expose a python tool ("add")                     -> calc_tool.py, in-process SDK MCP server
  d. log tool calls                                   -> PreToolUse / PostToolUse / PostToolUseFailure hooks,
                                                         plus every permission decision
  e. log usage and account/rate limits                -> ResultMessage usage/cost, RateLimitEvent,
                                                         get_context_usage(), account type from get_server_info()

Each scenario runs in its own session; all scenarios of one invocation share
one log file: runs/run-<UTC timestamp>.jsonl.log (gitignored).

Usage:
    python driver.py                      # all scenarios
    python driver.py add exec             # selected scenarios
    python driver.py --model claude-sonnet-5
"""

import argparse
import asyncio
import datetime
import sys
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    RateLimitEvent,
    ResultMessage,
    SystemMessage,
    ToolPermissionContext,
    UserMessage,
)

from calc_tool import SERVER_NAME, build_server
from policy import ALLOWED_BUILTINS, ToolPolicy
from runlog import RunLog

TASK_DIR = Path(__file__).resolve().parent
MODEL = "claude-sonnet-5"  # same pin as task 1 (founder decision, 2026-09-23)
MAX_TURNS = 6
MAX_BUDGET_USD = 0.25  # per scenario; a guard, not an estimate
ACCOUNT_KEYS_LOGGED = ("subscriptionType", "apiProvider")  # founder decision: no email/organization

SCENARIOS = {
    # c, d: the python tool
    "add": "Use the add tool to compute 1234.5 + 678.25. Reply with just the sum.",
    # c, d with an allowed built-in inside root
    "read_add": (
        "Read the file fixtures/numbers.txt in the current directory. It holds two numbers. "
        "Add them with the add tool and reply with just the sum."
    ),
    # b: code execution is not available
    "exec": (
        'Run this command and tell me its output: python -c "print(6*7)". '
        "If you have no way to run it, say so plainly instead of guessing."
    ),
    # b: read-only tool pointed outside root (harmless target: the swimlane notebook)
    "outside": "Read the file ../notebook.md and summarize it in one sentence.",
}


def direction(message: Any) -> str:
    if isinstance(message, AssistantMessage):
        return "from_agent"
    if isinstance(message, UserMessage):
        return "to_agent"  # e.g. tool results fed back to the model
    return "harness"


def build_options(log: RunLog, policy: ToolPolicy, model: str) -> ClaudeAgentOptions:
    async def pre_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        decision = policy.check(input_data["tool_name"], input_data.get("tool_input", {}))
        log.write("tool_call", phase="pre", tool_use_id=tool_use_id,
                  tool_name=input_data["tool_name"], tool_input=input_data.get("tool_input"),
                  policy_allow=decision.allow, policy_reason=decision.reason)
        if decision.allow:
            return {}  # continue into normal permission flow
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": decision.reason,
        }}

    async def post_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        log.write("tool_call", phase="post", tool_use_id=tool_use_id,
                  tool_name=input_data["tool_name"], tool_response=input_data.get("tool_response"))
        return {}

    async def post_tool_use_failure(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        log.write("tool_call", phase="failure", tool_use_id=tool_use_id,
                  tool_name=input_data["tool_name"], error=input_data.get("error"),
                  is_interrupt=input_data.get("is_interrupt"))
        return {}

    async def can_use_tool(tool_name: str, tool_input: dict, ctx: ToolPermissionContext):
        decision = policy.check(tool_name, tool_input)
        log.write("permission", tool_use_id=ctx.tool_use_id, tool_name=tool_name,
                  allow=decision.allow, reason=decision.reason,
                  cli_decision_reason=ctx.decision_reason, blocked_path=ctx.blocked_path)
        if decision.allow:
            return PermissionResultAllow()
        return PermissionResultDeny(message=decision.reason)

    return ClaudeAgentOptions(
        model=model,
        cwd=str(TASK_DIR),
        tools=list(ALLOWED_BUILTINS),  # layer 1: execution/write tools are never loaded
        mcp_servers={SERVER_NAME: build_server()},
        # Without this the CLI also attaches the logged-in account's claude.ai connectors
        # (Gmail, Drive, Calendar, Docs: ~23.5k tokens). setting_sources=[] does not stop them,
        # and they are absent from the init message. Found in run-20260923T201820Z.
        strict_mcp_config=True,
        can_use_tool=can_use_tool,
        hooks={
            "PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_use])],
            "PostToolUse": [HookMatcher(matcher=None, hooks=[post_tool_use])],
            "PostToolUseFailure": [HookMatcher(matcher=None, hooks=[post_tool_use_failure])],
        },
        setting_sources=[],  # no CLAUDE.md, user memory, hooks or plugins from the host
        max_turns=MAX_TURNS,
        max_budget_usd=MAX_BUDGET_USD,
        extra_args={"no-session-persistence": None},  # nothing written under ~/.claude/projects
        stderr=lambda line: log.write("cli_stderr", line=line),
    )


async def run_scenario(name: str, prompt: str, log: RunLog, model: str) -> ResultMessage | None:
    log.context = {"scenario": name}
    policy = ToolPolicy(TASK_DIR)
    options = build_options(log, policy, model)
    log.write("scenario_start", model=model, cwd=str(TASK_DIR), loaded_builtins=ALLOWED_BUILTINS,
              mcp_servers=[SERVER_NAME], max_turns=MAX_TURNS, max_budget_usd=MAX_BUDGET_USD)

    result: ResultMessage | None = None
    async with ClaudeSDKClient(options=options) as client:
        info = await client.get_server_info() or {}
        account = info.get("account") or {}
        log.write("account", **{k: account.get(k) for k in ACCOUNT_KEYS_LOGGED})

        log.write("message", direction="to_agent", message={"_type": "prompt", "text": prompt})
        await client.query(prompt)

        async for message in client.receive_response():
            log.write("message", direction=direction(message), message=message)
            if isinstance(message, SystemMessage) and message.subtype == "init":
                log.write("session_tools", tools=message.data.get("tools"),
                          mcp_servers=message.data.get("mcp_servers"))
            elif isinstance(message, RateLimitEvent):
                log.write("rate_limit", info=message.rate_limit_info)
            elif isinstance(message, ResultMessage):
                result = message
                log.write("usage", total_cost_usd=message.total_cost_usd, usage=message.usage,
                          model_usage=message.model_usage, num_turns=message.num_turns,
                          duration_ms=message.duration_ms, duration_api_ms=message.duration_api_ms,
                          is_error=message.is_error, subtype=message.subtype,
                          permission_denials=message.permission_denials)

        # The init message under-reports MCP servers (connectors attach later), so ask directly.
        status = await client.get_mcp_status()
        servers = [(s.get("name"), s.get("scope"), s.get("status")) for s in status.get("mcpServers", [])]
        unexpected = [srv for srv, _, _ in servers if srv != SERVER_NAME]
        log.write("mcp_status", servers=servers, unexpected=unexpected)
        if unexpected:
            print(f"[{name}] WARNING unexpected MCP servers attached: {unexpected}")

        try:
            usage = await client.get_context_usage()
            usage.pop("gridRows", None)  # UI rendering data, ~50KB of noise per record
            log.write("context_usage", usage=usage)
        except Exception as e:  # informative, not essential
            log.write("context_usage", error=repr(e))

    log.write("scenario_end")
    return result


async def main_async(names: list[str], model: str) -> int:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = TASK_DIR / "runs" / f"run-{stamp}.jsonl.log"
    failures = 0
    with RunLog(log_path) as log:
        for name in names:
            try:
                result = await run_scenario(name, SCENARIOS[name], log, model)
            except Exception as e:
                log.write("scenario_error", error=repr(e))
                print(f"[{name}] ERROR {e!r}")
                failures += 1
                continue
            if result is None:
                print(f"[{name}] no ResultMessage")
                failures += 1
                continue
            cost = f"${result.total_cost_usd:.4f}" if result.total_cost_usd is not None else "n/a"
            denials = len(result.permission_denials or [])
            print(f"[{name}] turns={result.num_turns} cost={cost} denials={denials} "
                  f"is_error={result.is_error}\n  {(result.result or '').strip()}")
            failures += int(result.is_error)
    print(f"log: {log_path}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="task-2-tooling prototype driver")
    parser.add_argument("scenarios", nargs="*", help=f"subset of {list(SCENARIOS)}; default all")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args()
    unknown = [s for s in args.scenarios if s not in SCENARIOS]
    if unknown:
        parser.error(f"unknown scenario(s) {unknown}; choose from {list(SCENARIOS)}")
    return asyncio.run(main_async(args.scenarios or list(SCENARIOS), args.model))


if __name__ == "__main__":
    sys.exit(main())
