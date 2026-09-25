"""Explicit Python tools: addition, session ledger, bounded text-file reads."""

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from ledger_store import NIMOI
from privacy import redact
from protocol import ADD, add


BLOCKED_PARTS = {".git", ".codex", ".claude", ".ssh", ".aws", ".azure", ".runtime",
                 ".venv", "node_modules", "__pycache__", "run", "runs"}
SECRET_NAME = re.compile(r"(^|[._-])(auth|credentials?|secrets?|tokens?)([._-]|$)", re.I)
DEVICE = re.compile(r"(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", re.I)


def args_only(arguments, allowed, required=()):
    if not isinstance(arguments, dict) or set(arguments) - set(allowed) or set(required) - set(arguments):
        raise ValueError("Unexpected or missing tool arguments.")
    return arguments


def integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}.")
    return value


class ReadFiles:
    def __init__(self, root=NIMOI):
        self.root = root.resolve(strict=True)

    def path(self, value):
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError("Provide a NIMOI-relative or absolute local path.")
        normalized = value.replace("\\", "/")
        if normalized.startswith("//") or any(p == ".." for p in normalized.split("/")):
            raise ValueError("Network/device paths and parent traversal are not allowed.")
        supplied = Path(value)
        if supplied.drive and not supplied.is_absolute():
            raise ValueError("Drive-relative paths are not allowed.")
        candidate = supplied if supplied.is_absolute() else self.root / supplied
        try:
            relative = candidate.relative_to(self.root)
        except ValueError:
            raise ValueError("Path is outside the NIMOI read boundary.") from None
        cursor = self.root
        for part in relative.parts:
            lower = part.lower()
            if (":" in part or part.endswith((".", " ")) or DEVICE.fullmatch(part)
                    or lower in BLOCKED_PARTS or lower.startswith(".env")
                    or lower == "lease.json" or SECRET_NAME.search(part)
                    or lower.startswith("id_rsa") or lower.startswith("id_ed25519")
                    or Path(part).suffix.lower() in (".pem", ".key", ".pfx", ".p12")):
                raise ValueError("This credential/runtime or special path is excluded from filesystem tools.")
            cursor /= part
            info = cursor.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Symlink and reparse-point paths are not allowed.")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(self.root):
            raise ValueError("Resolved path is outside NIMOI.")
        return resolved

    def list(self, arguments):
        args_only(arguments, ("path", "offset", "limit"), ("path",))
        offset = integer(arguments.get("offset", 0), 0, 1000000, "offset")
        limit = integer(arguments.get("limit", 100), 1, 200, "limit")
        try:
            directory = self.path(arguments["path"])
            if not directory.is_dir():
                raise ValueError("Not a directory.")
            entries, excluded = [], 0
            for child in sorted(directory.iterdir(), key=lambda p: p.name):
                try:
                    safe = self.path(str(child))
                    info = safe.stat()
                    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                        raise ValueError("Special file")
                    entries.append({"name": child.name, "type": "directory" if safe.is_dir() else "file"})
                except (ValueError, OSError):
                    excluded += 1
            return {"path": directory.relative_to(self.root).as_posix(),
                    "entries": entries[offset:offset + limit], "excluded_count": excluded,
                    "next_offset": offset + limit if offset + limit < len(entries) else None}
        except OSError as error:
            raise ValueError(f"Filesystem read failed: {error.strerror}") from None

    def _check_handle(self, file):
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
            raise ValueError("Only ordinary files with one hard link may be read.")
        if os.name == "nt":
            # Validate the opened object, not merely the pre-open spelling.
            import msvcrt
            from ctypes import wintypes
            function = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
            function.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
            function.restype = wintypes.DWORD
            buffer = ctypes.create_unicode_buffer(32768)
            length = function(msvcrt.get_osfhandle(file.fileno()), buffer, len(buffer), 0)
            if not length or length >= len(buffer):
                raise ValueError("Could not verify the opened file's path.")
            final = buffer.value
            if final.startswith("\\\\?\\"):
                final = final[4:]
            self.path(final)

    def read(self, arguments):
        args_only(arguments, ("path", "offset", "limit"), ("path",))
        offset = integer(arguments.get("offset", 0), 0, 2000000, "offset")
        limit = integer(arguments.get("limit", 16000), 1, 24000, "limit")
        try:
            path = self.path(arguments["path"])
            with path.open("rb") as file:
                self._check_handle(file)
                raw = file.read(2000001)
            if len(raw) > 2000000 or b"\x00" in raw:
                raise ValueError("Read accepts UTF-8 text files up to 2 MB, not binary files.")
            text = raw.decode("utf-8-sig")
        except (OSError, UnicodeError) as error:
            raise ValueError("Could not read this path as ordinary UTF-8 text.") from None
        # Redact the full file before slicing so chunk boundaries cannot split keys.
        safe = redact(text)
        return {"path": path.relative_to(self.root).as_posix(), "offset": offset,
                "text": safe[offset:offset + limit], "total_chars": len(safe),
                "next_offset": offset + limit if offset + limit < len(safe) else None,
                "sha256": hashlib.sha256(raw).hexdigest(), "redacted": safe != text}


