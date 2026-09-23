"""The python `add` tool, served to the agent from an in-process SDK MCP server.

The agent sees it as `mcp__calc__add`.
"""

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

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


def build_server():
    return create_sdk_mcp_server(name=SERVER_NAME, version="0.1.0", tools=[add])
