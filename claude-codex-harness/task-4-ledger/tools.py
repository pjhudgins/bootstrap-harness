# Task 4 version of ../task-3-ui/tools.py: `add` is unchanged; the ledger and filesystem
# tools and the Toolbox are new.
"""Python tools offered to the Codex agent as app-server dynamic tools (experimental API).

- add: sum of two numbers (task 2).
- ledger_list, ledger_read: read the harness's ledger, including earlier chats.
- ledger_write, ledger_tag: the agent's only way to write. Entries are attested to the
  agent's author; nothing named `harness/`, nothing tagged `harness`, and no protected
  label (`harness`, `log.*`) can be written, tagged or untagged by the agent.
- fs_list, fs_read: read-only, bounded to the nimoi folder. Secret files and `.git` are
  refused and key-like strings are redacted, because every result is sent to the model
  and recorded in the ledger (rules.md, Secrets).
Every tool returns (success, text); none raises on bad input.
"""

import fnmatch
import json
import math
import re
from pathlib import Path

import ledger_log
from ledger_log import scribe

# ---- add (task 2) ---------------------------------------------------------------------

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


def _spec(name, description, properties, required=()):
    return {"type": "function", "name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties,
                            "required": list(required), "additionalProperties": False}}


# ---- ledger tools -------------------------------------------------------------------

AGENT_LABEL = "pilot"            # added to every name the agent writes
MAX_BODY_CHARS = 20000           # a body the agent writes
MAX_READ_CHARS = 20000           # one body returned by ledger_read
LEDGER_SPECS = [
    _spec("ledger_list",
          "List entry names in your ledger (this harness's ledger, all chats). Harness log "
          "entries (names under harness/) are hidden unless include_harness is true.",
          {"prefix": {"type": "string", "description": "only names starting with this"},
           "label": {"type": "string", "description": "only names carrying this label"},
           "include_harness": {"type": "boolean"},
           "limit": {"type": "integer", "description": "at most this many names (default 100)"}}),
    _spec("ledger_read",
          "Read one ledger entry: its current body, id, author, time and labels; "
          "optionally its full history.",
          {"name": {"type": "string"}, "history": {"type": "boolean"}}, ["name"]),
    _spec("ledger_write",
          "Write a text entry to your ledger. To create a new name, omit prev. To update an "
          "existing name, pass prev = the id its current entry has (read it first). You may "
          "not write names under harness/ or names tagged harness. Your entries are "
          "attributed to you automatically and tagged 'pilot'.",
          {"name": {"type": "string", "description": "segments of letters, digits, . _ - "
                                                     "separated by /, e.g. pilot/observations"},
           "body": {"type": "string"},
           "prev": {"type": "string", "description": "current id of the name, when updating"},
           "labels": {"type": "array", "items": {"type": "string"},
                      "description": "extra labels to tag it with"}},
          ["name", "body"]),
    _spec("ledger_tag",
          "Add (or with remove=true, remove) a label on a ledger name. Harness entries and "
          "the labels harness and log.* are protected.",
          {"name": {"type": "string"}, "label": {"type": "string"},
           "remove": {"type": "boolean"}}, ["name", "label"]),
]
REFUSAL_HELP = {
    "bound": "the name already exists: read it with ledger_read and pass its current id as prev",
    "stale_prev": "the name changed since you read it: read it again and cite its current id",
    "unbound": "the name does not exist yet: omit prev to create it",
    "bad_name": "names are 1-256 ASCII chars: segments of letters, digits, '.', '_', '-' "
                "separated by '/'",
    "reserved_name": "names under _ledger/ are reserved",
    "bad_label": "labels are 1-64 chars of letters, digits, '.', '_', '-'",
    "reserved_label": "labels starting with _ are reserved",
}


# wiki_ledger_v0.4 §3.1 label grammar, minus reserved labels (leading _).
LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def is_protected_label(label):
    return label == ledger_log.HARNESS_LABEL or label.startswith(ledger_log.LOG_LABEL_PREFIX)


# ---- filesystem tools ---------------------------------------------------------------

MAX_FILE_BYTES = 5_000_000
MAX_FS_CHARS = 30000
MAX_LIST = 500
FS_SPECS = [
    _spec("fs_list",
          "List a directory inside the nimoi folder (read-only). Paths are relative to the "
          "nimoi folder; '.' is the nimoi folder itself.",
          {"path": {"type": "string"}}),
    _spec("fs_read",
          "Read a text file inside the nimoi folder (read-only). Returns up to max_lines lines "
          "starting at start_line (1-based), within a size cap. Secret files are refused.",
          {"path": {"type": "string"},
           "start_line": {"type": "integer"}, "max_lines": {"type": "integer"}},
          ["path"]),
]
# From bootstrap-harness/.gitignore's secrets section, plus the usual key stores.
SECRET_PATTERNS = [".env", ".env.*", "*.env", "*.token", "*.key", "*.pem", "*.p12", "*.pfx",
                   "credentials*.json", "secrets*.json", "auth.json", "id_rsa*", "id_ed25519*",
                   ".netrc", ".npmrc", ".pypirc"]