def tool(name, description, properties, required, handler):
    return ({"type": "function", "name": name, "description": description,
             "inputSchema": {"type": "object", "properties": properties,
                             "required": required, "additionalProperties": False}}, handler)


def registry(journal, root=NIMOI):
    files = ReadFiles(root)
    def ledger_list(a):
        args_only(a, ("tag", "prefix", "offset", "limit"))
        for key in ("tag", "prefix"):
            if key in a and not isinstance(a[key], str):
                raise ValueError(key + " must be text.")
        return journal.list_entries(a.get("tag"), a.get("prefix", ""),
            integer(a.get("offset", 0), 0, 1000000, "offset"),
            integer(a.get("limit", 50), 1, 100, "limit"))

    def ledger_read(a):
        args_only(a, ("name", "history", "offset", "limit"), ("name",))
        if not isinstance(a["name"], str) or type(a.get("history", False)) is not bool:
            raise ValueError("name must be text and history must be boolean.")
        offset = integer(a.get("offset", 0), 0, 100000000, "offset")
        limit = integer(a.get("limit", 16000), 1, 24000, "limit")
        data = journal.read_entry(a["name"], a.get("history", False))
        text = json.dumps(data, ensure_ascii=False)
        return {"format": "JSON text; concatenate pages to parse", "text": text[offset:offset + limit],
                "offset": offset, "next_offset": offset + limit if offset + limit < len(text) else None}

    def ledger_write(a):
        args_only(a, ("name", "body", "prev", "tags"), ("name", "body", "prev", "tags"))
        return journal.agent_write(a["name"], a["body"], a["prev"], a["tags"])

    string = {"type": "string"}
    paging = {"offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1}}
    return {
        "add": (ADD, lambda a: {"sum": add(a)}),
        "ledger_list": tool("ledger_list", "List names, current ids, attested authors and tags in this conversation's ledger. Optional tag/prefix filter and pagination.",
            {"tag": string, "prefix": string, **paging}, [], ledger_list),
        "ledger_read": tool("ledger_read", "Read a current entry or its history in this conversation's ledger. Returns paged JSON text with ids for updates.",
            {"name": string, "history": {"type": "boolean"}, **paging}, ["name"], ledger_read),
        "ledger_write": tool("ledger_write", "Create/update text under agent/. Fixed author gpt-codex-test-pilot. The harness automatically adds agent; explicitly including it is also valid. Other tags must start agent-. Use prev:null to create or cite the current entry id to update. Protected harness entries cannot be changed. No delete or untag.",
            {"name": string, "body": string, "prev": {"type": ["string", "null"]},
             "tags": {"type": "array", "items": string}}, ["name", "body", "prev", "tags"], ledger_write),
        "fs_list": tool("fs_list", "List one directory inside NIMOI. Paths relative to NIMOI or absolute within it. Sorted names, paginated. Credential/runtime and link paths excluded. Does not write or execute.",
            {"path": string, **paging}, ["path"], files.list),
        "fs_read": tool("fs_read", "Read UTF-8 text inside NIMOI, max 2 MB file and 24,000 characters per page. Character offsets refer to redacted text. Follow next_offset until null for complete reading. Credential/runtime and link paths excluded. Does not write or execute.",
            {"path": string, **paging}, ["path"], files.read),
    }
