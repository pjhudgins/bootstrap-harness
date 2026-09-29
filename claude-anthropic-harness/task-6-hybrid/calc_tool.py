"""The python `add` tool, served to the agent from an in-process SDK MCP server.

The agent sees it as `mcp__calc__add`.
(From task-2-tooling/calc_tool.py; task 5 returns tool definitions, and the agent builds the servers.)
"""

from typing import Any

from claude_agent_sdk import tool

SERVER_NAME = "calc"


def add_numbers(a: Any, b: Any) -> int | float:
    """Pure sum with strict typing: numbers only (bool and numeric strings rejected)."""
    for name, v in (("a", a), ("b", b)):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise TypeError(f"{name} must be a number, got {type(v).__name__} {v!r}")
    return a + b


@tool("add", "Add two numbers and return their sum.", {"a": float, "b": float})
async def add(args: dict[str, Any]) -> dict[str, Any]:
    try:
        total = add_numbers(args.get("a"), args.get("b"))
    except TypeError as e:
        return {"content": [{"type": "text", "text": f"error: {e}"}], "is_error": True}
    return {"content": [{"type": "text", "text": str(total)}]}


def tools() -> list[Any]:
    return [add]
