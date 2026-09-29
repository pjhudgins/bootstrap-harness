"""Where agents may touch the disk at all: the fixed filesystem rules, before any bounds.

A tool's path is refused if any part of it is a name Windows rewrites (a trailing dot or
space, a ':' stream, a device name such as NUL), or if it is absolute and not inside the
nimoi folder (checked before the disk is touched, so a UNC path contacts no server).
Otherwise it is resolved (links followed; Windows short names and letter case turned
into the real names), and the rules apply to the resolved path:
- it must be inside the nimoi folder;
- no part of it is .git;
- no part of it is harness state that only the ledger tools may read: a ledger file
  (*.ledger), a ledger lease (lease.json), harness runtime state (.runtime);
- to be read or written, its file name must not be secret-bearing (keys, tokens, .env).
Agents see these rules in prompts/bounds.md.
"""

import fnmatch
import os
import re
from pathlib import Path, PurePath

GIT = ".git"
HIDDEN_PATTERNS = ("*.ledger", "lease.json", ".runtime")
SECRET_PATTERNS = (".env", ".env.*", "*.env", "*.token", "*.key", "*.pem", "*.p12", "*.pfx",
                   "credentials*.json", "secrets*.json", "auth.json", "id_rsa*", "id_ed25519*",
                   ".netrc", ".npmrc", ".pypirc")
SECRET_ALLOWED = (".env.example",)
DEVICE_NAME = re.compile(r"(con|prn|aux|nul|com[0-9]|lpt[0-9]|conin\$|conout\$)(\..*)?",
                         re.IGNORECASE)


class PathRefused(ValueError):
    """A path the fixed rules refuse; the message says why."""


def secret_name(name):
    lower = name.lower()
    return lower not in SECRET_ALLOWED and any(fnmatch.fnmatch(lower, p) for p in SECRET_PATTERNS)


def hidden_name(name):
    lower = name.lower()
    return any(fnmatch.fnmatch(lower, p) for p in HIDDEN_PATTERNS)


def _rewritten_name(part):
    """Why Windows would not use this path part as written, or None."""
    if part in (".", ".."):
        return None
    if part.endswith((".", " ")):
        return "ends with a dot or a space"
    if ":" in part:
        return "contains ':' (an alternate data stream)"
    if DEVICE_NAME.fullmatch(part.rstrip(" ")):
        return "is a device name"
    return None


class PathPolicy:
    def __init__(self, fs_root):
        self.root = Path(fs_root).resolve()

    def rel(self, resolved):
        """The nimoi-relative, "/"-separated form of a resolved path inside the root."""
        return resolved.relative_to(self.root).as_posix() if resolved != self.root else ""

    def resolve(self, path, *, must_exist=True):
        """(resolved path, nimoi-relative path) for a tool's path argument; PathRefused
        if the fixed rules refuse it."""
        if not isinstance(path, str) or not path:
            raise PathRefused("path must be a non-empty string")
        given = PurePath(path)
        for part in given.parts[1 if given.anchor else 0:]:
            problem = _rewritten_name(part)
            if problem:
                raise PathRefused(f"refused: path part {part!r} {problem}")
        candidate = Path(path)
        if candidate.is_absolute() or given.drive:
            # Checked before anything touches the disk: resolving a UNC path
            # (\\server\share) would contact that server, and Windows may send it the
            # user's credentials.
            lexical = Path(os.path.abspath(candidate))
            if lexical != self.root and self.root not in lexical.parents:
                raise PathRefused("refused: outside the nimoi folder")
        else:
            candidate = self.root / candidate
        try:
            resolved = candidate.resolve(strict=must_exist)
        except (OSError, RuntimeError):
            raise PathRefused(f"not found: {path}") from None
        if resolved != self.root and self.root not in resolved.parents:
            raise PathRefused("refused: outside the nimoi folder")
        rel = self.rel(resolved)
        parts = [part.lower() for part in PurePath(rel).parts]
        if GIT in parts:
            raise PathRefused("refused: .git internals are never readable or writable")
        if any(hidden_name(part) for part in parts):
            raise PathRefused("refused: ledger files, leases and harness runtime state are "
                              "never read or written with fs tools; read the ledger with the "
                              "ledger tools")
        return resolved, rel


def check_not_secret(resolved):
    if secret_name(resolved.name):
        raise PathRefused("refused: secret-bearing file name")
