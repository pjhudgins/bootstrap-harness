"""The subagent tools: start a harness-owned subagent, and wait for its report.

Offered to agents below the nesting cap. The parent writes the instructions to the
ledger first and passes the entry's name; the harness pins the version current at spawn,
and the subagent's first message links to that exact version (rules.md 5e).
"""

import bounds as bd
from toolkit import ToolError, string, tool

MAX_WAIT_SECONDS = 300


@tool("subagent_spawn",
      "Start a subagent, owned by the harness, that runs in parallel with you. First write "
      "its instructions to the ledger (with a Bar: line) and pass that entry's name; the "
      "version current now is the one it gets. Give it a model and bounds in the same "
      "notation as yours: they must lie inside your bounds, and fs.write must stay out of "
      "fs.exec folders. Returns its id at once; use subagent_wait to get its report.",
      {"model": string(),
       "bounds": {"type": "object", "description": "the five keys, each a list of entries",
                  "properties": {k: {"type": "array", "items": {"type": "string"}}
                                 for k in bd.KEYS}, "additionalProperties": False},
       "instructions": string("ledger entry name")},
      ("model", "bounds", "instructions"), spawns=True)
def subagent_spawn(box, args):
    model, requested, instructions = args["model"], args["bounds"], args["instructions"]
    if not isinstance(model, str) or not isinstance(instructions, str):
        raise ToolError("model and instructions must be strings")
    if not isinstance(requested, dict):
        raise ToolError("bounds must be an object with the five keys")
    box.need_entry(instructions)
    entry = box.agent.scribe.current(instructions)
    if entry is None or not isinstance(entry.body, str) or not entry.body.strip():
        raise ToolError(f"{instructions!r} has no text body: write the instructions to the "
                        "ledger first")
    child = bd.child_bounds(box.agent.bounds, requested)
    return box.agent.spawn(model, child, instructions, entry.id)


@tool("subagent_wait",
      f"Get one of your subagents' state, waiting up to `seconds` (0 = just look, at most "
      f"{MAX_WAIT_SECONDS}) for it to finish. A finished subagent returns its final report. "
      "The wait ends early if your own turn is being stopped.",
      {"subagent": string(), "seconds": {"type": "number"}}, ("subagent",), spawns=True)
def subagent_wait(box, args):
    subagent, seconds = args["subagent"], args.get("seconds", 0)
    if not isinstance(subagent, str) or isinstance(seconds, bool) \
            or not isinstance(seconds, (int, float)) or not 0 <= seconds <= MAX_WAIT_SECONDS:
        raise ToolError(f"subagent must be an id; seconds between 0 and {MAX_WAIT_SECONDS}")
    return box.agent.wait(subagent, seconds)
