"""The ledger tools: list, read, write and label entries within the agent's ledger bounds.

The ledger is append-only. Writing a name again supersedes its current entry, whoever
wrote it, and the ledger keeps every version with its author. Founder, 2026-09-25: who
may supersede whom is doctrine still to be developed, not enforced here beyond the write
bounds and the protected names and labels. Agents never write the harness's names
(harness/, transcript/, exec/) or labels (log.*, transcript*, exec*), and never a
key-like string.
"""

import json
import re

import ledger_log
from ledger_log import scribe
from toolkit import ToolError, string, tool

AGENT_LABEL = "agent"            # on every name an agent writes
MAX_BODY_CHARS = 20000
MAX_READ_CHARS = 20000
LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
REFUSAL_HELP = {
    "bound": "the name already exists: read it and pass its current id as prev",
    "stale_prev": "the name changed since you read it: read it again and cite its current id",
    "unbound": "the name does not exist yet: omit prev to create it",
    "bad_name": "names are 1-256 ASCII chars: segments of letters, digits, '.', '_', '-' "
                "separated by '/'",
    "reserved_name": "names under _ledger/ are reserved",
}


def entry_view(entry, max_chars=MAX_READ_CHARS):
    body = entry.body if isinstance(entry.body, str) or entry.body is None \
        else json.dumps(entry.body, ensure_ascii=False)
    truncated = body is not None and len(body) > max_chars
    return {"id": entry.id, "ts": entry.ts, "author": entry.author,
            "body": body[:max_chars] if truncated else body,
            "body_truncated": truncated, "deleted": entry.body is None}


def _refused(error):
    help_text = REFUSAL_HELP.get(error.code, "")
    return ToolError(f"ledger refused ({error.code}){': ' + help_text if help_text else ''}")


def _protected(box, name):
    return (ledger_log.is_protected_name(name)
            or any(ledger_log.is_protected_label(x) for x in box.agent.scribe.labels(name)))


def _no_keys(*texts):
    if any(ledger_log.has_key_like(text) for text in texts):
        raise ToolError("refused: the text holds a key-like string (an API key or token); "
                        "keys never go in the ledger")


@tool("ledger_list",
      "List ledger entry names you can read, all chats. Harness records (harness/) are "
      "hidden unless include_harness is true.",
      {"prefix": string(), "label": string(), "include_harness": {"type": "boolean"},
       "limit": {"type": "integer", "description": "default 100"}})
def ledger_list(box, args):
    prefix, label = args.get("prefix", ""), args.get("label")
    limit, include_harness = args.get("limit", 100), args.get("include_harness", False)
    if not isinstance(prefix, str) or not (label is None or isinstance(label, str)):
        raise ToolError("prefix and label must be strings")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ToolError("limit must be a positive integer")
    s = box.agent.scribe
    names = s.labelled(label) if label else s.names()
    names = [n for n in names if n.startswith(prefix) and box.can_read_entry(n)
             and (include_harness or not n.startswith(ledger_log.HARNESS_PREFIX))]
    return {"total": len(names), "names": names[:limit], "truncated": len(names) > limit,
            "ledger": s.ledger, "session": s.session}


@tool("ledger_read",
      "Read a ledger entry you can read: its current version's body, id, author, time and "
      "labels; with history=true, every earlier version too.",
      {"name": string(), "history": {"type": "boolean"}}, ("name",))
def ledger_read(box, args):
    name = args["name"]
    box.need_entry(name)
    s = box.agent.scribe
    current = s.current(name)
    if current is None:
        return {"name": name, "exists": False, "labels": sorted(s.labels(name))}
    result = {"name": name, "exists": True, "labels": sorted(s.labels(name)),
              "current": entry_view(current)}
    if args.get("history"):
        result["history"] = [entry_view(e, 2000) for e in s.history(name)]
    return result


@tool("ledger_write",
      "Write a text entry under a name inside your ledger.write. Omit prev to create a new "
      "name. To supersede a name's current entry (yours or another author's), pass prev = "
      "its current id; the ledger keeps every version and its author. Entries are "
      "attributed to you and labelled 'agent'.",
      {"name": string(), "body": string(), "prev": string(),
       "labels": {"type": "array", "items": {"type": "string"}}},
      ("name", "body"), needs="ledger.write")
def ledger_write(box, args):
    name, body, prev = args["name"], args["body"], args.get("prev")
    labels = args.get("labels") or []
    if not isinstance(name, str) or not isinstance(body, str) \
            or not (prev is None or isinstance(prev, str)):
        raise ToolError("name, body and prev must be strings")
    if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
        raise ToolError("labels must be a list of strings")
    if len(body) > MAX_BODY_CHARS:
        raise ToolError(f"body is over {MAX_BODY_CHARS} characters")
    if _protected(box, name):
        raise ToolError("refused: harness-owned entries (harness/, transcript/, exec/, or "
                        "protected labels) are never written by agents")
    box.need("ledger.write", name)
    bad = [x for x in labels if ledger_log.is_protected_label(x) or not LABEL_RE.fullmatch(x)]
    if bad:
        raise ToolError(f"refused labels {bad}: protected, reserved or malformed")
    _no_keys(name, body, *labels)
    s, author = box.agent.scribe, box.agent.author
    try:
        entry_id = s.write(name, body, author=author, prev=prev)
        for label in dict.fromkeys([AGENT_LABEL, *labels]):
            if label not in s.labels(name):
                s.tag(name, label, author=author)
    except scribe.Refused as error:
        raise _refused(error) from None
    return {"id": entry_id, "name": name, "author": author, "labels": sorted(s.labels(name))}


@tool("ledger_tag",
      "Add, or with remove=true remove, a label on a name inside your ledger.write.",
      {"name": string(), "label": string(), "remove": {"type": "boolean"}},
      ("name", "label"), needs="ledger.write")
def ledger_tag(box, args):
    name, label, remove = args["name"], args["label"], bool(args.get("remove"))
    if not isinstance(name, str) or not isinstance(label, str):
        raise ToolError("name and label must be strings")
    if _protected(box, name) or ledger_log.is_protected_label(label):
        raise ToolError("refused: harness-owned entries and protected labels")
    box.need("ledger.write", name)
    if not LABEL_RE.fullmatch(label):
        raise ToolError(f"refused label {label!r}: reserved or malformed")
    _no_keys(label)
    s = box.agent.scribe
    try:
        entry_id = (s.untag if remove else s.tag)(name, label, author=box.agent.author)
    except scribe.Refused as error:
        raise _refused(error) from None
    return {"id": entry_id, "name": name, "labels": sorted(s.labels(name))}
