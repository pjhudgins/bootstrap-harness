"""The governance tools (rules.md 6c, 6f).

The governor's: start a task owner on a ticket, message it, see its state, close it; list
the task owners' requests and answer them. The governor grants up to its own bounds;
anything beyond goes to the human (founder, 2026-09-28).
A task owner's: ask the governor for something it may not do, and wait for the answer.
The request blocks the task owner: the call returns when it is decided, or after a
while as "pending", and governor_wait waits again.
"""

import bounds as bd
import policy
from toolkit import ToolError, string, tool

GOVERNOR = ("governor",)
OWNERS = ("task-owner",)
MAX_WAIT_SECONDS = 300
REQUEST_KINDS = {
    "bounds": "wider bounds, in the bounds notation (only what the task needs)",
    "promote_script": "a script you drafted in the workspace copied into the scripts "
                      "folder, so it can run (only a human can approve this)",
    "question": "a question the governor should answer",
    "other": "any other action you may not take yourself",
}
DECISIONS = ("grant", "refuse", "answer", "ask_human")
BOUNDS_SCHEMA = {"type": "object", "description": "the five keys, each a list of entries",
                 "properties": {k: {"type": "array", "items": {"type": "string"}} for k in bd.KEYS},
                 "additionalProperties": False}


def _seconds(args, default=0):
    seconds = args.get("seconds", default)
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) \
            or not 0 <= seconds <= MAX_WAIT_SECONDS:
        raise ToolError(f"seconds must be between 0 and {MAX_WAIT_SECONDS}")
    return seconds


def _text(args, key):
    value = args[key]
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{key} must be a non-empty string")
    return value


# ---- the governor's ----------------------------------------------------------------------
@tool("owner_start",
      "Start a task owner on a ticket. Write the ticket to the ledger first, under tickets/, "
      "with a Bar: line and everything the task owner needs; pass its name. The version "
      "current now is the one it gets. Give it a model (" + ", ".join(policy.LAYER_MODELS["task-owner"])
      + ") and bounds inside yours, in the same notation. It runs in parallel and works until "
      "you close it; its reports and requests come to you. Returns its id at once.",
      {"model": string(), "bounds": BOUNDS_SCHEMA, "ticket": string("ledger entry name")},
      ("model", "bounds", "ticket"), layers=GOVERNOR)
def owner_start(box, args):
    model, requested, ticket = args["model"], args["bounds"], args["ticket"]
    if not isinstance(model, str) or not isinstance(ticket, str):
        raise ToolError("model and ticket must be strings")
    if not isinstance(requested, dict):
        raise ToolError("bounds must be an object with the five keys")
    box.need_entry(ticket)
    entry = box.agent.scribe.current(ticket)
    if entry is None or not isinstance(entry.body, str) or not entry.body.strip():
        raise ToolError(f"{ticket!r} has no text body: write the ticket to the ledger first")
    child = bd.child_bounds(box.agent.bounds, requested)
    return box.agent.conv.start_owner(box.agent, model, child, ticket, entry.id)


@tool("owner_message",
      "Send one of your task owners a message: a follow-up, a correction, or more work on "
      "its ticket. It gets it as its next turn, at once if it is idle.",
      {"owner": string("task owner id"), "text": string()}, ("owner", "text"), layers=GOVERNOR)
def owner_message(box, args):
    return box.agent.conv.message_owner(box.agent, _text(args, "owner"), _text(args, "text"))


@tool("owner_status",
      f"A task owner's state and latest report, waiting up to `seconds` (0 = just look, at "
      f"most {MAX_WAIT_SECONDS}) for its current turn to end. Without `owner`: all of yours.",
      {"owner": string(), "seconds": {"type": "number"}}, layers=GOVERNOR)
def owner_status(box, args):
    owner = args.get("owner")
    if owner is not None and not isinstance(owner, str):
        raise ToolError("owner must be a task owner id")
    return box.agent.conv.owner_status(box.agent, owner, _seconds(args))


