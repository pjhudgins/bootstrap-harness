"""Only the calculator is permitted; no execution or filesystem tools."""

import math

TOOL_NAME = "mcp__calc__add"


def add_numbers(args):
    if not isinstance(args, dict) or set(args) != {"a", "b"}:
        raise ValueError("Expected exactly two fields: a and b")
    for value in args.values():
        if type(value) not in (int, float):
            raise ValueError("Operands must be finite numbers, not bools or strings")
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError("Operands must be finite numbers")
    total = args["a"] + args["b"]
    try:
        finite = math.isfinite(total)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("Sum must be finite")
    return total


def allowed(name, args):
    if name != TOOL_NAME:
        return False
    try:
        add_numbers(args)
    except ValueError:
        return False
    return True
