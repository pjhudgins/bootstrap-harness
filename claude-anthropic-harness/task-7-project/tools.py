"""One tool registry for both model backends (task 6).

Each harness tool is defined once, in the module that owns it (files.py, exec_tool.py,
ledger_tools.py, calc_tool.py, subagents.py, governance.py), as an SDK tool object:
name, description, schema and an async handler returning {"content": [...], "is_error"?}.
ToolDef wraps one for a given server, so the same tool can be served:
  - to a Claude agent, as an in-process MCP server (backend_claude.py);
  - to a GPT agent, as a Codex dynamic tool (backend_codex.py).
The allowlist, the checks and the records are the same whichever backend runs the agent
(agent.AgentCore).

Every tool's full name is mcp__<server>__<name> for both backends, so records, prompts,
the allowlist and audits read the same for Claude and GPT agents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

_PY_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


def json_schema(schema: Any) -> dict[str, Any]:
    """A full JSON schema from either form the SDK's @tool accepts: a JSON schema already
    (it has "type"), or a simple {"field": python_type} mapping (every field required)."""
    if isinstance(schema, dict) and "type" in schema:
        return schema
    props = {k: {"type": _PY_TYPES.get(v, "string")} for k, v in (schema or {}).items()}
    return {"type": "object", "properties": props, "required": list(props)}


@dataclass(frozen=True)
class ToolDef:
    server: str
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    sdk_tool: Any = None  # the original SDK tool object, for the Claude backend's MCP servers

    @property
    def full_name(self) -> str:
        return f"mcp__{self.server}__{self.name}"

    @classmethod
    def from_sdk(cls, server: str, sdk_tool: Any) -> "ToolDef":
        return cls(server=server, name=sdk_tool.name, description=sdk_tool.description,
                   input_schema=json_schema(sdk_tool.input_schema), handler=sdk_tool.handler, sdk_tool=sdk_tool)


def result_text(result: dict[str, Any]) -> str:
    """The text of a tool result (MCP content items), for backends that return plain text."""
    parts = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(parts)