SECRET_ALLOWED = [".env.example"]
KEY_LIKE = re.compile("|".join([
    r"sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}",
    r"gh[pousr]_[A-Za-z0-9]{30,}",
    r"github_pat_[A-Za-z0-9_]{30,}",
    r"xox[abprs]-[A-Za-z0-9-]{10,}",
    r"AKIA[0-9A-Z]{16}",
    r"AIza[0-9A-Za-z_-]{35}",
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
]))


def redact_keys(text):
    return KEY_LIKE.subn("<redacted: key-like string>", text)


class Toolbox:
    """All dynamic tools for one conversation: bound to its scribe, the agent's attested
    author and the filesystem root."""

    def __init__(self, ledger, agent_author, fs_root=ledger_log.NIMOI_ROOT):
        self.ledger = ledger
        self.agent_author = agent_author
        self.fs_root = Path(fs_root).resolve()
        self.handlers = {"add": add, "ledger_list": self.ledger_list,
                         "ledger_read": self.ledger_read, "ledger_write": self.ledger_write,
                         "ledger_tag": self.ledger_tag, "fs_list": self.fs_list,
                         "fs_read": self.fs_read}
        self.specs = [ADD_SPEC, *LEDGER_SPECS, *FS_SPECS]
        self.names = [spec["name"] for spec in self.specs]
        self.properties = {spec["name"]: spec["inputSchema"]["properties"] for spec in self.specs}

    def call(self, tool, namespace, arguments):
        """Run one tool call; returns (success, output_text). Never raises for bad input."""
        if namespace is not None or tool not in self.handlers:
            return False, f"unknown tool {namespace + '.' if namespace else ''}{tool}"
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return False, f"arguments are not valid JSON: {arguments!r}"
        if not isinstance(arguments, dict):
            return False, f"arguments must be an object, got {arguments!r}"
        # Undeclared arguments are refused, not ignored: an `author` sent by the model must
        # visibly fail rather than look accepted (the harness names every author itself).
        unexpected = sorted(set(arguments) - set(self.properties[tool]))
        if unexpected:
            return False, f"unexpected arguments {unexpected} for {tool}"
        try:
            return True, json.dumps(self.handlers[tool](arguments), ensure_ascii=False)
        except ToolInputError as error:
            return False, str(error)
        except KeyError as error:
            return False, f"missing argument {error} for {tool}"
        except OSError as error:
            return False, f"filesystem error: {error.strerror or error!r}"
        # scribe.WriteFailed and anything unexpected propagate: the harness must stop,
        # not carry on as if the ledger were intact (spec §5.4).

    # -- ledger ------------------------------------------------------------------------
    def _protected_name(self, name):
        return (name == "harness" or name.startswith("harness/")
                or any(is_protected_label(label) for label in self.ledger.labels(name)))

    def _refused(self, error):
        help_text = REFUSAL_HELP.get(error.code, "")
        return ToolInputError(f"ledger refused ({error.code}){': ' + help_text if help_text else ''}")

    @staticmethod
    def _entry(entry, max_chars=MAX_READ_CHARS):
        body = entry.body if isinstance(entry.body, str) or entry.body is None \
            else json.dumps(entry.body, ensure_ascii=False)
        truncated = body is not None and len(body) > max_chars
        return {"id": entry.id, "ts": entry.ts, "author": entry.author,
                "body": body[:max_chars] if truncated else body,
                "body_truncated": truncated, "deleted": entry.body is None}

    def ledger_list(self, args):
        prefix, label = args.get("prefix") or "", args.get("label")
        limit = args.get("limit") or 100
        if not isinstance(limit, int) or limit < 1:
            raise ToolInputError("limit must be a positive integer")
        names = self.ledger.labelled(label) if label else self.ledger.names()
        names = [n for n in names if n.startswith(prefix)
                 and (args.get("include_harness") or not n.startswith("harness/"))]
        return {"total": len(names), "names": names[:limit],
                "truncated": len(names) > limit, "ledger": self.ledger.ledger,
                "session": self.ledger.session, "sessions": len(self.ledger.sessions)}

    def ledger_read(self, args):
        name = args["name"]
        if not isinstance(name, str):
            raise ToolInputError("name must be a string")
        current = self.ledger.current(name)
        if current is None:
            return {"name": name, "exists": False, "labels": sorted(self.ledger.labels(name))}
        result = {"name": name, "exists": True, "labels": sorted(self.ledger.labels(name)),
                  "current": self._entry(current)}
        if args.get("history"):
            result["history"] = [self._entry(e, 2000) for e in self.ledger.history(name)]
        return result

    def ledger_write(self, args):
        name, body, prev = args["name"], args["body"], args.get("prev")
        labels = args.get("labels") or []
        if not isinstance(name, str) or not isinstance(body, str):
            raise ToolInputError("name and body must be strings")
        if len(body) > MAX_BODY_CHARS:
            raise ToolInputError(f"body is over {MAX_BODY_CHARS} characters")
        if self._protected_name(name):
            raise ToolInputError("refused: harness entries (names under harness/ or tagged "
                                 "harness) cannot be written by the agent")
        if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
            raise ToolInputError("labels must be a list of strings")
        bad = [x for x in labels if is_protected_label(x)]
        if bad:
            raise ToolInputError(f"refused: protected labels {bad}")
        # Check labels before writing, so a bad label cannot leave the body half-tagged.
        invalid = [x for x in labels if not LABEL_RE.fullmatch(x)]
        if invalid:
            raise ToolInputError(f"invalid labels {invalid}: {REFUSAL_HELP['bad_label']}, "
                                 "not starting with _")
        try:
            entry_id = self.ledger.write(name, body, author=self.agent_author, prev=prev)
            for label in [AGENT_LABEL, *labels]:
                self.ledger.tag(name, label, author=self.agent_author)
        except scribe.Refused as error:
            raise self._refused(error) from None
        return {"id": entry_id, "name": name, "author": self.agent_author,
                "labels": sorted(self.ledger.labels(name))}

    def ledger_tag(self, args):
        name, label, remove = args["name"], args["label"], bool(args.get("remove"))
        if not isinstance(name, str) or not isinstance(label, str):
            raise ToolInputError("name and label must be strings")
        if self._protected_name(name):
            raise ToolInputError("refused: harness entries cannot be tagged by the agent")
        if is_protected_label(label):
            raise ToolInputError(f"refused: {label!r} is a protected label")
        try:
            op = self.ledger.untag if remove else self.ledger.tag
            entry_id = op(name, label, author=self.agent_author)
        except scribe.Refused as error:
            raise self._refused(error) from None
        return {"id": entry_id, "name": name, "labels": sorted(self.ledger.labels(name))}

    # -- filesystem ----------------------------------------------------------------------
    def _resolve(self, path):
        if not isinstance(path, str) or not path:
            raise ToolInputError("path must be a non-empty string")
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.fs_root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ToolInputError(f"not found: {path}") from None
        if resolved != self.fs_root and self.fs_root not in resolved.parents:
            raise ToolInputError("refused: outside the nimoi folder")
        relative = resolved.relative_to(self.fs_root)
        if ".git" in relative.parts:
            raise ToolInputError("refused: .git internals are not readable")
        return resolved, relative

    @staticmethod
    def _secret(name):
        lower = name.lower()
        if lower in SECRET_ALLOWED:
            return False
        return any(fnmatch.fnmatch(lower, pattern) for pattern in SECRET_PATTERNS)

    def fs_list(self, args):
        resolved, relative = self._resolve(args.get("path") or ".")
        if not resolved.is_dir():
            raise ToolInputError(f"not a directory: {relative.as_posix()}")
        entries = []
        for child in sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            item = {"name": child.name, "type": "dir" if child.is_dir() else "file"}
            if item["type"] == "file":
                try:
                    item["size"] = child.stat().st_size
                except OSError:
                    item["size"] = None
                if self._secret(child.name):
                    item["readable"] = False
            entries.append(item)
        return {"path": relative.as_posix() or ".", "entries": entries[:MAX_LIST],
                "total": len(entries), "truncated": len(entries) > MAX_LIST}

    def fs_read(self, args):
        resolved, relative = self._resolve(args.get("path"))
        if resolved.is_dir():
            raise ToolInputError(f"is a directory (use fs_list): {relative.as_posix()}")
        if self._secret(resolved.name):
            raise ToolInputError("refused: secret-bearing file name")
        start = args.get("start_line") or 1
        count = args.get("max_lines") or 400
        if not all(isinstance(v, int) and v >= 1 for v in (start, count)):
            raise ToolInputError("start_line and max_lines must be positive integers")
        size = resolved.stat().st_size
        with open(resolved, "rb") as handle:
            data = handle.read(MAX_FILE_BYTES)
        if b"\x00" in data[:8192]:
            raise ToolInputError("refused: binary file")
        lines = data.decode("utf-8", errors="replace").splitlines()
        chunk = "\n".join(lines[start - 1:start - 1 + count])
        chunk, redactions = redact_keys(chunk)
        truncated_chars = len(chunk) > MAX_FS_CHARS
        return {"path": relative.as_posix(), "size": size,
                "file_truncated_at_bytes": MAX_FILE_BYTES if size > MAX_FILE_BYTES else None,
                "lines": f"{start}-{min(start - 1 + count, len(lines))} of {len(lines)}",
                "text": chunk[:MAX_FS_CHARS], "chars_truncated": truncated_chars,
                "redactions": redactions}
