"""Harness-owned filesystem tools (rules.md task 5c; peer review 2026-09-25).

    mcp__fs__list    list one directory                      (fs.read)
    mcp__fs__read    read a text file, paged by line          (fs.read)
    mcp__fs__search  regex search under a directory           (fs.read)
    mcp__fs__write   write one pinned ledger entry revision   (fs.write)

The CLI's own Read, Glob and Grep are not loaded. Task 5 found two ways past checks made
from outside those tools:
  - a rooted path that the CLI resolved against the drive root;
  - Windows 8.3 short names in Glob patterns.
Every peer lane owns its file tools. Owning them means one resolver picks the file, and
the same resolved path is both checked and opened.

FsAccess.check(scope, path) does, in order:
  1. resolve to the final path (bounds.fs_target): symlinks, junctions and 8.3 names expand;
  2. require the path to be inside nimoi;
  3. refuse the always-denied names, whatever the bounds:
     - secret-looking files;
     - raw ledger files (*.ledger) and lease.json, so ledger.read cannot be bypassed through files;
  4. for writes, refuse the scripts directory (only a human promotes scripts);
  5. apply the agent's bounds for the scope.
Listings and searches hide whatever check refuses. A search prunes excluded directories
instead of refusing the whole search.

Writes are pinned and recorded before they happen:
  - the source is an exact ledger revision (an entry id), so a later edit cannot change
    what gets exported;
  - expected_sha256 = null only creates a new file; a hash replaces the file only if it
    still has that hash;
  - file_write_started is recorded first. If the ledger cannot record it, nothing is written.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import tool

from bounds import Bounds, covers, fs_target
from ledger_tools import Denied, LedgerGuard, denied_reply
from ledgerlog import wikilink

SERVER_NAME = "fs"

# Mirrors the secrets block of bootstrap-harness/.gitignore.
SECRET_NAME_PATTERNS = (".env", ".env.*", "*.env", "*.token", "*.key", "*.pem", "credentials*.json", "secrets*.json")
# The scribe writes only <stamp>.ledger and lease.json (bootstrap-ledger scribe.py).
LEDGER_FILE_PATTERNS = ("*.ledger", "lease.json")
SEARCH_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}  # noise, not a security rule

READ_PAGE_LINES = 2000
READ_MAX_CHARS = 100_000
READ_MAX_BYTES = 20_000_000
LIST_PAGE = 200
SEARCH_MAX_RESULTS = 200
SEARCH_MAX_FILES = 20_000
SEARCH_MAX_FILE_BYTES = 2_000_000
SEARCH_LINE_CHARS = 300
WRITE_MAX_BYTES = 2_000_000


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str
    target: str | None = None  # the bounds target ("/a/b"), after resolution
    path: Path | None = None  # the resolved path that will be opened


def fixed_denial(target: str) -> str | None:
    """Why `target` is always refused, whatever the bounds (None if it is not)."""
    for part in (p.lower() for p in target.split("/") if p):
        if any(fnmatch.fnmatch(part, pat) for pat in SECRET_NAME_PATTERNS):
            return f"{target} looks like a secret file"
        if any(fnmatch.fnmatch(part, pat) for pat in LEDGER_FILE_PATTERNS):
            return f"{target} is a raw ledger file; read the ledger with the ledger tools, within ledger.read"
    return None


def _is_text(data: bytes) -> bool:
    return b"\x00" not in data[:8192]


class FsAccess:
    """The one resolver and checker for every filesystem tool."""

    def __init__(self, root: Path, bounds: Bounds, never_writable: list[str] = ()):
        self.root = Path(root).resolve()
        self.bounds = bounds
        self.never_writable = list(never_writable)  # bounds targets, e.g. the scripts dir

    def check(self, scope: str, raw: Any) -> Decision:
        if not isinstance(raw, str) or not raw.strip():
            return Decision(False, "no path given")
        target = fs_target(raw, self.root)  # resolved first: aliases cannot dodge the rules below
        if target is None:
            return Decision(False, f"path {raw!r} is outside nimoi")
        why = fixed_denial(target)
        if why:
            return Decision(False, why, target)
        if scope == "fs.write" and any(covers("fs.write", n, target) for n in self.never_writable):
            return Decision(False, f"{target} is in the harness scripts directory, which no agent may write; "
                                   "a human promotes scripts", target)
        if not self.bounds.permits(scope, target):
            return Decision(False, f"{target} is outside this agent's {scope} "
                                   f"({self.bounds.scope(scope).render() or 'nothing'})", target)
        return Decision(True, f"{target} is within {scope}", target, self.root / target.lstrip("/"))

    def require(self, scope: str, raw: Any) -> Decision:
        d = self.check(scope, raw)
        if not d.allow:
            raise Denied(d.reason)
        return d

    # ---- fs.read -------------------------------------------------------------------------

    def list_dir(self, raw: Any = "/", offset: int = 0, limit: int = LIST_PAGE) -> dict[str, Any]:
        d = self.require("fs.read", raw or "/")
        if not d.path.is_dir():
            raise Denied(f"{d.target} is not a directory")
        entries, hidden = [], 0
        with os.scandir(d.path) as it:
            for e in sorted(it, key=lambda e: e.name.lower()):
                if not self.check("fs.read", e.path).allow:
                    hidden += 1
                    continue
                try:
                    is_dir = e.is_dir()
                    entries.append({"name": e.name, "type": "dir"} if is_dir else
                                   {"name": e.name, "type": "file", "bytes": e.stat().st_size})
                except OSError:
                    hidden += 1
        offset, limit = max(0, int(offset)), max(1, min(int(limit), LIST_PAGE))
        page = entries[offset:offset + limit]
        return {"path": d.target, "entries": page, "total": len(entries), "offset": offset,
                "next_offset": offset + limit if offset + limit < len(entries) else None, "hidden": hidden}

    def read_file(self, raw: Any, offset: int = 1, limit: int = READ_PAGE_LINES) -> dict[str, Any]:
        d = self.require("fs.read", raw)
        if not d.path.is_file():
            raise Denied(f"{d.target} is not a file")
        if d.path.stat().st_size > READ_MAX_BYTES:
            raise Denied(f"{d.target} is larger than {READ_MAX_BYTES} bytes")
        data = d.path.read_bytes()
        if not _is_text(data):
            raise Denied(f"{d.target} looks binary")
        lines = data.decode("utf-8", errors="replace").splitlines()
        start = max(1, int(offset))
        limit = max(1, min(int(limit), READ_PAGE_LINES))
        chunk, chars, end, cut = [], 0, start - 1, False
        for i in range(start - 1, min(len(lines), start - 1 + limit)):
            line = lines[i]
            if chunk and chars + len(line) + 1 > READ_MAX_CHARS:
                break
            if len(line) > READ_MAX_CHARS:
                line, cut = line[:READ_MAX_CHARS], True
            chunk.append(line)
            chars += len(line) + 1
            end = i + 1
        return {"path": d.target, "start_line": start, "end_line": end, "total_lines": len(lines),
                "next_offset": end + 1 if end < len(lines) else None, "line_truncated": cut,
                "sha256": hashlib.sha256(data).hexdigest(), "text": "\n".join(chunk)}

    def search(self, pattern: Any, raw: Any = "/", glob: Any = None, ignore_case: bool = False,
               max_results: int = SEARCH_MAX_RESULTS) -> dict[str, Any]:
        if not isinstance(pattern, str) or not pattern or len(pattern) > 500:
            raise Denied("pattern must be a regular expression of 1-500 characters")
        try:
            rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as e:
            raise Denied(f"pattern is not a valid regular expression: {e}") from e
        d = self.require("fs.read", raw or "/")
        max_results = max(1, min(int(max_results), SEARCH_MAX_RESULTS))
        matches: list[dict[str, Any]] = []
        pruned: list[str] = []
        walked, searched, truncated = 0, 0, False

        def scan(path: Path) -> None:
            nonlocal searched
            if glob and not fnmatch.fnmatch(path.name.lower(), str(glob).lower()):
                return
            c = self.check("fs.read", str(path))
            if not c.allow:
                return
            try:
                if path.stat().st_size > SEARCH_MAX_FILE_BYTES:
                    return
                data = path.read_bytes()
            except OSError:
                return
            if not _is_text(data):
                return
            searched += 1
            for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    matches.append({"path": c.target, "line": n, "text": line[:SEARCH_LINE_CHARS]})
                    if len(matches) >= max_results:
                        return

        if d.path.is_file():
            walked = 1
            scan(d.path)
        else:
            for dirpath, dirnames, filenames in os.walk(d.path, topdown=True, followlinks=False):
                keep = []
                for name in sorted(dirnames):
                    full = Path(dirpath) / name
                    if name in SEARCH_SKIP_DIRS or full.is_symlink() or full.is_junction():
                        continue
                    c = self.check("fs.read", str(full))
                    if c.allow:
                        keep.append(name)
                    elif len(pruned) < 50:
                        pruned.append(c.target or name)
                dirnames[:] = keep
                for name in sorted(filenames):
                    walked += 1
                    if walked > SEARCH_MAX_FILES or len(matches) >= max_results:
                        truncated = True
                        break
                    scan(Path(dirpath) / name)
                if truncated:
                    break
        # files_searched: readable text files whose name passed the glob; files_walked: every file seen.
        return {"path": d.target, "pattern": pattern, "glob": glob, "matches": matches, "files_searched": searched,
                "files_walked": walked, "pruned": pruned, "truncated": truncated or len(matches) >= max_results}


class FileTools:
    """The fs server's tools for one agent: which exist follows its fs.read and fs.write scopes."""

    def __init__(self, *, access: FsAccess, guard: LedgerGuard, bus: Any, agent_id: str,
                 on_read: Callable[[str, int, int, int], None] | None = None):
        self.access = access
        self.guard = guard
        self.bus = bus
        self.agent_id = agent_id
        self.on_read = on_read  # (target, first line, last line, total lines): the onboarding gate

    def note_read(self, out: dict[str, Any]) -> dict[str, Any]:
        """Report a completed read to the onboarding gate. Runs on the event loop, because it records."""
        if self.on_read:
            self.on_read(out["path"], out["start_line"], out["end_line"], out["total_lines"])
        return out

    def write(self, entry_id: Any, path: Any, expected_sha256: Any = None) -> dict[str, Any]:
        d = self.access.require("fs.write", path)
        src = self.guard.read_line(entry_id)  # an exact revision, within this agent's ledger.read
        body = src["body"]
        data = (body.encode("utf-8") if isinstance(body, str)
                else (json.dumps(body, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        if len(data) > WRITE_MAX_BYTES:
            raise Denied(f"the body is {len(data)} bytes; the limit is {WRITE_MAX_BYTES}")
        p = d.path
        if p.is_dir():
            raise Denied(f"{d.target} is a directory")
        if not p.parent.is_dir() and not self.access.check("fs.write", str(p.parent)).allow:
            raise Denied(f"{d.target}: its directory does not exist and is outside fs.write")
        if expected_sha256 is None:
            if p.exists():
                raise Denied(f"{d.target} exists; to replace it, pass its current sha256 (from mcp__fs__read) "
                             "as expected_sha256")
            replaced = None
        else:
            if not p.is_file():
                raise Denied(f"{d.target} does not exist; to create it, pass expected_sha256 null")
            replaced = hashlib.sha256(p.read_bytes()).hexdigest()
            if replaced != expected_sha256:
                raise Denied(f"{d.target} has changed: its sha256 is {replaced}, not {expected_sha256}")
        digest = hashlib.sha256(data).hexdigest()
        started = self.bus.publish("file_write_started", agent=self.agent_id, entry=src["name"],
                                   entry_id=src["id"], path=d.target, sha256=digest, replaces_sha256=replaced)
        if not started or not started.get("name"):
            raise Denied("the ledger could not record this write, so nothing was written")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "xb" if replaced is None else "wb") as f:  # bytes: no newline translation
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            self.bus.publish("file_write_failed", agent=self.agent_id, path=d.target, error=repr(e),
                             started=started.get("id"))
            raise Denied(f"writing {d.target} failed: {e}") from e
        record = self.bus.publish("file_write", agent=self.agent_id, entry=src["name"], entry_id=src["id"],
                                  path=d.target, bytes=len(data), sha256=digest, replaced_sha256=replaced,
                                  started=started.get("id"))
        return {"written": d.target, "bytes": len(data), "sha256": digest, "from_entry": src["name"],
                "from_id": src["id"], "replaced_sha256": replaced, "record": wikilink(record) if record else None}

    # ---- tool definitions ----------------------------------------------------------------

    def tools(self) -> list[Any]:
        can_read = bool(self.access.bounds.scope("fs.read").allow)
        can_write = bool(self.access.bounds.scope("fs.write").allow)

        def reply(data: dict[str, Any]) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}]}

        async def run(name: str, args: dict[str, Any], fn: Callable[[], dict[str, Any]], *,
                      threaded: bool = True, then: Callable[[dict[str, Any]], dict[str, Any]] = lambda o: o
                      ) -> dict[str, Any]:
            """Run a tool body. Read-only walks go to a thread so they cannot stall the event loop.
            Anything that records (writes, `then`) stays on the loop: EventBus.publish only returns
            its record there, and a write must see its started-record before acting."""
            try:
                out = await asyncio.to_thread(fn) if threaded else fn()
                return reply(then(out))
            except Denied as e:
                return denied_reply(self.bus, self.agent_id, f"mcp__{SERVER_NAME}__{name}", args, e)
            except Exception as e:
                return {"content": [{"type": "text", "text": f"error: {e!r}"}], "is_error": True}

        path_doc = ("a path from the nimoi root (\"/origins\"), an absolute path under nimoi, or a path "
                    "relative to nimoi")
        out: list[Any] = []
        if can_read:
            @tool("list", f"List one directory within your fs.read. `path`: {path_doc}. Entries you may not "
                  "read are hidden and only counted.",
                  {"type": "object", "properties": {"path": {"type": "string"},
                                                    "offset": {"type": "integer", "default": 0}}})
            async def list_(args: dict[str, Any]) -> dict[str, Any]:
                return await run("list", args, lambda: self.access.list_dir(args.get("path") or "/",
                                                                            args.get("offset", 0)))

            @tool("read", f"Read a text file within your fs.read, by line. `path`: {path_doc}. Returns up to "
                  f"{READ_PAGE_LINES} lines from `offset` (1-based) and `next_offset` (null at the end), plus the "
                  "file's sha256 (needed to replace a file with mcp__fs__write).",
                  {"type": "object", "properties": {"path": {"type": "string"},
                                                    "offset": {"type": "integer", "default": 1},
                                                    "limit": {"type": "integer", "default": READ_PAGE_LINES}},
                   "required": ["path"]})
            async def read(args: dict[str, Any]) -> dict[str, Any]:
                return await run("read", args, lambda: self.access.read_file(
                    args.get("path"), args.get("offset", 1), args.get("limit", READ_PAGE_LINES)), then=self.note_read)

            @tool("search", f"Search text files for a regular expression, under a directory within your "
                  f"fs.read (default: the nimoi root). `glob` filters file names (e.g. \"*.md\"). Excluded "
                  f"directories are skipped and listed as pruned; .git and caches are skipped. At most "
                  f"{SEARCH_MAX_RESULTS} matches.",
                  {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"},
                                                    "glob": {"type": "string"},
                                                    "ignore_case": {"type": "boolean", "default": False}},
                   "required": ["pattern"]})
            async def search(args: dict[str, Any]) -> dict[str, Any]:
                return await run("search", args, lambda: self.access.search(
                    args.get("pattern"), args.get("path") or "/", args.get("glob"), bool(args.get("ignore_case"))))

            out += [list_, read, search]
        if can_write:
            @tool("write", "Write one exact ledger revision to a file within your fs.write. `entry_id` is the "
                  "revision's id (returned by mcp__ledger__write and mcp__ledger__read), so a later edit cannot "
                  "change what is written. `expected_sha256`: null creates a new file only; to replace a file "
                  "give its current sha256 (from mcp__fs__read). A text body is written exactly.",
                  {"type": "object", "properties": {"entry_id": {"type": "string"}, "path": {"type": "string"},
                                                    "expected_sha256": {"type": ["string", "null"]}},
                   "required": ["entry_id", "path"]})
            async def write(args: dict[str, Any]) -> dict[str, Any]:
                return await run("write", args, lambda: self.write(args.get("entry_id"), args.get("path"),
                                                                   args.get("expected_sha256")), threaded=False)

            out.append(write)
        return out
