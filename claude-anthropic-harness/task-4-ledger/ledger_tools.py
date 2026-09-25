"""Agent tools for the ledger, served in-process as MCP server "ledger".

    mcp__ledger__read    one name: current body, id, author, labels; optional history
    mcp__ledger__list    names, filtered by prefix and/or label
    mcp__ledger__write   create a name, or update one the agent owns (prev = current id)

Write restrictions (rules.md task 4c), all in LedgerGuard.write:
  - names under log/ are the harness's; refused;
  - a name carrying the `harness` label is refused, whoever wrote it;
  - an existing name may be updated only if its current author is the agent;
  - labels starting with `harness` or `log.` cannot be applied by the agent;
    there is no untag tool, so protection cannot be stripped first;
  - no null bodies: the agent cannot delete.
Authorship is set here, never taken from the model (scribe interfaces.md, rule 1).
Every agent-written name is tagged `pilot`.
"""

import json
import re
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from ledgerlog import KIND_LABEL_PREFIX, LOG_PREFIX, PROTECTED_LABEL

SERVER_NAME = "ledger"
TOOL_NAMES = tuple(f"mcp__{SERVER_NAME}__{t}" for t in ("read", "list", "write"))
AGENT_LABEL = "pilot"
PROTECTED_LABEL_PREFIXES = (PROTECTED_LABEL, KIND_LABEL_PREFIX)
LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")  # scribe grammar minus reserved leading _
MAX_CHARS_DEFAULT = 20000
LIST_LIMIT_MAX = 500


class Denied(Exception):
    """The guard refused; nothing was written."""


def _entry(e: Any) -> dict[str, Any]:
    return {"id": e.id, "ts": e.ts, "author": e.author, "prev": e.prev, "body": e.body}


def _clip(value: Any, max_chars: int) -> tuple[str, bool]:
    text = json.dumps(value, ensure_ascii=False, indent=1)
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


class LedgerGuard:
    def __init__(self, scribe_module: Any, ledger: Any, agent_author: str):
        self._sm = scribe_module
        self.ledger = ledger
        self.agent_author = agent_author

    def read(self, name: str, history: bool = False) -> dict[str, Any]:
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
        names = [n for n in names if n.startswith(prefix)]
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
        if body is None:
            raise Denied("null bodies (deletes) are not available to the agent")
        labels = list(labels or [])
        for label in labels:
            if not isinstance(label, str) or not LABEL_RE.fullmatch(label):
                raise Denied(f"label {label!r} is outside the label grammar")
            if label.startswith(PROTECTED_LABEL_PREFIXES):
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
        for label in [AGENT_LABEL, *labels]:
            if label not in existing and label not in applied:
                self.ledger.tag(name, label, author=self.agent_author)
                applied.append(label)
        return {"written": entry_id, "name": name, "author": self.agent_author,
                "labels": sorted(self.ledger.labels(name))}


def build_server(guard: LedgerGuard):
    def ok(data: dict[str, Any], max_chars: int = MAX_CHARS_DEFAULT) -> dict[str, Any]:
        text, clipped = _clip(data, max_chars)
        if clipped:
            text += f"\n...[clipped at {max_chars} characters; raise max_chars to see more]"
        return {"content": [{"type": "text", "text": text}]}

    def err(message: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": f"error: {message}"}], "is_error": True}

    @tool("read", "Read one ledger name: its current body, id, author and labels. "
          "Set history to include every earlier body.",
          {"type": "object", "properties": {
              "name": {"type": "string"},
              "history": {"type": "boolean", "default": False},
              "max_chars": {"type": "integer", "default": MAX_CHARS_DEFAULT}},
           "required": ["name"]})
    async def read(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return ok(guard.read(args["name"], bool(args.get("history", False))),
                      int(args.get("max_chars", MAX_CHARS_DEFAULT)))
        except Exception as e:
            return err(repr(e))

    @tool("list", "List ledger names, optionally filtered by name prefix and/or label. "
          "Harness log entries are under log/<session>/ and carry the labels 'harness' and 'log.<kind>'.",
          {"type": "object", "properties": {
              "prefix": {"type": "string", "default": ""},
              "label": {"type": "string"},
              "limit": {"type": "integer", "default": 100}}})
    async def list_(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return ok(guard.list(args.get("prefix", ""), args.get("label"), args.get("limit", 100)))
        except Exception as e:
            return err(repr(e))

    @tool("write", "Write a JSON body to a ledger name. Creating a new name needs no prev; updating a name "
          "you wrote earlier needs prev = its current id (from read). You cannot write under log/, "
          "write names labelled 'harness', or apply labels starting with 'harness' or 'log.'. "
          "Your entries are tagged 'pilot'; extra labels are optional.",
          {"type": "object", "properties": {
              "name": {"type": "string"},
              "body": {"description": "any JSON value except null"},
              "prev": {"type": "string"},
              "labels": {"type": "array", "items": {"type": "string"}}},
           "required": ["name", "body"]})
    async def write(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return ok(guard.write(args.get("name"), args.get("body"), args.get("prev"), args.get("labels")))
        except Denied as e:
            return err(f"denied: {e}")
        except Exception as e:
            return err(repr(e))

    return create_sdk_mcp_server(name=SERVER_NAME, version="0.1.0", tools=[read, list_, write])
