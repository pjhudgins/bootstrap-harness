"""Tool policy: which tools the agent may use, and where the read-only ones may look.

(Copied unchanged from task-2-tooling/policy.py, 2026-09-23; rules keep each task's code in its own folder.
In task 3, `root` is task-3-ui/workspace/, the agent's cwd.)

Founder decision (2026-09-23): keep read-only built-ins (Read, Glob, Grep) plus
the python add tool; nothing that executes code or writes files.

Two layers, both driven by ToolPolicy.check():
1. Loading: driver passes tools=ALLOWED_BUILTINS, so Bash, PowerShell, Write,
   Edit, NotebookEdit, Agent, WebFetch... are never loaded into the session.
2. Enforcement: a PreToolUse hook (fires on every call, including ones
   Claude Code would auto-approve) and can_use_tool (fires when permission
   is requested) both deny anything check() rejects.

Read/Glob/Grep are not directory-scoped by Claude Code, and the swimlane .env
(where API keys may live) is one level above this task. So check() confines
them to `root` and refuses secret-looking names.

Known prototype limits: Glob/Grep with no path search everything under root;
names are screened, not file contents. Nothing secret lives under root.
"""

import fnmatch
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

ALLOWED_BUILTINS = ["Read", "Glob", "Grep"]
ADD_TOOL = "mcp__calc__add"
ALLOWED_TOOLS = frozenset(ALLOWED_BUILTINS + [ADD_TOOL])

# Mirrors the secrets block of bootstrap-harness/.gitignore.
SECRET_NAME_PATTERNS = (
    ".env", ".env.*", "*.env", "*.token", "*.key", "*.pem",
    "credentials*.json", "secrets*.json",
)


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str


def _is_secret_name(name: str) -> bool:
    name = name.lower()
    return any(fnmatch.fnmatch(name, pat) for pat in SECRET_NAME_PATTERNS)


class ToolPolicy:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def check(self, tool_name: str, tool_input: dict[str, Any]) -> Decision:
        if tool_name not in ALLOWED_TOOLS:
            return Decision(False, f"{tool_name} is not on the allowlist {sorted(ALLOWED_TOOLS)}")
        if tool_name == "Read":
            return self._check_path(tool_input.get("file_path"), required=True)
        if tool_name == "Glob":
            d = self._check_path(tool_input.get("path"), required=False)
            return d if not d.allow else self._check_pattern(tool_input.get("pattern", ""))
        if tool_name == "Grep":
            d = self._check_path(tool_input.get("path"), required=False)
            return d if not d.allow else self._check_pattern(tool_input.get("glob") or "")
        return Decision(True, "allowlisted")

    def _check_path(self, raw: Any, *, required: bool) -> Decision:
        if not raw:
            if required:
                return Decision(False, "no path given")
            return Decision(True, "defaults to root")
        p = Path(str(raw)).expanduser()
        if not p.is_absolute():
            p = self.root / p  # on Windows this also anchors "/x" to root's drive
        resolved = p.resolve()
        if not resolved.is_relative_to(self.root):
            return Decision(False, f"path {raw!r} resolves outside {self.root}")
        if any(_is_secret_name(part) for part in resolved.relative_to(self.root).parts):
            return Decision(False, f"path {raw!r} looks like a secret file")
        return Decision(True, "inside root")

    def _check_pattern(self, pattern: str) -> Decision:
        if not pattern:
            return Decision(True, "no pattern")
        pp = PurePath(pattern)
        if pp.anchor or pattern.startswith(("/", "\\", "~")):
            return Decision(False, f"pattern {pattern!r} is absolute")
        if ".." in pp.parts:
            return Decision(False, f"pattern {pattern!r} climbs out of root")
        if any(_is_secret_name(part) for part in pp.parts):
            return Decision(False, f"pattern {pattern!r} targets secret-looking files")
        return Decision(True, "pattern inside root")
