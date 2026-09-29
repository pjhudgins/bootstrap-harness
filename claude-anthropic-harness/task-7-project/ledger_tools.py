"""Agent tools for the ledger, served in-process as MCP server "ledger".

    mcp__ledger__read    one name, or one exact revision by id: body, author, labels
    mcp__ledger__list    names, filtered by prefix and/or label
    mcp__ledger__write   create a name, or update one the agent owns (prev = current id)

Each guard carries its agent's Bounds (task 5):
  - read, list, read_entry and read_line see only names within ledger.read;
  - write needs ledger.write as well as every rule below.

Write restrictions (rules.md task 4c), all in LedgerGuard.write:
  - names under log/ are the harness's, and are refused;
  - a name carrying the `harness` label is refused, whoever wrote it;
  - an existing name may be updated only if its current author is the agent;
  - the agent cannot apply the label `harness` or any label starting with `log.` or `agent.`.
    There is no untag tool, so protection cannot be stripped first. The reservation is exact
    (peer review: `harness-notes` must stay usable as a topic label);
  - no null bodies: the agent cannot delete.

Authorship is set here, never taken from the model (scribe interfaces.md, rule 1).
Every agent-written name is tagged with the agent's role (`pilot`; task 6: `governor`, `owner`,
`subagent`) and `agent.<id>`.
"""

from __future__ import annotations

import json
import re
from typing import Any

from claude_agent_sdk import tool

from bounds import Bounds
from ledgerlog import AGENT_LABEL_PREFIX, KIND_LABEL_PREFIX, LOG_PREFIX, PROTECTED_LABEL

SERVER_NAME = "ledger"
AGENT_LABEL = "pilot"
LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")  # scribe grammar minus reserved leading _
ID_RE = re.compile(r"[0-9]{8}T[0-9]{6}Z:[1-9][0-9]*")
MAX_CHARS_DEFAULT = 20000
LIST_LIMIT_MAX = 500


class Denied(Exception):
    """A guard or tool refused; nothing was written."""


def label_reserved(label: str) -> bool:
    return label == PROTECTED_LABEL or label.startswith((KIND_LABEL_PREFIX, AGENT_LABEL_PREFIX))


def denied_reply(bus: Any, agent_id: str, tool_name: str, args: dict[str, Any], reason: Any) -> dict[str, Any]:
    """A refusal by a tool's own check. It is recorded as `tool_denied`, so refusals are first-class
    in the ledger, then returned to the agent as an error."""
    if bus is not None:
        bus.publish("tool_denied", agent=agent_id, tool_name=tool_name, tool_input=args, reason=str(reason))
    return {"content": [{"type": "text", "text": f"error: denied: {reason}"}], "is_error": True}


def _entry(e: Any) -> dict[str, Any]:
    return {"id": e.id, "ts": e.ts, "author": e.author, "prev": e.prev, "body": e.body}


