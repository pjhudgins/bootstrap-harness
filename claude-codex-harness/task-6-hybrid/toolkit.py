# From ../task-5-subagent/toolkit.py. Task 6: tools are offered by layer.
"""How an agent's tools are declared, offered and called.

A tool is a function handler(box, args), declared once with @tool: its name, its
description, its parameters, the layers it is offered to, and for a subagent the bounds
key that must be non-empty. A Toolbox is one agent's set of tools:
- the governor gets its governance tools and reading tools, never file writes, scripts
  or subagents (it does not do task owners' work);
- a task owner gets every tool of its layer from the start, since the governor can
  widen its bounds later; its bounds decide each call;
- a subagent gets the tools its fixed bounds make usable (task 5).
The Toolbox checks each call's arguments against the tool's schema; keeps every tool but
fs_list and fs_read shut until the agent has read the onboarding file to its last line;
and turns a refusal into a failed result.

The rules the tools enforce are stated for agents in prompts/bounds.md.
"""

import json
import math
from pathlib import Path

import bounds as bd
import ledger_log
import paths

TOOLS = {}  # name -> Tool, in declaration order (the order agents see them in)
ALWAYS_OPEN = ("fs_list", "fs_read")  # usable before the onboarding has been read
LAYERS = ("governor", "task-owner", "subagent")
WORKERS = ("task-owner", "subagent")


class ToolError(ValueError):
    """A refused or malformed call: the message is the tool's failed result."""


class Tool:
    def __init__(self, name, handler, description, params, required, layers, needs):
        self.name, self.handler, self.description = name, handler, description
        self.params, self.required = params, tuple(required)
        self.layers, self.needs = tuple(layers), needs

    def schema(self):
        return {"type": "object", "properties": self.params, "required": list(self.required),
                "additionalProperties": False}

    def spec(self):
        """The dynamic-tool declaration Codex offers the model."""
        return {"type": "function", "name": self.name, "description": self.description,
                "inputSchema": self.schema()}


def tool(name, description, params=None, required=(), *, layers=LAYERS, needs=None):
    """Declare a tool, offered to `layers`; to a subagent only if its bounds key `needs`
    is non-empty."""
    def declare(handler):
        TOOLS[name] = Tool(name, handler, description, params or {}, required, layers, needs)
        return handler
    return declare


def offered(tool_, layer, bounds):
    return layer in tool_.layers and (layer != "subagent" or tool_.needs is None
                                      or bool(bounds[tool_.needs]))


def string(description=None):
    return {"type": "string", **({"description": description} if description else {})}


class Toolbox:
    """The tools of one agent. `agent` supplies: id, author, layer, bounds (read at each
    call: a grant takes effect at once), record, scribe, conv (whose methods the
    governance and subagent tools call), instructions_name, produced (names its own
    calls created), onboarding (a relative path), onboarding_done(),
    note_onboarding_lines(start, end, total)."""

    def __init__(self, agent, *, fs_root, workspace, scripts):
        self.agent = agent
        self.paths = paths.PathPolicy(fs_root)
        self.workspace = Path(workspace).resolve()
        self.scripts_rel = self.paths.rel(Path(scripts).resolve())
        self.tools = [t for t in TOOLS.values() if offered(t, agent.layer, agent.bounds)]
        self.names = [t.name for t in self.tools]
        self.specs = [t.spec() for t in self.tools]
        self._by_name = {t.name: t for t in self.tools}

    # -- dispatch ----------------------------------------------------------------------
    def call(self, name, namespace, arguments):
        """Run one tool call; returns (success, output_text). A ledger failure is not a
        tool result: it propagates and stops the conversation."""
        found = self._by_name.get(name) if namespace is None else None
        if found is None:
            return False, f"unknown tool {namespace + '.' if namespace else ''}{name}"
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return False, f"arguments are not valid JSON: {arguments!r}"
        if not isinstance(arguments, dict):
            return False, f"arguments must be an object, got {arguments!r}"
        unexpected = sorted(set(arguments) - set(found.params))
        if unexpected:
            return False, f"unexpected arguments {unexpected} for {name}"
        missing = [key for key in found.required if key not in arguments]
        if missing:
            return False, f"missing arguments {missing} for {name}"
        if not self.agent.onboarding_done() and name not in ALWAYS_OPEN:
            return False, (f"refused: read the latest NIMOI onboarding first, to its last "
                           f"line: fs_read {{\"path\": \"{self.agent.onboarding}\"}}")
        try:
            return True, json.dumps(found.handler(self, arguments), ensure_ascii=False)
        except (ToolError, bd.BoundsError, paths.PathRefused) as error:
            return False, str(error)
        except ledger_log.LedgerFailure:
            raise
        except OSError as error:
            return False, f"filesystem error: {error.strerror or error!r}"
        except Exception as error:  # a bug in one tool must not end the conversation
            return False, f"internal error in {name}: {error!r}"

    # -- checks shared by the tools ------------------------------------------------------
    def need(self, key, item, why=""):
        """Refuse unless the agent's bounds allow `item` under `key`."""
        if not self.agent.bounds.allows(key, item):
            raise ToolError(f"refused: {item or '.'!r} is outside your {key} "
                            f"{list(self.agent.bounds[key])}{why}")

    def can_read_entry(self, name):
        """ledger.read (or ledger.write) covers it, it is the agent's own instructions
        entry, or the agent's own tool calls produced it."""
        agent = self.agent
        return (agent.bounds.allows("ledger.read", name) or name == agent.instructions_name
                or name in agent.produced)

    def need_entry(self, name):
        if not isinstance(name, str):
            raise ToolError("an entry name must be a string")
        if not self.can_read_entry(name):
            self.need("ledger.read", name)

    def where_scripts_run(self, rel):
        """Is `rel` inside the scripts folder, or inside the folder of one of this agent's
        fs.exec entries?"""
        folders = [self.scripts_rel + "/", *map(bd.folder_of, self.agent.bounds["fs.exec"])]
        return any(bd.covers("fs.exec", folder, rel) for folder in folders)

    def can_write_near(self, rel):
        """Could this agent write anywhere in the folder `rel` runs from?"""
        folder = bd.folder_of(rel)
        return any(bd.overlaps("fs.write", w, folder) for w in self.agent.bounds["fs.write"])


# ---- add (task 2) -----------------------------------------------------------------------
@tool("add", 'Add two numbers. Returns JSON {"sum": <number>}.',
      {"a": {"type": "number"}, "b": {"type": "number"}}, ("a", "b"))
def add(box, args):
    for key in ("a", "b"):
        value = args[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolError(f"{key} must be a number, got {value!r}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ToolError(f"{key} must be finite, got {value!r}")
    try:
        total = args["a"] + args["b"]
    except OverflowError:
        raise ToolError("the sum is too large to represent") from None
    if isinstance(total, float) and not math.isfinite(total):
        raise ToolError("the sum is not finite")
    return {"sum": total}
