# Copied from ../task-2-tooling/tools.py on 2026-09-24. Changes made here belong to task 3.
"""Python tools offered to the Codex agent as app-server dynamic tools (experimental API)."""

import json
import math

ADD_SPEC = {
    "type": "function",
    "name": "add",
    "description": 'Add two numbers. Returns JSON {"sum": <number>}.',
    "inputSchema": {
        "type": "object",
        "properties": {
            "a": {"type": "number", "description": "first addend"},
            "b": {"type": "number", "description": "second addend"},
        },
        "required": ["a", "b"],
        "additionalProperties": False,
    },
}


class ToolInputError(ValueError):
    pass


def add(arguments):
    if not isinstance(arguments, dict) or set(arguments) != {"a", "b"}:
        raise ToolInputError(f"expected exactly the arguments a and b, got {arguments!r}")
    for key in ("a", "b"):
        value = arguments[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolInputError(f"{key} must be a number, got {value!r}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ToolInputError(f"{key} must be finite, got {value!r}")
    try:
        total = arguments["a"] + arguments["b"]
    except OverflowError:
        raise ToolInputError("the sum is too large to represent") from None
    if isinstance(total, float) and not math.isfinite(total):
        raise ToolInputError("the sum is not finite")
    return {"sum": total}


TOOLS = {"add": (ADD_SPEC, add)}
SPECS = [spec for spec, _ in TOOLS.values()]


def call(tool, namespace, arguments):
    """Run one tool call; returns (success, output_text). Never raises for bad input."""
    if namespace is not None or tool not in TOOLS:
        return False, f"unknown tool {namespace + '.' if namespace else ''}{tool}"
    if isinstance(arguments, str):  # tolerate arguments sent as a JSON string
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return False, f"arguments are not valid JSON: {arguments!r}"
    try:
        return True, json.dumps(TOOLS[tool][1](arguments))
    except ToolInputError as error:
        return False, str(error)