class LedgerGuard:
    def __init__(self, scribe_module: Any, ledger: Any, agent_author: str, bounds: Bounds,
                 agent_id: str = "pilot", role_label: str = AGENT_LABEL):
        self._sm = scribe_module
        self.ledger = ledger
        self.agent_author = agent_author
        self.bounds = bounds
        self.agent_id = agent_id
        self.role_label = role_label

    def _readable(self, name: Any) -> None:
        if not isinstance(name, str) or not name:
            raise Denied("name must be a non-empty string")
        if not self.bounds.permits("ledger.read", name):
            raise Denied(f"{name!r} is outside your ledger.read "
                         f"({self.bounds.scope('ledger.read').render() or 'nothing'})")

    def read_entry(self, name: Any) -> Any:
        """The current entry of a readable, bound name."""
        self._readable(name)
        current = self.ledger.current(name)
        if current is None:
            raise Denied(f"{name!r} has never been written")
        return current

    def read_line(self, entry_id: Any) -> dict[str, Any]:
        """One exact body revision by id, pinned: a later revision cannot change it."""
        if not isinstance(entry_id, str) or not ID_RE.fullmatch(entry_id):
            raise Denied(f"{entry_id!r} is not a ledger id (like 20260925T192147Z:12)")
        line = self.ledger.line(entry_id)
        if line is None or "body" not in line:
            raise Denied(f"{entry_id} is not a body revision in this ledger")
        self._readable(line["name"])
        if line["body"] is None:
            raise Denied(f"{entry_id} is a deletion; it has no body")
        return {"id": entry_id, "name": line["name"], "author": line["author"], "ts": line["ts"],
                "prev": line.get("prev"), "body": line["body"]}

    def read(self, name: Any = None, history: bool = False, entry_id: Any = None) -> dict[str, Any]:
        if entry_id:
            line = self.read_line(entry_id)
            current = self.ledger.current(line["name"])
            return {"ledger": self.ledger.ledger, "name": line["name"], "revision": line,
                    "is_current": current is not None and current.id == entry_id,
                    "labels": sorted(self.ledger.labels(line["name"]))}
        self._readable(name)
        current = self.ledger.current(name)
        out: dict[str, Any] = {"ledger": self.ledger.ledger, "name": name,
                               "labels": sorted(self.ledger.labels(name))}
        if current is None:
            out["bound"] = False
            return out
        out.update(bound=True, deleted=current.body is None, current=_entry(current))
        if history:
            out["history"] = [_entry(e) for e in self.ledger.history(name)]
        return out

    def list(self, prefix: str = "", label: str | None = None, limit: int = 100) -> dict[str, Any]:
        limit = max(1, min(int(limit), LIST_LIMIT_MAX))
        names = self.ledger.labelled(label) if label else self.ledger.names()
        if label:
            bound = set(self.ledger.names())
            names = [n for n in names if n in bound]
        names = [n for n in names if n.startswith(prefix) and self.bounds.permits("ledger.read", n)]
        rows = []
        for n in names[:limit]:
            e = self.ledger.current(n)
            rows.append({"name": n, "id": e.id, "author": e.author, "labels": sorted(self.ledger.labels(n))})
        return {"ledger": self.ledger.ledger, "session": self.ledger.session, "total": len(names),
                "returned": len(rows), "truncated": len(names) > limit, "names": rows}

    def write(self, name: Any, body: Any, prev: str | None = None, labels: Any = ()) -> dict[str, Any]:
        if not isinstance(name, str) or not name:
            raise Denied("name must be a non-empty string")
        if name == LOG_PREFIX.rstrip("/") or name.startswith(LOG_PREFIX):
            raise Denied(f"names under {LOG_PREFIX} belong to the harness")
        if not self.bounds.permits("ledger.write", name):
            raise Denied(f"{name!r} is outside your ledger.write "
                         f"({self.bounds.scope('ledger.write').render() or 'nothing'})")
        if body is None:
            raise Denied("null bodies (deletes) are not available to the agent")
        labels = list(labels or [])
        for label in labels:
            if not isinstance(label, str) or not LABEL_RE.fullmatch(label):
                raise Denied(f"label {label!r} is outside the label grammar")
            if label_reserved(label):
                raise Denied(f"label {label!r} is reserved for the harness")
        existing = self.ledger.labels(name)
        if PROTECTED_LABEL in existing:
            raise Denied(f"{name!r} carries the {PROTECTED_LABEL!r} label and is protected")
        current = self.ledger.current(name)
        if current is not None and current.author != self.agent_author:
            raise Denied(f"{name!r} was last written by {current.author!r}; the agent may only update its own entries")
        try:
            entry_id = self.ledger.write(name, body, author=self.agent_author, prev=prev)
        except self._sm.Refused as e:
            raise Denied(f"scribe refused ({e.code}): {e}") from e
        applied = []
        for label in [self.role_label, AGENT_LABEL_PREFIX + self.agent_id, *labels]:
            if label not in existing and label not in applied:
                self.ledger.tag(name, label, author=self.agent_author)
                applied.append(label)
        return {"written": entry_id, "name": name, "author": self.agent_author,
                "labels": sorted(self.ledger.labels(name))}