@tool("owner_close",
      "Close a task owner when its ticket is done or abandoned: its turn, if one is "
      "running, is stopped, and it gets no more messages.",
      {"owner": string(), "reason": string()}, ("owner", "reason"), layers=GOVERNOR)
def owner_close(box, args):
    return box.agent.conv.close_owner(box.agent, _text(args, "owner"), _text(args, "reason"))


@tool("request_list", "Your task owners' requests to you; `state` filters: pending, "
      "asked_human, decided.", {"state": string()}, layers=GOVERNOR)
def request_list(box, args):
    state = args.get("state")
    if state not in (None, "pending", "asked_human", "decided"):
        raise ToolError("state is pending, asked_human or decided")
    return box.agent.conv.list_requests(state)


@tool("request_answer",
      "Decide a task owner's request. decision: grant (bounds requests: pass `bounds`, the "
      "part you grant, inside your own bounds), refuse, answer (questions and other "
      "requests: your answer in `message`), or ask_human (anything beyond your own bounds, "
      "such as promoting a script: the human decides on the page; say what you recommend "
      "in `message`). Assess safety and governance only; the task's quality is the task "
      "owner's.",
      {"request": string("request id"), "decision": {"type": "string", "enum": list(DECISIONS)},
       "message": string(), "bounds": BOUNDS_SCHEMA},
      ("request", "decision", "message"), layers=GOVERNOR)
def request_answer(box, args):
    decision = args["decision"]
    if decision not in DECISIONS:
        raise ToolError(f"decision is one of {list(DECISIONS)}")
    bounds = args.get("bounds")
    if bounds is not None and not isinstance(bounds, dict):
        raise ToolError("bounds must be an object with the five keys")
    return box.agent.conv.answer_request(box.agent, _text(args, "request"), decision,
                                         _text(args, "message"), bounds)


# ---- the task owner's ----------------------------------------------------------------------
@tool("governor_request",
      "Ask the governor for something you may not do yourself, with your justification. "
      "kind: " + "; ".join(f"{k} = {v}" for k, v in REQUEST_KINDS.items()) + ". For bounds, "
      "pass `bounds`; for promote_script, `from_path` (your draft in the workspace) and "
      "`to_path` (in the scripts folder). You are blocked until it is decided: the call "
      f"waits up to `seconds` (default {MAX_WAIT_SECONDS}) and returns the decision, or "
      "'pending' — then call governor_wait. Do not work around a refusal.",
      {"kind": {"type": "string", "enum": list(REQUEST_KINDS)}, "justification": string(),
       "bounds": BOUNDS_SCHEMA, "from_path": string(), "to_path": string(),
       "seconds": {"type": "number"}},
      ("kind", "justification"), layers=OWNERS)
def governor_request(box, args):
    kind = args["kind"]
    if kind not in REQUEST_KINDS:
        raise ToolError(f"kind is one of {list(REQUEST_KINDS)}")
    details = {}
    if kind == "bounds":
        if not isinstance(args.get("bounds"), dict):
            raise ToolError("a bounds request needs `bounds`, in the bounds notation")
        details["bounds"] = bd.Bounds(args["bounds"]).as_dict()
    if kind == "promote_script":
        for key in ("from_path", "to_path"):
            if not isinstance(args.get(key), str) or not args[key]:
                raise ToolError("a promote_script request needs from_path and to_path")
        details.update(from_path=args["from_path"], to_path=args["to_path"])
    seconds = _seconds(args, MAX_WAIT_SECONDS)
    return box.agent.conv.make_request(box.agent, kind, _text(args, "justification"),
                                       details, seconds)


@tool("governor_wait",
      f"Wait up to `seconds` (at most {MAX_WAIT_SECONDS}) for the decision on one of your "
      "requests. The wait ends early if your turn is being stopped.",
      {"request": string(), "seconds": {"type": "number"}}, ("request",), layers=OWNERS)
def governor_wait(box, args):
    return box.agent.conv.wait_request(box.agent, _text(args, "request"),
                                       _seconds(args, MAX_WAIT_SECONDS))