def tools_for(guard: LedgerGuard, bus: Any = None) -> list[Any]:
    """The ledger tools the guard's bounds allow (none if neither ledger scope is set)."""
    can_read = bool(guard.bounds.scope("ledger.read").allow)
    can_write = bool(guard.bounds.scope("ledger.write").allow)

    def ok(data: dict[str, Any], max_chars: int = MAX_CHARS_DEFAULT) -> dict[str, Any]:
        text = json.dumps(data, ensure_ascii=False, indent=1)
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n...[clipped at {max_chars} characters; raise max_chars to see more]"
        return {"content": [{"type": "text", "text": text}]}

    async def run(name: str, args: dict[str, Any], fn: Any) -> dict[str, Any]:
        try:
            return fn()
        except Denied as e:
            return denied_reply(bus, guard.agent_id, f"mcp__{SERVER_NAME}__{name}", args, e)
        except Exception as e:
            return {"content": [{"type": "text", "text": f"error: {e!r}"}], "is_error": True}

    @tool("read", "Read one ledger name, or one exact revision by `id`. For a name: its current body, id, "
          "author and labels; set history for every earlier body. `bound` is true when the name has ever "
          "been written (it then has a `current` entry); labels alone do not bind a name. `deleted` is true "
          "when the current body is a deletion (null). `current.prev` is the id that write replaced (null "
          "for a first write). For an id: that revision, and whether it is still current.",
          {"type": "object", "properties": {
              "name": {"type": "string"}, "id": {"type": "string"},
              "history": {"type": "boolean", "default": False},
              "max_chars": {"type": "integer", "default": MAX_CHARS_DEFAULT}}})
    async def read(args: dict[str, Any]) -> dict[str, Any]:
        return await run("read", args, lambda: ok(
            guard.read(args.get("name"), bool(args.get("history", False)), args.get("id")),
            int(args.get("max_chars", MAX_CHARS_DEFAULT))))

    @tool("list", "List ledger names, optionally filtered by name prefix and/or label. Harness records are "
          "under log/<session>/ and carry the labels 'harness', 'log.<kind>' and 'agent.<id>'. Message text "
          "is its own entry labelled 'log.text' (body = the text, author = its writer); the 'message' record "
          "that follows links to it as [[name]].",
          {"type": "object", "properties": {
              "prefix": {"type": "string", "default": ""}, "label": {"type": "string"},
              "limit": {"type": "integer", "default": 100}}})
    async def list_(args: dict[str, Any]) -> dict[str, Any]:
        return await run("list", args, lambda: ok(
            guard.list(args.get("prefix", ""), args.get("label"), args.get("limit", 100))))

    @tool("write", "Write a JSON body to a ledger name. A new name needs no prev; updating a name you wrote "
          "needs prev = its current id. You cannot write under log/, write names labelled 'harness', or "
          f"apply the labels 'harness', 'log.*' or 'agent.*'. Your entries are labelled '{guard.role_label}' and "
          "'agent.<your id>'; extra labels are optional. Returns the new revision's id.",
          {"type": "object", "properties": {
              "name": {"type": "string"}, "body": {"description": "any JSON value except null"},
              "prev": {"type": "string"}, "labels": {"type": "array", "items": {"type": "string"}}},
           "required": ["name", "body"]})
    async def write(args: dict[str, Any]) -> dict[str, Any]:
        return await run("write", args, lambda: ok(
            guard.write(args.get("name"), args.get("body"), args.get("prev"), args.get("labels"))))

    return ([read, list_] if can_read else []) + ([write] if can_write else [])
